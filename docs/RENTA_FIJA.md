
# RENTA FIJA — mapa de datos (Mongo + SQL)

> **Qué es este documento.** Mapa verificado **desde el código** (no desde otros
> docs) de la vista **RENTA FIJA** (`/renta-fija` en acaquant-web): qué datos
> muestra, de qué colección Mongo salen, quién las llena, cómo se relacionan, y
> qué está espejado en SQL y qué no.
>
> **Método.** Cada afirmación de abajo fue verificada leyendo el archivo y la
> línea reales (routers, services, motores, jobs, `sql/schema.sql`). Lo que **no**
> pude verificar está marcado explícitamente como `⚠️ a verificar` — no se asumió
> nada. Lo que requiere **medir en prod** (conteos, si el sync corre de verdad)
> está en la sección final.
>
> Fecha de relevamiento: **2026-06-12**. Si cambia un motor/job/endpoint, este
> doc queda viejo — regenerar revisando las mismas fuentes.

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** una sola pantalla (`/renta-fija`) con **4 paneles** —
Tabla de bonos, Forwards, Curvas y Breakevens (+ sub-tab Libro). Es la pantalla
más pesada en datos de toda la sección MERCADOS.

📋 **De dónde sale todo:** **100% de Mongo.** La vista lee ~16 colecciones de la
base `Trading` (+ 2 de `Valuaciones`). Se apoya en **3 colecciones base**
(`Curvas`, `MarketSnapshot`, `SnapshotsCierre`) y el resto **se deriva** de ellas
(forwards, breakevens, fair value son **cálculos**, no datos crudos).

📋 **Estado de SQL para esta vista (lo importante):**
- **Lectura: CERO.** Ningún endpoint de renta fija lee de Postgres. No existe
  `renta_fija_sql.py`. (Verificado.)
- **Escritura/espejo:** la mayoría de los **históricos y maestros** SÍ tienen
  tabla espejo en SQL, poblada por el batch `jobs/sync_postgres.py` (cron cada
  20 min, sin flag). El **live de forwards/breakevens** (la matriz intradía)
  **NO tiene espejo en SQL** — solo su cierre diario va a la tabla `mercado_hist`.

---

## 2. Los 4 paneles (qué muestra cada uno y con qué endpoint)

| Panel | Sub-bloques | Endpoint(s) backend | Service |
|---|---|---|---|
| **1. Tabla Renta Fija** | Tasa Fija · CER · Hard Dólar · Dólar Linked · **Libro** | `GET /api/cotizaciones/snapshot-live` (bundle, poll 5s) · `/renta-fija` · Libro: `/historico/trades` | `renta_fija.py` |
| **2. Forwards** | Live · Gráfico · Z-score | `/forwards` · `/historico/forwards` · `/forwards-zscore` | `derivados.py` |
| **3. Curvas** | 4 curvas × (Live / Histórico / Fair Value) | `/historico/curva` · `/analitica/listar-curva` · `/fair-value` · `/fair-value/historico` | `renta_fija.py` · `analitica.py` · `fair_value.py` |
| **4. Breakevens** | Live · Histórico · overlay REM | `/breakevens` · `/historico/breakevens` · `/rem/breakeven-acumulado` | `derivados.py` · `rem.py` |

> **`snapshot-live` es un bundle:** un solo endpoint que devuelve
> `{renta_fija, forwards, breakevens}` leyendo del cache de cada service (TTL
> 5/30/30s). El front hace **1 poll cada 5s** en vez de 3. Verificado en
> `api/routers/cotizaciones.py:202-226`.

Todos los endpoints listados **existen** y fueron verificados en
`api/routers/cotizaciones.py` y `api/routers/analitica.py`.

---

## 3. Colecciones Mongo de la vista — quién las lee y quién las llena

Verificado: acceso `db["Coleccion"]` en cada service + el motor/job que escribe.

### 3.1. Colecciones BASE (el núcleo)

| Colección (`Trading.*`) | Qué es | La leen (services) | La llena (motor/job) |
|---|---|---|---|
| **Curvas** | Maestro estático de cada bono: flujos, vencimiento, cupón, `cer_emision` | renta_fija, analitica, carry_trade, sensibilidad, titulos_flujos, fair_value | Maestro editable (no lo escribe un motor) — carga/edición |
| **MarketSnapshot** | Estado **vivo** por ticker: precio, book, TEA, duration, paridad | renta_fija, analitica, carry_trade, sensibilidad, fair_value | **motor rofex** (`engines/valores.py`: book + precios) **y motor curvas** (`engines/curvas.py`: TEA/TEM/duration/paridad). Cada uno escribe SOLO sus campos con `$set` parcial |
| **SnapshotsCierre** | Foto del cierre diario por bono | renta_fija, analitica, carry_trade | **`jobs/snapshot_cierre.py`** (cron 20:25 UTC) |

### 3.2. Colecciones DERIVADAS (cálculos a partir de las base)

| Colección | Qué es | La lee | La llena (verificado) |
|---|---|---|---|
| **ForwardsLive** | Matriz de tasas forward (1 doc/curva, vivo) | derivados.py | **motor forwards** (`engines/forwards.py`) |
| **ForwardsHistorico** | Forwards de cierre (1 doc/fecha,curva) | derivados.py | motor forwards |
| **ForwardsZscore** | Media/desvío por par para z-score | derivados.py | **`jobs/forwards_zscore.py`** (post-cierre) |
| **BreakevensLive** | Breakeven Lecap↔CER (1 doc global, vivo) | derivados.py | **motor breakevens** (`engines/breakevens.py`) |
| **BreakevensHistorico** | Breakevens de cierre (1 doc/fecha) | derivados.py | motor breakevens |
| **FitParams** | Betas Nelson-Siegel de la curva (fair value) | fair_value.py | **`jobs/fair_value.py`** |
| **FairValueResiduos** | Residuo/z-score por bono vs curva teórica | fair_value.py | `jobs/fair_value.py` |

> El motor breakevens y el motor forwards **leen `MarketSnapshot` + `CER`** (y el
> de breakevens también `InflacionMensual` y `TimeSales`) para calcular. Es
> decir: si el precio vivo no llega, forwards y breakevens no se actualizan.
> Verificado en `engines/breakevens.py:125-205` y `engines/forwards.py:45`.

### 3.3. Colecciones de soporte

| Colección | Qué es | La lee | La llena |
|---|---|---|---|
| **TimeSales** | Cada trade (Time & Sales) — alimenta sub-tab Libro | analitica, canje, renta_fija, macro | motor rofex (cada trade) |
| **CanjeCierre** | Cierre diario de tickers de canje | canje.py | `jobs/cierre_canje.py` |
| **CER** | Valor CER publicado por BCRA (1 doc/día) | renta_fija, descomposicion_retorno | `jobs/bcra.py` |
| **InflacionMensual** | IPC mensual (usado por motor breakevens) | (motor breakevens) | `jobs/argentina_datos.py` ⚠️ *a verificar el job exacto* |
| **DiasHabiles** | Calendario hábil (liquidación CER T+10) | renta_fija | job de días hábiles ⚠️ *nombre a verificar* |
| **REM** | Consenso de inflación (overlay breakevens / rolldown) | derivados (rem), descomposicion_retorno | `jobs/argentina_datos.py` ⚠️ *a verificar* |
| **UVA** | Valor UVA | macro.py | carga manual ⚠️ *a verificar* |
| **Caucion / CaucionSnapshot** | Caución cierre / vivo | repo.py | motor caución (`engines/caucion.py`) |
| **FuturosDLR / FuturosDLRSnapshot** | Futuros DLR cierre / vivo | derivados.py | motor futuros DLR |
| **Valuaciones.Dolar / DolarSnapshot** | MEP/CCL histórico / vivo | macro, carry_trade | motores dólares / dolar_mep |

---

## 4. Relaciones clave entre colecciones (los "joins")

Mongo **no tiene foreign keys**: estos cruces se hacen **en Python**, no en la
base. Los más importantes (verificados):

1. **El cruce maestro:** `Curvas.ticker_corto` ↔ `MarketSnapshot.ticker` ↔
   `SnapshotsCierre.ticker`. Une el "DNI" del bono (Curvas) con su precio vivo
   (MarketSnapshot) o de cierre (SnapshotsCierre). Aparece en casi todos los
   endpoints. (`renta_fija.py`, `analitica.py`, `sensibilidad.py`.)

2. **CER fijado:** `Curvas.fecha_vencimiento` → `DiasHabiles` (T+10) →
   `CER.fecha`. Si el CER de liquidación de un bono ya está publicado, el bono
   "migra" de curva CER a tasa fija en runtime. (`renta_fija.py`.)

3. **Par breakeven:** `Curvas` (lecap) ↔ `Curvas` (cer) por `fecha_vencimiento`
   aproximada (±20 días). El motor breakevens empareja Lecap con el CER más
   cercano en plazo. (`engines/breakevens.py`.)

4. **Fair value live:** `FitParams` (betas del cierre) + `MarketSnapshot` (TEA
   viva) → `FairValueResiduos` recalculado. (`fair_value.py`.)

5. **Carry / retorno total:** `SnapshotsCierre` (precios) + `Valuaciones.Dolar`
   (MEP) + `Trading.DOLAR` (oficial A3500). (`carry_trade.py`, `renta_fija.py`.)

---

## 5. Estado SQL — qué está espejado y qué no

**Verificado contra `sql/schema.sql` (27 tablas) y el `run()` de
`jobs/sync_postgres.py` (todas estas sync corren sin flag, en cada corrida del
cron, cada 20 min en horario de mercado).**

### 5.1. Colecciones CON espejo en SQL

| Colección Mongo | Tabla SQL | Cómo se puebla |
|---|---|---|
| Trading.Curvas | `curvas` | `sync_curvas` (batch) |
| Trading.BondsMaster | `bonds_master` | `sync_bonds_master` (batch) |
| Trading.MarketSnapshot | `market_snapshot` | `sync_market_snapshot` (batch, baseline) **+ dual-write vivo de los motores** (flag `SNAPSHOT_SQL`, hoy **ENCENDIDO**) |
| Trading.SnapshotsCierre | `snapshots_cierre` + `snapshots_cierre_hist` | `sync_snapshots_cierre*` (batch) + dual-write (`MERCADO_SQL_WRITE`) |
| Trading.CanjeCierre | `canje_cierre` | `sync_canje_cierre` (batch) + dual-write |
| Trading.CER · InflacionMensual · BADLAR · DOLAR · TAMAR · RiesgoPais · InflacionInteranual | `series_macro` (las 7 juntas) | `sync_series_macro` (batch) |
| Trading.REM | `rem` | `sync_rem` (batch) |
| Trading.BreakevensHistorico | `mercado_hist` (clave `coleccion='BreakevensHistorico'`) | `sync_mercado_hist` (batch) |
| Trading.ForwardsHistorico | `mercado_hist` | `sync_mercado_hist` |
| Trading.FuturosDLR | `mercado_hist` | `sync_mercado_hist` |
| Trading.Caucion | `mercado_hist` | `sync_mercado_hist` |
| Trading.FitParams | `mercado_hist` | `sync_mercado_hist` |
| Trading.FairValueResiduos | `mercado_hist` | `sync_mercado_hist` |
| Valuaciones.Dolar | `dolar` | `sync_dolar` (batch) |

> **`mercado_hist` es genérica:** una sola tabla con `(coleccion, fecha, k,
> data jsonb)` que guarda el **doc completo** de 6 colecciones de cierre. Es
> espejo **batch** — NO hay dual-write vivo desde los motores (verificado: no
> existe `mirror_*` de mercado_hist en `engines/`).

### 5.2. Colecciones SIN espejo en SQL (quedaron afuera)

Estas **no tienen ninguna tabla** en `sql/schema.sql`:

- **ForwardsLive** y **BreakevensLive** — el **vivo intradía** de forwards y
  breakevens (la matriz que ves moverse). Solo su cierre diario va a
  `mercado_hist`; el live **no está en SQL**.
- **ForwardsZscore** — coeficientes de z-score.
- **TimeSales** — los trades (Libro).
- **CaucionSnapshot**, **FuturosDLRSnapshot**, **Valuaciones.DolarSnapshot** —
  los snapshots **vivos** (singletons).
- **DiasHabiles**, **UVA**, **InflacionMensual** *(esta última sí entra a
  `series_macro`; ver 5.1 — confirmado por el comentario del schema)*.

### 5.3. Conclusión de migración para RENTA FIJA

- La vista **funciona 100% sobre Mongo**. SQL hoy **no sirve ni un dato** de esta
  pantalla.
- SQL tiene un espejo de **escritura** bastante completo de los **históricos y
  maestros** de renta fija (curvas, cierres, series macro, fair value de cierre,
  forwards/breakevens de cierre). Sirve para reportería futura.
- Lo que **falta del todo** en SQL es el **estado vivo derivado**: la matriz live
  de forwards y breakevens, el z-score, el Time & Sales. Si algún día se quiere
  servir la vista desde SQL, eso hay que diseñarlo (hoy no existe).

---

## 6. Cache / frecuencia (verificado: `@cached` en los services)

| Service | TTL de cache |
|---|---|
| `renta_fija.py` (snapshot bonos) | 10s (+ otros: 15/30/60/300) |
| `derivados.py` (forwards/breakevens) | forwards/breakevens 5–30s; históricos 300s |
| `fair_value.py` | 30s (live) / 300s (cierre, histórico) |
| `analitica.py` | 60–300s |
| `macro.py` (series BCRA) | 3600s; MEP 5s |
| `repo.py` (caución) | 5s (live) / 300s (histórico) |

El front poll de la pantalla: `snapshot-live` cada 5s; fair-value live 90s;
forwards-zscore 5 min; trades (Libro) 5s. *(Intervalos según el código del
front; el backend manda con su TTL de cache.)*

---

## 7. ⚠️ Pendiente de medir en prod (NO asumido)

Esto **no se puede afirmar leyendo código** — requiere correr una medición:

1. **¿El espejo SQL está realmente poblado y al día?** Las tablas existen y el
   sync está wired, pero que corra OK depende de que `jobs/sync_postgres` se
   ejecute y Postgres esté accesible. **Medir con:**
   `python -m scripts.diag_inventario_mongo_sql` (lista conteos Mongo vs SQL,
   read-only).

2. **Conteos reales por colección/tabla** — no hay ningún número en este doc a
   propósito (sería fruta). Salen del mismo diag.

3. **Nombres de job exactos** marcados `⚠️ a verificar` en §3.3
   (InflacionMensual / DiasHabiles / REM / UVA): sé qué colección es y que la
   leen, pero no confirmé el job que las escribe leyendo su línea. No afecta la
   vista (son soporte), pero queda anotado para no afirmar de más.

---

## 8. Archivos fuente (para regenerar/auditar este doc)

- **Frontend:** `acaquant-web/src/app/renta-fija/page.tsx` + componentes
  `renta-fija-live.tsx`, `renta-fija-table.tsx`, `forwards-panel.tsx`,
  `curvas-chart.tsx`, `breakevens-block.tsx`, `fair-value-view.tsx`,
  `libro-panel.tsx`.
- **Routers:** `api/routers/cotizaciones.py`, `api/routers/analitica.py`.
- **Services:** `renta_fija.py`, `derivados.py`, `fair_value.py`, `analitica.py`,
  `canje.py`, `carry_trade.py`, `sensibilidad.py`, `descomposicion_retorno.py`,
  `titulos_flujos.py`, `macro.py`, `repo.py`, `rem.py`.
- **Motores:** `engines/valores.py`, `engines/curvas.py`, `engines/forwards.py`,
  `engines/breakevens.py`, `engines/caucion.py`.
- **Jobs:** `snapshot_cierre.py`, `cierre_canje.py`, `bcra.py`, `fair_value.py`,
  `forwards_zscore.py`, `argentina_datos.py`, `sync_postgres.py`.
- **SQL:** `sql/schema.sql` (tablas `curvas`, `bonds_master`, `market_snapshot`,
  `snapshots_cierre*`, `canje_cierre`, `series_macro`, `rem`, `mercado_hist`,
  `dolar`).
