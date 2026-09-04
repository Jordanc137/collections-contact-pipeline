# Input Formats

Format detection is always by **column headers** — never by filename or folder.
This is intentional: naming conventions for the same platform have varied across
multiple file batches in production.

---

## Format A — Systems XLSX

**Detection**: contains `DURACION SEG` AND `CATEGORIA LLAMADA` in headers.

Pre-processed export from the dialer management system. Call category is already
computed — the pipeline uses it directly without re-deriving from STATUS.

| Column | Notes |
|--------|-------|
| `FECHA` | Call date (YYYY-MM-DD or similar) |
| `PHONE NUMBER` | 10-digit phone |
| `STATUS` | Raw status code |
| `CAMPAIGN ID` | Used to derive TOOL (Blaster/Predictive/IVR by prefix) |
| `CUENTA` | Account identifier |
| `DURACION SEG` | Duration in seconds |
| `CATEGORIA LLAMADA` | Pre-computed category — used as-is |

---

## Format B — Operations XLSX

**Detection**: contains `FECHA INICIO Y HORA` AND `PHONE NUMBER` AND `STATUS`.

Daily operational export. No duration column — call category is derived from
the STATUS catalog in the pipeline.

| Column | Notes |
|--------|-------|
| `FECHA INICIO Y HORA` | Datetime string (first 10 chars = date, chars 11–12 = hour) |
| `PHONE NUMBER` | 10-digit phone |
| `STATUS` | Raw status code → looked up in CAT_PREDICTIVE / CAT_BLASTER |
| `CAMPAIGN ID` | Tool derivation |
| `CUENTA` | Account identifier |

Row-level dedup within the same file: `(FECHA INICIO Y HORA, PHONE, CAMPAIGN ID)`.

---

## Format C — Vicidial EXPORT_CALL_REPORT (TXT)

**Detection**: tab-delimited file with `call_date` AND `phone_number_dialed` in headers.

Native Vicidial export. Contains duration and full status vocabulary.

| Column | Notes |
|--------|-------|
| `call_date` | Date |
| `call_time` | Time (first 2 chars = hour) |
| `phone_number_dialed` | Phone number |
| `status` | STATUS code |
| `campaign_id` | Tool derivation |
| `lead_id` | Account identifier |
| `length_in_sec` | Duration in seconds |

---

## Format D — AI calls CSV

**Detection**: comma-delimited with `campaign_date` AND `answered_by` AND `contact_f_id`.

Calls placed by an AI agent (Calixta or equivalent). No STATUS catalog — answered/ringing
is derived from the `status` column directly (`ANSWERED` = Answered, else Ringing).

| Column | Notes |
|--------|-------|
| `campaign_date` | Date |
| `started_at` | Datetime |
| `number_to` | Phone number |
| `status` | `ANSWERED` or other |
| `campaign_name` | Campaign name |
| `contact_f_id` | Account identifier (used for cross-reference in optimization) |
| `duration` | Duration in seconds |

---

## Format E — WhatsApp / Chat CSV

**Detection**: comma-delimited with `Teléfono` AND `Resultado` AND `Fecha de inicio`.

Chat platform export (Calixta or equivalent). Contact type is derived from `Resultado`.

| Column | Notes |
|--------|-------|
| `Fecha de inicio` | Datetime string |
| `Teléfono` | Phone number |
| `Resultado` | `RESUELTO`/`CERRADO`/`ATENDIDO` = Answered, else Ringing |
| `Estatus` | Chat status |

---

## Format F — Blaster CHOCK CSV

**Detection**: comma-delimited with `Estado` AND `Duración` AND `telefono` AND `Campaign ID`.

Alternate robocall platform with its own simpler status vocabulary. The business
tool name is still "Blaster" (same as classic Blaster), but the dedup key uses
`Blaster_CHOCK` internally to allow both sources to coexist for the same date.

| Column | Notes |
|--------|-------|
| `Fecha` | Date |
| `Hora` | Time |
| `Estado` | Status (see STATUS_CATALOG.md) |
| `telefono` | Phone number |
| `Campaign ID` | Campaign identifier |
| `Duración` | Duration in seconds |

---

## Format G — MuttechMX XLSX

**Detection**: contains `FechaLlamada` AND `TelefonoMarcado` AND `Estatus` in headers.

Alternate predictive platform. Similar to Vicidial but with its own status vocabulary.
Business tool name is "Predictivo"; dedup key uses `Predictivo_MT` internally.

**pandas note**: pandas coerces the string `"NA"` to `NaN` by default. This platform
has a legitimate status code `"NA"` (distinct from Vicidial's `"NA"`). Always read
these files with `python-calamine` or `keep_default_na=False`.

| Column | Notes |
|--------|-------|
| `FechaLlamada` | Date |
| `TelefonoMarcado` | Phone number |
| `Estatus` | Status (see STATUS_CATALOG.md) |
| `DuracionMinutos` | Duration in minutes (converted to seconds: × 60) |
| `NombreUsuario` | Agent name (`"Sistema"` = automated, no real agent) |

---

## Assignment files

Daily portfolio assignment files — the ground truth for which accounts are active
on a given day.

**Detection**: filename contains `asig` (case-insensitive) + 8-digit date (`DDMMYYYY`).

| Column | Notes |
|--------|-------|
| `idUnico` | Unique account identifier (always treated as string) |
| `idCampania` | Campaign ID (integer) |
| `telefono1`…`telefono4` | Up to 4 phone numbers per account |
| `nombre`, `saldo`, etc. | Account metadata |

The pipeline tries to read `.csv` (utf-8-sig, then latin1) and `.xlsx`.

---

## Payment files

Two payment source types:

**RPT payments** (folder `4.-PAGOS`):
Columns: `idUnico`, `abonoTotal`, `montoRequerido`, `idCampania`

**Cut-time payments** (folder `PAGOS X CORTE`):
Same schema but with an hour column — used for hourly payment analysis.

Payment files for the same date from both sources are combined without double-counting.
