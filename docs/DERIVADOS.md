# DERIVADOS — futuros, sintéticos y agro

> **Un doc por dominio.** Consolidó a `DERIVADOS.md`, `SINTETICOS.md` y `AGRO.md`
> el 2026-08-31. Los tres describían la misma familia —posiciones armadas con
> futuros ROFEX— y separarlos hacía que el mismo concepto (la tasa implícita, el
> plazo, la pizarra) se explicara tres veces y drifteara distinto en cada copia.


---

# PARTE A — Derivados (futuros DLR y tasa implícita)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **DERIVADOS** (`/derivados` en acaquant-web) = **opciones financieras (GGAL)**.
> Qué muestra, de qué tabla SQL sale, quién la llena, relaciones, y cómo se
> conecta.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable por código se
> marca `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.

---

### 1. Resumen ejecutivo

📋 **Qué es la vista:** `/derivados` (`DerivadosShell` → `DerivadosView`) = la mesa
de **opciones financieras de GGAL**: cadena call/put, estrategias, payoff,
escenarios, costo histórico y griegas.

📋 **De dónde sale todo:** el schema SQL **`mercado`** (5 tablas de opciones).
Buena parte de la vista (payoff, escenarios, estrategias) se calcula
**en el navegador** con Black-Scholes — sin pegar al backend.

📋 **Estado SQL (lo importante):** **DERIVADOS está 100% en SQL** (migración
completa, 2026-06-29). La vista lee SQL-native vía `core.postgres.get_pool()`;
los motores/jobs escriben SQL-native vía `core.pg_mirror`.

---

### 2. Bloques de la vista y endpoints

| Bloque (panel) | ¿Pega al backend? | Endpoint | Service |
|---|---|---|---|
| **Cadena opciones GGAL** (call/put) | Sí (poll 30s) | `GET /api/cotizaciones/opciones` | `opciones.py::get_opciones` |
| **Header KPIs** (tasa, VR local/ADR) | Sí (SSR 30s) | `GET /api/cotizaciones/opciones/meta` · `PUT /opciones/tasa` (admin) | `opciones.py::get/update_opciones_meta` |
| **Estrategias** (tabla variantes) | **No** — cálculo local sobre la cadena | — | — |
| **Payoff** (P&L vs precio) | **No** — Black-Scholes en el browser | — | — |
| **Escenarios** | **No** — local | — | — |
| **Costo histórico (estrategia)** | Sí (on-demand) | `POST /api/analitica/estrategia-historico` (+ `vr-ggal`) | `opciones_sql.py::estrategia_historico` — ticks de hoy (`options_data`) + un cierre por día de los últimos 21 (`options_data_hist`) |
| **Histórico de un contrato** | Sí (on-demand) | `GET /api/cotizaciones/historico/opciones` (+ `vr-ggal`) | `opciones_sql.py::get_historico_opciones` — ticks de hoy (`options_data`) + un cierre por día de la vida del contrato (`options_data_hist`) |
| **Griegas histórico** | Sí (on-demand) | `GET /api/cotizaciones/griegas/opciones` | `opciones.py::get_griegas_historico` |
| **Spot GGAL diario** (2º eje charts) | Sí | `GET /api/cotizaciones/vr-ggal` | `opciones.py::get_vr_ggal_serie` |

Endpoints verificados en `api/routers/cotizaciones.py` (289–339) y
`api/routers/analitica.py` (249). 

> **Dato clave:** ~la mitad de la vista (payoff/escenarios/estrategias) es
> **cálculo Black-Scholes client-side**. El backend solo sirve: cadena viva,
> históricos, griegas, meta y spot.

---

### 3. Tablas SQL (schema `mercado`) — quién las lee y quién las llena

> **Trampa que costó una tarde:** `options_data` (ticks) la purga `archive_options_data` todas
> las noches (20:50 UTC) y deja SOLO la rueda vigente. Ningún histórico intradía tiene más de
> 1-2 días. Los días anteriores salen de `options_data_hist` (rollup de `options_rollup`, una
> fila por contrato y día con high/low/last/ev/griegas/spot): los dos endpoints de histórico
> suman un punto de cierre (17:00) por día a los ticks de hoy, sin duplicar los días que sí
> tienen intradía.

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

### 4. Relaciones clave (verificado)

1. **Cadena ↔ griegas:** la cadena viva (`options_snapshot`) trae las griegas ya
   calculadas por el motor (Black-Scholes con IV implícita). El histórico de
   griegas sale del rollup diario (`options_data_hist`).
2. **Estrategia histórica:** `estrategia_historico` agrupa `mercado.options_data`
   por buckets de 15 min y arma el costo de la estrategia con **ATM dinámico** (el
   offset de cada pata es relativo al ATM de cada bucket, no a un strike fijo).
3. **Spot de 2º eje:** los charts de costo/histórico superponen el spot GGAL
   (local/ADR) leído de `options_vr`.

---

### 5. Estado SQL

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

### 6. Cache (verificado: `@cached` en `opciones.py`)

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

### 7. ⚠️ Pendiente de verificar / medir en prod (NO asumido)

1. **Horario del cron `jobs/volatilidad_ggal.py`** (VR GGAL) — no lo confirmé en
   `deploy/crontab.txt`. ⚠️ a verificar.
2. **Timezone de `inicio_hoy`** en el filtro de `get_opciones` (¿UTC o ART?) — el
   código usa `datetime.now(UTC)`; confirmar que el corte de "hoy" es el esperado
   por la mesa. ⚠️ a verificar.

---

### 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/derivados/page.tsx` + `derivados-shell.tsx`,
  `derivados-view.tsx`, `opciones-table-compact.tsx`, `estrategias-tabla.tsx`,
  `payoff-chart.tsx`, `escenarios-tabla.tsx`, `costo-historico-chart.tsx`,
  `opcion-historico-chart.tsx`, `griegas-historico-chart.tsx`,
  `lib/estrategias.ts` (Black-Scholes client-side).
- **Routers:** `api/routers/cotizaciones.py` (289–339), `api/routers/analitica.py` (249).
- **Service:** `api/services/opciones.py`. Conexión SQL: `core.postgres.get_pool()`.
- **Motor:** `engines/options.py` (`OptionsEngine`).
- **Jobs:** `jobs/options_rollup.py` (rollup diario), `jobs/volatilidad_ggal.py`
  (VR GGAL), `jobs/archive_options_data.py` (purga de `options_data`).
- **SQL:** schema `mercado` — `options_snapshot`, `options_data`,
  `options_data_hist`, `options_metadata`, `options_vr`.

---

# PARTE B — Sintéticos

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **SINTÉTICOS** (`/sinteticos` en acaquant-web): tasa implícita (TNA/TE) de
> sintéticos armados con LECAP+Rofex y DLK+Rofex. Qué muestra, de qué tabla
> SQL sale, relaciones y cómo se persiste.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable se marca
> `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.
> Actualizado tras el **decomiso total de Mongo (2026-06-29)**: todo lee/escribe
> SQL-native (Postgres/Supabase).

---

### 1. Resumen ejecutivo

📋 **Qué es la vista:** `/sinteticos` (`DerivadosSinteticosView`): dos tablas y dos
gráficos de **tasa sintética** —
**(a) Long Rofex − Long LECAP** y **(b) Short Rofex − Long DLK**— con su TE/TNA y
la curva de TNA por plazo.

📋 **De dónde sale todo:** **un solo endpoint** (`GET /api/derivados/sinteticos`)
que **calcula en vivo** combinando 3 tablas SQL del schema `mercado` + el dólar
oficial. No hay datos "sintéticos" crudos: el sintético es un **cálculo** sobre
futuros DLR + bonos + precios vivos.

📋 **Estado SQL (lo importante):** **SQL-native (migración COMPLETA).** El service
`sinteticos.py` lee SQL (conexión `core.postgres.get_pool()`, vía los helpers
`*_sql.py` / `core`). Las tablas de entrada son `mercado.curvas` y
`mercado.market_snapshot` (heredadas de RENTA FIJA) más `mercado.futuros_dlr_snapshot`
(la pata Rofex viva). El histórico se persiste en `mercado.snapshots_sinteticos`
vía `core.pg_mirror`.

---

### 2. Endpoint y bloques

> **Router:** `api/routers/derivados_sinteticos.py` (`GET /api/derivados/sinteticos`).
> El front pega vía proxy `/api/derivados-sinteticos`. **Poll: 5s.** Acceso
> público (todos los roles).

| Bloque (en pantalla) | Qué muestra | Endpoint |
|---|---|---|
| **Header** | Spot + fuente + último update | `GET /api/derivados/sinteticos` |
| **Tabla Long Rofex − Long LECAP** | ticker, futuro, px, vto, plazo, descalce, TE, TNA | (mismo) |
| **Tabla Short Rofex − Long DLK** | ticker DLK, futuro, px, DLR ajuste, vtos, TE, TNA | (mismo) |
| **Curva TNA (Long LECAP)** | TNA % vs plazo (días), `descalce==0` | (mismo) |
| **Curva TNA (Short DLK)** | TNA % vs plazo | (mismo) |

**Toda la vista se sirve de UN endpoint.** Service: `sinteticos.py::get_sinteticos`
(verificado, `api/services/sinteticos.py:82-182`, cache 5s).

---

### 3. Tablas SQL — quién las lee y quién las llena (verificado)

`get_sinteticos` lee del schema `mercado` (conexión `core.postgres.get_pool()`):

| Tabla SQL | Schema | Qué aporta al cálculo | La llena (verificado) |
|---|---|---|---|
| **futuros_dlr_snapshot** | `mercado` | Precio vivo de los futuros DLR (la pata Rofex) | **`engines/futuros_dlr.py`** |
| **curvas** (`curva="tasa_fija"`) | `mercado` | LECAPs: `flujo_vencimiento`, vto (la pata LECAP) | maestro editable |
| **curvas** (`curva="dolar_linked"`) | `mercado` | DLK: `dolar_emision`, vto (la pata DLK) | maestro editable |
| **market_snapshot** | `mercado` | `metrics.last_price` de cada LECAP/DLK | motores rofex + curvas |
| *(spot)* | — | Dólar oficial vía `mid_oficial_live("oficial")` (MAE mayorista) | script local MAE |

**Histórico:** `jobs/snapshot_sinteticos.py` (cron **20:40 UTC** L-V) llama a
`get_sinteticos()` y persiste el resultado en **`mercado.snapshots_sinteticos`**
(1 fila por ts/tipo/ticker) vía `core.pg_mirror`, para la serie temporal de TNA/TE.

---

### 4. Relaciones / lógica del cálculo (verificado)

El sintético **empareja por (año, mes) de vencimiento** la pata de futuro DLR con
la pata de bono (LECAP o DLK), y calcula la tasa con el spot oficial:

- **Long Rofex − Long LECAP:** `TE = (cobro/px_futuro) / (px_lecap/spot) − 1`;
  `TNA = TE × 365/plazo`.
- **Short Rofex − Long DLK:** `TE = (100×px_futuro/dolar_emision) / px_dlk − 1`;
  `TNA = TE × 365/plazo`.

Las filas con **descalce ≠ 0** (vencimientos que no matchean) se muestran en
tabla pero se **excluyen de la curva TNA**.

> **Dependencia clave:** el sintético no existe sin las 3 fuentes vivas. Si el
> motor `futuros_dlr` o el que escribe `market_snapshot` están stale (motor caído),
> la tasa sale vieja.

---

### 5. Estado SQL

**Verificado (migración COMPLETA, decomiso Mongo 2026-06-29):**
- `api/services/sinteticos.py` lee SQL vía `core.postgres.get_pool()` (helpers
  `*_sql.py` / `core`).
- Entrada viva: `mercado.curvas`, `mercado.market_snapshot` (heredadas de RENTA
  FIJA) y `mercado.futuros_dlr_snapshot` (pata Rofex). El histórico de cierre de
  futuros DLR vive en `mercado.futuros_dlr_snapshot`.
- Histórico del sintético: `mercado.snapshots_sinteticos`, escrito vía
  `core.pg_mirror`.

**Conclusión:** la vista SINTÉTICOS se calcula 100% SQL-native; su resultado
(vivo e histórico) está espejado en SQL. No queda nada atado a Mongo.

---

### 6. Cache

`get_sinteticos` → `@cached(ttl=5)` (misma cadencia que los motores). Front poll 5s.

---

### 7. ⚠️ Pendiente de verificar / medir en prod

1. **Poblamiento de `mercado.curvas`** (de dónde entran LECAPs/DLKs): es un maestro
   editable; el engine/ingest exacto no se confirmó línea a línea. No afecta el
   mapa de la vista. ⚠️ a verificar.
2. **Conteos reales** (¿`mercado.snapshots_sinteticos` tiene historia?, ¿cuántos
   futuros DLR vivos?) → consultar SQL.

---

### 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/sinteticos/page.tsx` +
  `derivados-sinteticos-view.tsx`.
- **Router:** `api/routers/derivados_sinteticos.py`.
- **Service:** `api/services/sinteticos.py`.
- **Motores/jobs:** `engines/futuros_dlr.py` (futuros DLR vivos →
  `mercado.futuros_dlr_snapshot`), `engines/valores.py` + `engines/curvas.py`
  (`mercado.market_snapshot`), `jobs/snapshot_sinteticos.py` (histórico
  `mercado.snapshots_sinteticos` vía `core.pg_mirror`).
- **SQL:** `mercado.curvas`, `mercado.market_snapshot`,
  `mercado.futuros_dlr_snapshot`, `mercado.snapshots_sinteticos`.

---

# PARTE C — Agro (pase, pizarra, opciones sobre commodities)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **AGRO** (`/agro` en acaquant-web): qué muestra, de qué tabla SQL sale
> cada dato, quién la llena, cómo se relacionan.
>
> **Método.** Cada afirmación fue verificada leyendo archivo:línea (routers,
> services, motores, `sql/schema.sql`). Lo no verificable por código se marca
> `⚠️ a verificar`. Lo que requiere medir en prod está al final. No se asumió nada.
>
> Relevamiento: **2026-06-12**. Actualizado **2026-06-29** (decomiso total de
> Mongo: AGRO lee/escribe SQL-native).

---

### 1. Resumen ejecutivo

📋 **Qué es la vista:** `/agro` (componente `AgroShell`) con **3 pestañas**:
**Mercado** (futuros + opciones + simulador de cobertura + pizarra de pases),
**Mejoras Precio Dispo** (LECAPs para mejorar el precio disponible), y **Datos**
(carga manual de la Cámara Arbitral de Cereales).

📋 **De dónde sale todo:** SQL (Postgres/Supabase), todo bajo el schema
**`mercado`**: snapshots de mercado (`agro_snapshot`, `agro_opciones_snapshot`,
`futuros_dlr_snapshot`), datos cargados a mano por la mesa (`agro_pizarra`,
`camara_cereales`) y el volumen agro (`volumen_mercado_agro`, denominador del
market share que vive en otra vista). El market share cruza además
`operaciones.operaciones` y `clientes.comitentes`.

📋 **Estado SQL (lo importante):** **AGRO migró por completo a SQL** (decomiso de
Mongo, 2026-06-29). Todas sus tablas propias (`mercado.agro_snapshot`,
`mercado.agro_opciones_snapshot`, `mercado.futuros_dlr_snapshot`,
`mercado.agro_pizarra`, `mercado.camara_cereales`, `mercado.volumen_mercado_agro`)
y las compartidas (`mercado.curvas`, `mercado.market_snapshot`) viven en SQL. Los
motores escriben SQL-native (vía `core.pg_mirror`) y los services leen SQL.

---

### 2. Las 4 pestañas y sus endpoints

> **Backend real:** los endpoints viven bajo el prefijo `/api/derivados/agro*`
> (router `api/routers/derivados_agro.py`). El front los llama vía proxy con el
> alias `/api/derivados-agro/*`. Acá uso la ruta **backend real**.

| Pestaña | Bloque | Endpoint backend | Service |
|---|---|---|---|
| **Mercado** | Futuros agro | `GET /api/derivados/agro` (poll 5s) | `derivados_agro.py::get_pase_agro` |
| **Mercado** | Pizarra de pases | `GET /api/derivados/agro` + `PATCH /agro/pizarra/{commodity}` | `derivados_agro.py` |
| **Mercado** | Cadena de opciones | `GET /api/derivados/agro/opciones/{commodity}` (poll 5s) | `derivados_agro.py::get_panel_opciones` |
| **Mercado** | Simulador cobertura | `POST /api/derivados/agro/estrategia/simular` | `derivados_agro.py::simular_estrategia` |
| **Mejoras Precio Dispo** | LECAPs por commodity | `GET /api/derivados/agro/mejoras-dispo` (poll 5s) | `mejoras_dispo.py::get_mejoras_dispo` |
| **Chicago** | Futuros CBOT (feed Eikon oficina) | `GET /api/derivados/agro/chicago` (poll 10s) | `core/eikon_chicago.py::tablero_chicago` |
| **Datos** | Cámara Arbitral (carga manual) | `GET /api/derivados/agro/camara` (poll 10s) + `PATCH /agro/camara/{cereal}` | `camara_cereales.py` |

> **Chicago (2026-07-24):** 5 familias CBOT (Soja `Sc1-5` / Aceite `BOc1-6` /
> Maíz `Cc1-5` / Trigo `Wc1-5` / Harina `SMc1-6`; Aceite y Harina saltean la
> posición 3), precios en **USD/tonelada** (factores server-side en
> `core/eikon_chicago.py::FAMILIAS`; la tabla `mercado.eikon_chicago_snapshot`
> guarda crudo ¢/bu·¢/lb·USD/st). Los datos entran SOLO cuando el user prende
> el feed Eikon de oficina (mismo script que la RV internacional — ver
> `docs/RENTA_VARIABLE.md` parte B); con el feed apagado queda la última foto con
> su hora. La vista es una grilla de 5 tablas (Mes / Precio USD-t / Var),
> componente `agro-chicago.tsx`. Visible también para el invitado (mercado).

Endpoints verificados en `api/routers/derivados_agro.py`.

> **Nota — Market share AGRO NO está en esta vista.** El gráfico de participación
> de mercado (`GET /api/operaciones/ops/agro`) pertenece a la vista
> **OPERACIONES** (`agro-view.tsx`, otro árbol de componentes), no a `/agro`. Se
> documenta en el doc de Operaciones. Acá solo se menciona la relación.

---

### 3. Tablas SQL — quién las lee y quién las llena (verificado)

| Tabla | Schema | Qué es | La lee | La llena (verificado) |
|---|---|---|---|---|
| **agro_snapshot** | `mercado` | Futuros agro vivos (~24 filas) | derivados_agro, mejoras (indirecto) | **`engines/motor_agro.py`** (upsert cada ~5s) |
| **agro_opciones_snapshot** | `mercado` | Cadena de opciones agro viva | derivados_agro | **`engines/motor_agro_opciones.py`** (upsert cada ~5s) |
| **futuros_dlr_snapshot** | `mercado` | Futuros DLR vivos | mejoras_dispo | **`engines/futuros_dlr.py`** (limpieza: `jobs/cleanup_futuros_dlr.py`) |
| **curvas** | `mercado` | Maestro de bonos (LECAPs para mejoras) | mejoras_dispo | maestro editable |
| **market_snapshot** | `mercado` | TEA viva de cada LECAP | mejoras_dispo | motores rofex + curvas |
| **agro_pizarra** | `mercado` | Precio pizarra USD por commodity (3 filas) | derivados_agro | **MANUAL** (PATCH desde la mesa) |
| **agro_pizarra_audit** | `mercado` | Log de cambios de la pizarra | — | escrito en cada PATCH |
| **camara_cereales** | `mercado` | Precios Cámara Rosario (5 cereales) | camara_cereales, mejoras_dispo, derivados_agro | **MANUAL** (PATCH desde la mesa) |
| **camara_cereales_audit** | `mercado` | Log de cambios de la cámara | — | escrito en cada PATCH |

> **Dato clave de diseño:** la **Pizarra** y la **Cámara** NO las alimenta ningún
> motor — son **carga manual de la mesa** (PATCH con `require_module("agro")`),
> con tablas de auditoría (`*_audit`) que registran `updated_by` + `updated_at`.

---

### 4. Relaciones clave (verificado)

1. **Pase agro:** `get_pase_agro` cruza `mercado.agro_snapshot` (futuros vivos en
   USD) + `mercado.agro_pizarra` (precio pizarra manual) + `mercado.camara_cereales`
   (precio USD de cámara) para armar la pizarra de pases.

2. **Opción ↔ futuro:** se emparejan por **prefijo del ticker del futuro**, NO
   por fecha de vencimiento (las opciones agro vencen ~1 mes antes que el futuro
   subyacente). Verificado en `derivados_agro.py` (`_futuro_ticker_de_opcion`).

3. **Mejoras dispo:** `mercado.camara_cereales` (precio ARS spot) +
   `mercado.futuros_dlr_snapshot` (cobertura cambiaria) + `mercado.curvas` (LECAPs)
   + `mercado.market_snapshot` (TEA de cada LECAP) → tasa directa / valor final.

   ⚠️ **La columna TNA se DERIVA de la TEA (corregido 2026-08-26).** El motor de
   curvas publica una sola tasa por bono —la **TEA**, la TIR efectiva anual— y la
   fila la convertía… nunca: mostraba la TEA cruda bajo un encabezado que decía
   TNA. Nada fallaba y ninguna celda quedaba en `--`; el mismo papel simplemente
   mostraba 29,34% acá y 26,01% en RENTA FIJA, donde la TNA sí se deriva desde
   siempre. La conversión vive UNA sola vez, en **`quant/tasas.py`**
   (`TNA = TEM×12`, `TEM = (1+TEA)^(1/12) − 1` — la convención de la casa), y la
   congela `tests/unit/test_mejoras_dispo_tasas.py`.

   Arrastraba a la plata: `tasa_directa` prorrateaba **linealmente una tasa
   efectiva** (`TEA × días/365`), que no es ninguna de las dos convenciones y
   sobreestimaba el interés — TEA 30% a 180 días daba 14,79% cuando la convención
   lineal da 13,08%. Hoy lo lineal se aplica sobre la TNA, que es para lo que
   existe una tasa nominal, y la fila viaja además con `rendimiento_efectivo`
   = `(1+TEA)^(días/365) − 1` (13,81% en ese ejemplo): **la plata real** que rinde
   la Lecap al vencimiento. No es una columna — está en el tooltip de la celda TNA
   junto con la TEA, para que la pregunta «¿esto es TNA o TEA?» se conteste sin
   salir de la pantalla.

---

### 5. Estado SQL — migración completa

**Decomiso de Mongo terminado (2026-06-29): AGRO lee y escribe SQL-native.**

#### 5.1. Tablas propias de AGRO (todas en SQL, schema `mercado`)
- `mercado.agro_snapshot` — futuros agro vivos
- `mercado.agro_opciones_snapshot` — cadena de opciones agro viva
- `mercado.futuros_dlr_snapshot` — futuros DLR (vivo + cierre)
- `mercado.agro_pizarra` (+ `agro_pizarra_audit`) — pizarra manual
- `mercado.camara_cereales` (+ `camara_cereales_audit`) — cámara manual
- `mercado.volumen_mercado_agro` — denominador del market share (**carga manual mensual**
  vía `python -m scripts.cargar_volumen_agro`; `--listar` muestra qué meses están cargados)

#### 5.2. Compartidas (también SQL)
- `mercado.curvas` y `mercado.market_snapshot` (documentadas en RENTA_FIJA.md).
- (Market share, en la vista Operaciones) `operaciones.operaciones` y
  `clientes.comitentes`.

#### 5.3. Conclusión de migración para AGRO
- **Lectura y escritura: 100% SQL.** Los services de agro leen vía los `*_sql.py`
  / helpers de `core`; los motores escriben con `core.pg_mirror`. Conexión
  `core.postgres.get_pool()`.
- No queda nada de AGRO en Mongo.

---

### 6. ⚠️ Pendiente de verificar / medir en prod (NO asumido)

1. **Conteos reales** (¿`mercado.agro_snapshot` tiene las ~24 filas?, ¿la pizarra
   tiene las 3?, etc.) — medir en SQL.
2. **`agro_pizarra`/`camara_cereales` creación inicial:** el código asume que
   existen; no se vio dónde se crean por primera vez. ⚠️ a verificar (¿seed manual?).

---

### 7. Archivos fuente

- **Frontend:** `acaquant-web/src/app/agro/page.tsx` + `agro-shell.tsx`,
  `derivados-agro-view.tsx`, `derivados-agro-futuros.tsx`,
  `derivados-agro-opciones.tsx`, `derivados-agro-pizarra.tsx`,
  `derivados-agro-estrategias.tsx`, `agro-mejoras-dispo.tsx`, `agro-datos.tsx`.
- **Router:** `api/routers/derivados_agro.py`.
- **Services:** `derivados_agro.py`, `camara_cereales.py`, `mejoras_dispo.py`.
- **Motores:** `engines/motor_agro.py`, `engines/motor_agro_opciones.py`,
  `engines/futuros_dlr.py` (limpieza: `jobs/cleanup_futuros_dlr.py`).
- **SQL:** `sql/schema.sql` (schema `mercado`: `agro_snapshot`,
  `agro_opciones_snapshot`, `futuros_dlr_snapshot`, `agro_pizarra`(+`_audit`),
  `camara_cereales`(+`_audit`), `volumen_mercado_agro`, `curvas`,
  `market_snapshot`; + `operaciones.operaciones` y `clientes.comitentes` para el
  market share). Conexión `core.postgres.get_pool()`, escritura vía
  `core.pg_mirror`.
