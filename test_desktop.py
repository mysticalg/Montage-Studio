import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('desktop', Path(__file__).with_name('desktop.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class DesktopTests(unittest.TestCase):
    def test_frozen_app_reuses_adjacent_existing_data_home(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / 'server.py').write_text('')
            (home / 'desktop-app').mkdir()
            with patch.object(d.sys, 'frozen', True, create=True), patch.object(d.sys, 'executable', str(home / 'desktop-app' / 'Montage Studio.exe')):
                self.assertEqual(d.app_home(), home.resolve())

    def test_existing_service_does_not_launch_or_recover_jobs(self):
        data = Path('data').resolve()
        state = {'dataPath': str(data), 'jobs': [{'status': 'rendering'}]}
        with patch.object(d, 'read_state', return_value=state), patch.object(d.subprocess, 'Popen') as spawn:
            self.assertFalse(d.ensure_backend('http://127.0.0.1:8765', Path('.'), data))
            spawn.assert_not_called()

    def test_different_data_service_is_rejected(self):
        with patch.object(d, 'read_state', return_value={'dataPath': str(Path('other').resolve())}), patch.object(d.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'Another Montage Studio data folder'):
                d.ensure_backend('http://127.0.0.1:8765', Path('.'), Path('data'))
            spawn.assert_not_called()

    def test_unrelated_service_is_rejected(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'settings': {}}).encode()
        with patch.object(d.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'another application'):
                d.read_state('http://127.0.0.1:8765')

    def test_new_service_is_hidden_and_uses_same_data(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder).resolve()
            with patch.object(d, 'read_state', side_effect=[None, {'jobs': []}]), patch.object(d.subprocess, 'Popen') as spawn:
                self.assertTrue(d.ensure_backend('http://127.0.0.1:8765', data, data))
                self.assertEqual(spawn.call_args.kwargs['env']['MONTAGE_DATA'], str(data))
                self.assertEqual(spawn.call_args.kwargs['creationflags'], getattr(d.subprocess,'CREATE_NO_WINDOW',0) if d.os.name=='nt' else 0)


if __name__ == '__main__':
    unittest.main()
