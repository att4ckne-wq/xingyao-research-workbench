import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire,Module} from 'node:module';
import {fileURLToPath} from 'node:url';

const require=createRequire(import.meta.url);
const ts=require('typescript');
const filename=fileURLToPath(new URL('../app/components/AcademicAssistant.tsx',import.meta.url));
const source=await readFile(filename,'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true,target:ts.ScriptTarget.ES2022}}).outputText;
const component=new Module(filename);component.filename=filename;component.paths=Module._nodeModulePaths(fileURLToPath(new URL('../app/components/',import.meta.url)));component._compile(compiled,filename);
const {applyReadingContext}=component.exports;

test('draft uses server CAS storage, preserves pending edits and guards destructive UI transitions', async()=>{
 assert.match(source,/\/working-draft/);
 assert.match(source,/expected_version:draftVersion/);
 assert.match(source,/draftEditRevision\.current===revision/);
 assert.match(source,/beforeunload/);
 assert.match(source,/onDirtyChange\?\.\(dirty\)/);
 assert.match(source,/onClick=\{closeAssistant\}/);
 assert.match(source,/载入回复将替换当前工作稿/);
 assert.match(source,/保存未完成，当前编辑保留/);
 assert.doesNotMatch(source,/localStorage|sessionStorage/);
});

test('changing the reader selection updates material without losing an edited draft',()=>{
 const current={task:'writing',input:'继续写这段',selection:'原先已编辑材料',workingDraft:'尚未保存的作者工作稿1.25',sourceSelection:'旧原文',sourceTopic:'材料研究'};
 const next=applyReadingContext(current,'新选中的文献段落','材料研究');
 assert.equal(next.selection,'新选中的文献段落');
 assert.equal(next.task,'reading');
 assert.equal(next.workingDraft,current.workingDraft);
 assert.equal(current.selection,'原先已编辑材料');
 assert.equal(applyReadingContext(next,'新选中的文献段落','材料研究'),next);
});

test('ordinary rerenders preserve typed questions, while clearing selection starts a topic task',()=>{
 const current={task:'review',input:'作者尚未发送的问题',selection:'作者修订过的选文',workingDraft:'作者尚未保存的稿件',sourceSelection:'原文',sourceTopic:'课题A'};
 assert.equal(applyReadingContext(current,'原文','课题A'),current);
 const cleared=applyReadingContext(current,'','课题A');
 assert.equal(cleared.task,'topic');assert.equal(cleared.input,'课题A');
 assert.equal(cleared.selection,'');assert.equal(cleared.workingDraft,current.workingDraft);
 const unspecified=applyReadingContext(current,undefined,'课题A改名');
 assert.equal(unspecified.selection,current.selection);
 assert.equal(unspecified.input,current.input);
 assert.equal(unspecified.workingDraft,current.workingDraft);
});

test('reader resets to text on document change and embeds only PDF documents', async()=>{
 const source=await readFile(new URL('../app/components/ReaderPanel.tsx',import.meta.url),'utf8');
 assert.match(source,/setDocumentId\(e\.target\.value\);setMode\("text"\)/);
 assert.match(source,/mode==="pdf"&&doc\?\.filename\.toLowerCase\(\)\.endsWith\("\.pdf"\)/);
});
