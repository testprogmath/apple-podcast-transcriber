"use strict";
const assert = require("assert");
const { setup, loadApp, speechStub } = require("./dom.js");
const ID = "a".repeat(32);
const DOC = { id: ID, title: "中文标题", source_type: "pasted_text", audio_status: "text",
  hanly_available: true, mosaic_available: true, glossary: { 中文: { p: "zhōng wén", en: ["Chinese"] } },
  paragraphs: [[0]], sentences: [{ id: 0, text: "中文。", tokens: [{ t: "中文", w: true }, {t:"。",w:false}] }] };
const card = (title="中文标题", label="Pasted text") => ({ id: ID, title, source_label: label, created: "2026-09-30T12:00:00Z" });
function start({search="", documents=[], clipboard, respond, speech}={}) {
  const env=setup({search});
  const navigation=[];
  location.assign=(url)=>navigation.push(url);
  if (clipboard) window.navigator={clipboard};
  if (speech) Object.assign(window,speech.window);
  const calls=[];
  global.fetch=async (path, options={})=>{
    calls.push({path, ...options});
    if (respond) { const r=await respond(path,options); if(r) return r; }
    const result=path===`/api/reader/${ID}` ? DOC : {documents};
    return {ok:true,json:async()=>result};
  };
  loadApp();
  return {...env,calls,navigation};
}
const ok=(data)=>({ok:true,json:async()=>data});
const error=()=>({ok:false,json:async()=>({error:"Network failed"})});
async function settle() { for(let i=0;i<6;i++) await new Promise(r=>setImmediate(r)); }
const tests=[];
const test=(name,fn)=>tests.push([name,fn]);
test("empty library without a document opens creation",async()=>{
  const e=start(); await settle();
  assert.equal(e.calls[0].path,"/api/reader/documents");
  assert.equal(e.nodes.library.hidden,false);
  assert.equal(e.nodes.text.hidden,true);
  assert.match(e.nodes["library-status"].textContent,/Paste Chinese/);
  e.nodes["new-text"].click();
  assert.equal(e.nodes["new-document"].hidden,false);
  e.nodes["create-back"].click();
  assert.equal(e.nodes.library.hidden,false);
});
test("mixed-source cards contain safe text and durable direct URLs",async()=>{
  const e=start({documents:[card('<img onerror=alert(1)>中文'),card("podcast","Podcast"),card("notes","Markdown")]}); await settle();
  const cards=e.nodes["document-list"].children;
  assert.equal(cards.length,3);
  assert.equal(cards[0].children[0].textContent,'<img onerror=alert(1)>中文');
  assert.equal(cards[0].children[0].children.length,0);
  assert.equal(cards[0].href,`/reader/?doc=${ID}`);
  assert.match(cards[2].textContent,/Markdown/);
});
test("direct document and refresh reuse original Reader",async()=>{
  for(let i=0;i<2;i++) {
    const e=start({search:`?doc=${ID}`}); await settle();
    assert.equal(e.calls[0].path,`/api/reader/${ID}`);
    assert.equal(e.nodes.library.hidden,true);
    assert.equal(e.nodes.text.hidden,false);
    assert.equal(e.body.querySelectorAll(".word").length,1);
  }
});
test("normal paste creates without clipboard and preserves inner newlines",async()=>{
  const e=start({respond:(p,o)=>o.method==="POST"?ok({id:ID}):null}); await settle();
  e.nodes["new-text"].click();
  e.nodes["new-text-input"].value=" 中文 English 😊。\n\n第二段。 ";
  e.nodes["new-title"].value="我的标题";
  e.nodes["create-reader"].click(); await settle();
  const request=JSON.parse(e.calls.find(c=>c.method==="POST").body);
  assert.equal(request.text," 中文 English 😊。\n\n第二段。 ");
  assert.equal(request.source_type,"pasted_text");
  assert.equal(request.title,"我的标题");
  assert.deepEqual(e.navigation,[`/reader/?doc=${ID}`]);
});
test("creation errors retain textarea and title; blank input never submits",async()=>{
  const e=start({respond:(p,o)=>o.method==="POST"?error():null}); await settle();
  e.nodes["new-text-input"].value="  ";
  e.nodes["create-reader"].click(); await settle();
  assert.equal(e.calls.length,1);
  e.nodes["new-text-input"].value="中文。";
  e.nodes["new-title"].value="我的标题";
  e.nodes["create-reader"].click(); await settle();
  assert.equal(e.nodes["new-text-input"].value,"中文。");
  assert.equal(e.nodes["new-title"].value,"我的标题");
  assert.equal(e.nodes["create-reader"].disabled,false);
  assert.match(e.nodes["create-status"].textContent,/Network failed/);
});
test("clipboard only reads on click",async()=>{
  let reads=0;
  const e=start({clipboard:{readText:async()=>{reads++;return "繁體中文。";}}}); await settle();
  assert.equal(reads,0);
  e.nodes["new-text"].click(); assert.equal(reads,0);
  e.nodes["clipboard-paste"].click(); await settle();
  assert.equal(reads,1);
  assert.equal(e.nodes["new-text-input"].value,"繁體中文。");
});
for (const mode of ["missing","denied"]) test(`clipboard ${mode} preserves manual input`,async()=>{
  const clipboard=mode==="denied"?{readText:async()=>{throw Error("denied");}}:undefined;
  const e=start({clipboard}); await settle();
  e.nodes["new-text-input"].value="已有中文";
  e.nodes["clipboard-paste"].click(); await settle();
  assert.equal(e.nodes["new-text-input"].value,"已有中文");
  assert.match(e.nodes["create-status"].textContent,/Paste into the text box/);
  assert.equal(document.activeElement,e.nodes["new-text-input"]);
  assert.notEqual(e.nodes["new-text-input"].disabled,true);
});
for(const extension of ["txt","md","markdown"]) test(`file ${extension} sends original UTF-8 bytes`,async()=>{
  const raw=Buffer.from("\ufeff# 中文 😊");
  const e=start({respond:(p,o)=>o.method==="POST"?ok({id:ID}):null}); await settle();
  e.nodes["file-input"].files=[{name:`阅读.${extension}`,size:raw.length,arrayBuffer:async()=>raw}];
  e.nodes["file-input"].fire("change"); await settle();
  const payload=JSON.parse(e.calls.find(c=>c.method==="POST").body);
  assert.equal(payload.filename,`阅读.${extension}`);
  assert.equal(Buffer.from(payload.content,"base64").toString(),raw.toString());
  assert.deepEqual(e.navigation,[`/reader/?doc=${ID}`]);
});
for(const file of [{name:"a.pdf",size:2},{name:"a.txt",size:524289}]) test(`reject file ${file.name} ${file.size} before reading`,async()=>{
  const e=start(); await settle();
  e.nodes["file-input"].files=[{...file,arrayBuffer:()=>{throw Error("must not read");}}];
  e.nodes["file-input"].fire("change"); await settle();
  assert.equal(e.calls.length,1);
  assert.equal(e.nodes["upload-file"].disabled,false);
  assert.match(e.nodes["library-status"].textContent,/Choose|512 KiB/);
});
test("invalid UTF-8 server error stays in library",async()=>{
  const e=start({respond:(p,o)=>o.method==="POST"?{ok:false,json:async()=>({error:"Reader needs UTF-8"})}:null});await settle();
  e.nodes["file-input"].files=[{name:"a.txt",size:1,arrayBuffer:async()=>new Uint8Array([255])}];
  e.nodes["file-input"].fire("change");await settle();
  assert.match(e.nodes["library-status"].textContent,/UTF-8/);
  assert.deepEqual(e.navigation,[]);
});
test("rename preserves the document view, selection, URL and inert title",async()=>{
  const title='<script>中文</script>';
  const e=start({search:`?doc=${ID}`,respond:(p,o)=>o.method==="PATCH"?ok({id:ID,title}):null});await settle();
  const word=e.body.querySelector(".word");word.click();e.nodes["lexeme-action"].click();
  const counter=e.nodes["hanly-counter"].textContent;
  e.nodes["rename-document"].click();e.nodes["rename-title"].value=title;
  e.nodes["rename-save"].click();await settle();
  assert.equal(e.nodes.title.textContent,title);assert.equal(e.nodes.title.children.length,0);
  assert.equal(e.body.querySelector(".word"),word);
  assert.equal(e.nodes["hanly-counter"].textContent,counter);
  assert.deepEqual(e.navigation,[]);
});
test("file document retains dictionary, pinyin, baskets and TTS",async()=>{
  const speech=speechStub();
  const e=start({search:`?doc=${ID}`,speech,respond:(p,o)=>p===`/api/reader/${ID}`?ok({...DOC,source_type:"file"}):null});await settle();
  e.nodes["pinyin-toggle"].click();
  e.body.querySelector(".word").click();
  assert.match(e.nodes["lexeme-pinyin"].textContent,/zhōng/);
  e.nodes["lexeme-pronounce"].click();assert.equal(speech.last().text,"中文");
  e.nodes["lexeme-action"].click();
  e.body.querySelector(".audio-action").click();assert.equal(speech.last().text,"中文。");
  assert.doesNotMatch(e.nodes.toast.textContent,/original audio/i);
  assert.match(e.nodes["hanly-counter"].textContent,/1/);
});
test("library request failure is retryable",async()=>{
  let fail=true;const e=start({respond:()=>fail?error():ok({documents:[]})});await settle();
  assert.equal(e.nodes["library-retry"].hidden,false);
  fail=false;e.nodes["library-retry"].click();await settle();
  assert.equal(e.nodes["library-retry"].hidden,true);
});
(async()=>{let failed=0;for(const [name,fn] of tests){try{await fn();console.log(`✓ ${name}`);}catch(e){failed++;console.error(name,e);}}console.log(`${tests.length-failed}/${tests.length} library frontend tests passed`);if(failed)process.exitCode=1;})();
