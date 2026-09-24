"""Synthetic timing example; no files, services, or model are accessed."""

from subtitle_maintenance.validation import full_check


def main():
    cues = []
    words = []
    for index in range(120):
        tokens = [str(index), "hello", "world", "again"]
        cues.append({"start": index * 10, "end": index * 10 + 5, "tokens": tokens})
        words.extend(
            (token, index * 10 + 1.5 + offset * 0.5) for offset, token in enumerate(tokens)
        )
    aligned = full_check(cues, words, 1200)["passed"]
    wrong_cut = full_check(
        cues, [(word, time + 40 if time > 600 else time) for word, time in words], 1200
    )["passed"]
    print(f"Aligned synthetic dialogue accepted: {aligned}")
    print(f"Wrong-cut synthetic dialogue accepted: {wrong_cut}")
    return 0 if aligned and not wrong_cut else 1


if __name__ == "__main__":
    raise SystemExit(main())
