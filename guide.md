# GJMS automation guide

This repository contains the automation used to maintain the GJMS article
archive. The canonical automation scripts are in `gjms-automation/`.

## Repositories and locations

- Automation repository: `/Users/emumba/Downloads/projects/gjmsweb`
- Frontend repository: `/Users/emumba/Downloads/projects/gjms-frontend`
- New PDFs waiting for processing: `test_files/`
- Local Bunny mirror: `bunny-content/` (ignored by Git)
- Frontend archive data: `../gjms-frontend/archive-data.json`

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

```bash
python gjms-automation/extract_articles.py test_files
```

The extractor:

- Uses OpenDataLoader for first-page text.
- Runs up to 16 PDFs concurrently.
- Shows Rich live progress.
- Uses OCR only when extracted text is empty.
- Handles current and older GJMS page layouts.
- Converts titles to title case.
- Fails explicitly instead of inventing metadata.

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
directories. It prints each completed upload. Re-running the command uploads
the same filenames again, so use the dry run and inspect the remote directory
before rerunning.

### 5. Update the frontend archive

Edit:

```text
/Users/emumba/Downloads/projects/gjms-frontend/archive-data.json
```

Each issue is an object with `volume`, `title`, and `articles`. Recent entries
use this article shape:

```json
{
  "num": 1,
  "title": "Title Case Article Title",
  "authors": "Author One, Author Two",
  "pages": "1-25",
  "pdf": "http://gjms.b-cdn.net/2025/Volume 11/Issue 3, 2025/article.pdf"
}
```

Preserve the existing field names and URL convention. Validate the JSON:

```bash
python -m json.tool /Users/emumba/Downloads/projects/gjms-frontend/archive-data.json >/dev/null
```

### 6. Commit and publish

```bash
cd /Users/emumba/Downloads/projects/gjms-frontend
git diff -- archive-data.json
git add archive-data.json
git commit -m "Add journal archive entries"
git push origin main
```

Pushing `main` publishes the frontend through its existing GitHub deployment.

For the normal automated workflow, use one command instead:

```bash
python gjms-automation/run_automation.py
```

It processes PDFs concurrently, uploads them to Bunny, updates the correct
top-level archive issue, commits the JSON, and pushes `main`. Use
`--dry-run` to preview metadata and destinations without changing Bunny or
GitHub.

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
human-readable operational reference. Any future long-running command must
retain live progress, concurrency where appropriate, and explicit failure
reporting.
