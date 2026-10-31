import io
import zipfile
from .test_paper_module import setup_client, start, patch


def test_batch_conflict_is_atomic(setup_client):
    c=setup_client;project,body,first=start(c)
    second=c.post('/paper/records',json={**body,'title':'second'}).json()
    changed=patch(c,second,body,title='changed').json()
    response=c.post('/paper/records/batch-trash',json={'items':[{'id':first['id'],'expected_version':first['_version']},{'id':second['id'],'expected_version':second['_version']}]})
    assert response.status_code==409
    assert c.get(f"/paper/records/{first['id']}").status_code==200
    assert c.get(f"/paper/records/{changed['id']}").status_code==200


def test_zip_keeps_attachment_and_relative_reference(setup_client):
    c=setup_client;project,body,record=start(c)
    uploaded=c.post(f"/paper/records/{record['id']}/attachments",files={'file':('data.csv',b'a,b\n1,2\n','text/csv')}).json()
    attachment=uploaded['attachments'][0]
    record=patch(c,record,body,content=f"[data](att://{attachment['id']}#50x50)").json()
    response=c.get(f"/paper/records/{record['id']}/export.zip")
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        name=f"attachments/{attachment['id']}.csv"
        assert archive.read(name)==b'a,b\n1,2\n'
        assert f']({name})' in archive.read('record.md').decode()
    response=c.get(f"/paper/projects/{project['id']}/export.zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read(name)==b'a,b\n1,2\n'
        assert f'](../{name})' in archive.read(f"records/{record['id']}.md").decode()



def test_project_recycle_blocks_records_and_attachments_until_restore(setup_client):
    c=setup_client;project,body,record=start(c)
    uploaded=c.post(f"/paper/records/{record['id']}/attachments",files={'file':('data.csv',b'a,b\n1,2\n','text/csv')}).json()
    aid=uploaded['attachments'][0]['id']
    assert c.post(f"/paper/projects/{project['id']}/trash",json={'expected_version':project['_version']}).status_code==200
    assert c.get(f"/paper/records/{record['id']}").status_code==404
    assert c.get(f"/paper/attachments/{aid}").status_code==404
    assert c.get(f"/paper/records/{record['id']}/export.zip").status_code==404
    assert not any(r['id']==record['id'] for r in c.get('/paper/records').json())
    assert c.post(f"/paper/projects/{project['id']}/restore").status_code==200
    assert c.get(f"/paper/records/{record['id']}").status_code==200
    assert c.get(f"/paper/attachments/{aid}").content==b'a,b\n1,2\n'


def test_event_type_edit_preserves_builtin(setup_client):
    c=setup_client;start(c)
    builtin=c.get('/paper/event-types').json()[0]
    created=c.post('/paper/event-types',json={'name':'Custom','color':'#123456'}).json()
    assert c.patch(f"/paper/event-types/{created['id']}",json={'name':'Renamed','color':'#654321'}).status_code==200
    updated=next(t for t in c.get('/paper/event-types').json() if t['id']==created['id'])
    assert (updated['name'],updated['color'])==('Renamed','#654321')
    assert c.patch(f"/paper/event-types/{builtin['id']}",json={'name':'Forbidden','color':'#123456'}).status_code==409


def test_visible_record_order_retains_hidden_relative_order(setup_client):
    c=setup_client;project,body,first=start(c)
    second=c.post('/paper/records',json={**body,'title':'second'}).json()
    third=c.post('/paper/records',json={**body,'title':'third'}).json()
    initial=[r['id'] for r in c.get('/paper/records').json()]
    visible=[first['id'],third['id']]; visible.sort(key=initial.index)
    expected=[visible[1] if x==visible[0] else visible[0] if x==visible[1] else x for x in initial]
    assert c.post('/paper/records/order',json={'ids':visible[::-1]}).status_code==200
    assert [r['id'] for r in c.get('/paper/records').json()]==expected
    assert c.post('/paper/records/order',json={'ids':[first['id'],first['id']]}).status_code==422


def test_project_pdf_extracts_long_chinese_and_mixed_number(setup_client):
    import fitz
    c=setup_client;project,body,record=start(c)
    text='长中文实验记录混排数值1.25验证完整性' * 18
    assert patch(c,record,body,content=text).status_code==200
    response=c.get(f"/paper/projects/{project['id']}/export.pdf")
    assert response.status_code==200
    document=fitz.open(stream=response.content,filetype='pdf')
    extracted=''.join(page.get_text() for page in document).replace('\n','')
    assert text in extracted
    assert '1.25' in extracted
