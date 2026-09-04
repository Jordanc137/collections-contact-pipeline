# =============================================================================
# step8_best_hour.py - Optimal Contact Hour (Wilson Score CI)
#
# Question answered: "Of accounts contacted at hour H on a given day/week,
# what percentage paid?" Runs for all 24 hours and identifies the hour with
# the best payment rate, requiring a minimum sample to avoid noise.
#
# IMPORTANT — what this does NOT measure:
# Payment files in this pipeline typically contain only a date, not an hour.
# So this step measures the CONTACT hour, not the payment hour. The question
# is: "At what hour should we dial to maximize same-day/week payments?"
# That is the operationally actionable question — we can control when we dial,
# not when someone pays.
#
# STATISTICAL METHOD:
# Wilson score confidence interval (95% by default). More reliable than the
# normal approximation for small samples and always stays in [0%, 100%].
# An hour is only declared "optimal" if its sample >= MIN_SAMPLE_FOR_OPTIMAL_HOUR
# (configurable in config.py). If no hour reaches that threshold, no optimal
# hour is declared — better silence than noise.
#
# OUTPUT: best_contact_hour.json
# =============================================================================
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core import cdr_io

ENRICHED_PATH = os.path.join(config.OUTPUT_FOLDER, config.ENRICHED_PARQUET)
DASHBOARD_PATH = os.path.join(config.OUTPUT_FOLDER, config.DASHBOARD_DATA)
OUT_PATH      = os.path.join(config.OUTPUT_FOLDER, "best_contact_hour.json")

MIN_SAMPLE = config.MIN_SAMPLE_FOR_OPTIMAL_HOUR
CONFIDENCE = config.CONFIDENCE_LEVEL
BUCKETS    = config.INTENSITY_BUCKETS

# Payment loading — reuse logic from step4 (same definition of "paid")
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))


def _z_score(confidence):
    """Approximate z-score for common confidence levels."""
    table = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
    return table.get(confidence, 1.96)


def wilson_ci(successes, n, confidence=0.95):
    """Wilson score confidence interval. Returns (lower, center, upper) in [0,1]."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    z = _z_score(confidence)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), center, min(1.0, center + margin))


def week_of(fecha):
    y, m, d = map(int, fecha.split("-"))
    return f"Week {date(y, m, d).isocalendar().week}"


def load_paid_accounts():
    """Returns {date: set(account_id)} using RPT payments. Same source as step4."""
    import csv
    import glob
    import re

    PAYMENT_AMOUNT_ALIASES = ["payment_amount", "abonoTotal", "abono_total", "amount"]
    ACCOUNT_ID_ALIASES = ["account_id", "idUnico", "id_unico", "cuenta"]

    def _find_col(keys, aliases):
        low = {k.lower(): k for k in keys}
        for a in aliases:
            if a.lower() in low:
                return low[a.lower()]
        return None

    paid = defaultdict(set)
    folder = getattr(config, "PAYMENTS_RPT_FOLDER", None)
    if not folder or not os.path.isdir(folder):
        print("  [INFO] No payment folder configured — best_hour will have no payment data")
        return paid

    for path in sorted(glob.glob(os.path.join(folder, "*.csv"))):
        col_cache = None
        for enc in ("utf-8-sig", "latin1"):
            try:
                with open(path, encoding=enc, newline="") as f:
                    for row in csv.DictReader(f):
                        if col_cache is None:
                            keys = list(row.keys())
                            col_cache = {
                                "account": _find_col(keys, ACCOUNT_ID_ALIASES),
                                "date":    _find_col(keys, ["date", "fecha", "payment_date"]),
                            }
                        acct = str(row.get(col_cache.get("account") or "", "") or "").strip()
                        if not acct:
                            continue
                        fecha = None
                        if col_cache.get("date"):
                            raw = str(row.get(col_cache["date"], "") or "")[:10]
                            if len(raw) == 10:
                                fecha = raw
                        if not fecha:
                            fn = os.path.basename(path)
                            m = re.search(r"(\d{2})(\d{2})(\d{4})", fn)
                            if m:
                                fecha = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                        if fecha:
                            paid[fecha].add(acct)
                break
            except UnicodeDecodeError:
                continue
    return paid


def main():
    print("Loading payment data...")
    paid_by_date = load_paid_accounts()

    paid_by_week = defaultdict(set)
    for fecha, accts in paid_by_date.items():
        paid_by_week[week_of(fecha)].update(accts)

    # hour_day_accts[scope_key][hour] = set of account_ids contacted at that hour
    hour_day_accts = defaultdict(lambda: defaultdict(set))   # date → hour → accounts
    hour_week_accts = defaultdict(lambda: defaultdict(set))  # week → hour → accounts
    hour_camp_day   = defaultdict(lambda: defaultdict(set))  # (date, campaign) → hour → accounts
    hour_camp_week  = defaultdict(lambda: defaultdict(set))  # (week, campaign) → hour → accounts

    print("Reading CDR enriched...")
    for row in cdr_io.read_parquet_rows(ENRICHED_PATH):
        acct  = row.get("ACCOUNT_ID_FINAL", "")
        fecha = row["DATE"]
        hour  = row.get("HOUR", "")
        camp  = row.get("CAMPAIGN_ID_FINAL", "")
        if not acct or not hour or len(hour) < 2:
            continue
        try:
            h = int(hour[:2])
        except ValueError:
            continue
        week = week_of(fecha)
        hour_day_accts[fecha][h].add(acct)
        hour_week_accts[week][h].add(acct)
        if camp:
            hour_camp_day[(fecha, camp)][h].add(acct)
            hour_camp_week[(week, camp)][h].add(acct)

    def analyze_hours(hour_accts_dict, paid_set, scope_key):
        """For each hour, compute payment rate + Wilson CI."""
        results = []
        optimal_hour = None
        optimal_rate = -1.0

        for h in range(24):
            accts = hour_accts_dict.get(h, set())
            n = len(accts)
            paid_n = sum(1 for a in accts if a in paid_set)
            rate = paid_n / n if n > 0 else 0.0
            lo, center, hi = wilson_ci(paid_n, n, CONFIDENCE)
            results.append({
                "hour": h,
                "hour_label": f"{h:02d}:00",
                "accounts_contacted": n,
                "accounts_paid": paid_n,
                "payment_rate_pct": round(rate * 100, 2),
                "ci_lower_pct":  round(lo * 100, 2),
                "ci_upper_pct":  round(hi * 100, 2),
            })
            if n >= MIN_SAMPLE and rate > optimal_rate:
                optimal_rate = rate
                optimal_hour = h

        return {
            "scope": scope_key,
            "hours": results,
            "optimal_hour": optimal_hour,
            "optimal_hour_label": f"{optimal_hour:02d}:00" if optimal_hour is not None else None,
            "optimal_payment_rate_pct": round(optimal_rate * 100, 2) if optimal_hour is not None else None,
            "min_sample_used": MIN_SAMPLE,
        }

    print("Computing optimal hours...")
    output = {"by_day": [], "by_week": [], "by_campaign_day": [], "by_campaign_week": []}

    for fecha, h_dict in sorted(hour_day_accts.items()):
        paid = paid_by_date.get(fecha, set())
        rec  = analyze_hours(h_dict, paid, fecha)
        rec["date"] = fecha
        output["by_day"].append(rec)

    for week, h_dict in sorted(hour_week_accts.items()):
        paid = paid_by_week.get(week, set())
        rec  = analyze_hours(h_dict, paid, week)
        rec["week"] = week
        output["by_week"].append(rec)

    for (fecha, camp), h_dict in sorted(hour_camp_day.items()):
        paid = paid_by_date.get(fecha, set())
        rec  = analyze_hours(h_dict, paid, f"{fecha}|{camp}")
        rec["date"] = fecha
        rec["campaign_id"] = camp
        try:
            rec["campaign_name"] = config.CAMPAIGN_CATALOG.get(int(camp), camp)
        except (ValueError, TypeError):
            rec["campaign_name"] = camp
        output["by_campaign_day"].append(rec)

    for (week, camp), h_dict in sorted(hour_camp_week.items()):
        paid = paid_by_week.get(week, set())
        rec  = analyze_hours(h_dict, paid, f"{week}|{camp}")
        rec["week"] = week
        rec["campaign_id"] = camp
        try:
            rec["campaign_name"] = config.CAMPAIGN_CATALOG.get(int(camp), camp)
        except (ValueError, TypeError):
            rec["campaign_name"] = camp
        output["by_campaign_week"].append(rec)

    output["config"] = {
        "min_sample_for_optimal_hour": MIN_SAMPLE,
        "confidence_level": CONFIDENCE,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)
    print(f"OK → {OUT_PATH}")
    print(f"  Days analyzed: {len(output['by_day'])}")
    print(f"  Weeks analyzed: {len(output['by_week'])}")
    print(f"  Campaign-day combos: {len(output['by_campaign_day'])}")


if __name__ == "__main__":
    main()
