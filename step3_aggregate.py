# =============================================================================
# step3_aggregate.py - KPI Aggregation for Dashboard
#
# Reads cdr_enriched.parquet and produces dashboard_data.json with:
#   - Global KPIs and per-tool KPIs (attempts, answer rate, RPC rate)
#   - Flat matrix: week × day × tool × campaign (for dashboard filters)
#   - Contact intensity distribution by phone number (buckets)
#   - Answerable-number effectiveness per scope (total / week / day)
#   - Phone slot coverage (from assignments_stats.json)
#   - STATUS breakdown per tool
#
# ANSWERED vs ANSWERED_SHORT:
#   "Answered (short)" calls are NOT counted as useful contact in any
#   effectiveness metric. answer_rate = answered_only / attempts always.
#   This is intentional: a very short pickup (likely voicemail) is not
#   a productive contact even though the line technically connected.
# =============================================================================
import json
import os
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core import cdr_io

ENRICHED_PATH = os.path.join(config.OUTPUT_FOLDER, config.ENRICHED_PARQUET)
STATS_PATH    = os.path.join(config.OUTPUT_FOLDER, "assignments_stats.json")
OUT_PATH      = os.path.join(config.OUTPUT_FOLDER, config.DASHBOARD_DATA)

# Groups: campaign IDs that should appear merged under one label in the dashboard.
# Populate from config if your operation groups campaigns this way.
CAMPAIGN_GROUPS = getattr(config, "CAMPAIGN_GROUPS", {})  # {id_str: group_label}

TOOLS = [t for t in ["Blaster", "Predictivo", "IVR", "IA", "WhatsApp", "SMS",
                      "Predictive", "Robocall"]
         if True]  # all tools that may appear in CDR

SLOTS_ALL = ("phone1", "phone2", "phone3", "phone4",
             "telefono1", "telefono2", "telefono3", "telefono4", "no_match")

BUCKETS = [(1, 1, "1"), (2, 3, "2-3"), (4, 6, "4-6"), (7, 10, "7-10"), (11, 9999, "11+")]


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def bucket_label(n):
    for lo, hi, lbl in BUCKETS:
        if lo <= n <= hi:
            return lbl
    return "11+"


def day_meta(fecha):
    y, m, d = map(int, fecha.split("-"))
    dt = date(y, m, d)
    iso = dt.isocalendar()
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    month_names   = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return {
        "date":  fecha,
        "label": f"{weekday_names[dt.weekday()]} {d:02d}-{month_names[dt.month-1]}",
        "week":  f"Week {iso.week}",
    }


def new_bucket():
    return {"attempts": 0, "answered": 0, "answered_short": 0,
            "ringing": 0, "discard": 0, "rpc": 0, "messages": 0,
            "with_duration": 0}


def accumulate(b, cat, is_rpc, is_msg, has_duration=False):
    b["attempts"] += 1
    if cat in ("Answered", "Contestada"):
        b["answered"] += 1
    elif cat in ("Answered (short)", "Contestada corta"):
        b["answered_short"] += 1
    elif cat in ("Ringing", "Tono"):
        b["ringing"] += 1
    elif cat in ("Discard", "Descartar"):
        b["discard"] += 1
    if is_rpc:
        b["rpc"] += 1
    if is_msg:
        b["messages"] += 1
    if has_duration:
        b["with_duration"] += 1


def finalize(b):
    out = dict(b)
    n = b["attempts"]
    out["answer_rate_pct"]     = round(100 * b["answered"] / n, 2) if n else 0
    out["rpc_pct_attempts"]    = round(100 * b["rpc"] / n, 2) if n else 0
    out["rpc_pct_answered"]    = round(100 * b["rpc"] / b["answered"], 2) if b["answered"] else 0
    out["message_rate_pct"]    = round(100 * b["messages"] / n, 2) if n else 0
    out["with_duration_pct"]   = round(100 * b["with_duration"] / n, 2) if n else 0
    return out


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    agg = defaultdict(new_bucket)          # (date, tool, campaign_id)
    phone_total  = defaultdict(int)        # (phone, campaign_id) → attempts
    phone_day    = defaultdict(int)        # (date, phone, campaign_id) → attempts
    phone_week   = defaultdict(int)        # (week, phone, campaign_id) → attempts
    phone_answered       = defaultdict(int)     # (phone, campaign_id) → answered count
    phone_day_answered   = defaultdict(set)     # (date, campaign_id) → phones answered
    phone_week_answered  = defaultdict(set)     # (week, campaign_id) → phones answered
    phone_slot    = defaultdict(set)       # (campaign_id, slot) → phones
    phone_slot_day= defaultdict(set)       # (date, campaign_id, slot) → phones
    dates = set()
    status_tool = defaultdict(int)         # (tool, status) → count

    for row in cdr_io.read_parquet_rows(ENRICHED_PATH):
        fecha = row["DATE"]
        tool  = row["TOOL"]
        dates.add(fecha)

        idc = CAMPAIGN_GROUPS.get(row["CAMPAIGN_ID_FINAL"], row["CAMPAIGN_ID_FINAL"])
        cat      = row["CALL_CATEGORY"]
        is_rpc   = row.get("IS_RPC", "0") == "1"
        is_msg   = row.get("IS_MESSAGE", "0") == "1"
        has_dur  = row.get("HAS_DURATION", "0") == "1"

        accumulate(agg[(fecha, tool, idc)], cat, is_rpc, is_msg, has_dur)
        status_tool[(tool, row.get("STATUS", ""))] += 1

        phone = row.get("PHONE", "")
        if phone:
            week = day_meta(fecha)["week"]
            phone_total[(phone, idc)] += 1
            phone_day[(fecha, phone, idc)] += 1
            phone_week[(week, phone, idc)] += 1
            if cat in ("Answered", "Contestada"):
                phone_answered[(phone, idc)] += 1
                phone_day_answered[(fecha, idc)].add(phone)
                phone_week_answered[(week, idc)].add(phone)
            slot = row.get("PHONE_SLOT", "no_match")
            if slot not in SLOTS_ALL:
                slot = "no_match"
            phone_slot[(idc, slot)].add(phone)
            phone_slot_day[(fecha, idc, slot)].add(phone)

    days   = [day_meta(f) for f in sorted(dates)]
    weeks  = sorted({d["week"] for d in days}, key=lambda s: int(s.split()[1]))

    # Flat matrix for dashboard filters
    matrix = []
    camp_names = {}
    for idc in {idc for (_, _, idc) in agg}:
        try:
            camp_names[idc] = config.CAMPAIGN_CATALOG.get(int(idc), idc)
        except (ValueError, TypeError):
            camp_names[idc] = idc

    for (fecha, tool, idc), b in sorted(agg.items()):
        meta = day_meta(fecha)
        matrix.append({
            "date": fecha, "day": meta["label"], "week": meta["week"],
            "tool": tool, "campaign_id": idc,
            "campaign_name": camp_names.get(idc, idc),
            **finalize(b),
        })

    # Global and per-tool KPIs
    total = new_bucket()
    by_tool = defaultdict(new_bucket)
    for (fecha, tool, idc), b in agg.items():
        for k in total:
            total[k] += b[k]
            by_tool[tool][k] += b[k]

    # Intensity by phone number (full period) per campaign
    intensity = defaultdict(lambda: defaultdict(int))
    unique_phones = defaultdict(set)
    total_attempts = defaultdict(int)
    answered_phones = defaultdict(int)

    for (phone, idc), n in phone_total.items():
        intensity[idc][bucket_label(n)] += 1
        unique_phones[idc].add(phone)
        total_attempts[idc] += n
        if phone_answered.get((phone, idc), 0) > 0:
            answered_phones[idc] += 1

    # Load assignment stats
    asig = None
    if os.path.exists(STATS_PATH):
        with open(STATS_PATH, encoding="utf-8") as f:
            asig = json.load(f)

    def assigned_count(scope, key, idc):
        if asig is None:
            return None
        src = asig["phones_assigned_by_campaign"].get(scope, {})
        if scope == "total":
            return src.get(str(idc))
        return src.get(f"{key}|{idc}")

    intensity_out = []
    for idc, dist in intensity.items():
        nu = len(unique_phones[idc])
        intensity_out.append({
            "id":   idc,
            "name": camp_names.get(idc, idc),
            "unique_phones_attempted": nu,
            "unique_phones_assigned":  assigned_count("total", None, idc),
            "avg_attempts_per_phone":  round(total_attempts[idc] / nu, 2) if nu else 0,
            "phones_answered":         answered_phones[idc],
            "answer_rate_pct":         round(100 * answered_phones[idc] / nu, 2) if nu else 0,
            "distribution": {lbl: dist.get(lbl, 0) for _, _, lbl in BUCKETS},
            "by_slot": {sl: len(phone_slot.get((idc, sl), ())) for sl in SLOTS_ALL},
        })
    intensity_out.sort(key=lambda x: -x["unique_phones_attempted"])

    # Effectiveness by phone per scope (day/week)
    eff_scopes = []
    day_phones = defaultdict(set); day_attempts_sum = defaultdict(int)
    for (fecha, phone, idc), n in phone_day.items():
        day_phones[(fecha, idc)].add(phone)
        day_attempts_sum[(fecha, idc)] += n
    for (fecha, idc), phones in day_phones.items():
        nu = len(phones); ans = len(phone_day_answered.get((fecha, idc), ()))
        eff_scopes.append({
            "scope": "day", "key": fecha, "campaign_id": idc,
            "campaign_name": camp_names.get(idc, idc),
            "assigned": assigned_count("by_date", fecha, idc),
            "attempted": nu, "answered": ans,
            "answer_rate_pct": round(100 * ans / nu, 2) if nu else 0,
            "avg_attempts": round(day_attempts_sum[(fecha, idc)] / nu, 2) if nu else 0,
        })
    week_phones = defaultdict(set); week_attempts_sum = defaultdict(int)
    for (week, phone, idc), n in phone_week.items():
        week_phones[(week, idc)].add(phone)
        week_attempts_sum[(week, idc)] += n
    for (week, idc), phones in week_phones.items():
        nu = len(phones); ans = len(phone_week_answered.get((week, idc), ()))
        eff_scopes.append({
            "scope": "week", "key": week, "campaign_id": idc,
            "campaign_name": camp_names.get(idc, idc),
            "assigned": assigned_count("by_week", week, idc),
            "attempted": nu, "answered": ans,
            "answer_rate_pct": round(100 * ans / nu, 2) if nu else 0,
            "avg_attempts": round(week_attempts_sum[(week, idc)] / nu, 2) if nu else 0,
        })

    # Coverage (from assignment stats)
    coverage_out = None
    if asig and asig.get("coverage"):
        coverage_out = {}
        for fecha, obj in asig["coverage"].items():
            meta = day_meta(fecha)
            obj = dict(obj)
            obj["day"]  = meta["label"]
            obj["week"] = meta["week"]
            coverage_out[fecha] = obj

    # Intensity by phone per day
    int_day = defaultdict(lambda: defaultdict(int))
    for (fecha, phone, idc), n in phone_day.items():
        int_day[(fecha, idc)][bucket_label(n)] += 1
    intensity_day = []
    for (fecha, idc), dist in sorted(int_day.items()):
        meta = day_meta(fecha)
        intensity_day.append({
            "date": fecha, "day": meta["label"], "week": meta["week"],
            "campaign_id": idc, "campaign_name": camp_names.get(idc, idc),
            "unique_phones": sum(dist.values()),
            "distribution": {lbl: dist.get(lbl, 0) for _, _, lbl in BUCKETS},
            "by_slot": {sl: len(phone_slot_day.get((fecha, idc, sl), ()))
                        for sl in SLOTS_ALL},
        })

    status_out = [{"tool": t, "status": s, "count": n}
                  for (t, s), n in sorted(status_tool.items(), key=lambda x: (x[0][0], -x[1]))]

    start = min(dates) if dates else ""
    end   = max(dates) if dates else ""

    data = {
        "period": f"{start} – {end}",
        "generated": "auto",
        "days":   days,
        "weeks":  weeks,
        "tools":  sorted({tool for (_, tool, _) in agg}),
        "kpis": {
            "total":    finalize(total),
            "by_tool":  {t: finalize(b) for t, b in by_tool.items()},
        },
        "matrix":             matrix,
        "intensity_by_phone": intensity_out,
        "intensity_by_phone_day": intensity_day,
        "effectiveness_scopes": eff_scopes,
        "coverage":             coverage_out,
        "dates_with_assignment": (asig or {}).get("dates_with_assignment", []),
        "status_by_tool": status_out,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    print(f"OK → {OUT_PATH}")
    print(f"Total attempts: {data['kpis']['total']['attempts']:,}")
    for tool, b in sorted(data["kpis"]["by_tool"].items()):
        print(f"  {tool}: {b['attempts']:,} attempts | "
              f"answered {b['answered']:,} ({b['answer_rate_pct']}%) | "
              f"RPC {b['rpc']:,} ({b['rpc_pct_attempts']}% of attempts, "
              f"{b['rpc_pct_answered']}% of answered)")


if __name__ == "__main__":
    main()
