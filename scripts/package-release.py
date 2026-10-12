"""Package a clean native build with extension, docs and checksums, never local data."""
import hashlib, json, os, platform, shutil, subprocess, sys, tarfile, zipfile
import importlib.metadata
from pathlib import Path

root=Path(__file__).resolve().parents[1];out=root/'release';out.mkdir(exist_ok=True)
arch='arm64' if platform.machine().lower() in ('arm64','aarch64') else 'x64'
system='windows' if sys.platform=='win32' else 'macos' if sys.platform=='darwin' else 'linux'
stage=out/f'Montage-Studio-{system}-{arch}';stage.mkdir(exist_ok=True)
app=root/'dist'/'Montage Studio.app' if system=='macos' else root/'dist'/'Montage Studio'
shutil.copytree(app,stage/app.name,dirs_exist_ok=True,symlinks=True)
extension=stage/'x-extension';extension.mkdir(exist_ok=True)
for name in ('manifest.json','background.js','content.js','popup.html','popup.css','popup.js','README.md'):
    shutil.copy2(root/'x-extension'/name,extension/name)
for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.txt'):shutil.copy2(root/name,stage/name)
notices=stage/'runtime-licenses';notices.mkdir(exist_ok=True)
for distribution in importlib.metadata.distributions():
    name=distribution.metadata.get('Name','dependency')
    for file in distribution.files or []:
        if 'license' in str(file).lower() or 'copying' in str(file).lower():
            source=Path(distribution.locate_file(file))
            if source.is_file() and source.stat().st_size<2_000_000:
                target=notices/name/str(file).replace('..','_')
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    (notices/(name+'.txt')).write_text('Name: '+name+'\nVersion: '+distribution.version+'\nLicense: '+str(distribution.metadata.get('License','See included dependency license files'))+'\n',encoding='utf-8')
if system=='linux':
    launcher=stage/'Launch Montage Studio.sh'
    launcher.write_text('#!/bin/sh\ncd "$(dirname "$0")"\nexec "./Montage Studio/Montage Studio" "$@"\n');launcher.chmod(0o755)
    shutil.copy2(root/'scripts'/'install-linux-dependencies.sh',stage/'install-linux-dependencies.sh')
if system=='windows':(stage/'Launch Montage Studio.cmd').write_text('@echo off\r\nstart "" "%~dp0Montage Studio\\Montage Studio.exe"\r\n')
if any(p.name in ('studio.sqlite3','credentials.dpapi','extension-pair.json') or p.name=='data' for p in stage.rglob('*')):raise RuntimeError('Private data in release')
if system=='macos':
    dmg=out/f'{stage.name}.dmg'
    # Both an ordinary archive and a mountable image; copying to Applications is optional.
    os.symlink('/Applications',stage/'Applications',target_is_directory=True)
    subprocess.run(['hdiutil','create','-volname','Montage Studio','-srcfolder',str(stage),'-ov','-format','UDZO',str(dmg)],check=True)
    archive=out/f'{stage.name}.zip'
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(stage),str(archive)],check=True)
elif system=='windows':
    archive=Path(shutil.make_archive(str(out/stage.name),'zip',stage.parent,stage.name))
else:
    archive=out/f'{stage.name}.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:tar.add(stage,arcname=stage.name)
for asset in out.iterdir():
    if asset.is_file() and asset.suffix in ('.zip','.dmg','.gz'):
        (out/(asset.name+'.sha256')).write_text(hashlib.sha256(asset.read_bytes()).hexdigest()+'  '+asset.name+'\n')
print('Packaged',archive)
