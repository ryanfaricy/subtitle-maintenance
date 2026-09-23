import json
from pathlib import Path
import re
import os
from .common import run

VIDEO={'.mkv','.mp4','.m4v','.avi','.wmv','.asf','.mov','.mpg','.mpeg','.m2ts','.ts','.webm','.vob','.flv','.ogv','.divx','.mts'}
TEXT={'subrip','srt','ass','ssa','mov_text','webvtt','text','ttml','hdmv_text_subtitle','eia_608','eia_708'}
BITMAP={'dvd_subtitle','hdmv_pgs_subtitle','dvb_subtitle','xsub'}
SIDECAR={'.srt','.ass','.ssa','.vtt'}

def english(value):
    value=str(value).strip().lower().replace('_','-')
    return value in {'en','eng','english'} or value.startswith('en-')

def forced(stream):
    return bool(stream.get('disposition',{}).get('forced')) or bool(re.search(r'\b(forced|signs|songs)\b',stream.get('tags',{}).get('title',''),re.I))

def english_stream(stream):
    # Unknown language isn't silently relabelled from a vague track name.
    return english(stream.get('tags',{}).get('language',''))

def probe(path):
    return json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',path]))

def single_untagged_audio(data):
    """User opt-in only: one audio stream, absent/und language, no commentary hint.

    This is an assumption, NOT speech-language detection. Multiple tracks and
    explicit non-English languages must never be silently relabelled.
    """
    tracks=[s for s in data.get('streams',[]) if s.get('codec_type')=='audio']
    if len(tracks)!=1:return None
    s=tracks[0];tags=s.get('tags',{})
    if str(tags.get('language','')).strip().lower() not in {'','und'}:return None
    if re.search(r'commentary|description',tags.get('title',''),re.I):return None
    return s['index']

def inventory(video):
    data=probe(video);streams=data.get('streams',[])
    subs=[s for s in streams if s.get('codec_type')=='subtitle']
    full=lambda s:english_stream(s) and not forced(s)
    audio=[s for s in streams if s.get('codec_type')=='audio' and english_stream(s)
           and not re.search(r'commentary|description',s.get('tags',{}).get('title',''),re.I)]
    default=[s for s in audio if s.get('disposition',{}).get('default')]
    selected=(default or audio)
    return dict(data=data,duration=float(data['format']['duration']),
                text=[s for s in subs if full(s) and s.get('codec_name') in TEXT],
                bitmap=[s for s in subs if full(s) and s.get('codec_name') in BITMAP],
                audio_index=selected[0]['index'] if len(selected)==1 else None)

def sidecars(video):
    found=[];ambiguous=[]
    for p in sorted(video.parent.iterdir()):
        if not p.is_file() or p.is_symlink() or p.suffix.lower() not in SIDECAR:continue
        if p.stem==video.stem:
            ambiguous.append(p);continue
        prefix=video.stem+'.'
        if not p.stem.startswith(prefix):continue
        tags=re.split(r'[. _\-]+',p.stem[len(prefix):].lower())
        if set(tags)&{'forced','backup','old','tmp','temp','corrected','staging'}:continue
        if any(english(t) for t in tags):found.append(p)
    return found,ambiguous

def videos(paths,state_dir):
    seen=set();state_dir=state_dir.resolve()
    for raw in paths:
        root=Path(raw).expanduser()
        if not root.exists():raise ValueError(f'Path does not exist: {root}')
        def walk():
            for directory,dirs,files in os.walk(root,followlinks=False):
                dirs[:]=sorted(d for d in dirs if not d.startswith('.') and not (Path(directory)/d).is_symlink()
                               and (Path(directory)/d).resolve()!=state_dir)
                for name in sorted(files):yield Path(directory)/name
        for p in ([root] if root.is_file() else walk()):
            if p.is_symlink() or not p.is_file() or p.suffix.lower() not in VIDEO:continue
            resolved=p.resolve()
            if state_dir==resolved or state_dir in resolved.parents:continue
            if any(part.startswith('.') for part in p.relative_to(root.parent if root.is_file() else root).parts):continue
            if resolved not in seen:
                seen.add(resolved);yield resolved
