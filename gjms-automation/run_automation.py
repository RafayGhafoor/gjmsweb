"""Upload new PDFs, update the frontend archive, and publish the changes.

Always previews the extracted metadata before changing anything, and requires
confirmation, because the original bug was a bad title being published with
nobody looking at the extracted metadata.

    python run_automation.py             # preview, confirm, then publish
    python run_automation.py --dry-run   # preview only, nothing changes
    python run_automation.py --yes       # skip the prompt (unattended runs)
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote, unquote

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from titlecase import titlecase

from extract_articles import extract_first_page_text
from metadata import extract_metadata
from build_pages import run_build
import overrides as override_store
from upload_articles import (
    article_destination,
    ensure_directory,
    load_env,
    remote_size,
)
from ftplib import FTP, error_perm
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
    confidence: str = "high"
    rtl: bool = False
    notes: tuple[str, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        """Reasons a human should check this entry before it is published.

        Built from canonical conditions so the same problem is not reported
        twice ("no authors" and "no authors detected"); only the genuinely
        informative free-form note is carried through.
        """
        flags: list[str] = []
        if self.confidence != "high":
            flags.append(f"confidence={self.confidence}")
        if self.rtl:
            flags.append("RTL page: no Latin title")
        if not self.authors:
            flags.append("no authors")
        if not self.abstract:
            flags.append("no abstract")
        if len(self.title) > 220:
            flags.append(f"title {len(self.title)} chars")
        for note in self.notes:
            if note.startswith("suspicious"):
                flags.append(note)
        return tuple(flags)

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
        path,
        year,
        volume,
        issue,
        title,
        meta.authors_text,
        pages,
        meta.abstract,
        confidence=meta.confidence,
        rtl=meta.rtl,
        notes=tuple(meta.notes),
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

    records = apply_overrides(records)
    return sorted(records, key=lambda article: article.path.name.casefold())


def apply_overrides(records: list[Article]) -> list[Article]:
    """Let hand-corrected metadata win over extraction.

    Applied after extraction so a title fixed by hand is not overwritten on the
    next run (see `overrides.py`). An override is treated as authoritative, so
    the result is marked high confidence and not RTL.
    """
    data = override_store.load()
    if not data:
        return records
    out: list[Article] = []
    for article in records:
        entry = override_store.for_file(data, article.path.name)
        if not entry:
            out.append(article)
            continue
        fields = override_store.apply_to(
            {
                "title": article.title,
                "authors": article.authors,
                "abstract": article.abstract,
            },
            data,
            article.path.name,
        )
        console.print(
            f"[magenta]Override applied:[/magenta] {article.path.name} "
            f"({', '.join(sorted(entry))})"
        )
        out.append(
            replace(
                article,
                title=fields["title"],
                authors=fields["authors"],
                abstract=fields["abstract"],
                confidence="high",
                rtl=False,
            )
        )
    return out


def upload(records: list[Article], env_file: Path) -> None:
    """Upload each PDF, skipping files already present on Bunny at the same size.

    Re-running is therefore cheap and safe: an unchanged PDF is not re-sent,
    while a *changed* PDF (different size) still uploads. Metadata improvements
    are applied separately by `update_archive`, so re-running after the
    extractor improves refreshes titles without re-uploading anything.
    """
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
        uploaded: list[Article] = []
        skipped: list[Article] = []
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

                local_size = article.path.stat().st_size
                if remote_size(ftp, article.path.name) == local_size:
                    skipped.append(article)
                    progress.update(
                        task,
                        current=f"skip {article.path.name}"[:40],
                        bytes=f"{uploaded_bytes / 1024 / 1024:.1f} MB",
                    )
                    progress.advance(task)
                    continue

                with article.path.open("rb") as file_handle:
                    ftp.storbinary(f"STOR {article.path.name}", file_handle)
                uploaded_bytes += local_size
                uploaded.append(article)
                progress.update(
                    task,
                    current=article.path.name[:40],
                    bytes=f"{uploaded_bytes / 1024 / 1024:.1f} MB",
                )
                progress.advance(task)

        # Printed after the bar so the summary does not interleave with it.
        for article in uploaded:
            console.print(f"[green]Uploaded:[/green] {article.remote_path}")
        for article in skipped:
            console.print(
                f"[yellow]Skipped (already on Bunny):[/yellow] {article.remote_path}"
            )
        if skipped:
            console.print(
                f"[cyan]{len(skipped)} of {len(records)} already present; "
                f"{len(uploaded)} uploaded.[/cyan]"
            )


def find_section(data: list[dict], article: Article) -> dict | None:
    section_title = section_title_for(article)
    return next(
        (
            item
            for item in data
            if item.get("volume") == f"Vol {article.volume}"
            and item.get("title") == section_title
        ),
        None,
    )


def section_title_for(article: Article) -> str:
    return (
        f"Vol {article.volume} - No. {article.issue} "
        f"({MONTHS.get(article.issue, f'Issue {article.issue}')}, {article.year})"
    )


def find_article(section: dict, article: Article) -> dict | None:
    """The existing entry for this PDF, matched on its filename."""
    return next(
        (
            item
            for item in section.get("articles", [])
            if unquote(str(item.get("pdf", "")).rsplit("/", 1)[-1])
            == article.path.name
        ),
        None,
    )


def planned_updates(article: Article, existing: dict | None) -> dict[str, str]:
    """Fields this article would change, as {field: new value}.

    A low-confidence or right-to-left extraction never replaces a title that is
    already there - AGENTS.md requires keeping the curated title in that case -
    but it may still fill in authors or the abstract.
    """
    if existing is None:
        return {
            "title": article.title,
            "authors": article.authors,
            "pages": article.pages,
        }

    updates: dict[str, str] = {}
    may_replace_title = article.confidence == "high" and not article.rtl
    if article.title and (may_replace_title or not existing.get("title")):
        if existing.get("title") != article.title:
            updates["title"] = article.title
    if article.authors and existing.get("authors") != article.authors:
        updates["authors"] = article.authors
    if article.pages and existing.get("pages") != article.pages:
        updates["pages"] = article.pages
    return updates


def update_archive(records: list[Article], archive_path: Path) -> dict[str, list]:
    """Insert new articles and refresh existing ones in place.

    Existing entries used to be skipped outright, which meant re-running after
    the extractor improved could never fix a title. Now the fields are compared
    and updated when they differ, and the result reports what happened.
    """
    data = json.loads(archive_path.read_text(encoding="utf-8"))
    report: dict[str, list] = {"added": [], "updated": [], "unchanged": []}

    for article in records:
        section = find_section(data, article)
        if section is None:
            section = {
                "volume": f"Vol {article.volume}",
                "title": section_title_for(article),
                "articles": [],
            }
            data.append(section)

        existing = find_article(section, article)
        if existing is None:
            section["articles"].append(
                {
                    "num": len(section["articles"]) + 1,
                    "title": article.title,
                    "authors": article.authors,
                    "pages": article.pages,
                    "pdf": article.public_url,
                }
            )
            report["added"].append(article.path.name)
            console.print(f"[green]Archive added:[/green] {article.path.name}")
            continue

        updates = planned_updates(article, existing)
        if not updates:
            report["unchanged"].append(article.path.name)
            console.print(f"[dim]Archive unchanged:[/dim] {article.path.name}")
            continue
        for field, value in updates.items():
            console.print(
                f"[cyan]{field}[/cyan] {article.path.name}:\n"
                f"    - {existing.get(field)}\n"
                f"    + {value}"
            )
        existing.update(updates)
        report["updated"].append((article.path.name, sorted(updates)))
        console.print(f"[green]Archive updated:[/green] {article.path.name}")

    if report["added"] or report["updated"]:
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


def plan_records(records: list[Article], archive_path: Path) -> dict[str, dict]:
    """Preview how each record will affect the archive, without writing."""
    if not archive_path.exists():
        return {a.path.name: {"action": "new", "fields": ()} for a in records}
    data = json.loads(archive_path.read_text(encoding="utf-8"))
    plan: dict[str, dict] = {}
    for article in records:
        section = find_section(data, article)
        existing = find_article(section, article) if section else None
        if existing is None:
            plan[article.path.name] = {"action": "new", "fields": ()}
            continue
        updates = planned_updates(article, existing)
        plan[article.path.name] = {
            "action": "update" if updates else "unchanged",
            "fields": tuple(sorted(updates)),
        }
    return plan


def preview(records: list[Article], plan: dict[str, dict] | None = None) -> int:
    """Print exactly what would be published; return the warning count.

    Printed as full-width blocks rather than a table: the point of the gate is
    that a human can see the whole title, and a table truncates or wraps it.
    The original failure was a title that had swallowed the author line,
    affiliations, an email and two ORCIDs (461 characters).
    """
    console.print("\n[bold]═══ METADATA PREVIEW - check before publishing ═══[/bold]")

    flagged = 0
    for index, article in enumerate(records, 1):
        warnings = article.warnings
        if warnings:
            flagged += 1
        flag_text = (
            "[yellow]" + ", ".join(warnings) + "[/yellow]"
            if warnings
            else "[green]ok[/green]"
        )
        entry = (plan or {}).get(article.path.name, {})
        action = entry.get("action", "new")
        fields = entry.get("fields", ())
        if action == "new":
            action_text = "[green]add to archive[/green]"
        elif action == "update":
            action_text = f"[cyan]update archive: {', '.join(fields)}[/cyan]"
        else:
            action_text = "[dim]archive already current[/dim]"
        console.print(
            f"\n[bold cyan]{index}. {article.path.name}[/bold cyan]\n"
            f"   issue   : {article.year}/Volume {article.volume}/"
            f"Issue {article.issue}, {article.year} | pages {article.pages}\n"
            f"   title   : {article.title or '[red]<empty>[/red]'}\n"
            f"   authors : {article.authors or '[red]<none>[/red]'}\n"
            f"   abstract: {len(article.abstract)} chars\n"
            f"   action  : {action_text}\n"
            f"   flags   : {flag_text}"
        )

    new_count = sum(
        1 for entry in (plan or {}).values() if entry.get("action") == "new"
    )
    update_count = sum(
        1 for entry in (plan or {}).values() if entry.get("action") == "update"
    )
    console.print(
        f"\n[cyan]{len(records)} article(s): {new_count} new to the archive, "
        f"{update_count} to update.[/cyan]"
    )
    if flagged:
        console.print(
            f"[bold yellow]{flagged} of {len(records)} entr(y/ies) have warnings "
            f"- review the title and authors above.[/bold yellow]"
        )
    else:
        console.print("[bold green]No warnings.[/bold green]")
    return flagged


def confirm(warning_count: int, assume_yes: bool) -> bool:
    """Ask the operator to approve publishing. Safe by default."""
    if assume_yes:
        console.print("[yellow]--yes supplied: skipping confirmation.[/yellow]")
        return True
    if not sys.stdin.isatty():
        console.print(
            "[red]Not a terminal, so nothing was published.[/red] "
            "Re-run with [bold]--yes[/bold] to publish unattended."
        )
        return False

    if warning_count:
        console.print(
            f"\n[bold red]{warning_count} entr(y/ies) have warnings.[/bold red] "
            "Type [bold]publish[/bold] to upload and push anyway: "
        )
        expected = "publish"
    else:
        console.print(
            "\nType [bold]y[/bold] to upload to Bunny, update the archive and "
            "push to GitHub: "
        )
        expected = "y"
    try:
        answer = input().strip().casefold()
    except EOFError:
        return False
    if warning_count:
        return answer == expected
    return answer in ("y", "yes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path, default=Path("test_files"))
    parser.add_argument(
        "--frontend",
        type=Path,
        default=Path("/Users/emumba/Downloads/projects/gjms-frontend"),
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the extracted metadata and stop; change nothing",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (for unattended runs)",
    )
    args = parser.parse_args()

    archive_path = args.frontend / "archive-data.json"
    records = collect_articles(args.folder)
    plan = plan_records(records, archive_path)
    warning_count = preview(records, plan)

    if args.dry_run:
        console.print(
            "[yellow]Dry run: nothing uploaded, no file changed.[/yellow]"
        )
        return
    if not confirm(warning_count, args.yes):
        console.print(
            "[yellow]Aborted: nothing was uploaded, committed or pushed.[/yellow]"
        )
        return

    upload(records, args.env_file)
    report = update_archive(records, archive_path)
    abstracts_added = update_abstracts(
        records, args.frontend / "abstracts.json"
    )
    if report["added"] or report["updated"] or abstracts_added:
        # New or improved metadata needs its page and the sitemap regenerated.
        run_build(
            archive=archive_path,
            abstracts_path=args.frontend / "abstracts.json",
            out=args.frontend / "article",
        )
        parts = []
        if report["added"]:
            parts.append(f"add {len(report['added'])}")
        if report["updated"]:
            parts.append(f"update {len(report['updated'])}")
        message = f"Journal archive: {', '.join(parts) or 'refresh'}"
        publish(args.frontend, message)
    else:
        console.print("[yellow]Archive already up to date.[/yellow]")


if __name__ == "__main__":
    main()
