"""Cross-platform desktop entry point. Shares the local service and data."""
from platform_support import data_dir, prepare_tools, renderer
import argparse
import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
import urllib.error
import urllib.request


def app_home():
    if getattr(sys, 'frozen', False):
        executable_dir = Path(sys.executable).resolve().parent
        # The shipped desktop-app directory lives beside server.py and the old data.
        return executable_dir.parent if (executable_dir.parent / 'server.py').is_file() else executable_dir
    return Path(__file__).resolve().parent


def read_state(base):
    try:
        with urllib.request.urlopen(base + '/api/state', timeout=2) as response:
            value = json.load(response)
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None
    if not (isinstance(value, dict) and isinstance(value.get('settings'), dict)
            and isinstance(value.get('jobs'), list) and isinstance(value.get('csrf'), str)
            and value.get('callback') == base + '/oauth/callback'):
        raise RuntimeError('This port is occupied by another application. Stop it before opening Montage Studio.')
    return value


def ensure_backend(base, home, data, timeout=30):
    state = read_state(base)
    if state is not None:
        if state.get('dataPath') and Path(state['dataPath']).resolve() != data.resolve():
            raise RuntimeError('Another Montage Studio data folder is already running. Close its service first.')
        return False
    command = [sys.executable, '--server'] if getattr(sys, 'frozen', False) else [sys.executable, str(home / 'server.py')]
    environment = os.environ.copy()
    environment['MONTAGE_DATA'] = str(data)
    # Do not inherit a test's data path or launch from a temporary bundle directory.
    with (data / 'service.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=home, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if read_state(base) is not None:
            return True
        if process.poll() is not None:
            raise RuntimeError('The local service could not start. See data/service.log for details.')
        time.sleep(.2)
    # Leave a possibly busy service alone, rather than interrupting work.
    raise RuntimeError('The service is taking too long to start. See data/service.log, then try opening the app again.')


def show_error(message):
    logging.exception(message)
    if os.name == 'nt':
        ctypes.windll.user32.MessageBoxW(None, str(message), 'Montage Studio', 0x10)
    else:
        print(message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--diagnostics', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    home = app_home()
    prepare_tools(Path(__file__).resolve().parent)
    data = data_dir(home)
    data.mkdir(parents=True, exist_ok=True)
    os.environ['MONTAGE_DATA'] = str(data)
    if args.server:
        runpy.run_module('server', run_name='__main__')
        return
    handler = RotatingFileHandler(data / 'desktop.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
    logging.basicConfig(handlers=[handler], level=logging.INFO)
    mutex = None
    try:
        if os.name == 'nt' and not args.diagnostics:
            kernel = ctypes.windll.kernel32
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            identity = hashlib.sha256(str(data).lower().encode()).hexdigest()[:24]
            mutex = kernel.CreateMutexW(None, False, 'Local\\MontageStudioDesktop-' + identity)
            if not mutex:
                raise RuntimeError('Could not create the desktop window lock.')
            if kernel.GetLastError() == 183:
                ctypes.windll.user32.MessageBoxW(None, 'Montage Studio is already open. Switch to its window on the taskbar.', 'Montage Studio', 0x40)
                return
        base = 'http://127.0.0.1:' + os.environ.get('MONTAGE_PORT', '8765')
        started = ensure_backend(base, home, data)
        logging.info('Desktop attached to local service; started=%s', started)
        import webview
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        # No Python API is exposed to pages. X links open in the user's regular browser.
        window = webview.create_window('Montage Studio', base, width=1400, height=940,
                                       min_size=(1000, 700), background_color='#10131b', text_select=True)
        if args.diagnostics:
            def verify_loaded():
                # Built-in smoke check only: no media viewing or publication.
                deadline = time.monotonic() + 20
                result = {'passed': False}
                while time.monotonic() < deadline:
                    result = window.evaluate_js("({title:document.title, heading:document.querySelector('h1')?.textContent, inputs:document.querySelectorAll('input').length})")
                    if result.get('heading') == 'Create montage':
                        result['passed'] = True
                        break
                    time.sleep(.2)
                args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
                args.diagnostics.write_text(json.dumps(result, indent=2), encoding='utf-8')
                window.destroy()
            window.events.loaded += verify_loaded
        # Closing the window leaves renders and local schedules running in the service.
        # The supplied Stop command explicitly shuts down that service.
        webview.start(gui=renderer(), private_mode=False, storage_path=str(data / 'desktop-profile'))
    except Exception as error:
        if args.diagnostics:
            args.diagnostics.write_text(json.dumps({'passed': False, 'error': str(error)}), encoding='utf-8')
        show_error(str(error) + '\n\nCheck the platform requirements in README.md. Your data is preserved.')
        raise SystemExit(1)
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == '__main__':
    main()
