# `scripts/` — analysis and validation tooling

Standalone helper scripts for diagnosing and measuring the `gjms-automation`
metadata pipeline. They are **not** part of the publishing flow; nothing in
`run_automation.py` imports them. Use them to answer "is extraction getting
better or worse?" before changing `gjms-automation/metadata.py`.

All scripts are read-only with respect to Bunny and GitHub. They only write
into `scripts/reports/`.

## Quick start

```bash
# Fast iteration loop (~20s for 40 PDFs): stratified subset, no old-pipeline
# baseline. Use this while tuning rules.
python scripts/evaluate_metadata_accuracy.py --sample 40 --no-baseline

# Authoritative number before committing a change (~7 min for the full corpus).
python scripts/evaluate_metadata_accuracy.py

# Inspect one paper's actual OpenDataLoader node tree when a rule misbehaves.
python scripts/inspect_pdf_structure.py "Khadija Shaheen Paper.pdf"
python scripts/inspect_pdf_structure.py --grep "ABSTRACT" --limit 5
```

## `inspect_pdf_structure.py`

Dumps page 1 of a PDF as an ordered node list: index, `type`, `pdfua_tag`,
`heading level`, font, font size and a content preview, plus an `--json` mode
for the raw OpenDataLoader JSON.

Use it whenever a paper extracts wrongly — almost every rule in `metadata.py`
exists because of a specific tree shape observed here.

Notable shapes already discovered:

| Paper | Trap |
| --- | --- |
| `Khadija Shaheen Paper.pdf` | clean: `H1` title, `H3` author, `H2` Abstract |
| `Saima Musarrat paper.pdf` | title/authors are plain paragraphs; only `Abstract.` is `H1` |
| `Asia Gul paper.pdf` | journal header is `H3`, title is `H2`, `ABSTRACT` is `H1` (levels scrambled) |
| `Tania Manzoor Paper.pdf` | one node holds `Research Article\n<title line 1>\n<title line 2>` |
| `Haleema Rashid research paper.pdf` | one node holds `<author line>\nABSTRACT: <abstract text>` |

## `evaluate_metadata_accuracy.py`

Runs extraction over the corpus and scores it against the human-curated titles
already in `../gjms-frontend/archive-data.json`.

### How accuracy is measured

There is no hand-labelled ground truth, so the curated archive titles are the
reference. **`IDENTICAL to curated` is the headline target**: the extracted
title, normalised, equals the curated title exactly.

> **GOTCHA: do not evaluate against the current `archive-data.json`.** Once
> `backfill_archive.py` has run, that file *contains* the extractor's own output
> (26 titles replaced, 258 cleaned), so comparing extraction against it is
> circular — 158 of 342 entries trivially "match". For a non-circular
> comparison, dump the pre-backfill reference from git:
>
> ```bash
> cd ../gjms-frontend && git show HEAD:archive-data.json > /tmp/archive_original.json
> python scripts/evaluate_metadata_accuracy.py --archive /tmp/archive_original.json
> ```

Two caveats also drive the metric design:

1. **Legacy entries append the author to the title** (`"... Multan City -
   Pakistan - Khadija Naqvi"`). Titles are therefore compared twice: raw, and
   against a "core" reference with the trailing ` - Author` segment stripped.
2. **Some curated titles are truncated** (`"Debt, Inflation, Job Destruction
   and Its Impact - Arshia Yameen"`). A *better* extraction then fails a plain
   prefix test, so `covers` counts "the extraction begins with the curated
   title", and `prefix or completes ref` is the fair headline number.

Reported per side (`new` vs `old`, the pre-existing regex pipeline):

- `IDENTICAL to curated` — normalised exact equality (the target)
- `exact prefix match` — extraction is a prefix of the curated title
- `extraction completes ref` — extraction starts with the (truncated) curated title
- `prefix or completes ref` — headline accuracy
- `similarity >= 0.90 / 0.80 / 0.60` — difflib ratio bands (case/punctuation insensitive)
- `mean similarity`

Self-referential records are excluded automatically: for articles this
automation published, the archive title *is* the old pipeline's output, so
scoring `old` against them would always be 1.0.

### Options

| Flag | Purpose |
| --- | --- |
| `--sample N` | Stratified subset: round-robins across year/volume directories so a small sample still covers every layout generation |
| `--archive PATH` | Reference archive JSON. Point this at the **pre-backfill** copy dumped from git, or the comparison is circular |
| `--no-baseline` | Skip the old-pipeline run; roughly halves runtime |
| `--limit N` | First N PDFs alphabetically (biased — prefer `--sample`) |
| `--show N` | How many mismatches/regressions to print (default 25) |
| `--out PATH` | Report path (default `scripts/reports/metadata_accuracy.json`) |
| `--corpus`, `--archive`, `--workers` | Override defaults |

### Output

`scripts/reports/metadata_accuracy.json` — one record per PDF with the extracted
`title`/`authors`/`abstract`, `source` (`structure`, `legacy`, `filename`),
`confidence`, `rtl`, `notes`, the reference title, and the `new`/`old`/`new_core`/
`old_core` score objects. This is a generated artifact; it is not committed.

## Baseline: the old pipeline

`score()` also runs the pre-existing pipeline (plain-text extraction plus the
regex heuristics in `extract_articles.parse_article`) so every change is
measured against it. Last comparison against the **pre-backfill** curated titles
(stratified sample of 80 PDFs, 62 author-stripped "core" titles; a full-corpus
run wants a quiet machine):

| Metric (vs curated core) | new | old |
| --- | --- | --- |
| **IDENTICAL to curated** | **79.0%** | 43.5% |
| prefix or completes ref | **88.7%** | 53.2% |
| similarity >= 0.90 | **85.5%** | 48.4% |
| mean similarity | **0.958** | 0.778 |
| suspicious titles (boilerplate picked) | **0/80** | 24/80 (30.0%) |
| authors found | **92.5%** | (fallback to filename) |
| abstract found | **98.8%** | 0% (never extracted) |

`IDENTICAL` against the **raw** curated titles is 0% for both sides by
construction, because legacy titles append the author ("... - Khadija Naqvi");
that is exactly why the author-stripped "core" comparison exists. The residual
21% of non-identical titles are mostly hand-cleaned curated titles (the PDF has
publisher typos), right-to-left Urdu papers with no Latin title, and cases where
the backfill deliberately kept the curated title.

## Corpus location

Both scripts default to `bunny-content/`, the local FTP mirror maintained by
`gjms-automation/download_bunny.py`. Run that first if PDFs are missing.
