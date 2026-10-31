import io,json,hashlib
from pathlib import Path
import fitz,httpx
from PIL import Image
from docx import Document
from fastapi.testclient import TestClient
from backend import main,db,academic
from backend.tests.test_api_integration import configure_storage,authenticate


def project(client):
    response=client.post('/projects',json={'name':'Synthetic research','purpose':'research'});response.raise_for_status()
    assert response.json()['status']=='选题与写作'
    return response.json()['id']


def test_local_research_and_document_exports_are_real_files(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as client:
        authenticate(client);pid=project(client)
        answer=client.post('/assistant/chat',json={'project_id':pid,'message':'Prepare a framework','task':'outline'})
        assert answer.status_code==200 and answer.json()['local_only']
        assert '本地研究模板' in answer.json()['content']
        assert len(client.get(f'/projects/{pid}/assistant/messages').json())==2
        for format in ('md','docx','pdf'):
            result=client.post(f'/projects/{pid}/drafts',json={'title':'合成开题报告','content':'研究问题与数据\n\n保留真实数值 1.25。','format':format})
            assert result.status_code==200,result.text
            downloaded=client.get(result.json()['download_url']);assert downloaded.status_code==200
            if format=='docx':assert '真实数值' in '\n'.join(p.text for p in Document(io.BytesIO(downloaded.content)).paragraphs)
            if format=='pdf':
                with fitz.open(stream=downloaded.content,filetype='pdf') as pdf:assert '1.25' in ''.join(p.get_text() for p in pdf)
        assert client.get(f'/projects/{pid}/academic-output/draft-../draft.pdf').status_code!=200


def test_cloud_preview_binds_exact_context_and_destination(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path);calls=[]
    monkeypatch.setattr(main,'get_model_settings',lambda:{'id':'fake','ready':True,'base_url':'https://model.example/v1','model':'synthetic-model'})
    monkeypatch.setattr(main.keyring,'get_password',lambda *args:None)
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append((url,kwargs['json']))
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'reply':'合成回答，未进行真实模型调用。','suggestions':[]})}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(main.httpx,'AsyncClient',FakeClient)
    with TestClient(main.app) as client:
        authenticate(client);pid=project(client)
        client.post(f'/projects/{pid}/documents',files={'file':('paper.md',b'SENTINEL_FULL_PARAGRAPH_NOT_SELECTED','text/markdown')})
        client.post(f'/projects/{pid}/analyze')
        from backend.worker import claim_next_job
        main.run_persisted_job(claim_next_job());aid=client.get(f'/projects/{pid}').json()['anchors'][0]['id']
        client.post(f'/projects/{pid}/datasets',files={'file':('synthetic.csv',b'time,value\n1,987654\n2,654321\n','text/csv')})
        payload={'project_id':pid,'anchor_id':aid,'task':'translation','message':'Translate selected text','selection':'Synthetic selected paragraph.','use_cloud':True,'confirmed':True}
        assert client.post('/assistant/chat',json=payload).status_code==409 and not calls
        preview=client.post('/assistant/preview',json=payload).json()
        assert preview['context']['selection']==payload['selection']
        assert 'SENTINEL_FULL_PARAGRAPH_NOT_SELECTED' not in json.dumps(preview['context'])
        assert preview['context']['datasets'][0]['filename']=='synthetic.csv'
        assert preview['context']['datasets'][0]['columns'][0]['name']=='time'
        assert '987654' not in json.dumps(preview['context'])
        assert preview['context']['destination']['model']=='synthetic-model'
        assert client.post('/assistant/chat',json={**payload,'selection':'Changed after preview','preview_hash':preview['preview_hash']}).status_code==409 and not calls
        response=client.post('/assistant/chat',json={**payload,'preview_hash':preview['preview_hash']})
        assert response.status_code==200,response.text
        assert not response.json()['local_only'] and len(calls)==1
        assert json.loads(calls[0][1]['messages'][1]['content'])==preview['context']
        assert '不得捏造' in calls[0][1]['messages'][0]['content']


def test_reader_keeps_source_and_image_ownership(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path);buffer=io.BytesIO();image=Image.new('RGB',(30,20),(35,115,100));image.save(buffer,format='PNG')
    tiff=io.BytesIO();image.save(tiff,format='TIFF')
    doc=Document();doc.add_paragraph('Synthetic paper text.');doc.add_picture(io.BytesIO(buffer.getvalue()));doc.add_paragraph('Figure 1. Synthetic experiment.');doc.add_picture(io.BytesIO(tiff.getvalue()))
    raw=io.BytesIO();doc.save(raw);before=hashlib.sha256(raw.getvalue()).hexdigest()
    with TestClient(main.app) as client:
        authenticate(client);pid=project(client)
        uploaded=client.post(f'/projects/{pid}/documents',files={'file':('synthetic.docx',raw.getvalue(),'application/octet-stream')}).json()
        reader=client.get(f'/projects/{pid}/reader');assert reader.status_code==200,reader.text
        image=reader.json()['images'][0];assert image['document_id']==uploaded['id'] and 'filename' not in image
        saved=client.get(image['url']);assert saved.status_code==200 and saved.content==buffer.getvalue()
        tiff_original=next(item for item in reader.json()['images'] if item.get('original_url'))
        assert client.get(tiff_original['original_url']).content==tiff.getvalue()
        manifest=next((db.PROJECTS_ROOT/pid/'source').rglob('manifest.json'));mtime=manifest.stat().st_mtime_ns
        assert client.get(f'/projects/{pid}/reader').json()['images'][0]['id']==image['id']
        assert manifest.stat().st_mtime_ns==mtime
        original=client.get(reader.json()['documents'][0]['original_url']);assert hashlib.sha256(original.content).hexdigest()==before
        other=project(client)
        assert client.get(image['url'].replace(pid,other)).status_code==404
        monkeypatch.setattr(main,'ocr_document_image',lambda *args,**kwargs:{'status':'unavailable','text':'','confidence':0})
        assert client.post(image['url']+'/ocr').json()['status']=='unavailable'
        client.cookies.clear();assert client.get(image['url']).status_code==401


def test_mermaid_generates_editable_vector_and_rejects_unsupported_commands(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as client:
        authenticate(client);pid=project(client)
        result=client.post(f'/projects/{pid}/diagrams',json={'title':'Synthetic diagram','source':'flowchart TD\nA[Research question] --> B[Measured data]'})
        assert result.status_code==200,result.text
        assert b'<svg' in client.get(result.json()['preview_url']).content
        assert b'flowchart TD' in client.get(result.json()['source_url']).content
        assert client.post(f'/projects/{pid}/diagrams',json={'source':'flowchart TD\nclick A javascript:alert(1)'}).status_code==422


def test_literature_records_persist_without_changing_image_permissions(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    async def fake_search(query):
        return [{'title':'Synthetic title','source_url':'https://doi.org/10.1234/synthetic','provider':'Crossref','doi':'10.1234/synthetic','author':'A. Example','year':2025,'journal':'Synthetic Journal','verification_status':'metadata_found'}],{'successful_providers':['Crossref'],'failed_providers':[]}
    monkeypatch.setattr(main,'search_literature',fake_search)
    async def fake_verify(*args):return {'status':'matched','message':'Metadata matched'}
    monkeypatch.setattr(main,'verify_reference',fake_verify)
    with TestClient(main.app) as client:
        authenticate(client);pid=project(client)
        client.post(f'/projects/{pid}/documents',files={'file':('paper.md',b'Synthetic manuscript body.','text/markdown')})
        queued=client.post(f'/projects/{pid}/analyze').json()
        from backend.worker import claim_next_job
        main.run_persisted_job(claim_next_job());aid=client.get(f'/projects/{pid}').json()['anchors'][0]['id']
        record=client.post(f'/anchors/{aid}/literature',json={'query':'Synthetic title'}).json()[0]
        assert record['doi']=='10.1234/synthetic' and record['year']==2025 and not record['license']['verified']
        assert client.post(f'/anchors/{aid}/literature',json={'query':'Synthetic title'}).json()[0]['id']==record['id']
        assert client.post(f"/assets/{record['id']}/verify-reference").json()['status']=='matched'
        patch=client.patch(f"/assets/{record['id']}",json={'title':'Manually corrected title','reason':'更正合成文献题名'})
        assert patch.status_code==200,patch.text
        restored=client.get(f'/anchors/{aid}/candidates').json()[0]
        assert restored['title']=='Manually corrected title' and restored['verification_status']=='needs_review'
        assert not restored['license']['verified']
        assert client.post('/decisions',json={'project_id':pid,'target_type':'citation','target_id':record['id'],'action':'accept','reason':'Synthetic selected source'}).status_code==200
        from backend.services import create_export
        export_id,_=create_export(pid,False,None)
        import zipfile
        with zipfile.ZipFile(db.fetch_one('SELECT archive_path FROM exports WHERE id=?',(export_id,))['archive_path']) as archive:
            citations=json.loads(archive.read('references.json'))
            assert citations[0]['doi']=='10.1234/synthetic' and citations[0]['year']==2025
            assert '10.1234/synthetic' in archive.read('references.bib').decode('utf-8')
            assert 'TY  - JOUR' in archive.read('references.ris').decode('utf-8')


def test_verifier_reports_uncertainty_and_uses_only_fixed_registry(monkeypatch):
    calls=[]
    class Fake:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,**kwargs):
            calls.append(url)
            return httpx.Response(404,request=httpx.Request('GET',url))
    monkeypatch.setattr(academic.httpx,'AsyncClient',Fake)
    import asyncio
    result=asyncio.run(academic.verify_reference('Sample','10.1234/none'))
    assert result['status']=='not_found' and '不能单独证明' in result['message']
    assert calls[0].startswith('https://api.crossref.org/works/')
    assert asyncio.run(academic.verify_reference('Sample','https://internal.example/key'))['status']=='not_found'
    assert len(calls)==1
