"""Platform paths and native desktop helpers. No private configuration."""
import os, shutil, subprocess, sys
from pathlib import Path

def data_dir(home):
    if os.environ.get('MONTAGE_DATA'):
        return Path(os.environ['MONTAGE_DATA']).expanduser().resolve()
    # Preserve data from an existing portable installation.
    if (home/'data'/'studio.sqlite3').is_file():return (home/'data').resolve()
    if sys.platform=='win32':base=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))/'Montage Studio'
    elif sys.platform=='darwin':base=Path.home()/'Library'/'Application Support'/'Montage Studio'
    else:base=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local'/'share'))/'montage-studio'
    return base.resolve()

def renderer():
    return 'edgechromium' if sys.platform=='win32' else 'cocoa' if sys.platform=='darwin' else 'qt'

def prepare_tools(root):
    folder=root/'tools'/'bin'
    if folder.is_dir():os.environ['PATH']=str(folder)+os.pathsep+os.environ.get('PATH','')

def ai_executable(folder):
    return folder/('realesrgan-ncnn-vulkan.exe' if sys.platform=='win32' else 'realesrgan-ncnn-vulkan')

def open_folder(path,reveal=False):
    path=Path(path)
    if sys.platform=='win32':
        if reveal:subprocess.Popen(['explorer.exe',f'/select,{path}'])
        else:os.startfile(str(path))
    elif sys.platform=='darwin':subprocess.Popen(['open',*(['-R'] if reveal else []),str(path)])
    else:subprocess.Popen(['xdg-open',str(path.parent if reveal else path)])

def choose_path(kind):
    """macOS dialogs run in AppleScript, avoiding Tk on a service thread."""
    if sys.platform=='darwin':
        script='POSIX path of (choose file with prompt "Choose music file")' if kind=='audio' else 'POSIX path of (choose folder with prompt "Choose folder")'
        result=subprocess.run(['osascript','-e',script],capture_output=True,text=True)
        if result.returncode and '-128' not in result.stderr:raise ValueError('Could not open the file chooser. Enter a path directly.')
        return result.stdout.strip() if result.returncode==0 else ''
    if sys.platform.startswith('linux'):
        if shutil.which('zenity'):
            result=subprocess.run(['zenity','--file-selection',*(['--directory'] if kind!='audio' else []),'--title=Choose music file' if kind=='audio' else '--title=Choose folder'],capture_output=True,text=True)
        elif shutil.which('kdialog'):
            result=subprocess.run(['kdialog','--getopenfilename' if kind=='audio' else '--getexistingdirectory',str(Path.home())],capture_output=True,text=True)
        else:raise ValueError('Install zenity or kdialog for Browse, or enter the path directly.')
        return result.stdout.strip() if result.returncode==0 else ''
    import tkinter as tk
    from tkinter import filedialog
    root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
    try:
        return filedialog.askopenfilename(title='Choose music file',filetypes=[('Audio files','*.wav *.mp3 *.flac *.m4a *.ogg'),('All files','*.*')]) if kind=='audio' else filedialog.askdirectory(title='Choose folder')
    finally:root.destroy()
