import type {WorkspaceDocument} from "../lib/workspace-state";

export default function DocumentStatus({mode,projectId,documents,busy,message,onUpload,onRetry}:{mode:string;projectId:string;documents:WorkspaceDocument[];busy:boolean;message?:string;onUpload:()=>void;onRetry:()=>void}) {
  if(mode==="material")return <section className="document-empty-state"><div><strong>研究材料已添加</strong><span>可从“材料”菜单阅读；需要在正文中编辑和标注引用时，先选择“设为正文”。</span>{documents.map(document=><a key={document.id} href={`/api/projects/${projectId}/documents/${document.id}/original`} target="_blank" rel="noreferrer">{document.filename}</a>)}</div><button className="button secondary" disabled={busy} onClick={onUpload}>添加研究材料</button></section>;
  const pending=mode==="document_pending";
  if(mode==="research")return <section className="document-empty-state"><div><strong>从研究问题开始</strong><span>可先讨论选题，也可添加材料。</span></div><button className="button secondary" onClick={onUpload}>添加研究材料</button></section>;
  return <section aria-label="文档状态" className="document-empty-state" style={{display:"grid",gap:14,marginBottom:24}}>
    <div><strong>{mode==="empty"?"尚未成功上传文件":pending?"文档正在解析":mode==="document_unstarted"?"原稿已保存，尚未开始解析":mode==="document_failed"?"文档解析未完成":"未识别到可编辑正文"}</strong>
      <span>{message || (mode==="empty"?"请选择 PDF、DOCX 或数据文件。":pending?"原稿已保存。可查看原文件，或稍后刷新解析状态。":"原稿仍保留。扫描 PDF 需要 OCR；仅含图片的 Word 暂不能提取正文。")}</span></div>
    {documents.map(document=><a className="button secondary" key={document.id} target="_blank" rel="noreferrer" href={`/api/projects/${projectId}/documents/${document.id}/original`}>{document.filename.toLowerCase().endsWith(".pdf")?"打开原 PDF":"下载原文件"} · {document.filename}</a>)}
    <div style={{display:"flex",gap:8,flexWrap:"wrap"}}><button className="button secondary" onClick={onUpload}>重新选择文件</button>{documents.length>0&&<button className="button primary" onClick={onRetry} disabled={busy}>{busy?"处理中…":pending?"刷新解析状态":"重新解析"}</button>}</div>
  </section>;
}
