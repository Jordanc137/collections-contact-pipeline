# Guía: publicar el proyecto en GitHub

---

## Paso 1 — Crear el repositorio

1. Ve a **github.com/new**
2. Nombre del repo: `collections-contact-pipeline`
3. Descripción (este campo aparece en búsquedas y en tu perfil):
   ```
   Production ETL pipeline: 7-channel CDR ingestion, incremental Parquet I/O, Wilson-score contact optimization. Runs daily for a 13k–17k account collections operation.
   ```
4. **Public** ✓
5. No inicialices con README (ya tienes el tuyo)
6. Licencia: MIT (o selecciónala, ya está declarada en el README)
7. Click **Create repository**

---

## Paso 2 — Subir el código

En tu terminal, desde la carpeta del proyecto:

```bash
cd collections-contact-pipeline

git init
git add .
git commit -m "Initial release: production collections analytics pipeline"

git remote add origin https://github.com/TU_USUARIO/collections-contact-pipeline.git
git branch -M main
git push -u origin main
```

---

## Paso 3 — Topics (etiquetas) del repositorio

En la página del repo, haz click en el ícono de engrane al lado de "About" (arriba a la derecha).

Agrega estos topics — son exactamente las palabras que buscan reclutadores técnicos:

```
python  etl  data-pipeline  pandas  parquet  pyarrow  analytics
collections  fintech  data-engineering  operations  openpyxl
```

---

## Paso 4 — Releases

Publica una release para que el repo se vea activo y profesional:

1. En el repo, click en **Releases** → **Create a new release**
2. Tag: `v1.0.0`
3. Title: `v1.0.0 — Production release`
4. Description:
   ```
   Initial public release of the Collections Contact Analytics Pipeline.

   - 7-format CDR ingestion with header-based detection
   - Incremental (DATE, TOOL) deduplication
   - Streaming Parquet I/O — memory bounded to 100k-row batches
   - Parallel pipeline execution via ThreadPoolExecutor
   - Wilson-score optimal contact hour analysis
   - 4-sheet daily Excel: tool recommendation + age channel + payment scoring
   - Full documentation: ARCHITECTURE.md, INPUT_FORMATS.md, STATUS_CATALOG.md
   ```
5. **Publish release**

---

## Paso 5 — Pinear el repo en tu perfil

1. Ve a tu perfil de GitHub (`github.com/TU_USUARIO`)
2. Click en **Customize your pins**
3. Selecciona `collections-contact-pipeline`
4. Guarda

Este repo debe ser el primero que vea cualquiera que visite tu perfil.

---

## Paso 6 — README de perfil (si no tienes uno)

GitHub permite tener un repo especial `TU_USUARIO/TU_USUARIO` que aparece en tu perfil como una tarjeta de presentación. Si no tienes uno:

1. Crea un repo nuevo con tu mismo nombre de usuario
2. Marca "Add a README file"
3. Pon en ese README algo como:

```markdown
## Jordan Breña

**Data Analytics Engineer** · Python · ETL Pipelines · Collections Analytics

Construyo pipelines de datos para operaciones de cobranza:
ingesta incremental, enriquecimiento con asignaciones diarias,
dashboards HTML y reportes Excel accionables.

**Proyecto destacado:**
→ [collections-contact-pipeline](https://github.com/TU_USUARIO/collections-contact-pipeline)
  — Pipeline de CDR multi-plataforma para una cartera de 13k–17k cuentas diarias.
  7 parsers de formato, I/O Parquet en streaming, optimización por Wilson score.
```

---

## Paso 7 — Lo que los reclutadores van a ver

Cuando un reclutador técnico o un ingeniero de datos visite el repo, el README que construimos les muestra en orden:

1. **El headline** — "production-grade", "13,000–17,000 accounts", "solo" → credibilidad inmediata
2. **Qué problema resuelve** — en lenguaje de negocio, no técnico
3. **La tabla de escala** — números concretos son más convincentes que adjetivos
4. **El diagrama de arquitectura** — demuestra pensamiento de sistemas
5. **Los 7 problemas de ingeniería con solución** — cada uno tiene código real → demuestra que saben debuggear y optimizar en producción, no solo escribir código que funciona en dev
6. **La tabla de principios** — cada principio tiene un origen en una falla real → demuestra madurez de ingeniería

---

## Lo que NO debes hacer

- No pongas screenshots del dashboard (contienen datos reales)
- No subas ningún `.csv`, `.xlsx`, `.parquet` aunque sea de prueba
- No subas `config.py` (tiene tus rutas locales — el `.gitignore` ya lo excluye)
- No subas `IFT_Plan_Numeracion.csv` (es un archivo de 178k filas que no aporta al repo)

El `.gitignore` ya cubre todo esto.

---

## Frases para LinkedIn cuando compartas el link

**Opción A (técnica):**
> Publiqué en GitHub el pipeline de analítica que construí y mantengo en producción. Ingesta incremental de CDR de 7 plataformas de marcación, I/O Parquet en streaming (fix de 4 GB → batch de 100k filas), ejecución paralela por ThreadPoolExecutor, y análisis de hora óptima de contacto con Wilson score CI. Corre diario sobre una cartera de 13k–17k cuentas.
> 🔗 [link del repo]

**Opción B (de negocio):**
> Automaticé el análisis de intensidad de contacto para un equipo de cobranza. Antes: 7 fuentes de datos incompatibles, ninguna vista unificada. Ahora: un script, un dashboard, y un Excel accionable cada mañana. Lo construí solo, desde cero, en producción.
> 🔗 [link del repo]

Usa la A si tu audiencia es técnica (ingenieros, data engineers, CTOs).
Usa la B si tu audiencia es de negocio (gerentes, directores de operaciones, reclutadores no técnicos).

---

## Keywords que puedes agregar a tu LinkedIn

En la sección de Skills de LinkedIn, agrega:
- Data Engineering
- ETL Pipeline Development
- Python (Advanced)
- Apache Parquet / pyarrow
- Collections Analytics
- Operations Analytics
- Data Pipeline Architecture
