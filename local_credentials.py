"""Local-only service credentials. Never import legacy scripts to read secrets."""
import json
import os
from pathlib import Path
import stat


def get_secret(name, path=None):
    """Environment wins; otherwise read an owner-only JSON file outside Git.

    Missing/malformed configuration fails closed without including file contents
    in exceptions. This helper does not contact or alter any service.
    """
    value = os.environ.get(name)
    if value:
        return value
    target = Path(path) if path is not None else Path.home() / '.config/subtitle-maintenance/secrets.json'
    try:
        with target.open() as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('unsafe permissions')
            values = json.load(handle)
        value = values.get(name) if isinstance(values, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValueError('missing credential')
        return value
    except (OSError, ValueError, UnicodeError):
        raise RuntimeError('Configure ' + name + ' in the environment or owner-only local secrets.json') from None
