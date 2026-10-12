# GJMS automation

Tools that maintain the GJMS article archive: mirror Bunny, extract metadata,
preview and publish, and generate the frontend's static pages.

For orientation read `../guide.md` (human walkthrough) and `../agents.md`
(durable rules for agents).

## The normal workflow

```bash
python gjms-automation/run_automation.py             # preview -> confirm -> publish
python gjms-automation/run_automation.py --dry-run   # preview only, nothing changes
python gjms-automation/run_automation.py --yes       # skip the prompt (unattended)
```

`run_automation.py` prints every new article's title, authors and abstract
length with warning flags **before** changing anything, then requires approval:

* clean run -> type `y`
* any warnings -> must type `publish`
* not a terminal -> refuses unless `--yes` is passed

It then uploads to Bunny, updates `archive-data.json` (titles/authors) and
`abstracts.json` (abstracts, keyed by PDF URL), regenerates the static article
pages, commits and pushes.

## Tools

| Script | Purpose |
| --- | --- |
| `run_automation.py` | End-to-end workflow with the preview/confirm gate |
| `metadata.py` | Title/author/abstract extraction (library; see `../agents.md`) |
| `download_bunny.py` | Mirror the whole Bunny tree into `bunny-content/` |
| `upload_articles.py` | Upload PDFs to Bunny only, no archive/frontend changes |
| `backfill_archive.py` | One-off repair pass over every archive entry |
| `build_pages.py` | Regenerate `article/<slug>.html`, `sitemap.xml`, `robots.txt` |
| `extract_articles.py` | Legacy metadata parser; still used for issue and page range |
| `verify_links.py` | Check every archive PDF link (HEAD, ranged GET fallback) |

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

# Regenerate the static article pages from the archive JSON.
python gjms-automation/build_pages.py --dry-run
python gjms-automation/build_pages.py

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
