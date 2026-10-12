"""Local beat analysis and frame-accurate, capacity-constrained edit planning.

Uses FFmpeg and the standard library, including in the frozen desktop build.
"""
from array import array
from bisect import bisect_left, bisect_right
import math
import subprocess
import sys


def detect_beats(path, duration, bpm=0):
    if bpm:
        if not math.isfinite(bpm) or not 40 <= bpm <= 240:
            raise ValueError('Beat BPM must be 40–240, or 0 for automatic detection')
        interval=60/bpm
        return {'bpm':bpm,'source':'manual','times':[i*interval for i in range(1,math.ceil(duration/interval))]}
    result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vn','-ac','1','-ar','8000','-af','highpass=f=40,lowpass=f=3000','-f','s16le','-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('Could not analyse the audio beats: '+result.stderr.decode(errors='replace')[-500:])
    samples=array('h');samples.frombytes(result.stdout)
    if sys.byteorder!='little':samples.byteswap()
    hop=80;step=hop/8000
    energy=[sum(abs(x) for x in samples[i:i+hop])/hop for i in range(0,len(samples)-hop+1,hop)]
    onset=[max(0,e-sum(energy[max(0,i-4):i])/min(i,4)) if i else e for i,e in enumerate(energy)]
    peak=max(onset,default=0)
    if peak<10 or len(onset)<100:
        raise ValueError('No clear beat could be detected. Enter the track BPM or turn off Sync clip changes to music beats.')
    # Autocorrelation estimates tempo; onset peaks retain the actual beat phase.
    scores=[]
    for lag in range(33,min(101,len(onset)//2)):
        score=sum(onset[i]*onset[i-lag] for i in range(lag,len(onset)))/(len(onset)-lag)
        scores.append((score,lag))
    best,lag=max(scores)
    # A repeated pulse often gives an equally strong half-tempo correlation.
    # Prefer its fundamental when the shorter period has comparable support.
    shorter=[(score,period) for score,period in scores if abs(period-lag/2)<=2]
    if shorter:
        fundamental,period=max(shorter)
        if fundamental>=best*.85:lag=period
    if best<peak*peak*.0005:
        raise ValueError('The track has no stable detected pulse. Enter its BPM or turn off beat sync.')
    peaks=[i for i in range(1,len(onset)-1) if onset[i]>=peak*.12 and onset[i]>onset[i-1] and onset[i]>=onset[i+1]]
    if len(peaks)<3:
        raise ValueError('Too few beats were detected. Enter the track BPM or turn off beat sync.')
    # Pick the phase whose pulse train receives the strongest attacks.
    phase=max(range(lag),key=lambda phase:sum(max(onset[max(0,i-2):min(len(onset),i+3)],default=0) for i in range(phase,len(onset),lag)))
    times=[]
    for i in range(phase,len(onset),lag):
        nearby=[j for j in peaks if abs(j-i)<=lag*.2]
        located=max(nearby,key=lambda j:onset[j]) if nearby else i
        t=located*step
        if 0<t<duration and (not times or t-times[-1]>.2):times.append(t)
    return {'bpm':round(60/(lag*step),2),'source':'detected','times':times}


def fit_lengths(caps, target, fps, options, sequence_duration, stills=()):
    """Keep each selected clip visible and shorten video clips to fill the song."""
    minimum=1/fps
    if sequence_duration(caps,options)+1e-6<target:
        raise ValueError('Not enough unused footage to match the audio track')
    def scaled(factor):
        return [cap if i in stills else max(minimum,math.ceil(cap*factor*fps-1e-8)/fps) for i,cap in enumerate(caps)]
    if sequence_duration(scaled(0),options)>target+minimum:
        raise ValueError('Too many clips or stills for this track. Reduce the clip/still count or still duration.')
    lo,hi=0,1
    for _ in range(45):
        mid=(lo+hi)/2
        if sequence_duration(scaled(mid),options)<target:lo=mid
        else:hi=mid
    return scaled(hi)


def beat_timeline(caps, ideals, target, beat_times, fps, fade=0, flexible_end=False):
    """All transition centres land on beats; no source capacity is exceeded."""
    capframes=[max(1,math.floor(c*fps+1e-6)) for c in caps]
    idealframes=[max(1,round(c*fps)) for c in ideals]
    # Even overlaps put each transition centre on an integer output frame.
    overlaps=[max(0,min(round(fade*fps),a//2,b//2)//2*2) for a,b in zip(idealframes,idealframes[1:])]
    halves=[0]+[f//2 for f in overlaps]+[0]
    capacities=[cap-halves[i]-halves[i+1] for i,cap in enumerate(capframes)]
    end=math.ceil(target*fps-1e-7)
    points=sorted({round(t*fps) for t in beat_times if 0<round(t*fps)<end})
    if len(points)<len(caps)-1:
        raise ValueError('Not enough beats for the selected clip/still count. Use fewer clips, a longer track or a higher BPM.')
    ideals_at=[];elapsed=0
    for i,length in enumerate(idealframes[:-1]):
        elapsed+=length-halves[i]-halves[i+1];ideals_at.append(elapsed)
    layers=[];previous={0:0}
    for index in range(len(caps)-1):
        current={};parents={}
        for start,cost in previous.items():
            # Remaining segments must also have at least one visible frame.
            low=start+1 if flexible_end else max(start+1,end-sum(capacities[index+1:]))
            high=min(start+capacities[index],end-(len(caps)-index-1))
            for stop in points[bisect_left(points,low):bisect_right(points,high)]:
                candidate=cost+(stop-ideals_at[index])**2
                if stop not in current or candidate<current[stop]:current[stop]=candidate;parents[stop]=start
        if not current:
            raise ValueError('Unused clips cannot reach the nearby beats without repeating footage. Add clips, enable ping-pong, increase clip duration or turn off beat sync.')
        layers.append(parents);previous=current
    candidates=[(cost+(max(0,end-start-capacities[-1]))**2,start) for start,cost in previous.items() if end>start and (flexible_end or end-start<=capacities[-1])]
    if not candidates:raise ValueError('Unused clips cannot cover the track ending on this beat grid. Add clips or enable ping-pong.')
    _,stop=min(candidates)
    if flexible_end:end=min(end,stop+capacities[-1])
    boundaries=[stop,end]
    for parents in reversed(layers):
        stop=parents[stop];boundaries.append(stop)
    boundaries=sorted(set(boundaries))
    lengths=[(b-a+halves[i]+halves[i+1])/fps for i,(a,b) in enumerate(zip(boundaries,boundaries[1:]))]
    return {'lengths':lengths,'overlaps':[f/fps for f in overlaps],'cuts':[t/fps for t in boundaries[1:-1]],'duration':end/fps}
