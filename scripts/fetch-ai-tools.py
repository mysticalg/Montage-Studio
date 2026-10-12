"""Fetch official upstream native tools at build time, retain provenance/licenses."""
from pathlib import Path
import hashlib, json, os, shutil, sys, tempfile, urllib.request, zipfile

root=Path(__file__).resolve().parents[1];target=root/'tools'/'realesrgan'
platform='windows' if sys.platform=='win32' else 'macos' if sys.platform=='darwin' else 'ubuntu'
url=f'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-{platform}.zip'
target.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    archive=Path(temp)/'ai.zip';urllib.request.urlretrieve(url,archive)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    if platform=='windows' and digest!='abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d':raise RuntimeError('Upstream Windows tool checksum changed')
    wanted={'realesrgan-ncnn-vulkan.exe','realesrgan-ncnn-vulkan','vcomp140.dll','README_windows.md','README_macos.md','README_ubuntu.md'}
    models={'realesr-animevideov3-x2','realesr-animevideov3-x4','realesrgan-x4plus'}
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path=Path(entry.filename)
            if path.name in wanted or (path.parent.name=='models' and path.stem in models and path.suffix in ('.bin','.param')):
                out=target/('models' if path.parent.name=='models' else '')/path.name
                out.parent.mkdir(exist_ok=True);out.write_bytes(bundle.read(entry))
    executable=target/('realesrgan-ncnn-vulkan.exe' if platform=='windows' else 'realesrgan-ncnn-vulkan')
    if not executable.is_file():raise RuntimeError('Upstream archive has no executable')
    if platform!='windows':executable.chmod(0o755)
    (target/'provenance.json').write_text(json.dumps({'source':url,'archiveSha256':digest,'files':{str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file() and p.name!='provenance.json'}},indent=2))
    print(f'Bundled official {platform} AI runtime: {digest}')
