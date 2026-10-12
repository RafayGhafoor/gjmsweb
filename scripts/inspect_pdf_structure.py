"""Dump the OpenDataLoader node tree for PDFs, to debug metadata extraction.

WHY THIS EXISTS
    Title/author/abstract extraction reads OpenDataLoader's PDF/UA structure
    tags (`H1`/Doctitle, `H3`/Subtitle, `H2`/Abstract, `L`/`LI`). When a paper
    extracts wrongly, look at its actual node tree here before changing rules.

USAGE
    python scripts/inspect_pdf_structure.py "Khadija Shaheen Paper.pdf"
    python scripts/inspect_pdf_structure.py --grep "Abstract" --limit 5
    python scripts/inspect_pdf_structure.py --all --first-page-only

OUTPUT
    One block per PDF: index, type, pdfua_tag, heading level, font, size and a
    trimmed content preview. `--json` dumps the raw JSON path instead.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from opendataloader_pdf import convert
from rich.console import Console

console = Console(stderr=True)
DEFAULT_CORPUS = Path("bunny-content")


def walk(node, out):
    """Depth-first, document-order walk over ODL `kids` / `list items`."""
    if isinstance(node, dict):
        if node.get("type") in ("heading", "paragraph", "list item", "title"):
            out.append(node)
        for key in ("kids", "list items"):
            for child in node.get(key) or []:
                walk(child, out)
    elif isinstance(node, list):
        for item in node:
            walk(item, out)
    return out


def convert_first_page(pdf_path: Path, workdir: Path) -> Path:
    """Convert page 1 of a PDF to JSON; return the JSON path."""
    convert(
        str(pdf_path),
        output_dir=str(workdir),
        format="json",
        pages="1",
        image_output="off",
        keep_line_breaks=True,
        quiet=True,
    )
    return workdir / f"{pdf_path.stem}.json"


def show(pdf_path: Path, raw: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix="odl-inspect-") as tmp:
        workdir = Path(tmp)
        try:
            json_path = convert_first_page(pdf_path, workdir)
        except Exception as error:  # noqa: BLE001
            console.print(f"[red]convert failed for {pdf_path.name}: {error}[/red]")
            return
        if not json_path.exists():
            console.print(f"[red]no JSON produced for {pdf_path.name}[/red]")
            return
        data = json.loads(json_path.read_text(encoding="utf-8"))

    if raw:
        console.print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    nodes = walk(data.get("kids") or [], [])
    console.print(f"\n[bold cyan]{pdf_path.name}[/bold cyan] ({len(nodes)} nodes)")
    for index, node in enumerate(nodes):
        content = (node.get("content") or "").replace("\n", " ")[:88]
        console.print(
            f"  {index:>3} {str(node.get('type')):<11} {str(node.get('pdfua_tag')):<5}"
            f" lvl={str(node.get('heading level')):<5}"
            f" {str(node.get('font'))[:24]:<24} {content!r}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="*", type=Path, help="PDF paths (or bare names)")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--grep", help="Only filenames containing this text")
    parser.add_argument("--all", action="store_true", help="Inspect every PDF")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Dump raw ODL JSON")
    args = parser.parse_args()

    targets: list[Path] = []
    for item in args.pdf:
        if item.exists():
            targets.append(item)
        else:  # allow bare filenames from the corpus
            matches = [p for p in args.corpus.rglob("*.pdf") if p.name == item.name]
            if matches:
                targets.extend(matches)
            else:
                console.print(f"[yellow]not found: {item}[/yellow]")

    if args.all or args.grep:
        pool = sorted(args.corpus.rglob("*.pdf"), key=lambda p: p.as_posix().casefold())
        if args.grep:
            pool = [p for p in pool if args.grep.casefold() in p.name.casefold()]
        targets.extend(pool[: args.limit])

    if not targets:
        parser.error("no PDFs selected; pass paths, --grep or --all")

    for pdf_path in targets:
        show(pdf_path, raw=args.json)


if __name__ == "__main__":
    main()
