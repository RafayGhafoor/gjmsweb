"""Backfill title, authors and abstract for every archived article.

One-off repair pass for the whole archive (not the incremental path used by
`run_automation.py`, which only handles newly uploaded PDFs).

WHAT IT CHANGES
    1. `title`   - replaced with the extracted title when extraction is
                   high-confidence and not obviously truncated; otherwise the
                   curated title is kept.
    2. `title`   - the legacy "... - Author Name" author suffix is stripped out
                   of the title even when the title itself is kept.
    3. `authors` - filled from extraction, or from that stripped suffix.
    4. abstracts - written to a SEPARATE file keyed by PDF URL, so the archive
                   page can lazy-load them.

    Entries whose PDF cannot be found locally are left completely untouched.

SAFETY
    - `confidence != "high"` or `rtl` -> title kept.
    - If the curated title is a longer, extended version of the extracted one,
      the extraction probably lost text (or the curated title was truncated the
      other way), so the curated title is kept.
    - Always run `--dry-run` first: it prints the change summary and samples
      without writing anything.

USAGE
    python gjms-automation/backfill_archive.py --dry-run
    python gjms-automation/backfill_archive.py
    python gjms-automation/backfill_archive.py --limit 25 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metadata import (
    ABSTRACT_HEAD,
    MARKER_AFTER_NAME,
    ArticleMetadata,
    display_case,
    extract_metadata,
    is_boilerplate,
)

console = Console(stderr=True)

DEFAULT_CORPUS = Path("bunny-content")
FRONTEND = Path("/Users/emumba/Downloads/projects/gjms-frontend")
DEFAULT_ARCHIVE = FRONTEND / "archive-data.json"
DEFAULT_ABSTRACTS = FRONTEND / "abstracts.json"

AUTHOR_SUFFIX = re.compile(r"\s+[-\u2013]\s+([^-\u2013]{2,60})$")
FUNCTION_WORD = re.compile(
    r"\b(of|in|on|the|and|for|to|a|an|its|with|by|at|is|are)\b", re.IGNORECASE
)


def normalise(value: str) -> str:
    """Casefold, strip accents/punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", value or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_author_suffix(title: str) -> tuple[str, str | None]:
    """Split a legacy "Title - Author Name" into (title, author).

    Legacy archive titles append the author after " - ". The last segment is
    only treated as an author when it looks like a person's name rather than a
    title fragment (e.g. "... Multan City - Pakistan - Khadija Naqvi" keeps
    "- Pakistan" in the title).
    """
    if not title:
        return title, None
    parts = re.split(r"\s+[-\u2013]\s+", title)
    if len(parts) < 2:
        return title, None
    tail = parts[-1].strip()
    if 1 <= len(tail.split()) <= 4 and not FUNCTION_WORD.search(tail):
        return " - ".join(parts[:-1]).strip(), tail
    return title, None


def suspicious_title(title: str) -> bool:
    return bool(ABSTRACT_HEAD.match(title) or is_boilerplate(title))


# Markers that mean the title swallowed the author/affiliation block. These must
# be specific: a bare "university" or "Multan" appears in legitimate titles such
# as "A Case Study of Multan District-Pakistan".
BROKEN_MARKERS = (
    "@",                    # email address in the title
    "orcid",
    "research scholar",
    "department of",
    "university of",
    "email:",
    "e-mail",
    "cell #",
    "phd scholar",
    "ms scholar",
    "mphil scholar",
    "corresponding author",
)


def curated_is_broken(title: str) -> bool:
    """True when the curated title itself is unusable.

    This is the case the user originally reported: Khadija Shaheen's title had
    the author line, affiliations, emails and ORCIDs appended (461 characters).
    Journal furniture counts as broken too -- the backfill ran before the
    extractor learned to drop "Original Article", so one live entry still reads
    "Original Article Rural/Urban Migration and Wage Determination".
    """
    if not title or not title.strip():
        return True
    if len(title) > 220:
        return True
    if is_boilerplate(title):
        return True
    low = title.casefold()
    return any(marker in low for marker in BROKEN_MARKERS)


def tokens(value: str) -> set[str]:
    return set(normalise(value).split())


def extracted_is_contaminated(title: str) -> bool:
    """True when the extraction itself looks like it swallowed the author line.

    Needed because the superset rule below would otherwise let a contaminated
    extraction replace a good curated title, e.g. "... HOLY PROPHET. Ijaz
    Hussain1 , D r. H a" (the author line is glued on and its honorific is
    spaced out as "D r.", so the author-line detector cannot see it).
    """
    if MARKER_AFTER_NAME.search(title):
        return True
    return bool(re.search(r"\.\s+[A-Z][a-z]+\s+[A-Z]\.?\s*$", title))


def choose_title(
    curated: str, meta: ArticleMetadata
) -> tuple[str, str]:
    """Return (title, reason) where reason is 'extracted', 'kept' or 'cleaned'.

    The curated titles are human-approved, so extraction only REPLACES one when
    the curated title is demonstrably broken, or when the extraction is a strict
    superset of it (the curated title lost words). Here a plain "different" is
    not good enough: many PDFs set titles ALL CAPS with publisher typos
    ("EONOMIC", "CELLURAL"), and swapping a clean curated title for that text is
    a downgrade.
    """
    core, _ = split_author_suffix(curated)
    kept = (core, "cleaned" if core != curated else "kept")
    extracted = (meta.title or "").strip()

    if not extracted or meta.rtl or meta.confidence != "high" or suspicious_title(extracted):
        return kept
    if extracted_is_contaminated(extracted):
        return kept

    if curated_is_broken(curated):
        return display_case(extracted), "extracted"

    curated_norm, extracted_norm = normalise(core), normalise(extracted)
    if curated_norm and curated_norm == extracted_norm:
        return kept

    # The extraction carries every word of the curated title and more, so the
    # curated title was losing content.
    curated_tokens, extracted_tokens = tokens(core), tokens(extracted)
    if (
        curated_tokens
        and curated_tokens <= extracted_tokens
        and len(extracted_tokens) > len(curated_tokens)
    ):
        return display_case(extracted), "extracted"

    return kept


def resolve_local(pdf_url: str, corpus: Path) -> Path | None:
    """Map an archive PDF URL to its file in the local mirror."""
    if not pdf_url:
        return None
    path = unquote(urlsplit(pdf_url).path).lstrip("/")
    if not path:
        return None
    direct = corpus / path
    if direct.is_file():
        return direct

    # Near-miss renames (case / doubled spaces / typos) in the same directory.
    parent = (corpus / path).parent
    wanted = normalise(Path(path).stem)
    if parent.is_dir():
        for candidate in parent.glob("*.pdf"):
            if normalise(candidate.stem) == wanted:
                return candidate
    return None


@dataclass
class Summary:
    articles: int = 0
    resolved: int = 0
    unresolved: list[str] = field(default_factory=list)
    titles_extracted: int = 0
    titles_cleaned: int = 0
    authors_added: int = 0
    abstracts_added: int = 0
    low_confidence: int = 0
    rtl_kept: int = 0
    samples: list[tuple[str, str]] = field(default_factory=list)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--abstracts", type=Path, default=DEFAULT_ABSTRACTS)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, help="Only process the first N articles")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--samples", type=int, default=12, help="Sample changes to print")
    args = parser.parse_args()

    issues = json.loads(args.archive.read_text(encoding="utf-8"))

    # Flatten to (issue, article) so extraction can run concurrently.
    entries: list[tuple[dict, dict]] = []
    for issue in issues:
        for article in issue.get("articles", []):
            entries.append((issue, article))
    if args.limit:
        entries = entries[: args.limit]

    console.print(
        f"[cyan]Resolving {len(entries)} articles against "
        f"{args.corpus}...[/cyan]"
    )
    targets: dict[int, Path] = {}
    summary = Summary(articles=len(entries))
    for index, (_, article) in enumerate(entries):
        local = resolve_local(str(article.get("pdf") or ""), args.corpus)
        if local:
            targets[index] = local
        else:
            name = unquote(urlsplit(str(article.get("pdf") or "")).path).rsplit("/", 1)[-1]
            summary.unresolved.append(name or "(no pdf url)")
    summary.resolved = len(targets)
    console.print(
        f"[green]Resolved {summary.resolved}/{summary.articles} articles to local PDFs[/green]"
        f" ({len(summary.unresolved)} not found on disk)"
    )

    console.print(
        f"[cyan]Extracting metadata from {len(targets)} PDFs "
        f"with {args.workers} workers...[/cyan]"
    )
    metas: dict[int, ArticleMetadata] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(extract_metadata, path): index for index, path in targets.items()}
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("[cyan]{task.fields[current]:<44}[/cyan]"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Extracting", total=len(futures), current="")
            for future in as_completed(futures):
                index = futures[future]
                try:
                    metas[index] = future.result()
                except Exception as error:  # noqa: BLE001 - never abort the run
                    console.print(
                        f"[red]Failed {targets[index].name}: {error}[/red]"
                    )
                progress.update(task, current=targets[index].name[:44])
                progress.advance(task)

    # Apply changes in memory.
    abstracts: dict[str, str] = {}
    for index, (_, article) in enumerate(entries):
        meta = metas.get(index)
        if meta is None:
            continue

        curated_title = str(article.get("title") or "")
        title, reason = choose_title(curated_title, meta)
        if reason == "extracted" and title != curated_title:
            summary.titles_extracted += 1
            if len(summary.samples) < args.samples:
                summary.samples.append((curated_title, title))
        elif reason == "cleaned" and title != curated_title:
            summary.titles_cleaned += 1
        if meta.rtl:
            summary.rtl_kept += 1
        if meta.confidence != "high":
            summary.low_confidence += 1

        if title:
            article["title"] = title

        # Authors: prefer extracted names, else the suffix stripped off the title.
        _, suffix_author = split_author_suffix(curated_title)
        extracted_authors = meta.authors_text.strip()
        if extracted_authors:
            if article.get("authors") != extracted_authors:
                summary.authors_added += 1
            article["authors"] = extracted_authors
        elif suffix_author and not article.get("authors"):
            article["authors"] = suffix_author
            summary.authors_added += 1

        url = str(article.get("pdf") or "")
        if url and meta.abstract:
            if abstracts.get(url) != meta.abstract:
                summary.abstracts_added += 1
            abstracts[url] = meta.abstract

    # Report.
    console.print("\n[bold]═══ BACKFILL SUMMARY ═══[/bold]")
    console.print(f"articles scanned        : {summary.articles}")
    console.print(f"resolved to local PDF   : {summary.resolved}")
    console.print(f"titles replaced         : [green]{summary.titles_extracted}[/green]")
    console.print(f"titles cleaned (author suffix removed) : {summary.titles_cleaned}")
    console.print(f"authors set             : {summary.authors_added}")
    console.print(f"abstracts collected     : {summary.abstracts_added}")
    console.print(f"kept (low confidence)   : {summary.low_confidence}")
    console.print(f"kept (right-to-left)    : {summary.rtl_kept}")

    if summary.samples:
        console.print("\n[bold yellow]─── Sample title replacements ───[/bold yellow]")
        for before, after in summary.samples:
            console.print(f"  [red]- {before[:105]}[/red]")
            console.print(f"  [green]+ {after[:105]}[/green]")

    if summary.unresolved:
        console.print(
            f"\n[bold yellow]─── {len(summary.unresolved)} articles with no local PDF "
            f"(left untouched; first 10) ───[/bold yellow]"
        )
        for name in summary.unresolved[:10]:
            console.print(f"  {name}")

    if args.dry_run:
        console.print("\n[bold yellow]Dry run: nothing written.[/bold yellow]")
        return

    args.archive.write_text(
        json.dumps(issues, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    args.abstracts.write_text(
        json.dumps(abstracts, indent=1, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    console.print(f"\n[bold green]Wrote {args.archive}[/bold green]")
    console.print(f"[bold green]Wrote {args.abstracts} ({len(abstracts)} abstracts)[/bold green]")


if __name__ == "__main__":
    main()
