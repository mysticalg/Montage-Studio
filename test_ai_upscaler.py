import json,subprocess,tempfile,threading,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
import ai_upscaler as ai

class UpscalerTests(unittest.TestCase):
 def test_output_dimensions_and_legacy_defaults(self):
  self.assertEqual(ai.output_size('square'),(1080,1080))
  self.assertEqual(ai.output_size('portrait'),(720,1280))
  for value,w,h in [('720',1280,720),('1080',1920,1080),('1440',2560,1440),('2160',3840,2160)]:
   self.assertEqual(ai.output_size('landscape',value),(w,h));self.assertEqual(ai.output_size('portrait',value),(h,w));self.assertEqual(ai.output_size('square',value),(h,h))
  for fmt,res in [('bad','auto'),('landscape','999')]:
   with self.assertRaises(ValueError):ai.output_size(fmt,res)

 def test_portrait_23_dimensions(self):
  for resolution in ('auto','720','1080','1440','2160'):
   w,h=ai.output_size('portrait23',resolution)
   self.assertEqual(w*3,h*2)
   self.assertEqual((w%2,h%2),(0,0))
  self.assertEqual(ai.output_size('portrait23'),(720,1080))

 def test_display_dimensions_and_scale_selection(self):
  self.assertEqual(ai.source_size({'width':1920,'height':1080,'side_data_list':[{'rotation':90}]}),(1080,1920))
  self.assertEqual(ai.source_size({'width':720,'height':576,'sample_aspect_ratio':'64:45'}),(1024,576))
  self.assertEqual(ai.upscale_scale((1280,720),(1280,720),'fast'),0)
  self.assertEqual(ai.upscale_scale((640,360),(1280,720),'fast'),2)
  self.assertEqual(ai.upscale_scale((640,360),(1920,1080),'fast'),4)
  self.assertEqual(ai.upscale_scale((640,360),(1280,720),'general'),4)

 def test_high_resolution_bypass_and_missing_model(self):
  with tempfile.TemporaryDirectory() as temp:
   tmp=Path(temp)/'cache'
   path,result=ai.upscale('unused.mp4',tmp,(1280,720),{'width':1920,'height':1080})
   self.assertIsNone(path);self.assertFalse(result['applied']);self.assertFalse(tmp.exists())
   with patch.object(ai,'available',return_value=False):
    with self.assertRaisesRegex(ValueError,'complete Montage'):ai.upscale('missing.mp4',tmp,(1280,720),{'width':160,'height':90})

 def test_png_writer_and_frame_reader(self):
  import io,struct
  self.assertEqual(ai._frame(io.BytesIO(b'abcd'),4),b'abcd');self.assertIsNone(ai._frame(io.BytesIO(b''),4))
  with self.assertRaisesRegex(RuntimeError,'Incomplete'):ai._frame(io.BytesIO(b'ab'),4)
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'frame.png';ai.png_rgb(path,b'\x80'*12,2,2)
   self.assertEqual(struct.unpack('>II',path.read_bytes()[16:24]),(2,2))
   decoded=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-f','rawvideo','-pix_fmt','rgb24','-'])
   self.assertEqual(decoded,b'\x80'*12)

 def test_real_gpu_fast_and_general_models_with_chunking_and_cache(self):
  if __import__('os').environ.get('MONTAGE_SKIP_GPU_TEST') or not ai.available() or not ai.available('general'):self.skipTest('Bundled AI tools required')
  with tempfile.TemporaryDirectory() as temp:
   tmp=Path(temp);source=tmp/'source.mp4'
   subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=s=64x36:r=30:d=0.2','-c:v','libx264',str(source)],check=True)
   original=hashlib.sha256(source.read_bytes()).hexdigest()
   stream={'width':64,'height':36}
   for model in ('fast','general'):
    with patch.object(ai,'CHUNK_FRAMES',2):
     output,result=ai.upscale(source,tmp/model,(128,72),stream,model=model)
    self.assertTrue(result['applied']);self.assertEqual(result['frames'],6)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))['streams'][0]
    self.assertEqual((info['width'],info['height'],int(info['nb_frames'])),(128,72,6))
    self.assertEqual(len(list((tmp/model).glob('chunk-*.mp4'))),3)
    self.assertFalse(list((tmp/model).glob('*-frames/*.png')))
    with patch.object(ai,'checked_process',side_effect=AssertionError('Completed upscale repeated')):
     self.assertEqual(ai.upscale(source,tmp/model,(128,72),stream,model=model)[0],output)
   self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),original)

 def test_stopping_processing_terminates_child(self):
  import sys
  with tempfile.TemporaryDirectory() as temp:
   stop=threading.Event();stop.set()
   with self.assertRaisesRegex(RuntimeError,'Render stopped'):
    ai.checked_process([sys.executable,'-c','import time;time.sleep(10)'],Path(temp)/'log',stop)

if __name__=='__main__':unittest.main()
