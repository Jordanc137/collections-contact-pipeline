# =============================================================================
# step4_payments.py - Payment Rate by Contact Intensity
#
# What percentage of accounts paid, broken down by how many contact attempts
# they received? Answers: does more contact = more payments?
#
# SCOPES (each is independent — no double-counting):
#   total        : bucket = attempts for the full period; universe = all
#                  accounts assigned in any day of the period
#   by_week      : bucket = attempts that week; universe = accounts assigned
#                  that week; payment = paid that week
#   by_date      : bucket = attempts that day; universe = accounts assigned
#                  that day; payment = paid that day
#   *_by_tool    : same buckets but per tool
#
# Bucket 0 = accounts assigned but with zero attempts in that scope.
#
# PAYMENT SOURCES:
#   - RPT payments folder (PAYMENTS_RPT_FOLDER in config.py)
#   - Cut-time payments folder (PAYMENTS_CUT_FOLDER) — used only for hourly
#     analysis in step8; excluded here to avoid double-counting with RPT
#
# OUTPUT: payments_data.json
# =============================================================================
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core import cdr_io

ENRICHED_PATH = os.path.join(config.OUTPUT_FOLDER, config.ENRICHED_PARQUET)
OUT_PATH      = os.path.join(config.OUTPUT_FOLDER, "payments_data.json")

BUCKETS = config.INTENSITY_BUCKETS   # [(lo, hi, label), ...]
SLOTS   = ("phone1", "phone2", "phone3", "phone4",
           "telefono1", "telefono2", "telefono3", "telefono4")

ACCOUNT_ID_ALIASES = ["account_id", "idUnico", "id_unico", "cuenta"]
CAMPAIGN_ID_ALIASES = ["campaign_id", "idCampania", "id_campania"]
PAYMENT_AMOUNT_ALIASES = ["payment_amount", "abonoTotal", "abono_total", "amount"]


def bucket_label(n):
    for lo, hi, lbl in BUCKETS:
        if lo <= n <= hi:
            return lbl
    return BUCKETS[-1][2]


def week_of(fecha):
    y, m, d = map(int, fecha.split("-"))
    return f"Week {date(y, m, d).isocalendar().week}"


def _find_col(keys, aliases):
    low = {k.lower(): k for k in keys}
    for a in aliases:
        if a.lower() in low:
            return low[a.lower()]
    return None


def _read_csv_rows(path):
    for enc in ("utf-8-sig", "latin1"):
        try:
            with open(path, encoding=enc, newline="") as f:
                yield from csv.DictReader(f)
            return
        except UnicodeDecodeError:
            continue


def load_payments():
    """Returns set of account IDs that paid on each date.
    {date: set(account_id)} — uses RPT payment files only.
    Also returns {date: {account_id: payment_amount}} for amount analysis.
    """
    paid_by_date = defaultdict(set)
    amount_by_date = defaultdict(float)

    def _find_payment_files(folder):
        if not os.path.isdir(folder):
            return []
        paths = glob.glob(os.path.join(folder, "*.csv"))
        paths += glob.glob(os.path.join(folder, "*.xlsx"))
        return paths

    for folder_attr in ("PAYMENTS_RPT_FOLDER",):
        folder = getattr(config, folder_attr, None)
        if not folder:
            continue
        for path in sorted(_find_payment_files(folder)):
            col_cache = None
            for row in _read_csv_rows(path):
                if col_cache is None:
                    keys = list(row.keys())
                    col_cache = {
                        "account": _find_col(keys, ACCOUNT_ID_ALIASES),
                        "amount":  _find_col(keys, PAYMENT_AMOUNT_ALIASES),
                        "date":    _find_col(keys, ["date", "fecha", "payment_date"]),
                    }
                acct = str(row.get(col_cache.get("account") or "", "") or "").strip()
                if not acct:
                    continue
                # Date from filename if not in columns
                fecha = None
                if col_cache.get("date"):
                    raw = str(row.get(col_cache["date"], "") or "")[:10]
                    if len(raw) == 10:
                        fecha = raw
                if not fecha:
                    # Try to extract from filename: DDMMYYYY or YYYYMMDD
                    fn = os.path.basename(path)
                    m = re.search(r"(\d{2})(\d{2})(\d{4})", fn)
                    if m:
                        fecha = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                    else:
                        m = re.search(r"(\d{4})(\d{2})(\d{2})", fn)
                        if m:
                            fecha = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                if fecha:
                    paid_by_date[fecha].add(acct)
                    try:
                        amount_by_date[fecha] += float(
                            str(row.get(col_cache.get("amount") or "", 0) or 0)
                            .replace(",", ""))
                    except (ValueError, TypeError):
                        pass

    total = sum(len(s) for s in paid_by_date.values())
    print(f"  Payments loaded: {total:,} payment records across "
          f"{len(paid_by_date)} dates")
    return paid_by_date, amount_by_date


def load_daily_assignments():
    """Load {date: set(account_id)} from assignment files."""
    from pipeline.step2_enrich import detect_assignment_files, _read_assignment_rows, _find_col, ACCOUNT_ID_ALIASES, CAMPAIGN_ID_ALIASES, PHONE_SLOT_ALIASES
    acct_by_date = defaultdict(set)
    files, _ = detect_assignment_files()
    for fecha, path in files:
        for row in _read_assignment_rows(path):
            keys = list(row.keys())
            acct_col = _find_col(keys, ACCOUNT_ID_ALIASES)
            camp_col = _find_col(keys, CAMPAIGN_ID_ALIASES)
            if not acct_col:
                break
            idc = str(row.get(camp_col, "") or "").strip().split(".")[0]
            try:
                if idc and int(idc) in config.__dict__.get("_EXCLUDED_IDS_CACHE", set()):
                    continue
            except (ValueError, TypeError):
                pass
            acct = str(row.get(acct_col, "") or "").strip()
            if acct:
                acct_by_date[fecha].add(acct)
    return acct_by_date


def _empty_scope():
    return defaultdict(lambda: {"total": 0, "paid": 0})


def main():
    print("Loading payments...")
    paid_by_date, amount_by_date = load_payments()

    print("Loading CDR enriched...")
    # Per-account attempt counts per scope
    total_att   = defaultdict(int)       # account_id → total attempts
    day_att     = defaultdict(int)       # (date, account_id) → attempts
    week_att    = defaultdict(int)       # (week, account_id) → attempts
    tool_att    = defaultdict(int)       # (tool, account_id) → attempts
    slot_att    = defaultdict(int)       # (slot, account_id) → attempts
    slot_day_att= defaultdict(int)       # (date, slot, account_id) → attempts

    accts_seen_total = set()
    accts_seen_day   = defaultdict(set)  # date → accounts
    accts_seen_week  = defaultdict(set)  # week → accounts

    for row in cdr_io.read_parquet_rows(ENRICHED_PATH):
        acct  = row.get("ACCOUNT_ID_FINAL", "")
        fecha = row["DATE"]
        tool  = row["TOOL"]
        slot  = row.get("PHONE_SLOT", "")
        if not acct:
            continue
        week = week_of(fecha)
        total_att[acct] += 1
        day_att[(fecha, acct)] += 1
        week_att[(week, acct)] += 1
        tool_att[(tool, acct)] += 1
        if slot in SLOTS:
            slot_att[(slot, acct)] += 1
            slot_day_att[(fecha, slot, acct)] += 1
        accts_seen_total.add(acct)
        accts_seen_day[fecha].add(acct)
        accts_seen_week[week].add(acct)

    # ---------------------------------------------------------------------------
    # Build payment rate tables
    # ---------------------------------------------------------------------------
    def make_table(accounts_iter, paid_set, attempts_fn, label=""):
        """
        accounts_iter : iterable of account IDs in this scope's universe
        paid_set      : set of account IDs that paid in this scope
        attempts_fn   : callable(account_id) → int attempts in this scope
        """
        buckets = defaultdict(lambda: {"total": 0, "paid": 0})
        for acct in accounts_iter:
            n = attempts_fn(acct)
            lbl = bucket_label(n)
            buckets[lbl]["total"] += 1
            if acct in paid_set:
                buckets[lbl]["paid"] += 1
        out = []
        for lo, hi, lbl in BUCKETS:
            b = buckets[lbl]
            out.append({
                "bucket": lbl,
                "total": b["total"],
                "paid":  b["paid"],
                "payment_rate_pct": round(100 * b["paid"] / b["total"], 2) if b["total"] else 0,
            })
        return out

    print("Computing payment rates...")

    # Total scope
    all_paid = set().union(*paid_by_date.values())
    total_table = make_table(
        accts_seen_total, all_paid, lambda a: total_att.get(a, 0), "total"
    )

    # By date
    by_date_table = {}
    for fecha, accts in sorted(accts_seen_day.items()):
        paid = paid_by_date.get(fecha, set())
        by_date_table[fecha] = make_table(
            accts, paid, lambda a, f=fecha: day_att.get((f, a), 0)
        )

    # By week
    by_week_table = {}
    for week, accts in sorted(accts_seen_week.items()):
        paid_in_week = set().union(
            *[paid_by_date.get(d, set())
              for d in paid_by_date if week_of(d) == week]
        )
        by_week_table[week] = make_table(
            accts, paid_in_week, lambda a, w=week: week_att.get((w, a), 0)
        )

    data = {
        "total":   total_table,
        "by_date": by_date_table,
        "by_week": by_week_table,
        "payment_totals_by_date": {d: round(v, 2) for d, v in amount_by_date.items()},
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"OK → {OUT_PATH}")


if __name__ == "__main__":
    main()
