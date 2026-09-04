# Status Code Catalog

All raw STATUS codes from each platform are normalized to one of 5 categories.
Unknown codes are logged as warnings and fall to **No data** — never silently miscategorized.

## Categories

| Category | Description | Counts as contact? |
|----------|-------------|-------------------|
| `Answered` | Call answered, agent confirmed contact | Yes |
| `Answered (short)` | Answered but very brief — likely voicemail or immediate hangup | No |
| `Ringing` | Ringing, busy, no answer, call dropped | No |
| `Discard` | Disconnected number, answering machine, invalid — do not retry | No |
| `No data` | Unrecognized status — pipeline logs a warning | No |

**Short call threshold**: calls with `DURATION_SEC <= SHORT_CALL_THRESHOLD_SECONDS` (configurable in `config.py`) are reclassified from `Answered` to `Answered (short)`. This prevents voicemail pickups from inflating contact rate metrics.

---

## Predictive dialer (Vicidial-based)

| Status | Category | Notes |
|--------|----------|-------|
| `COL` | Answered | Agent placed call — RPC |
| `CTT` | Answered | Contact with account holder — RPC |
| `SALE` | Answered | Payment committed — RPC |
| `PDPP` | Answered | Previous payment promise — RPC |
| `CALLBK` | Answered | Callback scheduled — RPC |
| `XFER` | Answered | Transferred — RPC |
| `NA` | Answered | Confirmed answered as of 2026-09-02 — RPC |
| `AB` | Ringing | Busy (auto) |
| `ADCA` | Ringing | Auto dial — no answer |
| `ADC` | Ringing | Disconnected auto (reclassified from Answered, 2026-08-19) |
| `DROP` | Ringing | Agent not available (reclassified from Answered, 2026-08-19) |
| `PDROP` | Ringing | Pre-routing drop (reclassified from Answered, 2026-08-19) |
| `SVYCLM` | Ringing | Survey claim |
| `ILO` | Ringing | Added 2026-08-19 |
| `MSB` | Ringing | Added 2026-08-19 |
| `NOA` | Ringing | No agent available — added 2026-08-19 |
| `REF` | Ringing | Refused — added 2026-08-19 |
| `TIT` | Ringing | Added 2026-08-19 |
| `MSJ` | Ringing | Message — added 2026-08-19 |
| `FAM` | Ringing | Added 2026-08-19 |
| `AA` | Discard | Answering machine (auto) |
| `SINLIN` | Discard | Line disconnected |
| `DEF` | Discard | Defective number |
| `BZNDIR` | Discard | Direct voicemail |
| `FVN` | Discard | Wrong number |

**RPC (Right Party Contact)**: only determinable in Predictive, where the agent typifies the call result. `IS_RPC = 1` for COL, CTT, SALE, PDPP, CALLBK, XFER, NA.

---

## Robocall / Blaster (Vicidial-based)

Same STATUS codes and catalog as Predictive. No RPC tracking (no agent typification). Instead, `IS_MESSAGE = 1` for statuses that indicate a message was played/delivered: `XFER`, `PM`, `PU`, `SVYCLM`.

**Design decision**: `ADC` in Blaster maps to `No data` (vs `Ringing` in Predictive) because the operational meaning differs by platform. Verified against source documentation — intentional, not an inconsistency.

---

## Blaster CHOCK (alternate robocall platform)

Simpler vocabulary than classic Blaster/Predictive.

| Status | Category | Notes |
|--------|----------|-------|
| `CONTESTADA` | Answered | |
| `COMPLETADO` | Answered | IS_MESSAGE = 1 (message delivered) |
| `TRANSFERIDO` | Answered | IS_MESSAGE = 1 |
| `NO CONTESTA` | Ringing | |
| `BUZÓN` / `BUZON` | Ringing | |
| `RECHAZADA` | Ringing | Confirmed: rejected by network, not by person |

---

## MuttechMX (alternate predictive platform)

| Status | Category | Notes |
|--------|----------|-------|
| `AB` | Ringing | Busy Auto — equivalent to Vicidial AB |
| `AA` | Discard | Answering Machine Auto — equivalent to Vicidial AA |
| `OK` | Answered | 100% have duration > 0 and real agent — genuine contact |
| `ADC` | Ringing | Disconnected — aligned with Predictive ADC |
| `DROP` | Ringing | Agent not available — aligned with Predictive DROP |
| `PDROP` | Ringing | Pre-routing drop — aligned with Predictive PDROP |
| `ERI` | No data | "Agent Error" — pending business confirmation |
| `NA` | No data | "LLAMADA CONTESTADA" but 0 duration + system user (never a real agent) — ambiguous; pending confirmation. NOTE: this is MuttechMX's literal "NA" status, distinct from Vicidial's "NA" |

**pandas.read_excel note**: pandas coerces the string `"NA"` to `NaN` by default. Use `python-calamine` or `keep_default_na=False` to read this status correctly.

---

## AI calls (Calixta / calls_multiple_campaigns)

| Status | Category |
|--------|----------|
| `ANSWERED` | Answered |
| Other | Ringing |

---

## WhatsApp / chat

| Resultado | Category |
|-----------|----------|
| `RESUELTO`, `CERRADO`, `ATENDIDO`, `RESOLVED`, `CLOSED` | Answered |
| Other | Ringing |

---

## Validation

Status catalog alignment was validated against real exported TXT/CDR files from the source platforms (not assumed from documentation alone). Three intentional decisions are documented:

1. `ADC` in Blaster = `No data` (vs `Ringing` in Predictive) — platform-specific meaning
2. `COL`/`CTT` in Predictive = `Answered` — confirmed correct despite counter-intuitive codes
3. MuttechMX `NA` and `ERI` = `No data` — pending business confirmation; will be updated when confirmed
