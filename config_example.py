# =============================================================================
# config_example.py — Configuration template
# Copy this file to config.py and adjust paths for your environment.
# config.py is gitignored — never commit real paths or credentials.
# =============================================================================

import os

# ---------------------------------------------------------------------------
# BASE PATHS
# ---------------------------------------------------------------------------
# Root folder for the main CDR pipeline (ANALISIS INTENSIDAD DE CONTACTO)
PIPELINE_BASE = r"C:\Users\YOU\Documents\CDR_PIPELINE"

# Root folder for the optimization module (ANALISIS3)
OPTIMIZATION_BASE = r"C:\Users\YOU\Documents\CDR_OPTIMIZATION"

# ---------------------------------------------------------------------------
# INPUT FOLDERS (relative to PIPELINE_BASE)
# All are optional — missing folders are silently skipped during detection.
# ---------------------------------------------------------------------------
INPUT_FOLDERS = [
    "",                # project root (legacy files kept here)
    "BLASTER",         # robocall/blaster XLSX (formats A/B)
    "PREDICTIVO IVR",  # predictive/IVR XLSX (formats A/B)
    "BLASTER CHOCK",   # alternate robocall CSV (format F)
    "PREDICTIVO MT",   # alternate predictive XLSX (MuttechMX)
    "IA",              # AI call CSV (calls_multiple_campaigns*.csv)
    "WHATSAPP",        # chat CSV (calixtachat_chats*.csv)
    "SMS",             # reserved
    "REPORTES",        # raw EXPORT_CALL_REPORT TXT from Vicidial
    "insumos_cdr",     # legacy catch-all
]

# Daily assignment files folder
ASSIGNMENTS_FOLDER = os.path.join(PIPELINE_BASE, "ASIGNACIONES POR DIA")

# Payment report folders
PAYMENTS_RPT_FOLDER  = os.path.join(PIPELINE_BASE, "4.-PAGOS")
PAYMENTS_CUT_FOLDER  = os.path.join(PIPELINE_BASE, "PAGOS X CORTE")

# Output folder (created automatically if missing)
OUTPUT_FOLDER = os.path.join(PIPELINE_BASE, "salidas")

# ---------------------------------------------------------------------------
# CAMPAIGN CATALOG
# Map numeric campaign IDs to human-readable names.
# Campaigns in EXCLUDED_CAMPAIGNS are still cataloged here (so they display
# correctly in logs) but are filtered out of all main analysis outputs.
# ---------------------------------------------------------------------------
CAMPAIGN_CATALOG = {
    # Example entries — replace with your actual campaign IDs and names:
    1001: "SEGMENT A",
    1002: "SEGMENT B",
    1003: "SEGMENT C",
    # Special campaigns managed outside this analysis:
    9001: "EMPLOYEES",
    9002: "RESTRUCTURE",
}

# Campaign names to exclude from main analysis (managed separately).
# Must match the values (names) in CAMPAIGN_CATALOG above.
EXCLUDED_CAMPAIGNS = {
    "EMPLOYEES",
    "RESTRUCTURE",
    # Add more as needed — e.g. "SWAT", "PILOT", etc.
}

# ---------------------------------------------------------------------------
# TOOL / CHANNEL IDENTIFICATION
# Campaign ID prefixes that identify each dialing tool.
# Adjust if your dialing platform uses different campaign ID formats.
# ---------------------------------------------------------------------------
CAMPAIGN_ID_PREFIXES = {
    "Blaster":    ["CB"],   # e.g. CBBAZ01 → Blaster
    "Predictivo": ["CP"],   # e.g. CPBAZ → Predictivo
    "IVR":        ["IV"],   # e.g. IVRB01 → IVR
}

# ---------------------------------------------------------------------------
# CONTACT INTENSITY THRESHOLDS
# Buckets used in the intensity distribution and payment rate analysis.
# Each tuple: (min_attempts, max_attempts, label)
# ---------------------------------------------------------------------------
INTENSITY_BUCKETS = [
    (0,   0,    "0"),
    (1,   1,    "1"),
    (2,   3,    "2-3"),
    (4,   6,    "4-6"),
    (7,   10,   "7-10"),
    (11,  9999, "11+"),
]

# ---------------------------------------------------------------------------
# STATISTICAL PARAMETERS
# ---------------------------------------------------------------------------
# Minimum sample size to declare an "optimal contact hour" in step 8.
# Hours with fewer samples than this are excluded from the recommendation.
MIN_SAMPLE_FOR_OPTIMAL_HOUR = 10

# Wilson score confidence level for contact hour intervals (0.95 = 95% CI)
CONFIDENCE_LEVEL = 0.95

# Short-call threshold: calls with duration <= this value (seconds) are
# classified as "Contestada corta" instead of "Contestada".
SHORT_CALL_THRESHOLD_SECONDS = 10

# ---------------------------------------------------------------------------
# NUMBERING PLAN (phone_classifier.py)
# Path to the national numbering plan CSV for mobile/landline classification.
# Leave as None to disable classification (returns UNKNOWN for all numbers).
# ---------------------------------------------------------------------------
NUMBERING_PLAN_PATH = os.path.join(PIPELINE_BASE, "IFT_Plan_Numeracion.csv")

# ---------------------------------------------------------------------------
# OUTPUT FILE NAMES
# ---------------------------------------------------------------------------
MASTER_PARQUET    = "cdr_master.parquet"
ENRICHED_PARQUET  = "cdr_enriched.parquet"
DASHBOARD_DATA    = "dashboard_data.json"
DASHBOARD_HTML    = "dashboard.html"

# ---------------------------------------------------------------------------
# OPTIMIZATION MODULE (stepH3 / H4 / H5)
# ---------------------------------------------------------------------------
OPT_ASSIGNMENTS_FOLDER = os.path.join(OPTIMIZATION_BASE, "asignaciones")
OPT_OUTPUT_FOLDER      = os.path.join(OPTIMIZATION_BASE, "datos_limpios")

# Path to cdr_enriched.parquet from the main pipeline (consumed by stepH5)
OPT_CDR_ENRICHED_PATH  = os.path.join(OUTPUT_FOLDER, ENRICHED_PARQUET)
