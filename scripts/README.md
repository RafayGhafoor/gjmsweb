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
measured against it. Last run: **full corpus** (325 PDFs, 272 comparable
author-stripped "core" titles), against the pre-backfill reference.

| Metric (vs curated core) | new | old |
| --- | --- | --- |
| **IDENTICAL to curated** | **77.6%** | 46.7% |
| prefix or completes ref | **83.5%** | 52.6% |
| similarity >= 0.90 | **86.8%** | 54.4% |
| mean similarity | **0.935** | 0.753 |
| suspicious titles (boilerplate picked) | **0/325** | 102/325 (31.4%) |
| authors found | **89.8%** | (fallback to filename) |
| abstract found | **95.7%** | 0% (never extracted) |
| high confidence | 89.5% | n/a |

`IDENTICAL` against the **raw** curated titles is 0% for both sides by
construction, because legacy titles append the author ("... - Khadija Naqvi");
that is exactly why the author-stripped "core" comparison exists.

### Why the ceiling here is ~80%, not 100%

Of the 61 differing titles, only a handful were real extraction errors. Bucketing
them prevented wasted effort (and stopped a VLM being adopted on a hunch):

| Bucket | Count | Real error? |
| --- | --- | --- |
| extraction is LONGER (curated title was truncated) | ~14 | no - extraction is better |
| near-miss, ratio >= 0.90 (PDF has publisher typos, curator cleaned them) | ~18 | no |
| right-to-left/Urdu page: no English title exists in the PDF | 13 | no - unrecoverable |
| low/medium confidence (backfill deliberately kept curated) | ~8 | no - by design |
| genuine errors (author glued to title, boilerplate prefix) | ~5 | **yes** |

The 13 RTL cases were verified by dumping page 1 line by line: they carry an
Urdu title and an English *abstract*, but no English title at all, and their PDF
metadata is empty or journal boilerplate.

## Rejected: VLM hybrid for title extraction

Tested `thatdamai/moondream-1.8b` via Ollama (and, for comparison,
`richardyoung/olmocr2:7b-q8` and `maternion/LightOnOCR-2`). **Do not re-attempt
this** - it regresses accuracy badly. Evidence, measured on
`Khadija Shaheen Paper.pdf`, whose true title the layout extractor already
reproduces at similarity 1.000:

| Invocation | Result | Similarity to true title |
| --- | --- | --- |
| "Read the title of this paper." | empty in 0.1s | 0.000 |
| "Extract the title verbatim." | empty | 0.000 |
| "Describe this image." (the only prompt that works) | `Unpacking the Growth with Puzzle in Pakistan: The Roles of Technology Export and Population` | **0.459** |
| same at 3x resolution | dropped more words | **0.350** |

Three specific traps:

1. **The Modelfile template is `Question: {{ .Prompt }}\n\nAnswer:` and
   `"Question:"` is a stop token.** Any prompt that makes the model echo it
   halts generation immediately, so every read/extract prompt returns an empty
   string. `/api/chat` behaves identically.
2. **The one working prompt *captions*, it does not transcribe.** It invented
   "with", turned "Exports" into "Export", and dropped "Renewable Energy, FDI,
   Inflation and" entirely.
3. **More resolution made it worse** (0.459 -> 0.350 at 3x).

Note also that OpenDataLoader's own `--hybrid` flag is **not** pluggable to an
arbitrary model: it accepts only `docling-fast` and needs its own backend server
(`opendataloader-pdf[hybrid]`), so a moondream hybrid cannot be wired through it.

On the 13 Urdu pages a VLM cannot help either: it captioned the journal *header*
(`"titled Global Journal of Management Social Sciences and Humanities"`) and
asserted the title was "written in Arabic script". The information is not on the
page.

## Corpus location

Both scripts default to `bunny-content/`, the local FTP mirror maintained by
`gjms-automation/download_bunny.py`. Run that first if PDFs are missing.
