# AUM — PROBLEMAS Y SOLUCIONES (bitácora del incidente)

> **Estado: 🔴 ABIERTO** · Apertura: **2026-06-12** · Última actualización: **2026-06-13**
>
> Bitácora viva del incidente de datos de **AuM / Tenencia / Valuaciones**. Se
> documenta TODO: detecciones, hipótesis, pruebas, errores, intentos y avances,
> con fecha y hora, hasta resolverlo. **No se borra hasta que esté solucionado.**

---

## 1. Resumen del problema

Se detectó que **datos de tenencia valorizada están mal cargados** y el descalce
es potencialmente **generalizado** (afecta muchas cuentas/especies). Dos síntomas:

1. **Corrimiento de fecha**: la tenencia valorizada de una fecha no coincide con
   el sistema contable — los datos están corridos **un día hábil**.
2. **Tenencias congeladas**: hay especies cuya valuación queda **idéntica todos
   los días** (detectado en GD35, cuenta 255), cuando en el sistema contable
   cambia día a día.

Es **grave** porque `Valuaciones.AuM` es la fuente de **Carteras, AuM, PnL Títulos,
Tenencia HD (Back Office) y la nueva Valuaciones (flujo)**. Si el AuM está mal, todo
lo que lee de ahí está mal.

---

## 2. Hallazgos CONFIRMADOS (medidos, no supuestos)

### H1 — Aunesa corre la fecha: `desde=X` devuelve el día hábil ANTERIOR  ✅ CONFIRMADO (2026-06-12)
En el endpoint `GET /api/cuentas/{id}/posicionValuada`, el parámetro **`desde=X`
devuelve la posición del día hábil ANTERIOR a X**. Para tener la posición **al
día D, hay que pedir `desde = D + 1 día hábil`**.
- **Cómo se confirmó:** se consultó la cuenta 805 para fechas hábiles consecutivas
  (27/05, 28/05, 29/05, 01/06, 02/06) comparando una "huella" de cada respuesta
  (`scripts/diag_aunesa_255.py`) y se cruzó contra el sistema contable. El `desde`
  que coincidía con el contable era el del día SIGUIENTE al buscado.
- (Ej.: para ver el cierre del 29/05 hay que pedir `desde=01/06`.)

### H2 — El BACKFILL histórico no compensa ese corrimiento  ✅ CONFIRMADO (código)
`jobs/aum_backfill_historico.py` pide `desde = último día del mes` (formato
DD/MM/YYYY) **sin T+2** y guarda con `fecha_snapshot = ese mismo día`. Como
`desde=X` trae X-1 (ver H1), **cada snapshot mensual queda etiquetado con la fecha
de un día pero contiene la posición del día hábil anterior**. → La historia
mensual de AuM está **corrida ~1 día hábil para atrás**.

### H3 — El DIARIO usa T+2  ⚠️ A VERIFICAR si compensa o no
`jobs/aum.py` pide `desde = fecha_t2()` (T+2 días hábiles adelante) y guarda con
`fecha_snapshot = HOY`. **Falta confirmar** si el T+2 compensa el corrimiento de
H1 (y queda bien) o si también está corrido. Herramienta: `scripts/diag_aunesa_job_diario.py`.

---

## 3. Hallazgos EN INVESTIGACIÓN

### I1 — Tenencias congeladas (valuación idéntica día a día)  🔬 INVESTIGANDO
Detectado por el usuario: **GD35 en la cuenta 255 figura idéntico todos los días**
en la app, pero en el sistema contable cambia. Hipótesis: el **precio** (y por ende
la valuación) no se está actualizando en los snapshots diarios. Si el precio de un
bono no cambia día a día, es un bug seguro.
- **Herramienta:** `scripts/diag_aum_congelado.py` mide cuántas especies de una
  cuenta tienen PRECIO / VALUACIÓN / CANTIDAD idénticos en TODOS sus snapshots.
- **PENDIENTE:** correrlo en 255 y 805 y pegar resultados acá.

---

## 4. Impacto (qué se rompe si esto es real)

`Valuaciones.AuM` (Mongo) alimenta:
- **Carteras** (`/valuaciones`) → lee Mongo directo. Afectado.
- **AuM** (`/aum`) → Mongo por default; SQL si `PORTFOLIO_SQL=1`. La tabla SQL `aum`
  es **espejo** del Mongo (mismas fechas) → **leer de SQL no salva**.
- **PnL Títulos**, **Tenencia HD** (Back Office), **Valuaciones (flujo)** → todos
  derivan del AuM.

---

## 5. Bitácora cronológica

**2026-06-12**
- Detección inicial: la **Tenencia Valorizada HD al 29/05** (cuenta 255) no coincide
  con el sistema contable.
- Hipótesis inicial: *freeze* stale (el AuM se corrigió después de congelar el día).
  → diag `scripts/diag_tenencia_hd_fecha.py` (congelado vs recalculado).
- Se sospecha de la fecha de consulta a Aunesa. Se arma `scripts/diag_aunesa_255.py`
  para consultar la API cruda por fecha.
- Se descubre que la API trae filas de más → se filtra a `informacion == "Acumulado"`
  (igual que el job) y se agrega "huella" por fecha para comparar días.
- **CONFIRMADO (H1):** con la cuenta 805, `desde=X` devuelve el día hábil anterior.
- **Detección 2 (I1):** GD35 (255) idéntico todos los días → posible congelamiento
  generalizado. Se arma `scripts/diag_aum_congelado.py`.
- Se crea esta bitácora (23:03).
- **PENDIENTE:** correr `diag_aum_congelado` (255 y 805); verificar el diario (H3);
  definir el fix y si se re-backfillea la historia.

**2026-06-13**
- Corrido `diag_aum_congelado --cuenta 255`. Resultado: **63 unidades** con ≥2 snapshots.
  - **PRECIO idéntico todos los días: 7** → causa: ver H4 (no mandamos "Actualizar cotizaciones").
  - **VALUACIÓN idéntica: 6.**
  - **CANTIDAD idéntica: 15** → puede ser buy&hold (normal) o bug si esas especies operaron.
- **H4 CONFIRMADO (hipótesis del usuario):** el precio congelado se explica porque la
  web tiene el toggle **"Actualizar cotizaciones"** que fuerza el refresco, y **nosotros
  NO lo mandamos** en la consulta → algunas especies traen precio cacheado/viejo.
  Fix simple: agregar ese parámetro. (Prioridad menor según el usuario.)
- **Foco ahora:** la CANTIDAD congelada (¿bug o tenencia normal?). Se arma
  `scripts/diag_aum_cantidad_vs_movs.py` para cruzar las unidades de cantidad plana
  contra los boletos reales (NegocioMovimientos) y ver si operaron sin actualizarse.
  **PENDIENTE correrlo en 255.**
- **Test de fetch eficiente** (`test_portafolio_fetch` al 30/05, todas las cuentas):
  OK=1046, TIMEOUT=1, ERROR=751 → **los 751 "error" son cuentas SIN posición (HTTP 204)**,
  normal, no error real. Las pesadas (106=104s, 255=62s…) resolvieron con timeout 240s
  sin tumbar al resto. Validado el approach paralelo + timeout adaptativo.
- **SOLUCIÓN elegida (en marcha):** schema SQL nuevo **`portafolio`** que guarda la
  tenencia por **fecha REAL** (regla: `desde = D + 1 día hábil` → `fecha = D`, sin
  adivinar). Job `jobs/portafolio_backfill.py` (self-healing, resumable, bulk, sin
  exclusiones, con NOMINALES). **PENDIENTE:** correr backfill jun 01→17 y validar
  contra el contable; después job diario + frontend que muestre nominales.

---

## 6. Herramientas creadas (diags, read-only)

| Script | Qué hace |
|---|---|
| `scripts/diag_tenencia_hd_fecha.py` | Tenencia HD de un día: congelado vs recalculado contra AuM actual + diff. |
| `scripts/diag_aunesa_255.py` | Consulta cruda `posicionValuada` (`--cuenta`, multi-fecha) + huella por fecha para detectar el corrimiento. |
| `scripts/diag_aunesa_job_diario.py` | Replica el job DIARIO corriendo ahora (`desde=fecha_t2`, etiqueta hoy) — para verificar H3. |
| `scripts/diag_aum_congelado.py` | Detecta tenencias congeladas (precio/valuación idéntico día a día) por cuenta. |
| `scripts/diag_aum_cantidad_vs_movs.py` | Cruza las unidades de cantidad congelada contra NegocioMovimientos: distingue bug (operó y no cambió) de buy&hold normal. |
| `docs/aunesa_postman_collection.json` | Colección Postman con TODOS los endpoints de Aunesa (login, listadoCuentas, posicionValuada, consolidadosGenerales, informes). |

---

## 7. Próximos pasos

1. **Medir alcance del congelamiento** → correr `diag_aum_congelado` en 255 y 805 (y alguna comitente).
2. **Verificar el diario (H3)** → `diag_aunesa_job_diario` + cruzar contra contable de hoy.
3. **Definir el fix del `desde`** (sumar +1 día hábil) consistente entre diario y backfill.
4. **Decisión:** re-backfillear la historia de AuM corregida (afecta Carteras/PnL/Tenencia histórica) vs arreglar de acá en adelante.
5. **Cerrar:** validar 2-3 cuentas contra el sistema contable; recién ahí pasar el estado a ✅ RESUELTO.

---

> **Cómo se actualiza esta bitácora:** cada prueba/avance/error nuevo se agrega en
> §5 (Bitácora) con fecha y hora, y si confirma o descarta algo se mueve a §2/§3.
> No se borra nada hasta el cierre.
