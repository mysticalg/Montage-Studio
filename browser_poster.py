"""Browser posting through X's visible composer, using a separate local profile.

All browser objects live on one dedicated thread. No private X API calls,
credential extraction, CAPTCHA solving, or automatic uncertain-post retries.
"""
import calendar, importlib.util, queue, re, threading, time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

class BrowserFailure(RuntimeError):
    def __init__(self,message,uncertain=False):
        self.uncertain=uncertain
        super().__init__(message)

class BrowserPoster:
    def __init__(self,data,settings):
        self.data=Path(data); self.settings=settings; self.tasks=queue.Queue()
        self.lock=threading.Lock(); self.info=dict(available=bool(importlib.util.find_spec('playwright')),connected=False,status='Browser not opened',account='',busy=False)
        self.context=None; self.page=None; self.prepared=None;self.runtime=None
        threading.Thread(target=self._run,daemon=True).start()
    def state(self):
        with self.lock:return dict(self.info)
    def change(self,**values):
        with self.lock:self.info.update(values)
    def submit(self,action,job=None):
        future=Future(); self.tasks.put((action,job,future)); return future
    def call(self,action,job=None,timeout=660):
        return self.submit(action,job).result(timeout=timeout)
    def _run(self):
        while True:
            action,job,future=self.tasks.get()
            self.change(busy=True)
            try:
                if action=='open': result=self.open()
                elif action=='verify': result=self.verify()
                elif action=='prepare': result=self.prepare(job)
                elif action=='post': result=self.finish(job,False)
                elif action=='schedule': result=self.finish(job,True)
                elif action=='close':
                    if self.context:self.context.close()
                    if self.runtime:self.runtime.stop();self.runtime=None
                    self.context=None;self.page=None;self.prepared=None
                    self.change(connected=False,status='Browser closed');result={'ok':True}
                else:raise ValueError('Unknown browser action')
                future.set_result(result)
            except Exception as e:
                self.change(status=str(e))
                if any(x in str(e).lower() for x in ('login','sign in','closed')):self.change(connected=False)
                future.set_exception(e)
            finally:self.change(busy=False)
    def open(self):
        if not self.info['available']:raise BrowserFailure('Install Google Chrome or Microsoft Edge, then restart the app.')
        if self.context:
            try:self.page.title()
            except Exception:self.context=None
        if not self.context:
            if not self.runtime:
                from playwright.sync_api import sync_playwright
                self.runtime=sync_playwright().start()
            channel=self.settings().get('browserChannel','chrome')
            self.context=self.runtime.chromium.launch_persistent_context(str(self.data/'x-browser-profile'),channel=channel,headless=False,viewport={'width':1280,'height':900})
            self.page=self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto('https://x.com/home',wait_until='domcontentloaded',timeout=60000)
        self.page.bring_to_front()
        self.change(status='Sign in to X in the browser window, then click Verify browser login in the app.')
        return {'ok':True}
    def barrier(self):
        if any(s in self.page.url for s in ('/account/access','/i/flow/login','/i/flow/signup')):
            raise BrowserFailure('Sign in or complete the X security check yourself in the browser, then verify login again.')
        messages=' '.join(self.page.locator('[data-testid="toast"], [role="alert"]').all_text_contents()).lower()
        if any(word in messages for word in ('rate limit','too many requests','daily limit','automated','suspicious','unusual activity','verify you are human')):
            self.change(connected=False)
            raise BrowserFailure('X showed a rate or security warning. Browser posting stopped. Resolve it yourself before verifying login again.')
    def verify(self,navigate=True):
        if not self.context or not self.page:raise BrowserFailure('Open the X browser first and sign in.')
        if navigate:self.page.goto('https://x.com/home',wait_until='domcontentloaded',timeout=60000)
        self.barrier()
        expected=self.settings().get('expectedAccount','').strip().lstrip('@')
        if not expected:raise BrowserFailure('Enter the expected X account in Settings before posting.')
        profile=self.page.locator('[data-testid="AppTabBar_Profile_Link"]')
        try:profile.wait_for(state='visible',timeout=12000)
        except Exception:raise BrowserFailure('X login is not ready. Sign in in the browser window, then verify again.') from None
        account=(profile.get_attribute('href') or '').strip('/').split('/')[-1]
        if account.lower()!=expected.lower():raise BrowserFailure(f'Browser is logged in as @{account}, expected @{expected}. Switch accounts in X first.')
        self.change(connected=True,account=account,status='Browser connected as @'+account)
        return {'account':account,'ok':True}
    def ready(self,job):
        deadline=time.monotonic()+300
        button=self.page.locator('[data-testid="tweetButton"]')
        while time.monotonic()<deadline:
            self.barrier()
            text=' '.join(self.page.locator('[role="alert"], [role="status"]').all_text_contents()).lower()
            if any(x in text for x in ('failed to upload','could not be uploaded','upload failed','not supported')):
                raise BrowserFailure('X rejected the video upload. Check the browser for details.')
            processing=self.page.get_by_text(re.compile(r'^(Uploading|Processing)( video)?[.…]*$',re.I))
            status=self.page.get_by_role('status').filter(has_text=Path(job['payload']['file']).stem)
            upload_ready=status.count() and any(re.search(r':\s*Ready\b',x,re.I) for x in status.all_text_contents())
            if upload_ready and button.count() and button.is_enabled() and not processing.count():
                return
            time.sleep(1)
        raise BrowserFailure('X upload did not finish in five minutes. No final Post/Schedule button was clicked.')
    def prepare(self,job):
        self.verify(navigate=self.prepared!=job['id'])
        p=job['payload']
        if self.prepared!=job['id']:
            self.page.goto('https://x.com/compose/post',wait_until='domcontentloaded',timeout=60000)
            self.barrier()
            editor=self.page.get_by_role('textbox',name='Post text',exact=True)
            editor.wait_for(state='visible',timeout=15000)
            existing=editor.inner_text().strip()
            if existing or self.page.locator('[data-testid="attachments"]').count():
                raise BrowserFailure('An existing X composer draft is open. Save or discard that draft yourself before preparing another montage.')
            editor.fill(p.get('caption',''))
            inputs=self.page.locator('input[data-testid="fileInput"]')
            if not inputs.count():inputs=self.page.locator('input[type="file"]')
            inputs.first.set_input_files(p['file'])
            self.prepared=job['id']
        else:self.page.get_by_role('textbox',name='Post text',exact=True).fill(p.get('caption',''))
        disclosure=self.page.get_by_role('button',name='Content disclosure',exact=True)
        if p.get('madeWithAI',True):
            disclosure.click()
            switch=self.page.get_by_role('switch',name='Made with AI Mark this post as containing synthetically generated content.',exact=True)
            switch.set_checked(True)
            self.page.get_by_role('button',name='Done',exact=True).click()
        self.ready(job)
        self.change(status='Montage uploaded and ready in X. You can review it in the browser.')
        self.page.bring_to_front()
        return {'ok':True}
    @staticmethod
    def schedule_label(timestamp):
        d=datetime.fromtimestamp(timestamp)
        return f'{d:%b} {d.day}, {d.year} at {d.hour%12 or 12}:{d.minute:02} {"PM" if d.hour>=12 else "AM"}'
    def schedule_count(self,label,caption,proof=None):
        page=self.context.new_page()
        try:
            page.goto('https://x.com/compose/post/unsent/scheduled',wait_until='domcontentloaded',timeout=60000)
            candidates=page.get_by_role('button',name=re.compile(re.escape(label)))
            page.get_by_role('tab',name='Scheduled',exact=True).wait_for(state='visible',timeout=15000)
            try:page.get_by_role('button',name=re.compile('Will send on')).first.wait_for(state='visible',timeout=5000)
            except Exception:pass
            normalized=' '.join(caption.split())
            collected=set()
            for _ in range(100):
                for x in candidates.all_text_contents():
                    if not normalized or normalized in ' '.join(x.split()):collected.add(x)
                moved=page.get_by_role('dialog',name='Drafts',exact=True).evaluate('''root => {
                    const nodes=[root,...root.querySelectorAll('*')];
                    const scroll=nodes.find(n=>n.scrollHeight>n.clientHeight+10 && ['auto','scroll'].includes(getComputedStyle(n).overflowY));
                    if(!scroll)return false;
                    const before=scroll.scrollTop;scroll.scrollTop+=scroll.clientHeight;
                    return scroll.scrollTop>before;
                }''')
                if not moved:break
                page.wait_for_timeout(300)
            matching=list(collected)
            if proof and matching:
                candidates.last.scroll_into_view_if_needed(timeout=5000)
                page.screenshot(path=str(proof))
            return len(matching),matching
        finally:page.close()
    def finish(self,job,scheduled=False):
        attempted=False
        try:
            self.prepare(job)
            label=None;before=0
            if scheduled:
                at=job['scheduled']; d=datetime.fromtimestamp(at)
                if at<=time.time():raise BrowserFailure('Choose a future schedule time.')
                label=self.schedule_label(at)
                before,_=self.schedule_count(label,job['payload'].get('caption',''))
                if before:raise BrowserFailure('An identical caption is already scheduled at this time. Check X or choose a different time.')
                self.page.get_by_role('button',name='Schedule post',exact=True).click()
                for name,value in [('Month',calendar.month_name[d.month]),('Day',str(d.day)),('Year',str(d.year)),('Hour',f'{d.hour:02}'),('Minute',f'{d.minute:02}')]:
                    self.page.get_by_label(name,exact=True).select_option(label=value)
                self.page.get_by_role('button',name='Confirm',exact=True).click()
                final=self.page.get_by_role('button',name='Schedule',exact=True)
            else:final=self.page.get_by_role('button',name='Post',exact=True)
            self.barrier()
            if not final.is_enabled():raise BrowserFailure('X final button is disabled. Review the composer.')
            attempted=True;final.click(timeout=15000)
            if scheduled:
                self.page.wait_for_url('https://x.com/home',timeout=20000)
                proofs=self.data/'browser-proofs';proofs.mkdir(exist_ok=True)
                after,texts=self.schedule_count(label,job['payload'].get('caption',''),proofs/f'{job["id"]}.png')
                if after<=before:raise BrowserFailure('X schedule could not be verified in the Scheduled list.',True)
                result={'nativeSchedule':True,'verifiedText':texts[-1],'progress':'Saved in X Scheduled list'}
            else:
                toast=self.page.locator('[data-testid="toast"]').filter(has_text=re.compile(r'Your post was sent',re.I))
                toast.wait_for(state='visible',timeout=20000)
                link=toast.get_by_role('link',name='View',exact=True)
                href=link.get_attribute('href') if link.count() else None
                result={'progress':'Posted — browser confirmation','verifiedText':toast.inner_text()}
                if href:result['postUrl']=urljoin('https://x.com',href)
            self.prepared=None
            proofs=self.data/'browser-proofs';proofs.mkdir(exist_ok=True)
            if not scheduled:self.page.screenshot(path=str(proofs/f'{job["id"]}.png'))
            self.change(status=result['progress'])
            return result
        except Exception as e:
            raise BrowserFailure(str(e),attempted or getattr(e,'uncertain',False)) from None
