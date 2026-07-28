# Motor de Valuaciones (PnL Títulos)

Doc-checkpoint del motor de PnL por (cuenta, ticker). **Estado al 2026-05-16**.
Para retomar el desarrollo: leé esto + `api/services/pnl.py` + `engines/portfolio_snapshot.py`.

---

## Objetivo

Calcular el **PnL real** por activo de cada cuenta. La filosofía es la
clásica de cash flows:

```
PnL = (lo que vale ahora)  +  (todo lo cobrado)  −  (todo lo pagado)
```

Tres componentes separados:

| Componente | Qué representa |
|---|---|
| **PNL REALIZADO** | Ganancias/pérdidas de ventas cerradas históricamente (oculto en la UI actual). |
| **PNL REALIZADO_DIA** | Solo ventas cerradas hoy (post último AuM) — visible para day-trades. |
| **PNL NO REALIZADO** | Diferencia papel del stock vivo (`valor_actual_live − costo_remanente`). |
| **PNL PASIVO** | Cobros sueltos: cupones, dividendos, amortizaciones. |

**PNL TOTAL visible (UI)** = `pnl_no_realizado + pnl_pasivo + pnl_realizado_dia`.
El `pnl_realizado` histórico se oculta — irá a una vista histórica separada (futuro).

---

## Arquitectura

> **Nota (decomiso Mongo 2026-06-29):** todas las fuentes son SQL (Postgres).
> `operaciones.negocio_movimientos` (boletos), `portafolio.tenencia` (AuM),
> `portafolio.assets` (mapping), `valuaciones.dolar` (MEP histórico),
> `mercado.market_snapshot` / `mercado.snapshots_cierre` y
> `valuaciones.portfolio_snapshot` (precios live). Antes vivían en Mongo
> (`CashFlow`/`Valuaciones`/`Trading`).

```
┌────────────────────────────────┐  ┌────────────────────────────────┐
│  operaciones                   │  │  portafolio / valuaciones      │
│  └ negocio_movimientos         │  │  ├ tenencia (snapshot diario)  │
│     (boletos)                  │  │  ├ assets (mapping)            │
│                                │  │  │  • TICKER (humano)          │
│                                │  │  │  • CAFCI (FCI)              │
│                                │  │  │  • INSTRUMENTO (rofex)      │
│                                │  │  └ valuaciones.dolar (MEP hist)│
└──────────┬─────────────────────┘  └──────────┬─────────────────────┘
           │                                    │
           └────────────┬───────────────────────┘
                        ↓
       ┌─────────────────────────────────────┐
       │  api/services/pnl.py                │
       │  • cost-basis weighted-average      │
       │  • qty sin clip (wash trades)       │
       │  • valor_actual chain: live→cierre  │
       └────────────┬────────────────────────┘
                    ↓
   ┌────────────────────────┐  ┌────────────────────────────┐
   │ mercado.market_snapshot│  │ valuaciones.portfolio_snapshot│
   │   (analytics live)     │  │   (live tenencia)          │
   │   ← motor_rofex        │  │   ← portfolio_snapshot     │
   └────────────────────────┘  └────────────────────────────┘
                    ↓
       ┌─────────────────────────────────────┐
       │  GET /api/portfolio/pnl?id_cuenta   │
       │  GET /api/portfolio/pnl-todas       │
       └────────────┬────────────────────────┘
                    ↓
   /aum → VALUACIONES (acaquant-web)
       ├ PORTAFOLIO   (vista vieja)
       ├ PNL TÍTULOS  (split posiciones | detalle, con day-trade banner)
       └ TOTALES      (toda la mesa, agregada en una tabla)
```

### Archivos clave

**Backend (TRD-FX)**:
- `api/services/pnl.py` — motor de cost-basis (corazón del sistema).
  Funciones: `pnl_por_cuenta` (single-cuenta, `@cached(ttl=300)`),
  `pnl_todas_cuentas` (agregado mesa, `@cached(ttl=60)`),
  `_pnl_por_cuenta_core` (cálculo puro, toma todas las deps por kwarg),
  `_load_pnl_bulk_deps` (pre-carga bulk de maps + boletos + AuM para TOTALES).
- `api/services/_mep.py` — helper MEP con fallback a `valuaciones.dolar`.
- `api/services/aunesa_negocio.py` — parser de boletos / categorización.
- `api/services/valuaciones.py` — vista PORTAFOLIO (legacy AuM-based).
- `api/routers/carteras.py` — endpoints `/portfolio/pnl`, `/portfolio/pnl-todas`.
- `engines/portfolio_snapshot.py` — motor live de tenencia.
- `engines/_universo_portfolio.py` — universo dinámico (cuáles tickers suscribir).
- `core/cafci.py` — extracción de código CAFCI desde unidad.
- `jobs/portafolio_backfill.py::_alta_assets_nuevos` — auto-alta en `portafolio.assets` (SQL) cuando aparece una unidad nueva.
- `jobs/negocio_movimientos.py` — ingesta hourly de boletos desde Aunesa.

**Frontend (acaquant-web)**:
- `src/components/aum-view.tsx` — host con sub-tabs PORTAFOLIO / PNL TÍTULOS / TOTALES.
- `src/components/pnl-titulos-view.tsx` — vista por cuenta (split posiciones | detalle). Exporta tipos + helpers + `PosicionDetalle`.
- `src/components/pnl-totales-view.tsx` — vista agregada de toda la mesa (split tabla | detalle).
- `src/components/valuaciones-view.tsx` — vista PORTAFOLIO (chart evolución mensual + tabla).
- `src/app/api/aum-pnl/route.ts` y `aum-pnl-todas/route.ts` — proxies.

---

## Lógica del cost-basis (cómo se computa el PnL)

Para cada (cuenta, ticker), en orden cronológico de boletos:

```python
state[ticker] = {
    qty_actual:        0,    # neta running. SIN CLIP (ver wash trades).
    costo_remanente:   0,    # cost basis del stock vivo (en ARS)
    pnl_realizado:     0,    # ganancias de ventas pasadas (HISTÓRICO)
    pnl_realizado_dia: 0,    # solo de boletos > fecha_actual_aum
    pnl_pasivo:        0,    # cupones / divs / amorts
    pnl_pasivo_dia:    0,    # solo intraday
    qty_compras:       0,
    qty_ventas:        0,
}

para cada boleto en orden cronológico:
    si COMPRA (P, Q):
        # Caso especial: si qty_actual < 0 (short generado por venta
        # excesiva tipo wash trade), la compra "cubre" el short. Solo
        # la parte que excede el short entra al cost-basis.
        if qty_actual < 0:
            cubierto = min(Q, -qty_actual)
            nueva_compra = Q - cubierto
            costo_remanente += (P × Q) × (nueva_compra / Q)   # proporcional
        else:
            costo_remanente += P × Q
        qty_actual += Q

    si VENTA (P, Q):
        # SIN CLIP — qty_actual puede ir a negativo. Esto refleja
        # ventas que exceden el stock conocido (caución, ROE, wash
        # trades) y permite que la compra contraparte las cancele.
        if qty_actual > 0:
            avg_cost = costo_remanente / qty_actual
            qty_a_vender = min(Q, qty_actual)
            ingreso_proporcional = (P × Q) × (qty_a_vender / Q)
            realizado = ingreso_proporcional - (avg_cost * qty_a_vender)
            pnl_realizado += realizado
            if fecha > fecha_actual_aum:
                pnl_realizado_dia += realizado
            costo_remanente -= avg_cost * qty_a_vender
        qty_actual -= Q

    si ACREENCIA:
        pnl_pasivo += importe
        if fecha > fecha_actual_aum:
            pnl_pasivo_dia += importe
```

### Wash trades / caución / ROE / trasvaso (CRÍTICO)

Operaciones de **wash trade** (venta -50M + compra +50M mismo día) son
comunes en cuentas que operan caución / ROE / trasvasos. **No toman
posición económica real** — el AuM no las refleja.

Si el motor clipeara las ventas excedentes (versión vieja del código),
las compras posteriores inflaban qty_actual y costo_remanente con
fantasmas que crecían acumulativamente. **Caso TZX26: AuM=500K pero
motor calculaba 60.5M** por wash trades acumulados.

Solución actual (commit `0c3c638`):
- Ventas: SIN clip — qty_actual puede ir a negativo.
- Compras: si qty_actual < 0, primero "cubre" el short — esa porción NO
  entra al cost-basis. Solo lo que excede entra.
- Resultado: los wash trades se neutralizan matemáticamente. qty_calc
  final coincide con qty_aum cuando los boletos están completos.

---

## Cálculo del valor_actual (cadena de fallback)

Función `_valor_actual_live(db_t, db_v, unidad, qty_efectiva, tipoTitulo,
valor_aum, *, cartera, instrumentos_by_unidad, portfolio_snap_by_ticker,
snapshots_cierre_by_ticker)` en `pnl.py`. Los tres kwargs `*_by_*` son
opcionales: si vienen pre-cargados (path bulk de `pnl_todas_cuentas`) las
lookups se resuelven contra dicts en memoria; sin ellos cae al `find_one`
por ticker (path single-cuenta de PNL TÍTULOS).

```
1. Si qty_efectiva == 0 → return (0, "live")  # cerrado intraday
2. Si no hay unidad → return (valor_aum, "aum")  # fallback final
3. Lookup assets.INSTRUMENTO de la unidad.
4. Si INSTRUMENTO existe y no es placeholder ("" / "NO APLICA"):
   a. Lookup valuaciones.portfolio_snapshot[ticker=INSTRUMENTO]
      → si hay last_price o closing_price > 0 →
         return (qty_efectiva × precio × normalizer, "live")
   b. Lookup mercado.snapshots_cierre[ticker=INSTRUMENTO]
      → si hay last_price > 0 →
         return (qty_efectiva × precio × normalizer, "cierre")
5. Fallback: return (valor_aum, "aum")
```

**Normalizer por CARTERA** (`pnl.py::_aplicar_normalizer`, desde 2026-06-15). El
÷100 lo decide la **cartera** de `portafolio.tenencia` — confiable y siempre
presente (antes era por `tipoTitulo`, que se quedó NULL al migrar a SQL y rompía
el PnL valuando bonos ×100):
- Renta fija `HD / DL / ARS` (cotiza en paridad): `(precio × qty) / 100`.
- `DERIVADOS` (futuros): `(precio + 1) × qty`.
- `FCI / RENTA VARIABLE / MONEDAS` y resto: `precio × qty`.
- Fallback: si la cartera no está clasificada, cae al `tipoTitulo` legacy
  (TIPOS_DIVISOR_100) como red de seguridad.

### qty_efectiva (qué cantidad usamos)

| Caso | qty_efectiva |
|---|---|
| `qty_aum == 0` | 0 — AuM dice no tenés. Fuerza valor=0. Ignora qty_calc fantasma de boletos viejos no reconciliados. |
| Hay boletos | `qty_calc` — refleja todo lo movido (incluye intraday del día). |
| Sin boletos | `qty_aum` — única señal disponible. |

---

## Mapping unidad ↔ ticker

`operaciones.negocio_movimientos.ticker` ("AO28", "CAFCI3580-1199") y `portafolio.tenencia.unidad`
("[5921] AO28 - BONO TESORO NAC.") no matchean directo.

`pnl._build_unidad_maps()` (sin args, `@cached(ttl=300)`) construye dos maps
desde `portafolio.assets` (SQL):

| Tipo | match_key (interno, joinea boletos↔AuM) | display (UI) |
|---|---|---|
| FCI | `assets.CAFCI` (ej "CAFCI3580-1199") | `assets.TICKER` (ej "Consultatio Multimercado V") |
| Acciones / bonos / ONs | `assets.TICKER` (ej "AL30") | `assets.TICKER` |
| Sin metadata | regex fallback sobre unidad | match_key |

`assets.CAFCI` es derivado automáticamente de `unidad` por `_sincronizar_assets`
con regex `\bCAFCI\d+-\d+\b`.

`assets.INSTRUMENTO` se completa con script:
- `backfill_assets_instrumento.py` valida candidatos contra
  `manager.pyrofex_instruments` (lista canónica de pyRofex 24hs).
- Solo setea si el INSTRUMENTO existe en pyRofex — los strings inválidos
  quedan visibles en /manager → DIAGNÓSTICO.

---

## Motor de portfolio snapshot (live)

`engines/portfolio_snapshot.py` corre como systemd service paralelo a
motor_rofex (sesión pyRofex propia). Cron L-V 13-20 UTC, igual que
motor_rofex.

**Universo dinámico**: `engines/_universo_portfolio.py::tickers_de_tenencia()`
devuelve set de symbols pyRofex de:
- Unidades con qty != 0 en último AuM.
- Tickers operados hoy en `operaciones.negocio_movimientos`.
- Filtrado contra `manager.pyrofex_instruments` (validación canónica).

**Refresh dinámico**: thread cada 60min revisa el universo. Si entró
un ticker nuevo (compraste un activo que antes no estaba en cartera),
se suscribe al WS via `WebSocketManager.agregar_suscripciones` (aditivo,
no reabre conexión).

**Suscripción reducida**: solo entries `[LAST, CLOSING_PRICE]`. NO
escribe a `TimeSales`. NO contamina con cada tick. Solo `last_price`
en `valuaciones.portfolio_snapshot` cada 1s.

**Logs de refresh**: `manager.portfolio_snapshot_log` con timestamp,
nuevos tickers agregados, tickers de boletos sin INSTRUMENTO mappeable.

### Coverage actual (al 2026-05-08)

- ~358 tickers válidos de tenencia con INSTRUMENTO + cobertura en pyRofex.
- ~89 sospechosos (vencidos / ilíquidos) con INSTRUMENTO seteado pero
  sin feed activo en pyRofex — quedan con `valor_actual_source = "aum"`.
- FCI inevitables — no se cotizan en mercado, su precio es VCP del
  fondo (siempre fallback a AuM).

---

## Vista PNL TÍTULOS (acaquant-web)

`/aum → VALUACIONES → PNL TÍTULOS` con selector de cuenta.

**Layout: split 50/50.**

### Panel izquierdo (50%)

Tabla compacta de posiciones, filtrada en backend a posiciones reales:
```
TICKER · CANT · COSTO · VALOR · GAN % · PNL · FLAGS
```

**Filtro de visibilidad** (en `pnl.py`):
- Si `qty_aum == 0` Y no hay boletos > fecha_aum → SKIP la fila
  (cerrado histórico o fantasma de boletos no reconciliados).
- Si `qty_aum != 0` (long, short, palanca, USD/ARS, futuros) → siempre
  mostrar.
- Si `qty_aum == 0` con boletos del día → mostrar (day-trade cerrado
  hoy, ej. ETHA).

**Click en fila** → highlight + carga detalle a la derecha.
**Re-click** → deselecciona.

**FLAGS** abreviados: `P` (parcial), `SB` (sin boletos), `$` (USD/ARS).

### Panel derecho (50%)

Componente `PosicionDetalle` (exportado, reusable). Contenido:

1. **Header**: display name del ticker + ticker interno + unidad.
2. **6 mini-KPIs**: COSTO · VALOR · PNL · NO REAL · COBROS · GAN %.
3. **Banner azul "REALIZADO HOY"** (cuando `pnl_realizado_dia != 0`):
   muestra el realizado del día — útil para day-trades cerrados intraday.
4. **FLUJO (STOCK ACTUAL)**: compras / ventas / neto del **período activo**.
5. **COBROS PASIVOS (PERÍODO)**: breakdown por op del período.
6. **BOLETOS DEL STOCK ACTUAL**: tabla de boletos del período activo.
   Históricos previos al último reset de qty=0 quedan ocultos (ver nota).

### Filtro del período activo (frontend)

`_filtrarPeriodoActual(boletos)` en `pnl-titulos-view.tsx`: walk forward,
cuando qty pasa de >0 a ≤0 → reset. Devuelve solo boletos desde el
último reset.

**Caso RKLB** (al 2026-05-08, 26 boletos totales):
- Compras/ventas históricas (Nov 2025 - Mar 2026) que netaron a 0
  en varios momentos → "stock cerrado".
- 2 compras de mayo 2026 (25 + 433) → único stock vivo de 458 nominales.
- Detalle muestra solo esos 2 boletos. "24 históricos ocultos".

---

## Vista TOTALES (acaquant-web)

`/aum → VALUACIONES → TOTALES`. Endpoint `GET /api/portfolio/pnl-todas`.

**Sin selector de cuenta** — agrega TODAS las cuentas de la mesa.

**Layout: split 60/40.**

### Panel izquierdo (60%)

Tabla agregada por (cuenta, ticker):
```
CUENTA · TICKER · CANT · COSTO · VALOR · GAN % · PNL · FLAGS
```

**Solo posiciones abiertas**: `pnl_todas_cuentas` descarta del listado los
rows con `qty_aum == 0` (cerrados intraday — todo vendido hoy). Su
`pnl_realizado_dia` SÍ queda contado en el agregado `totales` por cuenta,
así que el filtro solo limpia el row listing, no sesga ningún KPI. (En PNL
TÍTULOS por cuenta sola esas filas SÍ se preservan, con el banner
"REALIZADO HOY".)

**Filtros encima**:
- Tipo de cuenta: `TODAS / ACCIONISTAS / SIN ACCIONISTAS / COOPERATIVAS / PRODUCTORES`.
- Búsqueda libre por cuenta (texto contiene).
- Búsqueda libre por ticker.

**Sortable** por todas las columnas. Default `PNL desc`.

**5 KPIs agregados** sobre las filas filtradas: PNL TOTAL, NO REAL,
PASIVO, VALOR, POSICIONES (con N cuentas).

### Panel derecho (40%)

Reusa `PosicionDetalle` con un header extra que indica a qué cuenta
pertenece la posición seleccionada.

### Performance

`pnl_todas_cuentas` **NO reusa** la cacheada `pnl_por_cuenta` — hacerlo
disparaba un N+1 (cada cuenta × ~20 tickers × 3 `find_one` en
`_valor_actual_live` + 1 regex query de boletos) que pegaba 502/504 con
883 cuentas. Arquitectura actual:

- `_load_pnl_bulk_deps` hace **~6 queries totales** (`portafolio.assets`,
  `valuaciones.portfolio_snapshot`, `mercado.snapshots_cierre`,
  `operaciones.negocio_movimientos` agrupado por `id_cuenta`, AuM del último
  snapshot global) — independiente de N cuentas.
- `_pnl_por_cuenta_core` recibe esos dicts por kwarg y procesa en RAM, sin
  pegarle a la DB per-cuenta.
- Cada bulk load va con `try/except`: si uno falla, el dict queda vacío y
  el core cae al path single-cuenta para esa fuente (degradación, no caída).
- Cache backend `@cached(ttl=60)` en `pnl_todas_cuentas`.
- `pnl_por_cuenta` (`@cached(ttl=300)`) sigue vigente solo para el path
  single-cuenta (PNL TÍTULOS).

### Casos de uso

- Sortear por GAN % desc → ver qué (cuenta, ticker) está rindiendo mejor.
- Buscar "AL30" → comparar cómo le va al ticker en todas las cuentas.
- Filtrar `ACCIONISTAS` → ver solo cuentas propias.
- Detectar inconsistencias (PARCIAL flag, NO REAL muy negativo, etc).

---

## Pesificación

Cada boleto USD/USDC se pesifica al MEP del día. El MEP se guarda
inmutable en cada boleto al ingestarlo (`b.mep`).

**Path principal**: `importe_ars = importe × b.mep`.

**Fallback**: si `b.mep == null` (boleto pre-fix sin reingestar o
fecha pre-feed), se llama a `get_mep_for_date(fecha)` que mira
`valuaciones.dolar`.

**Si tampoco hay MEP**: importe queda en moneda original. Reportado
en `fechas_sin_mep` per ticker (badge naranja en la UI).

### Histórico cargado

- 2024 H1 + H2 (`backfill_negocio_range`) — backfill completo.
- 2023 H1 + H2 — backfill completo.
- MEP histórico 2024 cargado a `valuaciones.dolar` por user.
- `match_mep_boletos --desde 2024-01-01 --hasta 2024-12-31` — pendiente
  de aplicar (script lo dejó listo, dry mostró 35.086 docs a actualizar
  con 2 sin MEP en feriados).

---

## Categorización de boletos (parser)

`api/services/aunesa_negocio.py::categorizar` mapea `op` a categoría:

| Categoría | Disparador en `op` | Notas |
|---|---|---|
| `compra` | empieza con "compra" o "licitaci" | Licitación primaria = compra. |
| `venta` | empieza con "venta" |  |
| `suscripcion_fci` | contiene "suscripci" | Cubre "Liquidación de suscripción" (bilateral). |
| `rescate_fci` | contiene "rescate" | Cubre "Liquidación de rescate". |
| `acreencia` | match en `informacion` (Cash dividend / Interest payment / Partial redemption) |  |
| `solicitud_*_fci` | "Solicitud de suscripción/rescate de FCI" | Filtrado del motor PnL — no es la liquidación real. |
| `caucion_*` | "Caución colocadora/tomadora ... Apertura/Cierre" |  |
| `comision`, `impuesto`, `deposito`, etc. | substrings en `informacion` |  |
| `otro` | fallback | Ignorado por motor PnL (excepto refinamiento TRD post-agrupación). |

### Patrones especiales

- **TRD** (op genérico de trading): refinado en `agrupar_boletos` por
  signo del `importe_dinero`. Si `importe < 0` → compra; `> 0` → venta.
- **Liquidación FCI bilateral**: nuevo regex `PATTERN_LIQUIDACION_FCI`
  que captura "Liquidación de suscripción/rescate - [CAFCI...] cant@precio"
  sin requerir `(MONEDA PLAZO)`. Tolera basura concatenada tipo
  "de FCI<NUMS>" (commit `db3f022`).

### Backfills aplicados (al 2026-05-08)

| Backfill | Docs actualizados | Commit |
|---|---|---|
| TRD compra/venta por signo | 144 | `220566f` |
| Licitación → compra | 136 | `4a84d0e` |
| Liquidación FCI bilateral | 3.336 | `982906b`, `db3f022` |
| Assets.CAFCI auto-fill | 184 | `11898cf` |
| Assets.INSTRUMENTO (pyRofex 24hs) | 358 | `c358eaf` |

---

## Filtros aplicados al motor PnL

### Filtro de visibilidad (backend)

```python
if qty_aum == 0 and not hay_actividad_post_aum:
    continue   # cerrado histórico o fantasma — no llega al frontend
```

Saca:
- Cerrados históricos (Schroder Retorno, EWZ, ARKK, GD30, etc).
- Fantasmas con cost residual de boletos viejos no reconciliados
  (TX26, GGAL, AL30, COME, TSLA, IBIT, PLTR — boletos hablan de stock,
  AuM dice cero).

Mantiene:
- Long, short, palanca, futuros, USD/ARS — qty_aum != 0.
- Day-trades cerrados hoy — qty_aum=0 PERO con boletos > fecha_aum.

### Filtro del período activo (frontend, fila expandida)

`_filtrarPeriodoActual` en `pnl-titulos-view.tsx` — solo muestra los
boletos desde el último reset de qty=0. Las acreencias del período
quedan incluidas (no fuerzan reset).

---

## Pendientes / próximos frentes

### Crítico

- **Aplicar `match_mep_boletos`** sin --dry para 2024 (35.086 docs).
- **Cargar MEP histórico 2023** + correr `match_mep_boletos --desde 2023-01-01`.

### Mejora UX

- **Vista histórica de PnL realizado** separada (filtros por fecha /
  ticker / cuenta). El realizado histórico está calculado en backend
  (`pnl_realizado`) pero oculto en UI actual.
- **`AuM exclusion` para cuenta 255 desde el motor PnL** (hoy se
  excluye solo de la vista AuM en `portfolio.py`, ver
  `_EXCLUDED_FROM_AUM_VIEW`).

### Performance

- `pnl_todas_cuentas`: cold start 30-60s. Si se vuelve crítico,
  refactor a agregación SQL (GROUP BY) en lugar de iterar por cuenta.

### Edge cases conocidos

- **Posiciones pre-data**: cuentas con activos comprados antes que
  arrancara el feed `NegocioMovimientos` (~Jul 2025) → flag PARCIAL.
  Cost basis incompleto, PnL no realizado subestimado.
- **Canjes / corporate actions**: AL30 → AL30D no se detecta como
  misma posición económica.
- **FCI sin INSTRUMENTO mappeable**: quedan con `valor_actual_source = "aum"`,
  mismo comportamiento que antes.

---

## Comandos útiles

```bash
# En el droplet, después de cambios al motor:
git pull && sudo systemctl restart api.service

# Si tocás motor portfolio_snapshot:
sudo systemctl restart motor_portfolio_snapshot.service
journalctl -u motor_portfolio_snapshot.service -f

# Ver motor en /manager → DIAGNÓSTICO (incluye PortfolioSnapshot
# como motor + Aunesa como API externa).
```

---

## Endpoints relevantes

| Endpoint | Qué devuelve |
|---|---|
| `GET /api/portfolio/pnl?id_cuenta=X` | PnL por (ticker) de una cuenta. |
| `GET /api/portfolio/pnl-todas?filtro_cuenta=Y` | PnL agregado de todas las cuentas (vista TOTALES). |
| `GET /api/portfolio/cuentas` | Lista cuentas para selector. |
| `GET /api/manager/assets?cartera=X` | Lista assets para corregir mapping. |
| `GET /api/manager/assets/values` | Autocomplete (carteras/emisores). |
| `PATCH /api/manager/assets` | Editar UPPERCASE en `portafolio.assets` (con audit). |
| `GET /api/manager/status` | Estado motores + jobs + APIs externas (incluye PortfolioSnapshot + Aunesa). |

---

## Refactor reciente — MM Cartea + order_book L2 deprecated

Se eliminó completo el sistema de microestructura Cartea cap 1-4 (commit
`3c32e7d`):
- `engines/order_book_l2.py` reemplazado por `engines/portfolio_snapshot.py`.
- `api/services/mm_microstructure.py`, `api/services/order_book_historico.py`,
  `api/routers/mm.py` eliminados.
- 8 tools MCP de microstructure removidas.
- Vista `/mm` y proxy `/api/mm` removidos del frontend.
- Trading.OrderBookL2 collection: exportada a CSV via
  `scripts/export_order_book_l2.py` y dropeada.

Fundamento: la mesa no usaba esas vistas y consumían RAM/sesiones
pyRofex que ahora se aprovechan para el live de tenencia.
