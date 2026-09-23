"""Opt-in alternate catalog numbering; never rename media or assume an offset.

TVmaze supplies an alternate search identity, not proof of OpenSubtitles identity.
Only a unique exact normalized episode-title match in the IMDb-matched show is
accepted. The existing dialogue gates must still approve any downloaded subtitle.
"""
import json
import re
import time
import unicodedata
import urllib.request
from .common import atomic_json


class MappingReview(ValueError):
    pass


def normalized(title):
    text=unicodedata.normalize('NFKD',title.casefold().replace('&',' and '))
    return ''.join(c for c in text if c.isalnum() and not unicodedata.combining(c))


def resolve(identity, catalog):
    if catalog['imdb'] != identity['imdb']:
        raise MappingReview('Catalog show identity mismatch')
    title=identity.get('title','')
    if not title.strip():raise MappingReview('No episode title available; numeric guessing disabled')
    matches=[e for e in catalog['episodes'] if normalized(e.get('name',''))==normalized(title)]
    # Even a same-season tie is ambiguous: never cherry-pick the desired numbering.
    if len(matches)!=1:raise MappingReview(f'Episode title has {len(matches)} exact catalog matches')
    e=matches[0]
    if not isinstance(e.get('season'),int) or not isinstance(e.get('number'),int):
        raise MappingReview('Matched episode has no regular season/episode numbering')
    evidence=dict(source='TVmaze',show_id=catalog['show_id'],episode_id=e['id'],
                  title=e['name'],airdate=e.get('airdate'),url=e.get('url'),
                  original_season=identity['season'],original_episode=identity['episode'],
                  mapped_season=e['season'],mapped_episode=e['number'])
    return dict(identity,season=e['season'],episode=e['number']),evidence


def map_identity(identity,state):
    imdb=identity['imdb']
    if not re.fullmatch(r'tt\d+',imdb):raise MappingReview('Invalid show IMDb ID')
    cache=state/'episode-catalogs'/(imdb+'.json')
    if cache.exists() and time.time()-cache.stat().st_mtime<7*86400:
        catalog=json.loads(cache.read_text())
    else:
        def get(path):
            req=urllib.request.Request('https://api.tvmaze.com/'+path,
                                       headers={'User-Agent':'SubtitleMaintenance/0.1'})
            with urllib.request.urlopen(req,timeout=30) as response:
                return json.load(response)
        show=get('lookup/shows?imdb='+imdb)
        if show.get('externals',{}).get('imdb')!=imdb:
            raise MappingReview('TVmaze lookup did not confirm show IMDb ID')
        episodes=get(f"shows/{int(show['id'])}/episodes?specials=1")
        catalog=dict(imdb=imdb,show_id=show['id'],episodes=episodes)
        atomic_json(cache,catalog)
    return resolve(identity,catalog)
