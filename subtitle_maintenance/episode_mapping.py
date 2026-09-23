"""Opt-in alternate catalog numbering; never rename media or assume an offset.

TVmaze supplies an alternate search identity, not proof of OpenSubtitles identity.
Only a unique exact normalized episode-title match in the IMDb-matched show is
accepted. The existing dialogue gates must still approve any downloaded subtitle.
"""
import json
from difflib import SequenceMatcher
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


def release_title_match(release, wanted, titles):
    """Classify explicit titles after an episode marker, not arbitrary substrings.

    Untitled scene releases stay eligible. Only a recognizable different catalog
    title is excluded. Longest prefix wins (e.g. Part II versus Part I); short
    ambiguous words are not used as negative evidence. Dialogue remains decisive.
    """
    marker=re.search(r'(?:s\d{1,3}[ ._-]*e\d{1,3}|\d{1,3}x\d{1,3})\b',release,re.I)
    if not marker:return 'unknown'
    raw=release[marker.end():]
    tail=normalized(raw)
    names={normalized(t) for t in titles if t}
    target=normalized(wanted)
    if target:names.add(target)
    matches=[n for n in names if len(n)>=8 and tail.startswith(n)]
    if matches:return 'match' if max(matches,key=len)==target else 'different'
    # Short exact titles may PROMOTE, never reject. Require a token boundary so
    # "CIA" cannot match "CIAPOW". Punctuation inside a title remains harmless.
    prefixes=[normalized(raw[:m.start()]) for m in re.finditer(r'[\s._-]+|$',raw)]
    if target and target in prefixes:return 'match'
    # Conservative fuzzy ranking only, stopping before recognizable release tags.
    # Never let approximate evidence exclude a candidate or bypass dialogue gates.
    title_part=re.split(r'(?i)(?:^|[\s._-])(?:\d{3,4}p|web(?:rip|[ ._-]?dl)?|hdtv|dvdrip|bluray|bdrip|x26[45]|h26[45]|aac|dd5)[\s._-]?',raw,maxsplit=1)[0]
    candidate=normalized(title_part)
    if len(target)>=12 and len(candidate)>=12 and SequenceMatcher(None,target,candidate,autojunk=False).ratio()>=0.92:
        return 'near_match'
    return 'unknown'


def merge_candidates(groups, wanted, titles):
    """Filter and deduplicate before the workflow applies its candidate budget."""
    by_id={};rejected=[]
    for label,entries in groups:
        for entry in entries:
            match=release_title_match(entry.get('release',''),wanted,titles)
            if match=='different':
                rejected.append(dict(entry,search_numbering=label,reason='Different episode title in release'))
                continue
            item=dict(entry,title_match=match,search_numberings=[label])
            old=by_id.get(entry['file_id'])
            if old:
                if label not in old['search_numberings']:old['search_numberings'].append(label)
                rank={'match':0,'near_match':1,'unknown':2}
                if rank[match]<rank[old['title_match']]:old['title_match']=match
            else:by_id[entry['file_id']]=item
    # Stable sort keeps provider ordering (including XL preference) within tiers.
    return sorted(by_id.values(),key=lambda e:{'match':0,'near_match':1,'unknown':2}[e['title_match']]),rejected


def search_both(provider, original, mapped, state, prefer_xl):
    groups=[];errors=[];seen=set()
    for label,ident in [('library',original),('mapped',mapped)]:
        key=(ident['imdb'],ident['season'],ident['episode'])
        if key in seen:continue
        seen.add(key)
        try:groups.append((label,provider.candidates(ident,prefer_xl)))
        except Exception as error:errors.append(dict(numbering=label,error=str(error)))
    if not groups:raise RuntimeError('Both episode searches failed: '+str(errors))
    catalog=json.loads((state/'episode-catalogs'/(original['imdb']+'.json')).read_text())
    titles=[e.get('name','') for e in catalog['episodes']]
    candidates,rejected=merge_candidates(groups,original['title'],titles)
    return candidates,rejected,errors
