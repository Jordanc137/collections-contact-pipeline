# Collections Contact Analytics Pipeline

**Python · pyarrow · pandas · openpyxl · ThreadPoolExecutor**

> Production-grade ETL and analytics pipeline for a collections operation managing **13,000–17,000 accounts per day** across 7 contact channels. Built, deployed, and maintained solo — from raw dialer exports to executive dashboard, in one `python run_pipeline.py`.

---

## What this solves

Collections teams generate call records from multiple dialing platforms in incompatible formats — different column names, different status vocabularies, different encodings. Meanwhile, the daily portfolio assignment, payment files, and campaign strategy targets live in separate systems. No one has a single view.

This pipeline ingests everything, resolves the identities, and answers:

- Which accounts are we actually reaching, and through which channel?
- Does more contact intensity drive more payments — or just noise?
- What is the optimal hour to dial each campaign, with statistical confidence?
- Which phone number slot (of 4) is each account actually answering on?
- Which accounts have never been contacted despite being in the portfolio for days?

---

## Scale & context

| Metric | Value |
|--------|-------|
| Accounts under management / day | 13,000 – 17,000 |
| Contact channels unified | 7 |
| Input format parsers | 7 (header-detected, never by filename) |
| Numbering plan blocks (IFT binary search) | 178,151 |
| RAM before streaming fix | ~4 GB (disk swapping) |
| RAM after fix | bounded to one 100k-row batch |

---

## Architecture

```
Raw CDR files (7 formats, 7 sources)
        │
        ▼
step1_consolidate.py   ← incremental, dedup on (DATE, TOOL)
        │  cdr_master.parquet
        ▼
step2_enrich.py        ← joins daily assignments, resolves campaign/phone slot
        │  cdr_enriched.parquet
        ▼
step3_aggregate.py     ← KPIs, coverage, intensity distribution
        │
        ├── step4_payments.py      [parallel] payment rate by intensity bucket
        ├── step8_best_hour.py     [parallel] Wilson-score optimal contact hour
        ├── stepE_strategy.py      [parallel] objective vs actual
        └── step6_no_contact.py   [parallel] uncontacted accounts → Excel
                │
                ▼
        step5_dashboard.py         ← assembles final HTML dashboard
```

**Optimization pipeline** (separate daily process, consumes `cdr_enriched.parquet`):
```
stepH3 (payment day/hour patterns)
  → stepH4 (campaign catalog normalization)
    → stepH5 (4-sheet Excel: tool recommendation + age channel + payment scoring)
```

---

## Key engineering problems solved

### 1. Silent data loss in incremental deduplication

**Problem:** CDR files for different tools (Blaster, Predictive, IVR) arrive on different days for the same campaign date. Deduplication keyed only on `DATE` silently dropped entire tools once any tool for that date was processed.

**Fix:** Composite key `(DATE, TOOL)`. Loaded from the existing Parquet using columnar reads — only 3 columns touched, not the full file.

```python
# Only reads DATE, TOOL, SOURCE_FILE — not all 15 columns
cols = cdr_io.read_parquet_columns(MASTER_PATH, ["DATE", "TOOL", "SOURCE_FILE"])
```

### 2. Format detection by headers, never by filename

**Problem:** The same platform used three different naming conventions across three files delivered in the same month.

**Fix:** Every parser triggers solely on its column signature. Seven formats, seven signatures. Zero filename logic anywhere.

```python
if "call_date" in header and "phone_number_dialed" in header:
    → Vicidial EXPORT_CALL_REPORT
elif "campaign_date" in header and "contact_f_id" in header:
    → AI calls CSV
elif "Teléfono" in header and "Resultado" in header:
    → WhatsApp chat CSV
```

### 3. 4 GB RAM → bounded streaming writes

**Problem:** Buffering all rows as Python dicts before writing to Parquet. At production scale (weeks of accumulated history), this caused ~4 GB RAM usage and disk swapping, with pipeline runs exceeding 30 minutes.

**Fix:** True streaming via `pq.ParquetWriter` in batches of 100k rows. Append mode streams both old file and new rows — neither is fully loaded at any point.

```python
# Each batch is written and discarded — memory stays bounded
if len(self._buffer) >= self.batch_size:
    self._flush()  # convert to Arrow batch, write, clear buffer
```

### 4. Windows file handle deadlock on `os.replace()`

**Problem:** `PermissionError (WinError 5)` on `os.replace()`. A `ParquetFile` object left open (even within the same process) locks the underlying file on Windows — Linux allows renaming open files, Windows does not.

**Fix:** Explicit `.close()` on every `ParquetFile` before any rename, guaranteed by `try/finally`.

### 5. Binary search timeout at real scale

**Problem:** Phone number classification rebuilt the 178,151-element search list on every call to `classify()`. Unnoticeable with 3 test blocks; caused timeouts classifying 22,000 real numbers.

**Fix:** `_cache_starts` precomputed once at load time. Zero cost per subsequent call.

### 6. Campaign exclusion defense-in-depth

Excluded campaigns are filtered at two independent points — not one. If a row slips past the assignment filter via fallback/inheritance logic, the enrichment loop catches it and routes it to a separate output file.

### 7. Wilson score CI for optimal contact hour

Raw payment rates mislead at small hourly samples. Wilson score CI stays within [0%, 100%] and an hour is only declared "optimal" if sample size ≥ `MIN_SAMPLE`. No recommendation is better than a spurious one.

---

## Optimization output (stepH5)

Daily 4-sheet Excel delivered to the contact team:

| Sheet | Content |
|-------|---------|
| **Tool Recommendation** | Best channel per account from CDR evidence. Priority 1 = already answers; 2 = suggest switch; 3 = keep current; 9 = new account |
| **Age-Based Recommendation** | Channel suggestion by generational cohort, reconciled with CDR evidence. Evidence always overrides demographic assumption |
| **Payment Classification** | Transparent point-scoring: payment consistency + history depth + delinquency + contactability. Positive / Regular / Negative / Insufficient data |
| **Methodology & Sources** | Full documentation of every calculation, weight, and threshold — auditable by the team |

---

## Setup

```bash
pip install pandas pyarrow openpyxl python-calamine
```

`python-calamine` is optional — Rust-based XLSX parser, ~10–15x faster than openpyxl. The pipeline detects and falls back automatically.

```bash
cp config_example.py config.py
# Set PIPELINE_BASE, CAMPAIGN_CATALOG, EXCLUDED_CAMPAIGNS, etc.

python pipeline/run_pipeline.py

# Daily optimization (after main pipeline completes):
python optimization/stepH3_cross_day_hour.py
python optimization/stepH4_rename_campaigns.py
python optimization/stepH5_tool_optimizer.py
```

---

## Diagnostic utilities

```bash
# Inspect master by date/tool — find gaps before they become problems
python utils/diagnose_master.py --date 2026-08-13

# Remove a date to force reprocessing from source files
python utils/purge_date.py 2026-08-13 --dry-run
python utils/purge_date.py 2026-08-13
```

---

## Design principles

Every decision came from a production failure, not a preference.

| Principle | Origin |
|-----------|--------|
| Header-based format detection | Filenames changed 3× in one month for the same platform |
| `(DATE, TOOL)` composite dedup key | Single-key silently dropped entire tools |
| Streaming Parquet writes | Buffering caused 4 GB RAM + disk swapping |
| Explicit file handle close | Windows `PermissionError` on open file rename |
| No zero-filling of missing data | Absence of evidence ≠ zero activity |
| Two-point campaign exclusion | Fallback logic can reconstruct excluded IDs |
| `idUnico` always typed as string | Numeric coercion caused silent identity mismatches |
| Wilson CI, not raw rate | Raw rates mislead at small n; Wilson stays bounded |

---

## Stack

`Python 3.13` · `pandas` · `pyarrow` · `openpyxl` · `python-calamine` · `ThreadPoolExecutor` · `bisect` · `csv` · `json`

No external API calls. No database. Runs on local files — designed for an operations environment without cloud access.

---

## License

MIT
