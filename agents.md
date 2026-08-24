# GJMS automation workflow

## Purpose

The `gjms-automation` tools maintain the article archive:

1. Download the complete Bunny Storage FTP tree into `bunny-content/`.
2. Extract article titles and authors from PDF first pages.
3. Upload new PDFs to Bunny using the existing layout:
   `year/Volume <volume>/Issue <issue>, <year>/<filename>`.
4. Update `/Users/emumba/Downloads/projects/gjms-frontend/archive-data.json`.
5. Push the frontend repository to GitHub to publish the changes.

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

The downloaded corpus contains 319 PDFs. The parser successfully processes all
319. OCR is used only when OpenDataLoader returns no text, which handles the
scanned `2016/Volume 2/Issue 4, 2016/Farkhanda paper.pdf`; normal PDFs do not
go through OCR.

Downloads use `aria2c` with up to 64 concurrent small-file transfers, up to 16
connections per server (the aria2 limit), resume support, and temporary
credentials/configuration files that are removed after the command. Files whose
local size matches the remote FTP size are omitted from the manifest and
skipped.

## Commands

```bash
# Run the complete process: extract, upload, update frontend JSON, commit, push.
python gjms-automation/run_automation.py

# Preview the complete process without changing Bunny or GitHub.
python gjms-automation/run_automation.py --dry-run

# Mirror all Bunny content locally.
python gjms-automation/download_bunny.py

# Preview upload destinations without changing Bunny.
python gjms-automation/upload_articles.py --dry-run

# Upload PDFs from test_files.
python gjms-automation/upload_articles.py
```

Downloaded content is kept in `bunny-content/` so it does not overwrite the
small `test_files/` development fixtures. FTP credentials remain in the ignored
root `.env` file.
