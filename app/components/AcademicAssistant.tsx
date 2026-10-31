"use client";
import * as Dialog from "@radix-ui/react-dialog";
import {useCallback,useEffect,useRef,useState} from "react";
import {X, Send, Download, ShieldCheck} from "lucide-react";

type Task = "topic"|"outline"|"writing"|"review"|"reading"|"translation"|"diagram";
type Message = {id:string;role:"user"|"assistant";content:string;local_only?:boolean};
type Payload = {project_id:string;anchor_id:string|null;message:string;task:Task;selection:string;topic:string;send_redacted:boolean;use_cloud:boolean;confirmed:boolean};
type EditorState = {task:Task;input:string;selection:string;workingDraft:string;sourceSelection:string|undefined;sourceTopic:string};
export function applyReadingContext(current:EditorState, selectedText:string|undefined, initialTopic:string):EditorState {
 if(current.sourceSelection===selectedText&&current.sourceTopic===initialTopic)return current;
 const next={...current,sourceSelection:selectedText,sourceTopic:initialTopic};
 return selectedText===undefined?next:{...next,selection:selectedText,task:selectedText?"reading":"topic",input:selectedText?"请解释这段原文的含义与证据。":initialTopic};
}
const tasks:Array<{id:Task;label:string;prompt:string}> = [
 {id:"topic",label:"选题",prompt:"请根据我的研究方向提出可验证的研究问题，区分已有依据与待查证的假设。"},
 {id:"outline",label:"框架",prompt:"请整理文章的论证框架，指出每部分需要哪些证据。"},
 {id:"writing",label:"写作",prompt:"请根据我提供的材料起草正文，不编造数据或引用。"},
 {id:"review",label:"修改",prompt:"请检查选中文本的逻辑与表达，保留事实、限定条件和作者原意。"},
 {id:"reading",label:"阅读",prompt:"请解释选中原文的问题、方法与结论，并指出证据位置和局限。"},
 {id:"translation",label:"翻译",prompt:"请翻译选中文本，保留术语、数字、引文与不确定程度。"},
 {id:"diagram",label:"图示",prompt:"请根据材料给出流程图的 Mermaid 源码，并说明各节点的依据。"},
];
export function apiFile(url:string) { return url.startsWith("/api/")?url:url.startsWith("/")?`/api${url}`:url; }
export async function academicRequest(url:string, body?:unknown) {
 const response=await fetch(url,{credentials:"include",...(body===undefined?{}:{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)})});
 const result=await response.json().catch(()=>({}));
 if(!response.ok) {
  const detail=result.detail;
  const message=typeof detail==="string"?detail:detail?.message|| (Array.isArray(detail)?detail.map((item:{loc?:Array<string|number>;msg?:string})=>`${item.loc?.slice(1).join(".")||"输入"}：${item.msg||"不符合要求"}`).join("；"):"");
  throw new Error(message||`请求失败（${response.status}）`);
 }
 return result;
}
function PreviewSummary({context}:{context:unknown}) {
 const value=context as {task?:string;project?:string;user_message?:string;selection?:string;anchor?:{text?:string};destination?:{base_url?:string;model?:string};datasets?:Array<{filename?:string;rows?:number;columns?:Array<{name:string;dtype:string}>}>;charts?:Array<{title?:string;chart_type?:string}>;references?:Array<{title:string;author?:string;source_url?:string}>;history?:Array<{role:string;content:string}>};
 return <div className="academic-preview-summary"><dl><dt>目标服务</dt><dd>{value.destination?.base_url||"未配置"}</dd><dt>目标模型</dt><dd>{value.destination?.model||"未配置"}</dd><dt>项目与任务</dt><dd>{value.project} · {tasks.find(item=>item.id===value.task)?.label||value.task}</dd></dl><h3>本次问题</h3><p>{value.user_message}</p><h3>本次材料</h3><p>{value.selection||value.anchor?.text||"未附加正文材料"}</p>{!!value.datasets?.length&&<details><summary>随本次发送的数据概要（{value.datasets.length}份）</summary>{value.datasets.map((item,index)=><p key={index}>{item.filename} · {item.rows}行<br/>{item.columns?.map(column=>column.name+"（"+column.dtype+"）").join("、")}</p>)}<p>仅包含字段与行数，不含原始数据值。</p></details>}{!!value.charts?.length&&<details><summary>既有图表设置（{value.charts.length}份）</summary>{value.charts.map((item,index)=><p key={index}>{item.title} · {item.chart_type}</p>)}</details>}{!!value.references?.length&&<details><summary>已选引用来源（{value.references.length}条）</summary>{value.references.map((item,index)=><p key={index}>{item.title} · {item.author}<br/>{item.source_url}</p>)}</details>}{!!value.history?.length&&<details><summary>随本次发送的既有对话（{value.history.length}条）</summary>{value.history.map((item,index)=><p key={index}>{item.role==="user"?"你":"助手"}：{item.content}</p>)}</details>}</div>;
}
export default function AcademicAssistant({project,anchor,initialTopic,selectedText,onClose,onDirtyChange}:{project:{id:string;name:string}|null;anchor?:{id:string;text:string};initialTopic:string;selectedText?:string;onClose:()=>void;onDirtyChange?:(dirty:boolean)=>void}) {
 const [editor,setEditor]=useState<EditorState>(()=>({task:selectedText?"reading":"topic",input:selectedText?"请解释这段原文的含义与证据。":initialTopic,selection:selectedText??anchor?.text??"",workingDraft:"",sourceSelection:selectedText,sourceTopic:initialTopic}));
 const {task,input,selection,workingDraft}=editor;
 const setTask=(task:Task)=>setEditor(current=>({...current,task}));
 const setInput=(input:string)=>setEditor(current=>({...current,input}));
 const setSelection=(selection:string)=>setEditor(current=>({...current,selection}));
 const setWorkingDraft=useCallback((workingDraft:string)=>setEditor(current=>({...current,workingDraft})),[]);
 const [cloud,setCloud]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const [messages,setMessages]=useState<Message[]>([]);
 const [preview,setPreview]=useState<{context:unknown;preview_hash:string;payload:Payload}|null>(null);
 const [format,setFormat]=useState("md"),[download,setDownload]=useState(""),[diagram,setDiagram]=useState<{preview_url:string;source_url:string}|null>(null);
 // Adjust this component's material state during render only when incoming context
 // changes. The next render is stable, and the independent draft remains intact.
 const nextEditor=applyReadingContext(editor,selectedText,initialTopic);
 if(nextEditor!==editor){setEditor(nextEditor);setPreview(null)}
 const [storedDraft,setStoredDraft]=useState(""),[draftVersion,setDraftVersion]=useState(0),[draftReady,setDraftReady]=useState(false),[draftSaving,setDraftSaving]=useState(false),[draftStatus,setDraftStatus]=useState("正在读取已存工作稿…");
 const draftEditRevision=useRef(0);
 const [mermaid,setMermaid]=useState("flowchart LR\n  A[研究问题] --> B[研究方法]\n  B --> C[证据与结论]");
 const projectId=project?.id;
 const dirty=workingDraft!==storedDraft;
 useEffect(()=>{onDirtyChange?.(dirty);},[dirty,onDirtyChange]);
 useEffect(()=>{if(!dirty)return;const prevent=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=""};window.addEventListener("beforeunload",prevent);return()=>window.removeEventListener("beforeunload",prevent)},[dirty]);
 useEffect(()=>{if(!projectId)return;let active=true;const revision=draftEditRevision.current;academicRequest(`/api/projects/${projectId}/working-draft`).then(value=>{if(!active)return;setStoredDraft(value.content);setDraftVersion(value.version);if(draftEditRevision.current===revision)setWorkingDraft(value.content);setDraftReady(true);setDraftStatus(value.version?`已读取版本 ${value.version}`:"尚无已存工作稿");}).catch(e=>{if(active){setDraftReady(false);setDraftStatus("已存稿读取失败，编辑内容保留；请重试读取。");setError(e.message)}});return()=>{active=false}},[projectId,setWorkingDraft]);
 const editDraft=(text:string)=>{draftEditRevision.current+=1;setWorkingDraft(text);setDownload("");};
 const saveDraft=async()=>{if(!projectId||!draftReady||draftSaving)return;const content=workingDraft;setDraftSaving(true);setError("");try{const value=await academicRequest(`/api/projects/${projectId}/working-draft`,{content,expected_version:draftVersion});setStoredDraft(content);setDraftVersion(value.version);setDraftStatus(`已保存版本 ${value.version}；历史版本保留。`);}catch(e){setDraftStatus("保存未完成，当前编辑保留。");setError(e instanceof Error?e.message:"工作稿保存失败")}finally{setDraftSaving(false)}};
 const restoreDraft=async()=>{if(!projectId||draftSaving)return;if(dirty&&!window.confirm("当前工作稿有未保存修改。读取最新已存稿将替换当前编辑，是否继续？"))return;const revision=draftEditRevision.current;setDraftSaving(true);setError("");try{const value=await academicRequest(`/api/projects/${projectId}/working-draft`);setStoredDraft(value.content);setDraftVersion(value.version);setDraftReady(true);if(draftEditRevision.current===revision){editDraft(value.content);setDraftStatus(`已读取版本 ${value.version}`)}else setDraftStatus("已读取最新版本信息；读取期间的新编辑保留，请核对后保存。");}catch(e){setError(e instanceof Error?e.message:"已存稿读取失败")}finally{setDraftSaving(false)}};
 const closeAssistant=()=>{if(dirty&&!window.confirm("工作稿尚有未保存修改。关闭将丢弃当前未保存编辑；建议先保存。仍要关闭吗？"))return;onDirtyChange?.(false);onClose()};
 useEffect(()=>{if(!projectId)return;let active=true;academicRequest(`/api/projects/${projectId}/assistant/messages`).then(items=>{if(active&&Array.isArray(items))setMessages(current=>current.length?current:items)}).catch(()=>{if(active)setError("历史消息读取失败，可继续提问。")});return()=>{active=false}},[projectId]);
 const latest=messages.filter(m=>m.role==="assistant").at(-1)?.content||"";
 const send=async(payload:Payload,hash?:string)=>{
  setBusy(true);setError("");setPreview(null);setDownload("");setDiagram(null);
  try {const reply=await academicRequest("/api/assistant/chat",{...payload,confirmed:payload.use_cloud,preview_hash:hash});setMessages(items=>[...items,{id:crypto.randomUUID(),role:"user",content:payload.message},{...reply,local_only:reply.local_only??!payload.use_cloud}]);setInput("");const diagramSource=reply.content?.match(/```mermaid\s*\n([\s\S]*?)```/i)?.[1];if(diagramSource)setMermaid(diagramSource.trim());}
  catch(e){setError(e instanceof Error?e.message:"助手请求失败");} finally{setBusy(false);}
 };
 const prepare=async()=>{
  if(!project||!input.trim()||busy) return;
  const payload:Payload={project_id:project.id,anchor_id:anchor?.id||null,message:input.trim(),task,selection,topic:initialTopic||project.name,send_redacted:true,use_cloud:cloud,confirmed:false};
  if(!cloud){await send(payload);return;}
  setBusy(true);setError("");
  try{const result=await academicRequest("/api/assistant/preview",payload);if(!result.preview_hash||result.context===undefined)throw new Error("发送预览不完整，请重试");setPreview({...result,payload});}
  catch(e){setError(e instanceof Error?e.message:"无法读取发送预览");}finally{setBusy(false);}
 };
 const exportResult=async(kind:"draft"|"diagram")=>{
  if(!project||(kind==="draft"&&!workingDraft.trim()))return;setBusy(true);setError("");setDownload("");setDiagram(null);
  try{if(kind==="draft"){const result=await academicRequest(`/api/projects/${project.id}/drafts`,{title:project.name,content:workingDraft,format});setDownload(apiFile(result.download_url));}
  else{const result=await academicRequest(`/api/projects/${project.id}/diagrams`,{title:project.name,source:mermaid});setDiagram(result);}}
  catch(e){setError(e instanceof Error?e.message:"文件生成失败");}finally{setBusy(false);}
 };
 return <aside className="assistant-drawer academic-assistant" aria-label="学术助手">
  <div className="assistant-header"><div><span className="assistant-mode"><ShieldCheck size={12}/>{cloud?"在线模型 · 发送前逐次预览":"本地模板 · 不调用模型"}</span><h2>学术助手</h2></div><button className="icon-btn" aria-label="关闭学术助手" onClick={closeAssistant}><X size={16}/></button></div>
  <div className="assistant-context"><strong>{project?.name||"先创建项目或上传材料"}</strong><span>选题、写作与阅读中的每一步</span></div>
  <div className="academic-task-list" aria-label="选择学术任务">{tasks.map(item=><button key={item.id} className={`suggestion-chip ${task===item.id?"selected":""}`} aria-pressed={task===item.id} onClick={()=>{setTask(item.id);setInput(item.prompt)}}>{item.label}</button>)}</div>
  <div className="assistant-messages" aria-live="polite">{!messages.length&&<div className="assistant-empty"><strong>从一个具体问题开始</strong><p>输入研究方向，或粘贴需要处理的文字。</p></div>}{messages.map(m=><div key={m.id} className={`assistant-message ${m.role}`}><span>{m.role==="user"?"你":m.local_only===true?"本地模板":m.local_only===false?"在线助手":"历史助手回复"}</span><p>{m.content}</p></div>)}{busy&&<p role="status">正在处理，请稍候…</p>}</div>
  {error&&<p className="academic-error" role="alert">{error}</p>}
  <details className="academic-selection"><summary>选中文本与补充材料</summary><label htmlFor="academic-selection">处理这段文字（可编辑）</label><textarea id="academic-selection" maxLength={50000} className="input" value={selection} onChange={e=>setSelection(e.target.value)}/></details>
  <details className="academic-deliverables"><summary>工作稿编辑 / 生成图示</summary><p>工作稿不会自动修改原稿。编辑后点“保存工作稿”，历史版本会保留；导出前核对内容与引用。</p><button className="button secondary compact" disabled={!latest||busy} onClick={()=>{if(workingDraft&&workingDraft!==latest&&!window.confirm("载入回复将替换当前工作稿，是否继续？建议先保存当前版本。"))return;editDraft(latest)}}>载入最近回复（替换工作稿）</button><div className="academic-inline"><button className="button secondary compact" disabled={!projectId||!draftReady||draftSaving||!dirty} onClick={saveDraft}>{draftSaving?"正在保存/读取…":"保存工作稿"}</button><button className="button ghost compact" disabled={!projectId||draftSaving} onClick={restoreDraft}>读取最新已存稿</button><span role="status">{dirty?"有未保存修改 · ":""}{draftStatus}</span></div><label htmlFor="working-draft">工作稿编辑</label><textarea id="working-draft" className="input" value={workingDraft} onChange={e=>editDraft(e.target.value)} placeholder="输入工作稿，或载入最近一条助手回复后编辑。新回复不会覆盖这里的文字。"/><div className="academic-inline"><label>文稿格式 <select value={format} onChange={e=>setFormat(e.target.value)}><option value="md">Markdown 文本</option><option value="docx">Word 文档</option><option value="pdf">PDF 文档</option></select></label><button className="button secondary compact" disabled={busy||!workingDraft.trim()} onClick={()=>exportResult("draft")}>生成草稿文件</button></div><label htmlFor="mermaid-source">流程图源码（Mermaid）</label><p id="mermaid-syntax-help">当前本地预览支持简单的 flowchart TD、TB、LR 方向声明，以及每行一条 A[标题] --&gt; B[标题] 连接。暂不支持复杂 Mermaid 语法。</p><textarea id="mermaid-source" className="input" aria-describedby="mermaid-syntax-help" value={mermaid} onChange={e=>setMermaid(e.target.value)}/><button className="button secondary compact" disabled={busy||!mermaid.trim()} onClick={()=>exportResult("diagram")}>生成图示</button>{download&&<a className="button secondary" href={download} download><Download size={14}/>下载草稿</a>}{diagram&&<div><iframe title="生成的流程图预览" src={apiFile(diagram.preview_url)}/><a href={apiFile(diagram.preview_url)} download>下载图示</a> · <a href={apiFile(diagram.source_url)} download>下载可编辑源码</a></div>}</details>
  <div className="assistant-composer"><label className="assistant-privacy"><input type="checkbox" checked={cloud} onChange={e=>setCloud(e.target.checked)}/>使用已配置在线模型</label><textarea className="input" value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();prepare()}}} placeholder="描述研究问题、写作要求或需要解释的内容" aria-label="给学术助手的消息"/><button className="button primary" disabled={busy||!project||!input.trim()} onClick={prepare}><Send size={15}/>{cloud?"预览发送内容":"获取本地建议"}</button></div>
  <Dialog.Root open={!!preview} onOpenChange={open=>{if(!open)setPreview(null)}}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog-content academic-preview"><Dialog.Title>确认本次在线发送</Dialog.Title><Dialog.Description>核对目标服务、问题与材料；可展开查看引用、既有对话和完整发送数据。确认后才会调用在线模型。</Dialog.Description>{preview&&<PreviewSummary context={preview.context}/>}<details><summary>查看完整发送数据</summary><pre aria-label="完整发送上下文">{preview?JSON.stringify(preview.context,null,2):""}</pre></details><div className="dialog-actions"><Dialog.Close asChild><button className="button secondary">取消</button></Dialog.Close><button className="button primary" disabled={busy} onClick={()=>preview&&send(preview.payload,preview.preview_hash)}>确认发送到在线模型</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
 </aside>;
}
