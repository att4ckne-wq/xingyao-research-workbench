import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const text=readFileSync(new URL('../app/lib/workspace-state.ts',import.meta.url),'utf8');
const js=ts.transpileModule(text,{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {workspaceMode,isDemoCandidateId,baiduScholarUrl,sourceEditIssues}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
test('documents never become data-only while pending, failed or empty',()=>{
 const documents=[{id:'pdf',filename:'short.pdf',parser:'pending'}];
 assert.equal(workspaceMode({documents},0,1),'document_unstarted');
 assert.equal(workspaceMode({documents,jobs:[{kind:'document_analysis',state:'queued'}]},0,1),'document_pending');
 assert.equal(workspaceMode({documents,jobs:[{kind:'document_analysis',state:'failed'}]},0,0),'document_failed');
 assert.equal(workspaceMode({documents:[{...documents[0],parser:'pymupdf'}]},0,0),'document_empty');
 assert.equal(workspaceMode({documents:[]},0,0),'empty');
 assert.equal(workspaceMode({documents:[]},0,1),'data');
 assert.equal(workspaceMode({documents},1,0),'document');
});
test('UUID starting with c is real; demo IDs are exact',()=>{
 assert.equal(isDemoCandidateId('c123456789abcdef0123456789abcdef0'),false);
 assert.equal(isDemoCandidateId('c1'),true); assert.equal(isDemoCandidateId('chart:1'),false);
});
test('source-edit requirements explain disabled saves without promoting references to images',()=>{
 const draft={sourceUrl:'https://europepmc.org/article/MED/123',licenseUrl:'',license:'Open access',verified:false,reuseStatus:'citation_only',reason:''};
 assert.match(sourceEditIssues(draft)[0],/至少 3/);
 assert.deepEqual(sourceEditIssues({...draft,reason:'更正来源网址'}),[]);
 assert.ok(sourceEditIssues({...draft,reason:'已阅读原文章节',verified:true}).some(item=>item.includes('引用无需')));
 assert.ok(baiduScholarUrl('燃料电池 & Pt').includes('%26'));
});


test('supplementary research materials await explicit manuscript selection instead of reporting empty parsing',()=>{
 assert.equal(workspaceMode({documents:[{id:'supplement',filename:'source.md',parser:'markdown'}],manuscript_required:true},0,0),'material');
});
