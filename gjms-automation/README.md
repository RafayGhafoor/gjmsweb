# GJMS automation

Run the metadata extractor:

```bash
python gjms-automation/extract_articles.py
```

Run the complete upload, archive update, and GitHub publishing workflow:

```bash
python gjms-automation/run_automation.py
```

Preview the complete workflow without uploading, modifying JSON, or pushing:

```bash
python gjms-automation/run_automation.py --dry-run
```

Preview Bunny destinations without uploading:

```bash
python gjms-automation/upload_articles.py --dry-run
```

Upload PDFs to the Bunny Storage layout
`<year>/Volume <volume>/Issue <issue>, <year>/<filename>`:

```bash
python gjms-automation/upload_articles.py
```

FTP settings are read from the ignored `.env` file in the repository root.
