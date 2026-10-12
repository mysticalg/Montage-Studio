import importlib.util, json, os, tempfile, unittest, threading, urllib.request, urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

TEMP=tempfile.TemporaryDirectory(); os.environ['MONTAGE_DATA']=TEMP.name
spec=importlib.util.spec_from_file_location('studio',Path(__file__).with_name('server.py')); s=importlib.util.module_from_spec(spec); spec.loader.exec_module(s)

class StudioTests(unittest.TestCase):
 def test_ai_output_validation_precedes_reservation_and_persists_plan(self):
  for options in ({'resolution':'999'},{'aiUpscaleModel':'invalid'}):
   with self.assertRaises(ValueError):s.reserve(options)
  with patch.object(s.ai_upscaler,'available',return_value=False):
   with self.assertRaisesRegex(ValueError,'upscaler is missing'):s.reserve({'aiUpscale':True})
  (self.pool/'clip.mp4').write_bytes(b'clip')
  options=dict(folders=[str(self.pool)],music=str(self.music),clips=1,batch=1,aiUpscale=True,aiUpscaleModel='general',resolution='1080')
  ids,_=s.reserve(options);payload=s.get_job(ids[0])['payload']
  self.assertTrue(payload['aiUpscale']);self.assertEqual(payload['resolution'],'1080');self.assertEqual(payload['aiUpscaleModel'],'general')

 def test_pingpong_ease_validation_and_saved_plan(self):
  for ease in (0,2.1,float('nan')):
   with self.assertRaisesRegex(ValueError,'deceleration'):s.reserve(dict(pingPongEase=ease))
  (self.pool/'clip.mp4').write_bytes(b'clip')
  options=dict(folders=[str(self.pool)],music=str(self.music),clips=1,batch=1,pingPong=True,smoothPingPong=True,pingPongEase=.4,clipDuration=2)
  with patch.object(s,'probe',return_value={'streams':[{'codec_type':'video','duration':'1'}],'format':{}}):
   ids,_=s.reserve(options)
  plan=s.get_job(ids[0])['payload'];self.assertTrue(plan['smoothPingPong']);self.assertEqual(plan['pingPongEase'],.4)

 def test_real_eased_pingpong_decelerates_and_preserves_duration_and_endpoints(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  folder=Path(TEMP.name)/'ease-test';folder.mkdir(exist_ok=True)
  width=192;height=64;raw=folder/'moving.rgb';source=folder/'moving.mp4'
  raw.write_bytes(b''.join(b''.join(bytes([255 if 20+4*n<=x<24+4*n else 0])*3 for x in range(width))*height for n in range(31)))
  s.run(['ffmpeg','-y','-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{width}x{height}','-framerate','30','-i',str(raw),'-c:v','libx264','-crf','0','-pix_fmt','yuv420p',str(source)])
  original=s.hash_file(source);output=s.ping_pong(source,3,folder,ease_seconds=.3)
  info=s.probe(output);self.assertEqual(int(info['streams'][0]['nb_frames']),90);self.assertAlmostEqual(float(info['format']['duration']),3,delta=.02)
  pixels=s.run(['ffmpeg','-v','error','-i',str(output),'-pix_fmt','gray','-f','rawvideo','-'])
  positions=[]
  for n in range(90):
   frame=pixels[n*width*height:(n+1)*width*height]
   weights=[sum(frame[y*width+x] for y in range(height)) for x in range(width)]
   positions.append(sum(x*w for x,w in enumerate(weights))/sum(weights))
  self.assertAlmostEqual(positions[0],21.5,delta=2);self.assertAlmostEqual(positions[30],141.5,delta=2);self.assertAlmostEqual(positions[60],21.5,delta=2)
  forward=[positions[i+1]-positions[i] for i in range(30)]
  self.assertTrue(all(delta>-.2 for delta in forward))
  self.assertTrue(all(positions[i+1]-positions[i]<.2 for i in range(30,60)))
  self.assertLess(sum(forward[-3:])/3,sum(forward[12:18])/6*.7)
  self.assertLess(sum(forward[:3])/3,sum(forward[12:18])/6*.7)
  self.assertEqual(s.hash_file(source),original)
  hard=s.ping_pong(source,3,folder);self.assertNotEqual(output,hard)
  source_info=s.probe(source)
  with patch.object(s,'probe',return_value=source_info),patch.object(s,'run',side_effect=AssertionError('Cached eased clip rerendered')):self.assertEqual(s.ping_pong(source,3,folder,ease_seconds=.3),output)

 def test_display_ratio_accounts_for_rotation_and_non_square_pixels(self):
  self.assertAlmostEqual(s.display_ratio({'width':1920,'height':1080}),16/9)
  self.assertAlmostEqual(s.display_ratio({'width':1920,'height':1080,'side_data_list':[{'rotation':-90}]}),9/16)
  self.assertAlmostEqual(s.display_ratio({'width':1080,'height':1920,'tags':{'rotate':'90'}}),16/9)
  self.assertAlmostEqual(s.display_ratio({'width':720,'height':576,'sample_aspect_ratio':'64:45'}),16/9)
  self.assertEqual(s.display_ratio({'width':100,'height':100,'sample_aspect_ratio':'0:1'}),1)
  with self.assertRaises(ValueError):s.display_ratio({'width':0,'height':1080})

 def test_ratio_scan_counts_caches_and_preserves_other_ratios(self):
  for i in range(6):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*30)
  ratios=[16/9,9/16,1,1.78,4/3,None]
  with patch.object(s,'clip_display_ratio',side_effect=lambda path:ratios[int(Path(path).stem)]) as read:
   result=s.scan([str(self.pool)],details=True,match_format='landscape')
   self.assertEqual(result,dict(total=2,unused=2,used=0,reserved=0,poolTotal=6,formatRatio='16:9',excludedRatio=3,unreadable=1))
   self.assertEqual(read.call_count,6)
  with patch.object(s,'clip_display_ratio',side_effect=AssertionError('Metadata cache ignored')):
   portrait,total=s.scan([str(self.pool)],match_format='portrait');self.assertEqual(total,1)
   with s.db() as c:c.execute('UPDATE clips SET used=1 WHERE hash=?',(portrait[0]['hash'],))
   self.assertEqual(s.scan([str(self.pool)],details=True,match_format='portrait')['used'],1)
   self.assertEqual(len(s.scan([str(self.pool)])[0]),5)
  with self.assertRaisesRegex(ValueError,'Choose landscape'):s.scan([str(self.pool)],match_format='invalid')

 def test_ratio_filter_applies_to_batch_and_extra_full_track_clips(self):
  for i in range(5):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*30)
  def ratio(path):return 9/16 if int(Path(path).stem)<3 else 16/9
  def info(path):return {'streams':[{'codec_type':'audio' if str(path).endswith('.wav') else 'video','duration':'2' if str(path).endswith('.wav') else '1'}],'format':{}}
  options=dict(folders=[str(self.pool)],music=str(self.music/'funk.wav'),clips=1,batch=1,format='portrait',matchFormatOnly=True,matchAudioLength=True)
  with patch.object(s,'clip_display_ratio',side_effect=ratio),patch.object(s,'probe',side_effect=info):
   ids,total=s.reserve(options)
  job=s.get_job(ids[0]);self.assertEqual(total,3);self.assertEqual(len(job['payload']['clips']),2)
  self.assertTrue(job['payload']['matchFormatOnly']);self.assertTrue(all(int(Path(c['path']).stem)<3 for c in job['payload']['clips']))
  ids,_=s.reserve(options|{'matchAudioLength':False,'clips':10,'batch':0})
  self.assertEqual(len(s.get_job(ids[0])['payload']['clips']),1)
  with self.assertRaisesRegex(ValueError,'No matching 1:1'):s.reserve(options|{'format':'square'})
  self.assertEqual(len(s.scan([str(self.pool)])[0]),2)
  with self.assertRaisesRegex(ValueError,'No unused matching'):s.reserve(options|{'matchAudioLength':False})

 def test_real_rotated_video_matches_display_format(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  landscape=self.pool/'landscape.mp4';rotated=self.pool/'rotated.mp4'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=green:s=160x90:r=30:d=0.2','-c:v','libx264',str(landscape)])
  help_text=s.run(['ffmpeg','-hide_banner','-h','full'])
  rotation=['-display_rotation','90'] if b'-display_rotation' in help_text else []
  metadata=[] if rotation else ['-metadata:s:v:0','rotate=90']
  s.run(['ffmpeg','-y','-v','error',*rotation,'-i',str(landscape),'-c','copy',*metadata,str(rotated)])
  self.assertAlmostEqual(s.clip_display_ratio(landscape),16/9)
  self.assertAlmostEqual(s.clip_display_ratio(rotated),9/16)
  clips,total=s.scan([str(self.pool)],match_format='portrait');self.assertEqual(total,1);self.assertEqual(clips[0]['path'],str(rotated.resolve()))

 def test_ratio_filter_missing_ffprobe_is_actionable_and_does_not_cache_failures(self):
  with patch.object(s.shutil,'which',return_value=None):
   with self.assertRaisesRegex(ValueError,'Install FFprobe'):s.scan([str(self.pool)],match_format='portrait')
  with s.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM clip_geometry').fetchone()[0],0)
 def test_export_access_rejects_outside_missing_and_traversing_paths(self):
  outside=self.pool/'outside.mp4';outside.write_bytes(b'outside')
  for value in (None,str(outside),str(s.EXPORTS/'..'/'pool'/'outside.mp4'),str(s.EXPORTS/'missing.mp4')):
   with self.assertRaises(ValueError):s.export_file({'payload':{'file':value}})
  with self.assertRaises(ValueError):s.export_file(None)

 def test_real_thumbnail_is_cached_and_preserves_video(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  video=s.EXPORTS/'thumbnail-fixture.mp4'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=blue:s=90x160:r=30:d=0.2','-c:v','libx264',str(video)])
  original=s.hash_file(video);job={'payload':{'file':str(video)}}
  image=s.export_thumbnail(job);self.assertTrue(image.is_file())
  info=s.probe(image)['streams'][0];self.assertEqual((info['width'],info['height']),(480,270))
  with patch.object(s.subprocess,'run',side_effect=AssertionError('Cached thumbnail regenerated')):
   self.assertEqual(s.export_thumbnail(job),image)
  self.assertEqual(s.hash_file(video),original)
  video.unlink()
  with self.assertRaises(ValueError):s.export_thumbnail(job)

 def test_folder_actions_use_only_validated_exports(self):
  video=s.EXPORTS/'folder fixture.mp4';video.write_bytes(b'fixture')
  with patch.object(s,'open_folder') as folder:
   s.open_exports_folder();folder.assert_called_once_with(s.EXPORTS,reveal=False)
   s.open_exports_folder({'payload':{'file':str(video)}})
   folder.assert_called_with(video.resolve(),reveal=True)
   with self.assertRaises(ValueError):s.open_exports_folder({'payload':{'file':str(self.pool/'outside.mp4')}})
   self.assertEqual(folder.call_count,2)

 def test_reset_releases_failed_and_orphaned_holds_but_preserves_paused_plans(self):
  for i in range(4):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  clips,_=s.scan([str(self.pool)]);clips=sorted(clips,key=lambda clip:clip['path'])
  payload=dict(clips=[dict(clips[1],startSeconds=.000037)],sequence=[dict(clips[1],startSeconds=.000037)])
  with s.db() as c:
   c.execute('UPDATE clips SET used=1 WHERE hash=?',(clips[0]['hash'],))
   c.execute('UPDATE clips SET reserved=? WHERE hash=?',('failed',clips[1]['hash']))
   c.execute('UPDATE clips SET reserved=? WHERE hash=?',('missing-job',clips[2]['hash']))
   c.execute('UPDATE clips SET reserved=? WHERE hash=?',('paused',clips[3]['hash']))
   c.execute('INSERT INTO jobs VALUES(?,?,?, ?,NULL,?)',('failed','error',json.dumps(payload),'Original render failure',1))
   c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('paused','paused',json.dumps(dict(clips=[clips[3]])),1))
  result=s.reset_cycle([str(self.pool)])
  self.assertEqual(result['released'],2);self.assertEqual(result['scan'],dict(total=4,unused=3,used=0,reserved=1))
  failed=s.get_job('failed');self.assertEqual(failed['status'],'error');self.assertEqual(failed['error'],'Original render failure')
  self.assertEqual(failed['payload']['sequence'],payload['sequence']);self.assertTrue(failed['payload']['cycleReservationReleased'])
  self.assertEqual(s.get_job('paused')['status'],'paused')
  s.retry_job('failed');self.assertEqual(s.get_job('failed')['status'],'queued')
  self.assertNotIn('cycleReservationReleased',s.get_job('failed')['payload'])
  with s.db() as c:self.assertEqual(c.execute('SELECT reserved FROM clips WHERE hash=?',(clips[1]['hash'],)).fetchone()[0],'failed')

 def test_reset_blocks_active_jobs_and_browser_actions_without_mutating_clips(self):
  path=self.pool/'clip.mp4';path.write_bytes(b'clip');clips,_=s.scan([str(self.pool)])
  with s.db() as c:c.execute('UPDATE clips SET used=1')
  for status in ('queued','rendering','browser_posting','web_schedule_queued','extension_active'):
   with s.db() as c:c.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,NULL,NULL,?)',('active',status,'{}',1))
   with self.assertRaisesRegex(ValueError,'Wait for active'):s.reset_cycle([str(self.pool)])
   with s.db() as c:self.assertEqual(c.execute('SELECT used FROM clips').fetchone()[0],1)
  with s.db() as c:c.execute('DELETE FROM jobs')
  with patch.object(s.BROWSER,'state',return_value={'busy':True}):
   with self.assertRaisesRegex(ValueError,'Wait for active'):s.reset_cycle([str(self.pool)])

 def test_retry_released_clips_cannot_conflict_with_another_build_or_consumption(self):
  for i in range(2):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  clips,_=s.scan([str(self.pool)]);payload=dict(clips=clips,sequence=clips)
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?, ?,NULL,?)',('failed','error',json.dumps(payload),'Failed',1))
  for used,owner in ((1,None),(0,'another-build')):
   with s.db() as c:c.execute('UPDATE clips SET used=?,reserved=? WHERE hash=?',(used,owner,clips[-1]['hash']))
   with self.assertRaisesRegex(ValueError,'already used or reserved'):s.retry_job('failed')
   self.assertEqual(s.get_job('failed')['status'],'error')
   with s.db() as c:self.assertIsNone(c.execute('SELECT reserved FROM clips WHERE hash=?',(clips[0]['hash'],)).fetchone()[0])

 def test_reset_preserves_ready_export_payloads_and_reports_used_counts(self):
  path=self.pool/'clip.mp4';path.write_bytes(b'clip');clips,_=s.scan([str(self.pool)])
  payload=dict(clips=clips,file='existing.mp4',caption='Saved caption')
  with s.db() as c:
   c.execute('UPDATE clips SET used=1')
   c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('ready','ready',json.dumps(payload),1))
  self.assertEqual(s.scan([str(self.pool)],details=True)['used'],1)
  self.assertEqual(s.reset_cycle([str(self.pool)])['scan']['unused'],1)
  self.assertEqual(s.get_job('ready')['payload'],payload)

 def test_real_tiny_seek_is_decimal_and_renders_successfully(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  path=self.pool/'tiny-seek.mp4';music=self.music/'tiny-tone.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=30:d=1','-c:v','libx264',str(path)])
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=1',str(music)])
  clip=dict(path=str(path),hash=s.hash_file(path),startSeconds=.000037,takeDuration=.5)
  payload=dict(clips=[clip],sequence=[clip],music=str(music),format='landscape',fixedClipLength=True,clipDuration=.5)
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('tiny-seek','queued',json.dumps(payload),1))
  with patch.object(s,'run',wraps=s.run) as commands:s.render(s.get_job('tiny-seek'))
  encoding=next(call.args[0] for call in commands.call_args_list if '-ss' in call.args[0])
  self.assertEqual(encoding[encoding.index('-ss')+1],'0.000037')
  self.assertEqual(s.get_job('tiny-seek')['status'],'ready')
  self.assertAlmostEqual(s.get_job('tiny-seek')['payload']['duration'],.5,delta=.034)

 def test_5000_clip_limit_and_no_silent_clamping(self):
  available=[dict(hash=f'{i:064x}',path=f'clip-{i}.mp4') for i in range(5001)]
  with s.db() as c:c.executemany('INSERT INTO clips(hash,path) VALUES(?,?)',[(clip['hash'],clip['path']) for clip in available])
  with patch.object(s,'scan',return_value=(available,5001)):
   ids,_=s.reserve(dict(folders=[str(self.pool)],music=str(self.music),clips=5000,batch=0))
  counts=sorted(len(s.get_job(j)['payload']['clips']) for j in ids)
  self.assertEqual(counts,[1,5000])
  hashes=[clip['hash'] for job in s.jobs() for clip in job['payload']['clips']]
  self.assertEqual(len(set(hashes)),5001)
  with s.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM clips WHERE reserved IS NOT NULL').fetchone()[0],5001)
  for count in (0,5001):
   with self.assertRaisesRegex(ValueError,'between 1 and 5000'):s.reserve(dict(folders=[str(self.pool)],music=str(self.music),clips=count,batch=1))

 def test_match_audio_selects_all_5000_requested_clips(self):
  available=[dict(hash=f'{i:064x}',path=f'clip-{i}.mp4') for i in range(5000)]
  def info(path):return {'streams':[{'codec_type':'audio' if str(path).endswith('.wav') else 'video','duration':'1000' if str(path).endswith('.wav') else '1'}],'format':{}}
  with patch.object(s,'scan',return_value=(available,5000)),patch.object(s,'probe',side_effect=info):
   ids,_=s.reserve(dict(folders=[str(self.pool)],music=str(self.music),clips=5000,batch=1,matchAudioLength=True))
  clips=s.get_job(ids[0])['payload']['clips'];self.assertEqual(len(clips),5000)
  self.assertTrue(all(abs(c['renderDuration']-.2)<.034 for c in clips))

 def test_5000_crossfades_bound_inputs_and_preserve_group_overlaps(self):
  import re
  tmp=Path(TEMP.name)/'bounded-fades';tmp.mkdir(exist_ok=True)
  parts=[tmp/f'input-{i}.mp4' for i in range(5000)];durations={str(p):1 for p in parts};commands=[]
  def fake_run(args):
   commands.append(args);inputs=[args[i+1] for i,a in enumerate(args) if a=='-i']
   graph=args[args.index('-filter_complex')+1]
   duration=sum(durations[p] for p in inputs)-sum(float(f) for f in re.findall(r'xfade=transition=fade:duration=([\d.]+)',graph))
   staged=Path(args[-1]);staged.write_bytes(b'encoded');durations[str(staged.with_name(staged.name.replace('.partial','')))]=duration
  with patch.object(s,'run',side_effect=fake_run),patch.object(s,'probe',side_effect=lambda path:{'format':{'duration':durations[str(path)]}}):
   _,length=s.crossfade_parts(parts,[1]*5000,[.1]*4999,tmp)
  self.assertAlmostEqual(length,5000-4999*.1,places=6)
  self.assertTrue(all(args.count('-i')<=16 for args in commands));self.assertGreater(len(commands),1)

 def test_real_crossfade_group_boundary_contains_both_sources(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  tmp=Path(TEMP.name)/'real-group-fades';tmp.mkdir(exist_ok=True);parts=[]
  for i in range(17):
   p=tmp/f'{i}.mp4';parts.append(p)
   colour='red' if i<16 else 'blue'
   s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'color=c={colour}:s=160x90:r=30:d=0.3','-c:v','libx264',str(p)])
  result,length=s.crossfade_parts(parts,[.3]*17,[.1]*16,tmp)
  self.assertAlmostEqual(length,3.5,delta=1/30+.002)
  pixel=s.run(['ffmpeg','-v','error','-ss','3.25','-i',str(result),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
  self.assertGreater(pixel[0],30);self.assertGreater(pixel[2],30)

 def test_single_music_file_is_used_for_every_batch_montage(self):
  for i in range(3): (self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  selected=self.music/'funk.wav'
  ids,_=s.reserve(dict(folders=[str(self.pool)],music=str(selected),clips=1,batch=0))
  self.assertEqual(len(ids),3)
  self.assertTrue(all(s.get_job(j)['payload']['music']==str(selected.resolve()) for j in ids))
 def test_music_file_validation_and_recursive_folder_support(self):
  nested=self.music/'nested';nested.mkdir(exist_ok=True);track=nested/'upper.WAV';track.write_bytes(b'music')
  self.assertIn(track,s.music_tracks(str(self.music)))
  for source,error in [('', 'Choose a music file or folder'),(str(self.music/'missing.wav'),'does not exist')]:
   with self.assertRaisesRegex(ValueError,error):s.music_tracks(source)
  invalid=self.music/'picture.jpg';invalid.write_bytes(b'image')
  with self.assertRaisesRegex(ValueError,'supported audio file'):s.music_tracks(str(invalid))
 def test_audio_picker_returns_file_and_destroys_dialog_on_selection_or_cancel(self):
  if os.name!='nt':self.skipTest('Windows native dialog fixture; other platforms covered by platform tests')
  http=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);port=http.server_address[1]
  original=s.PORT,s.BASE;s.PORT=port;s.BASE=f'http://127.0.0.1:{port}'
  threading.Thread(target=http.serve_forever,daemon=True).start()
  try:
   for selection in (str(self.music/'funk.wav'),''):
    with patch('tkinter.Tk') as root,patch('tkinter.filedialog.askopenfilename',return_value=selection) as picker:
     destroyed=threading.Event();root.return_value.destroy.side_effect=destroyed.set
     req=urllib.request.Request(s.BASE+'/api/browse',json.dumps({'kind':'audio'}).encode(),{'Content-Type':'application/json','X-Studio-Token':s.CSRF})
     with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response),{'file':selection})
     self.assertTrue(destroyed.wait(2));picker.assert_called_once();root.return_value.destroy.assert_called_once()
     self.assertIn('*.wav',picker.call_args.kwargs['filetypes'][0][1])
  finally:http.shutdown();http.server_close();s.PORT,s.BASE=original
 def test_audio_folder_does_not_report_exhausted_video_cycle(self):
  with self.assertRaisesRegex(ValueError,'No video files found'):
   s.reserve(dict(folders=[str(self.music)],music=str(self.music),clips=1,batch=1))
 def setUp(self):
  with s.db() as c: c.executescript('DROP TABLE IF EXISTS jobs; DROP TABLE IF EXISTS clips; DROP TABLE IF EXISTS settings; DROP TABLE IF EXISTS clip_geometry;')
  s.init()
  s.save_settings({'expectedAccount':'demo_creator','postingMode':'browser'})
  self.pool=Path(TEMP.name)/'pool'; self.pool.mkdir(exist_ok=True)
  for f in self.pool.iterdir(): f.unlink()
  self.music=Path(TEMP.name)/'music'; self.music.mkdir(exist_ok=True); (self.music/'funk.wav').write_bytes(b'music')
 def test_dedup_reservation_and_partial_batch(self):
  for i in range(5): (self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  (self.pool/'copy.mp4').write_bytes(bytes([0])*20)
  ids,total=s.reserve(dict(folders=[str(self.pool)],music=str(self.music),clips=2,batch=0))
  self.assertEqual(total,5); self.assertEqual(len(ids),3)
  hashes=[x['hash'] for j in s.jobs() for x in j['payload']['clips']]
  self.assertEqual(len(hashes),len(set(hashes))); self.assertEqual(len(hashes),5)
  self.assertEqual(len(s.scan([str(self.pool)])[0]),0)
 def test_restart_does_not_repeat_publication(self):
  with s.db() as c:
   for state in ('posting','uploading','scheduled','rendering'):
    c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,?,?)',(state,state,'{}',100,1))
  s.init(); states={j['id']:j['status'] for j in s.jobs()}
  self.assertEqual(states,dict(posting='uncertain',uploading='error',scheduled='paused',rendering='error'))
 def test_ambiguous_post_stays_uncertain(self):
  with s.db() as c: c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('one','ready',json.dumps(dict(file='test.mp4',caption='Test',madeWithAI=True)),1))
  def x(path,data=None,method=None):
   if path=='/2/users/me': return {'data':{'username':'demo_creator'}}
   raise TimeoutError('Response lost')
  with patch.object(s,'x_request',side_effect=x),patch.object(s,'upload',return_value='123'):
   s.publish(s.get_job('one'))
  self.assertEqual(s.get_job('one')['status'],'uncertain')
 def test_ollama_caption_json(self):
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('one','ready',json.dumps(dict(file='test.mp4',music='funk.wav',clips=[{'path':'city.mp4'}])),1))
  with patch.object(s,'http_json',side_effect=[{'capabilities':['completion']},{'message':{'content':json.dumps({'description':'City lights in motion.','hashtags':['#Synthwave','#CityLights']})}}]):
   result=s.caption('one','llama3.1:8b','City skyline, synthwave music',False)
  self.assertIn('#Synthwave',result['caption']); self.assertEqual(s.get_job('one')['payload']['captionModel'],'llama3.1:8b')
 def test_local_only_ollama(self):
  s.save_settings({'ollama':'https://external.example'})
  with self.assertRaises(ValueError): s.ollama_url()
 def test_windows_secret_roundtrip(self):
  if os.name!='nt': self.skipTest('Windows only')
  raw=b'local-test-secret'; encrypted=s.crypt(raw)
  self.assertNotIn(raw,encrypted); self.assertEqual(s.crypt(encrypted,True),raw)
 def test_oauth_state_and_pkce(self):
  s.save_settings({'clientId':'test-client'})
  url=s.oauth_start(); self.assertIn('code_challenge_method=S256',url); self.assertNotIn(s.OAUTH['verifier'],url)
  with self.assertRaises(ValueError): s.oauth_callback({'state':['wrong'],'code':['fake']})
 def test_caption_limit(self):
  with self.assertRaises(ValueError): s.validate_caption({'caption':'a'*281})
  self.assertEqual(s.validate_caption({'caption':'hello'}),'hello')
 def test_x_chunk_upload_and_processing(self):
  media=Path(TEMP.name)/'upload.mp4'; media.write_bytes(b'x'*(4*1024*1024+2)); calls=[]
  def x(path,data=None,method=None):
   calls.append((path,data))
   if path.endswith('initialize'): return {'data':{'id':'media123'}}
   if path.endswith('finalize'): return {'data':{'processing_info':{'state':'pending','check_after_secs':1}}}
   return {'data':{'processing_info':{'state':'succeeded'}}}
  with patch.object(s,'x_request',side_effect=x),patch.object(s,'http_json',return_value={}) as append,patch.object(s,'bearer',return_value='fake'),patch.object(s.time,'sleep'):
   self.assertEqual(s.upload(media),'media123'); self.assertEqual(append.call_count,2)
   self.assertIn(b'name="segment_index"\r\n\r\n1',append.call_args_list[1].args[1])
  self.assertIn('command=STATUS',calls[-1][0])
 def test_posted_result_is_persisted(self):
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('one','ready',json.dumps(dict(file='test.mp4',caption='Test',madeWithAI=True)),1))
  with patch.object(s,'x_request',side_effect=[{'data':{'username':'demo_creator'}},{'data':{'id':'999'}}]),patch.object(s,'upload',return_value='media'):
   s.publish(s.get_job('one'))
  self.assertEqual(s.get_job('one')['status'],'posted'); self.assertTrue(s.get_job('one')['payload']['postUrl'].endswith('/999'))
 def test_http_schedule_and_csrf(self):
  s.save_settings({'postingMode':'api'})
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('one','ready',json.dumps(dict(file='test.mp4',caption='Test',madeWithAI=True)),1))
  http=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler); port=http.server_address[1]
  original=s.PORT,s.BASE; s.PORT=port; s.BASE=f'http://127.0.0.1:{port}'
  t=threading.Thread(target=http.serve_forever,daemon=True); t.start()
  try:
   data=json.dumps(dict(id='one',caption='Scheduled test',at='2030-10-26T12:00:00+01:00')).encode()
   req=urllib.request.Request(s.BASE+'/api/schedule',data,{'Content-Type':'application/json'})
   with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(req)
   self.assertEqual(caught.exception.code,403)
   caught.exception.close()
   req.add_header('X-Studio-Token',s.CSRF)
   with patch.object(s,'bearer',return_value='fake'):
    with urllib.request.urlopen(req) as response:self.assertTrue(json.load(response)['ok'])
   j=s.get_job('one'); self.assertEqual(j['status'],'scheduled')
   self.assertEqual(j['scheduled'],s.datetime.fromisoformat('2030-10-26T11:00:00+00:00').timestamp())
  finally:http.shutdown();http.server_close();s.PORT,s.BASE=original
 def test_browser_result_persisted_without_api(self):
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,?,?)',('one','web_schedule_queued',json.dumps(dict(file='test.mp4',caption='Test',postingMode='browser')),2000000000,1))
  with patch.object(s.BROWSER,'call',return_value={'nativeSchedule':True,'verifiedText':'Saved in X','progress':'Saved in X Scheduled list'}),patch.object(s,'x_request') as api:
   s.publish_browser(s.get_job('one'),True)
  self.assertEqual(s.get_job('one')['status'],'web_scheduled');api.assert_not_called()
 def test_browser_uncertain_is_not_retryable(self):
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('one','post_queued',json.dumps(dict(file='test.mp4',caption='Test',postingMode='browser')),1))
  with patch.object(s.BROWSER,'call',side_effect=s.BrowserFailure('Confirmation missing',True)):
   s.publish_browser(s.get_job('one'))
  self.assertEqual(s.get_job('one')['status'],'uncertain')
 def test_native_schedule_english_label(self):
  at=s.datetime.fromisoformat('2030-10-26T12:00:00+01:00').timestamp()
  local=s.datetime.fromtimestamp(at)
  label=s.BROWSER.schedule_label(at)
  self.assertIn(str(local.year),label);self.assertIn(f'{local.minute:02}',label)
 def test_browser_reopen_reuses_driver(self):
  from browser_poster import BrowserPoster
  poster=BrowserPoster(Path(TEMP.name),lambda:{'browserChannel':'chrome'})
  runtime=MagicMock();old_page=MagicMock();old_page.title.side_effect=RuntimeError('Browser closed')
  new_page=MagicMock();new_context=MagicMock();new_context.pages=[new_page]
  runtime.chromium.launch_persistent_context.return_value=new_context
  poster.runtime=runtime;poster.context=MagicMock();poster.page=old_page
  poster.open()
  self.assertIs(poster.runtime,runtime);runtime.chromium.launch_persistent_context.assert_called_once()
  self.assertFalse(runtime.stop.called)
  poster.call('close',timeout=5);runtime.stop.assert_called_once();self.assertIsNone(poster.runtime)
 def test_real_crossfade_duration_and_blended_frame(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  clips=[]
  for colour in ('red','blue'):
   path=Path(TEMP.name)/f'{colour}.mp4'
   s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'color=c={colour}:s=160x90:r=30:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(path)])
   clips.append({'path':str(path),'hash':s.hash_file(path)})
  music=Path(TEMP.name)/'tone.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=5',str(music)])
  payload=dict(clips=clips,music=str(music),format='landscape',crossfade=True,fadeDuration=.5)
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('fade-test','queued',json.dumps(payload),1))
  s.render(s.get_job('fade-test'));result=s.get_job('fade-test')
  self.assertEqual(result['status'],'ready');self.assertAlmostEqual(result['payload']['duration'],3.5,delta=.07)
  pixel=s.run(['ffmpeg','-v','error','-ss','1.75','-i',result['payload']['file'],'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
  self.assertTrue(70<pixel[0]<190 and 70<pixel[2]<190,repr(pixel))
 def test_real_interpolation_doubles_rate_and_preserves_duration(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  path=Path(TEMP.name)/'moving-pattern.mp4';music=Path(TEMP.name)/'interp-tone.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=0.5','-c:v','libx264','-pix_fmt','yuv420p',str(path)])
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=1',str(music)])
  payload=dict(clips=[{'path':str(path),'hash':s.hash_file(path)}],music=str(music),format='landscape',interpolate=True)
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('interp-test','queued',json.dumps(payload),1))
  s.render(s.get_job('interp-test'));result=s.get_job('interp-test');info=s.probe(result['payload']['file'])
  video=next(v for v in info['streams'] if v['codec_type']=='video')
  self.assertEqual(video['avg_frame_rate'],'60/1');self.assertEqual(int(video['nb_frames']),30)
  self.assertAlmostEqual(float(info['format']['duration']),.5,delta=.04)
  raw=s.run(['ffmpeg','-v','error','-i',result['payload']['file'],'-vf','scale=16:9','-frames:v','8','-pix_fmt','rgb24','-f','rawvideo','-'])
  frames=[raw[i:i+432] for i in range(0,len(raw),432)]
  self.assertTrue(any(frames[i]!=frames[i-1] and frames[i]!=frames[i+1] for i in (1,3,5)))

 def test_stills_even_spacing_reservation_and_shortage(self):
  self.assertEqual(s.disperse(list(range(10)),['a','b','c']),[0,1,'a',2,3,4,'b',5,6,'c',7,8,9])
  for i in range(4): (self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  for i in range(4): (self.pool/f'{i}.png').write_bytes(bytes([i+10])*20)
  opts=dict(folders=[str(self.pool)],music=str(self.music),clips=2,batch=2,includeStills=True,stills=3)
  with self.assertRaises(ValueError):s.reserve(opts)
  self.assertEqual(len(s.jobs()),0);self.assertEqual(len(s.scan([str(self.pool)])[0]),4)
  opts['stills']=2
  ids,_=s.reserve(opts)
  hashes=[x['hash'] for j in s.jobs() for x in j['payload']['sequence']]
  self.assertEqual(len(hashes),8);self.assertEqual(len(set(hashes)),8)
  self.assertEqual(len(s.scan([str(self.pool)],s.IMAGE)[0]),0)
 def test_real_still_zoom_duration_and_60fps(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  image=Path(TEMP.name)/'pattern.png';music=Path(TEMP.name)/'still-tone.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=s=320x180','-frames:v','1',str(image)])
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=2',str(music)])
  for mode in ('none','in','out'):
   still=dict(path=str(image),hash=s.hash_file(image),kind='still',zoom=mode)
   payload=dict(clips=[],stills=[still],sequence=[still],music=str(music),format='landscape',stillDuration=.5,zoomAmount=20,interpolate=True)
   jid='still-'+mode
   with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',(jid,'queued',json.dumps(payload),1))
   s.render(s.get_job(jid));result=s.get_job(jid);info=s.probe(result['payload']['file'])
   v=next(v for v in info['streams'] if v['codec_type']=='video')
   self.assertEqual(v['avg_frame_rate'],'60/1');self.assertEqual(int(v['nb_frames']),30)
   self.assertAlmostEqual(float(info['format']['duration']),.5,delta=.04)
   if mode!='none':
    raw=s.run(['ffmpeg','-v','error','-i',result['payload']['file'],'-vf','scale=16:9','-pix_fmt','rgb24','-f','rawvideo','-'])
    self.assertNotEqual(raw[:432],raw[-432:])

 def test_extension_pairing_leases_commit_and_idempotent_result(self):
  from extension_bridge import ExtensionBridge
  bridge=ExtensionBridge(s.DATA,s.settings,s.jobs,s.get_job,s.update,s.claim,s.LOCK)
  code=bridge.pair_code();origin='chrome-extension://'+'a'*32
  with self.assertRaises(ValueError):bridge.authorize('wrong',origin,True)
  with self.assertRaises(ValueError):bridge.authorize(code,'https://evil.example',True)
  bridge.authorize(code,origin,True)
  with self.assertRaises(ValueError):bridge.authorize(code,'chrome-extension://'+'b'*32,True)
  with self.assertRaises(ValueError):bridge.heartbeat({'account':'other'})
  bridge.heartbeat({'account':'demo_creator'})
  file=s.EXPORTS/'extension-test.mp4';file.write_bytes(b'neutral-fixture')
  payload=dict(file=str(file),caption='Test',extensionAction='post')
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('ext-test','extension_queued',json.dumps(payload),1))
  command=bridge.take()['job'];self.assertEqual(command['filename'],file.name)
  self.assertIsNone(bridge.take()['job'])
  with self.assertRaises(ValueError):bridge.chunk(dict(id='ext-test',lease='wrong',offset=0))
  self.assertEqual(bridge.chunk(dict(id='ext-test',lease=command['lease'],offset=0))['length'],len(b'neutral-fixture'))
  result=dict(id='ext-test',lease=command['lease'],phase='complete',verifiedText='Your post was sent',postUrl='https://x.com/demo_creator/status/123')
  with self.assertRaises(ValueError):bridge.report(result)
  bridge.report(dict(id='ext-test',lease=command['lease'],phase='commit'))
  bridge.report(result);bridge.report(result)
  self.assertEqual(s.get_job('ext-test')['status'],'posted')
  bridge.pair_code()
  with self.assertRaises(ValueError):bridge.authorize(code,origin)
 def test_extension_interruption_is_uncertain_after_commit(self):
  from extension_bridge import ExtensionBridge
  bridge=ExtensionBridge(s.DATA,s.settings,s.jobs,s.get_job,s.update,s.claim,s.LOCK)
  file=s.EXPORTS/'extension-error.mp4';file.write_bytes(b'neutral')
  bridge.heartbeat({'account':'demo_creator'})
  for attempted in (False,True):
   jid='ext-error-'+str(attempted)
   with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',(jid,'extension_queued',json.dumps(dict(file=str(file),extensionAction='post')),1))
   command=bridge.take()['job'];request=dict(id=jid,lease=command['lease'])
   if attempted:bridge.report(dict(request,phase='commit'))
   bridge.report(dict(request,phase='error',message='Connection interrupted'))
   self.assertEqual(s.get_job(jid)['status'],'uncertain' if attempted else 'error')

 def test_extension_http_origin_token_and_bounded_media(self):
  s.EXTENSION.account='';s.EXTENSION.last_seen=0
  code=s.EXTENSION.pair_code()
  http=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);port=http.server_address[1]
  original=s.PORT,s.BASE;s.PORT=port;s.BASE=f'http://127.0.0.1:{port}'
  threading.Thread(target=http.serve_forever,daemon=True).start()
  def request(route,token,origin):
   return urllib.request.urlopen(urllib.request.Request(s.BASE+'/api/extension/'+route,b'{}',{'Content-Type':'application/json','X-Studio-Extension':token,'Origin':origin}))
  try:
   with self.assertRaises(urllib.error.HTTPError) as e:request('connect',code,'https://evil.example')
   self.assertEqual(e.exception.code,403);e.exception.close()
   origin='chrome-extension://'+'c'*32
   with self.assertRaises(urllib.error.HTTPError) as e:request('connect','bad',origin)
   self.assertEqual(e.exception.code,403);e.exception.close()
   with request('connect',code,origin) as response:
    self.assertTrue(json.load(response)['ok']);self.assertEqual(response.headers['Access-Control-Allow-Origin'],origin)
   with self.assertRaises(urllib.error.HTTPError) as e:request('chunk',code,origin)
   self.assertEqual(e.exception.code,403);e.exception.close()
  finally:http.shutdown();http.server_close();s.PORT,s.BASE=original

 def test_duplicate_server_launch_cannot_recover_active_jobs(self):
  import sys
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('active-render','rendering','{}',1))
  http=s.StudioHTTPServer(('127.0.0.1',0),s.Handler)
  try:
   env=os.environ.copy();env['MONTAGE_PORT']=str(http.server_address[1]);env['MONTAGE_DATA']=str(s.DATA)
   result=s.subprocess.run([sys.executable,str(Path(__file__).with_name('server.py'))],env=env,capture_output=True,timeout=10)
   self.assertNotEqual(result.returncode,0);self.assertEqual(s.get_job('active-render')['status'],'rendering')
  finally:http.server_close()

 def test_pingpong_duration_validation(self):
  for duration in (0,121,float('nan')):
   with self.assertRaises(ValueError):s.reserve(dict(clipDuration=duration))
 def test_real_pingpong_frame_order_and_short_sources(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  folder=Path(TEMP.name)/'ping-tests';folder.mkdir(exist_ok=True)
  for count in (1,2,6,30):
   raw=folder/f'{count}.rgb';source=folder/f'{count}.mp4'
   values=[32+i*32 for i in range(count)] if count<=6 else [96]*count
   raw.write_bytes(b''.join(bytes([v,v,v])*32*16 for v in values))
   s.run(['ffmpeg','-y','-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size','32x16','-framerate','30','-i',str(raw),'-c:v','libx264','-crf','0','-pix_fmt','yuv420p',str(source)])
   output=s.ping_pong(source,.5,folder);info=s.probe(output)
   video=next(v for v in info['streams'] if v['codec_type']=='video')
   self.assertEqual(int(video['nb_frames']),15);self.assertAlmostEqual(float(info['format']['duration']),.5,delta=.02)
   if count==6:
    pixels=s.run(['ffmpeg','-v','error','-i',str(output),'-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
    order=[round((pixels[i]-32)/32) for i in range(0,len(pixels),3)]
    self.assertEqual(order,[0,1,2,3,4,5,4,3,2,1,0,1,2,3,4])
 def test_real_pingpong_with_still_interpolation_and_crossfade(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  clips=[]
  for colour in ('green','yellow'):
   path=Path(TEMP.name)/f'ping-{colour}.mp4'
   s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'color=c={colour}:s=160x90:r=30:d=0.2','-c:v','libx264',str(path)])
   clips.append(dict(path=str(path),hash=s.hash_file(path)))
  image=Path(TEMP.name)/'ping-still.png';music=Path(TEMP.name)/'ping-tone.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=s=160x90','-frames:v','1',str(image)])
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=2',str(music)])
  still=dict(path=str(image),hash=s.hash_file(image),kind='still',zoom='in')
  payload=dict(clips=clips,stills=[still],sequence=[clips[0],still,clips[1]],music=str(music),format='landscape',pingPong=True,clipDuration=.5,stillDuration=.5,zoomAmount=10,interpolate=True,crossfade=True,fadeDuration=.1)
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('ping-mix','queued',json.dumps(payload),1))
  s.render(s.get_job('ping-mix'));job=s.get_job('ping-mix');info=s.probe(job['payload']['file'])
  self.assertEqual(job['status'],'ready');self.assertEqual(job['payload']['frameRate'],60);self.assertAlmostEqual(float(info['format']['duration']),1.3,delta=.04)

 def test_hour_source_random_excerpt_is_bounded_and_persisted(self):
  (self.pool/'hour.mp4').write_bytes(b'hour source placeholder')
  options=dict(folders=[str(self.pool)],music=str(self.music),clips=1,batch=1,fixedClipLength=True,clipDuration=10,randomStart=True)
  info={'streams':[{'codec_type':'video','duration':'3600'}],'format':{'duration':'3600'}}
  with patch.object(s,'probe',return_value=info),patch.object(s.random.SystemRandom,'uniform',return_value=1234.5) as pick:
   ids,_=s.reserve(options)
  clip=s.get_job(ids[0])['payload']['clips'][0]
  pick.assert_called_once_with(0,3590.0)
  self.assertEqual(clip['startSeconds'],1234.5);self.assertEqual(clip['takeDuration'],10)
  self.assertEqual(s.get_job(ids[0])['payload']['sequence'][0],clip)
  with patch.object(s,'probe',return_value=info):self.assertEqual(s.plan_excerpt(clip,10,False)['startSeconds'],0)
 def test_short_excerpt_uses_entire_clip(self):
  info={'streams':[{'codec_type':'video','duration':'3'}],'format':{'duration':'3'}}
  with patch.object(s,'probe',return_value=info),patch.object(s.random.SystemRandom,'uniform') as pick:
   clip=s.plan_excerpt(dict(path='short.mp4',hash='one'),10,True)
  self.assertEqual(clip['startSeconds'],0);self.assertEqual(clip['takeDuration'],3);pick.assert_not_called()
 def test_real_excerpt_seeks_to_correct_section_and_duration(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  path=Path(TEMP.name)/'excerpt-red-blue.mp4';music=Path(TEMP.name)/'excerpt-tone.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=red:s=160x90:r=30:d=1','-f','lavfi','-i','color=c=blue:s=160x90:r=30:d=1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264',str(path)])
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=1',str(music)])
  clip=dict(path=str(path),hash=s.hash_file(path))
  with patch.object(s.random.SystemRandom,'uniform',return_value=1.25):clip=s.plan_excerpt(clip,.5)
  payload=dict(clips=[clip],sequence=[clip],music=str(music),format='landscape',fixedClipLength=True,clipDuration=.5)
  with s.db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,NULL,NULL,?)',('excerpt-test','queued',json.dumps(payload),1))
  with patch.object(s,'run',wraps=s.run) as commands:s.render(s.get_job('excerpt-test'))
  encoding=next(call.args[0] for call in commands.call_args_list if str(path) in call.args[0] and '-vf' in call.args[0])
  self.assertLess(encoding.index('-ss'),encoding.index('-i'));self.assertIn('1.250000',encoding)
  result=s.get_job('excerpt-test');self.assertAlmostEqual(result['payload']['duration'],.5,delta=.02)
  pixel=s.run(['ffmpeg','-v','error','-i',result['payload']['file'],'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
  self.assertGreater(pixel[2],200);self.assertLess(pixel[0],30)

 def test_match_audio_adds_unique_clips_and_keeps_short_remainder_unused(self):
  for i in range(7):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  def info(path):
   audio=Path(path).suffix=='.wav'
   return {'streams':[{'codec_type':'audio' if audio else 'video','duration':'25' if audio else '6'}],'format':{}}
  options=dict(folders=[str(self.pool)],music=str(self.music/'funk.wav'),clips=1,batch=0,matchAudioLength=True,crossfade=True,fadeDuration=.5)
  with patch.object(s,'probe',side_effect=info):ids,_=s.reserve(options)
  self.assertEqual(len(ids),1);job=s.get_job(ids[0])
  self.assertEqual(len(job['payload']['clips']),5);self.assertEqual(job['payload']['audioDuration'],25)
  self.assertEqual(len({c['hash'] for c in job['payload']['clips']}),5)
  self.assertEqual(len(s.scan([str(self.pool)])[0]),2)
 def test_match_audio_shortage_and_invalid_track_do_not_reserve(self):
  for i in range(2):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  options=dict(folders=[str(self.pool)],music=str(self.music/'funk.wav'),clips=10,batch=1,matchAudioLength=True)
  def info(path):return {'streams':[{'codec_type':'audio' if str(path).endswith('.wav') else 'video','duration':'10' if str(path).endswith('.wav') else '2'}],'format':{}}
  with patch.object(s,'probe',side_effect=info):
   with self.assertRaisesRegex(ValueError,'Not enough unused footage'):s.reserve(options)
  self.assertEqual(s.jobs(),[]);self.assertEqual(len(s.scan([str(self.pool)])[0]),2)
  with patch.object(s,'probe',return_value={'streams':[{'codec_type':'audio','duration':'nan'}],'format':{}}):
   with self.assertRaisesRegex(ValueError,'Audio duration'):s.reserve(options)
  self.assertEqual(s.jobs(),[])
 def test_match_audio_transition_time_requires_additional_clip(self):
  for i in range(3):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  def info(path):return {'streams':[{'codec_type':'audio' if str(path).endswith('.wav') else 'video','duration':'3.6' if str(path).endswith('.wav') else '2'}],'format':{}}
  options=dict(folders=[str(self.pool)],music=str(self.music/'funk.wav'),clips=2,batch=1,matchAudioLength=True,crossfade=True,fadeDuration=.5)
  with patch.object(s,'probe',side_effect=info):ids,_=s.reserve(options)
  self.assertEqual(len(s.get_job(ids[0])['payload']['clips']),3)
 def test_real_match_audio_with_still_pingpong_interpolation_and_crossfade(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  for colour in ('green','yellow','blue'):
   path=self.pool/f'{colour}.mp4'
   s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'color=c={colour}:s=160x90:r=30:d=0.2','-c:v','libx264',str(path)])
  image=self.pool/'art.png';music=self.music/'full-track.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=s=160x90','-frames:v','1',str(image)])
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=1.2345',str(music)])
  options=dict(folders=[str(self.pool)],music=str(music),clips=1,batch=1,matchAudioLength=True,pingPong=True,fixedClipLength=True,clipDuration=.5,includeStills=True,stills=1,stillDuration=.5,stillZoom='in',interpolate=True,crossfade=True,fadeDuration=.1)
  ids,_=s.reserve(options);job=s.get_job(ids[0]);self.assertEqual(len(job['payload']['clips']),2)
  with patch.object(s,'run',wraps=s.run) as commands:s.render(job)
  ready=s.get_job(ids[0]);self.assertEqual(ready['status'],'ready');info=s.probe(ready['payload']['file'])
  target=ready['payload']['audioDuration'];self.assertAlmostEqual(ready['payload']['duration'],target,delta=1/60+.002)
  video=next(v for v in info['streams'] if v['codec_type']=='video');audio=next(v for v in info['streams'] if v['codec_type']=='audio')
  self.assertEqual(video['avg_frame_rate'],'60/1');self.assertAlmostEqual(float(audio['duration']),target,delta=.002)
  mux=next(call.args[0] for call in commands.call_args_list if str(music) in call.args[0] and '-map' in call.args[0])
  self.assertNotIn('-stream_loop',mux)
  self.assertEqual(len(s.scan([str(self.pool)])[0]),1)
 def test_real_match_audio_trims_long_clip_and_preserves_unused_sources(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  for colour in ('red','blue'):
   s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'color=c={colour}:s=160x90:r=30:d=2','-c:v','libx264',str(self.pool/f'{colour}.mp4')])
  music=self.music/'short-track.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=1.2345',str(music)])
  ids,_=s.reserve(dict(folders=[str(self.pool)],music=str(music),clips=1,batch=1,matchAudioLength=True))
  s.render(s.get_job(ids[0]));ready=s.get_job(ids[0])
  self.assertEqual(len(ready['payload']['clips']),1);self.assertAlmostEqual(ready['payload']['duration'],ready['payload']['audioDuration'],delta=1/30+.002)
  self.assertEqual(len(s.scan([str(self.pool)])[0]),1)

 def test_match_audio_respects_requested_count_and_fits_all_selected_clips(self):
  for i in range(5):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  def info(path):return {'streams':[{'codec_type':'audio' if str(path).endswith('.wav') else 'video','duration':'6' if str(path).endswith('.wav') else '10'}],'format':{}}
  with patch.object(s,'probe',side_effect=info):
   ids,_=s.reserve(dict(folders=[str(self.pool)],music=str(self.music/'funk.wav'),clips=3,batch=1,matchAudioLength=True))
  job=s.get_job(ids[0]);self.assertEqual(len(job['payload']['clips']),3)
  self.assertTrue(all(abs(c['renderDuration']-2)<.034 for c in job['payload']['clips']))
  self.assertEqual(len(s.scan([str(self.pool)])[0]),2)

 def test_beat_failure_rolls_back_jobs_and_reservations(self):
  for i in range(2):(self.pool/f'{i}.mp4').write_bytes(bytes([i])*20)
  def info(path):return {'streams':[{'codec_type':'audio' if str(path).endswith('.wav') else 'video','duration':'6'}],'format':{}}
  with patch.object(s,'probe',side_effect=info),patch.object(s,'detect_beats',side_effect=ValueError('No clear beat')):
   with self.assertRaisesRegex(ValueError,'No clear beat'):s.reserve(dict(folders=[str(self.pool)],music=str(self.music/'funk.wav'),clips=2,batch=1,beatSync=True))
  self.assertEqual(s.jobs(),[]);self.assertEqual(len(s.scan([str(self.pool)])[0]),2)

 def test_real_beat_synced_cuts_land_on_detected_clicks(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  import wave, struct, math
  for colour in ('red','green','blue'):
   s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'color=c={colour}:s=160x90:r=30:d=2.4','-c:v','libx264',str(self.pool/f'{colour}.mp4')])
  music=self.music/'clicks.wav';rate=8000
  samples=[]
  for i in range(6*rate):
   phase=(i/rate-.25)%.5
   samples.append(int(18000*math.exp(-phase*90)*math.sin(2*math.pi*180*i/rate)) if phase<.06 else 0)
  with wave.open(str(music),'wb') as w:w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes(struct.pack('<'+'h'*len(samples),*samples))
  original={str(p):s.hash_file(p) for p in [*self.pool.glob('*.mp4'),music]}
  ids,_=s.reserve(dict(folders=[str(self.pool)],music=str(music),clips=3,batch=1,matchAudioLength=True,beatSync=True))
  job=s.get_job(ids[0]);self.assertAlmostEqual(job['payload']['detectedBpm'],120,delta=3)
  cuts=job['payload']['beatCuts'];self.assertEqual(len(cuts),2)
  for cut in cuts:self.assertLess(min(abs(cut-(.25+i*.5)) for i in range(12)),1/30+.011)
  s.render(job);ready=s.get_job(ids[0]);self.assertEqual(ready['status'],'ready')
  self.assertEqual(len(ready['payload']['clips']),3);self.assertAlmostEqual(ready['payload']['duration'],6,delta=1/30+.002)
  for cut in cuts:
   frame=round(cut*30)
   pixels=s.run(['ffmpeg','-v','error','-i',ready['payload']['file'],'-vf',f'select=eq(n\\,{frame-1})+eq(n\\,{frame}),scale=1:1','-vsync','0','-pix_fmt','rgb24','-f','rawvideo','-'])
   self.assertEqual(len(pixels),6);self.assertNotEqual(pixels[:3],pixels[3:])
  self.assertTrue(all(s.hash_file(Path(path))==h for path,h in original.items()))

 def test_real_beat_sync_crossfade_still_pingpong_and_60fps(self):
  if not s.shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
  for colour in ('red','blue'):
   s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',f'color=c={colour}:s=160x90:r=30:d=0.2','-c:v','libx264',str(self.pool/f'{colour}.mp4')])
  image=self.pool/'art.png';music=self.music/'beat-tone.wav'
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=s=160x90','-frames:v','1',str(image)])
  s.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=2.25',str(music)])
  ids,_=s.reserve(dict(folders=[str(self.pool)],music=str(music),clips=2,batch=1,matchAudioLength=True,beatSync=True,beatBpm=120,pingPong=True,clipDuration=2,includeStills=True,stills=1,stillDuration=1,interpolate=True,crossfade=True,fadeDuration=.2))
  job=s.get_job(ids[0]);self.assertEqual(len(job['payload']['beatCuts']),2)
  s.render(job);ready=s.get_job(ids[0]);self.assertEqual(ready['status'],'ready');self.assertAlmostEqual(ready['payload']['duration'],2.25,delta=1/60+.002)
  self.assertEqual(len(ready['payload']['clips']),2);self.assertEqual(len(ready['payload']['stills']),1)
  elapsed=0
  for i,clip in enumerate(job['payload']['sequence'][:-1]):
   elapsed+=clip['renderDuration']
   if i:elapsed-=job['payload']['timelineOverlaps'][i-1]
   self.assertAlmostEqual(elapsed-job['payload']['timelineOverlaps'][i]/2,job['payload']['beatCuts'][i],delta=1e-5)

if __name__=='__main__': unittest.main()
