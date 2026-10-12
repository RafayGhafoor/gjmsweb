"""Extract article titles and authors from the first page of PDF files."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import tempfile
from pathlib import Path

from opendataloader_pdf import convert
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from ocrmac import ocrmac
import fitz
from titlecase import titlecase


console = Console(stderr=True)


AUTHOR_LINE = re.compile(
    r"^[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)*\d+(?:\s*,|$)"
)
SEPARATOR = re.compile(r"^[\s_-]{8,}$")
AFFILIATION_WORDS = (
    "university",
    "department",
    "institute",
    "scholar",
    "email",
    "cell",
    "tel",
    "school",
)


def extract_first_page_text(pdf_path: Path, output_dir: Path) -> str:
    """Convert one PDF's first page to text and return the generated text."""
    convert(
        str(pdf_path),
        output_dir=str(output_dir),
        format="text",
        pages="1",
        image_output="off",
        keep_line_breaks=True,
        quiet=True,
    )
    text_path = output_dir / f"{pdf_path.stem}.txt"
    text = text_path.read_text(encoding="utf-8")
    if text.strip():
        return text

    image_path = output_dir / f"{pdf_path.stem}.png"
    with fitz.open(pdf_path) as document:
        document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(image_path)
    recognized = ocrmac.OCR(str(image_path)).recognize()
    return "\n".join(item[0] for item in recognized)


def parse_article(text: str, fallback_author: str | None = None) -> tuple[str, list[str]]:
    """Extract the title and author names from first-page text."""
    text = re.sub(r"(?i)(?<!\n)(research article|abstract)(?!\n)", r"\n\1\n", text)
    text = re.sub(r"([_-]{8,})", r"\n\1\n", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    abstract_start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(r"^a(?:bstract|sbract)\b", line, re.IGNORECASE)
        ),
        len(lines),
    )

    article_start = next(
        (
            index
            for index in range(abstract_start - 1, -1, -1)
            if lines[index].casefold() == "research article"
        ),
        None,
    )
    if article_start is None:
        return parse_legacy_article(lines, abstract_start, fallback_author)

    article_lines = lines[article_start + 1:abstract_start]
    author_index = next(
        (
            index
            for index, line in enumerate(article_lines)
            if AUTHOR_LINE.match(line)
        ),
        None,
    )
    if author_index is None:
        if fallback_author and article_lines:
            return titlecase(" ".join(article_lines)), [fallback_author]
        raise ValueError("Could not identify the author line")

    title = titlecase(" ".join(article_lines[:author_index]))
    author_line = re.sub(r"\d+", "", article_lines[author_index])
    authors = [author.strip() for author in author_line.split(",") if author.strip()]
    if not title or not authors:
        raise ValueError("Title or authors were empty")
    return title, authors


def parse_legacy_article(
    lines: list[str],
    abstract_start: int,
    fallback_author: str | None = None,
) -> tuple[str, list[str]]:
    """Parse older GJMS pages that omit the Research Article marker."""
    separators = [
        index for index, line in enumerate(lines[:abstract_start])
        if SEPARATOR.match(line)
    ]
    if separators:
        content_start = separators[0] + 1
    else:
        content_start = next(
            (
                index
                for index, line in enumerate(lines[:abstract_start])
                if not any(
                    marker in line.casefold()
                    for marker in ("global journal", "vol ", "issn", "www.")
                )
            ),
            0,
        )
    content = lines[content_start:abstract_start]
    expanded_content: list[str] = []
    for line in content:
        match = re.search(r"\b(?:Prof|Dr)\.?\s*(?:Dr\.?)?\s*[A-Z]", line)
        if not match:
            match = re.search(
                r"\s+(?=[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)*\s+\d+)"
                r"(?=[A-Z][a-z])",
                line,
            )
        if match and match.start() > 0:
            expanded_content.extend((line[:match.start()].strip(), line[match.start():].strip()))
        else:
            expanded_content.append(line)
    content = [line for line in expanded_content if line]

    title_lines: list[str] = []
    author_lines: list[str] = []
    title_done = False
    for line in content:
        lower = line.casefold()
        looks_like_affiliation = any(word in lower for word in AFFILIATION_WORDS)
        looks_like_affiliation |= lower.startswith(("dean", "assistant professor"))
        looks_like_affiliation |= "faculty of" in lower
        starts_like_author = lower.startswith(("prof", "dr."))
        looks_like_author = (
            not looks_like_affiliation
            and len(line.split()) >= 2
            and re.search(r"[^\W\d_]", line, re.UNICODE)
            and (not line.isupper() or starts_like_author)
        )
        if not title_done and starts_like_author:
            title_done = True
        if not title_done and (line.isupper() or not title_lines):
            title_lines.append(line)
            continue
        if not title_done:
            title_done = True
        if looks_like_author:
            author_lines.append(re.sub(r"[,.]+$", "", line))

    title = titlecase(" ".join(title_lines).strip())
    authors = author_lines[:]
    if not authors and fallback_author:
        authors = [fallback_author]
    if not title or not authors:
        raise ValueError("Could not identify legacy title or authors")
    return title, authors


def extract_articles(folder: Path) -> list[dict[str, object]]:
    """Extract article metadata from every PDF in a folder."""
    pdf_paths = sorted(folder.rglob("*.pdf"), key=lambda path: path.as_posix().casefold())
    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files found in {folder}")

    records = []
    def parse_one(pdf_path: Path) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="opendataloader-") as temporary_dir:
            title, authors = parse_article(
                extract_first_page_text(pdf_path, Path(temporary_dir)),
                fallback_author=pdf_path.stem.split(" paper")[0].strip(),
            )
        return {"file": pdf_path.name, "title": title, "authors": authors}

    workers = min(16, len(pdf_paths))
    console.print(
        f"[cyan]Parsing {len(pdf_paths)} PDFs concurrently "
        f"with {workers} workers...[/cyan]"
    )
    failures: list[tuple[Path, Exception]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parse_one, path): path for path in pdf_paths}
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("[cyan]{task.fields[current]:<44}[/cyan]"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Parsing PDFs", total=len(futures), current="")
            for future in as_completed(futures):
                pdf_path = futures[future]
                try:
                    records.append(future.result())
                except Exception as error:
                    failures.append((pdf_path, error))
                progress.update(task, current=pdf_path.name[:44])
                progress.advance(task)

    for pdf_path, error in failures:
        console.print(f"[red]Failed: {pdf_path}: {error}[/red]")
    if failures:
        raise RuntimeError(f"{len(failures)} PDF(s) could not be parsed")

    records.sort(key=lambda record: str(record["file"]).casefold())
    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract article titles and authors from PDF first pages."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=Path("test_files"),
        help="Folder containing PDFs (default: ./test_files)",
    )
    args = parser.parse_args()
    print(json.dumps(extract_articles(args.folder), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
