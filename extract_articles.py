"""Extract article titles and authors from the first page of PDF files."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from opendataloader_pdf import convert


AUTHOR_LINE = re.compile(
    r"^[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)+\d+(?:\s*,|$)"
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
    return text_path.read_text(encoding="utf-8")


def parse_article(text: str) -> tuple[str, list[str]]:
    """Extract the title and author names from first-page text."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    try:
        article_start = next(
            index for index, line in enumerate(lines)
            if line.casefold() == "research article"
        )
    except StopIteration as error:
        raise ValueError("Could not find the 'Research Article' marker") from error

    try:
        abstract_start = next(
            index for index in range(article_start + 1, len(lines))
            if lines[index].casefold() == "abstract"
        )
    except StopIteration as error:
        raise ValueError("Could not find the 'Abstract' marker") from error

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
        raise ValueError("Could not identify the author line")

    title = " ".join(article_lines[:author_index])
    author_line = re.sub(r"\d+", "", article_lines[author_index])
    authors = [author.strip() for author in author_line.split(",") if author.strip()]

    if not title or not authors:
        raise ValueError("Title or authors were empty")
    return title, authors


def extract_articles(folder: Path) -> list[dict[str, object]]:
    """Extract article metadata from every PDF in a folder."""
    records = []
    pdf_paths = sorted(folder.glob("*.pdf"), key=lambda path: path.name.casefold())
    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files found in {folder}")

    with tempfile.TemporaryDirectory(prefix="opendataloader-") as temporary_dir:
        output_dir = Path(temporary_dir)
        for pdf_path in pdf_paths:
            text = extract_first_page_text(pdf_path, output_dir)
            title, authors = parse_article(text)
            records.append(
                {"file": pdf_path.name, "title": title, "authors": authors}
            )
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
