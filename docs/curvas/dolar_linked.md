# Curva `dolar_linked` — shape de `Trading.Curvas`

Bonos cuyo valor nominal está denominado en USD pero pagan en ARS al
tipo de cambio de referencia del momento del pago. Estructura paralela
a `cer` (porcentual sobre VN) con `tc_emision` jugando el rol de
`cer_emision` y la tasa de referencia siendo el TC en lugar del CER.

## Shape canónico

```json
{
  "ticker": "MERV - XMEV - <ticker_corto> - 24hs",
  "ticker_corto": "TZV26",
  "tipo": "dolar_linked",
  "curva": "dolar_linked",
  "tasa_referencia": "A3500",
  "tc_emision": 838.95,
  "fecha_emision": "2024-02-28",
  "fecha_vencimiento": "2026-06-30",
  "valor_nominal": 100,
  "cupon_anual": 0,
  "flujos": [
    { "fecha": "2026-06-30", "amortizacion_pct": 100 }
  ]
}
```

## Semántica de cada campo

| Campo | Tipo | Descripción |
|---|---|---|
| `ticker` | string | Ticker completo de ROFEX (`MERV - XMEV - <X> - 24hs`). |
| `ticker_corto` | string | Ticker corto (debe coincidir con `Valuaciones.Assets.TICKER`). |
| `tipo` | string | Siempre `"dolar_linked"`. Análogo a `"lecap"`/`"boncap"`/`"bonares"`/etc. en otras curvas. |
| `curva` | string | Siempre `"dolar_linked"` (filtra `listar_curva`). |
| `tasa_referencia` | string | TC al que se ajusta el bono. Hoy: `"A3500"` (BCRA mayorista, fixing diario en `Trading.DOLAR`). Si el día de mañana hay un bono con otra ref (oficial, mep), se distinguen acá. |
| `tc_emision` | number | TC al momento de la emisión, según el prospecto. Sirve para reportar `paridad_de_emision`. **No se usa en el cálculo del flujo en pesos** — el flujo siempre se valúa al `tasa_referencia` del momento del pago. |
| `fecha_emision` | string | YYYY-MM-DD. |
| `fecha_vencimiento` | string | YYYY-MM-DD. |
| `valor_nominal` | number | VN en USD. Convencional: 100. |
| `cupon_anual` | number | Tasa de interés nominal anual en USD. `0` para zero coupon (letras). |
| `flujos` | array | Mismo shape que `cer` y `soberanos` (porcentual). Cada flujo: `{fecha, amortizacion_pct, cupon_sobre_residual?, residual_previo_pct?}`. Para zero coupon basta con `{fecha, amortizacion_pct: 100}`. |

## Fórmula del flujo en pesos

```
flujo_ARS(fecha_pago) = (amortizacion_pct/100 · VN + cupon_sobre_residual/100 · VN) × TC(fecha_pago)
```

Donde `TC(fecha_pago)` se lee según `tasa_referencia`:

- `"A3500"` → en runtime usamos `Valuaciones.DolarOficial` casa=`"mayorista"`
  (escrito por `jobs/dolar_api.py`, cron 5 min en horario rueda) como
  proxy intra-day del A3500. **NO usamos `Trading.DOLAR` (BCRA fixing
  diario)**: aunque el A3500 oficial sea el TC pactado, ese fixing se
  publica 1 vez por día y daría paridades/TEAs stale durante el día.

**`tc_emision` no entra en la fórmula del flujo**. Aparece sólo como
referencia para reportar paridad inicial / brecha vs TC actual.

## Ejemplos

### TZV26 — letra zero coupon

Datos del prospecto BCBA:

| Campo | Valor |
|---|---|
| Emisor | Argentina |
| Tipo de activo | Títulos Públicos |
| Emisión | 2024-02-28 |
| Vencimiento | 2026-06-30 |
| Moneda denom. | USD |
| Moneda pago | ARS |
| Ticker | TZV26 |
| ISIN | AR0788694542 |
| Legislación | Argentina |
| Convención int. | 180/360 EU |
| Periodicidad int. | Nula |
| Tipo de cupón | Cero |
| Lámina mín. | 1 |
| TC aplicable | A3500 |
| TC inicial | 838,95 |

Doc en `Trading.Curvas`:

```json
{
  "ticker": "MERV - XMEV - TZV26 - 24hs",
  "ticker_corto": "TZV26",
  "tipo": "dolar_linked",
  "curva": "dolar_linked",
  "tasa_referencia": "A3500",
  "tc_emision": 838.95,
  "fecha_emision": "2024-02-28",
  "fecha_vencimiento": "2026-06-30",
  "valor_nominal": 100,
  "cupon_anual": 0,
  "flujos": [
    { "fecha": "2026-06-30", "amortizacion_pct": 100 }
  ]
}
```

Validación contra el CSV del prospecto (`docs/TZV26.csv`):

| Campo CSV | Valor | Cómo se reproduce |
|---|---|---|
| Flujo c/100 VN al vto | $141,298.65 ARS | VN=100 × TC(2026-06-30) — el CSV simuló con TC=1,412.99 al 2026-04-27 (= 100 × 1412.99 ≈ 141,299) |
| Amortización c/100 VN | 100.00% | `amortizacion_pct: 100` del flujo único |
| Interés c/100 VN | 0.00% | `cupon_anual: 0` |

### Bono dolar-linked cuponado (hipotético, para cuando aparezca)

Mismo shape pero con varios flujos y `cupon_anual > 0`:

```json
{
  "ticker": "MERV - XMEV - TVPP24 - 24hs",
  "ticker_corto": "TVPP24",
  "tipo": "dolar_linked",
  "curva": "dolar_linked",
  "tasa_referencia": "A3500",
  "tc_emision": 850.00,
  "fecha_emision": "2024-01-01",
  "fecha_vencimiento": "2027-01-01",
  "valor_nominal": 100,
  "cupon_anual": 0.04,
  "flujos": [
    { "fecha": "2025-01-01", "amortizacion_pct": 0,    "cupon_sobre_residual": 4.0, "residual_previo_pct": 100 },
    { "fecha": "2026-01-01", "amortizacion_pct": 0,    "cupon_sobre_residual": 4.0, "residual_previo_pct": 100 },
    { "fecha": "2027-01-01", "amortizacion_pct": 100,  "cupon_sobre_residual": 4.0, "residual_previo_pct": 100 }
  ]
}
```

Cada `cupon_sobre_residual` es % de VN en términos USD. Al cobrarlo se
multiplica por `TC(fecha_pago)` para obtener el flujo en ARS.

## Enriquecimiento por el motor (`engines/curvas.py`)

Para que la curva tenga TEA / duration / paridad, el motor necesita una
rama nueva análoga a la de CER pero con TC. Resumen:

- **Precio actual** sale de `Trading.TimeSales` (precio en pesos del trade).
- **Valor par hoy**: `VN × TC_actual` donde `TC_actual` es el último doc
  de `Trading.DOLAR` para `casa="A3500"` (o el último valor diario).
- **Paridad** = `precio_pesos / valor_par`. Análogo a CER paridad.
- **TEA en USD**: VPN(flujos en USD nominal) descontados a `TIR_USD`
  resolviendo `precio_USD = sum(flujos_USD / (1+TIR)^(t/365))`. El
  `precio_USD = precio_pesos / TC_actual`. La TEA es real en USD —
  sirve para comparar contra otras curvas en USD (soberanos hard-dollar).
- **Duration / convexity**: idénticas a CER pero con `freq_dias=365`
  (TEA anual).

## Visibilidad en AuM / Portfolios

Como cualquier otra curva: el bono no aparece en AuM hasta que tenga
doc en `Valuaciones.Assets` con `TICKER == ticker_corto`, `unidad`
único, `CARTERA`, `EMISOR`, `CLASE_ACTIVO`, `CALIFICACION`,
`VENCIMIENTO`. Sin esto la chain `Trading.Curvas → Valuaciones.Assets
→ Valuaciones.AuM` se rompe en el join intermedio.
