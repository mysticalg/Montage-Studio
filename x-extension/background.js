const BASE='http://127.0.0.1:8765';
let running=false;
async function store(v){await chrome.storage.local.set(v);}
async function api(route,data={},token){
  token=token||(await chrome.storage.local.get('token')).token;
  if(!token)throw Error('Pair the extension in Montage Studio Settings first.');
  const r=await fetch(BASE+'/api/extension/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Extension':token,'X-Studio-Extension-Id':chrome.runtime.id},body:JSON.stringify(data),signal:AbortSignal.timeout(15000)});
  const result=await r.json();if(!r.ok)throw Error(result.error||'Local app refused request');return result;
}
async function pageMessage(tabId,message){
  for(let i=0;i<40;i++){
    try{return await chrome.tabs.sendMessage(tabId,message);}catch(e){if(i===39)throw Error('X page did not load the extension. Refresh X and retry.');await new Promise(r=>setTimeout(r,500));}
  }
}
async function verify(){
  const saved=await chrome.storage.local.get(['tabId','active']);
  if(saved.active)throw Error('An upload is active; wait before verifying again.');
  let tab=saved.tabId?await chrome.tabs.get(saved.tabId).catch(()=>null):null;
  if(!tab||!tab.url?.startsWith('https://x.com/'))tab=await chrome.tabs.create({url:'https://x.com/home',active:true});
  await store({tabId:tab.id});
  const result=await pageMessage(tab.id,{action:'verify'});
  if(!result?.ok)throw Error(result?.error||'Sign into X yourself, then verify again.');
  await api('heartbeat',{account:result.account,status:'Extension verified as @'+result.account});
  await store({account:result.account,status:'Verified @'+result.account});return result;
}
async function stop(message){await store({enabled:false,status:message});await chrome.alarms.clear('queue');}
async function tick(){
  if(running)return;running=true;let claimed=null;
  try{
    const s=await chrome.storage.local.get(['enabled','account','active','outcome','tabId']);
    // Always deliver saved outcomes before looking for another job.
    if(s.outcome){await api('report',s.outcome);await store({outcome:null,active:null,status:s.outcome.verifiedText||s.outcome.message});return;}
    if(s.active){
      let alive=false;
      try{alive=(await pageMessage(s.active.tabId,{action:'operation-status',id:s.active.id})).active;}catch{}
      if(!alive){await api('report',{id:s.active.id,lease:s.active.lease,phase:'error',message:'X tab or extension operation was interrupted. Check X before retrying.'});await store({active:null});await stop('Interrupted operation; check X before retrying.');}
      return;
    }
    if(!s.enabled)return;
    if(!s.account)throw Error('Verify your X account first.');
    await api('heartbeat',{account:s.account,status:'Extension queue enabled'});
    const {job}=await api('take');if(!job)return;
    claimed=job;
    let tab=s.tabId?await chrome.tabs.get(s.tabId).catch(()=>null):null;
    // Dedicated tab keeps existing user drafts untouched. Reuse our prepared composer only.
    if(!tab||!tab.url?.startsWith('https://x.com/'))tab=await chrome.tabs.create({url:'https://x.com/compose/post',active:true});
    else {
      const own=await pageMessage(tab.id,{action:'prepared',id:job.id});
      if(!own?.prepared)await chrome.tabs.update(tab.id,{url:'https://x.com/compose/post',active:true});
    }
    await store({tabId:tab.id,active:{...job,tabId:tab.id},status:'Uploading '+job.filename});
    const r=await pageMessage(tab.id,{action:'start',job});
    if(!r?.ok)throw Error(r?.error||'Could not start upload');
  }catch(e){
    const s=await chrome.storage.local.get('active'),operation=s.active||claimed;
    if(operation){
      const result={id:operation.id,lease:operation.lease,phase:'error',message:e.message};
      await store({outcome:result});
      try{await api('report',result);await store({outcome:null,active:null});}catch{}
    }
    await stop(e.message);
  }finally{running=false;}
}
chrome.alarms.onAlarm.addListener(a=>{if(a.name==='queue')tick();});
chrome.runtime.onInstalled.addListener(()=>store({enabled:false,status:'Pair and verify your X account to start.'}));
chrome.runtime.onStartup.addListener(async()=>{
  const s=await chrome.storage.local.get('active');
  await store({enabled:false,status:s.active?'Browser restarted during an operation. Check X; do not requeue automatically.':'Browser restarted. Verify X, then enable the queue.'});
  await chrome.alarms.clear('queue');if(s.active)tick();
});
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  (async()=>{
    const s=await chrome.storage.local.get(['active','enabled','status','token']);
    if(sender.tab){
      const a=s.active;
      if(!a||sender.tab.id!==a.tabId||m.id!==a.id||m.lease!==a.lease||new URL(sender.tab.url).origin!=='https://x.com')throw Error('Unassigned X operation');
      if(m.action==='chunk')return api('chunk',{id:a.id,lease:a.lease,offset:m.offset});
      if(m.action==='progress'||m.action==='commit')return api('report',{id:a.id,lease:a.lease,phase:m.action,message:m.message});
      if(m.action==='schedule-preflight'){
        const tab=await chrome.tabs.create({url:'https://x.com/compose/post/unsent/scheduled',active:false});
        try{
          const r=await pageMessage(tab.id,{action:'existing-schedule',job:{...a,label:m.label}});
          if(!r?.ok)throw Error(r?.error||'Scheduled list unavailable');return r;
        }finally{await chrome.tabs.remove(tab.id).catch(()=>{});}
      }
      if(m.action==='schedule-check'){
        const tab=await chrome.tabs.create({url:'https://x.com/compose/post/unsent/scheduled',active:true});
        await store({active:{...a,tabId:tab.id},tabId:tab.id,status:'Verifying X Scheduled list'});
        const r=await pageMessage(tab.id,{action:'check-schedule',job:{...a,label:m.label}});
        if(!r?.ok)throw Error(r?.error||'Scheduled list unavailable');return {ok:true};
      }
      if(m.action==='outcome'){
        const result={id:a.id,lease:a.lease,phase:m.ok?'complete':'error',verifiedText:m.verifiedText,postUrl:m.postUrl,message:m.message};
        await store({outcome:result});
        try{await api('report',result);await store({outcome:null,active:null,status:m.verifiedText||m.message});}catch(e){await stop('Result saved; local app unavailable. '+e.message);return {ok:true};}
        if(!m.ok)await stop(m.message);else if(a.action==='prepare')await stop('Video prepared in X. Review it, then queue Post or Schedule in the app.');
        return {ok:true};
      }
      throw Error('Unknown content action');
    }
    if(sender.id!==chrome.runtime.id||!sender.url?.startsWith(chrome.runtime.getURL('popup.html')))throw Error('Popup required');
    if(m.action==='pair'){
      if(s.active)throw Error('Wait for active operation before pairing.');
      await api('connect',{},m.token);await store({token:m.token,account:'',status:'Paired. Verify X account next.'});return {ok:true,status:'Paired. Click Verify X account.'};
    }
    if(m.action==='verify'){const r=await verify();return {ok:true,status:'Verified @'+r.account};}
    if(m.action==='enable'){
      if(m.enabled){const saved=await chrome.storage.local.get('account');if(!saved.account)throw Error('Verify X first.');await store({enabled:true,status:'Queue enabled'});await chrome.alarms.create('queue',{periodInMinutes:.5});tick();}
      else await stop('Queue disabled. An operation already started may finish.');
      return {ok:true,enabled:!!m.enabled,status:m.enabled?'Queue enabled':'Queue disabled'};
    }
    if(m.action==='run'){await tick();const v=await chrome.storage.local.get('status');return {ok:true,status:v.status};}
    if(m.action==='status')return {ok:true,status:s.status||'Pair with app first.',enabled:!!s.enabled};
    throw Error('Unknown popup action');
  })().then(reply,e=>reply({ok:false,error:e.message}));return true;
});
