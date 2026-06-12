# DERIVADOS — mapa de datos (Mongo + SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **DERIVADOS** (`/derivados` en acaquant-web) = **opciones financieras (GGAL)**.
> Qué muestra, de qué colección Mongo sale, quién la llena, relaciones, y qué hay
> en SQL.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable por código se
> marca `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/derivados` (`DerivadosShell` → `DerivadosView`) = la mesa
de **opciones financieras de GGAL**: cadena call/put, estrategias, payoff,
escenarios, costo histórico, griegas y un "post-trade lab".

📋 **De dónde sale todo:** una **única base Mongo `Opciones`** (5 colecciones).
Buena parte de la vista (payoff, escenarios, estrategias, lab) se calcula
**en el navegador** con Black-Scholes — sin pegar al backend.

📋 **Estado SQL (lo importante):** **DERIVADOS está 100% afuera de SQL.** Ninguna
colección de la base `Opciones` tiene tabla (verificado: 0 apariciones en
`sql/schema.sql`). El service `opciones.py` no toca Postgres.

🔎 **Hallazgo:** la vista usa la base Mongo **`Opciones`** (vía `get_db_opciones()`,
`api/db.py:9`) que **no figura en el inventario del `CLAUDE.md`** — igual que la
base `Derivados` de AGRO. La doc raíz tiene el inventario de bases incompleto.

---

## 2. Bloques de la vista y endpoints

| Bloque (panel) | ¿Pega al backend? | Endpoint | Service |
|---|---|---|---|
| **Cadena opciones GGAL** (call/put) | Sí (poll 30s) | `GET /api/cotizaciones/opciones` | `opciones.py::get_opciones` |
| **Header KPIs** (tasa, VR local/ADR) | Sí (SSR 30s) | `GET /api/cotizaciones/opciones/meta` · `PUT /opciones/tasa` (admin) | `opciones.py::get/update_opciones_meta` |
| **Estrategias** (tabla variantes) | **No** — cálculo local sobre la cadena | — | — |
| **Payoff** (P&L vs precio) | **No** — Black-Scholes en el browser | — | — |
| **Escenarios** | **No** — local | — | — |
| **Post-Trade Lab** | **No** — local | — | — |
| **Costo histórico (estrategia)** | Sí (on-demand) | `POST /api/analitica/estrategia-historico` (+ `vr-ggal`) | `opciones.py::estrategia_historico` |
| **Histórico de un contrato** | Sí (on-demand) | `GET /api/cotizaciones/historico/opciones` (+ `vr-ggal`) | `opciones.py::get_historico_opciones` |
| **Griegas histórico** | Sí (on-demand) | `GET /api/cotizaciones/griegas/opciones` | `opciones.py::get_griegas_historico` |
| **Spot GGAL diario** (2º eje charts) | Sí | `GET /api/cotizaciones/vr-ggal` | `opciones.py::get_vr_ggal_serie` |

Endpoints verificados en `api/routers/cotizaciones.py` (289–339) y
`api/routers/analitica.py` (249). 

> **Dato clave:** ~la mitad de la vista (payoff/escenarios/estrategias/lab) es
> **cálculo Black-Scholes client-side**. El backend solo sirve: cadena viva,
> históricos, griegas, meta y spot.

---

## 3. Colecciones Mongo (base `Opciones`) — quién las lee y quién las llena

**Verificado: `opciones.py` accede vía `get_db_opciones()` → base `Opciones`.**

| Colección (`Opciones.*`) | Qué es | La lee (endpoint) | La llena (verificado) |
|---|---|---|---|
| **OptionsSnapshot** | Cadena viva por símbolo (precio + griegas, recalc 5s) | `/opciones` | **`engines/options.py`** (`OptionsEngine`, bulk UpdateOne ~5s con dirty-check) |
| **Data** | Tick-level histórico (cada trade, con griegas) | `/historico/opciones`, `/estrategia-historico` | `engines/options.py` (insert por trade nuevo) |
| **DataHistorica** | Rollup diario (1 fila/día/símbolo) | `/griegas/opciones` | **`jobs/options_rollup.py`** (post-cierre, ~20:15 UTC) |
| **Metadata** | Config (tasa risk-free) + VR GGAL (local/ADR) | `/opciones/meta` | tasa: PUT admin + init motor · VR: **`jobs/volatilidad_ggal.py`** (Yahoo) |
| **VR-GGal** | Serie diaria GGAL local + ADR (~40 ruedas) | `/vr-ggal` | `jobs/volatilidad_ggal.py` (delete_many + insert_many) |

> **Filtro fresh/stale (verificado, `opciones.py:20-28`):** `get_opciones` filtra
> `updated_at >= inicio del día`. Las opciones que **no operaron hoy** conservan
> el `updated_at` de la rueda anterior → **desaparecen de la cadena viva**. Sin
> ese filtro se mezclarían strikes con datos de días previos. (Mismo patrón
> "filtrar en el service, no reescribir el motor" del incidente de opciones.)

---

## 4. Relaciones clave (verificado)

1. **Cadena ↔ griegas:** la cadena viva (`OptionsSnapshot`) trae las griegas ya
   calculadas por el motor (Black-Scholes con IV implícita). El histórico de
   griegas sale del rollup diario (`DataHistorica`).
2. **Estrategia histórica:** `estrategia_historico` agrupa `Opciones.Data` por
   buckets de 15 min y arma el costo de la estrategia con **ATM dinámico** (el
   offset de cada pata es relativo al ATM de cada bucket, no a un strike fijo).
3. **Spot de 2º eje:** los charts de costo/histórico superponen el spot GGAL
   (local/ADR) leído de `VR-GGal`.

---

## 5. Estado SQL

**Verificado por búsqueda directa en `sql/schema.sql`:**

- `Opciones.OptionsSnapshot` → **0** en schema ❌
- `Opciones.Data` → ❌
- `Opciones.DataHistorica` → ❌
- `Opciones.Metadata` → ❌
- `Opciones.VR-GGal` → ❌
- `api/services/opciones.py` lee SQL: **0** (no `get_pool`, no `_sql`).

**Conclusión:** la vista DERIVADOS **no tiene NINGÚN punto de contacto con SQL**.
Está completamente fuera de la migración. Para llevarla a SQL habría que crear de
cero tablas para las 5 colecciones de la base `Opciones` — hoy no existen.

---

## 6. Cache (verificado: `@cached` en `opciones.py`)

| Función | TTL |
|---|---|
| `get_opciones` (cadena) | 60s |
| `get_opciones_meta` | 60s |
| `get_historico_opciones` | 30s |
| `get_vr_ggal_serie` | 300s |
| `get_griegas_historico` | 120s |
| `estrategia_historico` | 60s |

Front: cadena poll 30s; el resto on-demand (al seleccionar contrato/estrategia).

---

## 7. ⚠️ Pendiente de verificar / medir en prod (NO asumido)

1. **Base `Opciones` no inventariada** en `CLAUDE.md` — corregir doc raíz.
2. **Horario del cron `jobs/volatilidad_ggal.py`** (VR GGAL) — no lo confirmé en
   `deploy/crontab.txt`. ⚠️ a verificar.
3. **Timezone de `inicio_hoy`** en el filtro de `get_opciones` (¿UTC o ART?) — el
   código usa `datetime.now(UTC)`; confirmar que el corte de "hoy" es el esperado
   por la mesa. ⚠️ a verificar.
4. **Conteos reales** de cada colección → `python -m scripts.diag_inventario_mongo_sql`.

---

## 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/derivados/page.tsx` + `derivados-shell.tsx`,
  `derivados-view.tsx`, `opciones-table-compact.tsx`, `estrategias-tabla.tsx`,
  `payoff-chart.tsx`, `escenarios-tabla.tsx`, `costo-historico-chart.tsx`,
  `opcion-historico-chart.tsx`, `griegas-historico-chart.tsx`,
  `post-trade-lab.tsx`, `lib/estrategias.ts` (Black-Scholes client-side).
- **Routers:** `api/routers/cotizaciones.py` (289–339), `api/routers/analitica.py` (249).
- **Service:** `api/services/opciones.py`. DB helper: `api/db.py::get_db_opciones`.
- **Motor:** `engines/options.py` (`OptionsEngine`).
- **Jobs:** `jobs/options_rollup.py` (rollup diario), `jobs/volatilidad_ggal.py`
  (VR GGAL), `jobs/archive_options_data.py` (purga de `Data`).
- **SQL:** ninguna tabla. (Verificado: 0 colecciones `Opciones.*` en `sql/schema.sql`.)
