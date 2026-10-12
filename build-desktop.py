"""Native desktop builds; run on the target OS. Dependencies stay in release/."""
from pathlib import Path
import os, subprocess, sys

root=Path(__file__).resolve().parent
scratch=root/'build'
command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
         '--name','Montage Studio','--distpath',str(root/'dist'),'--workpath',str(scratch/'work'),
         '--specpath',str(scratch),'--add-data',str(root/'frontend'/'dist')+os.pathsep+'frontend/dist',
         '--add-data',str(root/'tools')+os.pathsep+'tools','--collect-all','webview',
         '--collect-all','playwright','--hidden-import','server','--hidden-import','platform_support']
if sys.platform!='win32':
    command+=['--collect-all','keyring','--collect-submodules','keyring.backends']
excluded=['numpy','matplotlib','torch','cefpython3','PySide2','PyQt6','PyQt5']
if sys.platform!='linux':excluded+=['PySide6']
if sys.platform=='darwin':command+=['--osx-bundle-identifier','io.github.mysticalg.montagestudio']
for module in excluded:command+=['--exclude-module',module]
command.append(str(root/'desktop.py'))
subprocess.run(command,cwd=root,check=True)
