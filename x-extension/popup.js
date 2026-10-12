const $=id=>document.getElementById(id);
async function act(action,extra={}){try{const result=await chrome.runtime.sendMessage({action,...extra});if(!result?.ok)throw Error(result?.error||'Extension unavailable');$('status').textContent=result.status||'Done';if('enabled' in result)$('enabled').checked=result.enabled;}catch(e){$('status').textContent=e.message;}}
$('pair').onclick=()=>act('pair',{token:$('token').value.trim()});
$('verify').onclick=()=>act('verify');
$('run').onclick=()=>act('run');
$('stop').onclick=()=>act('enable',{enabled:false});
$('enabled').onchange=()=>act('enable',{enabled:$('enabled').checked});
act('status');
