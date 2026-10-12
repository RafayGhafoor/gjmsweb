"""Extract article metadata and upload PDFs to Bunny Storage over FTP."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from ftplib import FTP, error_perm
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from extract_articles import extract_first_page_text, parse_article


console = Console()


VOLUME_ISSUE_YEAR_PATTERN = re.compile(
    r"\bVol\.?\s+(\d+)\s*\(\s*(\d+)\s*\).*?\b(20\d{2})\b"
)


def load_env(path: Path) -> None:
    """Load simple KEY=VALUE entries without requiring an extra dotenv package."""
    if not path.exists():
        raise FileNotFoundError(f"Environment file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def article_destination(text: str) -> tuple[str, str, str]:
    """Return the Bunny year, volume, and issue directories from first-page text."""
    match = VOLUME_ISSUE_YEAR_PATTERN.search(text)
    if not match:
        raise ValueError("Could not determine year, volume, and issue from the first page")
    volume, issue, year = match.groups()
    return year, volume, issue


def ensure_directory(ftp: FTP, name: str) -> None:
    """Enter a remote directory, creating it when necessary."""
    try:
        ftp.cwd(name)
    except error_perm:
        ftp.mkd(name)
        ftp.cwd(name)


def upload_articles(folder: Path, env_file: Path, dry_run: bool = False) -> None:
    """Upload each PDF to Bunny under <year>/<issue>/<filename>."""
    load_env(env_file)
    required = (
        "BUNNY_FTP_HOST",
        "BUNNY_FTP_PORT",
        "BUNNY_FTP_USERNAME",
        "BUNNY_FTP_PASSWORD",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing FTP configuration: {', '.join(missing)}")

    pdf_paths = sorted(folder.glob("*.pdf"), key=lambda path: path.name.casefold())
    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files found in {folder}")

    destinations = []
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TextColumn("[cyan]{task.fields[current]:<44}[/cyan]"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            "Reading destinations", total=len(pdf_paths), current=""
        )
        for pdf_path in pdf_paths:
            with tempfile.TemporaryDirectory(prefix="opendataloader-") as temporary_dir:
                text = extract_first_page_text(pdf_path, Path(temporary_dir))
            year, volume, issue = article_destination(text)
            destinations.append((pdf_path, year, volume, issue))
            progress.update(task, current=pdf_path.name[:44])
            progress.advance(task)

    if dry_run:
        for pdf_path, year, volume, issue in destinations:
            print(
                f"DRY RUN: {pdf_path} -> "
                f"{year}/Volume {volume}/Issue {issue}, {year}/{pdf_path.name}"
            )
        return

    with FTP() as ftp:
        ftp.connect(
            os.environ["BUNNY_FTP_HOST"],
            int(os.environ["BUNNY_FTP_PORT"]),
            timeout=30,
        )
        ftp.login(os.environ["BUNNY_FTP_USERNAME"], os.environ["BUNNY_FTP_PASSWORD"])
        ftp.set_pasv(os.environ.get("BUNNY_FTP_PASSIVE", "true").casefold() == "true")
        storage_root = ftp.pwd()

        uploaded_bytes = 0
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("[cyan]{task.fields[current]:<40}[/cyan]"),
            TextColumn("{task.fields[bytes]:>10}"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Uploading", total=len(destinations), current="", bytes="0 B"
            )
            for pdf_path, year, volume, issue in destinations:
                ftp.cwd(storage_root)
                ensure_directory(ftp, year)
                ensure_directory(ftp, f"Volume {volume}")
                ensure_directory(ftp, f"Issue {issue}, {year}")
                with pdf_path.open("rb") as file_handle:
                    ftp.storbinary(f"STOR {pdf_path.name}", file_handle)
                uploaded_bytes += pdf_path.stat().st_size
                progress.update(
                    task,
                    current=pdf_path.name[:40],
                    bytes=f"{uploaded_bytes / 1024 / 1024:.1f} MB",
                )
                progress.advance(task)

        for pdf_path, year, volume, issue in destinations:
            console.print(
                f"[green]Uploaded:[/green] {year}/Volume {volume}/"
                f"Issue {issue}, {year}/{pdf_path.name}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=Path("test_files"),
        help="Folder containing PDFs (default: ./test_files)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="FTP environment file (default: ./.env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract destinations without connecting or uploading",
    )
    args = parser.parse_args()
    upload_articles(args.folder, args.env_file, args.dry_run)


if __name__ == "__main__":
    main()
