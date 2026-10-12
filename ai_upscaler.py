"""Local Real-ESRGAN/Vulkan upscaling before loops/interpolation. No downloads at runtime."""
import json, math, os, shutil, struct, subprocess, time, zlib
from platform_support import ai_executable
from pathlib import Path

TOOLS=Path(__file__).resolve().parent/'tools'/'realesrgan'
MODELS={'fast':'realesr-animevideov3','general':'realesrgan-x4plus'}
CHUNK_FRAMES=32
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)

def available(model='fast'):
    if model not in MODELS:return False
    names=['realesr-animevideov3-x2','realesr-animevideov3-x4'] if model=='fast' else ['realesrgan-x4plus']
    return (ai_executable(TOOLS)).is_file() and all((TOOLS/'models'/f'{name}.{ext}').is_file() for name in names for ext in ('bin','param'))

def output_size(format_name,resolution='auto'):
    if format_name not in ('landscape','portrait','square'):raise ValueError('Invalid output format')
    if str(resolution) not in ('auto','720','1080','1440','2160'):raise ValueError('Choose automatic, 720p, 1080p, 1440p or 2160p output')
    short=(1080 if format_name=='square' else 720) if str(resolution)=='auto' else int(resolution)
    long=round(short*16/9)
    return (short,long) if format_name=='portrait' else (short,short) if format_name=='square' else (long,short)

def source_size(stream):
    width=float(stream.get('width',0));height=float(stream.get('height',0))
    if not math.isfinite(width) or not math.isfinite(height) or min(width,height)<=0:raise ValueError('Could not read source dimensions')
    try:
        a,b=map(float,stream.get('sample_aspect_ratio','1:1').split(':'));sar=a/b
        if not math.isfinite(sar) or sar<=0:sar=1
    except (ValueError,AttributeError,ZeroDivisionError):sar=1
    width=max(2,round(width*sar/2)*2);height=max(2,round(height/2)*2)
    angle=stream.get('tags',{}).get('rotate',0)
    for side in stream.get('side_data_list',[]):
        if 'rotation' in side:angle=side['rotation'];break
    if abs(abs(float(angle))%180-90)<1:width,height=height,width
    return width,height

def upscale_scale(size,target,model):
    if model not in MODELS:raise ValueError('Choose the fast or general AI model')
    required=min(target[0]/size[0],target[1]/size[1])
    if required<=1.02:return 0
    return 4 if model=='general' or required>2 else 2

def png_rgb(path,data,width,height):
    def block(name,payload):return struct.pack('>I',len(payload))+name+payload+struct.pack('>I',zlib.crc32(name+payload)&0xffffffff)
    stride=width*3
    rows=b''.join(b'\0'+data[y*stride:(y+1)*stride] for y in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+block(b'IHDR',struct.pack('>2I5B',width,height,8,2,0,0,0))+block(b'IDAT',zlib.compress(rows,1))+block(b'IEND',b''))

def checked_process(args,log,stop=None,tick=None,timeout=600):
    started=time.monotonic()
    with log.open('wb') as error:
        process=subprocess.Popen(args,stdout=error,stderr=error,creationflags=FLAGS)
        try:
            while process.poll() is None:
                if stop and stop.is_set():raise RuntimeError('Render stopped. Retry to resume.')
                if time.monotonic()-started>timeout:raise RuntimeError('AI processing timed out. Retry or disable AI upscaling.')
                if tick:tick()
                time.sleep(.2)
            if process.returncode:raise RuntimeError('AI upscaling failed. Check your Vulkan GPU driver or disable AI upscaling. '+log.read_text(errors='replace')[-1400:])
        finally:
            if process.poll() is None:process.kill();process.wait()

def _frame(stream,size):
    chunks=[];count=0
    while count<size:
        chunk=stream.read(size-count)
        if not chunk:
            if count:raise RuntimeError('Incomplete decoded video frame')
            return None
        chunks.append(chunk);count+=len(chunk)
    return b''.join(chunks)

def upscale(source,tmp,target,stream,seek=0,seconds=None,model='fast',progress=lambda message:None,stop=None):
    """Process bounded 32-frame batches; preserve 30 fps and every decoded frame."""
    size=source_size(stream);scale=upscale_scale(size,target,model)
    if not scale:return None,{'applied':False,'reason':'Source already meets output size'}
    if not available(model):raise ValueError('AI upscaler is missing. Use the complete Montage Studio desktop package.')
    tmp.mkdir(parents=True,exist_ok=True);final=tmp/'complete.mp4';manifest=tmp/'complete.json'
    if final.is_file() and manifest.is_file():return final,json.loads(manifest.read_text())
    incoming=tmp/'input-frames';enhanced=tmp/'enhanced-frames'
    for folder in (incoming,enhanced):folder.mkdir(exist_ok=True)
    args=['ffmpeg','-v','error','-threads','2']
    if seek:args+=['-ss',f'{float(seek):.6f}']
    args+=['-i',str(source)]
    if seconds is not None:args+=['-t',f'{float(seconds):.6f}']
    args+=['-an','-vf',f'scale={size[0]}:{size[1]},setsar=1,fps=30','-pix_fmt','rgb24','-f','rawvideo','pipe:1']
    started=time.monotonic();chunks=[];total=0
    with (tmp/'decode.log').open('wb') as errors:
        decoder=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=errors,creationflags=FLAGS)
        try:
            while True:
                block=[]
                for _ in range(CHUNK_FRAMES):
                    if stop and stop.is_set():raise RuntimeError('Render stopped. Retry to resume.')
                    frame=_frame(decoder.stdout,size[0]*size[1]*3)
                    if frame is None:break
                    block.append(frame)
                if not block:break
                index=len(chunks);chunk=tmp/f'chunk-{index:06}.mp4';count=tmp/f'chunk-{index:06}.json'
                cached=chunk.is_file() and count.is_file() and json.loads(count.read_text())==len(block)
                if not cached:
                    for folder in (incoming,enhanced):
                        for image in folder.glob('frame*.png'):image.unlink()
                    for i,frame in enumerate(block,1):png_rgb(incoming/f'frame{i:08}.png',frame,*size)
                    del frame;block_count=len(block);block.clear()
                    progress(f'AI upscaling: {total} frames complete · {model} model')
                    checked_process([str(ai_executable(TOOLS)),'-i',str(incoming),'-o',str(enhanced),'-m',str(TOOLS/'models'),'-n',MODELS[model],'-s',str(scale),'-t','256' if model=='fast' else '128','-j','1:2:2','-f','png'],tmp/'upscale.log',stop,
                        lambda:progress(f'AI upscaling: {total+len(list(enhanced.glob("frame*.png")))} frames · {model} model'))
                    images=list(enhanced.glob('frame*.png'))
                    if len(images)!=block_count:raise RuntimeError('AI upscaler did not return every frame; sources remain reserved for retry')
                    for i in range(1,block_count+1):
                        header=(enhanced/f'frame{i:08}.png').open('rb')
                        with header:dimensions=struct.unpack('>II',header.read(24)[16:24])
                        if dimensions!=(size[0]*scale,size[1]*scale):raise RuntimeError('AI upscaler returned incorrect frame dimensions')
                    staged=chunk.with_suffix('.partial.mp4');w,h=target
                    checked_process(['ffmpeg','-y','-v','error','-framerate','30','-i',str(enhanced/'frame%08d.png'),'-frames:v',str(block_count),'-an','-vf',f'scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1','-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-threads','4',str(staged)],tmp/'encode.log',stop)
                    staged.replace(chunk);count.write_text(json.dumps(block_count))
                    for folder in (incoming,enhanced):
                        for image in folder.glob('frame*.png'):image.unlink()
                else:block_count=len(block);block.clear()
                chunks.append(chunk);total+=block_count
            decoder.wait(timeout=30)
            if decoder.returncode:raise RuntimeError('Could not decode the selected excerpt: '+(tmp/'decode.log').read_text(errors='replace')[-1000:])
        finally:
            if decoder.poll() is None:decoder.kill();decoder.wait()
            decoder.stdout.close()
    if not total:raise ValueError('No frames available in the selected excerpt')
    listing=tmp/'chunks.txt';listing.write_text('\n'.join("file '"+p.as_posix().replace("'","'\\''")+"'" for p in chunks),encoding='utf-8')
    staged=tmp/'complete.partial.mp4'
    checked_process(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(listing),'-an','-c:v','copy',str(staged)],tmp/'concat.log',stop)
    staged.replace(final)
    result={'applied':True,'model':model,'scale':scale,'frames':total,'seconds':round(time.monotonic()-started,3)}
    manifest.write_text(json.dumps(result));return final,result
