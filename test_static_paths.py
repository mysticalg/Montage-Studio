"""macOS bundles use symlinks between Frameworks and Resources."""
import tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
import test_server
s=test_server.s

class StaticPathsTests(unittest.TestCase):
 def test_linked_frontend_is_served_but_traversal_is_blocked(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp).resolve();real=root/'Resources';real.mkdir();(real/'index.html').write_text('<h1>Montage test</h1>')
   frameworks=root/'Frameworks';(frameworks/'frontend').mkdir(parents=True)
   try:(frameworks/'frontend'/'dist').symlink_to(real,target_is_directory=True)
   except OSError:self.skipTest('Symlinks unavailable in this test environment')
   http=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);port=http.server_address[1]
   thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
   try:
    with patch.object(s,'ROOT',frameworks),patch.object(s,'PORT',port):
     base=f'http://127.0.0.1:{port}'
     with urllib.request.urlopen(base+'/') as r:self.assertEqual(r.read(),b'<h1>Montage test</h1>')
     with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(base+'/../private.txt')
     self.assertEqual(caught.exception.code,403);caught.exception.close()
   finally:http.shutdown();http.server_close();thread.join(timeout=2)
