# Motor de Valuaciones (PnL Títulos)

Doc-checkpoint del motor de PnL por (cuenta, ticker). Estado al 2026-05-08.
Para retomar el desarrollo desde acá: leé esto + `api/services/pnl.py`.

---

## Objetivo

Calcular el **PnL real** por activo de cada cuenta. La filosofía es la
clásica de cash flows:

```
PnL = (lo que vale ahora)  +  (todo lo cobrado)  −  (todo lo pagado)
```

Tres componentes separados (cada uno tiene su KPI propio):

| Componente | Qué representa |
|---|---|
| **PNL REALIZADO** | Ganancias/pérdidas de ventas cerradas (operaciones que ya cerraron en cash) |
| **PNL NO REALIZADO** | Diferencia papel del stock vivo (`valor_aum − costo_remanente`) |
| **PNL PASIVO** | Cobros sueltos: cupones, dividendos, amortizaciones |

`PNL TOTAL = realizado + no_realizado + pasivo`.

---

## Arquitectura

```
                ┌───────────────────────────────────┐
                │  CashFlow.NegocioMovimientos      │  ← boletos (compra/venta/acreencia)
                └──────────────┬────────────────────┘
                               ↓
                ┌──────────────┴────────────────────┐
                │  Valuaciones.Assets               │  ← unidad → TICKER (mapping)
                └──────────────┬────────────────────┘
                               ↓
                ┌──────────────┴────────────────────┐
                │  Valuaciones.AuM                  │  ← posición actual (cantidad × precio)
                └──────────────┬────────────────────┘
                               ↓
                ┌──────────────┴────────────────────┐
                │  api/services/pnl.py              │  ← cost-basis weighted-average
                └──────────────┬────────────────────┘
                               ↓
                ┌──────────────┴────────────────────┐
                │  GET /api/portfolio/pnl?id_cuenta │
                └──────────────┬────────────────────┘
                               ↓
                ┌──────────────┴────────────────────┐
                │  /aum → VALUACIONES → PNL TÍTULOS │  (acaquant-web)
                └───────────────────────────────────┘
```

### Archivos clave

- **Backend**:
  - `api/services/pnl.py` — el motor (cost-basis, pesificación, output).
  - `api/routers/carteras.py` — endpoint `GET /api/portfolio/pnl`.
  - `api/services/_mep.py` — helper MEP (con fallback a `Valuaciones.Dolar`).
- **Frontend**:
  - `acaquant-web/src/components/pnl-titulos-view.tsx` — vista PNL TÍTULOS.
  - `acaquant-web/src/components/aum-view.tsx` — host (sub-tab dentro de Valuaciones).
  - `acaquant-web/src/app/api/aum-pnl/route.ts` — proxy.

---

## Lógica del cost-basis (cómo se computa el PnL)

Para cada ticker, en orden cronológico de boletos:

```python
state[ticker] = {
    qty_actual:       0,    # neta running
    costo_remanente:  0,    # cost basis del stock vivo (en ARS)
    pnl_realizado:    0,    # ganancias de ventas cerradas
    pnl_pasivo:       0,    # cupones / divs / amorts
}

para cada boleto en orden cronológico:
    si COMPRA (P, Q):
        costo_remanente += P × Q
        qty_actual      += Q

    si VENTA (P, Q):
        avg_cost          = costo_remanente / qty_actual
        pnl_realizado    += (P − avg_cost) × Q
        costo_remanente  -= avg_cost × Q   ← descuenta solo la porción "viva"
        qty_actual       -= Q

    si ACREENCIA:
        pnl_pasivo += importe   # no afecta cantidad
```

**Cálculo final por ticker**:
- `valor_aum`        = `Valuaciones.AuM` último snapshot (ya pesificado a ARS).
- `pnl_no_realizado` = `valor_aum − costo_remanente` (si hay qty viva).
- `pnl_total`        = `realizado + no_realizado + pasivo`.

**Importante**: NO recalcular `valor_calc = qty × precio_actual` — el `precio_actual`
del AuM viene en paridad cruda (sin /100 para bonos) y rompe el cálculo.
Siempre usar `valor_aum` que ya viene correcto desde `jobs/aum.py`.

---

## Mapping `unidad ↔ ticker`

`NegocioMovimientos.ticker` ("AO28") y `Valuaciones.AuM.unidad` ("[5921] AO28 - BONO TESORO NAC.")
no matchean directo. La fuente de verdad del mapping es `Valuaciones.Assets`:

```js
// Valuaciones.Assets ejemplo:
{
  unidad:   "[57108] OLC2O",        // ← cómo viene del AuM
  TICKER:   "OLC2O",                 // ← cómo viene de NegocioMovimientos
  CARTERA:  "CARTERA DL",
  EMISOR:   "OLEODUCTOS",
  ...
}
```

El motor llama a `_build_unidad_to_ticker_map(db_v)` que lee Assets y devuelve
`{ unidad → TICKER }`. Si el `TICKER` está vacío (gap de metadata), cae al
regex fallback `_ticker_corto_fallback`.

**Para corregir matchings rotos**: completar el `TICKER` en
`Valuaciones.Assets` desde la tab `/manager → ASSETS` (ya soporta filtros
por CARTERA / EMISOR + edición inline).

---

## Pesificación

Cada boleto en USD/USDC se convierte a ARS para sumar consistente. El campo
`mep` ya viene guardado en cada boleto desde `jobs/negocio_movimientos.py`
(snapshot inmutable del día).

**Path principal**: `importe_ars = importe × b["mep"]`.

**Fallback**: si `b["mep"]` es `null` (boleto sin reingestar o fecha pre-feed),
se llama a `get_mep_for_date(fecha)` que mira `Valuaciones.Dolar`.

**Si tampoco hay MEP** (fecha muy vieja, antes que el feed arrancara):
- Importe queda en moneda original.
- `fechas_sin_mep` en el output reporta esas fechas para que la UI muestre warning.

---

## Estado de los datos

### Lo que está limpio (al 2026-05-08)

- `NegocioMovimientos`: backfilled desde 2025-01-01 hasta 2026-05-06 con el
  parser corregido (commit `ef1e81d` + `ca20a44`). Cada boleto tiene `mep`
  inmutable cuando aplica.
- `Valuaciones.Assets`: campos lowercase duplicados removidos (commit `622ddd8`).
  El UPPERCASE es la fuente de verdad.
- `Valuaciones.AuM`: filtros de exclusión aplicados (USDL, OTC, CDC, contrapartes,
  cuenta 255 — ver `jobs/_aum_filters.py`).

### Bugs conocidos resueltos

| Fecha | Bug | Fix |
|---|---|---|
| 2026-05-07 | Boletos USD con `importe` y `moneda` mezclados (compra YM38O) | `aunesa_negocio.py` prioriza línea de dinero por `_parsed.moneda` (commit `ef1e81d`) |
| 2026-05-07 | Boletos "Licitación" ignorados por motor PnL | `categorizar()` mapea a "compra" (commit `a06b918`) |
| 2026-05-08 | PNL no realizado millonario absurdo en bonos | Usar `valor_aum` del AuM, no `qty × precio_actual` (commit `f9887df`) |
| 2026-05-08 | AO28 duplicado en tabla (boletos vs AuM como tickers distintos) | Mapping desde Valuaciones.Assets en vez de regex inventado (commit `1363fea`) |

### Limitaciones conocidas (no son bugs, son data)

1. **Posiciones pre-data**: cuentas con activos comprados antes de que arrancara el
   feed `NegocioMovimientos` (~Jul 2025) → flag `PARCIAL` o `SIN BOLETOS`.
   El cost basis está incompleto, el PnL no realizado queda subestimado.

2. **Fechas sin MEP**: anteriores al 25/3/26 (cuando arrancó el feed `Valuaciones.Dolar`).
   Boletos USD de esas fechas tienen `mep=null` → quedan en USD nominal sin pesificar.
   Se reporta en `fechas_sin_mep`.

3. **Canjes / corporate actions**: si AL30 se canjea por AL30D, el motor los ve
   como dos tickers distintos. No detecta que son la misma posición económica.
   Hay que manejar manualmente (a futuro).

---

## Cómo está la UI hoy

`/aum → VALUACIONES → PNL TÍTULOS` (dentro del selector de cuenta tipeable):

**4 KPIs arriba**:
- PNL TOTAL (no_realizado + pasivo — la posición actual; ver nota abajo)
- PNL NO REALIZADO (papel)
- PNL PASIVO (cupones/divs/amorts)
- VALOR ACTUAL (con costo_remanente como sub)

**Toolbar**: toggle `SOLO ACTIVOS (qty_aum > 0)` filtrado.

**Tabla** (8 columnas): TICKER · CANTIDAD · COSTO · VALOR ACTUAL · NO REALIZADO · COBROS · PNL TOTAL · FLAGS.

**Realizado fuera de la vista actual**: el motor backend sigue calculando
`pnl_realizado` y lo expone en la respuesta del endpoint, pero el frontend
lo oculta — confunde al lector porque mezcla performance histórica
(ventas cerradas, todas las cuentas que pasaron por la cuenta) con
la posición actual (lo que está vivo HOY). El plan es armar una vista
**histórica de realizado** separada (con filtros por fecha/ticker/cuenta)
cuando se priorice. Mientras tanto, la fórmula visible es:
`PNL TOTAL = pnl_no_realizado + pnl_pasivo`.

**Click en un row → expande** y muestra:
- Flujo de boletos (compras / ventas / neto / Δ AuM).
- Breakdown de cobros pasivos por op (Cash dividend / Interest payment / Partial redemption).
- **Tabla de boletos individuales** (FECHA, OP, CANT, PRECIO, IMPORTE, MON, MEP, IMPORTE ARS).
  Sirve para auditar cada KPI contra los boletos reales.

**Flags posibles** en cada fila:
- `PARCIAL` — `qty_calc < qty_aum` (boletos pre-data).
- `SIN BOLETOS` — posición visible en AuM sin ningún boleto matcheado.
- `USD/ARS` — operó el ticker en monedas mixtas, pesificado fecha por fecha.

---

## Para retomar mañana

### Issues abiertos a resolver

1. **PARCIAL es el caso más común con tu cuenta 805** — el motor solo ve boletos
   desde Jul 2025. Estrategia futura: cargar histórico previo manualmente
   (vía CSV importado a `NegocioMovimientos`) o aceptar la limitación.

2. **Otros tipos de operación primaria** que pueden estar como `categoria=otro`:
   - "Suscripción primaria"
   - "Adjudicación"
   - "Toma firme"

   Si aparecen, hay que sumarlos a `categorizar()` en `aunesa_negocio.py` y
   reingestar.

3. **Comisiones**: hoy `categoria=comision` se IGNORA en el motor PnL — la
   asunción es que el `importe` de cada boleto ya viene neto de comisión
   (validado con boleto YM38O). Pero hay docs con `categoria=comision` por
   "aval", "custodia" etc. que afectan el PnL global de la cuenta. Si en
   algún momento queremos un PnL por cuenta total (no por activo), hay
   que sumarlas.

4. **Optimización**: el motor ahora trae todos los boletos de la cuenta y los
   procesa en Python. Para cuentas con muchos boletos (ALYC con miles) puede
   tardar. Si llegamos a tener problemas de performance, mover a aggregation
   pipeline server-side.

5. **Nivel cuenta agregado**: el motor da PnL por ticker. Falta una vista
   "PnL agregado por cartera" (FCI / Tasa Fija / CER / etc). Trivial
   sumarizando `rows` por `cartera` (que viene del map).

---

## Endpoints relevantes

- `GET /api/portfolio/pnl?id_cuenta=805` — el motor en sí.
- `GET /api/portfolio/cuentas` — lista cuentas para el dropdown.
- `GET /api/manager/assets?cartera=X` — para corregir mapping de unidad ↔ ticker.
- `GET /api/manager/assets/values` — autocomplete (carteras/emisores).
- `PATCH /api/manager/assets` — editar UPPERCASE en Valuaciones.Assets (con audit).
