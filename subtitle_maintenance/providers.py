import base64
import hashlib
import json
import os
from pathlib import Path
import re
import select
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET
from .common import atomic_json

class DownloadBudgetReached(RuntimeError):
    """Local per-run safety cap, not an OpenSubtitles rate-limit response."""

class ProviderTransportError(RuntimeError):
    """Sanitized helper failure; never carries response bodies or credentials."""

def read_helper_response(process,timeout):
    # Binary reads avoid TextIO.readline blocking forever on partial JSON and
    # avoid select() overlooking bytes buffered inside a Python text wrapper.
    deadline=time.monotonic()+timeout;buffer=bytearray()
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0 or not select.select([process.stdout],[],[],remaining)[0]:
            raise ProviderTransportError('response_timeout')
        chunk=os.read(process.stdout.fileno(),65536)
        if not chunk:raise ProviderTransportError('helper_eof')
        buffer.extend(chunk)
        if len(buffer)>8*1024*1024:raise ProviderTransportError('oversized_response')
        if b'\n' in buffer:
            line,extra=bytes(buffer).split(b'\n',1)
            if extra.strip():raise ProviderTransportError('unexpected_extra_response')
            try:data=json.loads(line)
            except (ValueError,UnicodeError):raise ProviderTransportError('invalid_json') from None
            if not isinstance(data,dict):raise ProviderTransportError('invalid_response_shape')
            return data

def legacy_transport_cooldown(saved):
    return (saved.get('transport_recovery_version',0)<1
            and saved.get('error')=={'error':'Provider transport failure'})

def legacy_token_cooldown(saved):
    """Only supersede old invalid-token cooldowns created before refresh support.

    Preserve the original record; never bypass quota/429 or new repeated failures.
    """
    error=saved.get('error',{})
    return (saved.get('auth_recovery_version',0)<1 and error.get('status')==401
            and error.get('stage')=='download'
            and str(error.get('message','')).strip().lower()=='invalid token')

def provider_error_label(data):
    if data.get('error')=='Provider transport failure':
        return ('Provider helper communication failed: '+data.get('reason','unknown legacy transport failure')
                +' (action='+str(data.get('action','unknown'))+', exit='+str(data.get('helper_exit','unknown'))+')')
    if data.get('status')==401:
        return 'Provider authentication failed (HTTP 401; fresh login did not resolve access)'
    return 'Provider unavailable: '+str(data.get('status') or data.get('error','unknown error'))+' '+str(data.get('stage') or '')

def identity(video,config,explicit=None):
    if explicit:return explicit
    database=config.get('bazarr_database')
    remote=str(video)
    for host,container in config.get('path_mappings',[]):
        if remote.startswith(host.rstrip('/')+'/'):
            remote=container.rstrip('/')+remote[len(host.rstrip('/')):];break
    if database and Path(database).exists():
        with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as db:
            row=db.execute('select e.season,e.episode,s.imdbId,e.sceneName,e.title from table_episodes e join table_shows s on e.sonarrSeriesId=s.sonarrSeriesId where e.path=?',(remote,)).fetchone()
            if row and row[2]:return dict(season=row[0],episode=row[1],imdb=row[2],release=row[3] or '',title=row[4] or '',source='library database')
            row=db.execute('select imdbId from table_movies where path=?',(remote,)).fetchone()
            if row and row[0]:return dict(imdb=row[0],source='library database')
    ep=re.search(r'\bS(\d{1,2})E(\d{1,3})\b',video.stem,re.I)
    nfopaths=[video.with_suffix('.nfo')] if not ep else [video.parent/'tvshow.nfo',video.parent.parent/'tvshow.nfo']
    for nfo in nfopaths:
        if not nfo.exists() or nfo.stat().st_size>1000000:continue
        root=ET.fromstring(nfo.read_bytes())
        ids=[x.text for x in root.findall('uniqueid') if x.get('type')=='imdb']+[root.findtext('imdbid')]
        imdb=next((x for x in ids if x and re.fullmatch(r'tt\d+',x)),None)
        if imdb:
            result=dict(imdb=imdb,source='local NFO')
            if ep:result.update(season=int(ep[1]),episode=int(ep[2]))
            return result
    raise ValueError('REVIEW_IDENTIFICATION: no reliable movie/episode ID; use --imdb for a single video')

class Provider:
    def __init__(self,config,state):
        self.config=config;self.state=state;self.process=None;self.last=0;self.downloads=0
    def close(self):
        process=self.process;self.process=None
        if process:
            try:
                try:process.stdin.close()
                except (OSError,ValueError):pass
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait(timeout=3)
            finally:process.stdout.close()

    def exchange(self,request):
        if self.process and self.process.poll() is not None:self.close()
        if not self.process:
            script=Path(__file__).with_name('bazarr_bridge.py')
            if os.environ.get('OPENSUBTITLES_API_KEY'):
                command=[self.config['python'],'-u',str(script)]
            elif self.config.get('bazarr_container'):
                command=['docker','exec','-i',self.config['bazarr_container'],'python3','-u','-c',script.read_text()]
            else:raise ValueError('Configure OpenSubtitles credentials or existing Bazarr credential bridge')
            self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        time.sleep(max(0,1.1-(time.monotonic()-self.last)));self.last=time.monotonic()
        self.process.stdin.write((json.dumps(request)+'\n').encode());self.process.stdin.flush()
        return read_helper_response(self.process,240 if request.get('action')=='download' else 150)
    def request(self,request):
        cooldown=self.state/'provider-cooldown.json'
        if cooldown.exists():
            saved=json.loads(cooldown.read_text())
            if saved['retry_at']>time.time() and not (legacy_token_cooldown(saved) or legacy_transport_cooldown(saved)):
                until=time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(saved['retry_at']))
                raise RuntimeError(provider_error_label(saved.get('error',{}))+'; retry after '+until)
        for attempt in range(2):
            try:
                data=self.exchange(request)
                break
            except (ProviderTransportError,OSError) as error:
                data=dict(error='Provider transport failure',
                          reason=str(error) if isinstance(error,ProviderTransportError) else type(error).__name__,
                          action=request.get('action'),helper_exit=self.process.poll() if self.process else None)
                self.close() # Discard late output; it must never become the next request's reply.
                if request.get('action')!='search' or attempt:break
                print('    Provider helper connection failed; retrying search once with a fresh helper',flush=True)
        if 'error' in data:
            retry=300 if data.get('error')=='Provider transport failure' else 3600
            try:retry=max(retry,int(data.get('retry_after') or 0))
            except ValueError:pass
            atomic_json(cooldown,dict(retry_at=time.time()+retry,error=data,auth_recovery_version=1,transport_recovery_version=1))
            raise RuntimeError(provider_error_label(data))
        return data
    def candidates(self,ident,prefer_xl):
        # Title is mapping evidence, not an API search parameter. Preserve legacy
        # cache keys when only a database title was added to the identity.
        key=hashlib.sha256(json.dumps({k:v for k,v in ident.items() if k!='title'},sort_keys=True).encode()).hexdigest()
        cache=self.state/'searches'/(key+'.json')
        if cache.exists() and time.time()-cache.stat().st_mtime<86400:data=json.loads(cache.read_text())
        else:
            data=[]
            for page in range(1,21):
                result=self.request(dict(action='search',page=page,**ident));data.extend(result.get('data',[]))
                if page>=result.get('total_pages',1):break
            else:raise ValueError('Provider search too broad; refine identification')
            atomic_json(cache,data)
        found=[];seen=set()
        for item in data:
            a=item['attributes'];f=a.get('feature_details',{})
            if a.get('language')!='en' or a.get('foreign_parts_only') or a.get('ai_translated') or a.get('machine_translated'):continue
            if ident.get('season') is not None:
                if f.get('season_number')!=ident['season'] or f.get('episode_number')!=ident['episode']:continue
            elif str(f.get('imdb_id',''))!=ident['imdb'].removeprefix('tt'):continue
            for entry in a.get('files',[]):
                if entry['file_id'] in seen:continue
                seen.add(entry['file_id'])
                found.append(dict(file_id=entry['file_id'],release=a.get('release',''),url=a.get('url')))
        found.sort(key=lambda c:(bool(re.search(r'\b(XL|UNCUT)\b',c['release'],re.I))!=prefer_xl,c['file_id']))
        return found
    def download(self,candidate):
        target=self.state/'downloads'/(str(candidate['file_id'])+'.srt')
        if target.exists():return target
        if self.downloads>=self.config.get('max_downloads',20):raise DownloadBudgetReached('Local per-run download budget reached; cached candidates remain usable')
        data=self.request(dict(action='download',file_id=candidate['file_id']));self.downloads+=1
        content=base64.b64decode(data['content'],validate=True)
        if not content or len(content)>2000000 or b'-->' not in content:raise ValueError('Invalid downloaded SRT')
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write(content)
        return target
