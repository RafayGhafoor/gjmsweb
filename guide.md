# GJMS automation guide

This repository contains the automation used to maintain the GJMS article
archive. The canonical automation scripts are in `gjms-automation/`.

## Repositories and locations

- Automation repository: `/Users/emumba/Downloads/projects/gjmsweb`
- Frontend repository: `/Users/emumba/Downloads/projects/gjms-frontend`
- New PDFs waiting for processing: `test_files/`
- Local Bunny mirror: `bunny-content/` (ignored by Git)
- Frontend archive data: `../gjms-frontend/archive-data.json` (titles/authors)
- Frontend abstracts: `../gjms-frontend/abstracts.json` (abstracts, keyed by PDF URL)
- Generated article pages: `../gjms-frontend/article/<slug>.html`, plus
  `sitemap.xml` and `robots.txt`

## Required tools

Install dependencies from the repository:

```bash
python -m pip install -r requirements.txt
```

The workflow also requires:

- Java, required by `opendataloader-pdf`
- `aria2c`, used for parallel FTP downloads
- macOS Vision support through `ocrmac` for scanned PDFs
- Git and access to the frontend GitHub repository

FTP credentials are read from the ignored root `.env` file. Never put the
password in source code, documentation, command output, or Git commits.

## Bunny Storage layout

Bunny is accessed over passive FTP at the configured host and port. The
existing storage layout is:

```text
<year>/Volume <volume>/Issue <issue>, <year>/<filename>
```

For example:

```text
2025/Volume 11/Issue 3, 2025/Arfa Iqbal paper.pdf
```

The upload script reads the year, volume, and issue from the PDF first page and
creates missing directories when needed.

## Normal workflow for new articles

### 1. Put PDFs in `test_files/`

Only PDFs should be processed by the article workflow. Filenames are retained
when uploading and in the Bunny URL.

### 2. Extract and review metadata

Metadata extraction lives in `gjms-automation/metadata.py`. It reads
OpenDataLoader's structured JSON (not just its plain text) because heading
levels are not trustworthy across volumes; it walks the node tree for order and
font size, then applies layout rules per line. It returns the title, authors,
abstract and affiliations plus `confidence` and `rtl` flags.

**Reviewing happens in `run_automation.py`, which previews before it changes
anything** (see step 6). To inspect extraction alone:

```bash
python gjms-automation/run_automation.py --dry-run
```

The extractor:

- Uses OpenDataLoader structured JSON for first-page content.
- Runs up to 16 PDFs concurrently, each with its own temporary directory.
- Shows Rich live progress naming the current file.
- Uses OCR only when OpenDataLoader returns no text (scanned PDFs).
- Handles current and older GJMS page layouts, including merged nodes such as
  `"Research Article\n<title line 1>\n<title line 2>"`.
- Keeps the titled casing the PDF itself uses, and never publishes a title whose
  `confidence` is not `high`.
- Leaves the title empty for right-to-left (Urdu) pages, where no Latin title
  exists on the page, so the curated archive title is kept.

`extract_articles.py` remains the legacy plain-text parser and is still used for
reading the issue metadata and page range.

Review title, authors, page range, volume, issue, and year before publishing.

### 3. Preview Bunny destinations

```bash
python gjms-automation/upload_articles.py --dry-run
```

This extracts the destination for every PDF and does not connect to Bunny.

### 4. Upload PDFs

```bash
python gjms-automation/upload_articles.py
```

The command uploads PDFs from `test_files/` to their derived Bunny
directories and prints each completed upload.

**Re-running is safe and cheap.** A file already on Bunny at the same size is
**skipped**, so re-running only sends files that are new or have changed. This
is what lets you re-run the workflow purely to refresh metadata: nothing is
re-uploaded, but improved titles, authors and abstracts are applied (step 5).

### Correcting metadata by hand

Because extraction is rebuilt from the PDFs on every run, a manual correction
has to be recorded somewhere that survives. Overrides live in
`gjms-automation/overrides.json`, keyed by PDF filename, and are applied **last**
— so a hand correction always wins over extraction. The easiest way to set one:

```bash
python gjms-automation/cli.py set "unpacking the growth" \
    --title "Unpacking the Growth Puzzle in Pakistan" \
    --authors "Khadija Shaheen, Abdul Ghafoor Awan"
```

That writes the override, updates `archive-data.json` and `abstracts.json`,
regenerates the affected pages, and (with `--push`) publishes. Add `--note` to
record why. To hand back to extraction: `cli.py clear "<pattern>"`.

The `slug` is deliberately **not** overridable: it is the published URL, so
correcting a title must not change it.

Run `python gjms-automation/cli.py` with no arguments for an interactive menu
(publish, browse, edit, clear, pages, backfill, mirror, links).

### 5. Update the frontend archive

`run_automation.py` does this automatically. It writes two files:

**`archive-data.json`** — each issue is an object with `volume`, `title`, and
`articles`. Recent entries use this article shape:

```json
{
  "num": 1,
  "title": "Title Case Article Title",
  "authors": "Author One, Author Two",
  "pages": "1-25",
  "pdf": "http://gjms.b-cdn.net/2025/Volume 11/Issue 3, 2025/article.pdf",
  "slug": "title-case-article-title-1a2b3c4"
}
```

**`abstracts.json`** — a flat map of PDF URL to abstract text. Abstracts are kept
out of `archive-data.json` so the archive page stays light; it lazy-loads them
on click.

The `slug` is the article's published URL (`article/<slug>.html`). It is persisted
and only ever assigned when missing, so correcting a title later cannot change a
already-published URL. Do not hand-edit it.

An article already in the archive is **updated in place** when its metadata has
changed, rather than skipped — that is how re-running applies a better title. A
low-confidence or RTL extraction never replaces a title that is already there,
though it may still fill in authors or the abstract.

Preserve the existing field names and URL convention. Validate the JSON:

```bash
python -m json.tool /Users/emumba/Downloads/projects/gjms-frontend/archive-data.json >/dev/null
```

### 6. Commit and publish

```bash
python gjms-automation/run_automation.py
```

It processes PDFs concurrently, previews the extracted metadata, asks for
confirmation, then uploads to Bunny, updates both JSON files, regenerates the
static article pages, commits and pushes `main`.

The preview is the safety gate. The original bug was a title that had swallowed
the author line, affiliations, an email and two ORCIDs (461 characters) and was
published with nobody looking at it. So every new article's title, authors and
abstract length is printed with warning flags (low confidence, RTL page,
missing authors/abstract, over-long title) before anything changes:

* clean run -> type `y`
* any warnings -> must type `publish`
* not a terminal -> refuses unless `--yes` is passed

Use `--dry-run` to preview and stop, or `--yes` for unattended runs.

For a manual publish, the files to stage are `archive-data.json`,
`abstracts.json`, `sitemap.xml`, `robots.txt` and `article/` — not just the JSON.
Pushing `main` publishes the frontend through its existing GitHub deployment.

To verify all archive PDF links without downloading complete PDFs:

```bash
python gjms-automation/verify_links.py
```

The checker encodes spaces and other path characters, uses concurrent HEAD
requests, and falls back to a one-byte ranged request when HEAD is unsupported.
If Bunny rate-limits requests, run it with `--workers 1 --delay 1`.

## Repairing existing archive entries

`backfill_archive.py` is a one-off repair pass over the whole archive (whereas
`run_automation.py` only handles newly uploaded PDFs):

```bash
python gjms-automation/backfill_archive.py --dry-run
python gjms-automation/backfill_archive.py
```

It fills authors and abstracts, strips the legacy `"Title - Author Name"` suffix
out of titles, and replaces a title only when the curated one is unusable (it
contains authors, emails, ORCIDs, journal furniture such as "Original Article",
or exceeds 220 characters) or when the extraction is a strict superset of it.
Anything with low confidence, a right-to-left page, or no local PDF is left
untouched.

## Static pages and search engines

`build_pages.py` generates `article/<slug>.html` for every article plus
`sitemap.xml` and `robots.txt`:

```bash
python gjms-automation/build_pages.py --dry-run
python gjms-automation/build_pages.py
```

This exists because `archive.html` builds its article list **in JavaScript,
inside a modal, only after a click**. Search engines do not click, so that page
exposes no titles, authors, abstracts or PDF links. The static pages are the
indexable surface: real HTML with the abstract inline, `ScholarlyArticle`
JSON-LD, a meta description, a canonical URL and `citation_*` meta tags. The
archive modal still lazy-loads from `abstracts.json` to stay light.

After deploying, submit `https://gjmsweb.com/sitemap.xml` to Google Search
Console — a new sitemap is not discovered on its own.

## Measuring extraction accuracy

`scripts/` holds read-only analysis tools; see `scripts/README.md`. They score
title extraction against the curated archive titles (`IDENTICAL to curated` is
the headline metric) and can dump a PDF's OpenDataLoader node tree when one
extracts wrongly. They never touch Bunny or GitHub.

The realistic ceiling is around 80%, not 100%: most remaining differences are
the curated title being truncated, the PDF containing publisher typos, or a
pure-Urdu paper whose English title does not exist on the page. A VLM hybrid was
tested and rejected — read `scripts/README.md` before re-attempting any
model-based extraction.

## Refreshing the Bunny mirror

To download the complete remote tree locally:

```bash
python gjms-automation/download_bunny.py
```

The downloader:

- Scans year directories in parallel.
- Uses `aria2c` with up to 64 concurrent downloads and up to 16 connections
  per server.
- Skips files whose local size matches the remote FTP size.
- Restarts incomplete files because Bunny FTP does not support resume requests.
- Shows connection, scan, manifest, and download progress.

The mirror is for auditing and parser development; it is not committed.

## Troubleshooting

- **FTP listing is empty with `NLST`:** use detailed `LIST`; Bunny can return an
  empty `NLST` response even when directories exist.
- **FTP root appears as `/gjms/`:** use the login working directory returned by
  `PWD`; do not force `cwd("/")`.
- **aria2 says FTP does not support resuming:** remove the stale `.aria2`
  sidecar or rerun the downloader; the script clears sidecars for pending
  files.
- **A PDF fails parsing:** inspect its first-page text. If it is scanned,
  `ocrmac` is used automatically when OpenDataLoader returns no text. Do not
  add guessed metadata without manual review.
- **Progress is missing:** run Python unbuffered:

  ```bash
  PYTHONUNBUFFERED=1 python -u gjms-automation/download_bunny.py
  ```

## Documentation contracts

`agents.md` records the durable workflow rules for AI agents. This guide is the
human-readable operational reference. `scripts/README.md` documents the accuracy
harness and its metric caveats; `gjms-automation/README.md` is the tool index;
`gjms-frontend/CLAUDE.md` describes the website. Any future long-running command
must retain live progress (naming the current file), concurrency where
appropriate, and explicit failure reporting.
