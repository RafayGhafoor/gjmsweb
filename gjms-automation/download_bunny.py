"""Mirror all Bunny FTP content into a local directory with live progress."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import posixpath
import subprocess
import tempfile
from urllib.parse import quote
from dataclasses import dataclass
from ftplib import FTP
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


console = Console()


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int


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


def parse_listing(rows: list[str]) -> list[tuple[str, str, int]]:
    """Parse Bunny's Unix-style LIST rows."""
    entries = []
    for row in rows:
        parts = row.split(maxsplit=8)
        if len(parts) < 9 or parts[0][0] not in "d-":
            continue
        kind, name = parts[0][0], parts[8]
        size = int(parts[4]) if parts[4].isdigit() else 0
        entries.append((kind, name, size))
    return entries


def list_subtree(year: str, settings: dict[str, str | bool]) -> list[RemoteFile]:
    """Recursively list one year subtree using its own FTP connection."""
    files: list[RemoteFile] = []
    with FTP(str(settings["host"]), timeout=30) as ftp:
        ftp.login(str(settings["username"]), str(settings["password"]))
        ftp.set_pasv(bool(settings["passive"]))
        ftp.cwd(year)

        def walk(relative_directory: str = "") -> None:
            rows: list[str] = []
            ftp.retrlines("LIST", rows.append)
            for kind, name, size in parse_listing(rows):
                relative_path = posixpath.join(year, relative_directory, name)
                if kind == "d":
                    ftp.cwd(name)
                    walk(posixpath.join(relative_directory, name))
                    ftp.cwd("..")
                else:
                    files.append(RemoteFile(relative_path, size))

        walk()
    console.print(f"[green]Finished scanning {year}: {len(files)} files[/green]")
    return files


def list_files(ftp: FTP, settings: dict[str, str | bool]) -> list[RemoteFile]:
    """Recursively list all remote files in parallel by year."""
    rows: list[str] = []
    ftp.retrlines("LIST", rows.append)
    years = [name for kind, name, _ in parse_listing(rows) if kind == "d"]
    console.print(f"[cyan]Scanning {len(years)} year directories in parallel...[/cyan]")

    files: list[RemoteFile] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(years)))) as executor:
        futures = {
            executor.submit(list_subtree, year, settings): year for year in years
        }
        for future in as_completed(futures):
            files.extend(future.result())
    return sorted(files, key=lambda item: item.path.casefold())


def download_all(destination: Path, env_file: Path) -> None:
    """Download every remote file with aria2, preserving its directory."""
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

    destination.mkdir(parents=True, exist_ok=True)
    settings = {
        "host": os.environ["BUNNY_FTP_HOST"],
        "port": os.environ["BUNNY_FTP_PORT"],
        "username": os.environ["BUNNY_FTP_USERNAME"],
        "password": os.environ["BUNNY_FTP_PASSWORD"],
        "passive": os.environ.get("BUNNY_FTP_PASSIVE", "true").casefold() == "true",
    }
    with FTP(os.environ["BUNNY_FTP_HOST"], timeout=60) as ftp:
        console.print("[bold cyan]Connecting to Bunny FTP...[/bold cyan]")
        ftp.login(os.environ["BUNNY_FTP_USERNAME"], os.environ["BUNNY_FTP_PASSWORD"])
        ftp.set_pasv(os.environ.get("BUNNY_FTP_PASSIVE", "true").casefold() == "true")
        console.print("[green]Connected and authenticated.[/green]")
        files = list_files(ftp, settings)
        total_bytes = sum(item.size for item in files)
        console.print(
            f"[bold green]Found {len(files)} remote files "
            f"({total_bytes:,} bytes).[/bold green]"
        )
    pending_files = [
        remote_file
        for remote_file in files
        if not (
            (destination / Path(remote_file.path)).is_file()
            and (destination / Path(remote_file.path)).stat().st_size
            == remote_file.size
        )
    ]
    skipped_files = len(files) - len(pending_files)
    skipped_bytes = total_bytes - sum(item.size for item in pending_files)
    console.print(
        f"[yellow]Skipping {skipped_files} already complete files "
        f"({skipped_bytes:,} bytes).[/yellow]"
    )
    if not pending_files:
        console.print("[bold green]Mirror is already up to date.[/bold green]")
        return

    with tempfile.TemporaryDirectory(prefix="bunny-download-") as temporary_dir:
        temporary_path = Path(temporary_dir)
        manifest = temporary_path / "files.txt"
        config = temporary_path / "aria2.conf"
        host = os.environ["BUNNY_FTP_HOST"]
        port = os.environ["BUNNY_FTP_PORT"]

        with manifest.open("w", encoding="utf-8") as output:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(
                    f"Creating aria2 manifest ({len(pending_files)} files)",
                    total=len(pending_files),
                )
                for remote_file in pending_files:
                    local_path = destination / Path(remote_file.path)
                    sidecar = local_path.with_name(local_path.name + ".aria2")
                    if sidecar.exists():
                        sidecar.unlink()
                    url_path = quote(remote_file.path, safe="/")
                    output.write(f"ftp://{host}:{port}/{url_path}\n")
                    output.write(f"  out={remote_file.path}\n")
                    progress.advance(task)

        config.write_text(
            "\n".join(
                [
                    f"ftp-user={os.environ['BUNNY_FTP_USERNAME']}",
                    f"ftp-passwd={os.environ['BUNNY_FTP_PASSWORD']}",
                    "ftp-pasv=true",
                    "continue=false",
                    "auto-file-renaming=false",
                    "allow-overwrite=true",
                    "file-allocation=none",
                    "max-concurrent-downloads=64",
                    "max-connection-per-server=16",
                    "split=1",
                    "summary-interval=1",
                    "console-log-level=notice",
                    "show-console-readout=true",
                    "download-result=full",
                ]
            ),
            encoding="utf-8",
        )

        console.print(
            "[bold cyan]Starting aria2 parallel download "
            "(64 concurrent files)...[/bold cyan]"
        )
        subprocess.run(
            [
                "aria2c",
                "--conf-path",
                str(config),
                "--dir",
                str(destination),
                "--input-file",
                str(manifest),
            ],
            check=True,
            stdout=None,
            stderr=None,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("bunny-content"),
        help="Local mirror directory (default: ./bunny-content)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="FTP environment file (default: ./.env)",
    )
    args = parser.parse_args()
    download_all(args.destination, args.env_file)


if __name__ == "__main__":
    main()
