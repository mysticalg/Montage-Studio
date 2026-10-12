import {test} from 'node:test';import assert from 'node:assert/strict';import vm from 'node:vm';import fs from 'node:fs/promises';
const source=await fs.readFile(new URL('background.js',import.meta.url),'utf8');
function harness(){
  const storage={token:'paired',account:'demo_creator',enabled:true},calls=[];let listener,starts=0,takes=0,failReport=false;
  const job={id:'job',lease:'lease',filename:'neutral.mp4',size:7,caption:'Neutral',action:'post',expectedAccount:'demo_creator'};
  const runtime={id:'a'.repeat(32),getURL:p=>'chrome-extension://'+'a'.repeat(32)+'/'+p,onMessage:{addListener:f=>listener=f},onInstalled:{addListener(){}},onStartup:{addListener(){}}};
  const chrome={runtime,storage:{local:{async get(keys){return Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(k=>[k,storage[k]]));},async set(v){Object.assign(storage,v);}}},alarms:{onAlarm:{addListener(){}},async clear(){},async create(){}},tabs:{async get(id){return{id,url:'https://x.com/compose/post'};},async create(){return{id:7,url:'https://x.com/compose/post'};},async update(id){return{id};},async sendMessage(id,m){if(m.action==='start'){starts++;return{ok:true};}if(m.action==='operation-status')return{ok:true,active:true};if(m.action==='prepared')return{ok:true,prepared:false};return{ok:true};}}};
  const context=vm.createContext({chrome,URL,AbortSignal,setTimeout,console,fetch:async(url,options)=>{const route=url.split('/').pop();calls.push({route,data:JSON.parse(options.body)});if(route==='report'&&failReport)throw Error('Local app offline');return{ok:true,json:async()=>route==='take'?{ok:true,job:takes++===0?job:null}:{ok:true}};}});
  vm.runInContext(source+'\nglobalThis.testTick=tick;',context);
  return{storage,calls,get starts(){return starts;},set failReport(v){failReport=v;},tick:()=>context.testTick(),message:m=>new Promise(resolve=>listener(m,{id:runtime.id,tab:{id:7,url:'https://x.com/compose/post'}},resolve))};
}
test('one active operation prevents duplicate queue claims',async()=>{
  const h=harness();await h.tick();await h.tick();assert.equal(h.starts,1);assert.equal(h.calls.filter(x=>x.route==='take').length,1);
  const result=await h.message({action:'outcome',id:'job',lease:'lease',ok:true,verifiedText:'Your post was sent'});assert.equal(result.ok,true);assert.equal(h.storage.active,null);
});
test('lost result delivery retries acknowledgement, never the publication',async()=>{
  const h=harness();await h.tick();h.failReport=true;
  await h.message({action:'outcome',id:'job',lease:'lease',ok:true,verifiedText:'Your post was sent'});
  assert.equal(h.storage.enabled,false);assert.equal(h.storage.outcome.phase,'complete');
  h.failReport=false;await h.tick();assert.equal(h.starts,1);assert.equal(h.storage.outcome,null);assert.equal(h.storage.active,null);
});
test('unassigned content tab cannot read media or report a result',async()=>{
  const h=harness();await h.tick();const before=h.calls.length;
  const result=await h.message({action:'chunk',id:'other',lease:'wrong',offset:0});assert.equal(result.ok,false);assert.equal(h.calls.length,before);
});
