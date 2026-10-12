"""Manual metadata overrides that survive re-extraction.

WHY THIS EXISTS
    `run_automation.py` and `backfill_archive.py` both rebuild titles, authors
    and abstracts from the PDFs. A hand-corrected title would therefore be
    overwritten the next time either runs. Overrides are stored separately and
    applied last, so a human edit always wins over extraction.

STORAGE
    `overrides.json` next to this file, committed to the repo:

        {
          "Khadija Shaheen Paper.pdf": {
            "title": "...",
            "authors": "...",
            "abstract": "...",
            "note": "why this was corrected"
          }
        }

    Keyed by PDF filename, because that is what the automation already matches
    archive entries on. Two different volumes could share a filename, which
    would make the key ambiguous, so `ambiguous_keys()` reports that.

API
    load(path)                     -> dict of overrides
    save(data, path)               -> None
    for_file(data, filename)       -> dict (possibly empty)
    merge(data, filename, **flds)  -> dict, dropping empty fields
    apply_to(article_fields, data, filename) -> dict with overrides applied
"""

from __future__ import annotations

import json
from pathlib import Path

OVERRIDES_PATH = Path(__file__).with_name("overrides.json")

# Only these fields may be overridden; `slug` and `pdf` are deliberately not,
# because changing them would break a published URL.
OVERRIDABLE = ("title", "authors", "abstract")


def load(path: Path = OVERRIDES_PATH) -> dict[str, dict]:
    """Read the override file; an absent file means no overrides."""
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(data: dict[str, dict], path: Path = OVERRIDES_PATH) -> None:
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def for_file(data: dict[str, dict], filename: str) -> dict:
    return data.get(filename) or {}


def merge(data: dict[str, dict], filename: str, **fields: str | None) -> dict:
    """Set or clear override fields for one PDF, returning the updated dict.

    Passing an empty string or None removes that field, so the extracted value
    is used again.
    """
    entry = dict(data.get(filename) or {})
    for key, value in fields.items():
        if key not in OVERRIDABLE and key != "note":
            raise ValueError(f"cannot override {key!r}; allowed: {OVERRIDABLE}")
        if value is None or value == "":
            entry.pop(key, None)
        else:
            entry[key] = value
    if entry:
        data[filename] = entry
    else:
        data.pop(filename, None)
    return data


def apply_to(fields: dict, data: dict[str, dict], filename: str) -> dict:
    """Return `fields` with any override for `filename` applied."""
    entry = for_file(data, filename)
    if not entry:
        return fields
    merged = dict(fields)
    for key in OVERRIDABLE:
        if entry.get(key):
            merged[key] = entry[key]
    return merged


def ambiguous_keys(filenames: list[str]) -> dict[str, int]:
    """Filenames appearing more than once, which cannot be keyed unambiguously."""
    counts: dict[str, int] = {}
    for name in filenames:
        counts[name] = counts.get(name, 0) + 1
    return {name: n for name, n in counts.items() if n > 1}
