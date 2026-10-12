"""Upload new PDFs, update the frontend archive, and publish the changes."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from titlecase import titlecase

from extract_articles import extract_first_page_text
from metadata import extract_metadata
from build_pages import run_build
from upload_articles import (
    article_destination,
    ensure_directory,
    load_env,
)
from ftplib import FTP
import os


console = Console()

# Deploy key used to push the frontend repository. GitHub rejects the default
# identity chain (no active `Host github.com` block in ~/.ssh/config), so the
# key is pinned explicitly instead of relying on the ssh-agent being primed.
SSH_KEY = Path(
    os.environ.get("GJMS_SSH_KEY", "~/.ssh/id_rsa_rafayghafoor")
).expanduser()

MONTHS = {
    "1": "Jan - March",
    "2": "April - June",
    "3": "July - Sept",
    "4": "Oct - Dec",
}
PAGE_PATTERN = re.compile(
    r"(?:pp\.?|pages?|page\s*#?)\s*([0-9]{1,4}\s*[-–]\s*[0-9]{1,4})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Article:
    path: Path
    year: str
    volume: str
    issue: str
    title: str
    authors: str
    pages: str
    abstract: str = ""

    @property
    def remote_path(self) -> str:
        return (
            f"{self.year}/Volume {self.volume}/"
            f"Issue {self.issue}, {self.year}/{self.path.name}"
        )

    @property
    def public_url(self) -> str:
        return f"http://gjms.b-cdn.net/{quote(self.remote_path, safe='/,')}"


def parse_one(path: Path) -> Article:
    # Title/authors/abstract come from the structure-aware extractor; the raw
    # first-page text is still needed for the issue metadata and page range.
    meta = extract_metadata(path)
    with tempfile.TemporaryDirectory(prefix="opendataloader-") as temporary_dir:
        text = extract_first_page_text(path, Path(temporary_dir))
    year, volume, issue = article_destination(text)
    match = PAGE_PATTERN.search(re.sub(r"\s+", " ", text))
    if not match:
        raise ValueError(f"Could not determine page range for {path}")
    pages = re.sub(r"\s+", "", match.group(1)).replace("–", "-")
    title = meta.title.strip()
    if not title:
        # Never publish a silently wrong title (that was the original bug): fall
        # back loudly to the filename so a human notices.
        title = titlecase(path.stem.split(" paper")[0].strip())
        console.print(
            f"[yellow]{path.name}: no title extracted; "
            f"using filename {title!r}[/yellow]"
        )
    return Article(
        path, year, volume, issue, title, meta.authors_text, pages, meta.abstract
    )


def collect_articles(folder: Path) -> list[Article]:
    paths = sorted(folder.glob("*.pdf"), key=lambda path: path.name.casefold())
    if not paths:
        raise FileNotFoundError(f"No PDF files found in {folder}")
    records: list[Article] = []
    failures: list[tuple[Path, Exception]] = []
    with ThreadPoolExecutor(max_workers=min(16, len(paths))) as executor:
        futures = {executor.submit(parse_one, path): path for path in paths}
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("[cyan]{task.fields[current]:<44}[/cyan]"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Processing new PDFs", total=len(futures), current="")
            for future in as_completed(futures):
                path = futures[future]
                try:
                    records.append(future.result())
                except Exception as error:
                    failures.append((path, error))
                progress.update(task, current=path.name[:44])
                progress.advance(task)
    for path, error in failures:
        console.print(f"[red]Failed: {path}: {error}[/red]")
    if failures:
        raise RuntimeError(f"{len(failures)} PDF(s) could not be processed")
    return sorted(records, key=lambda article: article.path.name.casefold())


def upload(records: list[Article], env_file: Path) -> None:
    load_env(env_file)
    with FTP() as ftp:
        ftp.connect(
            os.environ["BUNNY_FTP_HOST"],
            int(os.environ["BUNNY_FTP_PORT"]),
            timeout=30,
        )
        ftp.login(os.environ["BUNNY_FTP_USERNAME"], os.environ["BUNNY_FTP_PASSWORD"])
        ftp.set_pasv(os.environ.get("BUNNY_FTP_PASSIVE", "true").casefold() == "true")
        root = ftp.pwd()
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
                "Uploading to Bunny", total=len(records), current="", bytes="0 B"
            )
            for article in records:
                ftp.cwd(root)
                ensure_directory(ftp, article.year)
                ensure_directory(ftp, f"Volume {article.volume}")
                ensure_directory(ftp, f"Issue {article.issue}, {article.year}")
                with article.path.open("rb") as file_handle:
                    ftp.storbinary(f"STOR {article.path.name}", file_handle)
                uploaded_bytes += article.path.stat().st_size
                progress.update(
                    task,
                    current=article.path.name[:40],
                    bytes=f"{uploaded_bytes / 1024 / 1024:.1f} MB",
                )
                progress.advance(task)

        # Printed after the bar so the summary does not interleave with it.
        for article in records:
            console.print(f"[green]Uploaded:[/green] {article.remote_path}")


def update_archive(records: list[Article], archive_path: Path) -> bool:
    data = json.loads(archive_path.read_text(encoding="utf-8"))
    changed = False
    for article in records:
        section_title = (
            f"Vol {article.volume} - No. {article.issue} "
            f"({MONTHS.get(article.issue, f'Issue {article.issue}')}, {article.year})"
        )
        section = next(
            (
                item
                for item in data
                if item.get("volume") == f"Vol {article.volume}"
                and item.get("title") == section_title
            ),
            None,
        )
        if section is None:
            section = {
                "volume": f"Vol {article.volume}",
                "title": section_title,
                "articles": [],
            }
            data.append(section)
            changed = True

        existing = {
            unquote(str(item.get("pdf", "")).rsplit("/", 1)[-1])
            for item in section["articles"]
        }
        if article.path.name in existing:
            console.print(f"[yellow]Already in archive:[/yellow] {article.path.name}")
            continue
        section["articles"].append(
            {
                "num": len(section["articles"]) + 1,
                "title": article.title,
                "authors": article.authors,
                "pages": article.pages,
                "pdf": article.public_url,
            }
        )
        changed = True
        console.print(f"[green]Archive added:[/green] {article.path.name}")

    if changed:
        archive_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    return changed


def git_env() -> dict[str, str]:
    """Environment that forces git to authenticate with GJMS_SSH_KEY."""
    env = os.environ.copy()
    if SSH_KEY.is_file():
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {shlex.quote(str(SSH_KEY))} -o IdentitiesOnly=yes"
        )
    else:
        console.print(
            f"[yellow]SSH key not found at {SSH_KEY}; "
            "falling back to the default identity chain.[/yellow]"
        )
    return env


def update_abstracts(records: list[Article], abstracts_path: Path) -> int:
    """Merge newly extracted abstracts into abstracts.json.

    Abstracts live in a separate file because the archive page lazy-loads them;
    the static article pages carry them inline for search engines.
    """
    existing: dict[str, str] = {}
    if abstracts_path.exists():
        existing = json.loads(abstracts_path.read_text(encoding="utf-8"))
    added = 0
    for article in records:
        if article.abstract and existing.get(article.public_url) != article.abstract:
            existing[article.public_url] = article.abstract
            added += 1
    if added:
        abstracts_path.write_text(
            json.dumps(existing, indent=1, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Abstracts added:[/green] {added}")
    return added


def publish(frontend: Path, message: str) -> None:
    subprocess.run(
        [
            "git",
            "add",
            "archive-data.json",
            "abstracts.json",
            "sitemap.xml",
            "robots.txt",
            "article",
        ],
        cwd=frontend,
        check=True,
    )
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=frontend,
        check=False,
    )
    if status.returncode == 0:
        console.print("[yellow]No frontend archive changes to commit.[/yellow]")
        return
    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            message,
            "-m",
            "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
        ],
        cwd=frontend,
        check=True,
    )
    try:
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=frontend,
            check=True,
            env=git_env(),
        )
    except subprocess.CalledProcessError as exc:
        console.print(
            f"[bold red]Push failed[/bold red] (exit {exc.returncode}). "
            f"The commit is local; retry with:\n"
            f"  cd {frontend} && git push origin main\n"
            f"Check that {SSH_KEY} is authorized on the GitHub remote."
        )
        raise
    console.print("[bold green]Frontend pushed to GitHub.[/bold green]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path, default=Path("test_files"))
    parser.add_argument(
        "--frontend",
        type=Path,
        default=Path("/Users/emumba/Downloads/projects/gjms-frontend"),
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    records = collect_articles(args.folder)
    for article in records:
        console.print(
            f"{article.path.name}: {article.year}/Volume {article.volume}/"
            f"Issue {article.issue}, {article.year} | {article.pages}"
        )
    if args.dry_run:
        return
    upload(records, args.env_file)
    changed = update_archive(
        records, args.frontend / "archive-data.json"
    )
    abstracts_added = update_abstracts(
        records, args.frontend / "abstracts.json"
    )
    if changed or abstracts_added:
        # New papers need a static page, and the sitemap must list it.
        run_build(
            archive=args.frontend / "archive-data.json",
            abstracts_path=args.frontend / "abstracts.json",
            out=args.frontend / "article",
        )
        publish(args.frontend, "Add new journal articles")
    else:
        console.print("[yellow]Archive already up to date.[/yellow]")


if __name__ == "__main__":
    main()
