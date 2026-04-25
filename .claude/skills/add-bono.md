---
name: add-bono
description: Workflow para agregar un bono nuevo (tasa_fija / CER / soberano) al sistema. Incluye seed en Trading.Curvas, registro en Valuaciones.Assets, validación de flujos, y visibilidad en el frontend.
---

# Agregar un bono nuevo

Se aplica cuando el usuario pide "agregar el bono X" o "seedear un bono nuevo". Son 5 pasos obligados más 2 opcionales.

## 1. Definir metadata básica

Preguntá al usuario (si no lo dio):
- **Ticker completo** (ej. `MERV - XMEV - T17O5 - 24hs`).
- **Ticker corto** (ej. `T17O5`). Tiene que matchear con `Valuaciones.Assets.TICKER`.
- **Curva**: `tasa_fija`, `cer`, `soberanos`, o `dolar_linked`.
- **Tipo** (para soberanos): `globales` (ley NY) o `bonares` (ley AR).
- **Fecha emisión** y **fecha vencimiento** (YYYY-MM-DD).
- **Valor nominal** (típicamente 100).
- **Fuente de flujos** — CSV BCBA, prospecto, o manual.

## 2. Insertar doc en `Trading.Curvas`

Estructura según curva (ver `CLAUDE.md` sección "Trading.Curvas — flujos"):

- **tasa_fija** — flujos con campos absolutos (`amortizacion` + `interes`). Rellená `cupon_anual` con la tasa anual nominal. Necesitás `flujo_vencimiento` (el monto final en ARS).
- **cer** — flujos porcentuales: `amortizacion_pct` + `cupon_sobre_residual` **ya resuelto** (NO re-multiplicar por `residual_previo_pct`). `cupon_anual = 0` si es zero coupon. Requiere `cer_emision`.
- **soberanos** — mismo shape que CER pero `cupon_sobre_residual` ya en USD. Agregá `tipo: 'globales'` o `'bonares'`.

Si hay CSV BCBA o similar, proponé un script one-shot en `scripts/` (borrable post-seed). Idempotencia: usar `update_one({ticker}, {$set: doc}, upsert=True)`.

## 3. Insertar doc en `Valuaciones.Assets`

El `unidad` (PK en Aunesa) + metadata: `CARTERA`, `EMISOR`, `TICKER == ticker_corto`, `CLASE_ACTIVO`, `CALIFICACION`, `VENCIMIENTO`. Sin este doc, el bono no aparece en los joins de AuM Tasa Fija / CER / Portfolios.

Índice unique en `unidad` — ojo con duplicados si el bono ya existía en otra serie.

## 4. Validar con los motores

Una vez insertado:

1. Si los motores de mercado están corriendo (L-V 13:00-20:05 UTC), `engines/curvas.py` debería agarrar el bono en su próximo loop (≤ 5s) y enriquecer `Trading.TimeSales` con `TEA/Duration/Paridad` en cuanto haya trades.
2. Chequeo: `python -m scripts.test_enrich_one` (si existe) o query directa a TimeSales.
3. `GET /api/analitica/listar-curva?curva=<curva>` — el bono tiene que aparecer.

## 5. Confirmar en frontend

- `/renta-fija` → tab de la curva correspondiente.
- Para soberanos: confirmar que pinta con el color correcto (globales verde, bonares naranja) en `/renta-fija` chart + en `/retorno (Estrategia) → SENSIBILIDAD`.

## 6. Opcional — histórico desde CSV

Si el bono ya tiene operación previa y hay CSV con precios diarios (ej. Reuters), cargar con un script one-shot en `scripts/` que haga `insert_many` idempotente en `Trading.TimeSales` (clave `(ticker, timestamp)`).

## 7. Opcional — breakevens

Si es un CER o Lecap cuyo vto matchea con algún par existente (`MAX_DIFF_DIAS=20`), el par aparecerá automáticamente en `engines/breakevens.py` al próximo tick (30s). Chequeo: `/api/cotizaciones/breakevens`.

## Criterios de éxito

- ✓ Bono aparece en `GET /api/analitica/listar-curva`.
- ✓ Pinta en el chart de `/renta-fija`.
- ✓ `Trading.TimeSales` tiene docs enriquecidos con `duration` y `TEA`.
- ✓ Si corresponde, aparece en `/api/portfolio/tasa-fija` o `/cer`.
