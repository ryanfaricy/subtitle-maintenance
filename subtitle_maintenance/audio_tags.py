"""Explicit MKV language repair on a copy, never an implicit verification step."""
import os
from pathlib import Path
import shutil
import tempfile
import time
from . import media
from .common import digest, fingerprint, install, run


def tag_missing(video, apply, state, config):
    fp = fingerprint(video)
    info = media.inventory(video)
    index = media.single_untagged_audio(info['data'])
    result = dict(video=str(video))
    if index is None:
        return dict(result,status='NO_SAFE_AUDIO_TAG_CHANGE',detail='Requires exactly one untagged non-commentary audio stream')
    if video.suffix.lower() != '.mkv':
        return dict(result,status='REVIEW_CONTAINER',detail='Persistent audio tagging supports MKV only; use the assumption option for other formats')
    if time.time()-video.stat().st_mtime < config.get('min_age_minutes',10)*60:
        return dict(result,status='WAITING_FOR_STABILITY')
    if not apply:
        return dict(result,status='WOULD_TAG_AUDIO_ENGLISH',audio_index=index)
    st = video.stat()
    if shutil.disk_usage(video.parent).free < 2*st.st_size+64*1024*1024 or shutil.disk_usage(state).free < 2*st.st_size+64*1024*1024:
        raise ValueError('Insufficient space for video copy and retained original backup')
    sha = digest(video)
    with tempfile.TemporaryDirectory(prefix='.audio-tag-',dir=video.parent) as d:
        copy = Path(d)/video.name
        shutil.copy2(video,copy)
        if digest(copy) != sha:raise ValueError('Staging copy checksum mismatch')
        # Exactly one audio track: a1 is MKVToolNix's audio-track selector,
        # not ffprobe's global stream index. No remux or codec conversion.
        run(['mkvpropedit',copy,'--edit','track:a1','--set','language=eng'])
        after = media.inventory(copy)
        before_streams=info['data']['streams'];after_streams=after['data']['streams']
        def signature(s):
            return (s.get('index'),s.get('codec_type'),s.get('codec_name'),s.get('channels'),
                    s.get('width'),s.get('height'),s.get('disposition'),s.get('tags',{}).get('title'),
                    None if s.get('index')==index else s.get('tags',{}).get('language'))
        if (list(map(signature,before_streams)) != list(map(signature,after_streams))
                or after['audio_index'] != index or abs(after['duration']-info['duration'])>0.1):
            raise ValueError('Audio-tag stream verification failed; original untouched')
        receipt=install(copy,video,state/'backups',sha,video,fp)
        os.utime(video,ns=(st.st_atime_ns,st.st_mtime_ns))
        return dict(result,status='AUDIO_TAGGED_ENGLISH',receipt=receipt,
                    detail='Single untagged audio labelled English by user policy, not language detection')
