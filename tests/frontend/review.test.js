"use strict";
const assert = require("assert");
const {setup, loadApp, speechStub} = require("./dom.js");
const ID = "a".repeat(32);
const card = {id: 1, kind: "expression", language: "nl", speech_language: "nl-NL",
  text: "🙂 woord, woord.", target: "woord", span: [9, 14], exercise: "recognition",
  document_id: ID, sentence_id: 0, title: "Authentic", interaction: "signed-token"};
const ok = data => ({ok:true,json:async()=>data});
const error = () => ({ok:false,json:async()=>({error:"Network failed"})});
function start({item=card, speech, respond, due=1}={}) {
  const e=setup({telegram:{initData:"telegram-proof",ready(){},expand(){}}});
  if(speech) Object.assign(window,speech.window);
  const calls=[];
  global.fetch=async(path,options={})=>{
    const request={path,...options};calls.push(request);
    const custom=respond&&await respond(path,options);if(custom)return custom;
    if(path==="/api/reader/documents")return ok({documents:[]});
    if(path==="/api/reader/review")return ok({due,items:due?[item]:[],limit:10});
    if(path.endsWith("/answer"))return ok({meaning:"слово"});
    if(path.endsWith("/grade"))return ok({due_at:"2030-01-02"});
    throw Error(`Unexpected request ${path}`);
  };
  loadApp();return {...e,calls};
}
async function settle(){for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r));}
async function begin(e){await settle();e.nodes["review-open"].click();await settle();e.nodes["review-start"].click();await settle();}
function button(e,label){const b=e.nodes["review-card"].querySelectorAll("button").find(b=>b.textContent===label);assert(b,`Missing ${label}`);return b;}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
test("entry, due count, bounded session API and Telegram auth",async()=>{
  const e=start();await settle();assert(!e.nodes["review-open"].hidden);
  e.nodes["review-open"].click();await settle();assert.equal(e.nodes["review-status"].textContent,"1 due");
  e.nodes["review-start"].click();await settle();
  const request=e.calls.find(c=>c.path==="/api/reader/review"&&c.method==="POST");
  assert.deepEqual(JSON.parse(request.body),{listening:false});
  assert(e.calls.every(c=>c.headers["X-Telegram-Init-Data"]==="telegram-proof"));
  assert.equal(e.nodes.library.hidden,true);assert.equal(e.nodes.review.hidden,false);
});
test("recognition hides answer, reveals authentic context and grades only on action",async()=>{
  const e=start();await begin(e);
  assert(e.nodes["review-card"].textContent.includes(card.text));
  assert(!e.nodes["review-card"].textContent.includes("слово"));
  button(e,"Show answer").click();await settle();
  assert(e.nodes["review-card"].textContent.includes("слово"));
  assert(!e.calls.some(c=>c.path.endsWith("/grade")));
  assert(!/Hanly|Mosaic/.test(e.nodes["review-card"].textContent));
  button(e,"Good").click();button(e,"Good").click();await settle();
  assert.equal(e.calls.filter(c=>c.path.endsWith("/grade")).length,1);
  assert.match(e.nodes["review-status"].textContent,/Session complete/);
  e.nodes["review-back"].click();await settle();assert.equal(e.nodes.library.hidden,false);
});
test("cloze blanks exact second Unicode occurrence as one unit and retains punctuation",async()=>{
  const e=start({item:{...card,exercise:"cloze"}});await begin(e);
  assert.equal(e.nodes["review-card"].querySelector(".review-context").textContent,"🙂 woord, _____.");
  button(e,"Show answer").click();await settle();
  assert.equal(e.nodes["review-card"].querySelector(".review-context").textContent,card.text);
  assert.equal(card.text,"🙂 woord, woord.");
});
test("phrase cloze uses a single blank",async()=>{
  const e=start({item:{...card,text:"Het land was in de greep van oorlog.",target:"in de greep van",span:[13,28],exercise:"cloze"}});await begin(e);
  assert.equal(e.nodes["review-card"].querySelector(".review-context").textContent,"Het land was _____ oorlog.");
});
test("listening hides text, uses Dutch TTS and stops shared channel on exit",async()=>{
  const speech=speechStub();const e=start({speech,item:{...card,exercise:"listening"}});await begin(e);
  assert(!e.nodes["review-card"].textContent.includes(card.text));
  assert.equal(JSON.parse(e.calls.find(c=>c.method==="POST").body).listening,true);
  button(e,"▶ Play sentence").click();assert.equal(speech.spoken[0].lang,"nl-NL");
  button(e,"▶ Play sentence").click();assert.equal(speech.queue.length,1);
  button(e,"Show text").click();await settle();assert(e.nodes["review-card"].textContent.includes(card.text));
  e.nodes["review-back"].click();assert.equal(speech.queue.length,0);
});
test("sentence target reveals translation without lexical prompt",async()=>{
  const e=start({item:{...card,kind:"sentence",span:null,target:""}});await begin(e);
  assert(!e.nodes["review-card"].textContent.includes("What does"));
  button(e,"Show translation").click();await settle();assert(e.nodes["review-card"].textContent.includes("слово"));
});
test("failed grade stays retryable with same interaction and chosen grade",async()=>{
  let attempts=0;
  const e=start({respond:(p)=>p.endsWith("/grade")&&++attempts===1?error():null});await begin(e);
  button(e,"Show answer").click();await settle();button(e,"Good").click();await settle();
  assert.match(e.nodes["review-status"].textContent,/Retry Good/);
  assert.equal(button(e,"Good").disabled,false);assert.equal(button(e,"Easy").disabled,true);
  button(e,"Good").click();await settle();
  const grades=e.calls.filter(c=>c.path.endsWith("/grade"));assert.equal(grades.length,2);assert.equal(grades[0].body,grades[1].body);
  assert.match(e.nodes["review-status"].textContent,/Session complete/);
});
test("answer failure remains revealable without grading",async()=>{
  let attempts=0;const e=start({respond:p=>p.endsWith("/answer")&&++attempts===1?error():null});await begin(e);
  button(e,"Show answer").click();await settle();assert.equal(button(e,"Show answer").disabled,false);
  assert(!e.calls.some(c=>c.path.endsWith("/grade")));
  button(e,"Show answer").click();await settle();assert(e.nodes["review-card"].textContent.includes("слово"));
});
test("nothing due and reopening refresh authoritative state",async()=>{
  const e=start({due:0});await settle();e.nodes["review-open"].click();await settle();
  assert.match(e.nodes["review-status"].textContent,/Nothing due/);assert(e.nodes["review-start"].hidden);
  e.nodes["review-back"].click();await settle();e.nodes["review-open"].click();await settle();
  assert.equal(e.calls.filter(c=>c.path==="/api/reader/review").length,2);
});
(async()=>{let passed=0;for(const [name,fn]of tests){try{await fn();passed++;console.log(`✓ ${name}`);}catch(e){console.error(name,e);process.exitCode=1;}}console.log(`${passed}/${tests.length} review DOM tests passed`);})();
