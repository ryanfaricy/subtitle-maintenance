"""Validate user settings separately from filesystem discovery and workflow policy."""

import math
import re
from urllib.parse import urlsplit

POLICY = {"max_candidates", "max_downloads", "max_shift", "min_age_minutes"}
SECTIONS = {
    "tools": {"ffmpeg", "ffprobe", "mkvmerge", "mkvpropedit", "curl", "docker", "seconv"},
    "whisper": {"service_url", "model", "state_dir"},
    "shows": {"qi_root", "eastenders_root"},
    "sonarr": {"url", "host_root", "sonarr_root", "eastenders_root", "work_dir"},
    "backup": {"container", "user", "python", "database", "directory", "keep"},
    "policy": POLICY,
}
TOP_LEVEL = (
    POLICY
    | set(SECTIONS)
    | {
        "python",
        "model",
        "media_root",
        "state_dir",
        "legacy_transcripts",
        "bazarr_database",
        "bazarr_container",
        "service_url",
        "path_mappings",
        "human_approved_sha256",
    }
)


def validate(config):
    """Reject typos and unsafe values; normalize the optional policy section.

    Legacy flat policy keys remain supported, but duplicate definitions are an
    error. Mutation permission is deliberately absent from the config schema.
    """
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object")
    unknown = set(config) - TOP_LEVEL
    if unknown:
        raise ValueError("Unknown config keys: " + ", ".join(sorted(unknown)))
    for section, allowed in SECTIONS.items():
        if section not in config:
            continue
        values = config[section]
        if not isinstance(values, dict):
            raise ValueError(f"Config {section} must be an object")
        if set(values) - allowed:
            raise ValueError(
                f"Unknown keys in config {section}: " + ", ".join(sorted(set(values) - allowed))
            )
    config = dict(config)
    for key, value in config.pop("policy", {}).items():
        if key in config:
            raise ValueError(f"Define {key} once, either under policy or at the top level")
        config[key] = value
    for key, low, high, integer in (
        ("max_candidates", 1, None, True),
        ("max_downloads", 0, None, True),
        ("max_shift", 0, 60, False),
        ("min_age_minutes", 0, None, False),
    ):
        if key not in config:
            continue
        value = config[key]
        if (
            type(value) not in ((int,) if integer else (int, float))
            or not math.isfinite(value)
            or value < low
            or (high is not None and value > high)
        ):
            raise ValueError(
                f"Invalid {key}: expected {'integer' if integer else 'number'} >= {low}"
                + (f" and <= {high}" if high is not None else "")
            )
    keep = config.get("backup", {}).get("keep", 7)
    if type(keep) is not int or keep < 1:
        raise ValueError("backup.keep must be a positive integer")
    strings = TOP_LEVEL - set(SECTIONS) - POLICY - {"path_mappings", "human_approved_sha256"}
    for key in strings:
        if key in config and (not isinstance(config[key], str) or not config[key].strip()):
            raise ValueError(f"Config {key} must be a nonempty string")
    for name, values in config.items():
        if name in SECTIONS:
            for key, value in values.items():
                if key != "keep" and (not isinstance(value, str) or not value.strip()):
                    raise ValueError(f"Config {name}.{key} must be a nonempty string")
    urls = [
        config.get("service_url"),
        config.get("whisper", {}).get("service_url"),
        config.get("sonarr", {}).get("url"),
    ]
    for url in filter(None, urls):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Service URLs must use http/https and must not contain credentials")
    mappings = config.get("path_mappings", [])
    if not isinstance(mappings, list) or any(
        not isinstance(pair, list)
        or len(pair) != 2
        or any(not isinstance(x, str) or not x.startswith("/") for x in pair)
        for pair in mappings
    ):
        raise ValueError("path_mappings must be a list of absolute host/container path pairs")
    hashes = config.get("human_approved_sha256", [])
    if not isinstance(hashes, list) or any(
        not isinstance(x, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", x) for x in hashes
    ):
        raise ValueError("human_approved_sha256 must contain SHA256 hex strings")
    return config
