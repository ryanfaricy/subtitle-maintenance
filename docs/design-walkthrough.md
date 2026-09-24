# A changed file must not overwrite the user's work

Imagine a subtitle candidate passes dialogue checks, but another program edits
the original subtitle before installation. A successful timing check on the old
contents is no longer sufficient permission to replace the current file.

`common.install` checks the video's fingerprint and the target subtitle's expected
SHA256 before staging replacement. A changed target raises an error, preserving
both the current target and the candidate. With an unchanged target, installation
preserves the original in a content-addressed backup, journals a receipt, stages a
sibling file, checks content, and replaces the target. Recovery uses the recorded
hashes instead of guessing which file was original.

The tests `test_changed_target_rejected` and `test_backup_install_and_restore` in
`subtitle_maintenance/tests/test_maintenance.py` exercise this with temporary byte
fixtures. No media decoder or account is needed to test that safety property.
The verification tests separately demonstrate that plausible timing on one part
of an episode does not justify accepting a wrong cut elsewhere.

This is an intentional separation: transcript agreement authorizes a candidate;
container verification checks structural preservation; fingerprints protect
against stale inputs; receipts make recovery possible. None alone proves the
entire operation correct, and external writers can still create small race windows.

For a small, runnable illustration of timing acceptance and wrong-cut rejection,
see [the synthetic example](../examples/README.md).
