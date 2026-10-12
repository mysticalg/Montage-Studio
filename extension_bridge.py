"""Explicitly paired loopback bridge. Exposes queued exports, never login cookies."""
import base64, json, re, secrets, time
from pathlib import Path

class ExtensionBridge:
    def __init__(self,data,settings,jobs,get_job,update,claim,lock):
        self.data=Path(data);self.settings=settings;self.jobs=jobs;self.get_job=get_job
        self.update=update;self.claim_job=claim;self.lock=lock
        self.account='';self.last_seen=0;self.status='Extension not paired'
    def config(self):
        p=self.data/'extension-pair.json'
        return json.loads(p.read_text()) if p.exists() else {}
    def pair_code(self):
        with self.lock:
            if any(j['status']=='extension_active' for j in self.jobs()):raise ValueError('Wait for the active extension operation before changing its pairing code')
            code=secrets.token_urlsafe(32)
            (self.data/'extension-pair.json').write_text(json.dumps({'token':code}),encoding='utf-8')
            self.account='';self.last_seen=0;self.status='Pairing code created. Paste it into the extension.'
            return code
    def authorize(self,token,origin,connect=False):
        if not re.fullmatch(r'chrome-extension://[a-p]{32}',origin):raise ValueError('Extension origin required')
        with self.lock:
            c=self.config()
            if not token or not secrets.compare_digest(token,c.get('token','')):raise ValueError('Invalid extension pairing code')
            if c.get('origin') and c['origin']!=origin:raise ValueError('A different extension is paired. Generate a new code in Settings.')
            if not c.get('origin'):
                if not connect:raise ValueError('Pair the extension first')
                c['origin']=origin;(self.data/'extension-pair.json').write_text(json.dumps(c),encoding='utf-8')
    def state(self):
        with self.lock:
            for j in self.jobs():
                if j['status']=='extension_active' and j['payload'].get('extensionDeadline',0)<time.time():
                    self.update(j['id'],'uncertain' if j['payload'].get('extensionAttempted') else 'error',error='Extension connection timed out. Check X before retrying.')
        expected=self.settings().get('expectedAccount','').lstrip('@').strip()
        return {'paired':bool(self.config().get('origin')),'connected':bool(self.account and time.time()-self.last_seen<75 and self.account.lower()==expected.lower()),'account':self.account,'status':self.status}
    def heartbeat(self,p):
        expected=self.settings().get('expectedAccount','').lstrip('@').strip()
        account=str(p.get('account','')).lstrip('@')
        if account and account.lower()!=expected.lower():raise ValueError(f'Expected @{expected}; extension has @{account}')
        self.account=account;self.last_seen=time.time();self.status=str(p.get('status','Extension connected'))[:250]
        return {'ok':True,'expectedAccount':expected}
    def take(self):
        with self.lock:
            if not self.state()['connected']:raise ValueError('Verify your X account in the extension first')
            active=next((j for j in self.jobs() if j['status']=='extension_active'),None)
            if active:
                if active['payload'].get('extensionDeadline',0)<time.time():
                    self.update(active['id'],'uncertain' if active['payload'].get('extensionAttempted') else 'error',error='Extension operation interrupted. Check X before retrying.')
                return {'ok':True,'job':None}
            j=next((j for j in reversed(self.jobs()) if j['status']=='extension_queued'),None)
            if not j:return {'ok':True,'job':None}
            path=Path(j['payload'].get('file','')).resolve()
            if not path.is_file() or not path.is_relative_to((self.data/'exports').resolve()):raise ValueError('Export file missing or outside exports')
            if not self.claim_job(j['id'],'extension_active',('extension_queued',)):return {'ok':True,'job':None}
            lease=secrets.token_urlsafe(24)
            self.update(j['id'],error='',payload={'extensionLease':lease,'extensionDeadline':time.time()+120,'extensionAttempted':False,'progress':'Uploading through extension'})
            p=j['payload']
            return {'ok':True,'job':{'id':j['id'],'lease':lease,'filename':path.name,'size':path.stat().st_size,'caption':p.get('caption',''),'madeWithAI':p.get('madeWithAI',True),'action':p.get('extensionAction','post'),'at':j['scheduled'],'expectedAccount':self.settings()['expectedAccount']}}
    def leased(self,p):
        j=self.get_job(p.get('id'))
        if not j or j['status']!='extension_active' or not secrets.compare_digest(str(p.get('lease','')),j['payload'].get('extensionLease','!')):raise ValueError('Inactive extension operation')
        return j
    def chunk(self,p):
        with self.lock:
            j=self.leased(p);offset=int(p.get('offset',0));path=Path(j['payload']['file']).resolve()
            if not path.is_relative_to((self.data/'exports').resolve()):raise ValueError('Invalid export')
            if offset<0 or offset>=path.stat().st_size:raise ValueError('Invalid media offset')
            with path.open('rb') as f:f.seek(offset);raw=f.read(1024*1024)
            self.update(j['id'],payload={'extensionDeadline':time.time()+120})
            return {'ok':True,'data':base64.b64encode(raw).decode(),'length':len(raw)}
    def report(self,p):
        with self.lock:
            j=self.get_job(p.get('id'))
            # Result delivery can retry after a lost response; publication never retries.
            if j and j['payload'].get('extensionCompletedLease')==p.get('lease') and p.get('phase') in ('complete','error'):return {'ok':True}
            j=self.leased(p);phase=p.get('phase')
            if phase in ('progress','commit'):
                self.update(j['id'],payload={'extensionDeadline':time.time()+120,'progress':str(p.get('message','Working in X'))[:250],**({'extensionAttempted':True} if phase=='commit' else {})})
            elif phase=='complete':
                action=j['payload'].get('extensionAction')
                if action!='prepare' and not j['payload'].get('extensionAttempted'):raise ValueError('No final action recorded')
                if not p.get('verifiedText'):raise ValueError('X confirmation required')
                payload={'progress':str(p['verifiedText'])[:500],'verifiedText':str(p['verifiedText'])[:1000],'extensionCompletedLease':p['lease'],'extensionLease':'','extensionPrepared':action=='prepare'}
                url=str(p.get('postUrl',''))
                if url and re.fullmatch(r'https://x\.com/[A-Za-z0-9_]+/status/\d+',url):payload['postUrl']=url
                self.update(j['id'],'ready' if action=='prepare' else 'web_scheduled' if action=='schedule' else 'posted',error='',payload=payload)
            elif phase=='error':
                self.update(j['id'],'uncertain' if j['payload'].get('extensionAttempted') else 'error',error=str(p.get('message','Extension stopped'))[:1000],payload={'extensionCompletedLease':p['lease'],'extensionLease':''})
            else:raise ValueError('Unknown extension report')
            return {'ok':True}
