# =============================================================================
# stepH3_cross_day_hour.py - Daily Assignment × Payment Pattern Cross-reference
#
# Produces prioritization_today.csv: the daily assignment file enriched with
# each account's historical payment behavior:
#   - Most frequent payment day of the week
#   - Most frequent payment hour
#   - Payment day score (is today a likely payment day?)
#   - Contact priority (accounts whose likely payment day is today rank first)
#
# INPUTS:
#   - Daily assignment file (most recent in ASSIGNMENTS_FOLDER)
#   - payments_day_master.csv  — historical payment day patterns per account
#   - payments_hour_master.csv — historical payment hour patterns per account
#
# OUTPUT:
#   - prioritization_today.csv (in OPT_OUTPUT_FOLDER)
#
# NOTE: payments_day_master.csv and payments_hour_master.csv are pre-computed
# summaries from your payment history database (ANALISIS2 / recovery system).
# Format: account_id, day_name, count (for day master);
#         account_id, hour, count (for hour master).
# The pipeline reads these — it does not compute them from raw payment files.
# =============================================================================
import csv
import glob
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

BASE         = config.OPTIMIZATION_BASE
DIR_DATA     = config.OPT_OUTPUT_FOLDER
DIR_ASIG     = config.OPT_ASSIGNMENTS_FOLDER
OUT_PATH     = os.path.join(DIR_DATA, "prioritization_today.csv")
DAY_MASTER   = os.path.join(DIR_DATA, "payments_day_master.csv")
HOUR_MASTER  = os.path.join(DIR_DATA, "payments_hour_master.csv")

EXCEL_LIMIT  = 1_048_576

WEEKDAY_NAMES = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
                 4: "Friday",  5: "Saturday", 6: "Sunday"}
WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_ABBR  = {d: d[:3] for d in WEEKDAY_NAMES.values()}
ABBR_TO_DAY   = {v: k for k, v in WEEKDAY_ABBR.items()}

TODAY      = datetime.today()
TODAY_NAME = WEEKDAY_NAMES[TODAY.weekday()]

print("=" * 70)
print("OPTIMIZATION - STEP H3: Payment Day/Hour × Today's Assignment")
print(f"Today: {TODAY_NAME} ({TODAY.strftime('%Y-%m-%d')})")
print("=" * 70)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def _read_csv(path):
    for enc in ("utf-8-sig", "latin1"):
        try:
            with open(path, encoding=enc, newline="") as f:
                yield from csv.DictReader(f)
            return
        except UnicodeDecodeError:
            continue


def _read_assignment(path):
    if path.lower().endswith(".csv"):
        yield from _read_csv(path)
        return
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(it)]
        for r in it:
            yield dict(zip(header, r))
        wb.close()
    except Exception as e:
        print(f"  [WARNING] Could not read {os.path.basename(path)}: {e}")


def find_latest_assignment():
    paths = sorted(glob.glob(os.path.join(DIR_ASIG, "*asig*.[cx][ls][vx]*"),
                             ), key=os.path.getmtime, reverse=True)
    if not paths:
        paths = sorted(glob.glob(os.path.join(DIR_ASIG, "*.csv")),
                       key=os.path.getmtime, reverse=True)
    if not paths:
        print(f"[ERROR] No assignment files found in {DIR_ASIG}")
        raise SystemExit(1)
    path = paths[0]
    print(f"Assignment: {os.path.basename(path)}")
    return path


def find_col(keys, aliases):
    low = {k.lower(): k for k in keys}
    for a in aliases:
        if a.lower() in low:
            return low[a.lower()]
    return None


# ---------------------------------------------------------------------------
# LOAD PAYMENT PATTERN MASTERS
# ---------------------------------------------------------------------------
def load_day_master():
    """Returns {account_id: {'top_days': [day,...], 'top_pct': float,
                              'all_days': {day: pct}, 'n_weeks': int}}"""
    if not os.path.exists(DAY_MASTER):
        print(f"  [INFO] {DAY_MASTER} not found — day-of-week analysis skipped")
        return {}
    data = defaultdict(lambda: defaultdict(int))
    for row in _read_csv(DAY_MASTER):
        acct = str(row.get("account_id", "") or "").strip()
        day  = str(row.get("day_name", "") or "").strip()
        try:
            cnt = int(float(row.get("count", 0) or 0))
        except (ValueError, TypeError):
            cnt = 0
        if acct and day:
            data[acct][day] += cnt
    result = {}
    for acct, day_counts in data.items():
        total = sum(day_counts.values())
        if total == 0:
            continue
        sorted_days = sorted(day_counts, key=lambda d: -day_counts[d])
        top_day = sorted_days[0]
        top_pct = round(100 * day_counts[top_day] / total, 1)
        result[acct] = {
            "top_days":   sorted_days[:3],
            "top_day":    top_day,
            "top_pct":    top_pct,
            "all_days":   {d: round(100 * c / total, 1) for d, c in day_counts.items()},
            "n_weeks":    total,
            "is_today_top": top_day == TODAY_NAME,
        }
    print(f"  Day master: {len(result):,} accounts with payment day history")
    return result


def load_hour_master():
    """Returns {account_id: {'top_hour': 'HH:00', 'top_pct': float, ...}}"""
    if not os.path.exists(HOUR_MASTER):
        print(f"  [INFO] {HOUR_MASTER} not found — hour analysis skipped")
        return {}
    data = defaultdict(lambda: defaultdict(int))
    for row in _read_csv(HOUR_MASTER):
        acct = str(row.get("account_id", "") or "").strip()
        try:
            hour = int(float(row.get("hour", -1) or -1))
        except (ValueError, TypeError):
            hour = -1
        try:
            cnt = int(float(row.get("count", 0) or 0))
        except (ValueError, TypeError):
            cnt = 0
        if acct and 0 <= hour <= 23:
            data[acct][hour] += cnt
    result = {}
    for acct, hour_counts in data.items():
        total = sum(hour_counts.values())
        if total == 0:
            continue
        sorted_hours = sorted(hour_counts, key=lambda h: -hour_counts[h])
        top_hour = sorted_hours[0]
        top_pct  = round(100 * hour_counts[top_hour] / total, 1)
        result[acct] = {
            "top_hour":  f"{top_hour:02d}:00",
            "top_hours": [f"{h:02d}:00" for h in sorted_hours[:3]],
            "top_pct":   top_pct,
            "n_records": total,
        }
    print(f"  Hour master: {len(result):,} accounts with payment hour history")
    return result


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    os.makedirs(DIR_DATA, exist_ok=True)

    day_patterns  = load_day_master()
    hour_patterns = load_hour_master()

    path_asig = find_latest_assignment()
    rows_in   = list(_read_assignment(path_asig))
    if not rows_in:
        print("[ERROR] Assignment file is empty")
        raise SystemExit(1)

    keys = list(rows_in[0].keys())
    acct_col = find_col(keys, ["account_id", "idUnico", "id_unico", "cuenta"])
    camp_col = find_col(keys, ["campaign_id", "idCampania", "id_campania"])
    if not acct_col:
        print("[ERROR] Assignment file has no recognizable account ID column")
        raise SystemExit(1)

    # Exclude managed campaigns
    excluded_ids = {str(idc) for idc, name in config.CAMPAIGN_CATALOG.items()
                    if name in config.EXCLUDED_CAMPAIGNS}

    print(f"\nCross-referencing {len(rows_in):,} accounts...")
    rows_out = []
    n_with_day = n_with_hour = n_today = 0

    for row in rows_in:
        acct = str(row.get(acct_col, "") or "").strip()
        idc  = str(row.get(camp_col, "") or "").strip().split(".")[0] if camp_col else ""
        if not acct or idc in excluded_ids:
            continue

        dp = day_patterns.get(acct, {})
        hp = hour_patterns.get(acct, {})

        is_today = dp.get("is_today_top", False)
        n_with_day  += 1 if dp else 0
        n_with_hour += 1 if hp else 0
        n_today     += 1 if is_today else 0

        # Priority: accounts whose top payment day is today rank first (1),
        # then those with day history but not today (2), then those without (3)
        if is_today:
            priority = 1
        elif dp:
            priority = 2
        else:
            priority = 3

        enrich = {
            "contact_priority": priority,
            "is_likely_payment_day_today": is_today,
            "top_payment_day": dp.get("top_day", ""),
            "top_payment_days": ", ".join(dp.get("top_days", [])),
            "top_day_pct": dp.get("top_pct", ""),
            "weeks_with_payment_history": dp.get("n_weeks", ""),
            "top_payment_hour": hp.get("top_hour", ""),
            "top_payment_hours": ", ".join(hp.get("top_hours", [])),
            "top_hour_pct": hp.get("top_pct", ""),
        }
        rows_out.append({**row, **enrich})

    print(f"  Accounts with day pattern:  {n_with_day:,}")
    print(f"  Accounts with hour pattern: {n_with_hour:,}")
    print(f"  Today is likely pay day:    {n_today:,}")

    # Sort: priority 1 first, then 2, then 3
    rows_out.sort(key=lambda r: int(r.get("contact_priority", 9)))

    if len(rows_out) >= EXCEL_LIMIT:
        print(f"  [WARNING] {len(rows_out):,} rows approaches Excel limit ({EXCEL_LIMIT:,})")

    if not rows_out:
        print("[ERROR] No rows to write")
        raise SystemExit(1)

    fieldnames = list(rows_out[0].keys())
    with open(OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows_out)

    print(f"\nOK → {OUT_PATH} ({len(rows_out):,} rows)")


if __name__ == "__main__":
    main()
