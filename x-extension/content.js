(()=>{
  let active=null,prepared=null;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const visible=e=>!!(e&&e.getClientRects().length);
  const text=e=>(e?.textContent||'').trim();
  const norm=s=>s.replace(/\s+/g,' ').trim();
  function button(name){return [...document.querySelectorAll('button,[role=button]')].find(e=>visible(e)&&(e.getAttribute('aria-label')===name||text(e)===name));}
  function barrier(){
    if(location.pathname.startsWith('/account/access')||location.pathname.includes('/flow/'))throw Error('X login or security check required. Complete it yourself, then verify the extension again.');
    const alerts=[...document.querySelectorAll('[role=alert],[data-testid=toast]')].map(text).join(' ').toLowerCase();
    if(/rate limit|too many requests|daily limit|automated|suspicious|unusual activity|verify you are human/.test(alerts))throw Error('X showed a rate or security warning. Queue stopped; resolve it yourself.');
  }
  async function wait(fn,message,timeout=20000){
    const end=Date.now()+timeout;
    while(Date.now()<end){barrier();const value=fn();if(value)return value;await sleep(300);}
    throw Error(message);
  }
  async function verify(expected){
    const profile=await wait(()=>document.querySelector('[data-testid=AppTabBar_Profile_Link]'),'Sign into X in this browser, then Verify X account in the extension.',15000);
    const account=new URL(profile.href,location.origin).pathname.split('/').filter(Boolean)[0];
    if(!account)throw Error('X account could not be verified.');
    if(expected&&account.toLowerCase()!==expected.replace(/^@/,'').toLowerCase())throw Error('Signed in as @'+account+'; expected @'+expected+'. Switch accounts first.');
    return account;
  }
  function scheduleLabel(timestamp){
    const d=new Date(timestamp*1000);
    return d.toLocaleString('en-US',{month:'short'})+' '+d.getDate()+', '+d.getFullYear()+' at '+(d.getHours()%12||12)+':'+String(d.getMinutes()).padStart(2,'0')+' '+(d.getHours()>=12?'PM':'AM');
  }
  async function existingSchedule(job){
    await verify(job.expectedAccount);
    await wait(()=>[...document.querySelectorAll('[role=tab]')].some(e=>visible(e)&&text(e)==='Scheduled'),'X Scheduled tab could not be checked.');
    const end=Date.now()+8000;let exhausted=false;
    while(Date.now()<end){
      barrier();
      const match=[...document.querySelectorAll('[role=button],button')].find(e=>visible(e)&&text(e).includes(job.label)&&(!job.caption||norm(text(e)).includes(norm(job.caption))));
      if(match)return true;
      const dialog=[...document.querySelectorAll('[role=dialog]')].find(visible);
      const scroll=dialog&&[dialog,...dialog.querySelectorAll('*')].find(e=>e.scrollHeight>e.clientHeight+10&&/auto|scroll/.test(getComputedStyle(e).overflowY));
      if(scroll){const before=scroll.scrollTop;scroll.scrollTop+=Math.max(100,scroll.clientHeight/2);exhausted=scroll.scrollTop===before;}
      else exhausted=true;
      if(exhausted){await sleep(1000);const match=[...document.querySelectorAll('[role=button],button')].find(e=>visible(e)&&text(e).includes(job.label)&&(!job.caption||norm(text(e)).includes(norm(job.caption))));return !!match;}
      await sleep(200);
    }
    throw Error('Could not check the full Scheduled list. No new schedule submitted.');
  }
  async function send(action,extra={}){
    const r=await chrome.runtime.sendMessage({action,id:active.id,lease:active.lease,...extra});
    if(!r?.ok)throw Error(r?.error||'Extension connection lost');return r;
  }
  function select(label,value){
    const field=[...document.querySelectorAll('select')].find(e=>visible(e)&&(e.getAttribute('aria-label')===label||[...document.querySelectorAll('label')].some(l=>text(l)===label&&(l.control===e||l.contains(e)))));
    if(!field)throw Error('X schedule '+label+' control changed. No schedule submitted.');
    const option=[...field.options].find(o=>o.label===value||o.value===value);
    if(!option)throw Error('X schedule '+label+' option unavailable.');
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(field,option.value);
    field.dispatchEvent(new Event('change',{bubbles:true}));
  }
  async function upload(job){
    const editor=await wait(()=>document.querySelector('[data-testid=tweetTextarea_0][contenteditable=true], [role=textbox][contenteditable=true]'),'X composer not found.');
    const existing=text(editor),attachments=document.querySelector('[data-testid=attachments]');
    if(prepared!==job.id&&(existing||attachments))throw Error('Existing X draft found. Save or discard it yourself; extension will not overwrite it.');
    if(prepared===job.id&&existing!==job.caption)throw Error('Prepared caption was changed in X. Save/discard the draft yourself before continuing.');
    if(prepared!==job.id){
      if(job.size>512*1024*1024)throw Error('Video exceeds the extension’s 512 MB transfer limit.');
      await send('progress',{message:'Transferring video to X browser'});
      const chunks=[];
      for(let offset=0;offset<job.size;){
        const r=await send('chunk',{offset});
        if(!r.length)throw Error('Incomplete video transfer');
        const raw=atob(r.data),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
        chunks.push(bytes);offset+=r.length;
      }
      barrier();editor.focus();document.execCommand('insertText',false,job.caption);
      if(norm(text(editor))!==norm(job.caption))throw Error('X did not accept the caption. No post submitted.');
      const input=document.querySelector('input[data-testid=fileInput],input[type=file]');
      if(!input)throw Error('X file upload control changed.');
      const transfer=new DataTransfer();transfer.items.add(new File(chunks,job.filename,{type:'video/mp4'}));
      input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));prepared=job.id;
    }
    let lastProgress=0;
    await wait(()=>{
      if(Date.now()-lastProgress>20000){lastProgress=Date.now();send('progress',{message:'Waiting for X video processing'}).catch(()=>{});}
      const alerts=[...document.querySelectorAll('[role=status],[role=alert]')].map(text);
      if(alerts.some(t=>/failed to upload|could not be uploaded|upload failed|not supported/i.test(t)))throw Error('X rejected the upload.');
      const ready=alerts.some(t=>t.includes(job.filename.replace(/\.mp4$/i,''))&&/:\s*Ready\b/i.test(t));
      const post=document.querySelector('[data-testid=tweetButton]');
      return ready&&post&&!post.disabled&&post.getAttribute('aria-disabled')!=='true';
    },'X video did not become ready in five minutes. No post submitted.',300000);
    if(job.madeWithAI){
      const disclosure=button('Content disclosure');if(!disclosure)throw Error('X content disclosure control unavailable; set Made with AI manually.');disclosure.click();
      const toggle=await wait(()=>[...document.querySelectorAll('[role=switch]')].find(e=>visible(e)&&((e.getAttribute('aria-label')||'').includes('Made with AI')||text(e).includes('Made with AI'))),'Made with AI setting unavailable.');
      if(toggle.getAttribute('aria-checked')!=='true')toggle.click();
      await wait(()=>toggle.getAttribute('aria-checked')==='true','Made with AI setting did not save.');
      const done=button('Done');if(!done)throw Error('Disclosure Done button unavailable.');done.click();
    }
    await verify(job.expectedAccount);
  }
  async function run(job){
    active=job;
    try{
      await verify(job.expectedAccount);
      if(job.action==='schedule'){
        await send('progress',{message:'Checking for an existing identical X schedule'});
        const previous=await send('schedule-preflight',{label:scheduleLabel(job.at)});
        if(previous.exists)throw Error('An identical caption already exists at this time in X. Choose a different time or check that schedule yourself.');
      }
      await upload(job);
      if(job.action==='prepare'){await send('outcome',{ok:true,verifiedText:'Video and caption prepared in X for review'});return;}
      let final;
      if(job.action==='schedule'){
        const d=new Date(job.at*1000);if(d.getTime()<=Date.now())throw Error('Schedule time is in the past.');
        const schedule=button('Schedule post');if(!schedule)throw Error('X Schedule post control unavailable.');schedule.click();
        await wait(()=>[...document.querySelectorAll('select')].some(visible),'X schedule dialog not found.');
        select('Month',d.toLocaleString('en-US',{month:'long'}));select('Day',String(d.getDate()));select('Year',String(d.getFullYear()));select('Hour',String(d.getHours()).padStart(2,'0'));select('Minute',String(d.getMinutes()).padStart(2,'0'));
        const confirm=button('Confirm');if(!confirm)throw Error('Schedule Confirm unavailable.');confirm.click();
        final=await wait(()=>button('Schedule'),'Final Schedule button unavailable.');
      }else final=await wait(()=>button('Post'),'Final Post button unavailable.');
      barrier();if(final.disabled||final.getAttribute('aria-disabled')==='true')throw Error('X final button disabled.');
      // Durable commit marker precedes any final click, preventing ambiguous automatic retries.
      await send('commit',{message:'Submitting final '+(job.action==='schedule'?'Schedule':'Post')+' action'});final.click();
      if(job.action==='schedule'){
        await wait(()=>location.pathname==='/home'||[...document.querySelectorAll('[data-testid=toast]')].some(e=>/will send|scheduled/i.test(text(e))),'X schedule submission was not confirmed. Check X before retrying.',25000);
        const label=scheduleLabel(job.at);
        await send('schedule-check',{label,caption:job.caption});
      }else{
        const toast=await wait(()=>[...document.querySelectorAll('[data-testid=toast]')].find(e=>/Your post was sent/i.test(text(e))),'X post confirmation missing. Check X before retrying.',25000);
        const href=toast.querySelector('a[href*="/status/"]')?.href;
        await send('outcome',{ok:true,verifiedText:text(toast),postUrl:href});prepared=null;
      }
    }catch(e){try{await send('outcome',{ok:false,message:e.message});}catch{}}
    finally{active=null;}
  }
  async function checkSchedule(job){
    active=job;
    try{
      await verify(job.expectedAccount);
      let lastProgress=0;
      const found=await wait(()=>{
        if(Date.now()-lastProgress>20000){lastProgress=Date.now();send('progress',{message:'Verifying X Scheduled list'}).catch(()=>{});}
        const candidates=[...document.querySelectorAll('[role=button],button')].filter(e=>visible(e)&&text(e).includes(job.label)&&(!job.caption||norm(text(e)).includes(norm(job.caption))));
        if(candidates.length)return candidates[0];
        const dialog=[...document.querySelectorAll('[role=dialog]')].find(visible);
        if(dialog){const scroll=[dialog,...dialog.querySelectorAll('*')].find(e=>e.scrollHeight>e.clientHeight+10&&/auto|scroll/.test(getComputedStyle(e).overflowY));if(scroll)scroll.scrollTop+=Math.max(100,scroll.clientHeight/2);}
      },'Schedule not found in X Scheduled list. Check X before retrying.',30000);
      await send('outcome',{ok:true,verifiedText:'Saved in X Scheduled list: '+text(found)});
    }catch(e){try{await send('outcome',{ok:false,message:e.message});}catch{}}finally{active=null;}
  }
  chrome.runtime.onMessage.addListener((m,sender,reply)=>{
    if(sender.id!==chrome.runtime.id)return;
    if(m.action==='operation-status'){reply({ok:true,active:active?.id===m.id});return;}
    if(m.action==='prepared'){reply({ok:true,prepared:prepared===m.id});return;}
    if(m.action==='verify'){verify().then(account=>reply({ok:true,account}),e=>reply({ok:false,error:e.message}));return true;}
    if(m.action==='existing-schedule'){existingSchedule(m.job).then(exists=>reply({ok:true,exists}),e=>reply({ok:false,error:e.message}));return true;}
    if(m.action==='start'||m.action==='check-schedule'){
      if(active){reply({ok:false,error:'X operation already active'});return;}
      reply({ok:true});(m.action==='start'?run(m.job):checkSchedule(m.job));
    }
  });
})();
