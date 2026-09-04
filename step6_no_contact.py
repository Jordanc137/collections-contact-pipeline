# =============================================================================
# step6_no_contact.py - Accounts with Zero Contact Attempts
#
# Detects accounts in TODAY's assignment that received no contact attempt
# in the entire history (cdr_enriched.parquet), diagnoses why, and produces
# an actionable Excel for the contact team.
#
# DIAGNOSIS per phone slot (phone1..phone4):
#   NO_NUMBER        → column is empty in today's assignment
#   NEVER_DIALED     → phone does not appear in any CDR in the period
#   DIALED_DISCARDED → all attempts on this number ended in "Discard" category
#   DIALED_NO_ANSWER → dialed but no successful contact (under another account)
#   DIALED_OTHER_ACCT→ number was dialed and attributed to a different account
#
# SUGGESTED_SLOT = first slot with NEVER_DIALED number (fresh volume);
#   if none, first slot not discarded.
#
# OUTPUT: cdr_no_contact_today.xlsx (3 sheets: detail, summary, legend)
# =============================================================================
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core import cdr_io

ENRICHED_PATH = os.path.join(config.OUTPUT_FOLDER, config.ENRICHED_PARQUET)
OUT_XLSX      = os.path.join(config.OUTPUT_FOLDER, "cdr_no_contact_today.xlsx")

EXCLUDED_NAMES = config.EXCLUDED_CAMPAIGNS
EXCLUDED_IDS = {
    str(idc) for idc, name in config.CAMPAIGN_CATALOG.items()
    if name in EXCLUDED_NAMES
}
SLOTS = ("phone1", "phone2", "phone3", "phone4",
         "telefono1", "telefono2", "telefono3", "telefono4")
SLOT_ALIASES = {
    "phone1": ["phone1", "telefono1", "tel1"],
    "phone2": ["phone2", "telefono2", "tel2"],
    "phone3": ["phone3", "telefono3", "tel3"],
    "phone4": ["phone4", "telefono4", "tel4"],
}

_RE_NON_DIGIT = re.compile(r"\D")
def norm_phone(raw):
    if raw is None:
        return None
    s = str(raw).strip().split(".")[0]
    s = _RE_NON_DIGIT.sub("", s)
    return s[-10:] if len(s) >= 10 else None


def _find_col(keys, aliases):
    low = {k.lower(): k for k in keys}
    for a in aliases:
        if a.lower() in low:
            return low[a.lower()]
    return None


def _read_rows(path):
    if path.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "latin1"):
            try:
                with open(path, encoding=enc, newline="") as f:
                    yield from csv.DictReader(f)
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


def pick_today_assignment():
    """Return (date, path) for today's assignment, or most recent with a warning."""
    candidates = []
    folder = config.ASSIGNMENTS_FOLDER
    for root, _d, files in os.walk(folder):
        for fn in files:
            low = fn.lower()
            m = re.search(r"(\d{2})(\d{2})(\d{4})", fn)
            if (low.endswith(".csv") or low.endswith(".xlsx")) and "asig" in low and m:
                fecha = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                candidates.append((fecha, os.path.join(root, fn)))
    if not candidates:
        print(f"[ERROR] No assignment files found in {folder}")
        raise SystemExit(1)
    today = date.today().strftime("%Y-%m-%d")
    exact = [c for c in candidates if c[0] == today]
    if exact:
        return exact[0]
    fecha, path = max(candidates)
    print(f"[INFO] No assignment for today ({today}); using most recent: "
          f"{os.path.basename(path)} ({fecha})")
    return fecha, path


def main():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    # --- Build CDR index ---
    print("Indexing CDR enriched...")
    phone_attempts  = defaultdict(int)       # phone → total attempts
    phone_discards  = defaultdict(int)       # phone → discard-category attempts
    phone_answered  = defaultdict(int)       # phone → answered attempts
    accts_with_attempt = set()              # account_id strings

    for row in cdr_io.read_parquet_rows(ENRICHED_PATH):
        phone = row.get("PHONE", "")
        cat   = row.get("CALL_CATEGORY", "")
        acct  = row.get("ACCOUNT_ID_FINAL", "")
        if phone:
            phone_attempts[phone] += 1
            if cat in ("Discard", "Descartar"):
                phone_discards[phone] += 1
            if cat in ("Answered", "Contestada"):
                phone_answered[phone] += 1
        if acct:
            accts_with_attempt.add(acct)

    print(f"  {len(accts_with_attempt):,} accounts have at least one attempt in history")

    # --- Load today's assignment ---
    fecha_today, path_today = pick_today_assignment()
    print(f"Using assignment: {os.path.basename(path_today)} ({fecha_today})")

    ACCOUNT_ID_ALIASES = ["account_id", "idUnico", "id_unico", "cuenta"]
    CAMPAIGN_ID_ALIASES = ["campaign_id", "idCampania", "id_campania"]
    EXTRA_COLS = ["name", "balance", "required_payment", "weeks_overdue",
                  "nombre", "saldo", "pagoRequerido", "semanasAtraso", "strategy", "Estrategia"]

    def diag_phone(ph):
        if ph is None:
            return "NO_NUMBER"
        n = phone_attempts.get(ph, 0)
        if n == 0:
            return "NEVER_DIALED"
        if phone_discards.get(ph, 0) == n:
            return "DIALED_DISCARDED"
        if phone_answered.get(ph, 0) == 0:
            return "DIALED_NO_ANSWER"
        return "DIALED_OTHER_ACCT"

    rows_out = []
    total_assigned = 0
    col_cache = None

    for row in _read_rows(path_today):
        if col_cache is None:
            keys = list(row.keys())
            col_cache = {
                "account":  _find_col(keys, ACCOUNT_ID_ALIASES),
                "campaign": _find_col(keys, CAMPAIGN_ID_ALIASES),
                "phones":   {sl: _find_col(keys, aliases)
                             for sl, aliases in SLOT_ALIASES.items()},
                "extra":    {c: c for c in EXTRA_COLS if c in keys},
            }

        idc  = str(row.get(col_cache.get("campaign") or "", "") or "").strip().split(".")[0]
        acct = str(row.get(col_cache.get("account") or "", "") or "").strip()
        if not acct or idc in EXCLUDED_IDS:
            continue
        total_assigned += 1
        if acct in accts_with_attempt:
            continue  # has at least one attempt — not in scope

        phones = {sl: norm_phone(row.get(col or "")) for sl, col in col_cache["phones"].items()}
        diags  = {sl: diag_phone(ph) for sl, ph in phones.items()}

        suggested = (
            next((sl for sl in SLOT_ALIASES if diags[sl] == "NEVER_DIALED"), None) or
            next((sl for sl in SLOT_ALIASES
                  if diags[sl] not in ("NO_NUMBER", "DIALED_DISCARDED")), None) or ""
        )

        extra = {c: str(row.get(col, "") or "") for c, col in col_cache["extra"].items()}

        rows_out.append({
            "account_id": acct,
            "campaign_id": idc,
            "campaign_name": str(config.CAMPAIGN_CATALOG.get(int(idc), idc)
                                 if idc.isdigit() else idc),
            **{sl: phones[sl] or "" for sl in SLOT_ALIASES},
            **{f"diag_{sl}": diags[sl] for sl in SLOT_ALIASES},
            "suggested_slot": suggested,
            **extra,
        })

    no_contact = len(rows_out)
    print(f"Assigned today: {total_assigned:,} | Without any contact: {no_contact:,} "
          f"({100*no_contact/total_assigned:.1f}% of assigned)")

    if not rows_out:
        print("All accounts have at least one contact attempt — no output file needed.")
        return

    # --- Write Excel ---
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "No Contact"

    header_cols = (["account_id", "campaign_id", "campaign_name"] +
                   list(SLOT_ALIASES.keys()) +
                   [f"diag_{sl}" for sl in SLOT_ALIASES] +
                   ["suggested_slot"] +
                   list(rows_out[0].keys()
                        if rows_out else []))
    header_cols = list(dict.fromkeys(header_cols))  # deduplicate order

    header_fill = PatternFill("solid", fgColor="2F5496")
    header_font = Font(color="FFFFFF", bold=True)

    for ci, col in enumerate(header_cols, 1):
        cell = ws.cell(row=1, column=ci, value=col)
        cell.fill = header_fill
        cell.font = header_font

    for ri, row in enumerate(rows_out, 2):
        for ci, col in enumerate(header_cols, 1):
            ws.cell(row=ri, column=ci, value=row.get(col, ""))

    # Sheet 2: diagnosis summary
    ws2 = wb.create_sheet("Summary")
    ws2.append(["Diagnosis", "Phone Slot", "Count"])
    diag_counts = defaultdict(int)
    for row in rows_out:
        for sl in SLOT_ALIASES:
            diag_counts[(row[f"diag_{sl}"], sl)] += 1
    for (diag, slot), n in sorted(diag_counts.items()):
        ws2.append([diag, slot, n])

    # Sheet 3: legend
    ws3 = wb.create_sheet("Legend")
    ws3.append(["Code", "Meaning", "Action"])
    legend = [
        ("NO_NUMBER",       "Phone column is empty in today's assignment",
         "Check if account has other phone slots"),
        ("NEVER_DIALED",    "Number has never been dialed in the analysis period",
         "Priority — fresh number, high potential"),
        ("DIALED_DISCARDED","All previous attempts ended in Discard (disconnected/machine)",
         "Low priority — number likely invalid"),
        ("DIALED_NO_ANSWER","Dialed but no successful contact",
         "Can retry — line rings but no one answered"),
        ("DIALED_OTHER_ACCT","Dialed and attributed to a different account",
         "Shared number — approach with caution"),
    ]
    for row in legend:
        ws3.append(row)

    wb.save(OUT_XLSX)
    print(f"OK → {OUT_XLSX} ({no_contact:,} accounts, 3 sheets)")


if __name__ == "__main__":
    main()
