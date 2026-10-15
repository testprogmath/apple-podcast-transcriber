"use strict";
const assert = require("assert");
const {setup,loadApp,speechStub}=require("./dom.js");
const ID="a".repeat(32);
const caps={lexical_inspector:"contextual",speech_language:"nl-NL",pinyin:false,hanly:false,mosaic:false,internal_save:true};
function makeSentence(id,text) {
  const matches=[...text.matchAll(/[\p{L}\p{M}]+(?:['’ʼ-][\p{L}\p{M}]+)*/gu)];
  const tokens=[];let at=0;
  for(const m of matches) {
    if(m.index>at) tokens.push({t:text.slice(at,m.index),w:false,start:Array.from(text.slice(0,at)).length,end:Array.from(text.slice(0,m.index)).length});
    tokens.push({t:m[0],w:true,start:Array.from(text.slice(0,m.index)).length,end:Array.from(text.slice(0,m.index+m[0].length)).length});
    at=m.index+m[0].length;
  }
  if(at<text.length) tokens.push({t:text.slice(at),w:false,start:Array.from(text.slice(0,at)).length,end:Array.from(text).length});
  return {id,text,tokens,source_start:9.4,source_end:16.04};
}
const doc={id:ID,title:"Dutch",language:"nl",source_type:"pasted_text",capabilities:caps,glossary:{},
  hanly_available:false,mosaic_available:false,paragraphs:[[0,1]],sentences:[
    makeSentence(0,"Met de bevrijding van Nederland komt er een einde aan vijf jaar overheersing door de nazi's."),
    makeSentence(1,"🙂 Het land was in de greep van oorlog; naïef en cafés.")],saved_vocabulary:[]};
const ok=(data)=>({ok:true,json:async()=>data});
const error=(message="Contextual lookup failed")=>({ok:false,json:async()=>({error:message})});
function start({speech,respond,document:documentData=doc}={}) {
  const e=setup({search:`?doc=${ID}`});if(speech) Object.assign(window,speech.window);
  const selection={range:null};
  window.getSelection=()=>({get rangeCount(){return selection.range?1:0;},get isCollapsed(){return !selection.range;},getRangeAt:()=>selection.range,removeAllRanges:()=>{selection.range=null;}});
  const calls=[];
  global.fetch=async(path,init={})=>{
    const body=init.body?JSON.parse(init.body):null;calls.push({path,method:init.method||"GET",body});
    const custom=respond&&await respond(path,body);if(custom)return custom;
    if(path.endsWith("/lexical"))return ok({meaning:body.target_language==="en"?"liberation":"освобождение",context_note:"Здесь: освобождение страны.",saved:false});
    if(path.endsWith("/saved-vocabulary"))return ok({saved:true,item:{id:1,normalized_text:body.text.normalize("NFC").toLowerCase(),sentence_id:body.sentence_id,source_sentence:documentData.sentences.find(s=>s.id===body.sentence_id).text}});
    if(path.endsWith("/translation"))return ok({sentence_id:Number(path.split("/")[5]),translation:"С освобождением Нидерландов заканчивается оккупация.",source:"generated"});
    return ok(structuredClone(documentData));
  };
  loadApp();return {...e,calls,selection};
}
async function settle(){for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));}
const holder=(e,id)=>e.body.querySelector(`[data-sentence="${id}"]`);
const word=(e,text,id=0)=>holder(e,id).querySelectorAll(".word").find(n=>n.dataset.glyph===text);
const speakButton=(e,id=0)=>holder(e,id).querySelector(".audio-action");
const range=(a,from,b,to)=>({startContainer:a,startOffset:from,endContainer:b,endOffset:to,collapsed:false});
function choose(e,r){e.selection.range=r;document.fire("selectionchange",{});}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);

test("Dutch source-neutral prose has words, exact Unicode spans and no Chinese controls",async()=>{
  const e=start();await settle();
  assert.equal(e.nodes["pinyin-toggle"].hidden,true);assert.equal(e.nodes.counters.hidden,true);
  assert.equal(e.nodes.attribution.hidden,true);assert.equal(e.body.querySelectorAll("rt").length,0);
  assert.equal(e.body.querySelectorAll(".mark").length,0);
  assert(word(e,"bevrijding"));assert(word(e,"naïef",1));assert(word(e,"cafés",1));assert(word(e,"nazi's"));
  assert.equal(holder(e,0).querySelector(".sentence-source").textContent,doc.sentences[0].text);
  assert.equal(e.calls.length,1,"no eager lexical calls");
});
test("word tap forms contextual span request and compact inspector; Save persists",async()=>{
  const e=start();await settle();word(e,"bevrijding").click();await settle();
  const request=e.calls.find(c=>c.path.endsWith("/lexical")).body;
  assert.deepEqual(request,{sentence_id:0,start:7,end:17,text:"bevrijding",target_language:"ru"});
  assert.equal(e.nodes.lexeme.hidden,false);assert.match(e.nodes["lexeme-senses"].textContent,/освобождение/);
  assert.match(e.nodes["lexeme-context"].textContent,/Met de bevrijding/);
  assert.equal(e.nodes["lexeme-knowledge"].hidden,true);assert.equal(e.nodes["lexeme-action"].textContent,"☆ Save");
  e.nodes["lexeme-action"].click();await settle();
  assert.equal(e.nodes["lexeme-action"].textContent,"✓ Saved");
  assert(e.calls.some(c=>c.path.endsWith("/saved-vocabulary")));
  e.nodes["lexeme-close"].click();word(e,"bevrijding").click();await settle();
  assert.equal(e.nodes["lexeme-action"].textContent,"✓ Saved");
  e.nodes["lexeme-action"].click();await settle();assert.equal(e.calls.filter(c=>c.path.endsWith("/saved-vocabulary")).length,1);
});
test("target toggle uses existing RU EN preference and contextual cache",async()=>{
  const e=start();await settle();word(e,"bevrijding").click();await settle();
  e.nodes["language-toggle"].click();await settle();assert.match(e.nodes["lexeme-senses"].textContent,/liberation/);
  e.nodes["language-toggle"].click();await settle();assert.match(e.nodes["lexeme-senses"].textContent,/освобождение/);
  assert.equal(e.calls.filter(c=>c.path.endsWith("/lexical")).length,2);
});
test("failed lookup retains inspector, word speech, Save and explicit retry",async()=>{
  let failed=true;const speech=speechStub();
  const e=start({speech,respond:(p)=>p.endsWith("/lexical")&&failed?error():null});await settle();word(e,"bevrijding").click();await settle();
  assert.equal(e.nodes.lexeme.hidden,false);assert.equal(e.nodes["lexeme-retry"].hidden,false);
  e.nodes["lexeme-pronounce"].click();assert.equal(speech.last().lang,"nl-NL");
  e.nodes["lexeme-action"].click();await settle();assert.equal(e.nodes["lexeme-action"].textContent,"✓ Saved");
  failed=false;e.nodes["lexeme-retry"].click();await settle();assert.match(e.nodes["lexeme-senses"].textContent,/освобождение/);
});
test("late previous word lookup does not replace the current inspector",async()=>{
  let resolve;const wait=new Promise(r=>resolve=r);
  const e=start({respond:(p,b)=>p.endsWith("/lexical")&&b.text==="bevrijding"?wait:null});await settle();
  word(e,"bevrijding").click();await settle();word(e,"Nederland").click();await settle();
  resolve(ok({meaning:"старое значение"}));await settle();assert.equal(e.nodes["lexeme-glyph"].textContent,"Nederland");
  assert.doesNotMatch(e.nodes["lexeme-senses"].textContent,/старое/);
});
for(const voices of [[{lang:"zh-CN"},{lang:"nl_BE"},{lang:"nl-NL"}],[],[{lang:"nl-BE"}]]){
 test(`Dutch word and sentence TTS share one channel (${JSON.stringify(voices)})`,async()=>{
  const speech=speechStub({voices});const e=start({speech});await settle();
  speakButton(e).click();assert.equal(speech.last().lang,"nl-NL");assert.equal(speech.queue.length,1);
  word(e,"bevrijding").click();await settle();e.nodes["lexeme-pronounce"].click();
  assert.equal(speech.last().text,"bevrijding");assert.equal(speech.last().lang,"nl-NL");assert.equal(speech.queue.length,1);
  assert(!speakButton(e).classes.has("speaking"));
  if(voices.some(v=>v.lang==="nl-NL"))assert.equal(speech.last().voice.lang,"nl-NL");
  if(!voices.length)assert.equal(speech.last().voice,null);
  speakButton(e).click();assert.equal(speech.queue.length,1);assert.equal(speech.last().text,doc.sentences[0].text);
  assert(!e.nodes["lexeme-pronounce"].classes.has("speaking"));
 });
}
test("missing or failing TTS does not break lookup or translation",async()=>{
  for(const speech of [undefined,speechStub({broken:true})]){
    const e=start({speech});await settle();word(e,"bevrijding").click();await settle();
    assert.equal(e.nodes.lexeme.hidden,false);
    if(speech){e.nodes["lexeme-pronounce"].click();assert.match(e.nodes.toast.textContent,/unavailable/);}
    else assert.equal(e.nodes["lexeme-pronounce"].hidden,true);
    assert.match(e.nodes["lexeme-senses"].textContent,/освобождение/);
  }
});
test("Dutch sentence translation uses existing compact cached control",async()=>{
 const e=start();await settle();const button=holder(e,0).querySelector(".translation-action");assert(button);button.click();await settle();
 assert(e.calls.some(c=>c.path.endsWith("/translation")));button.click();button.click();await settle();
 assert.equal(e.calls.filter(c=>c.path.endsWith("/translation")).length,1);
});
test("native phrase selection saves exact phrase and leaves tokenization unchanged",async()=>{
  const e=start();await settle();const first=word(e,"in",1).childNodes[0],last=word(e,"van",1).childNodes[0];
  const before=holder(e,1).querySelectorAll(".word").map(n=>n.dataset.glyph);
  choose(e,range(first,0,last,3));await settle();assert.equal(e.nodes["selection-expression"].textContent,"Save expression");
  assert.equal(e.nodes["selection-hanly"].hidden,true);assert.equal(e.nodes["selection-text"].textContent,"in de greep van");
  e.nodes["selection-expression"].click();await settle();
  const request=e.calls.find(c=>c.path.endsWith("/saved-vocabulary")).body;
  assert.equal(request.text,"in de greep van");assert.equal(request.sentence_id,1);
  assert.equal(Array.from(doc.sentences[1].text).slice(request.start,request.end).join(""),request.text);
  assert.equal(e.calls.find(c=>c.path.endsWith("/lexical")).body.text,request.text);
  assert.deepEqual(holder(e,1).querySelectorAll(".word").map(n=>n.dataset.glyph),before);
});
test("cross-sentence and UI selection is rejected without requests",async()=>{
  const e=start();await settle();choose(e,range(word(e,"bevrijding").childNodes[0],0,word(e,"greep",1).childNodes[0],5));await settle();
  assert.match(e.nodes["selection-text"].textContent,/one sentence/);assert(e.nodes["selection-expression"].disabled);
  e.nodes["selection-expression"].click();await settle();assert.equal(e.calls.length,1);
  const label=holder(e,1).querySelector(".translation-action");
  choose(e,range(word(e,"greep",1).childNodes[0],0,label,0));await settle();assert(e.nodes["selection-expression"].disabled);
});
test("expression lookup failure still saves; save failure keeps native selection",async()=>{
 const e=start({respond:(p)=>p.endsWith("/lexical")?error():p.endsWith("/saved-vocabulary")?error("Save failed"):null});await settle();
 choose(e,range(word(e,"in",1).childNodes[0],0,word(e,"van",1).childNodes[0],3));await settle();
 e.nodes["selection-expression"].click();await settle();assert.match(e.nodes.toast.textContent,/Save failed/);assert(e.selection.range);
 assert.equal(e.nodes["selection-expression"].disabled,false);
});
test("save complete sentence uses canonical identity, preferred language and retry handler",async()=>{
  let saves=0;
  const e=start({respond: p=>p.endsWith("/saved-sentences")?(++saves===1?error("Save failed"):ok({saved:true})):null});
  await settle();
  const save=holder(e,0).querySelector(".sentence-save");
  assert.equal(save.textContent, "");
  assert.equal(save.getAttribute("aria-label"), "Save sentence");
  assert.equal(save.getAttribute("aria-pressed"), "false");
  assert(save.querySelector("svg"));
  save.click();await settle();assert.equal(save.disabled,false);
  assert.equal(save.getAttribute("aria-busy"), "false");
  save.click();await settle();assert.equal(save.getAttribute("aria-label"), "Sentence saved");
  assert.equal(save.getAttribute("aria-pressed"), "true");
  assert.equal(save.getAttribute("aria-busy"), "false");
  assert.equal(save.textContent, "");
  assert.equal(save.disabled,true);
  const request=e.calls.filter(c=>c.path.endsWith("/saved-sentences")).at(-1);
  assert.deepEqual(request.body,{sentence_id:0,target_language:"ru"});
});
(async()=>{let failed=0;for(const [name,fn]of tests){try{await fn();console.log(`✓ ${name}`);}catch(e){failed++;console.error(name,e);}}console.log(`${tests.length-failed}/${tests.length} Dutch frontend tests passed`);if(failed)process.exitCode=1;})();
