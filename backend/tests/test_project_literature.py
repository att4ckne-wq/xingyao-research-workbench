import json
import zipfile

from fastapi.testclient import TestClient
from backend import db, main
from backend.models import AssistantChatRequest
from backend.tests.test_api_integration import configure_storage, authenticate


def create_project(client, name='Synthetic topic'):
    result=client.post('/projects',json={'name':name,'purpose':'research'})
    assert result.status_code==200,result.text
    return result.json()['id']


def test_topic_literature_full_citation_lifecycle_without_fake_document(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    async def search(query):
        return [{'title':'Synthetic reference','source_url':'https://doi.org/10.1234/synthetic',
                 'provider':'Crossref','author':'A. Example','doi':'10.1234/synthetic','year':2026}],{'successful_providers':['Crossref'],'failed_providers':[]}
    async def verify(*args): return {'status':'matched','message':'Synthetic metadata matched'}
    monkeypatch.setattr(main,'search_literature',search)
    monkeypatch.setattr(main,'verify_reference',verify)
    with TestClient(main.app) as client:
        authenticate(client);pid=create_project(client)
        result=client.post(f'/projects/{pid}/literature',json={'query':'synthetic topic'})
        assert result.status_code==200,result.text
        item=result.json()[0];aid=item['id']
        assert item['anchor_id'] is None and item['reuse_status']=='citation_only' and not item['license']['verified']
        assert client.post(f'/projects/{pid}/literature',json={'query':'synthetic topic'}).json()[0]['id']==aid
        state=client.get(f'/projects/{pid}').json()
        assert state['documents']==[] and state['anchors']==[]
        assert client.post(f'/assets/{aid}/verify-reference').json()['status']=='matched'
        decision={'project_id':pid,'target_type':'citation','target_id':aid,'action':'accept','reason':'Synthetic reference selected'}
        assert client.post('/decisions',json=decision).status_code==200
        context=main._academic_context(AssistantChatRequest(project_id=pid,message='Prepare outline',task='outline'))
        assert context['references'][0]['doi']=='10.1234/synthetic'
        from backend.services import create_export
        export_id,_=create_export(pid,False,None)
        with zipfile.ZipFile(db.fetch_one('SELECT archive_path FROM exports WHERE id=?',(export_id,))['archive_path']) as archive:
            assert json.loads(archive.read('references.json'))[0]['asset_id']==aid
            assert '10.1234/synthetic' in archive.read('references.bib').decode('utf-8')
        assert client.post('/decisions',json={**decision,'action':'reject'}).status_code==200
        assert main._academic_context(AssistantChatRequest(project_id=pid,message='Prepare outline',task='outline'))['references']==[]
        assert client.get(f'/projects/{pid}/literature').json()[0]['citation_action']=='reject'


def test_project_reference_identity_and_image_permission_are_separate(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as client:
        authenticate(client);pid=create_project(client);other=create_project(client,'Other topic')
        item=client.post(f'/projects/{pid}/references',json={'title':'Manual synthetic source','source_url':'https://example.org/synthetic'}).json()
        assert item['citation_action']=='accept'
        assert client.get(f'/projects/{other}/literature').json()==[]
        assert client.post('/decisions',json={'project_id':other,'target_type':'citation','target_id':item['id'],'action':'accept','reason':'Must not cross project'}).status_code==404
        assert client.post('/decisions',json={'project_id':pid,'target_type':'asset','target_id':item['id'],'action':'accept','reason':'Cannot reuse images'}).status_code==409
        assert client.patch(f'/assets/{item["id"]}',json={'reason':'Cannot grant image rights','reuse_status':'safe_open','license_verified':True}).status_code==409
        patched=client.patch(f'/assets/{item["id"]}',json={'reason':'Correct synthetic title','title':'Corrected manual source'})
        assert patched.status_code==200,patched.text
        assert client.get(f'/projects/{pid}/literature').json()[0]['title']=='Corrected manual source'
        assert client.post(f'/projects/{pid}/references',json={'title':'Unsafe URL','source_url':'https://name:secret@example.org'}).status_code==422
        client.cookies.clear()
        assert client.get(f'/projects/{pid}/literature').status_code==401


def test_working_draft_routes_save_restore_and_reject_stale_version(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as client:
        authenticate(client);pid=create_project(client);other=create_project(client)
        assert client.get(f'/projects/{pid}/working-draft').json()=={'content':'','version':0}
        saved=client.post(f'/projects/{pid}/working-draft',json={'content':'Synthetic author text','expected_version':0})
        assert saved.status_code==200,saved.text
        assert saved.json()=={'content':'Synthetic author text','version':1}
        assert client.post(f'/projects/{pid}/working-draft',json={'content':'Stale overwrite','expected_version':0}).status_code==409
        assert client.get(f'/projects/{pid}/working-draft').json()==saved.json()
        assert client.get(f'/projects/{other}/working-draft').json()=={'content':'','version':0}
        for version in [-1,True,1.2,'1']:
            assert client.post(f'/projects/{pid}/working-draft',json={'content':'Invalid','expected_version':version}).status_code==422
        assert client.post(f'/projects/{pid}/working-draft',json={'content':'x'*200001,'expected_version':1}).status_code==422
        assert client.get('/projects/nonexistent/working-draft').status_code==404
        client.cookies.clear()
        assert client.get(f'/projects/{pid}/working-draft').status_code==401
