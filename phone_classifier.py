# =============================================================================
# phone_classifier.py - Mobile / landline classification for 10-digit phone
# numbers using a national numbering plan CSV.
#
# WHY NOT HEURISTICS: any hand-built prefix table becomes stale as the
# regulator reassigns number series. The only reliable source is the official
# national numbering plan published by the telecommunications authority.
#
# PORTING NOTE: this module was built against Mexico's IFT (Federal
# Telecommunications Institute) numbering plan. The CSV schema expected is:
#   ZONA, NUMERACION_INICIAL, NUMERACION_FINAL, OCUPACION, MODALIDAD,
#   RAZON_SOCIAL, FECHA_ASIGNACION
# with MODALIDAD values: "FIJO" = landline, "CPP"/"MPP" = mobile.
# For other countries: update COLUMN_MAP and _modalidad_to_type() to match
# your authority's schema.
#
# PERFORMANCE FIX (bisect list precomputed at load time):
# Binary search over 178,151 blocks. The search list (_cache_starts) is built
# once when the plan loads — rebuilding it on every classify() call caused
# timeouts at real production scale (~22k numbers per run).
#
# OVERLAP NOTE: ~0.02% of blocks in the real file have overlapping ranges
# (historical reassignments). Binary search assumes non-overlapping ranges;
# for those ~33 boundary blocks, the returned type may correspond to the
# neighbor block in rare edge cases. Not worth a more complex interval
# structure at this volume — documented here for transparency.
#
# WITHOUT THE PLAN FILE: classify() returns "UNKNOWN" for everything — never
# invents a type. The pipeline continues normally with UNKNOWN values.
# =============================================================================
import os
import csv
import bisect

# Path to the numbering plan CSV — expected in the same directory as this file.
# Override by setting phone_classifier.PLAN_PATH before calling classify().
PLAN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "numbering_plan.csv")

# Column name aliases (lowercase, stripped). Extend if your authority uses
# different column names across versions.
COLUMN_MAP = {
    "range_start": ["numeracion_inicial", "numeracion inicial", "range_start", "start"],
    "range_end":   ["numeracion_final",   "numeracion final",   "range_end",   "end"],
    "modality":    ["modalidad", "modality", "tipo_red", "tipo red", "type"],
}

# Return values
MOBILE   = "MOBILE"
LANDLINE = "LANDLINE"
UNKNOWN  = "UNKNOWN"

_cache        = None  # list of (start: int, end: int, type: str)
_cache_starts = None  # parallel list of start values for bisect


def _modality_to_type(raw):
    """Map the modality/type column value to MOBILE, LANDLINE, or None.

    None means the row is unrecognized and will be skipped (never guessed).

    Extend this function if your numbering plan uses different values.
    """
    m = raw.strip().upper()
    if m == "FIJO":
        return LANDLINE
    if m in ("CPP", "MPP"):  # CPP = caller-pays, MPP = receiver-pays — both mobile
        return MOBILE
    # Generic aliases for non-IFT plans
    if m in ("MOBILE", "MOVIL", "MOV"):
        return MOBILE
    if m in ("FIXED", "LANDLINE", "FIJO"):
        return LANDLINE
    # Fallback substring match
    if "MOV" in m:
        return MOBILE
    if "FIJ" in m or "FIX" in m or "LAND" in m:
        return LANDLINE
    return None


def _load_plan():
    global _cache, _cache_starts
    if _cache is not None:
        return _cache

    if not os.path.exists(PLAN_PATH):
        print(f"  [INFO] Numbering plan not found at {PLAN_PATH} — "
              f"classify() will return '{UNKNOWN}' for all numbers.")
        _cache = []
        _cache_starts = []
        return _cache

    blocks = []
    unrecognized_modalities = set()

    with open(PLAN_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header_lower = {h.strip().lower(): h for h in (reader.fieldnames or []) if h}

        # Resolve actual column names
        col = {}
        for internal, aliases in COLUMN_MAP.items():
            col[internal] = next(
                (header_lower[a] for a in aliases if a in header_lower), None
            )

        missing = [k for k, v in col.items() if v is None]
        if missing:
            print(f"  [WARNING] Numbering plan missing columns: {missing}. "
                  f"Update COLUMN_MAP in phone_classifier.py — returning '{UNKNOWN}' for all.")
            _cache = []
            _cache_starts = []
            return _cache

        for row in reader:
            try:
                start = int(str(row[col["range_start"]]).strip())
                end   = int(str(row[col["range_end"]]).strip())
            except (TypeError, ValueError, KeyError):
                continue

            modality_raw = str(row.get(col["modality"], "") or "")
            phone_type = _modality_to_type(modality_raw)
            if phone_type is None:
                if modality_raw.strip():
                    unrecognized_modalities.add(modality_raw.strip())
                continue

            if start > end:
                continue
            blocks.append((start, end, phone_type))

    blocks.sort()
    _cache = blocks
    _cache_starts = [b[0] for b in blocks]

    print(f"  [OK] Numbering plan: {len(blocks):,} blocks loaded from {os.path.basename(PLAN_PATH)}")
    if unrecognized_modalities:
        print(f"  [WARNING] Unrecognized modality values (rows skipped): "
              f"{sorted(unrecognized_modalities)}")
    return _cache


def plan_available():
    """True if the plan file loaded at least one block."""
    return len(_load_plan()) > 0


def classify(phone):
    """Classify a 10-digit phone number string as MOBILE, LANDLINE, or UNKNOWN.

    Never guesses — returns UNKNOWN for:
    - Invalid format (not exactly 10 digits)
    - Number outside all known blocks
    - Plan file not available
    """
    plan = _load_plan()
    if not plan or not phone:
        return UNKNOWN

    s = str(phone).strip()
    if len(s) != 10 or not s.isdigit():
        return UNKNOWN

    n = int(s)
    idx = bisect.bisect_right(_cache_starts, n) - 1
    if idx < 0:
        return UNKNOWN

    start, end, phone_type = plan[idx]
    if start <= n <= end:
        return phone_type
    return UNKNOWN


def reload():
    """Force a reload of the plan file (useful if PLAN_PATH was changed at runtime)."""
    global _cache, _cache_starts
    _cache = None
    _cache_starts = None
    return _load_plan()
