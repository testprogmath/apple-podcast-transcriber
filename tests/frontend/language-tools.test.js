"use strict";
const assert = require("assert");
const {setup,loadApp,speechStub,markup}=require("./dom.js");
const ID="a".repeat(32);
const caps={lexical_inspector:"contextual",local_dictionary:"dutch",speech_language:"nl-NL",pinyin:false,hanly:false,mosaic:false,internal_save:true};
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
    if(path.endsWith("/lexical")&&body.mode==="dictionary")return ok({dictionary:{available:false,candidates:[]}});
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
  const request=e.calls.find(c=>c.path.endsWith("/lexical")&&c.body.mode!=="dictionary").body;
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
  assert.equal(e.calls.filter(c=>c.path.endsWith("/lexical")&&c.body.mode!=="dictionary").length,2);
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

function candidate(lemma, {article="het",plural=[],diminutive=[],senses}={}) {
  return {id:1,lemma,pos:"noun",plural,diminutive,source_url:`https://en.wiktionary.org/wiki/${lemma}`,
    senses:senses||[{articles:article?[article]:[],labels:[],glosses:["fixture meaning"]}]};
}
function localResult(surface,candidates,available=true) {
  return ok({dictionary:{surface,available,candidates,is_ambiguous:candidates.length>1||candidates.some(c=>new Set(c.senses.flatMap(s=>s.articles||[])).size>1)}});
}
test("local noun renders before blocked contextual provider and survives language changes",async()=>{
  let complete;
  const blocked=new Promise(r=>{complete=r;});
  const e=start({respond:(p,b)=>p.endsWith("/lexical")?(b.mode==="dictionary"
    ?localResult(b.text,[candidate("bevrijding",{article:"de",plural:["bevrijdingen"],diminutive:["bevrijdinkje"]})]):blocked):null});
  await settle();word(e,"bevrijding").click();await settle();
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/de bevrijding · bevrijdingen/);
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/fixture meaning/);
  assert.match(e.nodes["lexeme-senses"].textContent,/Loading/);
  assert(e.nodes["lexeme-dictionary-more"].querySelector("details"));
  e.nodes["language-toggle"].click();await settle();
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/bevrijdingen/);
  assert.equal(e.calls.filter(c=>c.body?.mode==="dictionary").length,1);
  complete(ok({meaning:"liberation"}));await settle();
  assert.match(e.nodes["lexeme-senses"].textContent,/liberation/);
});
test("inflected surface preserves multiple analyses with disclosure and sense labels",async()=>{
  const documentData={...doc,sentences:[makeSentence(0,"huizen")],paragraphs:[[0]]};
  const analyses=[candidate("huis",{plural:["huizen"]}),{...candidate("huizen"),pos:"verb"},
    {...candidate("Huizen"),pos:"name"}];
  const e=start({document:documentData,respond:(p,b)=>b?.mode==="dictionary"?localResult(b.text,analyses):null});
  await settle();word(e,"huizen").click();await settle();
  assert.equal(e.nodes["lexeme-glyph"].textContent,"huizen");
  assert.match(e.nodes["lexeme-dictionary-label"].textContent,/multiple analyses \(3\)/);
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/→ het huis · huizen/);
  assert.match(e.nodes["lexeme-dictionary-more"].textContent,/Other analyses \(1\)/);
  assert.match(e.nodes["lexeme-dictionary-more"].textContent,/Huizen/);
});
for (const [article,plural] of [[null,[]],["het",[]],["het",["musea","museums"]]]) {
  test(`missing evidence is not guessed, multiple plurals retained ${article}/${plural}`,async()=>{
    const e=start({respond:(p,b)=>b?.mode==="dictionary"?localResult(b.text,[candidate("museum",{article,plural})]):null});
    await settle();word(e,"bevrijding").click();await settle();
    const content=e.nodes["lexeme-dictionary-content"].textContent;
    assert(content.includes(article?"het museum":"→ museum"));
    if(plural.length)assert(content.includes("musea, museums"));
    else assert(!content.includes(" · "));
  });
}
test("sense-dependent articles remain attached to distinct meanings and historical senses accessible",async()=>{
  const senses=[{articles:["de"],labels:[],glosses:["person"]},
    {articles:["het"],labels:["derogatory","informal"],glosses:["woman"]},
    {articles:["de"],labels:["obsolete"],glosses:["old fixture sense"]}];
  const e=start({respond:(p,b)=>b?.mode==="dictionary"?localResult(b.text,[candidate("mens",{senses})]):null});
  await settle();word(e,"bevrijding").click();await settle();
  const content=e.nodes["lexeme-dictionary-content"].textContent;
  assert.match(e.nodes["lexeme-dictionary-label"].textContent,/sense-dependent articles/);
  assert(content.includes("de person"));assert(content.includes("het (derogatory, informal) woman"));
  assert(e.nodes["lexeme-dictionary-more"].textContent.includes("obsolete"));assert(!content.includes("de / het mens"));
});
test("contextual failure, retry and timeout preserve local information and TTS",async()=>{
  const speech=speechStub({voices:[{lang:"nl-NL"}]});let failed=true;
  const e=start({speech,respond:(p,b)=>p.endsWith("/lexical")?(b.mode==="dictionary"
    ?localResult(b.text,[candidate("bevrijding",{article:"de"})]):failed?error():ok({meaning:"recovered"})):null});
  await settle();word(e,"bevrijding").click();await settle();
  assert.equal(e.nodes["lexeme-retry"].hidden,false);
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/de bevrijding/);
  e.nodes["lexeme-pronounce"].click();assert.equal(speech.last().lang,"nl-NL");
  failed=false;e.nodes["lexeme-retry"].click();await settle();
  assert.match(e.nodes["lexeme-senses"].textContent,/recovered/);
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/de bevrijding/);
});
test("contextual deadline and empty-success response leave dictionary visible and retryable",async()=>{
  let malformed=false;
  const e=start({respond:(p,b)=>p.endsWith("/lexical")?(b.mode==="dictionary"
    ?localResult(b.text,[candidate("bevrijding")]):malformed?ok({}):new Promise(()=>{})):null});
  await settle();const timers=[];global.setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length;};
  word(e,"bevrijding").click();await settle();
  timers.find(t=>t.ms===20000).fn();await settle();
  assert.match(e.nodes["lexeme-senses"].textContent,/timed out/);
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/fixture meaning/);
  malformed=true;e.nodes["lexeme-retry"].click();await settle();
  assert.match(e.nodes["lexeme-senses"].textContent,/unavailable/);
  assert.equal(e.nodes["lexeme-retry"].hidden,false);
});
for (const available of [true,false]) test(`dictionary miss/unavailable preserves contextual channel ${available}`,async()=>{
  const e=start({respond:(p,b)=>b?.mode==="dictionary"?localResult(b.text,[],available):null});
  await settle();word(e,"bevrijding").click();await settle();
  assert.match(e.nodes["lexeme-senses"].textContent,/освобождение/);
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,available?/No local dictionary entry/:/Dictionary unavailable/);
});
test("late dictionary response cannot replace a different selected word",async()=>{
  let complete;const blocked=new Promise(r=>{complete=r;});
  const e=start({respond:(p,b)=>b?.mode==="dictionary"?(b.text==="bevrijding"?blocked:localResult(b.text,[candidate("Nederland")])):null});
  await settle();word(e,"bevrijding").click();word(e,"Nederland").click();await settle();
  complete(localResult("bevrijding",[candidate("bevrijding")]));await settle();
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/Nederland/);
  assert(!e.nodes["lexeme-dictionary-content"].textContent.includes("bevrijding"));
});

for (const [text,sentence,translation,target] of [
  ["oorlog","Dit is het verhaal over ons land in de greep van oorlog en onderdrukking.","война","ru"],
  ["vrijheid","Over mensen, zoals jij en ik, die hun vrijheid verliezen.","свобода","ru"],
  ["greep","Het land was in de greep van oorlog.","во власти","ru"],
  ["oorlog","De oorlog eindigde.","war","en"],
]) test(`one Dutch contextual result for ${text}/${target}; old notes stay hidden`,async()=>{
  const documentData={...doc,sentences:[makeSentence(0,sentence)],paragraphs:[[0]]};
  const e=start({document:documentData,respond:(p,b)=>p.endsWith("/lexical")?(b.mode==="dictionary"
    ?localResult(b.text,[candidate(text)])
    :ok({meaning:translation,context_note:"legacy explanation that must not render"})):null});
  await settle();
  if (target==="en") { e.nodes["language-toggle"].click();await settle(); }
  word(e,text).click();await settle();
  assert.equal(e.nodes["lexeme-senses"].textContent,translation);
  assert.equal(e.nodes["lexeme-source"].textContent,"");
  assert.equal(e.nodes["lexeme-note"].textContent,"");
  assert.match(e.nodes["lexeme-context"].textContent,new RegExp(text));
  assert.equal(e.calls.find(c=>c.body&&c.body.mode!=="dictionary"&&c.path.endsWith("/lexical")).body.target_language,target);
});
test("dictionary sources and all entry links remain in a secondary native disclosure after context",async()=>{
  const e=start({respond:(p,b)=>b?.mode==="dictionary"?localResult(b.text,[
    candidate("mens",{article:"de",diminutive:["mensje"]}),candidate("mens",{article:"het"})]):null});
  await settle();word(e,"bevrijding").click();await settle();
  assert.equal(e.nodes["lexeme-dictionary-sources"].hidden,false);
  assert.equal(e.nodes["lexeme-dictionary-more"].hidden,false);
  assert(!e.nodes["lexeme-dictionary-content"].querySelector("a"));
  const links=e.nodes["lexeme-dictionary-links"].querySelectorAll("a");
  assert.equal(links.length,2);
  for (const link of links) {
    assert.equal(link.href,"https://en.wiktionary.org/wiki/mens");
    assert.equal(link.rel,"noopener noreferrer");
  }
  const sourceMarkup=markup();
  const disclosure=sourceMarkup.slice(sourceMarkup.indexOf('<details id="lexeme-dictionary-sources"'),sourceMarkup.indexOf('<button id="lexeme-retry"'));
  for (const credit of ["Dictionary source: Wiktionary","Wiktionary contributors","Kaikki/Wiktextract","CC BY-SA 4.0","raw snapshot 2026-10-03","compact projection","https://creativecommons.org/licenses/by-sa/4.0/"]) assert(disclosure.includes(credit));
  assert(sourceMarkup.indexOf('id="lexeme-context"')<sourceMarkup.indexOf('id="lexeme-dictionary-more"'));
  assert(sourceMarkup.indexOf('id="lexeme-dictionary-more"')<sourceMarkup.indexOf('id="lexeme-dictionary-sources"'));
  assert(!disclosure.includes("open="),"native closed disclosure remains keyboard/touch accessible");
});
test("unavailable contextual provider keeps dictionary and source disclosure visible",async()=>{
  const e=start({respond:(p,b)=>p.endsWith("/lexical")?(b.mode==="dictionary"
    ?localResult(b.text,[candidate("bevrijding",{article:"de"})])
    :error("Contextual meaning is unavailable.")):null});
  await settle();word(e,"bevrijding").click();await settle();
  assert.match(e.nodes["lexeme-senses"].textContent,/unavailable/);
  assert.match(e.nodes["lexeme-dictionary-content"].textContent,/de bevrijding/);
  assert.equal(e.nodes["lexeme-dictionary-sources"].hidden,false);
  assert.equal(e.nodes["lexeme-retry"].hidden,false);
});
(async()=>{let failed=0;for(const [name,fn]of tests){try{await fn();console.log(`✓ ${name}`);}catch(e){failed++;console.error(name,e);}}console.log(`${tests.length-failed}/${tests.length} Dutch frontend tests passed`);if(failed)process.exitCode=1;})();
