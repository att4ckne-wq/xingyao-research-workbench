import io,json,hashlib
from pathlib import Path
import fitz
from docx import Document
from fastapi.testclient import TestClient
from backend import db,parsers,services,main
from backend.tests.test_api_integration import configure_storage,authenticate
from backend.worker import claim_next_job

def upload(client,filename,content):
    project=client.post('/projects',json={'name':'Synthetic repair acceptance'}).json()
    response=client.post(f"/projects/{project['id']}/documents",files={'file':(filename,content,'application/octet-stream')})
    assert response.status_code==200,response.text
    return project['id'],response.json()

def analyze(client,project_id):
    job=client.post(f'/projects/{project_id}/analyze').json();claimed=claim_next_job();assert claimed['id']==job['job_id'];main.run_persisted_job(claimed)
    return client.get(f'/projects/{project_id}').json()

def test_short_pdf_upload_original_and_parsing(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    document=fitz.open();page=document.new_page();page.insert_text((72,72),'Short PDF sample.');raw=document.tobytes();document.close()
    monkeypatch.setattr(parsers,'_ocr_page',lambda page:(_ for _ in ()).throw(AssertionError('Digital short text must not require OCR')))
    with TestClient(main.app) as client:
        authenticate(client);pid,uploaded=upload(client,'short.pdf',raw);project=analyze(client,pid)
        assert project['jobs'][0]['state']=='completed'
        assert project['anchors'][0]['text']=='Short PDF sample.'
        response=client.get(f"/projects/{pid}/documents/{uploaded['id']}/original")
        assert response.content==raw and response.headers['content-type']=='application/pdf'
        assert response.headers['content-disposition'].startswith('inline')
        other=client.post('/projects',json={'name':'Other'}).json()['id']
        assert client.get(f"/projects/{other}/documents/{uploaded['id']}/original").status_code==404
        client.cookies.clear()
        assert client.get(f"/projects/{pid}/documents/{uploaded['id']}/original").status_code==401

def test_docx_tables_preserve_body_indices_and_export_edits(tmp_path):
    path=tmp_path/'table.docx';document=Document();document.add_paragraph('Body paragraph remains index zero.')
    table=document.add_table(rows=2,cols=2);table.cell(0,0).text='Temperature';table.cell(1,0).text='300 K';table.cell(1,1).text='0.42'
    document.save(path);before=hashlib.sha256(path.read_bytes()).hexdigest()
    anchors=parsers.parse_docx(path)
    assert anchors[0].paragraph_index==0
    target=next(anchor for anchor in anchors if anchor.text=='300 K')
    services._write_illustrated_docx(path,tmp_path/'edited.docx',[{'id':'table-cell','paragraph_index':target.paragraph_index,'text':'310 K'}],[])
    assert Document(tmp_path/'edited.docx').tables[0].cell(1,0).text=='310 K'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    only=Document();only.add_table(rows=1,cols=1).cell(0,0).text='Table-only Word text.';only.save(tmp_path/'only.docx')
    assert parsers.parse_docx(tmp_path/'only.docx')[0].text=='Table-only Word text.'

def test_empty_document_fails_explicitly_and_keeps_original(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path);document=Document();buf=io.BytesIO();document.save(buf)
    with TestClient(main.app) as client:
        authenticate(client);pid,uploaded=upload(client,'empty.docx',buf.getvalue());project=analyze(client,pid)
        assert project['status']=='解析失败' and project['jobs'][0]['state']=='failed'
        assert '未识别到正文' in project['jobs'][0]['message']
        assert project['documents'] and not project['anchors']
        assert client.get(f"/projects/{pid}/documents/{uploaded['id']}/original").content==buf.getvalue()

def test_citation_selection_is_independent_from_image_permission_and_exports(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as client:
        authenticate(client);pid,_=upload(client,'paper.md',b'A synthetic scientific paragraph for source citation acceptance.');project=analyze(client,pid);aid=project['anchors'][0]['id']
        asset_id='c123456789abcdef0123456789abcdef0'
        db.execute('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(asset_id,pid,aid,'Cu&amp;lt;sub&amp;gt;2&amp;lt;/sub&amp;gt;O','Europe PMC','https://europepmc.org/article/MED/123',None,'Fictional Author',json.dumps({'name':'Open access','verified':False,'restrictions':['license_not_specified']}),db.now(),.9,'citation_only',None,'synthetic',None))
        payload={'project_id':pid,'target_id':asset_id,'target_type':'citation','action':'accept','reason':'选择引用来源','warnings':[]}
        assert client.post('/decisions',json={**payload,'target_type':'asset'}).status_code==409
        assert client.post('/decisions',json=payload).status_code==200
        candidate=client.get(f'/anchors/{aid}/candidates').json()[0]
        assert candidate['citation_action']=='accept' and candidate['decision_action'] is None
        assert candidate['title']=='Cu2O' and not candidate['license']['verified']
        archive,warnings=services.create_export(pid,False,None)
        import zipfile
        with zipfile.ZipFile(db.fetch_one('SELECT archive_path FROM exports WHERE id=?',(archive,))['archive_path']) as z:
            manifest=json.loads(z.read('manifest.json'));references=json.loads(z.read('references.json'))
            assert references[0]['asset_id']==asset_id
            assert not manifest['figure_bindings'] and manifest['citations']==references
        assert client.post('/decisions',json={**payload,'action':'reject'}).status_code==200
        assert client.get(f'/anchors/{aid}/candidates').json()[0]['citation_action']=='reject'
        other=client.post('/projects',json={'name':'Other'}).json()['id']
        assert client.post('/decisions',json={**payload,'project_id':other}).status_code==404

def test_manual_reference_validates_and_is_immediately_selected(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as client:
        authenticate(client);pid,_=upload(client,'paper.md',b'Synthetic manuscript body.');aid=analyze(client,pid)['anchors'][0]['id']
        response=client.post(f'/anchors/{aid}/references',json={'title':'A &lt;i&gt;sample&lt;/i&gt; study','source_url':'https://xueshu.baidu.com/usercenter/paper/show?paperid=synthetic'})
        assert response.status_code==200,response.text
        assert response.json()['citation_action']=='accept' and response.json()['provider']=='百度学术'
        assert response.json()['title']=='A sample study'
        assert client.post(f'/anchors/{aid}/references',json={'title':'Unsafe','source_url':'javascript:alert(1)'}).status_code==422
        assert client.post('/anchors/nonexistent/references',json={'title':'Missing','source_url':'https://example.org'}).status_code==404
        assert len(client.get(f'/anchors/{aid}/candidates').json())==1

def test_original_document_cannot_escape_project_root(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as client:
        authenticate(client);pid,doc=upload(client,'paper.md',b'Fixture manuscript body.');outside=tmp_path/'outside.md';outside.write_text('unrelated fixture')
        db.execute('UPDATE documents SET local_path=? WHERE id=?',(str(outside),doc['id']))
        assert client.get(f"/projects/{pid}/documents/{doc['id']}/original").status_code==400


def test_bulk_rejection_preserves_selected_references(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as client:
        authenticate(client);pid,_=upload(client,'paper.md',b'Synthetic manuscript for bulk rejection.')
        aid=analyze(client,pid)['anchors'][0]['id']
        selected=client.post(f'/anchors/{aid}/references',json={'title':'Selected reference','source_url':'https://example.org/selected'}).json()
        rejected=client.post(f'/projects/{pid}/anchors/{aid}/candidates/reject-pending',json={'reason':'Reject unselected fixtures'})
        assert rejected.status_code==200
        assert selected['id'] not in rejected.json()['rejected_ids']
        restored=client.get(f'/anchors/{aid}/candidates').json()[0]
        assert restored['citation_action']=='accept' and restored['decision_action'] is None
