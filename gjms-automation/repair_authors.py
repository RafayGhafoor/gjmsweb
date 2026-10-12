#!/usr/bin/env python3
"""Repair author metadata in the frontend archive.

Two defects, both derived from the journal's own curated records — no name is
invented, no title is rewritten beyond removing the author suffix:

1. **Author appended to the title, `authors` empty.** Legacy entries were curated
   as ``"Title … - Author Name"``; 24 of them never had the author moved into the
   ``authors`` field. `scripts/evaluate_metadata_accuracy.py` already recognises
   this shape (`reference_core()`), so the split is the same rule the scoring
   already relies on.

2. **Affiliation/degree debris inside `authors`.** Page 1 of a paper often ends
   the author line with institution text or degree markers, which the extractor
   can pick up: ``"… , 1.MS"``, ``"… , UAF Sub-Campus Burewala-Vehari, Huda
   Farooq, Research Officer"``, ``"MS Business Administration, Campus Director,
   NCBA&E-Lahore, …"``. Segments that clearly name an institution, a role or a
   fused ordinal are dropped; everything else is kept untouched.

Anything ambiguous is left alone and printed for human review: a wrong author is
worse than a missing one. Cosmetic inconsistencies (a lowercase surname, "Prof:"
instead of "Prof.", a doubled honorific) are reported but never rewritten — a
person's name is theirs to spell.

Usage:
    python gjms-automation/repair_authors.py --dry-run
    python gjms-automation/repair_authors.py            # writes + keeps a .bak
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

DEFAULT_ARCHIVE = Path("/Users/emumba/Downloads/projects/gjms-frontend/archive-data.json")

# "Title … - Author Name" / "Title … -Author Name" (space after the dash varies).
# The separator is searched from the end, because titles contain hyphens of their
# own ("Hurmat-e-Rasool", "Sub-Continent") and an earlier match would swallow them.
SEPARATOR = re.compile(r"\s+[-–]\s*|\s*[-–]\s+")

# A trailing segment is only treated as a name when it looks like one.
NAME_SHAPE = re.compile(r"^[A-Z][\w.'’+-]*(?:\s+[A-Z][\w.'’+-]*){0,3}$")
NOT_A_NAME = re.compile(
    r"\b(case|study|analysis|review|papers?|journal|volume|issue|editorial|guideline|"
    r"university|college|department|institute|campus|district|city|school|level|"
    r"economy|economic|growth|pakistan|india|china|asia|high|secondary|students?|"
    r"banks?|firms?|industry|sector|performance|impact|role|effect|perspective)\b",
    re.I,
)

# Segments that are institution / role / degree debris rather than a person.
# The named institutions are the ones actually observed in this archive's author
# lines; they are listed explicitly because an acronym like COMSATS carries no
# keyword that a generic rule could match.
DEBRIS = re.compile(
    r"^\s*\d|"  # fused ordinals: "1.MS", "2Ph.D", "3. Ex"
    r"corresponding author|"
    r"^\s*isp\b|"
    r"\b(campus|university|universities|college|department|faculty|institute|school|academy|"
    r"director|officer|deputy|dean|chairman|professor of|research officer|"
    r"mphil|mba|msc|bsc|principal|business administration|administration|"
    r"comsats|ncba|uaf|macquarie|bahauddin|zakariya)\b|"
    # "MS Business Administration", "MS/" — but not "Ms. Ayesha", so the marker
    # must be followed by a non-name token.
    r"^\s*ms\.?\s*(?:/|$|business|administration|management|scholar|student)",
    re.I,
)

# Degree markers that trail an otherwise valid name: "… Awan Ph. D" -> "… Awan",
# and whole segments that are nothing but a marker ("Ph. D", "1. MS").
DEGREE_SUFFIX = re.compile(r"\s+(?:ph\.?\s*d\.?|m\.?\s*s\.?|m\.?phil|m\.?b\.?a|m\.?sc|b\.?sc|b\.?s\.?)$", re.I)
DEGREE_ONLY = re.compile(r"^\s*(?:ph\.?\s*d\.?|m\.?\s*s\.?|m\.?phil|m\.?b\.?a|m\.?sc|b\.?sc|b\.?s\.?|ex\b\.?)\s*$", re.I)

# A place name is only debris when it is part of an address run — that is, it
# directly follows another institution fragment ("Burewala Campus, Vehari").
# Loose tokens are left alone so a surname that happens to match a city is safe.
PLACE = re.compile(
    r"^\s*(vehari|burewala|bahawalnagar|bahawalpur|haroonabad|multan|lahore|islamabad|"
    r"karachi|faisalabad|rawalpindi|peshawar|quetta|sargodha|gujranwala|hyderabad|"
    r"punjab|sindh|balochistan|khyber|pakistan)\s*$",
    re.I,
)


def split_trailing_author(title: str) -> tuple[str, str] | None:
    """Return (title_without_author, author) when the title ends with a name."""
    separators = list(SEPARATOR.finditer(title))
    if not separators:
        return None
    last = separators[-1]
    core, name = title[: last.start()].strip(), title[last.end() :].strip()
    if len(core) < 25:  # a real title, not a fragment
        return None
    if any(ch.isdigit() for ch in name) and not re.search(r"\d{4}", name):
        return None
    if not NAME_SHAPE.match(name):
        return None
    if NOT_A_NAME.search(name):
        return None
    if len(name.split()) > 4:
        return None
    return core, name.strip(" .,")


def clean_authors(authors: str) -> str:
    """Drop institution/role/degree segments, keep real names, de-duplicate."""
    kept: list[str] = []
    previous_was_debris = False
    for part in (p.strip() for p in authors.split(",")):
        if not part:
            continue
        is_debris = bool(DEGREE_ONLY.match(part) or DEBRIS.search(part))
        is_address_tail = previous_was_debris and bool(PLACE.match(part))
        if is_debris or is_address_tail:
            previous_was_debris = True
            continue
        previous_was_debris = False
        stripped = DEGREE_SUFFIX.sub("", part).strip(" .,")
        if not stripped:
            continue
        if stripped not in kept:
            kept.append(stripped)
    return ", ".join(kept)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    args = parser.parse_args()

    if not args.archive.exists():
        print(f"archive not found: {args.archive}", file=sys.stderr)
        return 1

    data = json.loads(args.archive.read_text())
    articles = [article for issue in data for article in issue["articles"]]

    title_fixes: list[tuple[str, str, str, str]] = []
    author_fixes: list[tuple[str, str, str]] = []
    review: list[tuple[str, str, str]] = []

    for article in articles:
        title = (article.get("title") or "").strip()
        authors = (article.get("authors") or "").strip()

        if not authors:
            split = split_trailing_author(title)
            if split:
                core, name = split
                title_fixes.append((article.get("slug", ""), title, core, name))
                if not args.dry_run:
                    article["title"] = core
                    article["authors"] = name
            elif re.search(r"\s[-–]\s*\S", title):
                review.append((article.get("slug", ""), title, authors))
            continue

        cleaned = clean_authors(authors)
        if cleaned != authors:
            if not cleaned:
                review.append((article.get("slug", ""), title, authors))
                continue
            author_fixes.append((article.get("slug", ""), authors, cleaned))
            if not args.dry_run:
                article["authors"] = cleaned

    total = len(articles)
    print(f"archive: {args.archive}")
    print(f"records: {total}  ·  author suffix moved out of the title: {len(title_fixes)}"
          f"  ·  debris trimmed from authors: {len(author_fixes)}  ·  needs a human: {len(review)}\n")

    if title_fixes:
        print("1) title carried the author — the name moves into `authors`, the title loses the suffix")
        for slug, title, core, name in title_fixes:
            print(f"   author  : {name}")
            print(f"   title   : {title}")
            print(f"          -> {core}")
        print()

    if author_fixes:
        print("2) institution/degree debris in `authors`")
        for slug, before, after in author_fixes:
            print(f"   before  : {before}")
            print(f"   after   : {after}\n")

    if review:
        print("3) left untouched — decide by hand (a wrong author is worse than a missing one)")
        for slug, title, authors in review:
            print(f"   authors : {authors or '(empty)'}")
            print(f"   title   : {title}\n")

    if args.dry_run:
        print("dry run: nothing written. Re-run without --dry-run to apply.")
        return 0

    backup = args.archive.with_suffix(".json.bak")
    shutil.copy2(args.archive, backup)
    # Written the way the rest of the pipeline writes it (indent=2, ASCII
    # escapes) so the diff shows only the records that actually changed.
    args.archive.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n")
    print(f"applied. backup at {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
