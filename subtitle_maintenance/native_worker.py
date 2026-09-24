"""Short-lived process releases Metal memory after transcription."""

import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("model")
    args = parser.parse_args(argv)
    # Heavy optional dependencies are loaded only when the worker is invoked.
    import mlx.core as mx
    import mlx_whisper

    mx.set_cache_limit(512 * 1024 * 1024)
    data = mlx_whisper.transcribe(
        str(args.audio),
        path_or_hf_repo=args.model,
        language="en",
        word_timestamps=True,
        condition_on_previous_text=False,
        verbose=False,
    )
    with args.output.open("w") as handle:
        json.dump(data, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
