"""Local Montage Studio. Python 3.11+, FFmpeg. No cloud captions."""
import socket, math, sys
from platform_support import data_dir, open_folder, choose_path, prepare_tools
import base64, ctypes, hashlib, json, mimetypes, os, random, secrets, shutil, sqlite3, subprocess, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser
from pathlib import Path
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from contextlib import contextmanager
from browser_poster import BrowserPoster, BrowserFailure
from extension_bridge import ExtensionBridge
from beat_sync import detect_beats, fit_lengths, beat_timeline
import ai_upscaler

ROOT = Path(__file__).resolve().parent
prepare_tools(ROOT)
DATA = data_dir(ROOT)
DATA.mkdir(parents=True, exist_ok=True)
EXPORTS = DATA / 'exports'; EXPORTS.mkdir(exist_ok=True)
PORT = int(os.environ.get('MONTAGE_PORT', '8765'))
BASE = f'http://127.0.0.1:{PORT}'
CSRF = secrets.token_urlsafe(32)
LOCK = threading.RLock()
STOP = threading.Event()
THUMBNAIL_LOCK = threading.Lock()
DEFAULTS = dict(folders=[''], music='', clips=10, batch=1, format='landscape', ollama='http://127.0.0.1:11434', model='llama3.1:8b', notes='', clientId='', expectedAccount='',postingMode='extension',browserChannel='chrome',crossfade=False,fadeDuration=0.5,interpolate=False,includeStills=False,stillFolder="",stills=3,stillDuration=4.0,stillZoom="in",zoomAmount=10.0,pingPong=False,clipDuration=10.0,fixedClipLength=False,randomStart=True)
VIDEO = {'.mp4','.mov','.mkv','.webm','.avi','.m4v'}
IMAGE = {'.jpg','.jpeg','.png','.webp','.bmp'}
AUDIO = {'.wav','.mp3','.flac','.m4a','.ogg'}
MAX_CLIPS=5000
DEFAULTS['matchAudioLength']=False
DEFAULTS.update(beatSync=False,beatBpm=0.0)
DEFAULTS['matchFormatOnly']=False
DEFAULTS.update(smoothPingPong=False,pingPongEase=0.35)
DEFAULTS.update(aiUpscale=False,aiUpscaleModel='fast',resolution='auto')
FORMAT_RATIOS={'landscape':16/9,'portrait':9/16,'square':1.0}
FORMAT_LABELS={'landscape':'16:9','portrait':'9:16','square':'1:1'}

@contextmanager
def db():
    c = sqlite3.connect(DATA / 'studio.sqlite3', timeout=30); c.row_factory = sqlite3.Row
    try:
        with c: yield c
    finally: c.close()

def init():
    with db() as c:
        c.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clips(hash TEXT PRIMARY KEY,path TEXT NOT NULL,used INTEGER DEFAULT 0,reserved TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,error TEXT DEFAULT '',scheduled REAL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS clip_geometry(hash TEXT PRIMARY KEY,ratio REAL);
        ''')
        c.execute("UPDATE jobs SET status='error',error='Render interrupted. Retry to resume.' WHERE status='rendering'")
        c.execute("UPDATE jobs SET status='uncertain',error='Posting was interrupted. Check X before retrying.' WHERE status='posting'")
        c.execute("UPDATE jobs SET status='uncertain',error='Browser action was interrupted. Check X before doing anything again.' WHERE status='browser_posting'")
        c.execute("UPDATE jobs SET status='error',error='Upload interrupted before creating a post. Retry when ready.' WHERE status='uploading'")
        c.execute("UPDATE jobs SET status='paused',error='Scheduled job paused after restart. Resume explicitly.' WHERE status='scheduled'")
        c.execute("UPDATE jobs SET status='paused',error='Browser schedule was not submitted before restart. Schedule it again explicitly.' WHERE status='web_schedule_queued'")
        c.execute("UPDATE jobs SET status='uncertain',error='Extension operation interrupted. Check X before retrying.' WHERE status='extension_active'")
        c.execute("UPDATE jobs SET status='paused',error='Extension queue paused after restart. Queue explicitly again.' WHERE status='extension_queued'")
        if not c.execute("SELECT 1 FROM settings WHERE key='historyImported'").fetchone():
            c.execute('INSERT INTO settings VALUES(?,?)',('historyImported','true'))

def settings():
    with db() as c: values = {r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM settings')}
    return DEFAULTS | values

def save_settings(values):
    allowed = set(DEFAULTS)
    with db() as c:
        for k,v in values.items():
            if k in allowed: c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,json.dumps(v)))

def update(job_id, status=None, error=None, payload=None, scheduled=None):
    with LOCK, db() as c:
        row=c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row: raise ValueError('Montage not found')
        p=json.loads(row['payload']); p.update(payload or {})
        c.execute('UPDATE jobs SET status=?,error=?,payload=?,scheduled=? WHERE id=?', (status or row['status'], error if error is not None else row['error'],json.dumps(p),scheduled if scheduled is not None else row['scheduled'],job_id))

def claim(jid,status,allowed):
    with LOCK,db() as c:
        result=c.execute('UPDATE jobs SET status=? WHERE id=? AND status IN ('+','.join('?' for _ in allowed)+')',(status,jid,*allowed))
        return result.rowcount==1

def jobs():
    with LOCK,db() as c:
        return [dict(r) | {'payload':json.loads(r['payload'])} for r in c.execute('SELECT * FROM jobs ORDER BY created DESC')]

def get_job(job_id):
    return next((j for j in jobs() if j['id']==job_id), None)

def export_file(job):
    value=job['payload'].get('file') if job else None
    if not value: raise ValueError('This montage has no saved video yet')
    path=Path(value).resolve()
    if not path.is_relative_to(EXPORTS.resolve()) or not path.is_file() or path.suffix.lower()!='.mp4':
        raise ValueError('Saved video not found in the exports folder')
    return path

def export_thumbnail(job):
    source=export_file(job); stat=source.stat()
    key=hashlib.sha256(f'{source}|{stat.st_size}|{stat.st_mtime_ns}'.encode()).hexdigest()
    folder=DATA/'cache'/'thumbnails'; folder.mkdir(parents=True,exist_ok=True)
    target=folder/f'{key}.jpg'
    with THUMBNAIL_LOCK:
        if not target.is_file():
            partial=folder/f'{key}.partial.jpg'
            try:
                result=subprocess.run(['ffmpeg','-y','-v','error','-threads','1','-i',str(source),
                    '-frames:v','1','-vf','scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2',
                    '-threads','1','-q:v','3',str(partial)],capture_output=True,timeout=30)
                if result.returncode or not partial.is_file():raise ValueError('Thumbnail unavailable')
                partial.replace(target)
            finally:partial.unlink(missing_ok=True)
    return target

def open_exports_folder(job=None):
    open_folder(EXPORTS if job is None else export_file(job), reveal=job is not None)
    return {'ok':True}

def hash_file(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def display_ratio(stream):
    width=float(stream.get('width',0));height=float(stream.get('height',0))
    if not math.isfinite(width) or not math.isfinite(height) or width<=0 or height<=0:raise ValueError('Invalid video dimensions')
    sar=stream.get('sample_aspect_ratio','1:1')
    try:
        numerator,denominator=map(float,sar.split(':'));pixel_ratio=numerator/denominator
        if not math.isfinite(pixel_ratio) or pixel_ratio<=0:pixel_ratio=1
    except (ValueError,ZeroDivisionError,AttributeError):pixel_ratio=1
    ratio=width*pixel_ratio/height
    rotation=stream.get('tags',{}).get('rotate',0)
    for side in stream.get('side_data_list',[]):
        if 'rotation' in side:rotation=side['rotation'];break
    angle=float(rotation)
    if not math.isfinite(angle):raise ValueError('Invalid video rotation')
    if abs(abs(angle)%180-90)<1:ratio=1/ratio
    return ratio

def clip_display_ratio(path):
    try:
        result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_streams',
            '-of','json',str(path)],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:return None
        return display_ratio(json.loads(result.stdout)['streams'][0])
    except (OSError,ValueError,TypeError,KeyError,IndexError,subprocess.TimeoutExpired):return None

def filter_clip_ratios(found,format_name):
    if format_name not in FORMAT_RATIOS:raise ValueError('Choose landscape (16:9), portrait (9:16), or square (1:1)')
    if not shutil.which('ffprobe'):raise ValueError('Install FFprobe and add it to PATH to match video formats')
    with db() as c:cache={row['hash']:row['ratio'] for row in c.execute('SELECT hash,ratio FROM clip_geometry')}
    eligible={};unreadable=0;measured=[]
    for sha,path in found.items():
        ratio=cache.get(sha)
        if sha not in cache:ratio=clip_display_ratio(path);measured.append((sha,ratio))
        if ratio is None:unreadable+=1
        elif abs(ratio/FORMAT_RATIOS[format_name]-1)<=.02:eligible[sha]=path
    if measured:
        with db() as c:c.executemany('INSERT OR REPLACE INTO clip_geometry(hash,ratio) VALUES(?,?)',measured)
    return eligible,unreadable

def scan(folders, extensions=VIDEO, details=False,match_format=None):
    found={}
    for folder in folders:
        p=Path(folder).expanduser().resolve()
        if not p.is_dir(): raise ValueError(f'Folder does not exist: {p}')
        for f in p.rglob('*'):
            if f.is_file() and f.suffix.lower() in extensions:
                h=hash_file(f); found[h]=str(f)
    eligible,unreadable=filter_clip_ratios(found,match_format) if match_format is not None else (found,0)
    with LOCK, db() as c:
        for h,p in found.items():
            c.execute('INSERT INTO clips(hash,path) VALUES(?,?) ON CONFLICT(hash) DO UPDATE SET path=excluded.path',(h,p))
        available=[];used=0;reserved=0
        for h,p in eligible.items():
            r=c.execute('SELECT used,reserved FROM clips WHERE hash=?',(h,)).fetchone()
            if not r['used'] and not r['reserved']: available.append({'hash':h,'path':p})
            elif r['reserved']:reserved+=1
            else:used+=1
    if details:
        result={'total':len(eligible),'unused':len(available),'used':used,'reserved':reserved}
        if match_format is not None:result.update(poolTotal=len(found),formatRatio=FORMAT_LABELS[match_format],excludedRatio=len(found)-len(eligible)-unreadable,unreadable=unreadable)
        return result
    return available,len(eligible)

def reset_cycle(folders=None,match_format=None):
    active=('queued','rendering','uploading','posting','post_queued','browser_posting','web_schedule_queued','extension_queued','extension_active')
    with LOCK,db() as c:
        if BROWSER.state()['busy'] or c.execute('SELECT 1 FROM jobs WHERE status IN ('+','.join('?' for _ in active)+')',active).fetchone():
            raise ValueError('Wait for active renders and browser actions before resetting')
        failed=[dict(r) for r in c.execute("SELECT DISTINCT j.id,j.payload FROM jobs j JOIN clips c ON c.reserved=j.id WHERE j.status='error'")]
        released=c.execute("UPDATE clips SET reserved=NULL WHERE reserved IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM jobs j WHERE j.id=clips.reserved) OR reserved IN (SELECT id FROM jobs WHERE status IN ('error','cancelled','ready','posted','web_scheduled')))").rowcount
        for job in failed:
            p=json.loads(job['payload']);p['cycleReservationReleased']=True
            c.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(p),job['id']))
        reset=c.execute('UPDATE clips SET used=0 WHERE reserved IS NULL').rowcount
    result={'ok':True,'reset':reset,'released':released}
    try:result['scan']=scan(folders if folders is not None else settings()['folders'],details=True,match_format=match_format)
    except ValueError as error:result['scanError']=str(error)
    return result

def retry_job(jid):
    with LOCK,db() as c:
        row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row or row['status']!='error':raise ValueError('Job is no longer retryable. Refresh the library.')
        p=json.loads(row['payload'])
        if p.get('file'):
            c.execute("UPDATE jobs SET status='ready',error='' WHERE id=?",(jid,));return
        sequence=p.get('sequence',p.get('clips',[]))
        for clip in sequence:
            state=c.execute('SELECT used,reserved FROM clips WHERE hash=?',(clip['hash'],)).fetchone()
            if not state or state['used'] or state['reserved'] not in (None,jid):
                raise ValueError('This failed montage contains clips already used or reserved by another build. Build a new montage from unused clips instead.')
            if not Path(clip['path']).is_file():raise ValueError(f'Source file is missing: {Path(clip["path"]).name}')
        for clip in sequence:c.execute('UPDATE clips SET reserved=? WHERE hash=?',(jid,clip['hash']))
        p.pop('cycleReservationReleased',None)
        c.execute("UPDATE jobs SET status='queued',error='',payload=? WHERE id=?",(json.dumps(p),jid))

def disperse(clips, stills):
    buckets={}
    for i,still in enumerate(stills):
        position=((i+1)*len(clips))//(len(stills)+1)
        buckets.setdefault(position,[]).append(still)
    sequence=[]
    for i in range(len(clips)+1):
        sequence.extend(buckets.get(i,[]))
        if i<len(clips): sequence.append(clips[i])
    return sequence

def plan_excerpt(clip,duration,random_start=True):
    info=probe(clip['path'])
    video=next((s for s in info['streams'] if s['codec_type']=='video'),None)
    if not video:raise ValueError(f'No video stream in {Path(clip["path"]).name}')
    source_duration=float(video.get('duration') or info['format'].get('duration',0))
    if not math.isfinite(source_duration) or source_duration<=0:raise ValueError(f'Video duration could not be read: {Path(clip["path"]).name}')
    take=min(max(1,round(float(duration)*30))/30,source_duration)
    maximum=max(0,source_duration-take)
    start=random.SystemRandom().uniform(0,maximum) if random_start and maximum else 0
    return dict(clip,startSeconds=min(maximum,round(start,6)),takeDuration=take,sourceDuration=source_duration)

def music_tracks(source):
    if not str(source).strip(): raise ValueError('Choose a music file or folder')
    path=Path(source).expanduser().resolve()
    if path.is_file():
        if path.suffix.lower() not in AUDIO: raise ValueError('Choose a supported audio file: WAV, MP3, FLAC, M4A or OGG')
        return [path]
    if not path.is_dir(): raise ValueError(f'Music file or folder does not exist: {path}')
    tracks=[p for p in path.rglob('*') if p.is_file() and p.suffix.lower() in AUDIO]
    if not tracks: raise ValueError('No audio files found in the music folder')
    return tracks

def audio_duration(path):
    info=probe(path)
    audio=next((s for s in info['streams'] if s['codec_type']=='audio'),None)
    if not audio: raise ValueError(f'No audio stream in {Path(path).name}')
    duration=float(audio.get('duration') or info.get('format',{}).get('duration',0))
    if not math.isfinite(duration) or duration<=0: raise ValueError(f'Audio duration could not be read: {Path(path).name}')
    return duration

def fade_overlap(left,right,fade,fps):
    return max(0,int(min(float(fade),left/2,right/2)*fps)/fps)

def sequence_duration(lengths,options):
    fps=60 if options.get('interpolate') else 30
    return sum(lengths)-sum(fade_overlap(a,b,options.get('fadeDuration',.5),fps) for a,b in zip(lengths,lengths[1:])) if options.get('crossfade') else sum(lengths)

def plan_audio_clip(clip,options):
    if options.get('fixedClipLength') or options.get('pingPong'):
        planned=plan_excerpt(clip,options.get('clipDuration',10),bool(options.get('fixedClipLength') and options.get('randomStart',True)))
        length=options.get('clipDuration',10) if options.get('pingPong') else planned['takeDuration']
    else:
        info=probe(clip['path']); video=next((s for s in info['streams'] if s['codec_type']=='video'),None)
        if not video: raise ValueError(f'No video stream in {Path(clip["path"]).name}')
        length=float(video.get('duration') or info.get('format',{}).get('duration',0))
        if not math.isfinite(length) or length<=0: raise ValueError(f'Video duration could not be read: {Path(clip["path"]).name}')
        planned=dict(clip,sourceDuration=length)
    # Conservative 30 fps normalization estimate; final encoding measures actual coverage.
    frames=round(float(length)*30) if options.get('pingPong') else math.floor(float(length)*30+1e-6)
    return dict(planned,plannedDuration=max(1,frames)/30)

def prepare_timeline(payload,beats=None):
    fps=60 if payload.get('interpolate') else 30
    sequence=payload['sequence']
    caps=[round(payload['stillDuration']*fps)/fps if c.get('kind')=='still' else c['plannedDuration'] for c in sequence]
    track=payload.get('audioDuration') or audio_duration(payload['music'])
    target=track if payload.get('matchAudioLength') else min(track,sequence_duration(caps,payload))
    stills={i for i,c in enumerate(sequence) if c.get('kind')=='still'}
    lengths=fit_lengths(caps,target,fps,payload,sequence_duration,stills)
    overlaps=[fade_overlap(a,b,payload.get('fadeDuration',.5),fps) for a,b in zip(lengths,lengths[1:])] if payload.get('crossfade') else [0]*(len(lengths)-1)
    if payload.get('beatSync'):
        beats=beats or detect_beats(payload['music'],track,float(payload.get('beatBpm',0)))
        timeline=beat_timeline(caps,lengths,target,beats['times'],fps,payload.get('fadeDuration',.5) if payload.get('crossfade') else 0,flexible_end=not payload.get('matchAudioLength'))
        lengths=timeline['lengths'];overlaps=timeline['overlaps']
        if not payload.get('matchAudioLength'):target=timeline['duration']
        payload.update(beatCuts=timeline['cuts'],detectedBpm=beats['bpm'],beatSource=beats['source'])
    for clip,length in zip(sequence,lengths):clip['renderDuration']=length
    payload.update(timelineDuration=target,timelineOverlaps=overlaps)
    return payload

def reserve_audio_montages(options,available,tracks,images,still_count,still_duration,zoom,zoom_amount):
    plans=[];cursor=0;batch=int(options.get('batch',1));fps=60 if options.get('interpolate') else 30
    while cursor<len(available) and (batch==0 or len(plans)<batch):
        music=random.choice(tracks);target=audio_duration(music)
        selected=images[len(plans)*still_count:(len(plans)+1)*still_count]
        if len(selected)<still_count: raise ValueError(f'Not enough unused still images for this full-track batch; need {still_count} per montage')
        selected=[dict(image,kind='still',zoom=random.choice(('in','out')) if zoom=='random' else zoom) for image in selected]
        chosen=[];coverage=0;count=min(max(1,int(options.get('clips',10))),len(available)-cursor)
        beats=detect_beats(music,target,float(options.get('beatBpm',0))) if options.get('beatSync') else None
        payload=None;timeline_error=None
        for clip in available[cursor:]:
            chosen.append(plan_audio_clip(clip,options))
            if len(chosen)<count:continue
            sequence=disperse(chosen,selected)
            lengths=[round(still_duration*fps)/fps if c.get('kind')=='still' else c['plannedDuration'] for c in sequence]
            coverage=sequence_duration(lengths,options)
            if coverage+1e-6>=target and len(chosen)>=count:
                payload=dict(clips=chosen,music=str(music),format=options.get('format','landscape'),caption='',madeWithAI=True,notes=options.get('notes',''),crossfade=bool(options.get('crossfade')),fadeDuration=float(options.get('fadeDuration',.5)),interpolate=bool(options.get('interpolate')),stills=selected,sequence=sequence,stillDuration=still_duration,zoomAmount=zoom_amount,pingPong=bool(options.get('pingPong')),clipDuration=float(options.get('clipDuration',10)),fixedClipLength=bool(options.get('fixedClipLength')),randomStart=bool(options.get('randomStart',True)),matchAudioLength=True,audioDuration=target,beatSync=bool(options.get('beatSync')),beatBpm=float(options.get('beatBpm',0)),requestedClips=int(options.get('clips',10)))
                payload.update(aiUpscale=bool(options.get('aiUpscale')),aiUpscaleModel=options.get('aiUpscaleModel','fast'),resolution=options.get('resolution','auto'),matchFormatOnly=bool(options.get('matchFormatOnly')),smoothPingPong=bool(options.get('smoothPingPong')),pingPongEase=float(options.get('pingPongEase',.35)))
                try:prepare_timeline(payload,beats)
                except ValueError as error:
                    timeline_error=error;payload=None
                    if not options.get('beatSync'):raise
                    if 'Not enough beats' in str(error) or 'Too many clips' in str(error):break
                else:break
        if payload is None:
            if plans: break
            if timeline_error:raise timeline_error
            raise ValueError(f'Not enough unused footage to match the audio track ({target:.1f}s). Available montage length after transitions: {coverage:.1f}s. Add unused clips, increase clip/still duration, enable ping-pong, or turn off Match audio track length.')
        cursor+=len(chosen)
        plans.append((secrets.token_hex(6),payload))
    # Commit only complete planned montages; short batch remainders stay unused.
    with db() as c:
        for jid,payload in plans:
            c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',(jid,'queued',json.dumps(payload),time.time()))
            for clip in payload['sequence']: c.execute('UPDATE clips SET reserved=? WHERE hash=?',(jid,clip['hash']))
    return [jid for jid,_ in plans]

def reserve(options):
    ai_upscaler.output_size(options.get('format','landscape'),options.get('resolution','auto'))
    if options.get('aiUpscaleModel','fast') not in ai_upscaler.MODELS:raise ValueError('Choose the fast or general AI model')
    if options.get('aiUpscale') and not ai_upscaler.available(options.get('aiUpscaleModel','fast')):raise ValueError('AI upscaler is missing. Use the complete Montage Studio desktop package.')
    ease=float(options.get('pingPongEase',.35))
    if not math.isfinite(ease) or not .05<=ease<=2:raise ValueError('Ping-pong deceleration time must be between 0.05 and 2 seconds')
    bpm=float(options.get('beatBpm',0))
    if not math.isfinite(bpm) or (bpm!=0 and not 40<=bpm<=240):raise ValueError('Beat BPM must be 40–240, or 0 for automatic detection')
    clip_duration=float(options.get('clipDuration',10))
    if not 0.5<=clip_duration<=120:raise ValueError('Clip duration must be between 0.5 and 120 seconds')
    fade=float(options.get('fadeDuration',0.5))
    if not 0.1<=fade<=3: raise ValueError('Crossfade duration must be between 0.1 and 3 seconds')
    count=int(options.get('clips',10))
    if not 1<=count<=MAX_CLIPS:raise ValueError(f'Clips per montage must be between 1 and {MAX_CLIPS}')
    batch=int(options.get('batch',1))
    if batch<0 or batch>100: raise ValueError('Batch must be 1–100, or 0 for all remaining clips')
    still_count=int(options.get('stills',3)) if options.get('includeStills') else 0
    still_duration=float(options.get('stillDuration',4))
    zoom_amount=float(options.get('zoomAmount',10))
    zoom=options.get('stillZoom','in')
    if not 0<=still_count<=100: raise ValueError('Still count must be between 0 and 100')
    if not 0.5<=still_duration<=60: raise ValueError('Still duration must be between 0.5 and 60 seconds')
    if not 0<=zoom_amount<=50 or zoom not in ('none','in','out','random'): raise ValueError('Invalid still zoom settings')
    with LOCK:
        match_format=options.get('format','landscape') if options.get('matchFormatOnly') else None
        available,total=scan(options['folders'],match_format=match_format)
        if total==0 and match_format is not None:raise ValueError(f'No matching {FORMAT_LABELS[match_format]} video clips found. The filter allows a 2% ratio tolerance and skips unreadable files. Add matching clips or turn off Only use clips matching the selected format.')
        if total == 0: raise ValueError('No video files found in the selected video folders. Choose a folder containing MP4, MOV, MKV, WEBM, AVI or M4V files. Reset cycle cannot add missing videos.')
        if not available: raise ValueError('No unused matching clips in these folders. Add matching clips or explicitly reset the cycle in Settings.' if match_format is not None else 'No unused clips in these folders. Add new clips or explicitly reset the cycle in Settings.')
        random.SystemRandom().shuffle(available)
        tracks=music_tracks(options['music'])
        limit=len(available) if batch==0 else min(len(available),batch*count)
        images=[]
        if still_count:
            images,_=scan([options['stillFolder']] if options.get('stillFolder','').strip() else options['folders'],IMAGE)
            needed=((limit+count-1)//count)*still_count
            if not options.get('matchAudioLength') and len(images)<needed: raise ValueError(f'Need {needed} unused still images for this batch; found {len(images)}. Reduce the still count or batch size, or add images.')
            random.SystemRandom().shuffle(images)
        if options.get('matchAudioLength'):
            return reserve_audio_montages(options,available,tracks,images,still_count,still_duration,zoom,zoom_amount),total
        ids=[]
        with db() as c:
            for start in range(0,limit,count):
                chosen=available[start:min(start+count,limit)]
                if options.get('beatSync'):
                    chosen=[plan_audio_clip(clip,options) for clip in chosen]
                elif options.get('fixedClipLength') or options.get('pingPong'):
                    chosen=[plan_excerpt(clip,clip_duration,bool(options.get('fixedClipLength') and options.get('randomStart',True))) for clip in chosen]
                jid=secrets.token_hex(6); ids.append(jid)
                p=dict(clips=chosen,music=str(random.choice(tracks)),format=options.get('format','landscape'),matchFormatOnly=bool(options.get('matchFormatOnly')),caption='',madeWithAI=True,notes=options.get('notes',''),crossfade=bool(options.get('crossfade',False)),fadeDuration=fade,interpolate=bool(options.get('interpolate',False)))
                selected=images[(len(ids)-1)*still_count:len(ids)*still_count]
                selected=[dict(image,kind='still',zoom=random.choice(('in','out')) if zoom=='random' else zoom) for image in selected]
                p.update(aiUpscale=bool(options.get('aiUpscale')),aiUpscaleModel=options.get('aiUpscaleModel','fast'),resolution=options.get('resolution','auto'),smoothPingPong=bool(options.get('smoothPingPong')),pingPongEase=ease,stills=selected,sequence=disperse(chosen,selected),stillDuration=still_duration,zoomAmount=zoom_amount,pingPong=bool(options.get('pingPong',False)),clipDuration=clip_duration,fixedClipLength=bool(options.get('fixedClipLength',False)),randomStart=bool(options.get('randomStart',True)))
                if options.get('beatSync'):
                    p.update(beatSync=True,beatBpm=bpm,requestedClips=count)
                    prepare_timeline(p)
                c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',(jid,'queued',json.dumps(p),time.time()))
                for clip in chosen+selected: c.execute('UPDATE clips SET reserved=? WHERE hash=?',(jid,clip['hash']))
        return ids,total

def run(args):
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: raise RuntimeError(result.stderr.decode('utf-8',errors='replace')[-1800:])
    return result.stdout

def probe(path):
    return json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)]))

def eased_ping_leg(part,frames,ease_seconds,tmp):
    """Keep leg duration; use sine velocity ramps with zero speed at each turn."""
    length=(frames-1)/30;window=min(float(ease_seconds),length/2)
    ramp=2*window/math.pi;distance=length-2*window+2*ramp
    x=f'(N*{distance/(frames-1):.12f})'
    start=f'{ramp:.12f}*acos(max(-1,min(1,1-{x}/{ramp:.12f})))'
    end=f'{length:.12f}-{ramp:.12f}*acos(max(-1,min(1,1-({distance:.12f}-{x})/{ramp:.12f})))'
    timing=f'if(lt({x},{ramp:.12f}),{start},if(gt({x},{distance-ramp:.12f}),{end},{window:.12f}+{x}-{ramp:.12f}))'
    target=tmp/(part.stem+f'-ease-{ease_seconds:.3f}.mp4')
    if not target.exists():
        staged=target.with_name(target.stem+'.partial.mp4')
        vf=f"settb=AVTB,setpts='{timing}/TB',tpad=stop_mode=clone:stop_duration=0.2,minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,trim=end_frame={frames},setpts=N/(30*TB)"
        run(['ffmpeg','-y','-v','error','-i',str(part),'-an','-vf',vf,'-frames:v',str(frames),'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)])
        count=int(next(s for s in probe(staged)['streams'] if s['codec_type']=='video')['nb_frames'])
        if count!=frames:raise ValueError('Eased ping-pong did not preserve the clip frame count')
        staged.replace(target)
    return target

def ping_pong(part,duration,tmp,ease_seconds=0):
    """Repeat a forward/backward cycle without duplicated turning-point frames."""
    target_frames=max(1,round(float(duration)*30))
    video=next(s for s in probe(part)['streams'] if s['codec_type']=='video')
    frames=int(video['nb_frames'])
    if frames<1:raise ValueError('Clip contains no video frames')
    easing=ease_seconds>0 and frames>2 and target_frames>frames
    if easing:part=eased_ping_leg(part,frames,ease_seconds,tmp)
    target=tmp/(part.stem+'-pingpong.mp4')
    if target.exists():return target
    cycle=part
    if frames>2 and target_frames>frames:
        backward=tmp/(part.stem+'-backward.mp4')
        if not backward.exists():
            staged=backward.with_name(backward.stem+'.partial.mp4')
            run(['ffmpeg','-y','-v','error','-i',str(part),'-an','-vf',f'reverse,trim=start_frame=1:end_frame={frames-1},setpts=PTS-STARTPTS','-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)])
            staged.replace(backward)
        listing=tmp/(part.stem+'-cycle.txt')
        listing.write_text('\n'.join("file '"+f.as_posix().replace("'","'\\''")+"'" for f in (part,backward)),encoding='utf-8')
        cycle=tmp/(part.stem+'-cycle.mp4')
        if not cycle.exists():
            staged=cycle.with_name(cycle.stem+'.partial.mp4')
            run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(listing),'-an','-c:v','copy',str(staged)])
            staged.replace(cycle)
    staged=target.with_name(target.stem+'.partial.mp4')
    run(['ffmpeg','-y','-v','error','-stream_loop','-1','-i',str(cycle),'-an','-frames:v',str(target_frames),'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)])
    staged.replace(target)
    return target

def crossfade_parts(parts,lengths,overlaps,tmp,progress=None):
    """Bound open inputs/command size; preserve overlaps at every group boundary."""
    level=0;group_size=16
    while len(parts)>1:
        next_parts=[];next_lengths=[];next_overlaps=[]
        for start in range(0,len(parts),group_size):
            if STOP.is_set():raise RuntimeError('Render stopped. Retry to resume.')
            if progress:progress(f'Rendering crossfades: pass {level+1}, group {start//group_size+1} of {math.ceil(len(parts)/group_size)}')
            group=parts[start:start+group_size];times=lengths[start:start+group_size]
            fades=overlaps[start:start+len(group)-1]
            if len(group)==1:
                video=group[0];duration=times[0]
            else:
                args=['ffmpeg','-y','-v','error','-filter_complex_threads','1']
                for part in group:args+=['-threads','2','-i',str(part)]
                graph=[f'[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[v{i}]' for i in range(len(group))]
                timeline=times[0];previous='v0'
                for i in range(1,len(group)):
                    fade=fades[i-1]
                    if fade:graph.append(f'[{previous}][v{i}]xfade=transition=fade:duration={fade:.9f}:offset={timeline-fade:.9f}[mix{i}]')
                    else:graph.append(f'[{previous}][v{i}]concat=n=2:v=1:a=0[mix{i}]')
                    timeline+=times[i]-fade;previous=f'mix{i}'
                video=tmp/f'crossfade-{level}-{start//group_size}.mp4'
                staged=video.with_name(video.stem+'.partial.mp4')
                run(args+['-filter_complex',';'.join(graph),'-map',f'[{previous}]','-an','-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)])
                staged.replace(video);duration=float(probe(video)['format']['duration'])
            next_parts.append(video);next_lengths.append(duration)
            if start+len(group)<len(parts):next_overlaps.append(overlaps[start+len(group)-1])
        parts,lengths,overlaps=next_parts,next_lengths,next_overlaps;level+=1
    return parts[0],lengths[0]

def render(job):
    jid=job['id']; p=job['payload']; tmp=DATA/'cache'/jid; tmp.mkdir(parents=True,exist_ok=True)
    if not claim(jid,'rendering',('queued',)): return
    update(jid,'rendering',error='',payload={'progress':'Preparing clips'})
    w,h=ai_upscaler.output_size(p['format'],p.get('resolution','auto')); parts=[]
    ai_results=[]
    output_fps=60 if p.get('interpolate',False) else 30
    sequence=p.get('sequence',p['clips'])
    target_audio_duration=(p.get('audioDuration') or audio_duration(p['music'])) if p.get('matchAudioLength') else None
    planned_timeline=bool(p.get('timelineDuration'))
    lengths=[]
    for i,clip in enumerate(sequence):
        if STOP.is_set(): return
        part=tmp/f'{i:03}.mp4'
        update(jid,payload={'progress':f'Encoding clip {i+1} of {len(sequence)}'})
        if not part.exists():
            staged=tmp/f'{i:03}.partial.mp4'
            if clip.get('kind')=='still':
                frames=round(float(clip.get('renderDuration',p.get('stillDuration',4)))*output_fps)
                amount=float(p.get('zoomAmount',10))/100
                mode=clip.get('zoom','in')
                zoom='1' if mode=='none' else (f'1+{amount}*on/{max(1,frames-1)}' if mode=='in' else f'1+{amount}-{amount}*on/{max(1,frames-1)}')
                vf=f"scale={w*2}:{h*2}:force_original_aspect_ratio=decrease,pad={w*2}:{h*2}:(ow-iw)/2:(oh-ih)/2,zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={w}x{h}:fps={output_fps},setsar=1"
                run(['ffmpeg','-y','-v','error','-i',clip['path'],'-an','-vf',vf,'-frames:v',str(frames),'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)])
            else:
                seek=['-ss',format(float(clip.get('startSeconds',0)),'.6f')] if clip.get('startSeconds',0)>0 else []
                seconds=clip.get('renderDuration',clip.get('takeDuration',p.get('clipDuration',10) if p.get('fixedClipLength') or p.get('pingPong') else None))
                base_frames=max(1,math.ceil(float(seconds)*30-1e-7) if planned_timeline else round(float(seconds)*30)) if seconds is not None else None
                limit=['-t',str(base_frames/30)] if seconds is not None else []
                enhanced=None
                if p.get('aiUpscale'):
                    stream=next(s for s in probe(clip['path'])['streams'] if s['codec_type']=='video')
                    enhanced,result=ai_upscaler.upscale(clip['path'],tmp/f'ai-{i:03}',(w,h),stream,
                        seek=clip.get('startSeconds',0),seconds=base_frames/30 if base_frames else None,
                        model=p.get('aiUpscaleModel','fast'),progress=lambda message:update(jid,payload={'progress':f'Clip {i+1} · {message}'}),stop=STOP)
                    ai_results.append(dict(clip=i+1,**result))
                if enhanced:shutil.copyfile(enhanced,staged)
                else:run(['ffmpeg','-y','-v','error',*seek,'-i',clip['path'],*limit,'-an','-vf',f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS-STARTPTS,fps=30','-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)])
            staged.replace(part)
        elif p.get('aiUpscale') and clip.get('kind')!='still':
            manifest=tmp/f'ai-{i:03}'/'complete.json'
            if manifest.is_file():ai_results.append(dict(clip=i+1,**json.loads(manifest.read_text())))
        if p.get('pingPong',False) and clip.get('kind')!='still':
            update(jid,payload={'progress':f'Ping-pong clip {i+1} to {p["clipDuration"]} seconds'})
            ping_duration=clip.get('renderDuration',p['clipDuration'])
            if planned_timeline:ping_duration=math.ceil(ping_duration*30-1e-7)/30
            part=ping_pong(part,ping_duration,tmp,float(p.get('pingPongEase',.35)) if p.get('smoothPingPong') else 0)
        if p.get('interpolate',False) and clip.get('kind')!='still':
            update(jid,payload={'progress':f'Interpolating clip {i+1} of {len(sequence)} to 60 fps'})
            smooth=tmp/(part.stem+'-60fps.mp4')
            if not smooth.exists():
                clip_duration=clip.get('renderDuration',float(probe(part)['format']['duration']))
                staged=smooth.with_name(smooth.stem+'.partial.mp4')
                run(['ffmpeg','-y','-v','error','-i',str(part),'-an','-vf',f'tpad=stop_mode=clone:stop_duration=0.2,minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,trim=duration={clip_duration},setpts=PTS-STARTPTS','-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)])
                staged.replace(smooth)
            part=smooth
        if planned_timeline:
            exact_frames=round(clip['renderDuration']*output_fps)
            actual=probe(part)
            video_stream=next(s for s in actual['streams'] if s['codec_type']=='video')
            if int(video_stream.get('nb_frames',0))!=exact_frames:
                trimmed=tmp/(part.stem+'-timed.mp4')
                if not trimmed.exists():
                    staged=trimmed.with_name(trimmed.stem+'.partial.mp4')
                    run(['ffmpeg','-y','-v','error','-i',str(part),'-an','-frames:v',str(exact_frames),'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)])
                    staged.replace(trimmed)
                part=trimmed
        parts.append(part)
        if target_audio_duration is not None:
            lengths.append(float(probe(part)['format']['duration']))
            if not planned_timeline and sequence_duration(lengths,p)+1e-6>=target_audio_duration: break
    listing=tmp/'concat.txt'; listing.write_text('\n'.join("file '"+x.as_posix().replace("'","'\\''")+"'" for x in parts),encoding='utf-8')
    if target_audio_duration is None: lengths=[float(probe(x)['format']['duration']) for x in parts]
    duration=sum(lengths)
    overlaps=[]
    video=listing
    if p.get('crossfade') and len(parts)>1:
        update(jid,payload={'progress':'Rendering smooth crossfades'})
        overlaps=p['timelineOverlaps'] if planned_timeline else [fade_overlap(a,b,p.get('fadeDuration',.5),output_fps) for a,b in zip(lengths,lengths[1:])]
        video,duration=crossfade_parts(parts,lengths,overlaps,tmp,lambda message:update(jid,payload={'progress':message}))
    if target_audio_duration is not None:
        if duration+1/output_fps+1e-6<target_audio_duration: raise ValueError('Encoded footage is shorter than the selected audio. Add unused clips and build again; the reserved clips have not been consumed.')
        duration=target_audio_duration
    elif planned_timeline:duration=p['timelineDuration']
    target=EXPORTS/f'montage-{jid}.mp4'; partial=tmp/'final.mp4'
    update(jid,payload={'progress':'Adding music and verifying video'})
    video_input=['-f','concat','-safe','0','-i',str(listing)] if video==listing else ['-i',str(video)]
    audio_loop=[] if target_audio_duration is not None or planned_timeline else ['-stream_loop','-1']
    # Stream-copy cuts can retain reordered frames beyond the requested end.
    video_codec=['-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4'] if target_audio_duration is not None or planned_timeline else ['-c:v','copy']
    run(['ffmpeg','-y','-v','error',*video_input,*audio_loop,'-i',p['music'],'-map','0:v:0','-map','1:a:0',*video_codec,'-c:a','aac','-b:a','192k','-af',f'afade=t=in:d=1,afade=t=out:st={max(0,duration-2)}:d=2','-t',str(duration),'-movflags','+faststart',str(partial)])
    run(['ffmpeg','-v','error','-i',str(partial),'-f','null','-'])
    if target_audio_duration is not None:
        info=probe(partial);duration=float(info['format']['duration'])
        if abs(duration-target_audio_duration)>1/output_fps+.03: raise ValueError('Output length did not match the audio track. Clips remain reserved for retry.')
    partial.replace(target)
    used_sequence=sequence[:len(parts)]
    with db() as c:
        c.execute('UPDATE clips SET reserved=NULL WHERE reserved=?',(jid,))
        for clip in used_sequence: c.execute('UPDATE clips SET used=1,reserved=NULL WHERE hash=?',(clip['hash'],))
    used_payload=dict(sequence=used_sequence,clips=[c for c in used_sequence if c.get('kind')!='still'],stills=[c for c in used_sequence if c.get('kind')=='still']) if target_audio_duration is not None else {}
    update(jid,'ready',error='',payload={'progress':'Ready to share','duration':duration,'file':str(target),'actualCrossfades':overlaps,'frameRate':output_fps,'width':w,'height':h,'aiUpscaleResults':ai_results,**used_payload})
    (EXPORTS/f'montage-{jid}.json').write_text(json.dumps(get_job(jid),indent=2),encoding='utf-8')

class ApiError(RuntimeError):
    def __init__(self,code,detail):
        self.code=code
        super().__init__(f'HTTP {code}: {detail}')

def http_json(url, data=None, headers=None, method=None, timeout=60):
    headers=dict(headers or {})
    body=None
    if data is not None:
        if isinstance(data,bytes): body=data
        else: body=json.dumps(data).encode(); headers.setdefault('Content-Type','application/json')
    req=urllib.request.Request(url,body,headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail=e.read(4096).decode(errors='replace')
        raise ApiError(e.code,detail) from None

def ollama_url():
    url=settings()['ollama'].rstrip('/')
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='http' or parsed.hostname not in ('localhost','127.0.0.1','::1'): raise ValueError('Ollama must use a local http://localhost address')
    return url

def caption(jid, model, notes, vision):
    job=get_job(jid)
    if not job or job['status'] not in ('ready','paused','scheduled'): raise ValueError('Select a rendered montage first')
    model_info=http_json(ollama_url()+'/api/show',{'model':model})
    content='Write a concise accurate social post for this artist video montage. Use only the supplied information. Do not invent people, places, events, or tag accounts. Return JSON with description (one brief sentence) and hashtags (array of 3–5 specific hashtags). Total description plus hashtags should fit 280 characters. Creative notes: '+notes+'\nMusic: '+Path(job['payload']['music']).stem+'\nClips: '+', '.join(Path(x['path']).stem for x in job['payload']['clips'])
    msg={'role':'user','content':content}
    mode='Creative notes and filenames (text only)'
    if vision:
        if 'vision' not in model_info.get('capabilities',[]): raise ValueError('This model has no vision capability. Turn off frame input or choose a vision model.')
        images=[]; dur=job['payload']['duration']
        for frac in (.05,.5,.95):
            jpg=run(['ffmpeg','-v','error','-ss',str(dur*frac),'-i',job['payload']['file'],'-frames:v','1','-vf','scale=512:-2','-f','image2pipe','-c:v','mjpeg','-'])
            images.append(base64.b64encode(jpg).decode())
        msg['images']=images; mode='Three sampled frames and creative notes'
    body={'model':model,'messages':[msg],'stream':False,'format':'json','options':{'temperature':.6,'num_predict':400,'num_ctx':4096}}
    if 'thinking' in model_info.get('capabilities',[]): body['think']=False
    result=http_json(ollama_url()+'/api/chat',body,timeout=300)
    parsed=json.loads(result['message']['content'])
    desc=str(parsed.get('description','')).strip(); tags=parsed.get('hashtags',[])
    if isinstance(tags,str): tags=tags.replace(',',' ').split()
    tags=['#'+''.join(ch for ch in str(t).lstrip('#') if ch.isalnum() or ch=='_') for t in tags[:5]]
    text=(desc+'\n\n'+' '.join(t for t in tags if len(t)>1)).strip()
    update(jid,payload={'caption':text,'captionMode':mode,'captionModel':model})
    return {'description':desc,'hashtags':' '.join(tags),'caption':text,'mode':mode}

# User-scoped DPAPI keeps OAuth secrets off disk in plaintext.
class Blob(ctypes.Structure): _fields_=[('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]
def crypt(data,decrypt=False):
    if os.name!='nt': raise RuntimeError('Credential storage requires Windows DPAPI')
    buf=ctypes.create_string_buffer(data); src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte))); out=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(out)): raise ctypes.WinError()
    try: return ctypes.string_at(out.data,out.size)
    finally: ctypes.windll.kernel32.LocalFree(out.data)

def credentials():
    if os.name!='nt':
        import keyring
        try: value=keyring.get_password('Montage Studio',hashlib.sha256(str(DATA).encode()).hexdigest())
        except keyring.errors.NoKeyringError: return {}
        return json.loads(value) if value else {}
    p=DATA/'credentials.dpapi'
    return json.loads(crypt(p.read_bytes(),True)) if p.exists() else {}
def store_credentials(value):
    if os.name!='nt':
        import keyring
        try: keyring.set_password('Montage Studio',hashlib.sha256(str(DATA).encode()).hexdigest(),json.dumps(value))
        except keyring.errors.KeyringError as error: raise ValueError('Unlock your system keychain or use the browser extension.') from error
        return
    p=DATA/'credentials.dpapi'; temp=p.with_suffix('.tmp'); temp.write_bytes(crypt(json.dumps(value).encode())); temp.replace(p)

OAUTH={}
def oauth_start():
    s=settings()
    if not s['clientId']: raise ValueError('Enter your X developer Client ID in Settings first')
    state=secrets.token_urlsafe(24); verifier=secrets.token_urlsafe(48)
    OAUTH.clear(); OAUTH.update(state=state,verifier=verifier,started=time.time())
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return 'https://x.com/i/oauth2/authorize?'+urllib.parse.urlencode(dict(response_type='code',client_id=s['clientId'],redirect_uri=BASE+'/oauth/callback',scope='tweet.read tweet.write users.read media.write offline.access',state=state,code_challenge=challenge,code_challenge_method='S256'))

def exchange(fields):
    s=settings(); c=credentials(); headers={'Content-Type':'application/x-www-form-urlencoded'}
    if c.get('clientSecret'): headers['Authorization']='Basic '+base64.b64encode((s['clientId']+':'+c['clientSecret']).encode()).decode()
    else: fields['client_id']=s['clientId']
    token=http_json('https://api.x.com/2/oauth2/token',urllib.parse.urlencode(fields).encode(),headers,timeout=30)
    c.update(token); c['expires_at']=time.time()+token.get('expires_in',7200); store_credentials(c)
    return c

def bearer():
    c=credentials()
    if not c.get('access_token'): raise ValueError('Connect X in Settings first')
    if c.get('expires_at',0)<time.time()+90:
        if not c.get('refresh_token'): raise ValueError('X login expired. Reconnect in Settings.')
        c=exchange({'grant_type':'refresh_token','refresh_token':c['refresh_token']})
    return c['access_token']

def x_request(path,data=None,method=None):
    return http_json('https://api.x.com'+path,data,{'Authorization':'Bearer '+bearer()},method,timeout=60)

def oauth_callback(query):
    if not OAUTH or time.time()-OAUTH['started']>600 or not secrets.compare_digest(query.get('state',[''])[0],OAUTH['state']): raise ValueError('Expired or invalid login state')
    code=query.get('code',[''])[0]; verifier=OAUTH['verifier']; OAUTH.clear()
    if not code: raise ValueError('X login cancelled')
    c=exchange({'grant_type':'authorization_code','code':code,'redirect_uri':BASE+'/oauth/callback','code_verifier':verifier})
    user=x_request('/2/users/me')['data']; c['user']=user; store_credentials(c)

def upload(path):
    media=x_request('/2/media/upload/initialize',{'media_type':'video/mp4','total_bytes':Path(path).stat().st_size,'media_category':'tweet_video'})['data']['id']
    with open(path,'rb') as f:
        index=0
        for chunk in iter(lambda:f.read(4*1024*1024),b''):
            boundary='Montage'+secrets.token_hex(16)
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="segment_index"\r\n\r\n{index}\r\n--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="chunk.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+chunk+f'\r\n--{boundary}--\r\n'.encode()
            http_json('https://api.x.com/2/media/upload/'+str(media)+'/append',body,{'Authorization':'Bearer '+bearer(),'Content-Type':'multipart/form-data; boundary='+boundary}); index+=1
    result=x_request('/2/media/upload/'+str(media)+'/finalize',b'',method='POST')
    deadline=time.time()+600
    while result.get('data',{}).get('processing_info',{}).get('state') not in (None,'succeeded'):
        info=result['data']['processing_info']
        if info['state']=='failed': raise RuntimeError('X rejected media: '+json.dumps(info))
        if time.time()>deadline: raise RuntimeError('X media processing timed out; no post was created')
        time.sleep(min(30,max(1,info.get('check_after_secs',3))))
        result=x_request('/2/media/upload?'+urllib.parse.urlencode({'command':'STATUS','media_id':media}))
    return str(media)

def publish(job):
    jid=job['id']; p=job['payload']; attempted=False
    if not claim(jid,'uploading',('scheduled','post_queued','ready')): return
    p=get_job(jid)['payload']
    try:
        user=x_request('/2/users/me')['data']
        expected=settings()['expectedAccount'].strip().lstrip('@')
        if expected and user['username'].lower()!=expected.lower(): raise ValueError(f'Connected account is @{user["username"]}. Change expected account in Settings or reconnect.')
        update(jid,'uploading',error='')
        media=upload(p['file'])
        update(jid,'posting',payload={'progress':'Creating X post'})
        attempted=True
        result=x_request('/2/tweets',{'text':p['caption'],'media':{'media_ids':[media]},'made_with_ai':bool(p.get('madeWithAI',True))})
        post_id=result['data']['id']; update(jid,'posted',payload={'postId':post_id,'postUrl':f'https://x.com/{user["username"]}/status/{post_id}','progress':'Posted'})
    except Exception as e:
        uncertain=attempted and not (isinstance(e,ApiError) and 400<=e.code<500)
        update(jid,'uncertain' if uncertain else 'error',error=('Check your X account before trying again. ' if uncertain else '')+str(e))

def worker():
    while not STOP.wait(1):
        for j in reversed(jobs()):
            if j['status']=='queued':
                try: render(j)
                except Exception as e: update(j['id'],'error',error=str(e))
                break
            if j['status']=='post_queued' or (j['status']=='scheduled' and j['scheduled']<=time.time()):
                if j['payload'].get('postingMode')=='browser': publish_browser(j)
                else: publish(j)
                break
            if j['status']=='web_schedule_queued':
                publish_browser(j,True); break

def publish_browser(job,scheduled=False):
    jid=job['id']
    if not claim(jid,'browser_posting',('post_queued','web_schedule_queued')):return
    try:
        result=BROWSER.call('schedule' if scheduled else 'post',get_job(jid))
        update(jid,'web_scheduled' if scheduled else 'posted',error='',payload=result)
    except Exception as e:
        uncertain=getattr(e,'uncertain',False) or isinstance(e,TimeoutError)
        update(jid,'uncertain' if uncertain else 'error',error=('Check X before trying again. ' if uncertain else '')+str(e))

def re_extension_origin(origin):
    import re
    return bool(re.fullmatch(r'chrome-extension://[a-p]{32}',origin))

BROWSER=BrowserPoster(DATA,settings)
EXTENSION=ExtensionBridge(DATA,settings,jobs,get_job,update,claim,LOCK)

def validate_caption(p):
    text=str(p.get('caption','')).strip()
    # X uses weighted Unicode length; final API validation remains authoritative.
    weighted=sum(1 if ord(c)<=0x10ff or 0x2000<=ord(c)<=0x200d or 0x2010<=ord(c)<=0x201f or 0x2032<=ord(c)<=0x2037 else 2 for c in text)
    if weighted>280: raise ValueError('Caption exceeds the 280-character conservative limit')
    return text

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,value,status=200):
        b=json.dumps(value).encode(); self.send_response(status);
        if self.path.startswith('/api/extension/') and re_extension_origin(self.headers.get('Origin','')):self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self):
        origin=self.headers.get('Origin','')
        if not self.safe_host() or not self.path.startswith('/api/extension/') or not re_extension_origin(origin):return self.send({'error':'Invalid extension origin'},403)
        self.send_response(204);self.send_header('Access-Control-Allow-Origin',origin);self.send_header('Access-Control-Allow-Methods','POST, OPTIONS');self.send_header('Access-Control-Allow-Headers','Content-Type, X-Studio-Extension, X-Studio-Extension-Id');self.end_headers()
    def safe_host(self): return self.headers.get('Host','') in (f'127.0.0.1:{PORT}',f'localhost:{PORT}')
    def do_GET(self):
        if not self.safe_host(): return self.send({'error':'Invalid host'},403)
        u=urllib.parse.urlparse(self.path)
        try:
            if u.path=='/api/state':
                c=credentials(); return self.send({'app':'Montage Studio','dataPath':str(DATA),'exportsPath':str(EXPORTS),'settings':settings(),'jobs':jobs(),'csrf':CSRF,'xUser':c.get('user'),'browser':BROWSER.state(),'extension':EXTENSION.state(),'callback':BASE+'/oauth/callback','tools':{'ffmpeg':bool(shutil.which('ffmpeg')),'aiUpscaler':ai_upscaler.available(),'aiModels':{model:ai_upscaler.available(model) for model in ai_upscaler.MODELS}}})
            if u.path=='/api/models': return self.send(http_json(ollama_url()+'/api/tags',timeout=8))
            if u.path=='/oauth/callback':
                oauth_callback(urllib.parse.parse_qs(u.query)); self.send_response(302); self.send_header('Location','/'); self.end_headers(); return
            if u.path.startswith('/media/'):
                try:p=export_file(get_job(u.path.split('/')[-1]))
                except ValueError:return self.send({'error':'Media not found'},404)
            elif u.path.startswith('/thumbnail/'):
                try:p=export_thumbnail(get_job(u.path.split('/')[-1]))
                except (ValueError,OSError,subprocess.TimeoutExpired):return self.send({'error':'Thumbnail unavailable'},404)
            else:
                name=u.path.lstrip('/') or 'index.html'; p=(ROOT/'frontend'/'dist'/name).resolve()
                if not p.is_relative_to(ROOT/'frontend'/'dist'): return self.send({'error':'Invalid path'},403)
                if not p.is_file(): p=ROOT/'frontend'/'dist'/'index.html'
            if not p.is_file(): return self.send({'error':'Frontend missing. Run npm install and npm run build in frontend.'},404)
            size=p.stat().st_size; start=0; end=size-1; code=200
            if self.headers.get('Range'):
                raw=self.headers['Range'].removeprefix('bytes=').split('-'); start=int(raw[0] or 0); end=min(size-1,int(raw[1]) if raw[1] else size-1); code=206
                if start>end or start>=size: return self.send({'error':'Invalid range'},416)
            mime={'.js':'text/javascript','.css':'text/css','.html':'text/html','.mp4':'video/mp4'}.get(p.suffix.lower(),mimetypes.guess_type(str(p))[0] or 'application/octet-stream')
            self.send_response(code); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(end-start+1)); self.send_header('Accept-Ranges','bytes'); self.send_header('X-Content-Type-Options','nosniff')
            if code==206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            with p.open('rb') as f:
                f.seek(start); remaining=end-start+1
                while remaining:
                    chunk=f.read(min(1024*1024,remaining)); self.wfile.write(chunk); remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception as e: self.send({'error':str(e)},400)
    def do_POST(self):
        if self.path.startswith('/api/extension/') and self.path!='/api/extension/pair-code':
            try:
                if not self.safe_host():raise ValueError('Invalid host')
                EXTENSION.authorize(self.headers.get('X-Studio-Extension',''),self.headers.get('Origin') or 'chrome-extension://'+self.headers.get('X-Studio-Extension-Id',''),connect=self.path=='/api/extension/connect')
                length=int(self.headers.get('Content-Length','0'))
                if length>10000:raise ValueError('Extension request too large')
                p=json.loads(self.rfile.read(length) or b'{}')
                if self.path=='/api/extension/connect':return self.send({'ok':True,'expectedAccount':settings()['expectedAccount']})
                if self.path=='/api/extension/heartbeat':return self.send(EXTENSION.heartbeat(p))
                if self.path=='/api/extension/take':return self.send(EXTENSION.take())
                if self.path=='/api/extension/chunk':return self.send(EXTENSION.chunk(p))
                if self.path=='/api/extension/report':return self.send(EXTENSION.report(p))
                raise ValueError('Unknown extension route')
            except Exception as e:return self.send({'error':str(e)},403)
        if not self.safe_host() or self.headers.get('X-Studio-Token')!=CSRF or self.headers.get('Origin',BASE) not in (BASE,f'http://localhost:{PORT}'): return self.send({'error':'Invalid local request'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>100000: raise ValueError('Request too large')
            p=json.loads(self.rfile.read(length) or b'{}'); route=self.path
            if route=='/api/shutdown':
                BROWSER.submit('close')
                self.send({'ok':True}); STOP.set()
                threading.Thread(target=self.server.shutdown,daemon=True).start(); return
            if route=='/api/extension/pair-code':return self.send({'code':EXTENSION.pair_code()})
            if route=='/api/open-exports-folder':return self.send(open_exports_folder())
            if route=='/api/settings':
                save_settings(p)
                if p.get('clientSecret'):
                    c=credentials(); c['clientSecret']=p['clientSecret']; store_credentials(c)
                return self.send({'ok':True})
            if route=='/api/browse':
                selected=choose_path(p.get('kind','folder'))
                return self.send({'file' if p.get('kind')=='audio' else 'folder':selected})
            if route=='/api/scan':
                return self.send(scan(p['folders'],details=True,match_format=p.get('format','landscape') if p.get('matchFormatOnly') else None))
            if route=='/api/build':
                save_settings(p); ids,total=reserve(p); return self.send({'ids':ids,'total':total})
            if route=='/api/caption': return self.send(caption(p['id'],p['model'],p.get('notes',''),p.get('vision',False)))
            if route=='/api/connect': return self.send({'url':oauth_start()})
            if route=='/api/browser/open':
                BROWSER.submit('open');return self.send({'ok':True})
            if route=='/api/browser/verify':return self.send(BROWSER.call('verify',timeout=90))
            if route=='/api/browser/close':return self.send(BROWSER.call('close',timeout=30))
            if route=='/api/disconnect':
                store_credentials({}) if os.name!='nt' else (DATA/'credentials.dpapi').unlink(missing_ok=True); return self.send({'ok':True})
            if route=='/api/reset-cycle':
                return self.send(reset_cycle(p.get('folders'),p.get('format','landscape') if p.get('matchFormatOnly') else None))
            j=get_job(p.get('id'))
            if not j: raise ValueError('Select a montage')
            jid=j['id']
            if route=='/api/reveal-export':return self.send(open_exports_folder(j))
            if route=='/api/extension/prepare':
                if j['status'] not in ('ready','paused') or not j['payload'].get('file'):raise ValueError('Choose a ready montage')
                if not EXTENSION.state()['connected']:raise ValueError('Pair and verify X in the extension first')
                text=validate_caption(p)
                with LOCK:
                    if not claim(jid,'extension_queued',('ready','paused')):raise ValueError('Job changed state')
                    update(jid,error='',payload={'caption':text,'madeWithAI':bool(p.get('madeWithAI',True)),'postingMode':'extension','extensionAction':'prepare'})
                return self.send({'ok':True})
            if route=='/api/browser/prepare':
                if j['status'] not in ('ready','paused'):raise ValueError('Choose a ready montage')
                text=validate_caption(p)
                update(jid,payload={'caption':text,'madeWithAI':bool(p.get('madeWithAI',True)),'postingMode':'browser'})
                BROWSER.submit('prepare',get_job(jid));return self.send({'ok':True})
            if route=='/api/save-caption':
                if j['status'] in ('posting','uploading','posted','uncertain','extension_queued','extension_active'): raise ValueError('Caption cannot be changed in this state')
                update(jid,payload={'caption':validate_caption(p),'madeWithAI':bool(p.get('madeWithAI',True))}); return self.send({'ok':True})
            if route in ('/api/post','/api/schedule'):
                if j['status'] not in ('ready','paused') or not j['payload'].get('file'): raise ValueError('Montage must be ready or paused')
                mode=settings()['postingMode'];text=validate_caption(p)
                if mode=='browser':
                    if not BROWSER.state()['connected']:raise ValueError('Open X browser and verify login in Settings first')
                    if BROWSER.state()['busy']:raise ValueError('Browser is busy. Wait for the current browser action.')
                elif mode=='extension':
                    if not EXTENSION.state()['connected']:raise ValueError('Pair and verify X in the extension first')
                else:bearer()
                if route=='/api/post':
                    with LOCK:
                        if not claim(jid,'extension_queued' if mode=='extension' else 'post_queued',('ready','paused')): raise ValueError('Job changed state; refresh before posting')
                        update(jid,error='',payload={'caption':text,'madeWithAI':bool(p.get('madeWithAI',True)),'postingMode':mode,'extensionAction':'post'})
                else:
                    dt=datetime.fromisoformat(p['at'].replace('Z','+00:00'))
                    if not dt.tzinfo or dt.timestamp()<=time.time(): raise ValueError('Choose a future time with a timezone')
                    with LOCK:
                        if not claim(jid,'extension_queued' if mode=='extension' else 'web_schedule_queued' if mode=='browser' else 'scheduled',('ready','paused')): raise ValueError('Job changed state; refresh before scheduling')
                        update(jid,error='',scheduled=dt.timestamp(),payload={'caption':text,'madeWithAI':bool(p.get('madeWithAI',True)),'postingMode':mode,'extensionAction':'schedule'})
                return self.send({'ok':True})
            if route=='/api/pause' and j['status']=='scheduled':
                if not claim(jid,'paused',('scheduled',)): raise ValueError('Job is already posting; it cannot be paused')
                return self.send({'ok':True})
            if route=='/api/retry' and j['status']=='error':
                retry_job(jid)
                return self.send({'ok':True})
            if route=='/api/cancel' and j['status'] in ('queued','error','ready','paused','scheduled','extension_queued'):
                if not claim(jid,'cancelled',('queued','error','ready','paused','scheduled','extension_queued')): raise ValueError('Active job cannot be cancelled')
                with db() as c: c.execute('UPDATE clips SET reserved=NULL WHERE reserved=?',(jid,))
                return self.send({'ok':True})
            raise ValueError('Action unavailable in this state')
        except Exception as e: self.send({'error':str(e)},400)

class StudioHTTPServer(ThreadingHTTPServer):
    allow_reuse_address=False
    def server_bind(self):
        if os.name=='nt' and hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

if __name__=='__main__':
    # Bind before recovery can touch the database. A duplicate launch must have no side effects.
    try: server=StudioHTTPServer(('127.0.0.1',PORT),Handler)
    except OSError as e:
        print(f'Montage Studio cannot start on {BASE}: {e}. An existing app instance may already be running.',flush=True)
        raise SystemExit(1)
    init(); threading.Thread(target=worker,daemon=True).start()
    print(f'Montage Studio: {BASE}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: STOP.set()

