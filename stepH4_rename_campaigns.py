# =============================================================================
# stepH4_rename_campaigns.py - Campaign Catalog Normalization
#
# Applies the campaign catalog (ID → name) to prioritization_today.csv and
# removes excluded campaigns from the optimization output.
#
# This is a simple pass-through that:
#   1. Reads prioritization_today.csv (output of stepH3)
#   2. Joins campaign_id → campaign_name from config.CAMPAIGN_CATALOG
#   3. Removes rows whose campaign_name is in config.EXCLUDED_CAMPAIGNS
#   4. Overwrites prioritization_today.csv with the cleaned result
#
# Idempotent: if a "campaign_name" column already exists (previous run),
# it is replaced cleanly — no _x/_y suffix collision.
# =============================================================================
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

IN_OUT = os.path.join(config.OPT_OUTPUT_FOLDER, "prioritization_today.csv")
EXCEL_LIMIT = 1_048_576

CATALOG   = config.CAMPAIGN_CATALOG         # {int_id: "NAME"}
EXCLUDED  = config.EXCLUDED_CAMPAIGNS       # {"NAME", ...}
EXCLUDED_IDS = {idc for idc, name in CATALOG.items() if name in EXCLUDED}

CAMP_ID_ALIASES = ["campaign_id", "idCampania", "id_campania"]


def find_col(keys, aliases):
    low = {k.lower(): k for k in keys}
    for a in aliases:
        if a.lower() in low:
            return low[a.lower()]
    return None


def main():
    print("=" * 70)
    print("OPTIMIZATION - STEP H4: Campaign Catalog Normalization")
    print("=" * 70)

    if not os.path.exists(IN_OUT):
        print(f"[ERROR] {IN_OUT} not found — run stepH3 first.")
        raise SystemExit(1)

    rows = []
    for enc in ("utf-8-sig", "latin1"):
        try:
            with open(IN_OUT, encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue

    if not rows:
        print("[ERROR] File is empty or unreadable")
        raise SystemExit(1)

    keys = list(rows[0].keys())
    camp_id_col = find_col(keys, CAMP_ID_ALIASES)

    # Drop stale campaign_name if present
    if "campaign_name" in keys:
        print(f"  [INFO] 'campaign_name' column already present — replacing")
        keys.remove("campaign_name")
        rows = [{k: v for k, v in r.items() if k != "campaign_name"} for r in rows]

    n_before = len(rows)
    n_excluded = 0
    out_rows = []

    for row in rows:
        idc_raw = str(row.get(camp_id_col, "") or "").strip().split(".")[0] if camp_id_col else ""
        try:
            idc = int(idc_raw)
        except (ValueError, TypeError):
            idc = None

        name = CATALOG.get(idc, "UNCATALOGUED") if idc is not None else "NO_CAMPAIGN_ID"

        if name in EXCLUDED or idc in EXCLUDED_IDS:
            n_excluded += 1
            continue

        row["campaign_name"] = name
        out_rows.append(row)

    print(f"  Before: {n_before:,} | Excluded: {n_excluded:,} | After: {len(out_rows):,}")
    if n_excluded:
        print(f"  Excluded campaigns: {sorted(EXCLUDED)}")

    if len(out_rows) >= EXCEL_LIMIT:
        print(f"  [WARNING] {len(out_rows):,} rows approaches Excel limit ({EXCEL_LIMIT:,})")

    if not out_rows:
        print("[ERROR] All rows were excluded — check EXCLUDED_CAMPAIGNS in config.py")
        raise SystemExit(1)

    # Campaign name distribution
    from collections import Counter
    dist = Counter(r.get("campaign_name", "") for r in out_rows)
    print("\nCampaign distribution:")
    for name, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  {name:<30} {cnt:>8,}")

    fieldnames = list(dict.fromkeys(list(out_rows[0].keys())))
    with open(IN_OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    print(f"\nOK → {IN_OUT} ({len(out_rows):,} rows)")


if __name__ == "__main__":
    main()
