"""Verify archive PDF links without downloading complete files."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn


console = Console()

# BunnyCDN answers a burst of HEAD requests with 428/429 rather than 503, so a
# parallel run reports every link as broken unless these are retried. They mean
# "ask again in a moment", not "this PDF is gone".
TRANSIENT = {408, 425, 428, 429, 500, 502, 503, 504}
ATTEMPTS = 4


@dataclass(frozen=True)
class Link:
    volume: str
    article_number: int | str
    title: str
    url: str


@dataclass(frozen=True)
class Result:
    link: Link
    status: int | None
    method: str
    detail: str
    throttled: bool = False

    @property
    def working(self) -> bool:
        return self.status is not None and 200 <= self.status < 400

    @property
    def broken(self) -> bool:
        """A real failure: not working, and not merely throttled by the CDN."""
        return not self.working and not self.throttled


def load_links(archive_path: Path) -> list[Link]:
    data = json.loads(archive_path.read_text(encoding="utf-8"))
    links = []
    for issue in data:
        for article in issue.get("articles", []):
            url = article.get("pdf")
            if url:
                links.append(
                    Link(
                        str(issue.get("volume", "")),
                        article.get("num", ""),
                        str(article.get("title", "")),
                        str(url),
                    )
                )
    return links


def request_url(url: str) -> str:
    """Encode URL path characters while preserving existing percent escapes."""
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, quote(parts.path, safe="/%"), parts.query, parts.fragment)
    )


def _attempt(link: Link, url: str, timeout: float, delay: float) -> Result | None:
    """One HEAD (then a ranged GET fallback). None means "retry me"."""
    if delay:
        time.sleep(delay)
    try:
        request = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": "GJMS-link-checker/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Result(link, response.status, "HEAD", response.geturl())
    except urllib.error.HTTPError as error:
        if error.code in TRANSIENT:
            return None
        if error.code not in (400, 405, 501):
            return Result(link, error.code, "HEAD", str(error))
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Range": "bytes=0-0",
                "User-Agent": "GJMS-link-checker/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Result(link, response.status, "GET range", response.geturl())
    except urllib.error.HTTPError as error:
        if error.code in TRANSIENT:
            return None
        return Result(link, error.code, "GET range", str(error))
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def check_link(link: Link, timeout: float, delay: float) -> Result:
    url = request_url(link.url)
    for attempt in range(ATTEMPTS):
        outcome = _attempt(link, url, timeout, delay)
        if outcome is not None:
            return outcome
        time.sleep(0.5 * (2**attempt))  # 0.5s, 1s, 2s, 4s
    return Result(link, 428, "HEAD", "throttled by the CDN after retries", throttled=True)


def verify(
    links: list[Link], workers: int, timeout: float, delay: float
) -> list[Result]:
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(check_link, link, timeout, delay): link for link in links
        }
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("[cyan]{task.fields[current]:<44}[/cyan]"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Checking archive links", total=len(futures), current=""
            )
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                progress.update(
                    task,
                    current=unquote(urlsplit(result.link.url).path).rsplit("/", 1)[-1][:44],
                )
                progress.advance(task)
    return sorted(results, key=lambda result: result.link.url.casefold())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("/Users/emumba/Downloads/projects/gjms-frontend/archive-data.json"),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Concurrent checks. BunnyCDN answers large bursts with 428, so keep this modest",
    )
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument(
        "--delay",
        type=float,
        default=0,
        help="Seconds to wait before each request",
    )
    args = parser.parse_args()

    links = load_links(args.archive)
    if not links:
        raise ValueError(f"No PDF links found in {args.archive}")
    console.print(f"[cyan]Found {len(links)} PDF links.[/cyan]")
    results = verify(links, max(1, args.workers), args.timeout, max(0, args.delay))
    broken = [result for result in results if result.broken]
    throttled = [result for result in results if result.throttled]

    console.print(
        f"[bold green]Working: {len(results) - len(broken) - len(throttled)}[/bold green] | "
        f"[bold red]Broken: {len(broken)}[/bold red] | "
        f"[yellow]Unverified (throttled): {len(throttled)}[/yellow]"
    )
    for result in broken:
        console.print(
            f"[red]{result.status or 'ERROR'}[/red] "
            f"{result.link.volume} #{result.link.article_number} "
            f"{result.link.title}: {result.link.url} ({result.detail})"
        )
    if throttled:
        console.print(
            "[yellow]These were rate-limited by the CDN, not broken. "
            "Re-run with a lower --workers value and a --delay.[/yellow]"
        )
    # Only a genuinely broken link fails the run; throttling must not cry wolf.
    if broken:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
