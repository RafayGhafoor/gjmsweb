# GJMS automation

Tools that maintain the GJMS article archive: mirror Bunny, extract metadata,
preview and publish, and generate the frontend's static pages.

For orientation read `../guide.md` (human walkthrough) and `../agents.md`
(durable rules for agents).

## The normal workflow

```bash
python gjms-automation/cli.py                  # interactive menu (rich)
python gjms-automation/cli.py run              # preview -> confirm -> publish
python gjms-automation/cli.py run --dry-run    # preview only, nothing changes
python gjms-automation/cli.py run --yes        # skip the prompt (unattended)
```

With no command the CLI opens an interactive menu. With a non-terminal stdin it
prints help instead, so it never blocks a pipe or a script.

`run` prints every new article's title, authors and abstract length with warning
flags **before** changing anything, then requires approval:

* clean run -> type `y`
* any warnings -> must type `publish`
* not a terminal -> refuses unless `--yes` is passed

It then uploads to Bunny (skipping files already there at the same size),
updates `archive-data.json` and `abstracts.json` (updating existing entries in
place when their metadata changed), commits and pushes. The website's build
prerenders the pages from that data.

## Correcting metadata by hand

```bash
python gjms-automation/cli.py set "<pattern>" --title "..." --authors "..."
python gjms-automation/cli.py set "<pattern>" --abstract-file new.txt --push
python gjms-automation/cli.py clear "<pattern>"
python gjms-automation/cli.py show "<pattern>"
python gjms-automation/cli.py list "<pattern>"
```

`set` writes an override to `overrides.json`, applies it to the archive, and
publishes on request. It does not prerender pages - the website's SvelteKit build
generates those from the data on push. Overrides are applied **last** on every
future run, so extraction and the backfill can never silently undo a hand
correction. `slug` is not overridable - it is the published URL.

## Tools

| Script | Purpose |
| --- | --- |
| `cli.py` | Interactive menu plus `run`/`set`/`show`/`list`/`clear`/`mirror`/`links` |
| `run_automation.py` | End-to-end workflow with the preview/confirm gate |
| `metadata.py` | Title/author/abstract extraction (library; see `../agents.md`) |
| `overrides.py` | Loads/applies `overrides.json` (manual corrections) |
| `slugs.py` | Assigns the persisted `slug` the website builds article URLs from |
| `download_bunny.py` | Mirror the whole Bunny tree into `bunny-content/` |
| `upload_articles.py` | Upload PDFs to Bunny only, skipping those already present |
| `backfill_archive.py` | One-off repair pass over every archive entry |
| `build_pages.py` | **Legacy** page generator for the pre-SvelteKit site; no longer called, and its output was deleted from the frontend repository |
| `repair_authors.py` | Repair authors/titles in `archive-data.json` (author suffix in the title, affiliation/degree debris in `authors`) || `extract_articles.py` | Legacy metadata parser; still used for issue and page range |
| `verify_links.py` | Check every archive PDF link (HEAD, ranged GET fallback) |

## What this repo owns

Only two files are written for the website: `archive-data.json` (titles/authors)
and `abstracts.json` (abstracts, keyed by PDF URL). The frontend is a SvelteKit
build that prerenders article pages and its own sitemap from that data, so this
repo does not generate pages.

Standalone commands:

```bash
# Mirror all Bunny content locally.
python gjms-automation/download_bunny.py

# Upload PDFs from test_files/ to the Bunny layout
# <year>/Volume <volume>/Issue <issue>, <year>/<filename>
python gjms-automation/upload_articles.py --dry-run   # preview destinations only
python gjms-automation/upload_articles.py

# Repair titles/authors/abstracts across the whole archive (preview first).
python gjms-automation/backfill_archive.py --dry-run
python gjms-automation/backfill_archive.py

# Repair author metadata in the frontend archive (preview first; keeps a .bak).
# Fixes an author left in the title and institution/degree text inside `authors`.
python gjms-automation/repair_authors.py --dry-run
python gjms-automation/repair_authors.py

# Verify every frontend PDF link without downloading complete files.
python gjms-automation/verify_links.py
python gjms-automation/verify_links.py --workers 1 --delay 1   # rate-limited
```

## Analysis tooling

`../scripts/` holds read-only measurement tools — an OpenDataLoader node-tree
inspector and an accuracy harness that scores extraction against the curated
archive titles. They never touch Bunny or GitHub. See `../scripts/README.md`.

## Configuration

FTP settings are read from the ignored `.env` file in the repository root; never
put the password in source, docs, command output or commits.

Pushing needs the deploy key `~/.ssh/id_rsa_rafayghafoor` (override with
`GJMS_SSH_KEY`). There is no active `Host github.com` block in `~/.ssh/config`
and this machine's ssh binary compiles in a non-standard default port, so an
unpinned `git push` fails with `Permission denied (publickey)`.
