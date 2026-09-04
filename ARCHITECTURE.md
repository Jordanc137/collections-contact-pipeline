# Architecture

## Data flow

```
Raw input files (various platforms, various formats)
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  step1_consolidate.py                                   │
│  • Detects 7 input formats by column headers            │
│  • Normalizes to unified schema                         │
│  • Incremental: skips (DATE, TOOL) pairs already seen   │
│  • Output: cdr_master.parquet                           │
└─────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  step2_enrich.py                                        │
│  • Joins CDR to daily assignment files                  │
│  • Resolves campaign, segment, account ID, phone slot   │
│  • Priority: today's assignment > fallback > inherited  │
│  • Filters excluded campaigns (two-point defense)       │
│  • Output: cdr_enriched.parquet + assignments_stats.json│
└─────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  step3_aggregate.py                                     │
│  • KPIs per tool / campaign / week / day                │
│  • Contact intensity distribution (buckets)             │
│  • Coverage per phone slot (phone1..phone4)             │
│  • Output: dashboard_data.json                          │
└──────────────┬──────────────────────────────────────────┘
               │ (all steps below run in parallel)
    ┌──────────┼──────────┬────────────┬──────────────┐
    ▼          ▼          ▼            ▼              ▼
stepE       step7      step4        step8          step9
strategy  excluded   payments    best_hour      pay_cutoff
    │          │      + step4b        │              │
    └──────────┴──────────┴────────────┴──────────────┘
               │ (all JSON outputs)
               ▼
┌─────────────────────────────────────────────────────────┐
│  step5_dashboard.py                                     │
│  • Reads all JSON outputs                               │
│  • Renders HTML dashboard from template                 │
│  • Output: dashboard.html                               │
└─────────────────────────────────────────────────────────┘
```

## Optimization pipeline (separate process)

```
Daily assignment file (CSV/XLSX with accounts for today)
        │
        ▼
stepH3_cross_day_hour.py
  • Reads payment history from ANALISIS2 / external source
  • Computes: most frequent payment day, most frequent hour, per account
  • Output: prioritization_today.csv

        ▼
stepH4_rename_campaigns.py
  • Applies campaign catalog (ID → name)
  • Excludes campaigns managed outside this analysis
  • Output: prioritization_today.csv (updated in place)

        ▼
stepH5_tool_optimizer.py
  • Input 1: prioritization_today.csv
  • Input 2: cdr_enriched.parquet (from main pipeline)
  • Validates input freshness before proceeding
  • Produces: Prioritization_TOOL_DD_MM_YY.xlsx (4 sheets)
```

## Key files

| File | Role | Size (typical) |
|------|------|----------------|
| `cdr_master.parquet` | Raw CDR history (all platforms, all dates) | 50–200 MB |
| `cdr_enriched.parquet` | Enriched CDR (campaign, account, phone slot resolved) | 80–300 MB |
| `dashboard_data.json` | Pre-aggregated KPIs for dashboard rendering | 5–20 MB |
| `assignments_stats.json` | Coverage stats per day (assigned vs contacted) | 1–5 MB |

## Parallelism

Steps E, 7, 4+4b, 8, 9 are independent — each reads only `cdr_enriched.parquet` plus its own optional inputs, and writes its own JSON to `salidas/`. They run in parallel via `ThreadPoolExecutor`. Step 5 (dashboard) waits for all of them.

The sequential constraint is: step 1 must complete before step 2, step 2 before step 3, step 3 before the parallel group.

## Deduplication strategy

The master tracks processed `(DATE, TOOL)` pairs. On each run:
1. Existing pairs are loaded via columnar read (3 columns, fast).
2. For each input file, rows for already-processed `(DATE, TOOL)` pairs are skipped.
3. After processing each file, newly seen pairs are added to the in-memory set.

**Why composite key**: Blaster and Predictive files for the same campaign date often arrive on different days. A date-only key would skip Predictive if Blaster was already processed for that date.

**Blaster CHOCK / MuttechMX disambiguation**: Both share a business tool name ("Blaster", "Predictivo") with their classic counterparts but are independent source systems that can arrive in separate runs for the same date. They use a suffix in the dedup key (`Blaster_CHOCK`, `Predictivo_MT`) to avoid skipping each other.

## Campaign exclusion (two-point defense)

Campaigns managed outside this analysis (e.g. employees, restructured accounts, special squads) are filtered at two points:

1. **Assignment loading** (`step2_enrich.py`): excluded campaign IDs never enter the assignment lookup tables or coverage stats.
2. **CDR enrichment** (`step2_enrich.py`, main enrichment loop): defense against campaigns reconstructed via fallback/inheritance when no direct assignment match exists.

To exclude a new campaign: add its name to `EXCLUDED_CAMPAIGNS` in `config.py`. No other change needed.

## Missing data policy

Missing data is **never coerced to zero**. If a tool has no data for a given day/week:
- Dashboard displays "no data yet" (or equivalent)
- JSON field is `null`, not `0`

A zero would be analytically incorrect — absence of evidence ≠ zero activity for that tool.
