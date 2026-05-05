# Sesión 2026-05-05 — MVP Vista NEGOCIO en /operaciones

> Contexto operativo de la mesa: reciclado de la integración Aunesa
> `/operaciones/consolidadosGenerales` para construir una vista gerencial
> en `/operaciones/negocio` (acaquant-web). Si algo se rompe acá, la
> referencia es este doc.

## Resumen ejecutivo

Antes había un solo job (`jobs/cashflow.py`) que filtraba el endpoint
de Aunesa con 3 palabras clave (`deposito|transferencia|extraccion`)
y persistía sólo flujos de caja en `CashFlow.Movimientos`. El resto se
descartaba.

Ahora se construyó una capa de discovery + persistencia + vista que
**captura, parsea y categoriza TODOS los movimientos** (compras,
ventas, FCI super, FCI bilateral, acreencias, cauciones, depósitos,
extracciones, transferencias, comisiones, impuestos), con perspectiva
**cliente** (signo invertido vs el broker), normalizando dobles entradas
por boleto y deduplicando casos especiales (DIF/DIS en bilaterales,
multi-moneda en dividendos, líneas duplicadas en FCI super).

5 piezas: service compartido, job idempotente, endpoint, frontend con
tab NEGOCIO, y cron cada hora 12-22 ART L-V.

## Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│  Aunesa /operaciones/consolidadosGenerales (LIVE)                │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│  api/services/aunesa_negocio.py                                  │
│  - filtro pre-análisis (excluye OTC, USDL, "Integración          │
│    de garantías")                                                │
│  - parseo (regex boleto / caución / solicitud FCI / acreencia)   │
│  - categorización (16 categorías)                                │
│  - inversión de signo → perspectiva cliente                      │
│  - agrupación por comprobante con dedup específico:              │
│       solicitud_*_fci → quedarse con estado=DIS                  │
│       acreencia → priorizar línea non-ARS                        │
│       fci super → quedarse con líneas dinero uso=GRAL            │
│  - función pública: fetch_y_consolidar(fecha, tipos_cuenta)      │
└──────────────────────────────────────────────────────────────────┘
            │                            │
   ┌────────▼─────────┐          ┌──────▼─────────────────────┐
   │ jobs/            │          │ api/routers/manager/       │
   │  negocio_        │          │  aunesa.py                 │
   │  movimientos.py  │          │ GET /manager/aunesa/       │
   │ (cron horario)   │          │  explorar (admin discovery)│
   └────────┬─────────┘          └────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│  CashFlow.NegocioMovimientos (Mongo, idempotente)                │
│   uq (fecha, comprobante)                                        │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│  api/routers/operaciones.py                                      │
│   GET /api/operaciones/negocio/fechas                            │
│   GET /api/operaciones/negocio?fecha=                            │
│   gate _OPERACIONES (admin + trader, no sales)                   │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│  acaquant-web /operaciones tab NEGOCIO                           │
│   - selector fecha limitado a días con data                      │
│   - cards por categoría (16) con count + neto/abs + % del total  │
│   - top 20 tickers por volumen                                   │
│   - tabla detallada con filtros y búsqueda                       │
└──────────────────────────────────────────────────────────────────┘
```

## Schema de `CashFlow.NegocioMovimientos`

Un doc por boleto consolidado:

```js
{
  fecha:        "2026-05-04",       // ISO YYYY-MM-DD ART
  comprobante:  "BOL 2026069919",   // ID único de Aunesa
  cuenta:       "[805] MOLLO ...",
  categoria:    "compra",            // 16 valores posibles
  op:           "Compra",            // texto humano del op
  ticker:       "AL30",              // ticker corto, null si no aplica
  cantidad:     -1.00,               // signo cliente (+ entra al cliente)
  precio:       91410.00,            // precio o tasa% (cauciones)
  importe:      91410.00,            // plata movida, signo cliente
  moneda:       "ARS",               // ARS / USD / USDC / USDL
  plazo:        "Inm",               // CI / 24hs / Contado Inmediato / N días
  lugar:        "Local",             // Local / CV / A3 / etc
  estado:       "DIS",               // DIS / DIF / etc
  informacion:  "Compra [AL30]...",  // descripción raw
  n_lineas:     2,                   // # líneas raw que conforman el boleto
  ingestado_en: ISODate("...")       // último upsert
}
```

Índices:
- `uq (fecha, comprobante)` — unique, idempotencia.
- `(fecha, categoria)` — filter por segmento.
- `(fecha, cuenta)` — filter por cliente.
- `(fecha, ticker)` — filter por activo.

## Las 16 categorías

| Categoría | Significado |
|---|---|
| `compra` | Compra de bono/acción en mercado |
| `venta` | Venta en mercado |
| `suscripcion_fci` | Suscripción provisional/final FCI supermercado |
| `rescate_fci` | Rescate provisional/final FCI supermercado |
| `solicitud_suscripcion_fci` | Suscripción FCI bilateral (con sociedad gerente directa) |
| `solicitud_rescate_fci` | Rescate FCI bilateral |
| `acreencia` | Cupón / amortización / dividendo (Interest payment, Cash dividend, Partial redemption) |
| `caucion_col_ap` | Caución colocadora · Apertura |
| `caucion_col_ci` | Caución colocadora · Cierre |
| `caucion_tom_ap` | Caución tomadora · Apertura |
| `caucion_tom_ci` | Caución tomadora · Cierre |
| `caucion_otro` | Caución sin rol/fase identificable |
| `deposito` | Depósito en cuenta |
| `extraccion` | Extracción |
| `transferencia` | Transferencia |
| `comision` | Comisión cobrada |
| `impuesto` | Impuestos (DEB/CRED, IIBB, etc.) |
| `otro` | Fallback |

## Reglas de parseo / dedup (no inferibles)

### Inversión de signo
Aunesa devuelve `total` desde la perspectiva del **broker** (custodio). Para
mostrar al cliente, multiplicar por -1:
```python
total_cliente = -float(row["total"])
```
- Compra cliente → cantidad +, plata - (Aunesa raw lo manda al revés).
- Acreencia → ingreso + (Aunesa lo manda como egreso del lado broker).

### Doble entrada por boleto operativo
Compras/ventas y suscripciones FCI super tienen **2 líneas con el mismo
comprobante**: una en cuotapartes/título (`unidad="[ID] TICKER"`) y una en
moneda (`unidad="ARS"`). El consolidado las junta en 1 fila.

### FCI super: líneas dinero duplicadas
A veces hay 3-4 líneas dinero por boleto con distinto `uso`. Conservar **sólo
las que tienen `uso == "GRAL"`** para no contar plata múltiples veces.

### FCI bilateral: estados DIF y DIS
Las solicitudes (Solicitud de suscripción/rescate de FCI) vienen con 2
líneas duplicadas: una con `estado="DIF"` (asset contable, diferido) y otra
con `estado="DIS"` (plata real). Conservar **sólo DIS**. Aplicar tanto a
líneas título como a líneas dinero — los rescates bilaterales tienen las 2
líneas como cuotapartes (no ARS).

### Acreencias multi-moneda
Cuando un dividendo viene en USDC, también aparece una línea ARS chica
(comisión/IIBB residual). El importe real es la línea **non-ARS**. Para
acreencias con múltiples monedas, priorizar la que NO sea ARS.

### Filtros pre-análisis (excluidos completos)
`EXCLUIR_SUBSTRINGS = ("otc", "usdl", "integracion de garantias")` — si
`informacion` o `cuenta` contienen cualquiera, el movimiento se descarta
**antes** del análisis. Editar esa tupla al tope de `aunesa_negocio.py` para
sumar/quitar.

## Patrones regex del campo `informacion`

### Boletos (Compra/Venta + Suscripción/Rescate FCI super)
```
^<OP> [<TICKER>] <CANTIDAD>@<PRECIO> (<MONEDA> <PLAZO>)
```
- `<OP>` ∈ {`Compra`, `Venta`, `Suscripción provisional`, `Suscripción final`, `Rescate provisional`, `Rescate final`}.
- Cantidad y precio en formato AR (`1.234,56`).
- Plazo: `Inm`, `CI`, `24hs`, `48hs`, `Contado Inmediato`. CI = Contado Inmediato = Inm (sinónimos).

### Cauciones
```
^Caución <ROL> <MONEDA> <MONTO>@<TASA>% (<MONEDA> <DIAS> días) (<FASE>)
```
- `<ROL>` ∈ {`colocadora`, `tomadora`}.
- `<FASE>` ∈ {`Apertura`, `Cierre`}.
- En el output, `precio` = tasa%.

### Solicitudes FCI bilaterales
```
^Solicitud de <ACCIÓN> de FCI - [<ID>] <TICKER>
```
- `<ACCIÓN>` ∈ {`suscripción`, `rescate`}.
- No tiene cantidad ni precio en `informacion`. Se resuelven desde las
  líneas raw (DIS).

### Acreencias
```
... s/<TICKER>
```
El ticker está después de `s/` cerca del final de la descripción.
Ejemplos:
- `Interest payment (INTR) - DISN s/OTS3O` → ticker `OTS3O`.
- `Liquidación 658417 - Cash dividend (DVCA) - DISN s/JPM` → ticker `JPM`.

## Endpoints

### `GET /api/manager/aunesa/explorar?fecha=YYYY-MM-DD`
**Gate**: módulo `manager` (admin only).
**Uso**: discovery exploratorio en vivo. Pega a Aunesa cada vez (caro
para uso normal, sólo para debug/análisis).
**Devuelve**: meta + tipos + categorías + movimientos (raw enriquecido)
+ boletos consolidados.

### `GET /api/operaciones/negocio?fecha=YYYY-MM-DD`
**Gate**: módulo `operaciones` (admin + trader). **NO** sales.
**Uso**: vista de negocio del día. Lee de Mongo (no Aunesa). Default
fecha: hoy ART.
**Devuelve**: meta + agregados por categoría + top tickers + boletos.

### `GET /api/operaciones/negocio/fechas`
**Gate**: idem `operaciones`.
**Uso**: lista de fechas con data en `CashFlow.NegocioMovimientos`.
Sirve al frontend para limitar el selector.

## Job `jobs/negocio_movimientos.py`

Pega a Aunesa, consolida y upsertea. Idempotente.

```bash
python -m jobs.negocio_movimientos                  # hoy ART
python -m jobs.negocio_movimientos --fecha 2026-05-04
python -m jobs.negocio_movimientos --fecha 2026-05-04 --dry
```

Cron: `0 15-22 * * 1-5` (cada hora 12-22 ART L-V) en `deploy/crontab.txt`.

## Frontend `acaquant-web/src/components/negocio-view.tsx`

Layout gerencial:
1. **Header**: selector de fecha LIMITADO a días con data (dropdown del endpoint `/fechas`), navegación ‹/› entre días con data, botón "Última".
2. **Cards por categoría**: 16 posibles, click filtra la tabla. Cada card: count, importe neto/abs, # cuentas, # tickers, monedas, % del total.
3. **Top 20 tickers**: leaderboard horizontal. Click filtra la tabla.
4. **Tabla detallada**: 11 columnas con cantidades e importes coloreados por signo cliente. Buscador full-text.

Tab default en `/operaciones`.

## Política importante

`/api/operaciones/*` está bajo el gate `_OPERACIONES` y **bloqueado al
asistente y al MCP** por policy (memoria `project_focus_apis_mcp.md` y
`BLOCKED_PATH_PREFIXES` en `api/agent/`). NO exponer estas tools a Claude
desktop ni a chat — la vista de negocio es uso interno de la mesa.

## Pendientes / siguiente fase

1. **Cross-check con AuM** — reconstruir posiciones por cliente desde
   movimientos y comparar contra `Valuaciones.AuM`.
2. **P&L por cliente** — costo promedio + valuación actual.
3. **Filtro por cuenta (drill-down)** — dado un cliente, ver toda su
   actividad histórica.
4. **Filtro por ticker** — actividad agregada por activo.
5. **Resolver doble entrada en boletos no estándar** — algunos casos
   raros pueden quedar como "lineas: 1" sin agrupar bien.
6. **Aclarar con la mesa**:
   - `estado: DIS` vs `DIF` (parcial: DIS = real, DIF = diferido contable).
   - `uso: GRAL` vs `CVCUS` vs otros.
   - Significado completo de `lugar` (`Local`, `CV`, `A3`, etc.).
