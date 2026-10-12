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

from extract_articles import extract_first_page_text, parse_article
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
    with tempfile.TemporaryDirectory(prefix="opendataloader-") as temporary_dir:
        text = extract_first_page_text(path, Path(temporary_dir))
    title, authors = parse_article(text, fallback_author=path.stem)
    year, volume, issue = article_destination(text)
    match = PAGE_PATTERN.search(re.sub(r"\s+", " ", text))
    if not match:
        raise ValueError(f"Could not determine page range for {path}")
    pages = re.sub(r"\s+", "", match.group(1)).replace("–", "-")
    return Article(path, year, volume, issue, title, ", ".join(authors), pages)


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
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Processing new PDFs", total=len(futures))
            for future in as_completed(futures):
                path = futures[future]
                try:
                    records.append(future.result())
                except Exception as error:
                    failures.append((path, error))
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
        for article in records:
            ftp.cwd(root)
            ensure_directory(ftp, article.year)
            ensure_directory(ftp, f"Volume {article.volume}")
            ensure_directory(ftp, f"Issue {article.issue}, {article.year}")
            with article.path.open("rb") as file_handle:
                ftp.storbinary(f"STOR {article.path.name}", file_handle)
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


def publish(frontend: Path, message: str) -> None:
    subprocess.run(["git", "add", "archive-data.json"], cwd=frontend, check=True)
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
    if changed:
        publish(args.frontend, "Add new journal articles")
    else:
        console.print("[yellow]Archive already up to date.[/yellow]")


if __name__ == "__main__":
    main()
