import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire,Module} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const filename=fileURLToPath(new URL('../app/components/AcademicAssistant.tsx',import.meta.url));
const source=await readFile(filename,'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true,target:ts.ScriptTarget.ES2022}}).outputText;
const component=new Module(filename);component.filename=filename;component.paths=Module._nodeModulePaths(fileURLToPath(new URL('../app/components/',import.meta.url)));component._compile(compiled,filename);
const {default:Assistant,academicRequest,apiFile}=component.exports;

test('assistant starts locally and offers seven concrete academic tasks without network calls',()=>{
 const html=renderToStaticMarkup(React.createElement(Assistant,{project:{id:'fixture',name:'材料疲劳研究'},initialTopic:'材料疲劳研究',onClose(){}}));
 for(const name of ['选题','框架','写作','修改','阅读','翻译','图示'])assert.ok(html.includes(name));
 assert.match(html,/本地模板 · 不调用模型/);
 assert.match(html,/获取本地建议/);
 assert.doesNotMatch(html,/checked=""/);
});

test('explicit reader selection initializes material field, independently of research topic',()=>{
 const html=renderToStaticMarkup(React.createElement(Assistant,{project:{id:'fixture',name:'研究项目'},initialTopic:'研究项目',selectedText:'原文证据：样本尚不足以支持因果结论。',onClose(){}}));
 assert.match(html,/原文证据：样本尚不足以支持因果结论。/);
 assert.match(html,/请解释这段原文的含义与证据。/);
});

test('file URL normalization preserves API paths',()=>{
 assert.equal(apiFile('/projects/a/drafts/b.pdf'),'/api/projects/a/drafts/b.pdf');
 assert.equal(apiFile('/api/projects/a/drafts/b.pdf'),'/api/projects/a/drafts/b.pdf');
});

test('working draft is editable before chat and exports independently of future replies',()=>{
 const html=renderToStaticMarkup(React.createElement(Assistant,{project:{id:'fixture',name:'研究项目'},initialTopic:'研究项目',onClose(){}}));
 assert.match(html,/工作稿编辑/);
 assert.match(html,/id="working-draft"/);
 assert.match(html,/不会自动修改原稿/);
 assert.match(source,/content:workingDraft,format/);
 assert.match(source,/disabled=\{busy\|\|!workingDraft\.trim\(\)\}/);
 assert.match(source,/载入回复将替换当前工作稿/);
 assert.match(source,/editDraft\(latest\)/);
 const send=source.slice(source.indexOf('const send='),source.indexOf('const prepare='));
 assert.doesNotMatch(send,/setWorkingDraft/);
 assert.match(html,/flowchart TD、TB、LR/);
});

test('request exposes actual backend failure instead of reporting a generated result',async()=>{
 const original=globalThis.fetch;
 globalThis.fetch=async()=>({ok:false,status:422,json:async()=>({detail:'图示源码不符合要求'})});
 try{await assert.rejects(()=>academicRequest('/api/fixture',{}),/图示源码不符合要求/)}finally{globalThis.fetch=original}
});

test('research project empty state remains distinct from failed upload',async()=>{
 const text=await readFile(new URL('../app/lib/workspace-state.ts',import.meta.url),'utf8');
 const js=ts.transpileModule(text,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
 const {workspaceMode}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
 assert.equal(workspaceMode({status:'选题与写作',documents:[]},0,0),'research');
 assert.equal(workspaceMode({status:'上传失败',documents:[]},0,0),'empty');
 assert.equal(workspaceMode({status:'选题与写作',documents:[]},0,1),'data');
});

test('cloud confirmation presents complete server context and binds the same request hash',()=>{
 assert.match(source,/academicRequest\("\/api\/assistant\/preview",payload\)/);
 assert.match(source,/JSON\.stringify\(preview\.context,null,2\)/);
 assert.match(source,/send\(preview\.payload,preview\.preview_hash\)/);
 assert.match(source,/confirmed:payload\.use_cloud,preview_hash:hash/);
 assert.match(source,/\/assistant\/messages/);
 assert.match(source,/m\.local_only===true/);
});

test('reader distinguishes page previews and unsupported vectors, exposing OCR failures',async()=>{
 const reader=await readFile(new URL('../app/components/ReaderPanel.tsx',import.meta.url),'utf8');
 assert.match(reader,/preview_available!==false/);
 assert.match(reader,/下载原始图件/);
 assert.match(reader,/整页预览（含矢量内容）/);
 assert.match(reader,/result\.status==="unavailable"/);
 assert.match(reader,/!result\.text\?\.trim\(\)/);
 assert.match(reader,/data\.warnings/);
 assert.match(reader,/data\.limits/);
 assert.match(reader,/onSelect\(anchor\.text\)/);
 assert.match(reader,/title=\{`原始 PDF：/);
});

test('request exposes structured preview-expiry and model errors',async()=>{
 const original=globalThis.fetch;
 globalThis.fetch=async()=>({ok:false,status:409,json:async()=>({detail:{code:'cloud_preview_confirmation_required',message:'请重新预览当前发送内容'}})});
 try{await assert.rejects(()=>academicRequest('/api/fixture',{}),/请重新预览当前发送内容/)}finally{globalThis.fetch=original}
});
