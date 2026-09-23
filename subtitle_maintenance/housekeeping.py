"""Explicit metadata audits and reversible sidecar quarantine, never ASR/downloads.

Embedded text is trusted by policy, not certified correct. Unknown-language,
forced and ambiguous sidecars are intentionally outside automatic cleanup.
"""
import json
import os
from pathlib import Path
import shutil
import uuid

from . import media
from .common import atomic_json, backup, digest, fingerprint


def quarantine(sidecar, video, video_fp, root):
    """Copy and verify before unlinking; leave a recovery receipt even on interruption."""
    if sidecar.is_symlink() or fingerprint(video) != video_fp:
        raise ValueError('Changed video or symlink sidecar; refusing cleanup')
    saved, sha = backup(sidecar, root / 'objects')
    receipt = root / (uuid.uuid4().hex + '.receipt.json')
    data = dict(kind='sidecar-quarantine', status='PREPARED', target=str(sidecar),
                backup=str(saved), original_sha256=sha, video=str(video))
    atomic_json(receipt, data)
    if sidecar.is_symlink() or digest(sidecar) != sha or fingerprint(video) != video_fp:
        raise ValueError('Files changed before quarantine')
    sidecar.unlink()
    data['status'] = 'QUARANTINED'
    atomic_json(receipt, data)
    return str(receipt)


def restore(receipt, apply=False):
    """Exclusive creation refuses to overwrite any subsequently downloaded sidecar."""
    data = json.loads(Path(receipt).read_text())
    target, saved = Path(data['target']), Path(data['backup'])
    if data['kind'] != 'sidecar-quarantine' or data['status'] not in {'PREPARED', 'QUARANTINED'}:
        raise ValueError('Not a recoverable quarantine receipt')
    if target.exists() or target.is_symlink() or digest(saved) != data['original_sha256']:
        raise ValueError('Target exists or backup checksum mismatch; refusing restore')
    if apply:
        # A failed copy leaves the original backup intact. Never overwrite a live file.
        with target.open('xb') as out, saved.open('rb') as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        if digest(target) != data['original_sha256']:
            raise ValueError('Restore verification failed; backup retained')
        data['status'] = 'RESTORED'
        atomic_json(receipt, data)
    return str(target)


def inspect(video, mode, apply, state, config=None):
    before = fingerprint(video)
    inv = media.inventory(video)
    sidecars, unknown = media.sidecars(video)
    tracks = [s for s in inv['data']['streams'] if s.get('codec_type') == 'subtitle']
    result = dict(video=str(video), tracks=tracks, english_sidecars=list(map(str, sidecars)),
                  unlabelled_sidecars=list(map(str, unknown)))
    if mode == 'coverage':
        status = ('EMBEDDED_ENGLISH_TEXT' if inv['text'] else
                  'ENGLISH_SIDECAR' if sidecars else
                  'ENGLISH_BITMAP_ONLY' if inv['bitmap'] else 'NO_CONFIRMED_ENGLISH_TEXT')
    elif mode == 'defaults':
        defaults = [s for s in tracks if s.get('disposition', {}).get('default')]
        good = [s for s in inv['text'] if s.get('disposition', {}).get('default')]
        status = ('MULTIPLE_SUBTITLE_DEFAULTS' if len(defaults) > 1 else
                  'DEFAULT_ENGLISH_TEXT' if good else 'NO_DEFAULT_FULL_ENGLISH_TEXT')
    else:
        approved = (config or {}).get('human_approved_sha256', [])
        protected = [s for s in sidecars if digest(s) in approved]
        result['protected_sidecars'] = list(map(str, protected))
        sidecars = [s for s in sidecars if s not in protected]
        # Same-stem sibling videos can share a sidecar. Never remove one on behalf
        # of just one version while leaving another version without subtitles.
        siblings = [p for p in video.parent.iterdir()
                    if p.stem.casefold() == video.stem.casefold() and p.suffix.lower() in media.VIDEO]
        eligible = bool(inv['text'] and sidecars and len(siblings) == 1)
        result['cleanup_candidates'] = list(map(str, sidecars)) if eligible else []
        status = 'WOULD_QUARANTINE' if eligible else 'NO_SAFE_CLEANUP'
        if eligible and apply:
            result['receipts'] = []
            for sidecar in sidecars:
                result['receipts'].append(quarantine(sidecar, video, before, state / 'quarantine'))
            status = 'QUARANTINED'
    result['status'] = status
    return result
