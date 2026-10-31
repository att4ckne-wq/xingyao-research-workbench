import io,json,zipfile,hashlib
from fastapi.testclient import TestClient
from backend import db,main,citations
from backend.tests.test_api_integration import configure_storage,authenticate
from backend.worker import claim_next_job


def upload(client,pid,name,text,role="manuscript"):
    response=client.post(f'/projects/{pid}/documents?role={role}',files={'file':(name,text.encode(),'text/markdown')});assert response.status_code==200,response.text
    did=response.json()['id'];job=client.post(f'/projects/{pid}/analyze',json={'document_id':did});assert job.status_code==200,job.text
    main.run_persisted_job(claim_next_job());return did


def make_project(client):return client.post('/projects',json={'name':'Synthetic manuscript'}).json()['id']


def reference(client,pid,anchor,title,url):
    r=client.post(f'/anchors/{anchor}/references',json={'title':title,'source_url':url,'author':'Synthetic Author'})
    assert r.status_code==200,r.text
    return r.json()['id']


def test_append_preserves_manuscript_and_full_save_scope(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c);first=upload(c,pid,'main.md','First paragraph.\n\nSecond paragraph.')
        before=c.get(f'/projects/{pid}').json();ids=[a['id'] for a in before['anchors']]
        supplement=upload(c,pid,'supplement.md','Supplement text.','supplement')
        after=c.get(f'/projects/{pid}').json();assert after['manuscript_document_id']==first
        assert [a['id'] for a in after['anchors']]==ids and len(after['documents'])==2
        save=c.patch(f'/projects/{pid}/document-content',json={'document_id':first,'anchors':[{'id':a['id'],'text':a['text']+' revised'} for a in before['anchors']],'reason':'Synthetic revision'})
        assert save.status_code==200,save.text
        assert db.fetch_one('SELECT text FROM anchors WHERE document_id=?',(supplement,))['text']=='Supplement text.'
        c.put(f'/projects/{pid}/manuscript',json={'document_id':supplement}).raise_for_status()
        assert c.patch(f'/projects/{pid}/document-content',json={'document_id':first,'anchors':[{'id':ids[0],'text':'stale'}],'reason':'Stale save'}).status_code==409


def test_projection_order_dedup_and_local_cancel(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c);upload(c,pid,'main.md','First.\n\nSecond.')
        anchors=c.get(f'/projects/{pid}').json()['anchors'];a,b=[x['id'] for x in anchors]
        later=reference(c,pid,b,'Later','https://example.org/later')
        early=reference(c,pid,a,'Earlier','https://example.org/early')
        view=c.get(f'/projects/{pid}/citation-projection').json()
        assert view['paragraph_citations']=={a:[1],b:[2]}
        c.post(f'/projects/{pid}/citation-placements',json={'anchor_id':b,'reference_id':early,'action':'accept'}).raise_for_status()
        view=c.post(f'/projects/{pid}/citation-placements',json={'anchor_id':a,'reference_id':early,'action':'reject'}).json()
        assert view['paragraph_citations'][a]==[] and view['paragraph_citations'][b]==[1,2]
        assert len(view['bibliography'])==2
        assert db.fetch_one('SELECT text FROM anchors WHERE id=?',(a,))['text']=='First.'
        exported=c.post(f'/projects/{pid}/exports',json={'override_statistics':False});assert exported.status_code==200,exported.text
        archive=c.get(exported.json()['download_url'])
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
            projection=json.loads(z.read('citation-projection.json'));assert projection==view
            assert 'Second.[1][2]' in z.read('manuscript.md').decode()
            assert len(json.loads(z.read('references.json')))==2


def test_supplement_only_requires_explicit_manuscript(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c);did=upload(c,pid,'source.md','Research material.','supplement')
        project=c.get(f'/projects/{pid}').json();assert project['anchors']==[] and project['manuscript_required']
        assert c.post(f'/projects/{pid}/analyze').status_code==409
        other=make_project(c)
        assert c.put(f'/projects/{other}/manuscript',json={'document_id':did}).status_code==404
        c.put(f'/projects/{pid}/manuscript',json={'document_id':did}).raise_for_status()
        assert len(c.get(f'/projects/{pid}').json()['anchors'])==1


def test_reparse_preserves_anchor_and_rejects_changed_content(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c);did=upload(c,pid,'main.md','Original paragraph.')
        before=c.get(f'/projects/{pid}').json()['anchors'][0];rid=reference(c,pid,before['id'],'Source','https://example.org/source')
        c.post(f'/projects/{pid}/analyze',json={'document_id':did}).raise_for_status();main.run_persisted_job(claim_next_job())
        assert c.get(f'/projects/{pid}').json()['anchors'][0]['id']==before['id']
        row=db.fetch_one('SELECT local_path FROM documents WHERE id=?',(did,))
        from pathlib import Path
        Path(row['local_path']).write_text('Changed paragraph.',encoding='utf8')
        job=c.post(f'/projects/{pid}/analyze',json={'document_id':did}).json()['job_id'];main.run_persisted_job(claim_next_job())
        assert db.fetch_one('SELECT state FROM jobs WHERE id=?',(job,))['state']=='failed'
        assert db.fetch_one('SELECT id FROM assets WHERE id=?',(rid,))
        assert c.get(f'/projects/{pid}').json()['anchors'][0]['text']=='Original paragraph.'


def test_equivalent_reference_cancel_only_removes_target_paragraph(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c);upload(c,pid,'main.md','First.\n\nSecond.')
        a,b=[r['id'] for r in c.get(f'/projects/{pid}').json()['anchors']]
        first=reference(c,pid,a,'Same Source','https://example.org/same')
        second=reference(c,pid,b,'Same Source','https://example.org/same')
        view=c.get(f'/projects/{pid}/citation-projection').json()
        assert view['paragraph_citations']=={a:[1],b:[1]}
        assert sorted(view['bibliography'][0]['reference_ids'])==sorted([first,second])
        result=c.post(f'/projects/{pid}/citation-placements',json={'anchor_id':a,'reference_id':second,'action':'reject'}).json()
        assert result['paragraph_citations']=={a:[],b:[1]}


def test_identity_rules_collections_and_foreign_placements(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c);upload(c,pid,'main.md','One.\n\nTwo.')
        a,b=[r['id'] for r in c.get(f'/projects/{pid}').json()['anchors']]
        first=reference(c,pid,a,'Same title','https://example.org/first')
        second=reference(c,pid,b,'Same title','https://example.org/second')
        for rid,doi in [(first,'https://doi.org/10.1234/ABC'),(second,'doi: 10.1234/abc')]:
            db.execute('UPDATE assets SET rationale=? WHERE id=?',(json.dumps({'metadata':{'doi':doi}}),rid))
        view=c.get(f'/projects/{pid}/citation-projection').json();assert len(view['bibliography'])==1
        db.execute('UPDATE assets SET rationale=? WHERE id=?',(json.dumps({'metadata':{'doi':'10.1234/different'}}),second))
        assert len(c.get(f'/projects/{pid}/citation-projection').json()['bibliography'])==2
        for rid in [first,second]:db.execute('UPDATE assets SET rationale=? WHERE id=?',('{}',rid))
        assert len(c.get(f'/projects/{pid}/citation-projection').json()['bibliography'])==2
        saved=c.post(f'/projects/{pid}/references',json={'title':'Only collection','source_url':'https://example.org/collection'});assert saved.status_code==200,saved.text
        view=c.get(f'/projects/{pid}/citation-projection').json();assert len(view['bibliography'])==2
        other=make_project(c);upload(c,other,'other.md','Other project.')
        alien=c.get(f'/projects/{other}').json()['anchors'][0]['id']
        assert c.post(f'/projects/{pid}/citation-placements',json={'anchor_id':alien,'reference_id':first,'action':'accept'}).status_code==404
        assert c.post(f'/projects/{other}/citation-placements',json={'anchor_id':alien,'reference_id':first,'action':'accept'}).status_code==404
        assert c.get(f'/projects/{other}/citation-projection').json()['bibliography']==[]


def test_portable_materials_and_projection_roundtrip(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path/'source')
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c);main_doc=upload(c,pid,'main.md','Original main.')
        supplementary=upload(c,pid,'supplement.md','Supplement.','supplement')
        a=c.get(f'/projects/{pid}').json()['anchors'][0]['id'];rid=reference(c,pid,a,'Source','https://example.org/source')
        c.post(f'/projects/{pid}/citation-placements',json={'anchor_id':a,'reference_id':rid,'action':'accept'}).raise_for_status()
        expected=c.get(f'/projects/{pid}/citation-projection').json()
        package=c.get(f'/projects/{pid}/package');assert package.status_code==200,package.text
    configure_storage(monkeypatch,tmp_path/'destination')
    with TestClient(main.app) as c:
        authenticate(c)
        result=c.post('/projects/import',files={'file':('synthetic.zip',package.content,'application/zip')});assert result.status_code==200,result.text
        restored=c.get(f'/projects/{pid}').json();assert restored['manuscript_document_id']==main_doc and len(restored['documents'])==2
        assert c.get(f'/projects/{pid}/citation-projection').json()==expected
        role=db.fetch_one('SELECT role FROM document_roles WHERE document_id=?',(supplementary,));assert role['role']=='supplement'
        with db.connect() as conn:assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_export_fonts_are_explicit(tmp_path,monkeypatch):
    from backend.academic import draft_export
    from docx import Document
    from docx.oxml.ns import qn
    import fitz
    out=tmp_path/'fonts';out.mkdir()
    docx=draft_export(out,'中英 Title','正文 Mixed 123.','docx')
    doc=Document(docx)
    for name in ['Normal','Title','Heading 1']:
        fonts=doc.styles[name].element.rPr.rFonts
        assert fonts.get(qn('w:eastAsia'))=='SimSun'
        assert fonts.get(qn('w:ascii'))=='Times New Roman'
    pdf=draft_export(out,'中英 Title','正文 Mixed 123.','pdf')
    with fitz.open(pdf) as doc:
        names={f[3] for p in doc for f in p.get_fonts()}
        assert any('Times' in name for name in names) and any('SimSun' in name for name in names)
        assert 'Mixed 123.' in ' '.join(''.join(p.get_text() for p in doc).split())


def test_checkpoint_restores_material_roles_and_citation_positions(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c);did=upload(c,pid,'main.md','One paragraph.')
        sid=upload(c,pid,'supplement.md','Supplement.','supplement')
        aid=c.get(f'/projects/{pid}').json()['anchors'][0]['id'];rid=reference(c,pid,aid,'Source','https://example.org/source')
        c.post(f'/projects/{pid}/citation-placements',json={'anchor_id':aid,'reference_id':rid,'action':'accept'}).raise_for_status()
        expected=c.get(f'/projects/{pid}/citation-projection').json()
        checkpoint=db.fetch_one('SELECT id FROM checkpoints WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(pid,))['id']
        c.put(f'/projects/{pid}/manuscript',json={'document_id':sid}).raise_for_status()
        restored=c.post(f'/checkpoints/{checkpoint}/restore');assert restored.status_code==200,restored.text
        assert c.get(f'/projects/{pid}').json()['manuscript_document_id']==did
        assert c.get(f'/projects/{pid}/citation-projection').json()==expected
        assert db.fetch_one('SELECT role FROM document_roles WHERE document_id=?',(sid,))['role']=='supplement'


def test_unplaced_collection_exports_without_manuscript_numbers(tmp_path,monkeypatch):
    configure_storage(monkeypatch,tmp_path)
    with TestClient(main.app) as c:
        authenticate(c);pid=make_project(c)
        saved=c.post(f'/projects/{pid}/references',json={'title':'Saved source','source_url':'https://example.org/saved'});saved.raise_for_status()
        view=c.get(f'/projects/{pid}/citation-projection').json()
        assert view['bibliography']==[] and view['unplaced_references'][0]['number'] is None
        exported=c.post(f'/projects/{pid}/exports',json={'override_statistics':False});exported.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(c.get(exported.json()['download_url']).content)) as z:
            refs=json.loads(z.read('references.json'));assert refs[0]['asset_id']==saved.json()['id'] and refs[0]['number'] is None
            assert b'[1]' not in z.read('manuscript.md') and b'[1]' not in z.read('references.md')
            assert b'Saved source' in z.read('references.bib')
