# GJMS automation workflow

## Purpose

The `gjms-automation` tools maintain the article archive:

1. Download the complete Bunny Storage FTP tree into `bunny-content/`.
2. Extract article titles, authors and abstracts from PDF first pages.
3. **Preview the extracted metadata and confirm** before anything is changed.
4. Upload new PDFs to Bunny using the existing layout:
   `year/Volume <volume>/Issue <issue>, <year>/<filename>`. A file already on
   Bunny at the same size is **skipped**, so re-running is cheap and safe.
5. Update `/Users/emumba/Downloads/projects/gjms-frontend/archive-data.json`
   (titles/authors) and `abstracts.json` (abstracts, keyed by PDF URL). An entry
   already present is **updated in place** when its metadata changed, which is
   how re-running applies a better title.
6. The website's SvelteKit build prerenders `article/<slug>.html` and its own
   `sitemap.xml`/`robots.txt` from that data. **The automation does not
   generate pages** (see "Static pages and SEO" below).
7. Push the frontend repository to GitHub to publish the changes.

## Manual corrections

Extraction is rebuilt from the PDFs on every run, so a hand correction must be
recorded somewhere that survives. `gjms-automation/overrides.json` (committed,
keyed by PDF filename) holds title/author/abstract overrides and is applied
**last**, after extraction and after the backfill - a human edit always wins.

`slug` is deliberately not overridable: it is the published URL.

Set one with the CLI, which writes the override, updates the JSON, regenerates
the affected pages and (with `--push`) publishes:

```bash
python gjms-automation/cli.py set "<pattern>" --title "..." --authors "..."
python gjms-automation/cli.py clear "<pattern>"     # hand back to extraction
python gjms-automation/cli.py                       # interactive menu
```

Step 3 is the safety gate. The original bug was a title that had swallowed the
author line, affiliations, an email and two ORCIDs (461 characters) and was
published with nobody looking at the metadata. `run_automation.py` therefore
prints every new article's title, authors and abstract length with warning flags
(low confidence, RTL page, missing authors/abstract, over-long title) and
refuses to continue unless the operator approves:

* clean run -> type `y`
* any warnings -> must type `publish`
* not a terminal -> refuses unless `--yes` is passed

Step 7 needs the deploy key `~/.ssh/id_rsa_rafayghafoor`: there is no active
`Host github.com` block in `~/.ssh/config`, and this machine's ssh binary
compiles in a non-standard default port, so an unpinned `git push` fails with
`Permission denied (publickey)`. `run_automation.py` sets `GIT_SSH_COMMAND` to
that key (override with `GJMS_SSH_KEY`); both `gjmsweb` and `gjms-frontend` also
carry a local `core.sshCommand` for manual pushes.

## Metadata extraction

`gjms-automation/metadata.py` owns title/author/abstract extraction.

It reads OpenDataLoader's structured JSON (not just its plain text) because
**heading levels are not trustworthy across volumes**: in one paper the title is
`H1`, in another the *Abstract* is `H1` and the title is an untagged paragraph,
and in another the journal header is `H3` while the title is `H2`. A single node
also often holds several logical lines, e.g. `"Research Article\n<title line 1>\n
<title line 2>"` or `"<author line>\nABSTRACT: <text>"`.

So extraction walks the node tree for **order and font size**, splits every node
into lines, and applies layout rules per line. It returns `confidence` and `rtl`
flags; callers must keep the existing curated title when `confidence != "high"`,
and must never overwrite the title of an `rtl` page (a pure-Urdu paper has no
usable Latin title). Title/author/abstract quality is measured against the
curated archive titles - see `scripts/README.md`.

A VLM hybrid for title extraction was tested and **rejected** (moondream-1.8b
captions rather than transcribes; its `Question:` stop token makes read prompts
return empty). Accuracy ceiling is ~80% because most remaining differences are
the curated title being truncated or the PDF containing publisher typos. Read
`scripts/README.md` before re-attempting any model-based extraction.

Live progress must name the current file: use a Rich progress column bound to
`task.fields[current]`, as `extract_articles.py` does.

## Static pages and SEO

The frontend is a **SvelteKit app** (`gjms-frontend`) that `@sveltejs/adapter-static`
prerenders into `build/`; the deploy workflow runs `npm ci && npm run build` and
publishes only `build/`. It generates `article/<slug>.html` from `archive-data.json`
and emits its own `sitemap.xml` (401 URLs), `robots.txt`, `llms.txt` and 404 page.

The automation supplies **data only**: `archive-data.json` (titles/authors) and
`abstracts.json` (abstracts keyed by PDF URL). The data layer in
`src/lib/data/archive.ts` reads the former and `src/lib/client/abstracts.ts` the
latter; the site keeps both at the repository root and imports them.

`slug` is load-bearing: the frontend derives every article URL from it
(`archive.articles.filter(a => a.slug)`), so it must be stable and must never be
hand-written or renumbered - `build_pages.py` assigns it once and persists it.

`gjms-automation/build_pages.py` wrote a parallel set of `article/*.html`,
`sitemap.xml` and `robots.txt` at the repository root for the previous flat-HTML
site. Those are **not deployed** and are now legacy: two sources for one set of
URLs would drift. `run_automation.py` no longer calls it.

## Live progress requirement

Every long-running operation must print live progress to the terminal. At minimum,
progress must include the current item, total item count, filename, and a useful
counter such as bytes processed. The Bunny downloader implements this as
Rich progress/status output while connecting, scanning each remote directory,
building the manifest, and running `aria2c`'s live multi-download progress.
Future extraction and archive update commands must retain equivalent progress
output.

PDF extraction runs concurrently with up to 16 workers and uses an isolated
OpenDataLoader temporary directory for each PDF.

The mirrored corpus contains 325 PDFs. The parser successfully processes all
325 (324 via OpenDataLoader structure tags, 1 via the legacy text path). OCR is
used only when OpenDataLoader returns no text, which handles the scanned
`2016/Volume 2/Issue 4, 2016/Farkhanda paper.pdf`; normal PDFs do not go through
OCR. A mirror re-run reports "Mirror is already up to date" (336 remote files,
all matching by size), so the 325 local PDFs are current - note the downloader
compares file size, not a checksum.

The mirror can go **stale in both directions**: a run recovered three Issue 3,
2025 papers (`Rashid Majeed`, `Arfa Iqbal`, `Maria Riaz`) that were on Bunny but
missing locally. Re-run `download_bunny.py` before trusting corpus counts. There
is also known drift between the archive JSON and Bunny: some mirrored PDFs are
not referenced by any archive entry, and some archive URLs point to files that
no longer exist on Bunny.

Downloads use `aria2c` with up to 64 concurrent small-file transfers, up to 16
connections per server (the aria2 limit), resume support, and temporary
credentials/configuration files that are removed after the command. Files whose
local size matches the remote FTP size are omitted from the manifest and
skipped.

## Commands

```bash
# Interactive menu (publish, browse, edit, clear, pages, backfill, mirror, links).
python gjms-automation/cli.py

# Correct an article's metadata by hand and keep the correction.
python gjms-automation/cli.py set "<pattern>" --title "..." --push
python gjms-automation/cli.py show "<pattern>"
python gjms-automation/cli.py list "<pattern>"

# Preview extracted metadata, confirm, then upload/update/push.
python gjms-automation/run_automation.py

# Preview only: prints metadata + warnings, changes nothing.
python gjms-automation/run_automation.py --dry-run

# Skip the confirmation prompt (unattended runs only).
python gjms-automation/run_automation.py --yes

# Mirror all Bunny content locally.
python gjms-automation/download_bunny.py

# Preview upload destinations without changing Bunny.
python gjms-automation/upload_articles.py --dry-run

# Upload PDFs from test_files.
python gjms-automation/upload_articles.py

# Regenerate article pages, sitemap and robots from the archive JSON.
python gjms-automation/build_pages.py --dry-run
python gjms-automation/build_pages.py
```

Analysis and validation tooling lives in `scripts/` (read-only; it never touches
Bunny or GitHub). See `scripts/README.md` for full documentation.

```bash
# Fast accuracy loop while tuning extraction rules (~20s for 40 PDFs).
python scripts/evaluate_metadata_accuracy.py --sample 40 --no-baseline

# Authoritative full-corpus accuracy against the curated archive titles.
python scripts/evaluate_metadata_accuracy.py

# Dump one PDF's OpenDataLoader node tree when a paper extracts wrongly.
python scripts/inspect_pdf_structure.py "Khadija Shaheen Paper.pdf"
```

Downloaded content is kept in `bunny-content/` so it does not overwrite the
small `test_files/` development fixtures. FTP credentials remain in the ignored
root `.env` file.
