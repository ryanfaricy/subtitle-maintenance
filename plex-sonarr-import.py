#!/usr/bin/env python3
"""Explicitly scoped Plex recording importer. Preview unless --apply is supplied."""
import argparse
import ast
import collections
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VIDEO = {'.mkv', '.ts', '.mp4', '.m4v'}
TOKEN = re.compile(r'(?i)\bS(\d{1,4})E(\d+)\b')


def match_episode(name, episodes, year_numbering=False, date_numbering=False):
    """Return a unique episode only; never guess by title or fuzzy similarity."""
    tokens = list(TOKEN.finditer(name))
    if tokens:
        if len(tokens) != 1 or re.match(r'(?i)[- ]?(?:E|S\d+E|\d)', name[tokens[0].end():]):
            return None, 'multi-episode or ambiguous filename'
        season, number = map(int, tokens[0].groups())
        if season >= 1900:
            if not year_numbering:
                return None, 'year numbering requires an explicit series rule'
            candidates = [e for e in episodes if e.get('seasonNumber', 0) > 0
                          and str(e.get('airDate', '')).startswith(str(season) + '-')
                          and e.get('episodeNumber') == number]
        else:
            candidates = [e for e in episodes if e.get('seasonNumber') == season
                          and e.get('episodeNumber') == number]
    elif date_numbering:
        dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', name)
        if len(dates) != 1:
            return None, 'no unique original air date'
        candidates = [e for e in episodes if e.get('airDate') == dates[0]]
    else:
        return None, 'no supported episode token (date matching is opt-in)'
    if len(candidates) != 1:
        return None, 'episode absent or ambiguous in Sonarr'
    return candidates[0], 'unique match'


def fingerprint(path):
    st = path.stat()
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def recordings(root):
    # Unlike pathlib.rglob on some Python versions, traversal failures are fatal.
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError('Series folder missing or symlinked: ' + str(root))
    def fail(exc):
        raise exc
    for base, dirs, names in os.walk(root, onerror=fail, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith('.') and
                   not d.lower().startswith(('transcod', 'recording', 'temporary'))
                   and not (Path(base) / d).is_symlink()]
        for name in sorted(names):
            p = Path(base) / name
            if not name.startswith('.') and p.suffix.lower() in VIDEO and not p.is_symlink():
                yield p


class API:
    def __init__(self, url, key):
        self.url = url.rstrip('/')
        self.key = key

    def request(self, route, params=None, body=None):
        url = self.url + route
        if params:
            url += '?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={'X-Api-Key': self.key,
                    'Content-Type': 'application/json'},
                    data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError('Sonarr HTTP %s: %s' %
                               (exc.code, exc.read().decode()[:1200])) from None

    def command(self, payload):
        command = self.request('/command', body=payload)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = self.request('/command/' + str(command['id']))
            if status.get('status') == 'completed':
                if status.get('result') != 'successful':
                    raise RuntimeError('Command completed unsuccessfully: ' + str(command['id']))
                return
            if status.get('status') in ('failed', 'aborted', 'cancelled'):
                raise RuntimeError('Command failed: ' + str(command['id']))
            time.sleep(1)
        raise RuntimeError('Command timed out; inspect Sonarr before retry: ' + str(command['id']))


def credentials(args):
    url, key = os.environ.get('SONARR_URL'), os.environ.get('SONARR_API_KEY')
    if args.legacy_config:
        # Read only the two literal settings. NEVER import or execute the old script.
        tree = ast.parse(Path(args.legacy_config).read_text())
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in ('SONARR', 'API_KEY'):
                    values[name] = ast.literal_eval(node.value)
        url, key = url or values.get('SONARR'), key or values.get('API_KEY')
    if not url or not key:
        raise RuntimeError('Set SONARR_URL and SONARR_API_KEY or supply --legacy-config')
    return API(url, key)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series', action='append', required=True, help='Exact Sonarr title; repeatable')
    parser.add_argument('--year-numbered-series', action='append', default=[])
    parser.add_argument('--date-numbered-series', action='append', default=[], help='Only for verified ORIGINAL air-date filenames')
    parser.add_argument('--host-root', type=Path, required=True)
    parser.add_argument('--sonarr-root', required=True)
    parser.add_argument('--legacy-config', type=Path)
    parser.add_argument('--work-dir', type=Path, required=True, help='Lock and append-only import journal')
    parser.add_argument('--min-age-minutes', type=float, default=60)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if args.min_age_minutes < 60:
        parser.error('Recording grace period must be at least 60 minutes')
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with (args.work_dir / 'import.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another importer is running')
        return run(args, credentials(args))


def run(args, api):
    host_root = args.host_root.resolve(strict=True)
    sonarr_root = Path(args.sonarr_root)
    def to_host(remote):
        relative = Path(remote).relative_to(sonarr_root)
        result = host_root / relative
        result.resolve().relative_to(host_root)
        return result
    def to_remote(local):
        return (sonarr_root / local.relative_to(host_root)).as_posix()
    journal = args.work_dir / 'imports.jsonl'
    def record(data):
        with journal.open('a') as stream:
            stream.write(json.dumps(dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(), **data)) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    pending = {}
    if journal.exists():
        for line in journal.read_text().splitlines():
            event = json.loads(line)
            pending[(event['seriesId'], event['episodeId'])] = event
    all_series = api.request('/series')
    selected = []
    for title in dict.fromkeys(args.series):
        found = [s for s in all_series if s['title'].casefold() == title.casefold()]
        if len(found) != 1:
            raise RuntimeError('Series title absent or ambiguous: ' + title)
        selected.append(found[0])
    counts = collections.Counter()
    print('MODE:', 'APPLY' if args.apply else 'PREVIEW', flush=True)
    for series in selected:
        sid, title = series['id'], series['title']
        root = to_host(series['path'])
        # Plex identity hints are a useful additional check, never executable input.
        hints = root / '.plexmatch'
        if hints.exists():
            ids = re.findall(r'(?im)^TvdbId:\s*(\d+)\s*$', hints.read_text())
            if ids and (len(set(ids)) != 1 or int(ids[0]) != series.get('tvdbId')):
                raise RuntimeError('Plex/Sonarr TVDB identity conflict: ' + title)
        episodes = api.request('/episode', {'seriesId': sid})
        files = api.request('/episodefile', {'seriesId': sid})
        registered = {f['path']: f for f in files}
        groups = collections.defaultdict(list)
        year_rule = title.casefold() in {s.casefold() for s in args.year_numbered_series}
        date_rule = title.casefold() in {s.casefold() for s in args.date_numbered_series}
        for path in recordings(root):
            remote = to_remote(path)
            episode, reason = match_episode(path.name, episodes, year_rule, date_rule)
            # Registered files do not need repeated media inspection.
            if remote in registered:
                recovery = [e for e in episodes if e.get('episodeFileId') == registered[remote]['id']
                            and pending.get((sid, e['id']), {}).get('status') == 'pending']
                if len(recovery) == 1:
                    episode = recovery[0]
                retry = episode and pending.get((sid, episode['id']), {}).get('status') == 'pending'
                year_rename = episode and year_rule and bool(re.search(r'(?i)\bS\d{4}E\d+\b', path.name))
                if not retry and not year_rename:
                    counts['already_registered'] += 1
                    continue
            if not episode:
                counts['review'] += 1
                print('REVIEW:', path.name, '-', reason, flush=True)
                continue
            groups[episode['id']].append((path, episode))
        folder_items = {}
        for eid, group in groups.items():
            if len(group) != 1:
                counts['review'] += len(group)
                print('REVIEW: multiple sources for', title, eid, '; preserving all', flush=True)
                continue
            path, episode = group[0]
            remote = to_remote(path)
            before = fingerprint(path)
            if before[2] == 0 or time.time() - path.stat().st_mtime < args.min_age_minutes * 60:
                counts['deferred'] += 1
                print('DEFER: recent or empty recording:', path.name, flush=True)
                continue
            try:
                fresh = api.request('/episode/' + str(eid))
                existing_id = fresh.get('episodeFileId')
                if existing_id:
                    existing = api.request('/episodefile/' + str(existing_id))
                    if existing.get('path') != remote:
                        counts['review'] += 1
                        print('REVIEW: Sonarr already has episode; keeping source:', path.name, flush=True)
                        continue
                else:
                    folder = str(Path(remote).parent)
                    if folder not in folder_items:
                        # seriesId changes this API into a registered-library listing.
                        folder_items[folder] = api.request('/manualimport', {'folder': folder, 'filterExistingFiles': 'false'})
                    items = [x for x in folder_items[folder] if x.get('path') == remote]
                    if len(items) != 1 or not items[0].get('quality'):
                        raise RuntimeError('No unique Sonarr inspection with quality')
                    item = items[0]
                    if item.get('series') and item['series'].get('id') != sid:
                        raise RuntimeError('Sonarr inspection identified a different series')
                    rejected = [r.get('reason', '') for r in item.get('rejections', [])
                                if r.get('reason') != 'Invalid season or episode']
                    if rejected:
                        counts['review'] += 1
                        print('REVIEW:', path.name, '; '.join(rejected), flush=True)
                        continue
                label = '%s S%02dE%02d' % (title, episode['seasonNumber'], episode['episodeNumber'])
                action = 'RENAME' if existing_id else 'IMPORT'
                print(('APPLY ' if args.apply else 'WOULD ') + action + ': ' + path.name + ' -> ' + label, flush=True)
                if not args.apply:
                    counts['would_' + action.lower()] += 1
                    continue
                # Recheck file and episode immediately before any mutation.
                if fingerprint(path) != before:
                    raise RuntimeError('Recording changed during inspection')
                current = api.request('/episode/' + str(eid))
                if current.get('episodeFileId') != fresh.get('episodeFileId'):
                    raise RuntimeError('Sonarr episode changed during inspection; retry later')
                event = dict(seriesId=sid, episodeId=eid, source=remote, size=before[2])
                previous = pending.get((sid, eid), {})
                if previous.get('status') == 'pending':
                    if previous.get('size') != before[2]:
                        raise RuntimeError('Pending import file size changed; manual review required')
                    event['source'] = previous['source']
                record(dict(event, status='pending'))
                if not existing_id:
                    payload = {k: item[k] for k in ('quality', 'languages', 'releaseGroup', 'indexerFlags', 'releaseType') if k in item}
                    payload.update(path=remote, seriesId=sid, seasonNumber=episode['seasonNumber'], episodeIds=[eid])
                    api.command({'name': 'ManualImport', 'importMode': 'move', 'files': [payload]})
                    fresh = api.request('/episode/' + str(eid))
                    existing_id = fresh.get('episodeFileId')
                    if not existing_id:
                        raise RuntimeError('Import finished without registering an episode file')
                api.command({'name': 'RenameFiles', 'seriesId': sid, 'files': [existing_id]})
                final = api.request('/episodefile/' + str(existing_id))
                final_path = to_host(final['path'])
                final_path.resolve().relative_to(root.resolve())
                if not final_path.is_file() or final_path.stat().st_size != before[2]:
                    raise RuntimeError('Final file missing or size differs; inspect before retry')
                verified = api.request('/episode/' + str(eid))
                if not verified.get('hasFile') or verified.get('episodeFileId') != existing_id:
                    raise RuntimeError('Final episode registration verification failed')
                record(dict(event, status='complete', destination=final['path'], episodeFileId=existing_id))
                counts['completed'] += 1
                print('VERIFIED:', final_path.name, flush=True)
            except Exception as exc:
                counts['errors'] += 1
                print('ERROR:', path.name, str(exc), flush=True)
                if args.apply:
                    # Avoid continuing mutations after an uncertain command result.
                    print('Stopping apply run; the journal preserves recovery context.', flush=True)
                    print('SUMMARY:', json.dumps(dict(counts)), flush=True)
                    return 1
    print('SUMMARY:', json.dumps(dict(counts)), flush=True)
    return 1 if counts['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('ERROR:', str(exc), file=sys.stderr)
        sys.exit(1)
