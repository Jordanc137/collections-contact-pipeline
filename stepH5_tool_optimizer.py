# =============================================================================
# stepH5_tool_optimizer.py - Best Contact Tool per Account (4-sheet Excel)
#
# For each account in today's assignment, recommends which contact tool
# (channel) to use today based on multi-tool CDR history. Produces a daily
# 4-sheet Excel and updates prioritization_today.csv.
#
# FRESHNESS CHECK: validates that prioritization_today.csv is newer than
# the latest assignment file. Stops if stale — never generates a tool
# recommendation against yesterday's portfolio.
#
# SHEETS:
#   1. Tool Recommendation   — best tool per account from CDR evidence
#   2. Age-Based Recommendation — channel suggestion by generational cohort,
#      reconciled with real CDR evidence (evidence always wins)
#   3. Payment Behavior Classification — transparent scoring system
#   4. Methodology & Sources — full documentation of how each sheet works
#
# DECISION RULES (Sheet 1):
#   Priority 1 → Account HAS answered on some tool: use that tool
#   Priority 2 → Account doesn't answer after MIN_ATTEMPTS_TO_SWITCH: try
#                the best-performing untried tool globally
#   Priority 3 → Too few attempts to conclude: keep current tool(s)
#   Priority 9 → No CDR history: new account
#
# All missing data is labeled explicitly — never coerced to zero or "good".
# =============================================================================
import glob
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core import phone_classifier

phone_classifier.PLAN_PATH = getattr(config, "NUMBERING_PLAN_PATH",
                                      phone_classifier.PLAN_PATH)

AUTHOR = "collections-contact-pipeline"

BASE     = config.OPTIMIZATION_BASE
DIR_DATA = config.OPT_OUTPUT_FOLDER
DIR_ASIG = config.OPT_ASSIGNMENTS_FOLDER
IN_PRIOR = os.path.join(DIR_DATA, "prioritization_today.csv")
CDR_ENRICHED = config.OPT_CDR_ENRICHED_PATH

MIN_ATTEMPTS_TO_SWITCH = getattr(config, "MIN_ATTEMPTS_TO_SWITCH", 3)
TOOLS_FALLBACK_ORDER   = ["Predictivo", "Predictive", "Blaster", "Robocall",
                           "IVR", "IA", "WhatsApp", "SMS"]
EXCEL_LIMIT = 1_048_576

# Output columns for Sheet 1 (add/remove to match your assignment fields)
SHEET1_COLUMNS = [
    "account_id", "name", "phone1", "phone2", "phone3", "phone4",
    "campaign_id", "campaign_name",
    "recommended_tool", "tool_reason", "tools_tried",
    "is_likely_payment_day_today", "top_payment_day", "top_payment_hour",
    "weeks_with_payment_history",
]

SLOT_COLUMNS = ("phone1", "phone2", "phone3", "phone4",
                "telefono1", "telefono2", "telefono3", "telefono4")

print("=" * 70)
print("OPTIMIZATION - STEP H5: Best Contact Tool per Account")
print(f"({AUTHOR})")
print("=" * 70)


# ---------------------------------------------------------------------------
# FRESHNESS CHECK
# ---------------------------------------------------------------------------
def find_latest_assignment_mtime():
    paths = sorted(glob.glob(os.path.join(DIR_ASIG, "*asig*.[cx][ls][vx]*")),
                   key=os.path.getmtime, reverse=True)
    if not paths:
        paths = sorted(glob.glob(os.path.join(DIR_ASIG, "*.csv")),
                       key=os.path.getmtime, reverse=True)
    return (os.path.getmtime(paths[0]), paths[0]) if paths else (0, None)


if not os.path.exists(IN_PRIOR):
    print("[ERROR] prioritization_today.csv not found.")
    print("        Run stepH3 and stepH4 first.")
    raise SystemExit(1)

mtime_asig, asig_path = find_latest_assignment_mtime()
mtime_prior = os.path.getmtime(IN_PRIOR)

print(f"\nLatest assignment : {os.path.basename(asig_path or '')} "
      f"({datetime.fromtimestamp(mtime_asig).strftime('%Y-%m-%d %H:%M')})")
print(f"prioritization_today.csv: "
      f"{datetime.fromtimestamp(mtime_prior).strftime('%Y-%m-%d %H:%M')}")

if mtime_prior < mtime_asig:
    print("\n[STOP] prioritization_today.csv is older than the latest assignment.")
    print("       Re-run stepH3 and stepH4 with today's assignment first.")
    raise SystemExit(1)
print("[OK] prioritization_today.csv is up to date.\n")


# ---------------------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------------------
print(f"Reading: {IN_PRIOR}")
df_prior = pd.read_csv(IN_PRIOR, encoding="utf-8-sig", dtype={"account_id": str,
                                                                "idUnico": str},
                        low_memory=False)
# Normalize account ID column name
for col in ("account_id", "idUnico", "id_unico"):
    if col in df_prior.columns:
        df_prior = df_prior.rename(columns={col: "account_id"})
        break
df_prior["account_id"] = df_prior["account_id"].astype(str).str.strip()
n_prior = len(df_prior)
print(f"  {n_prior:,} accounts")

# Drop stale tool columns from previous run (idempotent)
STALE_COLS = ["recommended_tool", "tool_priority", "tool_reason", "tools_tried"]
df_prior = df_prior.drop(columns=[c for c in STALE_COLS if c in df_prior.columns])

if not os.path.exists(CDR_ENRICHED):
    print(f"[ERROR] CDR enriched not found: {CDR_ENRICHED}")
    print("        Run the main pipeline first (pipeline/run_pipeline.py).")
    raise SystemExit(1)

print(f"Reading CDR enriched: {CDR_ENRICHED}")
df_cdr = pd.read_parquet(CDR_ENRICHED)
print(f"  {len(df_cdr):,} CDR rows")

# Resolve account ID in CDR
for col in ("ACCOUNT_ID_FINAL", "IDUNICO_FINAL", "CUENTA_CDR"):
    if col in df_cdr.columns:
        df_cdr["account_id"] = df_cdr[col].fillna("").astype(str).str.strip()
        break
df_cdr = df_cdr[df_cdr["account_id"] != ""].copy()
print(f"  {len(df_cdr):,} rows with identifiable account")

# ---------------------------------------------------------------------------
# TOOL AGGREGATION per account
# ---------------------------------------------------------------------------
ANSWERED_CATS = {"Answered", "Contestada"}

agg = (df_cdr.groupby(["account_id", "TOOL"])
       .agg(attempts=("CALL_CATEGORY", "size"),
            answered=("CALL_CATEGORY", lambda s: s.isin(ANSWERED_CATS).sum()))
       .reset_index())
agg["answer_rate"] = (agg["answered"] / agg["attempts"]).round(4)
print(f"\nAccount-tool pairs with history: {len(agg):,}")

# Phone slot analysis
VALID_SLOTS = [s for s in SLOT_COLUMNS if "1" in s or "2" in s or "3" in s or "4" in s]
DIALER_TOOLS = {"Predictivo", "Predictive", "Blaster", "Robocall", "IVR"}

df_slot = df_cdr[df_cdr.get("PHONE_SLOT", pd.Series(dtype=str)).isin(SLOT_COLUMNS)].copy() \
    if "PHONE_SLOT" in df_cdr.columns else pd.DataFrame()

if not df_slot.empty:
    agg_slot = (df_slot.groupby(["account_id", "PHONE_SLOT"])
                .agg(attempts=("CALL_CATEGORY", "size"),
                     answered=("CALL_CATEGORY", lambda s: s.isin(ANSWERED_CATS).sum()),
                     phone=("PHONE", "first"))
                .reset_index())
    agg_slot["answer_rate"] = (agg_slot["answered"] / agg_slot["attempts"]).round(4)
    agg_slot["line_type"] = agg_slot["phone"].map(phone_classifier.classify)

    # Dialer-attempted slots with no answers but ringing tones → alt channel candidate
    df_dialer = df_slot[df_slot["TOOL"].isin(DIALER_TOOLS)]
    if not df_dialer.empty:
        agg_dialer = (df_dialer.groupby(["account_id", "PHONE_SLOT"])
                      .agg(dialer_attempts=("CALL_CATEGORY", "size"),
                           dialer_answered=("CALL_CATEGORY", lambda s: s.isin(ANSWERED_CATS).sum()),
                           dialer_ringing=("CALL_CATEGORY", lambda s: s.isin({"Ringing", "Tono"}).sum()))
                      .reset_index())
        agg_dialer["alt_channel_candidate"] = (
            (agg_dialer["dialer_attempts"] >= MIN_ATTEMPTS_TO_SWITCH) &
            (agg_dialer["dialer_answered"] == 0) &
            (agg_dialer["dialer_ringing"] > 0))
        agg_slot = agg_slot.merge(agg_dialer, on=["account_id", "PHONE_SLOT"], how="left")
        agg_slot["alt_channel_candidate"] = agg_slot["alt_channel_candidate"].fillna(False)
    else:
        agg_slot["alt_channel_candidate"] = False

    def _alt_recommendation(row):
        if not row.get("alt_channel_candidate", False):
            return ""
        lt = row.get("line_type", "UNKNOWN")
        if lt == phone_classifier.MOBILE:
            return "WhatsApp (mobile line confirmed — rings but no answer via dialer)"
        if lt == phone_classifier.LANDLINE:
            return "No alt channel (landline — WhatsApp/SMS not applicable)"
        return "Pending: line type unknown (missing numbering plan) — if mobile, WhatsApp candidate"

    agg_slot["alt_channel_recommendation"] = agg_slot.apply(_alt_recommendation, axis=1)
else:
    agg_slot = pd.DataFrame()


# ---------------------------------------------------------------------------
# GLOBAL TOOL RANKING (for fallback suggestions)
# ---------------------------------------------------------------------------
ranking = (agg.groupby("TOOL")
           .agg(total_attempts=("attempts", "sum"), total_answered=("answered", "sum"))
           .reset_index())
ranking["global_rate"] = (ranking["total_answered"] / ranking["total_attempts"]).round(4)
ranking = ranking.sort_values("global_rate", ascending=False)
tool_order = ranking["TOOL"].tolist() + [t for t in TOOLS_FALLBACK_ORDER
                                          if t not in set(ranking["TOOL"])]
print("\nGlobal tool ranking:")
print(ranking[["TOOL", "total_attempts", "total_answered", "global_rate"]].to_string(index=False))


# ---------------------------------------------------------------------------
# DECISION RULE PER ACCOUNT
# ---------------------------------------------------------------------------
def analyze_account(sub):
    """sub = rows of `agg` for one account (one row per tool tried)."""
    tried = set(sub["TOOL"])
    answering = sub[sub["answered"] > 0].sort_values("answer_rate", ascending=False)
    if len(answering):
        best = answering.iloc[0]
        return {
            "recommended_tool": best["TOOL"],
            "tool_priority":    1,
            "tool_reason": (
                f"ANSWERS via {best['TOOL']} "
                f"({int(best['answered'])}/{int(best['attempts'])} attempts) "
                f"— maximum priority today"
            ),
            "tools_tried": ", ".join(sorted(tried)),
        }
    total = int(sub["attempts"].sum())
    if total < MIN_ATTEMPTS_TO_SWITCH:
        current = sub.iloc[0]["TOOL"] if len(sub) else "NO_HISTORY"
        return {
            "recommended_tool": current,
            "tool_priority":    3,
            "tool_reason": (
                f"Too few attempts ({total}/{MIN_ATTEMPTS_TO_SWITCH}) to "
                f"recommend a switch — keep current tool(s)"
            ),
            "tools_tried": ", ".join(sorted(tried)),
        }
    untried = [t for t in tool_order if t not in tried]
    suggest = untried[0] if untried else tool_order[0]
    return {
        "recommended_tool": suggest,
        "tool_priority":    2,
        "tool_reason": (
            f"No answer after {total} attempts via {', '.join(sorted(tried))} "
            f"— try {suggest} (best global rate among untried)"
        ),
        "tools_tried": ", ".join(sorted(tried)),
    }


print("\nComputing per-account recommendations...")
results = []
for idu, sub in agg.groupby("account_id"):
    r = analyze_account(sub)
    r["account_id"] = idu
    results.append(r)
df_reco = pd.DataFrame(results)
print(f"  Accounts with CDR history: {len(df_reco):,}")

# Merge with today's prioritization
df_final = df_prior.merge(df_reco, on="account_id", how="left")
assert len(df_final) == n_prior, (
    f"Row count changed after merge: {n_prior} → {len(df_final)}")

n_new = int(df_final["recommended_tool"].isna().sum())
df_final["recommended_tool"] = df_final["recommended_tool"].fillna("NO_HISTORY")
df_final["tool_priority"]    = df_final["tool_priority"].fillna(9).astype(int)
df_final["tool_reason"]      = df_final["tool_reason"].fillna(
    "New account — no previous contact attempts on any tool")
df_final["tools_tried"]      = df_final["tools_tried"].fillna("")
print(f"  New accounts (no history): {n_new:,}")

# Sort: priority 1 first, then contact_priority from H3 as tiebreaker
sort_cols = ["tool_priority"] + (
    ["contact_priority"] if "contact_priority" in df_final.columns else [])
df_final = df_final.sort_values(by=sort_cols, ascending=True).reset_index(drop=True)

# Save a full copy before trimming for Sheet 1
df_complete = df_final.copy()

# Sheet 1: trim to defined output columns
cols1 = [c for c in SHEET1_COLUMNS if c in df_final.columns]
# Normalize column aliases
alias_map = {"idUnico": "account_id", "idCampania": "campaign_id",
             "campana": "campaign_name", "nombre": "name",
             "telefono1": "phone1", "telefono2": "phone2",
             "telefono3": "phone3", "telefono4": "phone4"}
df_final = df_final.rename(columns={v: k for k, v in alias_map.items()
                                     if v in df_final.columns and k not in df_final.columns})
cols1 = [c for c in SHEET1_COLUMNS if c in df_final.columns]
df_sheet1 = df_final[cols1]


# ---------------------------------------------------------------------------
# SHEET 2 — AGE-BASED CHANNEL RECOMMENDATION
# ---------------------------------------------------------------------------
def recommend_by_age(age):
    """Returns (cohort, suggested_tool, reason, sources).
    Based on publicly available studies on communication channel preferences.
    Update this function when newer research is available.
    """
    if pd.isna(age):
        return ("NO_DATA", "NO_DATA", "No age in today's assignment", "")
    age = int(age)
    if age < 18:
        return ("UNDER_18", "REVIEW_MANUALLY",
                "Registered age < 18 — verify data before contacting", "")
    if age <= 34:
        return ("18–34 (Gen Z / Young Millennial)", "WhatsApp",
                "Studies show 18–34 group strongly prefers messaging over voice calls",
                "[digital-habits-study]")
    if age <= 49:
        return ("35–49 (Millennial)", "WhatsApp",
                "WhatsApp usage remains high in this range; use as first attempt, "
                "dialer as reinforcement if no response",
                "[messaging-adoption-study]")
    if age <= 64:
        return ("50–64 (Gen X)", "Predictive",
                "Voice preference increases with age in this cohort; "
                "call first, WhatsApp as reinforcement",
                "[voice-preference-study]")
    return ("65+ (Boomer / Senior)", "Predictive",
            "Strong preference for human voice; avoid IVR/bot as first attempt",
            "[senior-channel-study]")


age_col = next((c for c in df_complete.columns if c.lower() == "age"
                or c.lower() == "edad"), None)
if age_col:
    _age_reco = df_complete[age_col].apply(recommend_by_age)
    df_complete["age_cohort"]       = [r[0] for r in _age_reco]
    df_complete["age_suggested_tool"] = [r[1] for r in _age_reco]
    df_complete["age_reason"]       = [r[2] for r in _age_reco]
    df_complete["age_source"]       = [r[3] for r in _age_reco]

    def combine_evidence_and_age(row):
        pri = row.get("tool_priority", 9)
        cdr_tool = row.get("recommended_tool", "NO_HISTORY")
        age_tool = row.get("age_suggested_tool", "NO_DATA")
        if pri == 1:
            return (cdr_tool, f"CDR evidence kept ({cdr_tool}): overrides demographic assumption")
        if pd.isna(age_tool) or age_tool in ("NO_DATA", "REVIEW_MANUALLY"):
            return (cdr_tool, "No usable age data — keeping CDR suggestion")
        return (age_tool, f"No CDR contact evidence yet — using age cohort suggestion "
                          f"({row.get('age_cohort', '')})")

    _comb = df_complete.apply(combine_evidence_and_age, axis=1)
    df_complete["combined_recommended_tool"] = [c[0] for c in _comb]
    df_complete["combined_reason"]           = [c[1] for c in _comb]
else:
    for col in ("age_cohort", "age_suggested_tool", "age_reason", "age_source",
                "combined_recommended_tool", "combined_reason"):
        df_complete[col] = pd.NA

AGE_COLS = (["account_id", "name"] +
            ([age_col] if age_col else []) +
            ["age_cohort", "phone1", "phone2", "phone3", "phone4",
             "campaign_id", "campaign_name",
             "recommended_tool", "age_suggested_tool", "age_source",
             "age_reason", "combined_recommended_tool", "combined_reason"])
AGE_COLS = [c for c in dict.fromkeys(AGE_COLS) if c in df_complete.columns]
df_sheet2 = df_complete[AGE_COLS]


# ---------------------------------------------------------------------------
# SHEET 3 — PAYMENT BEHAVIOR CLASSIFICATION (transparent scoring)
# ---------------------------------------------------------------------------
# Scoring factors (adjust weights/thresholds in config.py or here)
SCORE_CONFIG = {
    "payment_consistency_H": 2,   # tend_calificacion or payment_rating == "H"
    "payment_consistency_M": 1,
    "payment_history_many_weeks": 2,   # >= weeks threshold
    "payment_history_few_weeks":  1,
    "weeks_threshold": 4,
    "low_delinquency_bonus":  1,   # weeks_overdue <= low threshold
    "high_delinquency_penalty": -2,  # weeks_overdue >= high threshold
    "low_delinquency_threshold":  2,
    "high_delinquency_threshold": 7,
    "answers_bonus":   1,   # tool_priority == 1
    "no_answer_penalty": -1,  # tool_priority == 2
    "positive_threshold": 3,
    "negative_threshold": -2,
}

WEEKS_COL    = next((c for c in df_complete.columns
                     if c.lower() in ("semanasatraso", "weeks_overdue", "weeks_delinquent")), None)
CONSIST_COL  = next((c for c in df_complete.columns
                     if c.lower() in ("tend_calificacion", "payment_rating", "payment_consistency")), None)
PAY_WEEKS_COL= next((c for c in df_complete.columns
                     if c.lower() in ("tend_total_semanas_con_pago", "total_weeks_with_payment")), None)


def score_account(row):
    p = 0
    detail = []

    cons = str(row.get(CONSIST_COL, "") or "") if CONSIST_COL else ""
    if pd.isna(cons) or cons == "nan":
        cons = ""
    if cons.upper() == "H":
        p += SCORE_CONFIG["payment_consistency_H"]
        detail.append(f"+{SCORE_CONFIG['payment_consistency_H']} payment day consistency (H)")
    elif cons.upper() == "M":
        p += SCORE_CONFIG["payment_consistency_M"]
        detail.append(f"+{SCORE_CONFIG['payment_consistency_M']} payment day consistency (M)")

    weeks_pay = 0
    if PAY_WEEKS_COL:
        try:
            weeks_pay = float(row.get(PAY_WEEKS_COL, 0) or 0)
        except (ValueError, TypeError):
            weeks_pay = 0
        if not pd.isna(weeks_pay):
            weeks_pay = float(weeks_pay)
    if weeks_pay >= SCORE_CONFIG["weeks_threshold"]:
        p += SCORE_CONFIG["payment_history_many_weeks"]
        detail.append(f"+{SCORE_CONFIG['payment_history_many_weeks']} broad payment history "
                       f"({int(weeks_pay)} weeks)")
    elif weeks_pay >= 1:
        p += SCORE_CONFIG["payment_history_few_weeks"]
        detail.append(f"+{SCORE_CONFIG['payment_history_few_weeks']} some payment history")

    if WEEKS_COL:
        try:
            del_weeks = float(row.get(WEEKS_COL, None) or float("nan"))
        except (ValueError, TypeError):
            del_weeks = float("nan")
        if not pd.isna(del_weeks):
            if del_weeks <= SCORE_CONFIG["low_delinquency_threshold"]:
                p += SCORE_CONFIG["low_delinquency_bonus"]
                detail.append(f"+{SCORE_CONFIG['low_delinquency_bonus']} low delinquency "
                               f"({int(del_weeks)} wks)")
            elif del_weeks >= SCORE_CONFIG["high_delinquency_threshold"]:
                p += SCORE_CONFIG["high_delinquency_penalty"]
                detail.append(f"{SCORE_CONFIG['high_delinquency_penalty']} high delinquency "
                               f"({int(del_weeks)} wks)")

    tp = row.get("tool_priority", 9)
    if tp == 1:
        p += SCORE_CONFIG["answers_bonus"]
        detail.append(f"+{SCORE_CONFIG['answers_bonus']} answers (CDR evidence)")
    elif tp == 2:
        p += SCORE_CONFIG["no_answer_penalty"]
        detail.append(f"{SCORE_CONFIG['no_answer_penalty']} doesn't answer after attempts")

    return p, "; ".join(detail) if detail else "no scoring factors available yet"


def classify_payment(row):
    no_cons = (not CONSIST_COL or
               str(row.get(CONSIST_COL, "") or "") in ("", "SIN_HISTORICO", "nan"))
    tp = row.get("tool_priority", 9)
    if no_cons and tp in (3, 9):
        return "Insufficient data"
    if row["payment_score"] >= SCORE_CONFIG["positive_threshold"]:
        return "Positive payer"
    if row["payment_score"] <= SCORE_CONFIG["negative_threshold"]:
        return "Negative payer"
    return "Regular payer"


df_clasif = df_complete.copy()
_scores = df_clasif.apply(score_account, axis=1)
df_clasif["payment_score"]          = [r[0] for r in _scores]
df_clasif["payment_score_detail"]   = [r[1] for r in _scores]
df_clasif["payment_classification"] = df_clasif.apply(classify_payment, axis=1)

CLASIF_COLS = (["account_id", "name", "campaign_id", "campaign_name"] +
               ([WEEKS_COL] if WEEKS_COL else []) +
               (["saldo"] if "saldo" in df_clasif.columns else
                ["balance"] if "balance" in df_clasif.columns else []) +
               ([CONSIST_COL] if CONSIST_COL else []) +
               ([PAY_WEEKS_COL] if PAY_WEEKS_COL else []) +
               ["tool_priority", "payment_score", "payment_score_detail",
                "payment_classification"])
CLASIF_COLS = [c for c in dict.fromkeys(CLASIF_COLS) if c in df_clasif.columns]
df_sheet3 = df_clasif[CLASIF_COLS]

print("\nPayment classification distribution:")
print(df_sheet3["payment_classification"].value_counts().to_string())


# ---------------------------------------------------------------------------
# SHEET 4 — METHODOLOGY & SOURCES
# ---------------------------------------------------------------------------
sc = SCORE_CONFIG
method_rows = [
    ("SHEET 1 — Tool Recommendation", ""),
    ("Source", "Multi-tool CDR history from main pipeline (cdr_enriched.parquet): "
               "Blaster/Predictive/IVR/IA/WhatsApp/SMS."),
    ("Decision rule",
     f"ANSWERS via any tool → that tool (priority 1). "
     f"No answer after {MIN_ATTEMPTS_TO_SWITCH}+ attempts → try best untried tool globally "
     f"(priority 2). Too few attempts → keep current (priority 3). "
     f"No history → new account (priority 9)."),
    ("", ""),
    ("SHEET 2 — Age-Based Recommendation", ""),
    ("Philosophy", "Age is ONLY used when there is no CDR contact evidence yet. "
                   "Real contact evidence always overrides the demographic assumption."),
    ("Cohorts", "18–34: messaging preference. 35–49: messaging with voice reinforcement. "
                "50–64: voice first. 65+: human voice, avoid IVR as first attempt."),
    ("Update instructions", "Modify recommend_by_age() in stepH5_tool_optimizer.py "
                             "when newer research is available. Keep the source references."),
    ("", ""),
    ("SHEET 3 — Payment Behavior Classification", ""),
    ("Philosophy", "Transparent scoring — each factor is in its own column so any "
                   "classification can be audited. Not a black-box model."),
    ("Factor: payment day consistency",
     f"H: +{sc['payment_consistency_H']} | M: +{sc['payment_consistency_M']} | L/none: +0"),
    ("Factor: payment history depth",
     f">={sc['weeks_threshold']} wks: +{sc['payment_history_many_weeks']} | "
     f"1-{sc['weeks_threshold']-1} wks: +{sc['payment_history_few_weeks']} | none: +0"),
    ("Factor: delinquency",
     f"<={sc['low_delinquency_threshold']} wks: +{sc['low_delinquency_bonus']} | "
     f">={sc['high_delinquency_threshold']} wks: {sc['high_delinquency_penalty']} | mid: +0"),
    ("Factor: contactability (Sheet 1)",
     f"Answers: +{sc['answers_bonus']} | Doesn't answer: {sc['no_answer_penalty']}"),
    ("Positive payer threshold", f"score >= {sc['positive_threshold']}"),
    ("Negative payer threshold", f"score <= {sc['negative_threshold']}"),
    ("Insufficient data",
     "No payment consistency history AND no CDR contact evidence — "
     "truly unknown, not assumed Regular."),
    ("Note", "First-version weights — reasonable starting point, not validated by "
              "the collections team. Adjust SCORE_CONFIG at the top of stepH5 if needed."),
    ("", ""),
    ("Generated", datetime.now().strftime("%Y-%m-%d %H:%M")),
    ("Author / script", AUTHOR),
]
df_sheet4 = pd.DataFrame(method_rows, columns=["Concept", "Detail"])


# ---------------------------------------------------------------------------
# SAVE
# ---------------------------------------------------------------------------
# Update prioritization_today.csv (same format, new tool columns appended)
print(f"\nSaving: {IN_PRIOR}")
try:
    df_final.to_csv(IN_PRIOR, index=False, encoding="utf-8-sig")
    print(f"[OK] {len(df_final):,} rows")
except PermissionError:
    print("[ERROR] Cannot save prioritization_today.csv — file open in another program?")
    raise SystemExit(1)

today_str = datetime.today().strftime("%d_%m_%y")
excel_path = os.path.join(DIR_DATA, f"Contact_Tool_Prioritization_{today_str}.xlsx")
if len(df_sheet1) >= EXCEL_LIMIT:
    print(f"[WARNING] {len(df_sheet1):,} rows exceeds Excel limit ({EXCEL_LIMIT:,}) — "
          f"CSV is complete but Excel will be truncated")

try:
    with pd.ExcelWriter(excel_path, engine="openpyxl") as xw:
        df_sheet1.head(EXCEL_LIMIT - 1).to_excel(xw, index=False, sheet_name="Tool Recommendation")
        df_sheet2.head(EXCEL_LIMIT - 1).to_excel(xw, index=False, sheet_name="Age Recommendation")
        df_sheet3.head(EXCEL_LIMIT - 1).to_excel(xw, index=False, sheet_name="Payment Classification")
        df_sheet4.to_excel(xw, index=False, sheet_name="Methodology & Sources")
    print(f"OK → {excel_path}")
except Exception as e:
    print(f"[ERROR] Could not write Excel: {e}")
    raise SystemExit(1)

print("\nPriority distribution:")
LABELS = {
    1: "1 — ANSWERS via some tool (highest priority today)",
    2: "2 — Doesn't answer — suggest tool switch",
    3: "3 — Too few attempts — keep current tool(s)",
    9: "9 — New account (no CDR history)",
}
for p, cnt in df_final["tool_priority"].value_counts().sort_index().items():
    print(f"   {LABELS.get(int(p), str(p)):<60} {cnt:>8,}")
