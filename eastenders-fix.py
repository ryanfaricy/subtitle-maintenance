#!/usr/bin/env python3

import re
from local_credentials import get_secret
import warnings

# Suppress urllib3's harmless LibreSSL warning on macOS system Python.
warnings.filterwarnings(
    "ignore",
    message="urllib3 v2 only supports OpenSSL 1.1.1+"
)

import requests
from pathlib import Path
from collections import defaultdict

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

from script_config import load_config, show_path
CONFIG = load_config()
SONARR = CONFIG.get('sonarr', {}).get('url', 'http://localhost:8989/api/v3')
API_KEY = get_secret("SONARR_API_KEY")

# Path as macOS sees it
HOST_EASTENDERS_PATH = show_path(CONFIG, 'EastEnders')

# Same path as Sonarr's Docker container sees it
SONARR_EASTENDERS_PATH = CONFIG.get('sonarr', {}).get('eastenders_root')
if not SONARR_EASTENDERS_PATH:
    raise ValueError('Set sonarr.eastenders_root to the container EastEnders folder in config')

# Start SAFE.
# True  = print what would happen, change nothing
# False = actually submit imports to Sonarr
DRY_RUN = False

VIDEO_EXTENSIONS = {
    ".mkv",
    ".ts",
    ".mp4",
    ".m4v",
}

headers = {
    "X-Api-Key": API_KEY,
    "Content-Type": "application/json",
}


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------

def sonarr_get(endpoint, params=None):
    response = requests.get(
        f"{SONARR}{endpoint}",
        headers=headers,
        params=params,
        timeout=30,
    )

    response.raise_for_status()
    return response.json()


def sonarr_post(endpoint, payload):
    response = requests.post(
        f"{SONARR}{endpoint}",
        headers=headers,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()
    return response.json()


def host_to_sonarr_path(host_path):
    """
    Convert:
      /path/to/media/TV/EastEnders/Season 2026/file.mkv

    into:
      /data/Plex/TV/EastEnders/Season 2026/file.mkv
    """

    relative = host_path.relative_to(HOST_EASTENDERS_PATH)

    return (
        Path(SONARR_EASTENDERS_PATH) / relative
    ).as_posix()


# ------------------------------------------------------------
# FIND EASTENDERS IN SONARR
# ------------------------------------------------------------

series = sonarr_get("/series")

matches = [
    s for s in series
    if s.get("title", "").lower() == "eastenders"
]

if len(matches) != 1:
    print(
        f"ERROR: Expected exactly one EastEnders series, "
        f"but found {len(matches)}."
    )

    for s in matches:
        print(
            f"  id={s.get('id')} "
            f"title={s.get('title')} "
            f"path={s.get('path')}"
        )

    raise SystemExit(1)


eastenders = matches[0]

SERIES_ID = eastenders["id"]

print(
    f"Found EastEnders: "
    f"id={SERIES_ID}, "
    f"path={eastenders.get('path')}, "
    f"type={eastenders.get('seriesType')}"
)

print()


# ------------------------------------------------------------
# GET SONARR'S EASTENDERS EPISODES
# ------------------------------------------------------------

episodes = sonarr_get(
    "/episode",
    params={"seriesId": SERIES_ID},
)

print(f"Loaded {len(episodes)} EastEnders episodes from Sonarr.")
print()


# Build lookup:
#
# (2026, 134) -> Sonarr episode object
#
episode_lookup = {}

for ep in episodes:

    air_date = ep.get("airDate")

    if not air_date:
        continue

    try:
        year = int(air_date[:4])
    except ValueError:
        continue

    episode_number = ep.get("episodeNumber")

    if episode_number is None:
        continue

    key = (year, episode_number)

    # Don't silently accept ambiguous mappings.
    if key in episode_lookup:
        episode_lookup[key] = None
    else:
        episode_lookup[key] = ep


# ------------------------------------------------------------
# FIND PLEX DVR FILES
# ------------------------------------------------------------

pattern = re.compile(
    r"\bS(?P<year>\d{4})E(?P<episode>\d+)\b",
    re.IGNORECASE,
)

files_to_process = []

for path in HOST_EASTENDERS_PATH.rglob("*"):

    if not path.is_file():
        continue

    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        continue

    match = pattern.search(path.name)

    if not match:
        continue

    year = int(match.group("year"))
    episode_number = int(match.group("episode"))

    files_to_process.append(
        (path, year, episode_number)
    )


if not files_to_process:
    print("No Plex-style EastEnders recordings found.")
    raise SystemExit(0)


print(
    f"Found {len(files_to_process)} Plex-style recording(s)."
)
print()


# ------------------------------------------------------------
# DETECT DUPLICATES BEFORE IMPORTING
# ------------------------------------------------------------

grouped = defaultdict(list)

for path, year, episode_number in files_to_process:
    grouped[(year, episode_number)].append(path)


# For duplicate Plex recordings of the same episode,
# keep only the largest file.
preferred_file_for_key = {}

for key, paths in grouped.items():
    preferred_file_for_key[key] = max(
        paths,
        key=lambda p: p.stat().st_size
    )


# ------------------------------------------------------------
# PROCESS EACH FILE
# ------------------------------------------------------------

for path, year, episode_number in sorted(
    files_to_process,
    key=lambda x: (x[1], x[2], str(x[0])),
):



    print("=" * 72)
    print(f"FILE: {path.name}")

    key = (year, episode_number)

    # --------------------------------------------------------
    # Duplicate Plex recordings
    # --------------------------------------------------------

    preferred_file = preferred_file_for_key[key]

    if path != preferred_file:
        print(
            f"SKIP: Smaller duplicate for "
            f"{year} episode {episode_number}"
        )
        print(
            f"      Keeping: {preferred_file.name} "
            f"({preferred_file.stat().st_size / 1024 / 1024:.1f} MB)"
        )
        print(
            f"      Skipping: {path.name} "
            f"({path.stat().st_size / 1024 / 1024:.1f} MB)"
        )
        print()
        continue

    if len(grouped[key]) > 1:
        print(
            f"DUPLICATE: Using largest file for "
            f"{year} episode {episode_number}:"
        )
        print(
            f"           {path.name} "
            f"({path.stat().st_size / 1024 / 1024:.1f} MB)"
        )

    # --------------------------------------------------------
    # Find Sonarr episode
    # --------------------------------------------------------

    episode = episode_lookup.get(key)

    if episode is None:
        print(
            f"SKIP: Cannot uniquely match "
            f"{year} episode {episode_number} in Sonarr."
        )
        print()
        continue

    episode_id = episode["id"]
    season_number = episode["seasonNumber"]
    sonarr_episode_number = episode["episodeNumber"]
    air_date = episode.get("airDate")
    title = episode.get("title")

    # SAFETY: Never import an episode Sonarr already has.
    # If this Plex-style source is a redundant leftover, verify Sonarr's
    # registered file before deleting the source.
    if episode.get("hasFile"):
        print(
            f"SKIP: Sonarr already has S{season_number:02d}"
            f"E{sonarr_episode_number:02d}"
        )

        try:
            existing_episode = sonarr_get(
                f"/episode/{episode_id}"
            )

            existing_file = existing_episode.get("episodeFile", {})
            existing_sonarr_path = existing_file.get("path")

            if not existing_sonarr_path:
                print("      CLEANUP SKIP: Sonarr did not return its file path.")
                print()
                continue

            print(f"      Sonarr file: {existing_sonarr_path}")

            source_sonarr_path = host_to_sonarr_path(path)

            # Never delete the file if Sonarr is actually registered to
            # this exact source path.
            if existing_sonarr_path == source_sonarr_path:
                print("      CLEANUP SKIP: This is Sonarr's registered file.")
                print()
                continue

            # Translate Sonarr's registered path back to the macOS path
            # so we can verify that the replacement really exists.
            sonarr_root = Path(SONARR_EASTENDERS_PATH)
            existing_relative = Path(existing_sonarr_path).relative_to(
                sonarr_root
            )
            existing_host_path = HOST_EASTENDERS_PATH / existing_relative

            if not existing_host_path.is_file():
                print(
                    "      CLEANUP SKIP: Sonarr's registered replacement "
                    "does not exist on disk."
                )
                print()
                continue

            print(f"      Verified: {existing_host_path}")
            print(f"      DELETE redundant source: {path.name}")

            path.unlink()

            print("      Deleted successfully.")
            print()
            continue

        except Exception as exc:
            print(f"      CLEANUP SKIP: Verification failed: {exc}")
            print()
            continue

    print(
        f"MATCH: Plex S{year}E{episode_number}"
    )

    print(
        f"       Sonarr S{season_number:02d}"
        f"E{sonarr_episode_number:02d}"
    )

    print(
        f"       Air date: {air_date}"
    )

    print(
        f"       Episode ID: {episode_id}"
    )

    if title:
        print(
            f"       Title: {title}"
        )

    sonarr_file_path = host_to_sonarr_path(path)

    print(
        f"       Sonarr path: {sonarr_file_path}"
    )

    # --------------------------------------------------------
    # Ask Sonarr to inspect the file.
    #
    # This is useful because Sonarr itself can determine
    # quality, language, release type, etc.
    # --------------------------------------------------------

    try:
        manual_items = sonarr_get(
            "/manualimport",
            params={
                # Supplying seriesId selects registered files, not this folder.
                # The correct series/episode is assigned in the import payload.
                "folder": str(Path(sonarr_file_path).parent),
                "filterExistingFiles": "false",
            },
        )

        # Sonarr scans the directory, so select the exact file.
        manual_items = [
            item for item in manual_items
            if item.get("path") == sonarr_file_path
        ]

    except requests.RequestException as exc:
        print(
            f"ERROR: Sonarr could not inspect this file: {exc}"
        )
        print()
        continue

    if len(manual_items) != 1:
        print(
            f"SKIP: Expected Sonarr to return exactly one "
            f"manual-import item, but got {len(manual_items)}."
        )
        print()
        continue

    item = manual_items[0]

    # --------------------------------------------------------
    # Take Sonarr's detected properties but OVERRIDE the
    # episode association with the known-correct episode ID.
    # --------------------------------------------------------

    quality = item.get("quality")

    languages = item.get("languages") or []

    release_group = item.get("releaseGroup") or ""

    indexer_flags = item.get("indexerFlags", 0)

    release_type = item.get("releaseType", "singleEpisode")

    if quality is None:
        print(
            "SKIP: Sonarr did not return quality information."
        )
        print()
        continue

    import_file = {
        "path": sonarr_file_path,
        "seriesId": SERIES_ID,
        "seasonNumber": season_number,
        "episodeIds": [episode_id],
        "quality": quality,
        "languages": languages,
        "releaseGroup": release_group,
        "indexerFlags": indexer_flags,
        "releaseType": release_type,
    }

    # --------------------------------------------------------
    # DRY RUN
    # --------------------------------------------------------

    if DRY_RUN:
        print()
        print("WOULD IMPORT:")
        print(
            f"       {path.name}"
        )
        print(
            f"       as Sonarr S{season_number:02d}"
            f"E{sonarr_episode_number:02d}"
        )
        print(
            f"       using Sonarr episode ID {episode_id}"
        )
        print()
        continue

    # --------------------------------------------------------
    # ACTUAL MANUAL IMPORT
    # --------------------------------------------------------

    payload = {
        "name": "ManualImport",
        "importMode": "move",
        "files": [
            import_file
        ],
    }

    try:
        result = sonarr_post(
            "/command",
            payload,
        )

        command_id = result.get("id")

        print()
        print(
            f"IMPORT SUBMITTED TO SONARR"
        )

        print(
            f"       Command ID: {command_id}"
        )

        # Wait for Sonarr to finish the Manual Import.
        import time

        for _ in range(30):
            time.sleep(1)

            command = sonarr_get(
                f"/command/{command_id}"
            )

            status = command.get("status")
            result_status = command.get("result")

            if status == "completed":
                break

            if status == "failed":
                print(
                    f"ERROR: Sonarr import command failed."
                )
                break
        else:
            print(
                "ERROR: Timed out waiting for Sonarr import."
            )
            continue

        if status != "completed" or result_status != "successful":
            print(
                f"ERROR: Import did not complete successfully "
                f"(status={status}, result={result_status})."
            )
            continue

        # Retrieve the episode again. It should now have an
        # episodeFileId assigned by Sonarr.
        imported_episode = sonarr_get(
            f"/episode/{episode_id}"
        )

        episode_file_id = imported_episode.get(
            "episodeFileId"
        )

        if not episode_file_id:
            print(
                "ERROR: Import completed but Sonarr did not "
                "assign an episodeFileId."
            )
            continue

        print(
            f"       Episode File ID: {episode_file_id}"
        )

        # Let Sonarr apply its normal Daily-series naming and
        # Season 42 folder structure.
        rename_result = sonarr_post(
            "/command",
            {
                "name": "RenameFiles",
                "seriesId": SERIES_ID,
                "files": [episode_file_id],
            },
        )

        rename_command_id = rename_result.get("id")

        print(
            f"       Rename submitted: {rename_command_id}"
        )

        # Wait for Sonarr to finish renaming/moving the file before
        # scanning Season 2026 again. This prevents directory-scan
        # race conditions that can cause intermittent HTTP 500 errors.
        for _ in range(30):
            time.sleep(1)

            rename_command = sonarr_get(
                f"/command/{rename_command_id}"
            )

            rename_status = rename_command.get("status")
            rename_result_status = rename_command.get("result")

            if rename_status == "completed":
                break

            if rename_status == "failed":
                print("ERROR: Sonarr rename command failed.")
                break
        else:
            print("ERROR: Timed out waiting for Sonarr rename.")
            continue

        if rename_status != "completed" or rename_result_status != "successful":
            print(
                f"ERROR: Rename did not complete successfully "
                f"(status={rename_status}, result={rename_result_status})."
            )
            continue

        print("       Rename completed successfully.")

    except requests.RequestException as exc:
        print()
        print(
            f"ERROR submitting import: {exc}"
        )

        if getattr(exc, "response", None) is not None:
            print(
                exc.response.text
            )

    print()


print("=" * 72)

if DRY_RUN:
    print(
        "DRY RUN COMPLETE. No files were imported or changed."
    )
else:
    print(
        "IMPORT REQUESTS SUBMITTED TO SONARR."
    )
