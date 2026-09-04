# =============================================================================
# step2_enrich.py - Campaign & Assignment Enrichment
#
# Reads cdr_master.parquet (step 1) and enriches every CDR row with:
#   - Campaign name and segment metadata (from daily assignment files)
#   - Account identifier resolved across all phone slots (phone1..phone4)
#   - Phone slot that matched (telefono1..4 or "no_match")
#   - Validation origin (how the campaign was resolved)
#
# OUTPUT FILES:
#   cdr_enriched.parquet         — main analysis (excluded campaigns stripped)
#   cdr_excluded_enriched.csv    — excluded campaign rows (own analysis tab)
#   assignments_stats.json       — coverage stats per date/slot/tool
#   excluded_assignments_stats.json
#
# ASSIGNMENT FILE DETECTION:
#   Any .csv or .xlsx whose filename contains "asig" (case-insensitive) and
#   an 8-digit date (DDMMYYYY). Searched recursively under ASSIGNMENTS_FOLDER.
#
# VALIDATION PRIORITY (per CDR row):
#   1) Today's assignment file  → OK / CORRECTED / RECLASSIFIED / BY_ACCOUNT
#   2) Legacy cross-reference file (optional)  → LEGACY_CROSS
#   3) Last known assignment for this account  → INHERITED_ACCOUNT / _PHONE
#   4) No match  → NO_ASSIGNMENT
#
# EXCLUDED CAMPAIGNS (two-point defense):
#   1) Assignment loading: excluded IDs never enter the lookup tables
#   2) CDR enrichment loop: defense against campaigns reconstructed via
#      fallback/inheritance. Rows are written to cdr_excluded_enriched.csv.
#   Add a new exclusion: add the campaign name to EXCLUDED_CAMPAIGNS in
#   config.py — no other change needed.
# =============================================================================
import csv
import os
import re
import glob
import json
import sys
import time
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core import cdr_io

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
MASTER_PATH   = os.path.join(config.OUTPUT_FOLDER, config.MASTER_PARQUET)
ENRICHED_PATH = os.path.join(config.OUTPUT_FOLDER, config.ENRICHED_PARQUET)
EXCLUDED_PATH = os.path.join(config.OUTPUT_FOLDER, "cdr_excluded_enriched.csv")
STATS_OUT     = os.path.join(config.OUTPUT_FOLDER, "assignments_stats.json")
EXCLUDED_STATS_OUT = os.path.join(config.OUTPUT_FOLDER, "excluded_assignments_stats.json")

# Legacy cross-reference file (optional — leave path non-existent to skip)
LEGACY_CROSS_PATH = os.path.join(config.OUTPUT_FOLDER, "cdr_legacy_cross.csv")

# ---------------------------------------------------------------------------
# CAMPAIGN CONFIG (from config.py)
# ---------------------------------------------------------------------------
CAMPAIGN_CATALOG = config.CAMPAIGN_CATALOG                 # {id: name}
EXCLUDED_CAMPAIGNS = config.EXCLUDED_CAMPAIGNS             # {"NAME", ...}
EXCLUDED_IDS = {
    idc for idc, name in CAMPAIGN_CATALOG.items()
    if name in EXCLUDED_CAMPAIGNS
}

PHONE_SLOTS = ["phone1", "phone2", "phone3", "phone4"]
# Alias map: some assignment files may use different column names
PHONE_SLOT_ALIASES = {
    "phone1": ["phone1", "telefono1", "tel1"],
    "phone2": ["phone2", "telefono2", "tel2"],
    "phone3": ["phone3", "telefono3", "tel3"],
    "phone4": ["phone4", "telefono4", "tel4"],
}
ACCOUNT_ID_ALIASES = ["account_id", "idUnico", "id_unico", "cuenta"]
CAMPAIGN_ID_ALIASES = ["campaign_id", "idCampania", "id_campania"]

# Metadata columns to carry through from the assignment file.
# Adjust to match whatever segmentation columns your assignments contain.
METADATA_ALIASES = {
    "segment": ["segment", "MejorProducto", "mejor_producto", "product"],
    "rating":  ["rating",  "CALIFICACION", "calificacion", "score"],
    "tier":    ["tier",    "PARETTO", "paretto", "pareto"],
}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
_RE_NON_DIGIT = re.compile(r"\D")

def norm_phone(raw):
    if raw is None:
        return None
    s = str(raw).strip().split(".")[0]
    s = _RE_NON_DIGIT.sub("", s)
    if len(s) < 10:
        return None
    return s[-10:]


def week_of(fecha):
    y, m, d = map(int, fecha.split("-"))
    return f"Week {date(y, m, d).isocalendar().week}"


def _find_col(row_keys, aliases):
    """Return the first alias found in row_keys (case-insensitive)."""
    low = {k.lower(): k for k in row_keys}
    for a in aliases:
        if a.lower() in low:
            return low[a.lower()]
    return None


def _read_assignment_rows(path):
    """Yield row dicts from a CSV (utf-8-sig or latin1) or XLSX."""
    if path.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "latin1"):
            try:
                with open(path, encoding=enc, newline="") as f:
                    for row in csv.DictReader(f):
                        yield row
                return
            except UnicodeDecodeError:
                continue
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


def detect_assignment_files():
    """Find all assignment files: filename contains 'asig' + DDMMYYYY date."""
    found, skipped = [], []
    for root, _dirs, files in os.walk(config.ASSIGNMENTS_FOLDER):
        for fn in files:
            path = os.path.join(root, fn)
            low = fn.lower()
            if not (low.endswith(".csv") or low.endswith(".xlsx")) or low.startswith("~$"):
                continue
            m = re.search(r"(\d{2})(\d{2})(\d{4})", fn)
            if "asig" in low and m:
                fecha = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                found.append((fecha, path))
            else:
                skipped.append(fn)
    found.sort()
    return found, skipped


# ---------------------------------------------------------------------------
# LOAD DAILY ASSIGNMENTS
# ---------------------------------------------------------------------------
def load_assignments():
    """Returns:
        tel_lookup  : {date: {phone: (campaign_id, account_id, segment, rating, tier, slot)}}
        acct_lookup : {date: {account_id: (campaign_id, segment, rating, tier)}}
        stats       : aggregated counts for assignments_stats.json
        tel_excl    : {campaign_id: {date: {phone: (...)}}}  — excluded campaigns
        acct_excl   : {campaign_id: {date: {account_id: (...)}}}
        stats_excl  : counts for excluded_assignments_stats.json
    """
    files, skipped = detect_assignment_files()
    print(f"  Assignment folder: {config.ASSIGNMENTS_FOLDER}")
    if skipped:
        print(f"  [INFO] {len(skipped)} file(s) skipped (no 'asig'+date in name)")
    if not files:
        print("  [WARNING] No assignment files found. "
              "Expected filenames like: assignments_20260813.csv (DDMMYYYY)")

    tel_lookup  = {}
    acct_lookup = {}
    # aggregation sets for stats
    camp_day  = defaultdict(set)   # (date, campaign_id) → set of phone ints
    camp_week = defaultdict(set)
    camp_tot  = defaultdict(set)
    slot_total = defaultdict(int)  # (date, slot) → count

    tel_excl   = defaultdict(dict)
    acct_excl  = defaultdict(dict)
    camp_excl_day  = defaultdict(set)
    camp_excl_week = defaultdict(set)
    camp_excl_tot  = defaultdict(set)

    all_dates = []

    for fecha, path in files:
        t0 = time.time()
        day_tel  = {}
        day_acct = {}
        day_tel_excl  = defaultdict(dict)
        day_acct_excl = defaultdict(dict)
        week = week_of(fecha)
        n_rows = n_excl = 0
        col_cache = None  # resolved column names

        for row in _read_assignment_rows(path):
            if col_cache is None:
                keys = list(row.keys())
                col_cache = {
                    "campaign": _find_col(keys, CAMPAIGN_ID_ALIASES),
                    "account":  _find_col(keys, ACCOUNT_ID_ALIASES),
                    "segment":  _find_col(keys, METADATA_ALIASES["segment"]),
                    "rating":   _find_col(keys, METADATA_ALIASES["rating"]),
                    "tier":     _find_col(keys, METADATA_ALIASES["tier"]),
                    "phones":   {slot: _find_col(keys, aliases)
                                 for slot, aliases in PHONE_SLOT_ALIASES.items()},
                }

            n_rows += 1
            idc  = str(row.get(col_cache["campaign"], "") or "").strip().split(".")[0]
            idu  = str(row.get(col_cache["account"], "")  or "").strip()
            seg  = str(row.get(col_cache["segment"], "")  or "").strip().upper() or "NO_DATA"
            rat  = str(row.get(col_cache["rating"], "")   or "").strip().upper() or "NO_DATA"
            tier = str(row.get(col_cache["tier"], "")     or "").strip().upper() or "NO_DATA"

            is_excluded = int(idc) in EXCLUDED_IDS if idc.isdigit() else False

            if is_excluded:
                n_excl += 1
                if idu and idu not in day_acct_excl[idc]:
                    day_acct_excl[idc][idu] = (idc, seg, rat, tier)
                for slot, col in col_cache["phones"].items():
                    if not col:
                        continue
                    ph = norm_phone(row.get(col))
                    if ph is None:
                        continue
                    if ph not in day_tel_excl[idc]:
                        day_tel_excl[idc][ph] = (idc, idu, seg, rat, tier, slot)
                    ph_i = int(ph)
                    camp_excl_day[(fecha, idc)].add(ph_i)
                    camp_excl_week[(week, idc)].add(ph_i)
                    camp_excl_tot[idc].add(ph_i)
                continue

            if idu and idu not in day_acct:
                day_acct[idu] = (idc, seg, rat, tier)
            for slot, col in col_cache["phones"].items():
                if not col:
                    continue
                ph = norm_phone(row.get(col))
                if ph is None:
                    continue
                if ph not in day_tel:
                    day_tel[ph] = (idc, idu, seg, rat, tier, slot)
                    slot_total[(fecha, slot)] += 1
                ph_i = int(ph)
                camp_day[(fecha, idc)].add(ph_i)
                camp_week[(week, idc)].add(ph_i)
                camp_tot[idc].add(ph_i)

        tel_lookup[fecha]  = day_tel
        acct_lookup[fecha] = day_acct
        for idc, d in day_tel_excl.items():
            tel_excl[idc][fecha] = d
        for idc, d in day_acct_excl.items():
            acct_excl[idc][fecha] = d
        all_dates.append(fecha)

        excl_txt = f", {n_excl:,} excluded" if n_excl else ""
        print(f"  Assignment {fecha}: {n_rows:,} rows, "
              f"{len(day_tel):,} phones, {len(day_acct):,} accounts"
              f"{excl_txt} [{os.path.basename(path)}] ({time.time()-t0:.0f}s)")

    stats = {
        "dates_with_assignment": sorted(all_dates),
        "campaign_day":  {f"{d}|{i}": len(s) for (d, i), s in camp_day.items()},
        "campaign_week": {f"{w}|{i}": len(s) for (w, i), s in camp_week.items()},
        "campaign_total":{i: len(s) for i, s in camp_tot.items()},
        "slot_total":    {f"{d}|{sl}": n for (d, sl), n in slot_total.items()},
    }
    stats_excl = {
        "campaign_day":  {f"{d}|{i}": len(s) for (d, i), s in camp_excl_day.items()},
        "campaign_week": {f"{w}|{i}": len(s) for (w, i), s in camp_excl_week.items()},
        "campaign_total":{i: len(s) for i, s in camp_excl_tot.items()},
    }
    return tel_lookup, acct_lookup, stats, tel_excl, acct_excl, stats_excl


# ---------------------------------------------------------------------------
# LOAD LEGACY CROSS-REFERENCE (optional)
# ---------------------------------------------------------------------------
def load_legacy_cross():
    tel_date, acct_date, inherit_acct, inherit_tel = {}, {}, {}, {}
    if not os.path.exists(LEGACY_CROSS_PATH):
        print("  [INFO] No legacy cross-reference file found — skipping.")
        return tel_date, acct_date, inherit_acct, inherit_tel
    with open(LEGACY_CROSS_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("VALIDATION_ORIGIN") == "NO_ASSIGNMENT":
                continue
            fecha = row["DATE"]
            info  = (row["CAMPAIGN_ID_FINAL"], row.get("SEGMENT", ""),
                     row.get("RATING", ""), row.get("TIER", ""),
                     row["ACCOUNT_ID_FINAL"])
            tel  = row.get("PHONE", "")
            acct = row.get("ACCOUNT_ID_FINAL", "")
            if tel:
                tel_date[(tel, fecha)] = info
                if tel not in inherit_tel or fecha >= inherit_tel[tel][0]:
                    inherit_tel[tel] = (fecha, info)
            if acct:
                acct_date[(acct, fecha)] = info
                if acct not in inherit_acct or fecha >= inherit_acct[acct][0]:
                    inherit_acct[acct] = (fecha, info)
    print(f"  Legacy cross: {len(tel_date):,} phone-date pairs, "
          f"{len(inherit_acct):,} accounts for inheritance")
    return tel_date, acct_date, inherit_acct, inherit_tel


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(config.OUTPUT_FOLDER, exist_ok=True)
    print("Loading daily assignments...")
    tel_lkp, acct_lkp, stats_asig, tel_excl, acct_excl, stats_excl = load_assignments()
    print("Loading legacy cross-reference...")
    tel_date, acct_date, inherit_acct, inherit_tel = load_legacy_cross()

    # Accumulators for coverage stats
    cov_attempted  = defaultdict(set)          # (date, slot) → phone ints with ≥1 attempt
    cov_tool_int   = defaultdict(int)          # (date, slot, tool) → attempts
    cov_tool_phones = defaultdict(set)         # (date, slot, tool) → phones
    cov_tool_cat   = defaultdict(int)          # (date, slot, tool, category) → count
    out_of_asig_phones = defaultdict(set)      # date → phones outside any assignment
    out_of_asig_int    = defaultdict(int)      # (date, tool) → attempts outside
    n_excluded_cdr = 0

    validation_stats = defaultdict(int)
    t0 = time.time()

    # Build output schema: master columns + enrichment columns
    master_cols = cdr_io.get_parquet_columns(MASTER_PATH)
    enrich_cols = [
        "CAMPAIGN_ID_FINAL", "CAMPAIGN_NAME", "SEGMENT",
        "RATING", "TIER", "ACCOUNT_ID_FINAL", "PHONE_SLOT",
        "VALIDATION_ORIGIN",
    ]
    fieldnames = list(master_cols) + enrich_cols

    with cdr_io.ParquetDictWriter(ENRICHED_PATH, fieldnames, mode="w") as w, \
         open(EXCLUDED_PATH, "w", newline="", encoding="utf-8-sig") as fout_exc:

        w_exc = csv.DictWriter(fout_exc, fieldnames=fieldnames)
        w_exc.writeheader()

        for row in cdr_io.read_parquet_rows(MASTER_PATH):
            fecha  = row["DATE"]
            tel    = row["PHONE"]
            acct   = row.get("ACCOUNT_ID", "")
            tool   = row["TOOL"]
            cat    = row["CALL_CATEGORY"]
            cid_raw = row.get("CAMPAIGN_ID_CDR", "")

            idc = seg = rat = tier = idu = slot = None
            origin = None

            # --- 1) Try today's assignment ---
            day = tel_lkp.get(fecha)
            if day is not None:
                match = day.get(tel) if tel else None
                if match:
                    idc, idu, seg, rat, tier, slot = match
                    origin = ("OK" if cid_raw == str(idc)
                              else ("CORRECTED" if cid_raw in ("", "1")
                                    else "RECLASSIFIED"))
                    if tel:
                        tel_i = int(tel)
                        cov_attempted[(fecha, slot)].add(tel_i)
                        cov_tool_int[(fecha, slot, tool)] += 1
                        cov_tool_phones[(fecha, slot, tool)].add(tel_i)
                        cov_tool_cat[(fecha, slot, tool, cat)] += 1
                elif acct and acct in acct_lkp.get(fecha, {}):
                    idc, seg, rat, tier = acct_lkp[fecha][acct]
                    idu, slot, origin = acct, "no_slot", "BY_ACCOUNT"
                else:
                    # Check excluded campaigns before giving up
                    match_excl = None
                    if tel:
                        for by_date in tel_excl.values():
                            match_excl = (by_date.get(fecha) or {}).get(tel)
                            if match_excl:
                                break
                    if match_excl:
                        idc, idu, seg, rat, tier, slot = match_excl
                        origin = "OK"
                    elif acct:
                        for by_date in acct_excl.values():
                            m = (by_date.get(fecha) or {}).get(acct)
                            if m:
                                idc, seg, rat, tier = m
                                idu, slot, origin = acct, "no_slot", "BY_ACCOUNT"
                                break
                    if idc is None:
                        origin = "NO_ASSIGNMENT"
                        if tel:
                            out_of_asig_phones[fecha].add(int(tel))
                        out_of_asig_int[(fecha, tool)] += 1

            # --- 2) Legacy cross-reference ---
            if idc is None:
                info = tel_date.get((tel, fecha)) if tel else None
                if info is None and acct:
                    info = acct_date.get((acct, fecha))
                if info:
                    idc, seg, rat, tier, idu = info
                    slot = "no_slot"
                    origin = "LEGACY_CROSS"

            # --- 3) Inherit from last known assignment ---
            if idc is None:
                h = inherit_acct.get(acct) if acct else None
                if h:
                    idc, seg, rat, tier, idu = h[1]
                    slot = "no_slot"
                    origin = ("INHERITED_ACCOUNT"
                              if origin != "NO_ASSIGNMENT" else "NO_ASSIGNMENT")
                elif tel and tel in inherit_tel:
                    idc, seg, rat, tier, idu = inherit_tel[tel][1]
                    slot = "no_slot"
                    origin = ("INHERITED_PHONE"
                              if origin != "NO_ASSIGNMENT" else "NO_ASSIGNMENT")
                else:
                    idc = cid_raw or ""
                    seg = rat = tier = "NO_DATA"
                    idu = acct or ""
                    slot = ""
                    origin = origin or "NO_ASSIGNMENT"

            # Campaign name lookup
            try:
                camp_name = CAMPAIGN_CATALOG.get(int(idc), "UNCATALOGUED") if idc else "NO_DATA"
            except (ValueError, TypeError):
                camp_name = "UNCATALOGUED"

            # --- Excluded campaign? Divert to excluded file ---
            is_excl = int(idc) in EXCLUDED_IDS if (idc and str(idc).isdigit()) else False
            if is_excl:
                n_excluded_cdr += 1
                row.update({
                    "CAMPAIGN_ID_FINAL": idc or "",
                    "CAMPAIGN_NAME":     camp_name,
                    "SEGMENT":           seg or "NO_DATA",
                    "RATING":            rat or "NO_DATA",
                    "TIER":              tier or "NO_DATA",
                    "ACCOUNT_ID_FINAL":  idu or "",
                    "PHONE_SLOT":        slot or "NO_DATA",
                    "VALIDATION_ORIGIN": origin,
                })
                w_exc.writerow(row)
                continue

            validation_stats[origin] += 1
            row.update({
                "CAMPAIGN_ID_FINAL": idc or "",
                "CAMPAIGN_NAME":     camp_name,
                "SEGMENT":           seg or "NO_DATA",
                "RATING":            rat or "NO_DATA",
                "TIER":              tier or "NO_DATA",
                "ACCOUNT_ID_FINAL":  idu or "",
                "PHONE_SLOT":        slot or "NO_DATA",
                "VALIDATION_ORIGIN": origin,
            })
            w.writerow(row)

    # ---------------------------------------------------------------------------
    # Build and save assignments_stats.json (coverage per slot/date/tool)
    # ---------------------------------------------------------------------------
    tools_seen = sorted({t for (_, _, t) in cov_tool_int})
    coverage = {}
    for fecha in stats_asig["dates_with_assignment"]:
        slots_out = {}
        for slot in PHONE_SLOTS:
            total = stats_asig["slot_total"].get(f"{fecha}|{slot}", 0)
            if total == 0:
                continue
            attempted = len(cov_attempted.get((fecha, slot), ()))
            tool_out = {}
            for tool in tools_seen:
                n_int = cov_tool_int.get((fecha, slot, tool), 0)
                if n_int == 0:
                    continue
                cats = {c: n for (d, s, t, c), n in cov_tool_cat.items()
                        if d == fecha and s == slot and t == tool}
                tool_out[tool] = {
                    "attempts": n_int,
                    "phones_reached": len(cov_tool_phones.get((fecha, slot, tool), ())),
                    "coverage_pct": round(100 * len(cov_tool_phones.get(
                        (fecha, slot, tool), ())) / total, 2),
                    "categories": cats,
                }
            slots_out[slot] = {
                "total_assigned": total,
                "attempted": attempted,
                "attempted_pct": round(100 * attempted / total, 2),
                "not_attempted": total - attempted,
                "not_attempted_pct": round(100 * (total - attempted) / total, 2),
                "by_tool": tool_out,
            }
        fi = {t: n for (d, t), n in out_of_asig_int.items() if d == fecha}
        coverage[fecha] = {
            "slots": slots_out,
            "outside_assignment": {
                "unique_phones": len(out_of_asig_phones.get(fecha, ())),
                "total_attempts": sum(fi.values()),
                "by_tool": fi,
            },
        }

    out_stats = {
        "dates_with_assignment": stats_asig["dates_with_assignment"],
        "phones_assigned_by_campaign": {
            "total":   stats_asig["campaign_total"],
            "by_date": stats_asig["campaign_day"],
            "by_week": stats_asig["campaign_week"],
        },
        "coverage": coverage,
    }
    with open(STATS_OUT, "w", encoding="utf-8") as f:
        json.dump(out_stats, f, ensure_ascii=False)

    out_stats_excl = {
        "dates_with_assignment": stats_asig["dates_with_assignment"],
        "excluded_campaigns": sorted(EXCLUDED_CAMPAIGNS),
        "phones_assigned_by_campaign": {
            "total":   stats_excl["campaign_total"],
            "by_date": stats_excl["campaign_day"],
            "by_week": stats_excl["campaign_week"],
        },
    }
    with open(EXCLUDED_STATS_OUT, "w", encoding="utf-8") as f:
        json.dump(out_stats_excl, f, ensure_ascii=False)

    total = sum(validation_stats.values())
    elapsed = time.time() - t0
    print(f"\nValidation summary ({total:,} rows → {ENRICHED_PATH}, {elapsed:.0f}s):")
    for k, v in sorted(validation_stats.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v:,} ({100*v/total:.1f}%)" if total else f"  {k}: {v:,}")
    if n_excluded_cdr:
        print(f"  [EXCLUDED] {n_excluded_cdr:,} CDR rows for excluded campaigns "
              f"→ {EXCLUDED_PATH}")
    print(f"OK → {ENRICHED_PATH}")
    print(f"OK → {STATS_OUT}")
    print(f"OK → {EXCLUDED_PATH} ({n_excluded_cdr:,} rows)")
    print(f"OK → {EXCLUDED_STATS_OUT}")
