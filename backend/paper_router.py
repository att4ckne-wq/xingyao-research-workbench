"""Authenticated Paper module. Own storage; no Qt imports or startup migration."""
from __future__ import annotations
import hashlib,hmac,io,json,os,re,secrets,sqlite3,tempfile,threading,time,zipfile
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter,Depends,File,HTTPException,Response,UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field,field_validator
from Paper.paper_app import db as paper_db
from Paper.paper_app.models import PROJECT_STATUS,RECORD_FIELDS
from Paper.paper_app.security import ensure_within,safe_filename
from Paper.paper_app.services import recognition
from Paper.paper_app.services.export import record_to_markdown
from . import db as workbench_db

def no_cache(response: Response):response.headers['Cache-Control']='no-store'
router=APIRouter(prefix='/paper',tags=['Paper'],dependencies=[Depends(no_cache)])
MAX_UPLOAD=50*1024*1024

def ready():
    if not paper_db.DB_PATH.is_file():raise HTTPException(409,'Paper尚未初始化，请先开始使用。')

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class ProjectInput(Strict):
    name:str=Field(min_length=1,max_length=500)
    description:str=Field(default='',max_length=50000)
    status:str=Field(default='进行中',min_length=1,max_length=100)
    @field_validator('name')
    @classmethod
    def trim_name(cls,v):
        if not v.strip():raise ValueError('名称不能为空')
        return v.strip()
class ProjectUpdate(ProjectInput):expected_version:str=Field(pattern=r'^[0-9a-f]{64}$')
class RecordInput(Strict):
    project_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    event_type_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    title:str=Field(min_length=1,max_length=1000)
    occurred_at:str
    purpose:str=Field(default='',max_length=500000)
    conditions:str=Field(default='',max_length=500000)
    steps:str=Field(default='',max_length=500000)
    result:str=Field(default='',max_length=500000)
    conclusion:str=Field(default='',max_length=500000)
    content:str=Field(default='',max_length=1000000)
    tags:list[str]=Field(default_factory=list,max_length=100)
    started_at:str|None=None
    ended_at:str|None=None
    @field_validator('title')
    @classmethod
    def trim_title(cls,v):
        if not v.strip():raise ValueError('标题不能为空')
        return v.strip()
    @field_validator('occurred_at','started_at','ended_at')
    @classmethod
    def valid_date(cls,v):
        if v is not None:datetime.strptime(v,'%Y-%m-%d %H:%M')
        return v
    @field_validator('tags')
    @classmethod
    def valid_tags(cls,v):
        if any(len(x)>100 for x in v):raise ValueError('标签过长')
        return v
class RecordUpdate(RecordInput):expected_version:str=Field(pattern=r'^[0-9a-f]{64}$')
class VersionInput(Strict):expected_version:str=Field(pattern=r'^[0-9a-f]{64}$')
class BatchItem(VersionInput):id:str=Field(pattern=r"^[0-9a-f]{32}$")
class BatchInput(Strict):items:list[BatchItem]=Field(min_length=1,max_length=500)
class OrderInput(Strict):ids:list[str]=Field(min_length=1,max_length=2000)
class EventInput(Strict):name:str=Field(min_length=1,max_length=100);color:str=Field(default='#8A75BD',pattern=r'^#[0-9A-Fa-f]{6}$')
class RecognitionText(Strict):text:str=Field(min_length=1,max_length=20000)
class RecognitionSend(RecognitionText):
    confirmed:bool
    nonce:str=Field(pattern=r'^[0-9a-f]{32}$')
    expires_at:int
    token:str=Field(pattern=r'^[0-9a-f]{64}$')

def project_row(project_id):
    row=paper_db.fetch_one('SELECT * FROM projects WHERE id=? AND trashed_at IS NULL',(project_id,))
    if not row:raise HTTPException(404,'项目不存在或已在回收站中。')
    return {**row,'_version':paper_db.row_version(row)}

def record_row(record_id):
    row=paper_db.get_record(record_id)
    if not row or row.get('trashed_at'):raise HTTPException(404,'记录不存在或已在回收站中。')
    project_row(row['project_id'])
    row['_version']=paper_db.row_version(row)
    row['attachments']=[{k:v for k,v in a.items() if k!='local_path'} for a in paper_db.list_attachments(record_id)]
    return row

@router.get('/status')
def status():
    return {'initialized':paper_db.DB_PATH.is_file(),'data_root':str(paper_db.DATA_ROOT),'project_statuses':PROJECT_STATUS,
            'fields':[{'key':k,'label':v} for k,v in RECORD_FIELDS]+[{'key':'content','label':'补充记录'}]}

@router.post('/initialize')
def initialize():
    if not paper_db.DB_PATH.exists():paper_db.init_db()
    return status()

@router.get('/projects',dependencies=[Depends(ready)])
def projects():return [{**p,'_version':paper_db.row_version(p)} for p in paper_db.list_projects()]

@router.post('/projects',dependencies=[Depends(ready)])
def create_project(payload:ProjectInput):
    row=paper_db.create_project(payload.name,payload.description)
    if payload.status!='进行中':paper_db.update_project(row['id'],payload.name,payload.description,payload.status)
    return project_row(row['id'])

@router.patch('/projects/{project_id}',dependencies=[Depends(ready)])
def update_project(project_id:str,payload:ProjectUpdate):
    project_row(project_id)
    try:paper_db.update_project(project_id,**payload.model_dump())
    except paper_db.ConcurrentEditError as ex:raise HTTPException(409,str(ex)) from None
    return project_row(project_id)

@router.get('/projects-trash',dependencies=[Depends(ready)])
def trashed_projects():return paper_db.list_projects(include_trashed=True)

@router.post('/projects/{project_id}/trash',dependencies=[Depends(ready)])
def trash_project(project_id:str,payload:VersionInput):
    with paper_db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM projects WHERE id=? AND trashed_at IS NULL',(project_id,)).fetchone()
        if not row or paper_db.row_version(dict(row))!=payload.expected_version:raise HTTPException(409,'项目已变化，请刷新后重试。')
        timestamp=paper_db.now();conn.execute('UPDATE projects SET trashed_at=?,updated_at=? WHERE id=?',(timestamp,timestamp,project_id))
    return {'ok':True}

@router.post('/projects/{project_id}/restore',dependencies=[Depends(ready)])
def restore_project(project_id:str):
    with paper_db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM projects WHERE id=? AND trashed_at IS NOT NULL',(project_id,)).fetchone()
        if not row:raise HTTPException(404,'项目不在回收站。')
        conn.execute('UPDATE projects SET trashed_at=NULL,updated_at=? WHERE id=?',(paper_db.now(),project_id))
    return project_row(project_id)

@router.get('/event-types',dependencies=[Depends(ready)])
def event_types():return paper_db.list_event_types()

@router.post('/event-types',dependencies=[Depends(ready)])
def create_event_type(payload:EventInput):
    if not payload.name.strip():raise HTTPException(422,'名称不能为空。')
    try:return paper_db.create_event_type(payload.name.strip(),payload.color)
    except sqlite3.IntegrityError:raise HTTPException(409,'已有同名事件类型。') from None

@router.patch('/event-types/{type_id}',dependencies=[Depends(ready)])
def update_event_type(type_id:str,payload:EventInput):
    name=payload.name.strip()
    if not name:raise HTTPException(422,'名称不能为空。')
    with paper_db.connect() as conn:
        row=conn.execute('SELECT * FROM event_types WHERE id=?',(type_id,)).fetchone()
        if not row:raise HTTPException(404,'事件类型不存在。')
        if row['builtin']:raise HTTPException(409,'内置类型不能修改。')
        try:conn.execute('UPDATE event_types SET name=?,color=? WHERE id=?',(name,payload.color,type_id))
        except sqlite3.IntegrityError:raise HTTPException(409,'已有同名事件类型。') from None
    return {'ok':True}

@router.get('/records',dependencies=[Depends(ready)])
def records(project_id:str|None=None,event_type_id:str|None=None,q:str|None=None):
    if q and len(q)>200:raise HTTPException(422,'搜索词过长。')
    rows=paper_db.list_records(project_id=project_id,event_type_id=event_type_id,query=q)
    active_projects={p['id'] for p in paper_db.list_projects()}
    rows=[row for row in rows if row['project_id'] in active_projects]
    for row in rows:
        raw=paper_db.fetch_one('SELECT * FROM records WHERE id=?',(row['id'],))
        if raw:row['_version']=paper_db.row_version(raw)
    return rows

@router.post('/records',dependencies=[Depends(ready)])
def create_record(payload:RecordInput):
    project_row(payload.project_id)
    try:record_id=paper_db.save_record(payload.model_dump())
    except (sqlite3.IntegrityError,ValueError):raise HTTPException(422,'项目或事件类型不可用。') from None
    return record_row(record_id)

@router.post('/records/batch-trash',dependencies=[Depends(ready)])
def batch_trash(payload:BatchInput):
    if len({x.id for x in payload.items})!=len(payload.items):raise HTTPException(422,'记录不可重复。')
    with paper_db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for item in payload.items:
            row=conn.execute('SELECT * FROM records WHERE id=? AND trashed_at IS NULL AND EXISTS (SELECT 1 FROM projects WHERE projects.id=records.project_id AND projects.trashed_at IS NULL)',(item.id,)).fetchone()
            if not row or paper_db.row_version(dict(row))!=item.expected_version:raise HTTPException(409,'列表中记录已变化，未回收任何记录，请刷新。')
        timestamp=paper_db.now()
        conn.executemany('UPDATE records SET trashed_at=?,updated_at=? WHERE id=?',[(timestamp,timestamp,item.id) for item in payload.items])
    return {'ok':True}

@router.post('/records/order',dependencies=[Depends(ready)])
def record_order(payload:OrderInput):
    if len(set(payload.ids))!=len(payload.ids):raise HTTPException(422,'记录不可重复。')
    active={r['id'] for r in records()}
    if not set(payload.ids).issubset(active):raise HTTPException(409,'记录列表已变化，请刷新后重试。')
    paper_db.reorder_visible_records(payload.ids)
    return {'ok':True}

@router.get('/records/{record_id}',dependencies=[Depends(ready)])
def get_record(record_id:str):return record_row(record_id)

@router.patch('/records/{record_id}',dependencies=[Depends(ready)])
def update_record(record_id:str,payload:RecordUpdate):
    record_row(record_id);project_row(payload.project_id)
    try:paper_db.save_record(payload.model_dump(exclude={'expected_version'}),record_id,reason='网页编辑保存',expected_version=payload.expected_version)
    except paper_db.ConcurrentEditError as ex:raise HTTPException(409,str(ex)) from None
    except (sqlite3.IntegrityError,ValueError):raise HTTPException(422,'项目或事件类型不可用。') from None
    return record_row(record_id)

@router.post('/records/{record_id}/trash',dependencies=[Depends(ready)])
def trash_record(record_id:str,payload:VersionInput):
    with paper_db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM records WHERE id=? AND trashed_at IS NULL AND EXISTS (SELECT 1 FROM projects WHERE projects.id=records.project_id AND projects.trashed_at IS NULL)',(record_id,)).fetchone()
        if not row:raise HTTPException(404,'记录不存在。')
        if paper_db.row_version(dict(row))!=payload.expected_version:raise HTTPException(409,'记录已变化，请载入后重试。')
        conn.execute('UPDATE records SET trashed_at=?,updated_at=? WHERE id=?',(paper_db.now(),paper_db.now(),record_id))
    return {'ok':True}

@router.get('/trash',dependencies=[Depends(ready)])
def trash():
    active={p['id'] for p in paper_db.list_projects()}
    return [r for r in paper_db.list_records(include_trashed=True) if r['project_id'] in active]

@router.post('/records/{record_id}/restore',dependencies=[Depends(ready)])
def restore_record(record_id:str):
    row=paper_db.fetch_one('SELECT * FROM records WHERE id=? AND trashed_at IS NOT NULL',(record_id,))
    if not row:raise HTTPException(404,'记录不在回收站中。')
    project_row(row['project_id']);paper_db.restore_record(record_id)
    return record_row(record_id)

@router.get('/records/{record_id}/revisions',dependencies=[Depends(ready)])
def revisions(record_id:str):record_row(record_id);return paper_db.list_revisions(record_id)

@router.post('/records/{record_id}/revisions/{revision_id}/restore',dependencies=[Depends(ready)])
def restore_revision(record_id:str,revision_id:str,payload:VersionInput):
    record_row(record_id)
    row=paper_db.fetch_one('SELECT * FROM record_revisions WHERE id=? AND record_id=?',(revision_id,record_id))
    if not row:raise HTTPException(404,'修订不存在。')
    old=json.loads(row['snapshot_json']);fields=set(RecordInput.model_fields)
    data={k:old[k] for k in fields if k in old}
    if isinstance(data.get('tags'),str):data['tags']=json.loads(data['tags'])
    validated=RecordInput(**data);project_row(validated.project_id)
    try:paper_db.save_record(validated.model_dump(),record_id,reason='网页恢复修订',expected_version=payload.expected_version)
    except paper_db.ConcurrentEditError as ex:raise HTTPException(409,str(ex)) from None
    return record_row(record_id)

@router.get('/stats',dependencies=[Depends(ready)])
def stats(project_id:str|None=None):
    return {'overall':paper_db.overall_counts(),'monthly':paper_db.monthly_counts(project_id),'event_types':paper_db.event_type_counts(project_id)}

@router.post('/records/{record_id}/attachments',dependencies=[Depends(ready)])
async def upload(record_id:str,file:UploadFile=File(...)):
    record_row(record_id);filename=safe_filename(file.filename or 'attachment')
    suffix=Path(filename).suffix.lower()
    if suffix not in paper_db.ATTACHMENT_SUFFIXES:raise HTTPException(415,'不支持的附件格式。')
    staging=paper_db.DATA_ROOT/'.web-upload';staging.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging) as directory:
        source=Path(directory)/filename;size=0
        with source.open('xb') as handle:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>MAX_UPLOAD:raise HTTPException(413,'附件超过50 MB。')
                handle.write(chunk)
        try:paper_db.add_attachment(record_id,source,display_name=filename)
        except ValueError as ex:raise HTTPException(415,str(ex)) from None
    return record_row(record_id)

@router.get('/attachments/{attachment_id}',dependencies=[Depends(ready)])
def attachment(attachment_id:str):
    row=paper_db.fetch_one('SELECT * FROM attachments WHERE id=?',(attachment_id,))
    if not row:raise HTTPException(404,'附件不存在。')
    record_row(row['record_id'])
    try:path=ensure_within(paper_db.ATTACHMENT_ROOT,Path(row['local_path']))
    except ValueError:raise HTTPException(403,'附件路径不在当前Paper数据目录。') from None
    if not path.is_file():raise HTTPException(404,'附件文件缺失，请保留记录并核对备份。')
    inline=row['media_type'] in {'image/png','image/jpeg','image/tiff','application/pdf'}
    return FileResponse(path,media_type=row['media_type'],filename=row['filename'],content_disposition_type='inline' if inline else 'attachment',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})

@router.get('/records/{record_id}/export',dependencies=[Depends(ready)])
def export_record(record_id:str):
    row=record_row(record_id)
    text=record_to_markdown(row,attachments=[])
    return Response(text,media_type='text/markdown; charset=utf-8',headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="paper-{record_id}.md"'})

@router.get('/records/{record_id}/export.zip',dependencies=[Depends(ready)])
def export_record_zip(record_id:str):
    row=record_row(record_id)
    attachments=paper_db.list_attachments(record_id)
    output=io.BytesIO()
    text=record_to_markdown(row,attachments=[])
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        links=[]
        for item in attachments:
            try:path=ensure_within(paper_db.ATTACHMENT_ROOT,Path(item['local_path']))
            except ValueError:raise HTTPException(403,'附件路径不在当前Paper数据目录。') from None
            if not path.is_file():raise HTTPException(404,'附件文件缺失，请核对备份。')
            name=f"attachments/{item['id']}{path.suffix.lower()}"
            archive.write(path,name)
            text=re.sub(r'\]\(att://'+re.escape(item['id'])+r'(?:#[0-9]+x[0-9]+)?\)',lambda _:']('+name+')',text,flags=re.IGNORECASE)
            links.append(f"- [{item['filename']}]({name})")
        if links:text+='\n## 附件\n\n'+'\n'.join(links)+'\n'
        archive.writestr('record.md',text.encode('utf-8'))
    return Response(output.getvalue(),media_type='application/zip',headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="paper-{record_id}.zip"'})

@router.get('/projects/{project_id}/export.zip',dependencies=[Depends(ready)])
def export_project_zip(project_id:str):
    project=project_row(project_id)
    rows=paper_db.list_records(project_id=project_id,full=True)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        index=[f"# {project['name']}", '', project['description'], '']
        for row in rows:
            record_id=row['id'];text=record_to_markdown(row,attachments=[]);links=[]
            for item in paper_db.list_attachments(record_id):
                try:path=ensure_within(paper_db.ATTACHMENT_ROOT,Path(item['local_path']))
                except ValueError:raise HTTPException(403,'附件路径不在当前Paper数据目录。') from None
                if not path.is_file():raise HTTPException(404,'附件文件缺失，请核对备份。')
                name=f"attachments/{item['id']}{path.suffix.lower()}"
                archive.write(path,name)
                text=re.sub(r'\]\(att://'+re.escape(item['id'])+r'(?:#[0-9]+x[0-9]+)?\)',lambda _:'](../'+name+')',text,flags=re.IGNORECASE)
                links.append(f"- [{item['filename']}](../{name})")
            if links:text+='\n## 附件\n\n'+'\n'.join(links)+'\n'
            archive.writestr(f'records/{record_id}.md',text.encode('utf-8'))
            index.append(f"- [{row['title']}](records/{record_id}.md)")
        archive.writestr('index.md','\n'.join(index).encode('utf-8'))
    return Response(output.getvalue(),media_type='application/zip',headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="paper-project-{project_id}.zip"'})

@router.get('/projects/{project_id}/export.pdf',dependencies=[Depends(ready)])
def export_project_pdf(project_id:str):
    import fitz
    project=project_row(project_id)
    rows=paper_db.list_records(project_id=project_id,full=True)
    document=fitz.open()
    # Plain-text Markdown export; no HTML execution, Qt or external renderer.
    lines=[project['name'],project['description'],'正文以 Markdown 原文显示；附件请使用 ZIP 导出。']
    for row in rows:lines.extend(record_to_markdown(row,attachments=[]).splitlines()+[''])
    font_path=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'/'simhei.ttf'
    if not font_path.is_file():raise HTTPException(503,'未找到中文字体，无法生成可检索 PDF。')
    font=fitz.Font(fontfile=str(font_path));width=fitz.paper_rect('a4').width-72
    wrapped=[]
    for line in lines:
        chunk=''
        for char in line:
            if chunk and font.text_length(chunk+char,fontsize=9)>width:
                wrapped.append(chunk);chunk=''
            chunk+=char
        wrapped.append(chunk)
    for offset in range(0,len(wrapped),38):
        page=document.new_page()
        page.insert_font(fontname='paperfont',fontfile=str(font_path))
        for index,line in enumerate(wrapped[offset:offset+38]):
            page.insert_text((36,40+index*20),line,fontname='paperfont',fontsize=9)
    data=document.tobytes();document.close()
    return Response(data,media_type='application/pdf',headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="paper-project-{project_id}.pdf"'})

_secret=secrets.token_bytes(32)
_recognition_lock=threading.Lock()
_recognition_cache:dict[str,dict]={}
def active_model():
    row=workbench_db.fetch_one('SELECT id,name,base_url,model,kind FROM model_profiles WHERE enabled=1')
    if not row:raise HTTPException(409,'请先在Workbench设置中启用模型。')
    return recognition.ModelProfile(**row)
def sign(text,profile,expires_at,nonce):
    raw=json.dumps([text,profile.id,profile.base_url,profile.model,profile.kind,expires_at,nonce],ensure_ascii=False).encode()
    return hmac.new(_secret,raw,hashlib.sha256).hexdigest()

@router.post('/recognition/preview',dependencies=[Depends(ready)])
def recognition_preview(payload:RecognitionText):
    profile=active_model();expires=int(time.time())+600;nonce=secrets.token_hex(16)
    return {'text':payload.text,'service':profile.base_url,'model':profile.model,'model_name':profile.name,
            'sensitive':recognition.sensitive_summary(payload.text),'expires_at':expires,'nonce':nonce,'token':sign(payload.text,profile,expires,nonce)}

@router.post('/recognition/run',dependencies=[Depends(ready)])
def recognition_run(payload:RecognitionSend):
    if not payload.confirmed:raise HTTPException(400,'必须先确认完整发送预览。')
    profile=active_model();now=int(time.time())
    if payload.expires_at<now or payload.expires_at>now+600 or not hmac.compare_digest(payload.token,sign(payload.text,profile,payload.expires_at,payload.nonce)):
        raise HTTPException(409,'原文或模型配置已变化/预览过期，请重新预览。')
    with _recognition_lock:
        for key in list(_recognition_cache):
            if _recognition_cache[key]['expires']<now:del _recognition_cache[key]
        cached=_recognition_cache.get(payload.token)
        if cached:
            if 'result' in cached:return cached['result']
            raise HTTPException(409,'此预览已提交，请等待结果或重新预览。')
        if len(_recognition_cache)>=64:raise HTTPException(429,'识别任务较多，请稍后重试。')
        _recognition_cache[payload.token]={'expires':payload.expires_at}
    try:
        result=recognition.recognize(payload.text,profile,confirmed=True)
    except recognition.RecognitionError as ex:raise HTTPException(422,str(ex)) from None
    value={'fields':result.fields,'unassigned':result.unassigned,'evidence':result.evidence}
    with _recognition_lock:_recognition_cache[payload.token]['result']=value
    return value
