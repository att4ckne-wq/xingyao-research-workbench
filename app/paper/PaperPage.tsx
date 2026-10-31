"use client";

import * as Dialog from "@radix-ui/react-dialog";
import Image from "next/image";
import {useCallback,useEffect,useRef,useState} from "react";
import {createPortal} from "react-dom";
import styles from "./paper.module.css";

const fields=[{key:"purpose",label:"目的"},{key:"conditions",label:"条件参数"},{key:"steps",label:"步骤"},{key:"result",label:"结果"},{key:"conclusion",label:"结论"},{key:"content",label:"补充记录"}] as const;
type TextKey=typeof fields[number]["key"];
type Project={id:string;name:string;description:string;status:string;_version:string};
type EventType={id:string;name:string;color:string;builtin:number};
type Attachment={id:string;filename:string;media_type:string;size_bytes:number;sha256:string};
type Summary={_version:string;id:string;project_id:string;event_type_id:string;title:string;occurred_at:string;project_name:string;event_type_name:string;tags:string[]};
type RecordDetail=Summary&Record<TextKey,string>&{_version:string;started_at:string|null;ended_at:string|null;attachments:Attachment[]};
type Editor=Record<TextKey,string>&{id:string|null;_version:string;project_id:string;event_type_id:string;title:string;occurred_at:string;started_at:string;ended_at:string;tags:string};
type Status={initialized:boolean;data_root:string;project_statuses:string[]};
type Stats={overall:{projects:number;records:number;attachments:number};monthly:Array<{label:string;total:number}>;event_types:Array<{label:string;total:number}>};
type Revision={id:string;reason:string;created_at:string;snapshot:{title:string}};
type Preview={text:string;service:string;model:string;model_name:string;sensitive:string[];nonce:string;expires_at:number;token:string};
type RecognitionResult={fields:Record<TextKey,string>;unassigned:string;evidence:Array<[string,number,number]>};
type Fill={before:Partial<Record<TextKey,string>>;after:Partial<Record<TextKey,string>>};
type View="records"|"timeline"|"stats"|"trash"|"settings";

async function api<T>(path:string,init:RequestInit={}):Promise<T>{
  const response=await fetch(`/api/paper${path}`,{...init,credentials:"include",cache:"no-store"});
  const data=await response.json().catch(()=>null);
  if(!response.ok){
    if(response.status===401)throw new Error("会话已过期，请重新登录工作站。");
    const detail=data?.detail;
    throw new Error(typeof detail==="string"?detail:Array.isArray(detail)?detail.map((x:{msg?:string})=>x.msg||"输入无效").join("；"):"操作未完成，请稍后重试。");
  }
  return data as T;
}
const json=(method:string,body:unknown):RequestInit=>({method,headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
const errorText=(error:unknown)=>error instanceof Error?error.message:"操作未完成。";
function localMinute(){const d=new Date();const pad=(n:number)=>String(n).padStart(2,"0");return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;}
function toEditor(row:RecordDetail):Editor{return {id:row.id,_version:row._version,project_id:row.project_id,event_type_id:row.event_type_id,title:row.title,occurred_at:row.occurred_at,started_at:row.started_at||"",ended_at:row.ended_at||"",tags:row.tags.join("、"),...Object.fromEntries(fields.map(f=>[f.key,row[f.key]||""]))} as Editor;}

export default function PaperPage({onDirtyChange,active=true}:{onDirtyChange?:(dirty:boolean)=>void;active?:boolean}){
  const [toolbarHost,setToolbarHost]=useState<HTMLElement|null>(null);
  useEffect(()=>{let mounted=true;Promise.resolve().then(()=>{if(mounted)setToolbarHost(document.getElementById("paper-top-actions"));});return()=>{mounted=false;};},[active]);
  const [status,setStatus]=useState<Status|null>(null);
  const [projects,setProjects]=useState<Project[]>([]);const [types,setTypes]=useState<EventType[]>([]);
  const [selected,setSelected]=useState<Record<string,string>>({});const [dragId,setDragId]=useState("");const [eventName,setEventName]=useState("");const [eventColor,setEventColor]=useState("#8a75bd");
  const [trashedProjects,setTrashedProjects]=useState<Project[]>([]);
  const [records,setRecords]=useState<Summary[]>([]);const [stats,setStats]=useState<Stats|null>(null);
  const [projectId,setProjectId]=useState("");const [typeId,setTypeId]=useState("");
  const [query,setQuery]=useState("");const [search,setSearch]=useState("");const [view,setView]=useState<View>("records");
  const [editor,setEditor]=useState<Editor|null>(null);const [saved,setSaved]=useState("");const [attachments,setAttachments]=useState<Attachment[]>([]);
  const [busy,setBusy]=useState(false);const [loading,setLoading]=useState(false);const [error,setError]=useState("");const [notice,setNotice]=useState("");
  const [refresh,setRefresh]=useState(0);const [theme,setTheme]=useState<"light"|"dark">("light");
  const [projectDraft,setProjectDraft]=useState<(Partial<Project>&{name:string;description:string;status:string})|null>(null);
  const [revisions,setRevisions]=useState<Revision[]|null>(null);
  const [recognitionOpen,setRecognitionOpen]=useState(false);const [recognitionText,setRecognitionText]=useState("");
  const [preview,setPreview]=useState<Preview|null>(null);const [result,setResult]=useState<RecognitionResult|null>(null);
  const [recognitionBusy,setRecognitionBusy]=useState(false);const [recognitionError,setRecognitionError]=useState("");
  const [selectedFields,setSelectedFields]=useState<Partial<Record<TextKey,boolean>>>({});
  const [strategies,setStrategies]=useState<Partial<Record<TextKey,string>>>({});
  const [undo,setUndo]=useState<Fill[]>([]);const [redo,setRedo]=useState<Fill[]>([]);
  const lifecycle=useRef({mounted:true,generation:0,request:null as AbortController|null});
  const uploadInput=useRef<HTMLInputElement>(null);
  const dirty=editor!==null&&JSON.stringify(editor)!==saved;
  const selectedProject=projects.find(p=>p.id===projectId);

  const refreshMetadata=useCallback(async()=>{
    const current=await api<Status>("/status");setStatus(current);
    if(current.initialized){const [p,t]=await Promise.all([api<Project[]>("/projects"),api<EventType[]>("/event-types")]);setProjects(p);setTypes(t);}
  },[]);
  useEffect(()=>{
    const state=lifecycle.current;state.mounted=true;const controller=new AbortController();
    api<Status>("/status",{signal:controller.signal}).then(async current=>{
      if(!state.mounted||controller.signal.aborted)return;setStatus(current);
      if(current.initialized){const [p,t]=await Promise.all([api<Project[]>("/projects",{signal:controller.signal}),api<EventType[]>("/event-types",{signal:controller.signal})]);if(state.mounted&&!controller.signal.aborted){setProjects(p);setTypes(t);}}
    }).catch(e=>{if(state.mounted&&!controller.signal.aborted)setError(errorText(e));});
    return()=>{state.mounted=false;state.generation++;controller.abort();state.request?.abort();};
  },[]);
  useEffect(()=>{onDirtyChange?.(dirty);},[dirty,onDirtyChange]);
  useEffect(()=>{const handler=(event:BeforeUnloadEvent)=>{if(dirty){event.preventDefault();event.returnValue="";}};window.addEventListener("beforeunload",handler);return()=>window.removeEventListener("beforeunload",handler);},[dirty]);
  useEffect(()=>{
    if(!status?.initialized)return;
    const controller=new AbortController();
    const parameters=new URLSearchParams();if(projectId)parameters.set("project_id",projectId);if(typeId)parameters.set("event_type_id",typeId);if(search)parameters.set("q",search);
    const task=Promise.resolve().then(()=>{if(!controller.signal.aborted)setLoading(true);return api<Summary[]>(view==="trash"?"/trash":`/records?${parameters}`,{signal:controller.signal});});
    task.then(rows=>{if(!controller.signal.aborted)setRecords(rows);}).catch(e=>{if(!controller.signal.aborted)setError(errorText(e));}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    api<Stats>(`/stats${projectId?`?project_id=${projectId}`:""}`,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setStats(value);}).catch(e=>{if(!controller.signal.aborted)setError(errorText(e));});
    return()=>controller.abort();
  },[status?.initialized,projectId,typeId,search,view,refresh]);
  useEffect(()=>{if(view==="trash"&&status?.initialized)api<Project[]>("/projects-trash").then(setTrashedProjects).catch(e=>setError(errorText(e)));},[view,status?.initialized,refresh]);
  async function trashProject(){
    const target=projectDraft;
    if(busy||!target?.id||!target._version)return;
    if(!window.confirm(`回收项目“${target.name}”？未保存的项目修改将放弃，记录和附件会保留。`))return;
    if(editor?.project_id===target.id&&!allowDiscard())return;
    setBusy(true);setError("");
    try{
      await api(`/projects/${target.id}/trash`,json("POST",{expected_version:target._version}));
      setProjectDraft(null);
      if(editor?.project_id===target.id){setEditor(null);setSaved("");setAttachments([]);setUndo([]);setRedo([]);}
      setProjectId(current=>current===target.id?"":current);
      await refreshMetadata();setNotice("项目已移到回收站。");bump();
    }catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  async function restoreProject(id:string){try{await api(`/projects/${id}/restore`,{method:"POST"});await refreshMetadata();bump();}catch(e){setError(errorText(e));}}
  function bump(){setRefresh(x=>x+1);}
  function allowDiscard(){return !dirty||window.confirm("当前记录有未保存的内容，是否放弃草稿？");}
  function loadRecord(row:RecordDetail){const next=toEditor(row);setEditor(next);setSaved(JSON.stringify(next));setAttachments(row.attachments);setUndo([]);setRedo([]);setError("");setView("records");}
  async function openRecord(id:string){if(busy||!allowDiscard())return;setBusy(true);try{loadRecord(await api<RecordDetail>(`/records/${id}`));}catch(e){setError(errorText(e));}finally{setBusy(false);}}
  function newRecord(){if(!allowDiscard())return;if(!projects.length||!types.length){setError("请先新建项目。");return;}const next:Editor={id:null,_version:"",project_id:projectId||projects[0].id,event_type_id:types[0].id,title:"",occurred_at:localMinute(),started_at:"",ended_at:"",tags:"",purpose:"",conditions:"",steps:"",result:"",conclusion:"",content:""};setEditor(next);setSaved(JSON.stringify(next));setAttachments([]);setUndo([]);setRedo([]);setView("records");setError("");}
  async function saveRecord(){
    if(!editor||busy)return;if(!editor.title.trim()){setError("请填写记录标题。");return;}setBusy(true);setError("");
    const {id,_version,...values}=editor;
    const payload={...values,tags:values.tags.split(/[,，、]/).map(x=>x.trim()).filter(Boolean),started_at:values.started_at||null,ended_at:values.ended_at||null,...(id?{expected_version:_version}:{})};
    try{loadRecord(await api<RecordDetail>(id?`/records/${id}`:"/records",json(id?"PATCH":"POST",payload)));setNotice("记录已保存到原Paper数据目录。");bump();}
    catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  async function saveProject(){
    if(!projectDraft||!projectDraft.name.trim()||busy)return;setBusy(true);setError("");
    const {id,_version,...rest}=projectDraft;
    const payload={name:rest.name,description:rest.description,status:rest.status,...(id?{expected_version:_version}:{})};
    try{const project=await api<Project>(id?`/projects/${id}`:"/projects",json(id?"PATCH":"POST",payload));await refreshMetadata();setProjectId(project.id);setProjectDraft(null);setNotice("项目已保存。");bump();}
    catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  async function trashRecord(){if(!editor?.id||!window.confirm(`将“${editor.title}”移到Paper回收站？`))return;setBusy(true);try{await api(`/records/${editor.id}/trash`,json("POST",{expected_version:editor._version}));setEditor(null);setSaved("");setAttachments([]);setNotice("已移到回收站，附件仍保留。");bump();}catch(e){setError(errorText(e));}finally{setBusy(false);}}
  async function restoreRecord(id:string){setBusy(true);try{await api(`/records/${id}/restore`,{method:"POST"});setNotice("记录已恢复。");bump();}catch(e){setError(errorText(e));}finally{setBusy(false);}}
  async function uploadFiles(files:FileList|File[]|null,insert=false){
    if(!files||!editor?.id||busy){if(files)setError("请先保存记录，再添加附件。");return;}setBusy(true);setError("");
    try{for(const file of Array.from(files)){const body=new FormData();body.append("file",file);const row=await api<RecordDetail>(`/records/${editor.id}/attachments`,{method:"POST",body});setAttachments(row.attachments);if(insert){const added=row.attachments[row.attachments.length-1];if(added?.media_type.startsWith("image/"))setEditor(current=>current?{...current,content:`${current.content}\n\n![图片](att://${added.id}#560x320)`}:current);}}setNotice("附件已保存；正文中的草稿未改动。");bump();}
    catch(e){setError(errorText(e));}finally{setBusy(false);if(uploadInput.current)uploadInput.current.value="";}
  }
  async function showRevisions(){if(!editor?.id)return;try{setRevisions(await api<Revision[]>(`/records/${editor.id}/revisions`));}catch(e){setError(errorText(e));}}
  async function restoreRevision(id:string){if(!editor?.id||!window.confirm("恢复到此修订？当前已保存版本会保留在修订历史中。"))return;if(!allowDiscard())return;setBusy(true);try{loadRecord(await api<RecordDetail>(`/records/${editor.id}/revisions/${id}/restore`,json("POST",{expected_version:editor._version})));setRevisions(null);bump();}catch(e){setError(errorText(e));}finally{setBusy(false);}}
  function textEdit(key:TextKey,value:string){setEditor(e=>e?{...e,[key]:value}:e);}
  function closeRecognition(){lifecycle.current.generation++;lifecycle.current.request?.abort();setRecognitionBusy(false);setRecognitionOpen(false);}
  function changeRecognitionText(text:string){lifecycle.current.generation++;lifecycle.current.request?.abort();setRecognitionText(text);setPreview(null);setResult(null);setRecognitionError("");setRecognitionBusy(false);}
  async function previewRecognition(){setRecognitionBusy(true);setRecognitionError("");const current=++lifecycle.current.generation;try{const value=await api<Preview>("/recognition/preview",json("POST",{text:recognitionText}));if(current===lifecycle.current.generation&&lifecycle.current.mounted)setPreview(value);}catch(e){if(current===lifecycle.current.generation)setRecognitionError(errorText(e));}finally{if(current===lifecycle.current.generation)setRecognitionBusy(false);}}
  async function runRecognition(){
    if(!preview)return;const controller=new AbortController();lifecycle.current.request=controller;const current=++lifecycle.current.generation;setRecognitionBusy(true);setRecognitionError("");
    try{const value=await api<RecognitionResult>("/recognition/run",{...json("POST",{text:recognitionText,confirmed:true,nonce:preview.nonce,expires_at:preview.expires_at,token:preview.token}),signal:controller.signal});if(current!==lifecycle.current.generation||!lifecycle.current.mounted)return;setResult(value);setSelectedFields(Object.fromEntries(fields.map(f=>[f.key,!editor?.[f.key].trim()])));setStrategies({});}
    catch(e){if(current===lifecycle.current.generation&&!controller.signal.aborted)setRecognitionError(errorText(e));}finally{if(current===lifecycle.current.generation)setRecognitionBusy(false);}
  }
  function fillResult(){
    if(!result||!editor)return;const patch:Fill={before:{},after:{}};
    for(const f of fields){if(!selectedFields[f.key]||!result.fields[f.key])continue;const old=editor[f.key];const mode=strategies[f.key]||"empty";if(mode==="empty"&&old.trim())continue;const value=mode==="append"&&old.trim()?`${old}\n\n${result.fields[f.key]}`:result.fields[f.key];if(value!==old){patch.before[f.key]=old;patch.after[f.key]=value;}}
    if(!Object.keys(patch.after).length){setRecognitionError("没有可填入的区块，请核对勾选和填入方式。");return;}
    setEditor({...editor,...patch.after});setUndo(x=>[...x,patch]);setRedo([]);closeRecognition();setNotice("识别结果已填入草稿，可整次撤销；尚未保存记录。");
  }
  function historyFill(direction:"undo"|"redo"){
    if(!editor)return;const stack=direction==="undo"?undo:redo;const item=stack[stack.length-1];if(!item)return;const expected=direction==="undo"?item.after:item.before;
    if(Object.entries(expected).some(([key,value])=>editor[key as TextKey]!==value)){setError("相关区块已手工修改，未自动覆盖。请先保留或处理手工内容。");return;}
    setEditor({...editor,...(direction==="undo"?item.before:item.after)});
    if(direction==="undo"){setUndo(x=>x.slice(0,-1));setRedo(x=>[...x,item]);}else{setRedo(x=>x.slice(0,-1));setUndo(x=>[...x,item]);}
  }
  async function batchTrash(){const items=Object.entries(selected).map(([id,expected_version])=>({id,expected_version}));if(!items.length||!window.confirm(`将选中的 ${items.length} 条记录移到回收站？`))return;if(editor?.id&&selected[editor.id]&&!allowDiscard())return;setBusy(true);try{await api("/records/batch-trash",json("POST",{items}));if(editor?.id&&selected[editor.id]){setEditor(null);setSaved("");setAttachments([]);}setSelected({});setNotice("已移入回收站。");bump();}catch(e){setError(errorText(e));}finally{setBusy(false);}}
  async function reorderDrop(target:string){if(!dragId||dragId===target)return;const order=records.map(r=>r.id);const from=order.indexOf(dragId),to=order.indexOf(target);if(from<0||to<0)return;order.splice(to,0,order.splice(from,1)[0]);setDragId("");try{await api("/records/order",json("POST",{ids:order}));bump();}catch(e){setError(errorText(e));}}
  async function saveType(id?:string){if(!eventName.trim())return;setBusy(true);try{await api(id?`/event-types/${id}`:"/event-types",json(id?"PATCH":"POST",{name:eventName,color:eventColor}));setEventName("");await refreshMetadata();}catch(e){setError(errorText(e));}finally{setBusy(false);}}
  async function moveRecord(index:number,delta:number){const order=records.map(r=>r.id);const next=index+delta;if(next<0||next>=order.length)return;[order[index],order[next]]=[order[next],order[index]];setBusy(true);try{await api("/records/order",json("POST",{ids:order}));bump();}catch(e){setError(errorText(e));}finally{setBusy(false);}}

  return <section className={styles.paper} data-theme={theme} aria-label="实验记录 Paper">
    {active&&toolbarHost&&createPortal(<div className={styles.portalToolbar} data-theme={theme} role="group" aria-label="实验记录操作"><button disabled={busy||!status?.initialized} onClick={()=>setProjectDraft({name:"",description:"",status:"进行中"})}>新建项目</button><button className={styles.primary} disabled={busy||!status?.initialized} onClick={newRecord}>新建记录</button><div className={styles.secondaryActions}><button disabled={busy} onClick={()=>{void refreshMetadata().then(bump).catch(e=>setError(errorText(e)));}}>刷新</button><button onClick={()=>setTheme(t=>t==="light"?"dark":"light")}>{theme==="light"?"夜航紫":"雾紫纸页"}</button></div><details className={styles.moreActions}><summary>更多</summary><div><button disabled={busy} onClick={()=>{void refreshMetadata().then(bump).catch(e=>setError(errorText(e)));}}>刷新</button><button onClick={()=>setTheme(t=>t==="light"?"dark":"light")}>{theme==="light"?"夜航紫":"雾紫纸页"}</button></div></details></div>,toolbarHost)}
    {error&&<div className={styles.error} role="alert">{error} <button onClick={()=>setError("")}>关闭提示</button></div>}
    {notice&&<div className={styles.notice} role="status">{notice}</div>}
    {!status?<div className={styles.empty} role="status">正在读取Paper状态…</div>:!status.initialized?<div className={styles.empty}><h3>开始使用实验记录</h3><p>Paper将使用独立的数据目录，不合并工作站数据库。</p><p className={styles.hint}>{status.data_root}</p><button className={styles.primary} onClick={()=>{void api("/initialize",{method:"POST"}).then(refreshMetadata).catch(e=>setError(errorText(e)));}}>初始化Paper</button></div>:<div className={styles.layout}>
      <aside className={styles.sidebar} aria-label="Paper项目"><div className={styles.sectionTitle}><span>工作空间</span><button disabled={!selectedProject||busy} onClick={()=>selectedProject&&setProjectDraft({...selectedProject})}>编辑项目</button></div>
        <form onSubmit={event=>{event.preventDefault();setSearch(query);}}><label className={styles.srOnly} htmlFor="paper-search">搜索记录</label><input id="paper-search" placeholder="标题、正文或标签，Enter 搜索" value={query} onChange={event=>setQuery(event.target.value)}/></form>
        <button onClick={()=>{setQuery("");setSearch("");}}>清除搜索</button><label className={styles.hint} htmlFor="paper-event-filter">事件筛选</label><select id="paper-event-filter" value={typeId} onChange={e=>setTypeId(e.target.value)}><option value="">全部事件类型</option>{types.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select>
        <div className={styles.projects}><button aria-pressed={!projectId} onClick={()=>setProjectId("")}>全部项目</button>{projects.map(p=><button key={p.id} title={`${p.name}（${p.status}）\n${p.description}`} aria-pressed={projectId===p.id} onClick={()=>setProjectId(p.id)} onDoubleClick={()=>setProjectDraft({...p})}>{p.name}（{p.status}）</button>)}</div>
        {projectId&&<div className={styles.projectExports}><a href={`/api/paper/projects/${projectId}/export.zip`}>项目ZIP（含附件）</a> · <a href={`/api/paper/projects/${projectId}/export.pdf`}>项目PDF（Markdown原文）</a></div>}</aside>
      <div className={styles.main}>
        <div className={styles.tabs} role="tablist" aria-label="Paper视图">{([["records","记录"],["timeline","时间线"],["stats","统计"],["trash","回收站"],["settings","设置"]] as const).map(([key,label])=><button role="tab" aria-selected={view===key} key={key} onClick={()=>setView(key)}>{label}</button>)}</div>
        {loading&&<p className={styles.hint} role="status">正在更新列表…</p>}
        {view==="records"&&<button disabled={busy||!Object.keys(selected).length} onClick={()=>void batchTrash()}>回收选中记录 ({Object.keys(selected).length})</button>}
        {(view==="records"||view==="trash")&&<div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>发生时间</th><th>记录标题</th><th>类型</th><th>{view==="trash"?"恢复":"顺序"}</th></tr></thead><tbody>{records.map((r,i)=><tr key={r.id} draggable={view==="records"&&!busy} onDragStart={()=>setDragId(r.id)} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();void reorderDrop(r.id);}}><td><span className={styles.recordTime}>{view==="records"&&<input type="checkbox" aria-label={`选择 ${r.title}`} checked={!!selected[r.id]} onChange={e=>setSelected(old=>{const next={...old};if(e.target.checked)next[r.id]=r._version;else delete next[r.id];return next;})}/>}<time dateTime={r.occurred_at.replace(" ","T")}>{r.occurred_at}</time></span></td><td>{view==="trash"?r.title:<button className={styles.titleButton} disabled={busy} onClick={()=>void openRecord(r.id)}>{r.title}</button>}</td><td>{r.event_type_name}</td><td>{view==="trash"?<button disabled={busy} onClick={()=>void restoreRecord(r.id)}>恢复</button>:<><button aria-label={`上移 ${r.title}`} disabled={busy||i===0} onClick={()=>void moveRecord(i,-1)}>↑</button> <button aria-label={`下移 ${r.title}`} disabled={busy||i===records.length-1} onClick={()=>void moveRecord(i,1)}>↓</button></>}</td></tr>)}</tbody></table>{!records.length&&!loading&&<div className={styles.empty}>{view==="trash"?"回收站暂无记录":"暂无匹配记录。可以新建记录或调整筛选。"}</div>}</div>}
        {view==="trash"&&<div><h3>已回收项目</h3>{trashedProjects.map(p=><div key={p.id}>{p.name} <button onClick={()=>void restoreProject(p.id)}>恢复项目</button></div>)}</div>}
        {view==="timeline"&&<div className={styles.timeline}>{records.map(r=><article key={r.id}><strong>{r.occurred_at}</strong><p><button disabled={busy} onClick={()=>void openRecord(r.id)}>{r.title}</button> · {r.event_type_name}</p></article>)}{!records.length&&<p>暂无时间线记录。</p>}</div>}
        {view==="stats"&&stats&&<div className={styles.stats}><div className={styles.statCard}><h3>月份分布</h3>{stats.monthly.map(r=><div className={styles.statRow} key={r.label}><span>{r.label}</span><strong>{r.total}</strong></div>)}{!stats.monthly.length&&<p>暂无记录</p>}</div><div className={styles.statCard}><h3>事件类型</h3>{stats.event_types.map(r=><div className={styles.statRow} key={r.label}><span>{r.label}</span><strong>{r.total}</strong></div>)}</div><p className={styles.hint}>全部Paper：{stats.overall.projects}个项目 · {stats.overall.records}条记录 · {stats.overall.attachments}份附件</p></div>}
        {view==="settings"&&<div className={styles.editor}><h3>独立Paper数据</h3><p className={styles.hint}>{status.data_root}</p><p>网页与桌面端使用同一份Paper记录；Workbench文档与制图数据库保持独立。</p><h3>事件类型</h3><label>名称 <input value={eventName} onChange={e=>setEventName(e.target.value)}/></label><label>颜色 <input type="color" value={eventColor} onChange={e=>setEventColor(e.target.value)}/></label><button disabled={busy||!eventName.trim()} onClick={()=>void saveType()}>新建类型</button>{types.map(t=><div key={t.id}>{t.name} {t.builtin?"（内置）":<button disabled={busy||!eventName.trim()} onClick={()=>void saveType(t.id)}>应用名称与颜色</button>}</div>)}<h3>编辑与模型</h3><p>正文以Markdown原文编辑，已有图片引用和格式文本按原文保留。自动识别使用工作站已启用模型，发送前完整预览确认；密钥不会传入网页。</p><p>主题仅影响本次网页会话。备份设置沿用Paper原有配置。</p></div>}
        {editor&&view==="records"&&<div className={styles.editor}><div className={styles.editorHead}><strong>{editor.id?"编辑记录":"新建记录"}{dirty?" · 未保存":""}</strong><button disabled={busy} onClick={()=>{if(allowDiscard()){setEditor(null);setSaved("");setAttachments([]);}}}>关闭编辑</button></div>
          <fieldset disabled={busy}><div className={styles.grid}>
            <label className={`${styles.field} ${styles.wide}`}>记录标题<input value={editor.title} onChange={e=>setEditor({...editor,title:e.target.value})}/></label>
            <label className={styles.field}>所属项目<select value={editor.project_id} onChange={e=>setEditor({...editor,project_id:e.target.value})}>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            <label className={styles.field}>事件类型<select value={editor.event_type_id} onChange={e=>setEditor({...editor,event_type_id:e.target.value})}>{types.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
            <label className={styles.field}>发生时间<input type="datetime-local" value={editor.occurred_at.replace(" ","T")} onChange={e=>setEditor({...editor,occurred_at:e.target.value.replace("T"," ")})}/></label>
            <label className={styles.field}>标签<input placeholder="逗号或顿号分隔" value={editor.tags} onChange={e=>setEditor({...editor,tags:e.target.value})}/></label>
            <label className={styles.field}>开始时间（可选）<input type="datetime-local" value={editor.started_at.replace(" ","T")} onChange={e=>setEditor({...editor,started_at:e.target.value.replace("T"," ")})}/></label>
            <label className={styles.field}>结束时间（可选）<input type="datetime-local" value={editor.ended_at.replace(" ","T")} onChange={e=>setEditor({...editor,ended_at:e.target.value.replace("T"," ")})}/></label>
          </div><div className={styles.actions}><button onClick={()=>{setRecognitionOpen(true);setRecognitionError("");}}>自动识别…</button><button disabled={!undo.length} onClick={()=>historyFill("undo")}>撤销识别填入</button><button disabled={!redo.length} onClick={()=>historyFill("redo")}>重做识别填入</button><span className={styles.hint}>修改尚未保存时，请点“保存记录”。</span></div>
          <div className={styles.actions}><span className={styles.hint}>插入到补充记录：</span>{["α","β","γ","Δ","μ","°","±","×","≤","≥","²","₁","$公式$"] .map(symbol=><button key={symbol} onClick={()=>textEdit("content",editor.content+symbol)}>{symbol}</button>)}</div><div className={styles.grid}>{fields.map(f=><label className={`${styles.field} ${f.key==="steps"||f.key==="content"?styles.wide:""}`} key={f.key}>{f.label}<textarea className={styles.documentText} value={editor[f.key]} onChange={e=>textEdit(f.key,e.target.value)} onPaste={e=>{const images=Array.from(e.clipboardData.files).filter(file=>file.type.startsWith("image/"));if(images.length){e.preventDefault();void uploadFiles(images,true);}}} rows={f.key==="content"?5:3}/>{f.key==="content"&&<div>{Array.from(editor.content.matchAll(/!\[[^\]]*\]\(att:\/\/([a-f0-9]{32})(?:#(\d+)x(\d+))?\)/gi)).map((match,i)=>{const image=attachments.find(a=>a.id===match[1]&&["image/png","image/jpeg"].includes(a.media_type));return image?<div key={i}><Image unoptimized src={`/api/paper/attachments/${image.id}`} alt={image.filename} width={Math.min(1200,Number(match[2])||560)} height={Math.min(1200,Number(match[3])||320)} style={{maxWidth:"100%",height:"auto"}}/><button onClick={()=>{const width=window.prompt("图片宽度（1–1200）",match[2]||"560");const height=window.prompt("图片高度（1–1200）",match[3]||"320");if(width&&height&&/^\d+$/.test(width)&&/^\d+$/.test(height)&&+width>0&&+width<=1200&&+height>0&&+height<=1200)textEdit("content",editor.content.replace(match[0],match[0].replace(/(?:#\d+x\d+)?\)$/,`#${width}x${height})`)));}}>设置尺寸</button></div>:null;})}</div>}</label>)}</div>
          <div className={styles.actions}><button disabled={!editor.id} onClick={()=>uploadInput.current?.click()}>添加附件</button><input hidden className={styles.srOnly} ref={uploadInput} type="file" multiple tabIndex={-1} onChange={e=>void uploadFiles(e.target.files)}/><button disabled={!editor.id} onClick={()=>void showRevisions()}>修订历史</button>{editor.id&&<a className={styles.hint} href={`/api/paper/records/${editor.id}/export`}>下载Markdown正文</a>}{editor.id&&<a className={styles.hint} href={`/api/paper/records/${editor.id}/export.zip`}>下载含附件的ZIP</a>}<button onClick={()=>{setEditor({...editor,id:null,_version:"",title:`${editor.title}（副本）`});setAttachments([]);setSaved("");}}>另存副本</button></div>
          <div className={styles.attachments} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();void uploadFiles(e.dataTransfer.files);}}><span className={styles.hint}>{editor.id?"可拖入附件，单个不超过50 MB。添加附件会立即保存到此记录。":"请先保存记录，再添加附件。"}</span>{attachments.map(a=><div key={a.id}><div className={styles.attachmentRow}><a href={`/api/paper/attachments/${a.id}`} target="_blank" rel="noreferrer">{a.filename}</a><span className={styles.hint}>{(a.size_bytes/1024).toFixed(1)} KB</span><details className={styles.attachmentDetails}><summary>详情</summary><code>SHA256 {a.sha256}</code></details>{a.media_type.startsWith("image/")&&<button onClick={()=>textEdit("content",`${editor.content}\n\n![${a.filename.replace(/[\[\]\\]/g,"_")}](att://${a.id})`)}>插入正文引用</button>}</div>{a.media_type.startsWith("image/")&&a.media_type!=="image/tiff"&&<Image className={styles.attachmentImage} unoptimized src={`/api/paper/attachments/${a.id}`} alt={a.filename} width={560} height={320}/>}</div>)}</div>
          </fieldset><div className={styles.footer}><button disabled={busy||!editor.id} onClick={()=>void trashRecord()}>移到回收站</button><button className={styles.primary} disabled={busy||!editor.title.trim()} onClick={()=>void saveRecord()}>{busy?"正在处理…":"保存记录"}</button></div>
        </div>}
      </div></div>}
    <Dialog.Root open={active&&projectDraft!==null} onOpenChange={open=>{if(!open&&!busy)setProjectDraft(null);}}><Dialog.Portal><Dialog.Overlay className={styles.overlay}/><Dialog.Content className={styles.dialog} data-theme={theme}><Dialog.Title>{projectDraft?.id?"编辑项目":"新建项目"}</Dialog.Title><Dialog.Description className={styles.hint}>修改项目名称、说明或状态。</Dialog.Description>{projectDraft&&<fieldset disabled={busy}><label className={styles.field}>项目名称<input value={projectDraft.name} onChange={e=>setProjectDraft({...projectDraft,name:e.target.value})}/></label><label className={styles.field}>项目说明<textarea value={projectDraft.description} onChange={e=>setProjectDraft({...projectDraft,description:e.target.value})}/></label><label className={styles.field}>状态<select value={projectDraft.status} onChange={e=>setProjectDraft({...projectDraft,status:e.target.value})}>{Array.from(new Set([...(status?.project_statuses||["进行中","已暂停","已结题"]),projectDraft.status])).map(s=><option key={s}>{s}</option>)}</select></label></fieldset>}{error&&<p className={styles.error} role="alert">{error}</p>}<div className={`${styles.actions} ${styles.projectDialogActions}`}>{projectDraft?.id&&<button className={styles.danger} disabled={busy} onClick={()=>void trashProject()}>回收项目</button>}<button disabled={busy} onClick={()=>setProjectDraft(null)}>取消</button><button className={styles.primary} disabled={busy||!projectDraft?.name.trim()} onClick={()=>void saveProject()}>保存项目</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={active&&revisions!==null} onOpenChange={open=>{if(!open)setRevisions(null);}}><Dialog.Portal><Dialog.Overlay className={styles.overlay}/><Dialog.Content className={styles.dialog} data-theme={theme}><Dialog.Title>修订历史</Dialog.Title><Dialog.Description className={styles.hint}>恢复前保留当前已保存版本；恢复同样检查保存冲突。</Dialog.Description>{revisions?.map(r=><div className={styles.statRow} key={r.id}><div><strong>{r.snapshot.title}</strong><p className={styles.hint}>{r.created_at} · {r.reason}</p></div><button disabled={busy} onClick={()=>void restoreRevision(r.id)}>恢复</button></div>)}{revisions?.length===0&&<p>暂无修订。</p>}<button onClick={()=>setRevisions(null)}>关闭</button></Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={active&&recognitionOpen} onOpenChange={open=>{if(!open)closeRecognition();}}><Dialog.Portal><Dialog.Overlay className={styles.overlay}/><Dialog.Content className={styles.dialog} data-theme={theme}><Dialog.Title>自动识别</Dialog.Title><Dialog.Description className={styles.hint}>粘贴混合文字，核对发送内容后由模型按原文归类。填入仍是草稿。</Dialog.Description><label className={styles.field}>待识别文字<textarea rows={6} value={recognitionText} disabled={recognitionBusy} onChange={e=>changeRecognitionText(e.target.value)}/></label>{recognitionError&&<p className={styles.error} role="alert">{recognitionError}</p>}
      {!result&&<div className={styles.actions}><button disabled={recognitionBusy||!recognitionText.trim()} onClick={()=>void previewRecognition()}>预览发送内容</button></div>}
      {preview&&!result&&<><p><strong>服务：</strong>{preview.service}<br/><strong>模型：</strong>{preview.model_name} / {preview.model}</p><p className={styles.hint}>敏感类型：{preview.sensitive.join("、")||"未检出（请自行核对全文）"}</p><pre className={styles.preview}>{preview.text}</pre><button className={styles.primary} disabled={recognitionBusy} onClick={()=>void runRecognition()}>{recognitionBusy?"正在识别…":"确认发送并识别"}</button><p className={styles.hint}>可能产生服务商费用。取消后迟到结果不会填入，已发送的请求可能仍会计费。</p></>}
      {result&&<div className={styles.recognitionRows}>{fields.map(f=><div className={styles.recognitionRow} key={f.key}><label><input type="checkbox" checked={!!selectedFields[f.key]} onChange={e=>setSelectedFields({...selectedFields,[f.key]:e.target.checked})}/>{f.label}<select aria-label={`${f.label}填入方式`} value={strategies[f.key]||"empty"} onChange={e=>setStrategies({...strategies,[f.key]:e.target.value})}><option value="empty">仅填空</option><option value="append">追加</option><option value="replace">替换</option></select></label><pre className={styles.preview}>{result.fields[f.key]||"未识别到原文"}</pre></div>)}<div><strong>未归类原文</strong><pre className={styles.preview}>{result.unassigned||"无"}</pre></div><div><strong>原文依据位置</strong><ul>{result.evidence.map(([field,start,end],i)=><li key={i}>{fields.find(f=>f.key===field)?.label||field}：字符 {start+1}–{end}</li>)}</ul></div><button className={styles.primary} onClick={fillResult}>填入所选区块</button></div>}
      <div className={styles.actions}><button onClick={closeRecognition}>取消 / 关闭</button></div>
    </Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}
