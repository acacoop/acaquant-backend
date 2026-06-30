# DERIVADOS — mapa de datos (SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **DERIVADOS** (`/derivados` en acaquant-web) = **opciones financieras (GGAL)**.
> Qué muestra, de qué tabla SQL sale, quién la llena, relaciones, y cómo se
> conecta.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable por código se
> marca `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/derivados` (`DerivadosShell` → `DerivadosView`) = la mesa
de **opciones financieras de GGAL**: cadena call/put, estrategias, payoff,
escenarios, costo histórico, griegas y un "post-trade lab".

📋 **De dónde sale todo:** el schema SQL **`mercado`** (5 tablas de opciones).
Buena parte de la vista (payoff, escenarios, estrategias, lab) se calcula
**en el navegador** con Black-Scholes — sin pegar al backend.

📋 **Estado SQL (lo importante):** **DERIVADOS está 100% en SQL** (migración
completa, 2026-06-29). La vista lee SQL-native vía `core.postgres.get_pool()`;
los motores/jobs escriben SQL-native vía `core.pg_mirror`.

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

## 3. Tablas SQL (schema `mercado`) — quién las lee y quién las llena

**Verificado: `opciones.py` lee SQL vía `core.postgres.get_pool()`.**

| Tabla SQL (`mercado.*`) | Qué es | La lee (endpoint) | La llena (verificado) |
|---|---|---|---|
| **options_snapshot** | Cadena viva por símbolo (precio + griegas, recalc 5s) | `/opciones` | **`engines/options.py`** (`OptionsEngine`, ~5s con dirty-check) |
| **options_data** | Tick-level histórico (cada trade, con griegas) | `/historico/opciones`, `/estrategia-historico` | `engines/options.py` (insert por trade nuevo) |
| **options_data_hist** | Rollup diario (1 fila/día/símbolo) | `/griegas/opciones` | **`jobs/options_rollup.py`** (post-cierre, ~20:15 UTC) |
| **options_metadata** | Config (tasa risk-free) + VR GGAL (local/ADR) | `/opciones/meta` | tasa: PUT admin + init motor · VR: **`jobs/volatilidad_ggal.py`** (Yahoo) |
| **options_vr** | Serie diaria GGAL local + ADR (~40 ruedas) | `/vr-ggal` | `jobs/volatilidad_ggal.py` (reemplazo full de la serie) |

> **Filtro fresh/stale (verificado, `opciones.py:20-28`):** `get_opciones` filtra
> `updated_at >= inicio del día`. Las opciones que **no operaron hoy** conservan
> el `updated_at` de la rueda anterior → **desaparecen de la cadena viva**. Sin
> ese filtro se mezclarían strikes con datos de días previos. (Mismo patrón
> "filtrar en el service, no reescribir el motor" del incidente de opciones.)

---

## 4. Relaciones clave (verificado)

1. **Cadena ↔ griegas:** la cadena viva (`options_snapshot`) trae las griegas ya
   calculadas por el motor (Black-Scholes con IV implícita). El histórico de
   griegas sale del rollup diario (`options_data_hist`).
2. **Estrategia histórica:** `estrategia_historico` agrupa `mercado.options_data`
   por buckets de 15 min y arma el costo de la estrategia con **ATM dinámico** (el
   offset de cada pata es relativo al ATM de cada bucket, no a un strike fijo).
3. **Spot de 2º eje:** los charts de costo/histórico superponen el spot GGAL
   (local/ADR) leído de `options_vr`.

---

## 5. Estado SQL

**Migración completa (2026-06-29): la vista DERIVADOS lee SQL-native.**

- `mercado.options_snapshot` ✅
- `mercado.options_data` ✅
- `mercado.options_data_hist` ✅
- `mercado.options_metadata` ✅
- `mercado.options_vr` ✅
- `api/services/opciones.py` lee SQL vía `core.postgres.get_pool()`; los
  motores/jobs escriben SQL-native vía `core.pg_mirror`.

**Conclusión:** la vista DERIVADOS está **100% sobre SQL** (schema `mercado`). No
queda ningún punto de contacto con Mongo.

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

1. **Horario del cron `jobs/volatilidad_ggal.py`** (VR GGAL) — no lo confirmé en
   `deploy/crontab.txt`. ⚠️ a verificar.
2. **Timezone de `inicio_hoy`** en el filtro de `get_opciones` (¿UTC o ART?) — el
   código usa `datetime.now(UTC)`; confirmar que el corte de "hoy" es el esperado
   por la mesa. ⚠️ a verificar.

---

## 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/derivados/page.tsx` + `derivados-shell.tsx`,
  `derivados-view.tsx`, `opciones-table-compact.tsx`, `estrategias-tabla.tsx`,
  `payoff-chart.tsx`, `escenarios-tabla.tsx`, `costo-historico-chart.tsx`,
  `opcion-historico-chart.tsx`, `griegas-historico-chart.tsx`,
  `post-trade-lab.tsx`, `lib/estrategias.ts` (Black-Scholes client-side).
- **Routers:** `api/routers/cotizaciones.py` (289–339), `api/routers/analitica.py` (249).
- **Service:** `api/services/opciones.py`. Conexión SQL: `core.postgres.get_pool()`.
- **Motor:** `engines/options.py` (`OptionsEngine`).
- **Jobs:** `jobs/options_rollup.py` (rollup diario), `jobs/volatilidad_ggal.py`
  (VR GGAL), `jobs/archive_options_data.py` (purga de `options_data`).
- **SQL:** schema `mercado` — `options_snapshot`, `options_data`,
  `options_data_hist`, `options_metadata`, `options_vr`.
