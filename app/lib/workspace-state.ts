export type WorkspaceDocument = {id:string; filename:string; parser?:string};
export type WorkspaceJob = {kind:string; state:string; message?:string};
export function workspaceMode(project:{manuscript_required?:boolean;documents?:WorkspaceDocument[];status?:string;jobs?:WorkspaceJob[]}|null, anchors:number, datasets:number) {
  if (!project) return "demo";
  if (anchors) return "document";
  if(project.manuscript_required && project.documents?.length)return "material";
  if (project.documents?.length) {
    const job=project.jobs?.find(item=>item.kind==="document_analysis");
    if (job?.state==="failed" || project.status==="解析失败") return "document_failed";
    if (["queued","running","paused"].includes(job?.state || "")) return "document_pending";
    if (project.documents.some(item=>item.parser==="pending")) return "document_unstarted";
    return "document_empty";
  }
  if (datasets) return "data";
  return project.status === "选题与写作" ? "research" : "empty";
}
export const isDemoCandidateId=(id:string)=>/^c\d+$/.test(id);
export const baiduScholarUrl=(query:string)=>`https://xueshu.baidu.com/s?wd=${encodeURIComponent(query.trim())}`;
export function sourceEditIssues(draft:{sourceUrl:string;licenseUrl:string;license:string;verified:boolean;reuseStatus:string;reason:string}) {
  const issues:string[]=[];
  const httpUrl=(value:string)=>{try {const url=new URL(value);return ["http:","https:"].includes(url.protocol)&&!!url.hostname;}catch{return false;}};
  if (!httpUrl(draft.sourceUrl)) issues.push("请填写完整的来源网址（http 或 https）。");
  if (draft.licenseUrl && !httpUrl(draft.licenseUrl)) issues.push("许可原文网址格式不正确。");
  if (draft.reason.trim().length<(draft.verified?5:3)) issues.push(draft.verified?"请用至少 5 个字说明核验依据。":"请用至少 3 个字说明修改理由。");
  if (draft.verified) {
    if (!draft.license.trim() || !draft.licenseUrl) issues.push("核验图片复用需要许可名称和许可原文网址。");
    if (!["safe_open","attribution_required"].includes(draft.reuseStatus)) issues.push("当前仅作来源引用。引用无需核验图片许可，请使用卡片上的“引用来源”。");
  }
  return issues;
}
