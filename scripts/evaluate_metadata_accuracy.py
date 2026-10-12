"""Score title/author/abstract extraction across the corpus.

HOW ACCURACY IS MEASURED
    There is no hand-labelled ground truth, but the frontend archive already
    holds ~338 human-curated titles. Legacy entries append the author to the
    title ("... Multan City - Khadija Naqvi"), so a correct extraction is
    normally a PREFIX of the curated title. We therefore score:

      prefix  : normalised extracted title is a prefix of the curated title
      ratio   : difflib similarity >= 0.90 / 0.80 against the curated title

    Titles are also flagged as suspicious when they look like boilerplate
    (e.g. the extractor picked "ABSTRACT" instead of the real title).

USAGE
    python scripts/evaluate_metadata_accuracy.py
    python scripts/evaluate_metadata_accuracy.py --workers 16 --limit 40

OUTPUT
    Live progress plus a summary table, and scripts/reports/metadata_accuracy.json
    containing one record per PDF (extracted fields, reference title, scores).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gjms-automation"))

from extract_articles import (  # noqa: E402
    extract_first_page_text,
    parse_article,
)
from metadata import (  # noqa: E402
    ABSTRACT_HEAD,
    extract_metadata,
    is_boilerplate,
)

console = Console(stderr=True)
DEFAULT_CORPUS = ROOT / "bunny-content"
DEFAULT_ARCHIVE = ROOT.parent / "gjms-frontend" / "archive-data.json"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def normalise(value: str) -> str:
    """Casefold, strip accents/punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", value or "")
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def reference_titles(archive_path: Path) -> dict[str, str]:
    """Map PDF basename -> curated archive title."""
    from urllib.parse import unquote, urlsplit

    data = json.loads(Path(archive_path).read_text(encoding="utf-8"))
    refs: dict[str, str] = {}
    for issue in data:
        for article in issue.get("articles", []):
            url = str(article.get("pdf") or "")
            title = str(article.get("title") or "")
            if not url or not title:
                continue
            basename = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
            refs.setdefault(basename, title)
    return refs


def baseline_title(pdf_path: Path) -> tuple[str | None, list[str]]:
    """What the pre-existing pipeline (plain text + regex heuristics) yields."""
    with tempfile.TemporaryDirectory(prefix="gjms-baseline-") as tmp:
        try:
            text = extract_first_page_text(pdf_path, Path(tmp))
            title, authors = parse_article(
                text, fallback_author=pdf_path.stem.split(" paper")[0].strip()
            )
            return title, authors
        except Exception:  # noqa: BLE001
            return None, []


FUNCTION_WORD = re.compile(r"\b(of|in|on|the|and|for|to|a|an|its|with|by|at|is|are)\b", re.IGNORECASE)


def reference_core(title: str) -> str:
    """Strip the trailing " - Author Name" that legacy entries append.

    Legacy archive titles look like "... in Multan City - Pakistan - Khadija
    Naqvi": the last ` - ` segment is the author, the rest is the title.
    """
    parts = re.split(r"\s+[-\u2013]\s+", title or "")
    if len(parts) < 2:
        return title or ""
    tail = parts[-1].strip()
    if 1 <= len(tail.split()) <= 4 and not FUNCTION_WORD.search(tail):
        return " - ".join(parts[:-1])
    return title or ""


def compare(title: str | None, reference: str | None) -> dict[str, object]:
    """Score an extracted title against the curated archive title.

    `identical`: normalised exact equality -- the strict target
    `prefix`   : the extraction is a prefix of the curated title (exact win)
    `covers`   : the extraction begins with the curated title, i.e. the curated
                 title was truncated short ("Debt, Inflation, Job Destruction
                 and Its Impact - Arshia Yameen") and the extraction completes
                 it. Still a correct extraction.
    """
    if not title or not reference:
        # An empty title (e.g. a right-to-left paper) scores zero rather than
        # returning no score at all, so every record has the same shape.
        return {
            "identical": False, "prefix": False, "covers": False,
            "substring": False, "ratio": 0.0,
        }
    ref_norm = normalise(reference)
    new_norm = normalise(title)
    return {
        "identical": bool(new_norm) and new_norm == ref_norm,
        "prefix": bool(new_norm) and ref_norm.startswith(new_norm),
        "covers": bool(ref_norm) and new_norm.startswith(ref_norm),
        "substring": bool(new_norm) and new_norm in ref_norm,
        "ratio": difflib.SequenceMatcher(None, new_norm, ref_norm).ratio(),
    }


def stratified_sample(pdfs: list[Path], size: int) -> list[Path]:
    """Deterministically spread a sample across corpus volumes/years.

    Taking the first N alphabetically would draw from one volume and mislead:
    tagging quality varies by volume. This round-robins across the year/volume
    directories so a small sample still covers every layout generation.
    """
    groups: dict[Path, list[Path]] = {}
    for pdf in pdfs:
        groups.setdefault(pdf.parent, []).append(pdf)
    ordered = [groups[key] for key in sorted(groups, key=lambda d: d.as_posix().casefold())]

    picked: list[Path] = []
    depth = 0
    while len(picked) < size and any(depth < len(group) for group in ordered):
        for group in ordered:
            if depth < len(group) and len(picked) < size:
                picked.append(group[depth])
        depth += 1
    return sorted(picked, key=lambda p: p.as_posix().casefold())


def score(pdf_path: Path, reference: str | None, with_baseline: bool = True) -> dict[str, object]:
    record: dict[str, object] = {"file": pdf_path.name, "reference": reference}
    try:
        meta = extract_metadata(pdf_path)
    except Exception as error:  # noqa: BLE001
        record["error"] = f"{type(error).__name__}: {error}"
        return record

    record.update(
        {
            "title": meta.title,
            "authors": meta.authors,
            "abstract": meta.abstract,
            "source": meta.source,
            "rtl": meta.rtl,
            "confidence": meta.confidence,
            "notes": meta.notes,
            "n_authors": len(meta.authors),
            "abstract_chars": len(meta.abstract),
            "suspicious": bool(
                ABSTRACT_HEAD.match(meta.title) or is_boilerplate(meta.title)
            ),
        }
    )

    base_title = base_authors = None
    if with_baseline:
        base_title, base_authors = baseline_title(pdf_path)
    record["baseline_title"] = base_title
    record["baseline_authors"] = base_authors
    record["baseline_suspicious"] = bool(
        base_title and (ABSTRACT_HEAD.match(base_title) or is_boilerplate(base_title))
    )
    # Entries created by this automation have an archive title that IS the old
    # pipeline's output, so scoring "old" against them is circular.
    record["self_referential"] = bool(
        reference and base_title and normalise(reference) == normalise(base_title)
    )

    if reference:
        record["new"] = compare(meta.title, reference)
        record["old"] = compare(base_title, reference)
        # Fairer comparison: drop the author suffix legacy entries append.
        core = reference_core(reference)
        record["reference_core"] = core
        if core and normalise(core) != normalise(reference):
            record["new_core"] = compare(meta.title, core)
            record["old_core"] = compare(base_title, core)
        record.update(record["new"])
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--sample", type=int, help="Stratified subset size (fast iteration)")
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="Skip the old-pipeline comparison (roughly halves runtime)",
    )
    parser.add_argument("--limit", type=int, help="Only evaluate the first N PDFs")
    parser.add_argument("--show", type=int, default=25, help="Mismatches to print")
    parser.add_argument("--out", type=Path, help="Report path (default reports/metadata_accuracy.json)")
    args = parser.parse_args()

    refs = reference_titles(args.archive)
    all_pdfs = sorted(args.corpus.rglob("*.pdf"), key=lambda p: p.as_posix().casefold())
    pdfs = all_pdfs
    if args.sample:
        pdfs = stratified_sample(all_pdfs, args.sample)
    elif args.limit:
        pdfs = pdfs[: args.limit]

    scope = (
        f"{len(pdfs)} of {len(all_pdfs)} PDFs"
        if len(pdfs) != len(all_pdfs)
        else f"{len(pdfs)} PDFs (full corpus)"
    )
    console.print(
        f"[cyan]Scoring {scope} against {len(refs)} curated titles "
        f"({args.workers} workers){' [no baseline]' if args.no_baseline else ''}...[/cyan]"
    )

    records: list[dict[str, object]] = []
    run = partial(score, with_baseline=not args.no_baseline)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run, p, refs.get(p.name)): p for p in pdfs
        }
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("[cyan]{task.fields[current]:<44}[/cyan]"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Scoring", total=len(futures), current="")
            for future in as_completed(futures):
                pdf_path = futures[future]
                records.append(future.result())
                progress.update(task, current=pdf_path.name[:44])
                progress.advance(task)

    records.sort(key=lambda r: str(r["file"]).casefold())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = args.out or (REPORT_DIR / "metadata_accuracy.json")
    report_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    errors = [r for r in records if r.get("error")]
    ok = [r for r in records if not r.get("error")]
    self_ref = [r for r in ok if r.get("self_referential")]
    compared = [
        r for r in ok if r.get("reference") and not r.get("self_referential")
    ]
    unmatched = [r for r in ok if not r.get("reference")]

    def rate(part: int, whole: int) -> str:
        return f"{part}/{whole} ({100 * part / whole:.1f}%)" if whole else "n/a"

    def side(records_in: list[dict], key: str) -> dict[str, int]:
        """Score one side ('new' or 'old') across compared records."""
        scores = [r[key] for r in records_in if r.get(key)]
        return {
            "identical": sum(1 for s in scores if s.get("identical")),
            "prefix": sum(1 for s in scores if s["prefix"]),
            "covers": sum(1 for s in scores if s.get("covers")),
            "either": sum(1 for s in scores if s["prefix"] or s.get("covers")),
            "substring": sum(1 for s in scores if s["substring"]),
            "r90": sum(1 for s in scores if s["ratio"] >= 0.90),
            "r80": sum(1 for s in scores if s["ratio"] >= 0.80),
            "r60": sum(1 for s in scores if s["ratio"] >= 0.60),
            "mean_ratio": (
                sum(s["ratio"] for s in scores) / len(scores) if scores else 0.0
            ),
            "n": len(scores),
        }

    console.print(f"\n[bold]═══ EXTRACTION ACCURACY ({len(records)} PDFs) ═══[/bold]")
    console.print(f"errors                     : [red]{len(errors)}[/red]")
    console.print(f"no curated title to compare: {len(unmatched)}")
    console.print(
        f"excluded self-referential   : {len(self_ref)} "
        "(archive title == old pipeline output)"
    )

    new_scores = side(compared, "new")
    old_scores = side(compared, "old")
    total = new_scores["n"]

    console.print(
        f"\n[bold]TITLE vs {total} CURATED TITLES[/bold]"
        f"   {'new':>12} {'old (before)':>14}"
    )
    for label, key in (
        ("IDENTICAL to curated", "identical"),
        ("exact prefix match", "prefix"),
        ("extraction completes ref", "covers"),
        ("prefix or completes ref", "either"),
        ("substring match", "substring"),
        ("similarity >= 0.90", "r90"),
        ("similarity >= 0.80", "r80"),
        ("similarity >= 0.60", "r60"),
    ):
        console.print(
            f"  {label:<24} {rate(new_scores[key], total):>18}"
            f" {rate(old_scores[key], total):>16}"
        )
    console.print(
        f"  {'mean similarity':<24} {new_scores['mean_ratio']:>18.3f}"
        f" {old_scores['mean_ratio']:>16.3f}"
    )

    # Fairer view: curated titles that have the author appended ("- Khadija
    # Naqvi") are truncated by design, so also score against the core title.
    core_new = side(compared, "new_core")
    core_old = side(compared, "old_core")
    if core_new["n"]:
        total_core = core_new["n"]
        console.print(
            f"\n[bold]TITLE vs CURATED CORE (author suffix stripped, "
            f"{total_core} titles)[/bold]   {'new':>12} {'old (before)':>14}"
        )
        for label, key in (
            ("IDENTICAL to curated", "identical"),
            ("exact prefix match", "prefix"),
            ("extraction completes ref", "covers"),
            ("prefix or completes ref", "either"),
            ("similarity >= 0.90", "r90"),
            ("similarity >= 0.80", "r80"),
        ):
            console.print(
                f"  {label:<20} {rate(core_new[key], total_core):>18}"
                f" {rate(core_old[key], total_core):>16}"
            )
        console.print(
            f"  {'mean similarity':<20} {core_new['mean_ratio']:>18.3f}"
            f" {core_old['mean_ratio']:>16.3f}"
        )

    suspicious = [r for r in ok if r.get("suspicious")]
    base_sus = [r for r in ok if r.get("baseline_suspicious")]
    console.print(f"\n[bold]SUSPICIOUS TITLES[/bold] (boilerplate/abstract picked)")
    console.print(f"  new : [red]{rate(len(suspicious), len(ok))}[/red]")
    console.print(f"  old : [red]{rate(len(base_sus), len(ok))}[/red]")

    no_author = [r for r in ok if not r.get("authors")]
    no_abs = [r for r in ok if not r.get("abstract")]
    rtl = [r for r in ok if r.get("rtl")]
    console.print(f"\n[bold]FIELD COVERAGE[/bold]")
    console.print(f"  authors found      : [green]{rate(len(ok) - len(no_author), len(ok))}[/green]")
    console.print(f"  abstract found     : [yellow]{rate(len(ok) - len(no_abs), len(ok))}[/yellow]")
    console.print(f"  right-to-left pages: [yellow]{rate(len(rtl), len(ok))}[/yellow]")
    console.print(f"  high confidence    : {rate(sum(1 for r in ok if r.get('confidence') == 'high'), len(ok))}")
    console.print(f"  source breakdown   : {dict(Counter(r.get('source') for r in ok))}")

    # Where the new extractor beats / loses to the old one.
    wins = [r for r in compared if r.get("new") and r.get("old") and r["new"]["ratio"] > r["old"]["ratio"] + 0.01]
    losses = [r for r in compared if r.get("new") and r.get("old") and r["new"]["ratio"] < r["old"]["ratio"] - 0.01]
    console.print(f"\n[bold]NEW vs OLD[/bold]  improved: [green]{len(wins)}[/green]  "
                  f"regressed: [red]{len(losses)}[/red]  "
                  f"tied: {len(compared) - len(wins) - len(losses)}")

    console.print(f"\n[bold yellow]─── New extractor REGRESSIONS vs old (worst {args.show}) ───[/bold yellow]")
    for r in sorted(losses, key=lambda r: r["new"]["ratio"] - r["old"]["ratio"])[: args.show]:
        console.print(f"\n[cyan]{r['file']}[/cyan] new={r['new']['ratio']:.2f} old={r['old']['ratio']:.2f}")
        console.print(f"   new: {(r.get('title') or '')[:110]!r}")
        console.print(f"   old: {(r.get('baseline_title') or '')[:110]!r}")
        console.print(f"   ref: {(r.get('reference') or '')[:110]!r}")

    worst = sorted(
        [r for r in compared if not (r.get("new") or {}).get("prefix")],
        key=lambda r: (r.get("new") or {}).get("ratio") or 0,
    )
    console.print(f"\n[bold yellow]─── Worst {args.show} title mismatches (new) ───[/bold yellow]")
    for r in worst[: args.show]:
        console.print(f"\n[cyan]{r['file']}[/cyan] ratio={(r.get('new') or {}).get('ratio'):.2f} "
                      f"src={r.get('source')} rtl={r.get('rtl')} conf={r.get('confidence')}")
        console.print(f"   new: {(r.get('title') or '')[:120]!r}")
        console.print(f"   ref: {(r.get('reference') or '')[:120]!r}")

    if suspicious:
        console.print(f"\n[bold yellow]─── Suspicious titles ───[/bold yellow]")
        for r in suspicious[: args.show]:
            console.print(f"  {r['file']}: {(r.get('title') or '')[:80]!r}")

    if no_abs:
        console.print(f"\n[bold yellow]─── Missing abstracts (first {args.show}) ───[/bold yellow]")
        for r in no_abs[: args.show]:
            console.print(f"  {r['file']} [{r.get('source')}]")

    console.print(f"\n[green]Report: {report_path}[/green]")


if __name__ == "__main__":
    main()
