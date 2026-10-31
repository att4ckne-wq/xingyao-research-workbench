"""Explicit manuscript scope, with read-only compatibility for single-document projects."""
from . import db

def document_id(project_id):
    selection=db.fetch_one("SELECT document_id FROM project_manuscripts WHERE project_id=?",(project_id,))
    if selection:return selection['document_id']
    docs=db.fetch_all("SELECT d.id FROM documents d LEFT JOIN document_roles r ON r.document_id=d.id WHERE d.project_id=? AND COALESCE(r.role,'manuscript')='manuscript' ORDER BY d.created_at,d.id",(project_id,))
    return docs[0]['id'] if len(docs)==1 else None

def anchors(project_id):
    did=document_id(project_id)
    return db.fetch_all("SELECT * FROM anchors WHERE project_id=? AND document_id=? ORDER BY paragraph_index,id",(project_id,did)) if did else []

def select(project_id,did):
    if not db.fetch_one("SELECT id FROM documents WHERE project_id=? AND id=?",(project_id,did)):raise ValueError('Document not found in this project')
    db.execute("INSERT INTO project_manuscripts(project_id,document_id) VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET document_id=excluded.document_id",(project_id,did))
