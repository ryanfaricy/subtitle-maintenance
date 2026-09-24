# Synthetic timing example

After installing the project, run from the checkout:

```sh
python examples/demo_verification.py
```

Expected output:

```text
Aligned synthetic dialogue accepted: True
Wrong-cut synthetic dialogue accepted: False
```

The example creates original, artificial cue and word timestamps in memory. The
second case adds a 40-second discontinuity halfway through. It illustrates one
verification gate; it is not an end-to-end media or speech-recognition benchmark.
No media, subtitles, accounts, model downloads, or filesystem writes are needed.
