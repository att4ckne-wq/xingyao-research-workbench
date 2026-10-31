import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const text=readFileSync(new URL('../app/lib/citation-sync.ts',import.meta.url),'utf8');
const js=ts.transpileModule(text,{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {publishParagraphCitation}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const view=(mapping)=>({document_id:'document',version:'saved',bibliography:[],paragraph_citations:mapping});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
const base={projectId:'project',anchorId:'paragraph',referenceId:'reference',selected:true,isCurrent:()=>true};

test('saved POST projection is published immediately without GET, reload or export',async()=>{
 const reply=deferred();const calls=[];let displayed=view({});
 const saving=publishParagraphCitation({...base,request:(...args)=>{calls.push(args);return reply.promise},onProjection:next=>{displayed=next}});
 assert.deepEqual(displayed.paragraph_citations,{});
 const saved=view({paragraph:[1]});reply.resolve(saved);await saving;
 assert.strictEqual(displayed,saved);
 assert.deepEqual(calls,[['/api/projects/project/citation-placements',{anchor_id:'paragraph',reference_id:'reference',action:'accept'}]]);
});

test('cancel immediately publishes authoritative removal and renumbering in all paragraphs',async()=>{
 let displayed=view({paragraph:[1],later:[2]});const saved=view({paragraph:[],later:[1]});
 await publishParagraphCitation({...base,selected:false,request:async(url,body)=>{assert.equal(body.action,'reject');return saved},onProjection:next=>{displayed=next}});
 assert.deepEqual(displayed.paragraph_citations,{paragraph:[],later:[1]});
});

test('failed save leaves displayed numbering intact and reports failure',async()=>{
 const displayed=view({paragraph:[1]});let calls=0;
 await assert.rejects(publishParagraphCitation({...base,request:async()=>{throw new Error('save failed')},onProjection:()=>calls++}),/save failed/);
 assert.equal(calls,0);assert.deepEqual(displayed.paragraph_citations,{paragraph:[1]});
});

test('changing projects while save is pending does not publish into the new workspace',async()=>{
 const reply=deferred();let current=true,published=0;
 const saving=publishParagraphCitation({...base,isCurrent:()=>current,request:()=>reply.promise,onProjection:()=>published++});
 current=false;reply.resolve(view({paragraph:[1]}));await saving;assert.equal(published,0);
});

test('a late earlier save cannot replace the newer citation projection',async()=>{
 let generation=1;const first=deferred();let displayed;
 const old=publishParagraphCitation({...base,isCurrent:()=>generation===1,request:()=>first.promise,onProjection:next=>{displayed=next}});
 generation=2;const latest=view({paragraph:[1,2]});
 await publishParagraphCitation({...base,isCurrent:()=>generation===2,request:async()=>latest,onProjection:next=>{displayed=next}});
 first.resolve(view({paragraph:[1]}));await old;assert.strictEqual(displayed,latest);
});
