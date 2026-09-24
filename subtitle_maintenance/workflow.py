import json
from pathlib import Path
import re
import shutil
import tempfile
import time
from . import media,native,subtitles
from .common import digest,fingerprint,install,run
from .providers import identity,DownloadBudgetReached

def verify(video,candidate,info,config,state,drift_render=False):
    cues=subtitles.read(candidate)
    full=native.words(native.transcript(video,info,None,None,config,state))
    def samples(fractions=(.15,.5,.85)):
        for fraction in fractions:
            start=round(info['duration']*fraction)
            if start<15 or start+55>info['duration']:raise ValueError('Video too short for independent verification')
            data=native.transcript(video,info,start-15,70,config,state)
            yield start,native.words(data,start-15)
    if drift_render:
        from . import drift
        return subtitles.validate(cues,full,info['duration'],lambda:samples(drift.SAMPLES),0,5)
    check=subtitles.validate(cues,full,info['duration'],samples,config.get('max_shift',60))
    if check['passed'] or not config.get('allow_drift_correction') or check.get('full',{}).get('passed'):return check
    from . import drift
    if drift.combined_episode(video.stem):
        check['drift_rejection']='Combined episode excluded';return check
    try:
        fitted=drift.fit(cues,full,info['duration'])
        corrected=[dict(c,start=c['start']*fitted['scale']+fitted['offset'],end=c['end']*fitted['scale']+fitted['offset']) for c in cues]
        verified=subtitles.validate(corrected,full,info['duration'],lambda:samples(drift.SAMPLES),0,5)
        if not verified['passed'] or verified['offset']!=0:
            check.update(drift_rejection='Corrected candidate failed unchanged verification gates',drift_fit=fitted,drift_verification=verified)
            return check
        return dict(verified,offset=fitted['offset'],scale=fitted['scale'],drift_fit=fitted)
    except ValueError as e:
        check['drift_rejection']=str(e);return check

def prepare(video,source,info,config,state,folder):
    candidate=source
    if source.suffix.lower()!='.srt':
        candidate=folder/(digest(source)+'.converted.srt')
        run(['ffmpeg','-nostdin','-v','error','-i',source,'-map','0:0','-c:s','srt','-y',candidate])
    check=verify(video,candidate,info,config,state)
    if not check['passed']:return None,check
    if check['offset'] or check.get('scale',1)!=1:
        corrected=folder/(digest(candidate)+'.corrected.srt')
        subtitles.shifted(candidate,corrected,check['offset'],check.get('scale',1))
        rendered=verify(video,corrected,info,dict(config,allow_drift_correction=False),state,
                        drift_render=check.get('scale',1)!=1)
        if not rendered['passed'] or rendered['offset']!=0:raise ValueError('Rendered correction failed revalidation')
        check['rendered_verification']=rendered;candidate=corrected
    return candidate,check

def ocr(video,info,config,state,apply):
    if video.suffix.lower()!='.mkv':return dict(status='REVIEW_CONTAINER',detail='OCR embedding currently supports MKV only')
    if not apply:return dict(status='WOULD_OCR',detail='Retain bitmap; add non-forced English text; no provider sidecars embedded')
    original_fp=fingerprint(video);original_sha=digest(video);st=video.stat()
    if shutil.disk_usage(video.parent).free<3*st.st_size+512*1024*1024:
        raise ValueError('Insufficient free media space for safe OCR staging')
    if shutil.disk_usage(state).free<st.st_size+512*1024*1024:raise ValueError('Insufficient backup space')
    with tempfile.TemporaryDirectory(prefix='.subtitle-ocr-',dir=video.parent) as d:
        copy=Path(d)/video.name;shutil.copy2(video,copy)
        if digest(copy)!=original_sha:raise ValueError('OCR input copy verification failed')
        print('    OCR on isolated copy; original is not touched during conversion',flush=True)
        worker=Path(__file__).with_name('bitmap_ocr.py')
        run([config['python'],worker,copy,'--languages','eng','--apply'],7200)
        after=media.inventory(copy)
        oldstreams=info['data']['streams'];newstreams=after['data']['streams']
        from collections import Counter
        signature=lambda s:(s.get('codec_type'),s.get('codec_name'),s.get('tags',{}).get('language'),s.get('channels'),s.get('width'),s.get('height'))
        originals=Counter(signature(s) for s in oldstreams);outputs=Counter(signature(s) for s in newstreams)
        if originals-outputs or not after['text'] or abs(after['duration']-info['duration'])>1:
            raise ValueError('OCR remux inventory/duration verification failed')
        receipt=install(copy,video,state/'backups',original_sha,video,original_fp)
        import os
        os.utime(video,ns=(st.st_atime_ns,st.st_mtime_ns))
        return dict(status='OCR_EMBEDDED',receipt=receipt,detail='Container verified; OCR accuracy is not guaranteed')

def process(video,args,config,state,provider):
    info=media.inventory(video);fp=fingerprint(video)
    sidecars,unknown=media.sidecars(video)
    record=dict(video=str(video),video_fingerprint=fp,sidecars=[str(p) for p in sidecars],attempts=[])
    if time.time()-video.stat().st_mtime<config.get('min_age_minutes',10)*60:
        return dict(record,status='WAITING_FOR_STABILITY')
    if args.audio_stream is not None:
        if not any(s.get('codec_type')=='audio' and s['index']==args.audio_stream for s in info['data']['streams']):raise ValueError('Requested audio stream is not present')
        info['audio_index']=args.audio_stream
    if info['audio_index'] is None and config.get('assume_single_untagged_english'):
        info['audio_index']=media.single_untagged_audio(info['data'])
        if info['audio_index'] is not None:
            record['audio_assumption']='Single untagged audio treated as English by user option; file unchanged'
            print('    '+record['audio_assumption'],flush=True)
    if info['text']:return dict(record,status='TRUSTED_EMBEDDED',detail='Full non-forced English text; not dialogue verified')
    if args.scan_only:
        return dict(record,status='NEEDS_OCR' if info['bitmap'] else 'NEEDS_SIDECAR_CHECK' if sidecars else 'NEEDS_DOWNLOAD',unknown_sidecars=[str(p) for p in unknown])
    if info['bitmap']:return dict(record,**ocr(video,info,config,state,args.apply))
    if info['audio_index'] is None:
        reason=('Single audio track has no language tag; use --assume-single-untagged-english if appropriate'
                if media.single_untagged_audio(info['data']) is not None else 'No unambiguous English dialogue track (missing, multiple, or non-English tracks)')
        return dict(record,status='REVIEW_AUDIO_LANGUAGE',detail=reason)
    selected=next(s for s in info['data']['streams'] if s['index']==info['audio_index'])
    record['selected_audio']=dict(index=selected['index'],codec=selected.get('codec_name'),
                                  channels=selected.get('channels'),language=selected.get('tags',{}).get('language','und'),
                                  default=bool(selected.get('disposition',{}).get('default')))
    print(f"    Analysis audio: stream {selected['index']} ({selected.get('codec_name','unknown')}, {selected.get('channels','?')} channels)",flush=True)
    folder=state/'staging'/__import__('hashlib').sha256(str(video).encode()).hexdigest()[:20]
    folder.mkdir(parents=True,exist_ok=True)
    originals={str(p):digest(p) for p in sidecars}
    for source in sidecars:
        if originals[str(source)] in config.get('human_approved_sha256',[]):
            return dict(record,status='HUMAN_APPROVED',subtitle=str(source),installed_sha256=originals[str(source)])
        print('    Checking '+source.name,flush=True)
        try:
            candidate,check=prepare(video,source,info,config,state,folder)
            record['attempts'].append(dict(source=str(source),check=check))
            if candidate:
                if check['offset']==0 and check.get('scale',1)==1:return dict(record,status='VERIFIED',subtitle=str(source),installed_sha256=digest(source))
                if source.suffix.lower()!='.srt':
                    return dict(record,status='REVIEW_FORMAT',detail='Verified corrected SRT staged; preserve original styled sidecar',candidate=str(candidate))
                if args.apply:
                    receipt=install(candidate,source,state/'backups',originals[str(source)],video,fp)
                    return dict(record,status='RETIMED',receipt=receipt,subtitle=str(source),installed_sha256=digest(source))
                return dict(record,status='WOULD_RETIME',candidate=str(candidate),offset=check['offset'],scale=check.get('scale',1),detail=f"Timing scale {check.get('scale',1):.6f}, offset {check['offset']:+.3f}s")
        except Exception as e:record['attempts'].append(dict(source=str(source),error=str(e)))
    if args.no_download:return dict(record,status='REVIEW',detail='No verified sidecar; provider search disabled')
    if unknown:return dict(record,status='REVIEW_UNTAGGED_SIDECAR',detail='Unlabelled sidecar present; language is not assumed')
    ident=identity(video,config,args.identity)
    if config.get('map_episode_titles') and ident.get('season') is not None:
        from .episode_mapping import map_identity,MappingReview
        record['library_identity']=dict(ident)
        try:ident,evidence=map_identity(ident,state)
        except MappingReview as e:return dict(record,status='REVIEW_EPISODE_MAPPING',detail=str(e))
        record['episode_mapping']=evidence
        print(f"    Title mapping: S{evidence['original_season']:02}E{evidence['original_episode']:02} -> S{ident['season']:02}E{ident['episode']:02} ({evidence['title']}; TVmaze)",flush=True)
    prefer_xl=bool(re.search(r'\b(XL|UNCUT)\b',ident.get('release',''),re.I)) or ('QI' in video.name and info['duration']>=2400)
    record['identity']=ident
    if record.get('episode_mapping'):
        from .episode_mapping import search_both
        candidates,rejected,search_errors=search_both(provider,record['library_identity'],ident,state,prefer_xl)
        record.update(rejected_candidates=rejected,provider_search_errors=search_errors)
        print(f'    Combined searches: {len(candidates)} unique eligible candidates; {len(rejected)} wrong-title results excluded',flush=True)
        for error in search_errors:print('    Search incomplete: '+error['numbering']+': '+error['error'],flush=True)
    else:candidates=provider.candidates(ident,prefer_xl)
    record['available_candidates']=len(candidates)
    record['candidate_limit']=config.get('max_candidates',3)
    record['candidates_beyond_limit']=max(0,len(candidates)-record['candidate_limit'])
    # A single existing English SRT is replaceable; multiple selections need review.
    if len(sidecars)>1:return dict(record,status='REVIEW_MULTIPLE_SIDECARS')
    target=sidecars[0] if sidecars else video.with_name(video.stem+'.en.srt')
    if target.suffix.lower()!='.srt':return dict(record,status='REVIEW_FORMAT')
    expected=originals.get(str(target))
    if target.exists() and expected is None:raise ValueError('Unexpected destination sidecar')
    deferred=False
    for entry in candidates[:config.get('max_candidates',3)]:
        try:
            source=provider.download(entry)
            print('    Checking provider candidate: '+entry['release'],flush=True)
            candidate,check=prepare(video,source,info,config,state,folder)
            record['attempts'].append(dict(provider=entry,check=check))
            if not candidate:continue
            # Keep the winning provider identity explicit: candidate may point to
            # a locally shifted file whose filename no longer identifies its source.
            record.update(candidate=str(candidate),candidate_sha256=digest(candidate),
                          selected_provider=dict(entry,provider='OpenSubtitles'),
                          selected_offset_seconds=check['offset'],selected_scale=check.get('scale',1),destination=str(target))
            if args.apply:
                receipt=install(candidate,target,state/'backups',expected,video,fp)
                return dict(record,status='DOWNLOADED_VERIFIED',receipt=receipt,subtitle=str(target),installed_sha256=digest(target))
            return dict(record,status='WOULD_INSTALL')
        except DownloadBudgetReached as e:
            deferred=True
            record['attempts'].append(dict(provider=entry,deferred='download_budget',error=str(e)))
        except Exception as e:record['attempts'].append(dict(provider=entry,error=str(e)))
    if deferred:return dict(record,status='DEFERRED_DOWNLOAD_BUDGET',detail='Local download cap reached; some candidates not tested. Resume later or increase --max-downloads')
    if record.get('provider_search_errors'):
        return dict(record,status='DEFERRED_PROVIDER_SEARCH',detail='One numbering search failed; available candidates did not pass. Retry when provider access recovers')
    if record['candidates_beyond_limit']:
        return dict(record,status='MORE_CANDIDATES_REMAIN',detail=f"{record['candidates_beyond_limit']} eligible candidates beyond the attempt limit; increase --max-candidates. Originals unchanged")
    return dict(record,status='UNRESOLVED',detail='No tested candidate passed; originals unchanged')
