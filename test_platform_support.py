import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import platform_support as p

class PlatformTests(unittest.TestCase):
 def test_data_and_renderer(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True),patch.object(p.Path,'home',return_value=Path('home').resolve()):
   home=Path(tmp)
   for system,engine in [('win32','edgechromium'),('darwin','cocoa'),('linux','qt')]:
    with patch.object(p.sys,'platform',system):
     self.assertEqual(p.renderer(),engine);self.assertNotEqual(p.data_dir(home),home/'data')
   (home/'data').mkdir();(home/'data'/'studio.sqlite3').touch()
   self.assertEqual(p.data_dir(home),home/'data')
   with patch.dict(os.environ,{'MONTAGE_DATA':str(home/'custom')}):self.assertEqual(p.data_dir(home),home/'custom')
 def test_folder_reveal(self):
  with patch.object(p.subprocess,'Popen') as spawn:
   with patch.object(p.sys,'platform','darwin'):
    p.open_folder(Path('/tmp/video.mp4'),True);spawn.assert_called_with(['open','-R',str(Path('/tmp/video.mp4'))])
   with patch.object(p.sys,'platform','linux'):
    p.open_folder(Path('/tmp/video.mp4'),True);spawn.assert_called_with(['xdg-open',str(Path('/tmp'))])
 def test_ai_executable_platform(self):
  for system,name in [('win32','realesrgan-ncnn-vulkan.exe'),('darwin','realesrgan-ncnn-vulkan'),('linux','realesrgan-ncnn-vulkan')]:
   with patch.object(p.sys,'platform',system):self.assertEqual(p.ai_executable(Path('tools')).name,name)
 def test_mac_and_linux_dialogs(self):
  from types import SimpleNamespace
  with patch.object(p.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='/tmp/music.wav\n',stderr='')) as run:
   with patch.object(p.sys,'platform','darwin'):
    self.assertEqual(p.choose_path('audio'),'/tmp/music.wav');self.assertEqual(run.call_args.args[0][0],'osascript')
   with patch.object(p.sys,'platform','linux'),patch.object(p.shutil,'which',return_value='/usr/bin/zenity'):
    self.assertEqual(p.choose_path('folder'),'/tmp/music.wav');self.assertIn('--directory',run.call_args.args[0])
   with patch.object(p.sys,'platform','linux'),patch.object(p.shutil,'which',return_value=None):
    with self.assertRaisesRegex(ValueError,'enter the path'):p.choose_path('audio')
