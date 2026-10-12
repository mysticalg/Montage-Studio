"""Exercise packaged server, neutral FFmpeg render and clean data on the native runner."""
import hashlib,json,os,subprocess,sys,tempfile,time,urllib.request,urllib.error
from pathlib import Path
root=Path(__file__).resolve().parents[1]
exe=root/'dist'/'Montage Studio.app'/'Contents'/'MacOS'/'Montage Studio' if sys.platform=='darwin' else root/'dist'/'Montage Studio'/('Montage Studio.exe' if sys.platform=='win32' else 'Montage Studio')
with tempfile.TemporaryDirectory() as folder:
 temp=Path(folder);env=os.environ|{'MONTAGE_DATA':str(temp/'data'),'MONTAGE_PORT':'8876'}
 pool=temp/'clips';pool.mkdir();track=temp/'music.wav'
 for i in range(2):subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'testsrc2=s=160x90:r=30:d=0.6','-vf',f'hue=h={i*100}','-c:v','libx264',str(pool/f'{i}.mp4')],check=True)
 subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1.2',str(track)],check=True)
 original={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pool.iterdir()}
 log=(temp/'service.log').open('wb');process=subprocess.Popen([str(exe),'--server'],env=env,stdout=log,stderr=log)
 base='http://127.0.0.1:8876'
 def get():
  with urllib.request.urlopen(base+'/api/state',timeout=3) as r:return json.load(r)
 def post(path,payload):
  request=urllib.request.Request(base+'/api/'+path,json.dumps(payload).encode(),{'Content-Type':'application/json','X-Studio-Token':state['csrf']})
  try:
   with urllib.request.urlopen(request,timeout=30) as r:return json.load(r)
  except urllib.error.HTTPError as e:raise RuntimeError(e.read().decode()) from e
 try:
  for _ in range(100):
   try:state=get();break
   except OSError:
    if process.poll() is not None:raise RuntimeError('Frozen service failed: '+(temp/'service.log').read_text(errors='replace'))
    time.sleep(.2)
  else:raise RuntimeError('Frozen server did not start')
  assert state['jobs']==[] and state['settings']['expectedAccount']=='' and state['settings']['folders']==['']
  built=post('build',{'folders':[str(pool)],'music':str(track),'clips':2,'batch':1,'format':'landscape','resolution':'720','matchAudioLength':True,'crossfade':True,'fadeDuration':.1,'pingPong':True,'smoothPingPong':True,'pingPongEase':.15,'clipDuration':.8})
  for _ in range(300):
   job=get()['jobs'][0]
   if job['status']=='error':raise RuntimeError(job['error'])
   if job['status']=='ready':break
   time.sleep(.2)
  else:raise RuntimeError('Frozen render timed out')
  video=Path(job['payload']['file']);info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]))
  stream=next(s for s in info['streams'] if s['codec_type']=='video')
  assert (stream['width'],stream['height'])==(1280,720)
  assert abs(float(info['format']['duration'])-1.2)<.08
  assert any(s['codec_type']=='audio' for s in info['streams'])
  subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True)
  assert original=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pool.iterdir()}
  post('shutdown',{});process.wait(timeout=15)
  # GUI loads the real bundled frontend in each platform's renderer.
  diagnostics=temp/'desktop-smoke.json'
  gui=subprocess.Popen([str(exe),'--diagnostics',str(diagnostics)],env=env,stdout=log,stderr=log)
  gui.wait(timeout=90)
  assert gui.returncode==0 and json.loads(diagnostics.read_text())['passed'],(temp/'service.log').read_text(errors='replace')+'\n'+(temp/'data'/'service.log').read_text(errors='replace')+'\n'+(diagnostics.read_text() if diagnostics.exists() else 'No desktop diagnostics')
  state=get();post('shutdown',{})
  print(json.dumps({'platform':sys.platform,'render':'passed','desktop':'passed','size':[1280,720],'duration':info['format']['duration'],'originals':'preserved'}))
 finally:
  if process.poll() is None:process.terminate();process.wait(timeout=15)
  try:
   state=get();post('shutdown',{})
  except OSError:pass
  time.sleep(1);log.close()
