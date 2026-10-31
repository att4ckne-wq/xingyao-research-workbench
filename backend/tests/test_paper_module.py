from pathlib import Path
import io,json
import pytest
from fastapi.testclient import TestClient
from backend import db as wb,main
from backend import paper_router as route
from Paper.paper_app import db as paper
from .test_api_integration import configure_storage,authenticate

@pytest.fixture()
def setup_client(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path/'workbench')
    folder=tmp_path/'paper'
    monkeypatch.setattr(paper,'DATA_ROOT',folder);monkeypatch.setattr(paper,'DB_PATH',folder/'experiment.db');monkeypatch.setattr(paper,'ATTACHMENT_ROOT',folder/'attachments')
    with TestClient(main.app) as client:
        yield client

def start(client):
    authenticate(client)
    assert client.post('/paper/initialize').status_code==200
    p=client.post('/paper/projects',json={'name':'Synthetic project'}).json()
    t=client.get('/paper/event-types').json()[0]
    body={'project_id':p['id'],'event_type_id':t['id'],'title':'Synthetic record','occurred_at':'2026-10-29 12:30',
          'purpose':'Original purpose','content':'![image](att://abc)\n原始正文','tags':['one'],'started_at':None,'ended_at':None}
    r=client.post('/paper/records',json=body);assert r.status_code==200,r.text
    return p,body,r.json()

def patch(client,record,body,**changes):
    return client.patch(f"/paper/records/{record['id']}",json={**body,**changes,'expected_version':record['_version']})

def test_auth_gate_and_lazy_initialization(setup_client):
    c=setup_client
    assert not paper.DB_PATH.exists()
    for method,path in [('get','/paper/status'),('get','/paper/projects'),('post','/paper/initialize')]:
        assert getattr(c,method)(path).status_code==401
    authenticate(c)
    assert c.get('/paper/status').json()['initialized'] is False
    assert not paper.DB_PATH.exists()
    assert c.get('/paper/projects').status_code==409
    assert not paper.DB_PATH.exists()

def test_independent_storage_crud_and_preferences(setup_client):
    c=setup_client;p,body,r=start(c)
    assert wb.fetch_one('SELECT COUNT(*) AS n FROM projects')['n']==0
    assert paper.fetch_one('SELECT COUNT(*) AS n FROM projects')['n']==1
    changed=patch(c,r,body,title='Updated record');assert changed.status_code==200
    row=changed.json();assert row['content']==body['content']
    assert c.get('/paper/records?q=Updated').json()[0]['id']==r['id']
    assert len(c.get(f"/paper/records/{r['id']}/revisions").json())==1
    assert c.patch('/account/preferences',json={'last_screen':'paper'}).status_code==200
    assert c.get('/paper/stats').json()['overall']['records']==1

def test_native_web_conflict_within_same_second_preserves_revision(setup_client,monkeypatch):
    c=setup_client;p,body,r=start(c)
    monkeypatch.setattr(paper,'now',lambda:'2026-10-29T12:00:00+00:00')
    first=patch(c,r,body,result='Web result');assert first.status_code==200
    native=paper.get_record(r['id']);token=paper.row_version(native)
    receipt={};paper.save_record({**body,'result':'Native result'},r['id'],expected_version=token,version_receipt=receipt)
    stale=patch(c,first.json(),body,result='Stale web');assert stale.status_code==409
    assert paper.get_record(r['id'])['result']=='Native result'
    assert len(paper.list_revisions(r['id']))==2
    with pytest.raises(paper.ConcurrentEditError):paper.save_record(body,r['id'],expected_version=token)
    assert receipt['version']==paper.row_version(paper.get_record(r['id']))

def test_project_conflict_and_input_validation(setup_client):
    c=setup_client;p,body,r=start(c)
    payload={'name':'Renamed','description':'notes','status':'已暂停','expected_version':p['_version']}
    assert c.patch(f"/paper/projects/{p['id']}",json=payload).status_code==200
    assert c.patch(f"/paper/projects/{p['id']}",json={**payload,'name':'Stale'}).status_code==409
    for change in [{'title':'  '},{'occurred_at':'not-date'},{'local_path':'E:/private'}]:
        assert c.post('/paper/records',json={**body,**change}).status_code==422
    assert c.post('/paper/projects',json={'name':' '}).status_code==422

def test_trash_restore_and_revision_restore(setup_client):
    c=setup_client;p,body,r=start(c)
    r2=patch(c,r,body,content='Second body').json()
    rev=c.get(f"/paper/records/{r['id']}/revisions").json()[0]
    restored=c.post(f"/paper/records/{r['id']}/revisions/{rev['id']}/restore",json={'expected_version':r2['_version']})
    assert restored.status_code==200 and restored.json()['content']==body['content']
    assert c.post(f"/paper/records/{r['id']}/trash",json={'expected_version':r2['_version']}).status_code==409
    assert c.post(f"/paper/records/{r['id']}/trash",json={'expected_version':restored.json()['_version']}).status_code==200
    assert c.get(f"/paper/records/{r['id']}").status_code==404
    assert c.get('/paper/trash').json()[0]['id']==r['id']
    assert c.post(f"/paper/records/{r['id']}/restore").status_code==200

def test_attachment_signatures_safe_paths_and_no_path_disclosure(setup_client,tmp_path):
    c=setup_client;p,body,r=start(c)
    url=f"/paper/records/{r['id']}/attachments"
    assert c.post(url,files={'file':('fake.pdf',b'not pdf','application/pdf')}).status_code==415
    response=c.post(url,files={'file':('data.csv',b'x,y\n1,2\n','text/csv')});assert response.status_code==200,response.text
    a=response.json()['attachments'][0];assert 'local_path' not in a
    download=c.get(f"/paper/attachments/{a['id']}");assert download.content==b'x,y\n1,2\n'
    assert download.headers['x-content-type-options']=='nosniff'
    secret=tmp_path/'outside.txt';secret.write_text('outside',encoding='utf-8')
    paper.execute('UPDATE attachments SET local_path=? WHERE id=?',(str(secret),a['id']))
    assert c.get(f"/paper/attachments/{a['id']}").status_code==403
    assert c.get(f"/paper/records/{r['id']}/export").content.decode().find('Original purpose')>=0

def test_recognition_confirmation_binding_and_replay_dedup(setup_client,monkeypatch):
    c=setup_client;start(c)
    wb.execute("INSERT INTO model_profiles VALUES(?,?,?,?,?,?,?,?)",('fake','Synthetic model','online','https://example.invalid','synthetic',1,wb.now(),wb.now()))
    calls=[]
    def recognize(text,profile,**kwargs):
        calls.append((text,profile.id))
        return route.recognition.Result(fields={k:('Synthetic purpose.' if k=='purpose' else '') for k in ['purpose','conditions','steps','result','conclusion','content']},evidence=(('purpose',0,18),),unassigned='')
    monkeypatch.setattr(route.recognition,'recognize',recognize)
    preview=c.post('/paper/recognition/preview',json={'text':'Synthetic purpose.'}).json()
    assert not calls
    send={k:preview[k] for k in ('text','nonce','expires_at','token')};send['confirmed']=False
    assert c.post('/paper/recognition/run',json=send).status_code==400
    send['confirmed']=True
    assert c.post('/paper/recognition/run',json={**send,'text':'Changed text'}).status_code==409
    result=c.post('/paper/recognition/run',json=send);assert result.status_code==200,result.text
    assert c.post('/paper/recognition/run',json=send).json()==result.json()
    assert len(calls)==1
    wb.execute("UPDATE model_profiles SET model='changed' WHERE id='fake'")
    assert c.post('/paper/recognition/run',json=send).status_code==409
    assert len(calls)==1

