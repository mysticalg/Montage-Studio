import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {JSDOM} from 'jsdom';
const source=await fs.readFile(new URL('content.js',import.meta.url),'utf8');
function fixture({draft='',warning='',wrongAccount=false,failFinal=false,existingSchedule=false}={}){
  const dom=new JSDOM(`<a data-testid="AppTabBar_Profile_Link" href="/${wrongAccount?'Other':'demo_creator'}"></a><div data-testid="tweetTextarea_0" role="textbox" contenteditable="true">${draft}</div><input type="file" data-testid="fileInput"><div role="status" hidden></div><button data-testid="tweetButton">Post</button><button aria-label="Content disclosure">Content disclosure</button><button role="switch" aria-label="Made with AI" aria-checked="false">Made with AI</button><button>Done</button>${warning?'<div role="alert">'+warning+'</div>':''}`,{url:'https://x.com/compose/post',runScripts:'outside-only'});
  const w=dom.window;let handler,attached,finalClicks=0,resolve;
  const reports=[];const complete=new Promise(r=>resolve=r);
  w.Element.prototype.getClientRects=function(){return this.hidden?[]:[{}];};
  w.document.execCommand=(command,unused,value)=>{w.document.querySelector('[contenteditable]').textContent=value;return true;};
  w.DataTransfer=class{constructor(){this.files=[];this.items={add:file=>this.files.push(file)};}};
  const input=w.document.querySelector('input');Object.defineProperty(input,'files',{set:v=>attached=v,get:()=>attached});
  input.addEventListener('change',()=>{const status=w.document.querySelector('[role=status]');status.hidden=false;status.textContent='test.mp4: Ready';});
  w.document.querySelector('[role=switch]').onclick=e=>e.currentTarget.setAttribute('aria-checked','true');
  w.document.querySelector('[data-testid=tweetButton]').onclick=()=>{finalClicks++;if(!failFinal){const toast=w.document.createElement('div');toast.dataset.testid='toast';toast.innerHTML='Your post was sent <a href="/demo_creator/status/123">View</a>';w.document.body.append(toast);}};
  // Fast timers keep the missing-confirmation test bounded without changing production logic.
  if(failFinal){const realDate=w.Date;let now=0;w.Date=class extends realDate{static now(){return now+=1000;}};w.setTimeout=(f)=>setTimeout(f,0);}
  w.chrome={runtime:{id:'test-extension',onMessage:{addListener:f=>handler=f},sendMessage:async m=>{
    reports.push(m);
    if(m.action==='chunk')return {ok:true,length:7,data:Buffer.from('fixture').toString('base64')};
    if(m.action==='schedule-preflight')return {ok:true,exists:existingSchedule};
    if(m.action==='outcome'||m.action==='schedule-check'){resolve(m);return {ok:true};}
    return {ok:true};
  }}};
  w.eval(source);
  return {dom,reports,complete,get attached(){return attached;},get clicks(){return finalClicks;},start(action='prepare',extra={}){handler({action:'start',job:{id:'one',lease:'lease',filename:'test.mp4',size:7,caption:'Neutral city montage. #City',madeWithAI:true,action,expectedAccount:'demo_creator',...extra}},{id:'test-extension'},()=>{});},check(job){handler({action:'check-schedule',job},{id:'test-extension'},()=>{});}};
}
test('prepares a File, caption and disclosure without publishing',async()=>{
  const f=fixture();f.start();const result=await f.complete;
  assert.equal(result.ok,true);assert.equal(f.attached[0].name,'test.mp4');assert.equal(f.attached[0].size,7);
  assert.equal(f.dom.window.document.querySelector('[contenteditable]').textContent,'Neutral city montage. #City');
  assert.equal(f.dom.window.document.querySelector('[role=switch]').getAttribute('aria-checked'),'true');assert.equal(f.clicks,0);f.dom.window.close();
});
test('posts only after commit acknowledgement and verifies toast',async()=>{
  const f=fixture();f.start('post');const result=await f.complete;
  assert.equal(result.ok,true);assert.equal(f.clicks,1);assert.ok(f.reports.find(m=>m.action==='commit'));assert.equal(result.postUrl,'https://x.com/demo_creator/status/123');f.dom.window.close();
});
test('wrong account, foreign draft and security warnings prevent upload and final clicks',async()=>{
  for(const options of [{wrongAccount:true},{draft:'My existing draft'},{warning:'Suspicious activity detected'}]){
    const f=fixture(options);f.start('post');const result=await f.complete;
    assert.equal(result.ok,false);assert.equal(f.attached,undefined);assert.equal(f.clicks,0);f.dom.window.close();
  }
});
test('missing publication confirmation does not click Post twice',async()=>{
  const f=fixture({failFinal:true});f.start('post');const result=await f.complete;
  assert.equal(result.ok,false);assert.equal(f.clicks,1);assert.ok(f.reports.find(m=>m.action==='commit'));assert.match(result.message,/confirmation missing/);f.dom.window.close();
});
test('manifest access is limited to X and the loopback app',async()=>{
  const manifest=JSON.parse(await fs.readFile(new URL('manifest.json',import.meta.url)));
  assert.equal(manifest.manifest_version,3);assert.deepEqual(manifest.permissions,['storage','alarms']);
  assert.deepEqual(manifest.host_permissions,['http://127.0.0.1:8765/*','https://x.com/*']);
  assert.deepEqual(manifest.content_scripts[0].matches,['https://x.com/*']);
});
test('native scheduling sets fields, commits once, and requests Scheduled-list verification',async()=>{
  const f=fixture(),w=f.dom.window,d=new Date(2030,9,20,15,30);
  const open=w.document.createElement('button');open.textContent='Schedule post';w.document.body.append(open);
  open.onclick=()=>{
    for(const [label,value] of [['Month','October'],['Day','20'],['Year','2030'],['Hour','15'],['Minute','30']]){
      const field=w.document.createElement('select');field.setAttribute('aria-label',label);field.innerHTML=`<option value="blank">Choose</option><option value="${value}">${value}</option>`;w.document.body.append(field);
    }
    const confirm=w.document.createElement('button');confirm.textContent='Confirm';w.document.body.append(confirm);
    confirm.onclick=()=>{const final=w.document.createElement('button');final.textContent='Schedule';final.onclick=()=>w.history.replaceState({},'', '/home');w.document.body.append(final);};
  };
  f.start('schedule',{at:d.getTime()/1000});const result=await f.complete;
  assert.equal(result.action,'schedule-check');assert.equal(result.label,'Oct 20, 2030 at 3:30 PM');assert.ok(f.reports.find(m=>m.action==='commit'));
  for(const label of ['Month','Day','Year','Hour','Minute'])assert.notEqual(w.document.querySelector(`[aria-label="${label}"]`).value,'blank');
  assert.equal(f.clicks,0);w.close();
});
test('Scheduled-list check requires date and caption together',async()=>{
  const f=fixture(),w=f.dom.window,label='Oct 20, 2030 at 3:30 PM';
  const row=w.document.createElement('button');row.textContent=`Will send on ${label} Neutral city montage. #City`;w.document.body.append(row);
  f.check({id:'one',lease:'lease',label,caption:'Neutral city montage. #City',expectedAccount:'demo_creator'});
  const result=await f.complete;assert.equal(result.ok,true);assert.match(result.verifiedText,/Saved in X Scheduled list/);w.close();
});
test('an existing identical schedule prevents upload and submission',async()=>{
  const f=fixture({existingSchedule:true});f.start('schedule',{at:new Date(2030,9,20,15,30).getTime()/1000});
  const r=await f.complete;assert.equal(r.ok,false);assert.match(r.message,/identical caption already exists/);assert.equal(f.attached,undefined);assert.equal(f.clicks,0);assert.equal(f.reports.some(m=>m.action==='commit'),false);f.dom.window.close();
});
