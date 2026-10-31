export type CitationProjection = {document_id:string|null;version:string;bibliography:Array<{number:number;reference_key:string;reference_ids:string[];title:string;author?:string;year?:number;journal?:string;source_url?:string}>;paragraph_citations:Record<string,number[]>};

type ParagraphCitationUpdate = {
  projectId:string;
  anchorId:string;
  referenceId:string;
  selected:boolean;
  request:(url:string,body:unknown)=>Promise<CitationProjection>;
  isCurrent:()=>boolean;
  onProjection:(projection:CitationProjection)=>void;
};

export async function publishParagraphCitation(update:ParagraphCitationUpdate) {
  const projection=await update.request(`/api/projects/${update.projectId}/citation-placements`,{
    anchor_id:update.anchorId,reference_id:update.referenceId,action:update.selected?"accept":"reject",
  });
  if(update.isCurrent())update.onProjection(projection);
  return projection;
}
