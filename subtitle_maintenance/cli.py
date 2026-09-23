import argparse
from collections import Counter
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from . import __version__,media
from .common import atomic_json,digest,fingerprint,install
from .providers import Provider
from .workflow import process

def main():
    p=argparse.ArgumentParser(description='Conservative English subtitle maintenance. Preview by default.')
    p.add_argument('paths',nargs='*',type=Path)
    p.add_argument('--config',type=Path,default=Path.home()/'scripts/subtitle-maintenance.json')
    p.add_argument('--state-dir',type=Path,default=Path.home()/'Library/Application Support/SubtitleMaintenance')
    p.add_argument('--apply',action='store_true')
    p.add_argument('--scan-only',action='store_true',help='Inventory only: no provider downloads or speech transcription')
    p.add_argument('--audit', choices=['coverage','defaults'], help='Read-only metadata audit; no transcription/downloads')
    p.add_argument('--cleanup-sidecars', action='store_true', help='Preview redundant English sidecar quarantine; requires --apply to move')
    p.add_argument('--restore-quarantine', type=Path, help='Restore a quarantine receipt; preview unless --apply')
    p.add_argument('--cache-only',action='store_true',help='Use only existing speech transcripts')
    p.add_argument('--no-download',action='store_true')
    p.add_argument('--limit',type=int,default=0)
    p.add_argument('--rescan',action='store_true')
    p.add_argument('--audio-stream',type=int,help='Explicit ffprobe audio stream index; one video only')
    p.add_argument('--imdb',help='Explicit tt... movie or series ID; one video only')
    p.add_argument('--season',type=int)
    p.add_argument('--episode',type=int)
    p.add_argument('--restore',type=Path,help='Restore a committed receipt, preview unless --apply')
    a=p.parse_args()
    modes=[a.audit is not None,a.cleanup_sidecars,a.restore_quarantine is not None,a.restore is not None,a.scan_only]
    if sum(modes)>1:p.error('Choose only one audit, cleanup, restore, or scan mode')
    if a.audit and a.apply:p.error('--audit is read-only')
    if a.restore_quarantine and a.paths:p.error('--restore-quarantine cannot be combined with paths')
    if a.restore and (a.paths or a.scan_only):p.error('--restore cannot be combined with paths or --scan-only')
    if a.scan_only and a.apply:p.error('--scan-only cannot be combined with --apply')
    config=json.loads(a.config.expanduser().read_text()) if a.config.expanduser().exists() else {}
    config.setdefault('python',sys.executable);config.setdefault('model','')
    config['cache_only']=a.cache_only
    a.state_dir=a.state_dir.expanduser().resolve();a.state_dir.mkdir(parents=True,exist_ok=True)
    a.identity=None
    if a.imdb:
        import re
        if not re.fullmatch(r'tt\d+',a.imdb):p.error('--imdb must be an IMDb tt identifier')
        a.identity=dict(imdb=a.imdb,source='explicit user input')
        if (a.season is None)!=(a.episode is None):p.error('Supply both --season and --episode')
        if a.season is not None:a.identity.update(season=a.season,episode=a.episode)
    if a.audio_stream is not None or a.imdb:
        if len(a.paths)!=1 or not a.paths[0].expanduser().is_file():p.error('Metadata/audio overrides require one video file')
    with (a.state_dir/'run.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:p.error('Another subtitle-maintain job is already running')
        if a.restore_quarantine:
            from .housekeeping import restore
            print(('Restored: ' if a.apply else 'Would restore: ')+restore(a.restore_quarantine,a.apply))
            return 0
        if a.restore:
            r=json.loads(a.restore.read_text());target=Path(r['target']);backup=Path(r['backup']) if r.get('backup') else None
            if not backup:p.error('This receipt created a new file; automatic deletion is not supported')
            if r['status']!='COMMITTED' or digest(backup)!=r['original_sha256'] or digest(target)!=r['installed_sha256']:p.error('Restore checksum/state mismatch')
            print('Restore:',target)
            if a.apply:install(backup,target,a.state_dir/'rollback-backups',r['installed_sha256'],target,fingerprint(target))
            return 0
        if not a.paths:p.error('Provide a video or folder')
        db=sqlite3.connect(a.state_dir/'state.db')
        db.execute('create table if not exists results (video text primary key, signature text, result text, updated real)')
        provider=Provider(config,a.state_dir);counts=Counter();report=[]
        print('Scanning; '+('APPLY' if a.apply else 'PREVIEW')+' mode. State: '+str(a.state_dir),flush=True)
        try:
            for n,video in enumerate(media.videos(a.paths,a.state_dir),1):
                if a.limit and n>a.limit:break
                print(f'[{n}] {video}',flush=True)
                try:
                    sidecars,unknown=media.sidecars(video)
                    signature=hashlib.sha256(json.dumps([__version__,config,a.apply,a.scan_only,a.no_download,a.audio_stream,a.identity,
                        fingerprint(video),[(str(s),digest(s)) for s in sidecars+unknown]],sort_keys=True).encode()).hexdigest()
                    old=db.execute('select result from results where video=? and signature=?',(str(video),signature)).fetchone()
                    data=json.loads(old[0]) if old else None
                    terminal={'TRUSTED_EMBEDDED','VERIFIED','HUMAN_APPROVED'}
                    if a.audit or a.cleanup_sidecars:
                        from .housekeeping import inspect
                        data=inspect(video,a.audit or 'cleanup',a.apply,a.state_dir,config)
                    elif data and data['status'] in terminal and not a.rescan:
                        data=dict(data,cached=True)
                    else:data=process(video,a,config,a.state_dir,provider)
                except Exception as e:data=dict(video=str(video),status='ERROR',error=str(e));signature=''
                counts[data['status']]+=1;report.append(data)
                print('  '+data['status']+(': '+data.get('error','') if data.get('error') else ''),flush=True)
                for item in data.get('cleanup_candidates',[]):print('    '+item,flush=True)
                db.execute('insert or replace into results values (?,?,?,?)',(str(video),signature,json.dumps(data),time.time()));db.commit()
                atomic_json(a.state_dir/'latest-report.json',dict(version=__version__,counts=dict(counts),results=report))
        finally:provider.close();db.close()
    print('Summary:',dict(counts),flush=True)
    return 1 if counts['ERROR'] else 0
