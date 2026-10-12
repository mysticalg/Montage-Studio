"""Refresh top-level release notices without changing any tested application bytes."""
from pathlib import Path
import hashlib,shutil,subprocess,sys,tarfile,zipfile

root=Path(__file__).resolve().parents[1];incoming=root/'incoming';stage=root/'build'/'notice-stage';stage.mkdir(parents=True,exist_ok=True)
system='macos' if sys.platform=='darwin' else 'windows' if sys.platform=='win32' else 'linux'
archive=next(incoming.glob('*.tar.gz' if system=='linux' else '*.zip'))
if system=='macos':subprocess.run(['ditto','-x','-k',str(archive),str(stage)],check=True)
elif system=='windows':
    with zipfile.ZipFile(archive) as bundle:bundle.extractall(stage)
else:
    with tarfile.open(archive) as bundle:bundle.extractall(stage,filter='data')
package=next(p for p in stage.iterdir() if p.is_dir() and p.name.startswith('Montage-Studio-'))
exe=package/'Montage Studio.app'/'Contents'/'MacOS'/'Montage Studio' if system=='macos' else package/'Montage Studio'/('Montage Studio.exe' if system=='windows' else 'Montage Studio')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
original=digest(exe)
for name in ('README.md','LICENSE','LICENSE-Python.txt','THIRD_PARTY_NOTICES.txt'):shutil.copy2(root/name,package/name)
assert digest(exe)==original,'Executable changed during notice refresh'
out=root/'release';out.mkdir(exist_ok=True)
if system=='macos':
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(package),str(out/archive.name)],check=True)
    subprocess.run(['hdiutil','create','-volname','Montage Studio','-srcfolder',str(package),'-ov','-format','UDZO',str(out/(package.name+'.dmg'))],check=True)
elif system=='windows':shutil.make_archive(str(out/package.name),'zip',stage,package.name)
else:
    with tarfile.open(out/archive.name,'w:gz') as bundle:bundle.add(package,arcname=package.name)
for asset in out.iterdir():
    if asset.is_file():
        (out/(asset.name+'.sha256')).write_text(digest(asset)+'  '+asset.name+'\n')
print('Final notices included; tested executable unchanged:',original)
