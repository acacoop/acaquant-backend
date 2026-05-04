# MCP Tools Reference — TradingAV

> Documento de referencia para LLMs que consumen las 34 tools del MCP server `https://api.acaquant.com/mcp`. Pensado para alimentar el contexto de Claude (Custom Connector / Project knowledge) y acelerar la decisión de qué tool usar para cada pregunta del usuario.

## Qué expone este MCP

Datos de mercado **argentino** en lectura: curvas de tasa fija (Lecaps/Boncaps), CER, soberanos hard-dollar (Globales/Bonares), TAMAR, dólar-linked, forwards entre tasas, breakevens de inflación, futuros DLR (Rofex), cauciones, MEP/CCL/canje, opciones GGAL, expectativas REM del BCRA, series macro, **order book live (depth 5) de bonos**, y **order book L2 histórico** (cada cambio del book persistido tick-a-tick para tickers seleccionados).

## Qué NO expone (y no hay que asumir que existe)

- Portfolio, posiciones, AuM del usuario.
- Operaciones / órdenes / cuentas.
- Datos de management / RBAC / usuarios.
- Cualquier dato no público o no de mercado.

Si el usuario pide algo de esos rubros, el connector no puede ayudar — hay que decirlo explícitamente.

---

## Convenciones críticas (leer antes de usar cualquier tool)

| Convención | Detalle |
|---|---|
| **Tasas** | **Por default decimales** (`tea = 0.092` = 9.2%). Pero hay excepciones donde el dato viene en %: tirs param de `sensibilidad_retorno` (CSV "4,5,6"), TNA de `cauciones_live/historico` (26.1 = 26.1%), `tasa_implicita_tna` de `futuros_dlr_live` (TNA, no TEA), `canje` de `mep_actual` (4.11 = 4.11%), valores de IPC en `rem_expectativas` (%), y `carry_usd` de `carry_trade.tabla` (%). Cada tool detallada abajo aclara su unidad. **Antes de mostrar al usuario, leé la unidad real de la tool — no asumas decimal.** |
| **Fechas** | Inputs y outputs en `YYYY-MM-DD` (ISO date) o `YYYY-MM-DDTHH:MM:SSZ` (ISO datetime UTC). |
| **Tickers** | "Corto" = `TX26`, `AL30`, `S15D5`, `GFGC10950A`. "Completo" = `MERV - XMEV - TX26 - 24hs`. La mayoría de tools aceptan ambos. |
| **Curvas válidas** | `tasa_fija`, `cer`, `soberanos`, `tamar`, `dolar_linked`. **No hay curva `lecap` ni `boncap`**: ambas viven dentro de `tasa_fija`. |
| **Reasignación CER → tasa_fija** | Si un bono CER ya tiene su CER de liquidación publicado por BCRA, aparece en `tasa_fija` con `cer_fijado: true` (no en `cer`). |
| **Nulls** | Muchos campos pueden venir `null` si el bono no operó / falta dato. Validar antes de operar matemáticamente. |
| **Errores** | Algunas tools devuelven `{"error": "..."}` (dict), otras devuelven `[]` o `{}` vacío. No hay estándar uniforme — chequear ambas posibilidades. |
| **Cache** | Las tools live tienen TTL 5–10s; los snapshots históricos 60–300s. No es necesario refetch agresivo. |
| **Atlas pausa** | La DB Mongo está pausada **04:00–11:20 UTC** todos los días (ahorro). Durante esa ventana las tools tiran error de conexión — no es un bug, es ventana operativa. |

---

## Cheat sheet — qué tool para qué pregunta

| Pregunta del usuario | Tool primaria | Combinar con |
|---|---|---|
| "¿Qué Lecaps hay vivas?" / "Listame los CER" | `listar_curva` | — |
| "¿Cómo cerró la curva tasa fija el 15 de marzo?" | `snapshot_curva_historico` | — |
| "¿Está empinándose la curva CER?" | `pendiente_curva` | con `fecha_comparacion` |
| "¿Cuánto se opera el TX26 vs su promedio?" | `liquidez_secundario` | — |
| "Trades del último mes del AL30" | `historico_trades` | — |
| "Series de cierre de toda la curva tasa fija" | `historico_curva` | — |
| "¿Cómo está el book del TX26 ahora?" / "Profundidad del AL30" | `order_book` | — |
| "Books de toda la curva CER" / "Spread + depth de tasa fija" | `order_books_curva` | — |
| "Cómo evolucionó el spread de AL30 CI entre 14:00 y 14:30" | `order_book_historico` | — |
| "Qué tickers tienen captura L2 activa hoy" | `listar_tickers_orderbook_l2` | — |
| "¿Qué Lecap convino comprar entre A y B?" (post-mortem) | `descomposicion_retorno` | `historico_trades` para contexto |
| "¿Qué Lecer convino el último mes?" (post-mortem CER) | `descomposicion_retorno` (curva="cer") | — |
| "¿Qué Lecap conviene a 30 días si la curva no se mueve?" | `rolldown_esperado` | — |
| "Ranking de Lecers por carry+roll a 60 días" | `rolldown_esperado` (curva="cer") | — |
| "Si la TIR de Globales sube a 11%, ¿cuánto pierdo?" | `sensibilidad_retorno` | — |
| "Forwards vivos entre Lecaps" | `forwards_live` | — |
| "¿Cómo evolucionó el forward marzo-junio?" | `forwards_historico` | — |
| "¿El forward A-B está caro o barato vs su historia?" | `forwards_zscore` | combinar con `forwards_live` |
| "¿Qué bonos están baratos vs su curva fair value?" | `fair_value` | — |
| "Evolución del z-score temporal del T30A7" | `fair_value_historico_bono` | — |
| "¿Qué inflación implícita hay entre TX26 y S15D5?" | `breakevens_live` | — |
| "Histórico del breakeven a 6 meses" | `breakevens_historico` | — |
| "TNA caución en pesos hoy" | `cauciones_live` | — |
| "Caución histórica último mes" | `cauciones_historico` | — |
| "Futuros del dólar Rofex" | `futuros_dlr_live` | — |
| "Histórico DLR/MAY26" | `futuros_dlr_historico` | — |
| "MEP hoy" / "CCL ahora" | `mep_actual` | — |
| "MEP últimos 90 días" | `mep_historico` | — |
| "Canje AL30 último año" | `canje` | — |
| "Carry trade en USD curva CER" | `carry_trade` | — |
| "TAMAR último trimestre" / "Riesgo país hoy" | `serie_macro` | — |
| "¿La TAMAR está alta o baja vs últimos 90 días?" | `clasificar_nivel` | — |
| "Inflación esperada por el REM" | `rem_expectativas` | — |
| "Chain de calls de GGAL" | `opciones_chain` | `opciones_meta` para tasa libre de riesgo |
| "Histórico de la GFGC10950A" | `opciones_historico` | — |
| "¿Cuál es la tasa libre de riesgo configurada para opciones?" | `opciones_meta` | — |

---

## Tools detalladas

Cada tool listada con: **descripción** · **params** · **retorno** · **ejemplos de prompt** · **gotchas**.

---

### Curvas y bonos

#### `listar_curva`
**Para qué:** Lista bonos vigentes de una curva con su estado actual (precio, TEA, duration, volumen, etc.). Es la tool más usada — punto de entrada para cualquier pregunta sobre "qué hay en la curva X".

**Params:**
- `curva: str` (req) — `tasa_fija | cer | soberanos | tamar | dolar_linked`
- `ordenar_por: str = "vencimiento"` — `vencimiento | volumen | tea | duration`
- `vencimiento_min_meses: float | None`
- `vencimiento_max_meses: float | None`
- `limit: int | None`

**Retorna:** `list[dict]` con: `ticker`, `ticker_corto`, `tipo`, `fecha_vencimiento` (ISO), `fecha_emision`, `meses_al_vto`, `ultimo_precio`, `tea`, `tem`, `paridad`, `duration`, `mod_duration`, `convexity`, `total_money_dia`, `total_nominals_dia`, `ts_ultimo_trade`, `cer_fijado` (opcional, true si CER reasignado a tasa_fija), `tc_breakeven` (sólo `curva="tasa_fija"`).

**Prompts típicos:**
- "Listame las Lecaps ordenadas por TEA"
- "¿Qué bonos CER vencen entre 6 y 18 meses?"
- "Top 5 soberanos por duration"
- "¿Qué TC necesito a vto del S30A6 para empatar contra MEP?"

**Gotchas:**
- TEA viene en decimales: `0.092` → mostrar como "9.2%".
- Bonos sin trades del día tienen `ultimo_precio: null`.
- `cer_fijado: true` significa que ese bono está en `tasa_fija` aunque originalmente sea CER (porque el CER de liquidación ya está publicado).
- `tc_breakeven` = MEP × (flujo_vencimiento / ultimo_precio). En ARS por dólar, ya redondeado. `null` si falta MEP o el bono no es tasa fija.

---

#### `snapshot_curva_historico`
**Para qué:** Reconstruye la curva entera tal como cerró un día pasado.

**Params:** `curva: str`, `fecha: str` (YYYY-MM-DD).

**Retorna:** Mismo shape que `listar_curva` pero con el último trade de **ese día** (no hoy).

**Prompts típicos:**
- "¿Cómo estaba la curva CER el 15 de octubre?"
- "Snapshot tasa fija al cierre del viernes pasado"

**Gotchas:** Solo incluye bonos que operaron ese día (los que no operaron no aparecen — no devuelve "estado vivo de hace una semana", devuelve "lo que se cruzó ese día específico"). Para el último trade efectivo de cada bono cualquier fecha, usar `historico_trades` con filtro.

---

#### `pendiente_curva`
**Para qué:** Mide el spread (largo - corto) de una curva en bps, y opcionalmente lo compara con un día pasado para ver empinamiento/aplanamiento.

**Params:**
- `curva: str`
- `metrica: str = "tea"` (`tea | tem | duration`)
- `fecha_comparacion: str | None` (YYYY-MM-DD)
- `dias_min_corto: int = 30` — excluye bonos del anchor "corto" con menos de N días al vencimiento. Default 30 evita TEAs ruidosas de fin de plazo que inflan artificialmente el spread. Bajalo a 0 para incluir todos.

**Retorna:** `dict` con `pendiente_actual_bps`, `corto: {ticker, duration, [metrica]}`, `largo: {ticker, duration, [metrica]}`, y si `fecha_comparacion` se provee: `pendiente_comparacion_bps`, `delta_bps`, `interpretacion` (`empinamiento | aplanamiento | sin cambio material`).

**Prompts típicos:**
- "¿Está empinándose la curva tasa fija?"
- "Pendiente CER hoy vs hace una semana"

**Gotchas:**
- "Largo" y "corto" se determinan por **duration**, no por vencimiento. La diferencia es chica para Lecaps zero coupon, pero importa en bonos con cupones.
- Si querés ver el spread "puro" entre primer y último bono sin filtro, pasar `dias_min_corto=0` — pero el resultado puede ser ruido si el primer bono está a días de vencer.

---

#### `liquidez_secundario`
**Para qué:** Compara el volumen del día de un bono contra su promedio de las últimas N ruedas. Útil pre-trade.

**Params:** `ticker: str` (corto o completo), `dias: int = 20`.

**Retorna:** `dict` con `volumen_dia_actual`, `volumen_promedio_dia`, `ratio_vs_promedio`, `clasificacion` (`baja | media | alta | anomalamente_alta | sin_datos`).

**Prompts típicos:**
- "¿Hay liquidez en el TX26 hoy?"
- "Volumen del AL30 vs su promedio"

---

#### `historico_trades`
**Para qué:** Trades raw (tick-level) de un instrumento en los últimos 15 días.

**Params:** `instrumento: str | None` — corto o completo. Omitir = todos los instrumentos.

**Retorna:** `list[dict]` con `instrumento`, `timestamp`, `price`, `size`, `side` (buy/sell), `money` (ARS), `duration`, `TEA`, `TEM`, `paridad`. **Máx 10000 registros**, ventana fija de 15 días.

**Prompts típicos:**
- "Trades del TX28 esta semana"
- "Últimas operaciones del AL30"

**Gotchas:** Si pedís un instrumento sin filtro, podés volar el contexto. Filtrar siempre por `instrumento` para prompts del usuario.

---

#### `historico_curva`
**Para qué:** Series diarias de cierre para todos los bonos de una curva. Una entrada por (fecha, ticker).

**Params:** `curva: str`.

**Retorna:** `list[dict]` con `fecha`, `ticker`, `tipo`, `price`, `TEA`, `TEM`, `duration`, `paridad`. Solo días con trades.

**Prompts típicos:**
- "Histórico de cierres de toda la curva CER"
- "Series de TEA diarias para Lecaps"

**Gotchas:** Output puede ser **muy grande** (cientos de bonos × cientos de días). Considerar pedir `historico_trades` filtrado por instrumento si el usuario quiere uno solo.

---

### Order Book (LOB live, depth 5, sin histórico)

#### `order_book`
**Para qué:** Order book live de un ticker de renta fija ARG con profundidad 5. La fuente es `Trading.MarketSnapshot`, que el motor `MicrostructureEngine` (engines/valores.py) replica desde pyRofex WS cada 1s. **Sin histórico** — solo el último estado.

**Params:** `ticker: str` — corto (`TX26`) o completo (`MERV - XMEV - TX26 - 24hs`).

**Retorna:** `dict | None`
```python
{
  "ticker":     "MERV - XMEV - TX26 - 24hs",
  "updated_at": "2026-05-01T15:32:08Z",
  "book": {
    "bids":   [{"price": 1.234, "size": 100}, ...x5],   # nivel 1 = best bid
    "offers": [{"price": 1.236, "size": 150}, ...x5],   # nivel 1 = best ask
  },
  "metrics": {
    "last_price":    1.235,
    "open_price":    1.230,
    "high_price":    1.240,
    "low_price":     1.225,
    "closing_price": 1.232,   # del cierre anterior
  },
}
```
`None` si el ticker no está en el universo de `Trading.Curvas` o nunca recibió market data.

**Prompts típicos:**
- "¿Cómo está el book del TX26 ahora?"
- "Profundidad bid/ask del AL30"
- "¿Cuál es el spread top of book del S30A6?"

**Gotchas:**
- **Latencia real**: el motor escribe a Mongo cada 1s. El dato puede tener hasta 1 segundo de delay vs el mercado real. Para HFT no sirve; para análisis de microestructura sí.
- **Listas pueden ser < 5**: si el ticker tiene poca profundidad de mercado, vienen 1, 2 o 3 niveles. No asumir longitud 5.
- **Fuera de rueda**: durante feriados, fin de semana o pre-apertura, `updated_at` puede ser viejo (último tick conocido) y los precios stale. Validar con `updated_at`.
- **Sin métricas analíticas**: esta tool intencionalmente NO trae TEA/duration/paridad. Para esas usar `listar_curva` o `serie_macro` con `<TICKER>.<CAMPO>`.

---

#### `order_books_curva`
**Para qué:** Lo mismo que `order_book` pero para todos los tickers de una curva en una sola llamada. Útil para análisis comparativo de liquidez, microestructura o relative value batch.

**Params:** `curva: str` — `tasa_fija | cer | soberanos | tamar | dolar_linked`.

**Retorna:** `list[dict]` — array de docs con la misma forma que `order_book` (un elemento por ticker de la curva). Tickers que nunca recibieron market data NO aparecen.

**Prompts típicos:**
- "Books de toda la curva CER"
- "Spread + depth de todas las Lecaps"
- "¿Qué bonos de la curva soberanos tienen mejor liquidez en el book?"

**Gotchas:**
- **Tamaño**: una curva con 30 tickers ~15 KB. Es razonable, pero no llamar repetidamente — caches solo la lista de tickers (TTL 5 min), los books son fresh read.
- **`updated_at` por ticker**: distintos tickers pueden tener distintos `updated_at` (operaron hace distinto tiempo). No es un timestamp global.
- **Reasignación CER → tasa_fija**: a diferencia de `listar_curva`, esta tool NO reasigna. Si pedís `curva="cer"` vas a recibir TODOS los CER (incluso los ya fijados). Para distinguir, cruzar con `listar_curva`.

---

#### `order_book_historico`
**Para qué:** Series temporales del LOB L2 entre dos timestamps. A diferencia de `order_book` (último estado vivo), devuelve la serie completa de cambios del book — cada vez que bids u offers cambiaron en algún nivel se persistió como un doc nuevo. Útil para análisis de microestructura: evolución del spread, depth, imbalance, velocidad de updates, replay de eventos.

**Cobertura:** Solo tickers en `config.TICKERS_BOOK_FULL` del backend (hoy: AL30 CI). Usar `listar_tickers_orderbook_l2` para confirmar qué hay disponible.

**Params:**
- `ticker: str` — **debe ser el ticker COMPLETO** (`MERV - XMEV - AL30 - CI`). El corto NO resuelve acá.
- `desde: str | None` — ISO datetime (`2026-05-04T13:00:00Z`) o fecha (`2026-05-04`). Default: 1h atrás de `hasta`.
- `hasta: str | None` — ISO datetime o fecha. Default: ahora UTC.
- `limit: int = 1000` — max 10000.

**Retorna:** `list[dict]` ordenado ascendente por `ts`:
```python
[
  {
    "ts":     "2026-05-04T13:30:01.234Z",
    "ticker": "MERV - XMEV - AL30 - CI",
    "bids":   [{"price": 1234.5, "size": 100}, ...x5],
    "offers": [{"price": 1234.8, "size": 75}, ...x5],
  },
  ...
]
```

**Prompts típicos:**
- "Mostrame cómo evolucionó el spread del top of book de AL30 CI entre 14:00 y 14:30"
- "Cuántos cambios de book tuvo AL30 CI hoy en la primera hora de rueda"
- "Calculá el order imbalance promedio de AL30 CI durante la rueda"

**Gotchas:**
- Si la ventana excede `limit` docs, devuelve los **primeros `limit`** (no los últimos). Pedir ventana más chica si querés cobertura completa.
- Cobertura limitada: solo tickers en whitelist. Para ampliar requiere agregar al config y reiniciar el motor `motor_order_book_l2`.
- Persistencia es event-driven (cada cambio del book), no muestreada. Un ticker líquido puede tener miles de docs por minuto en horario activo.

---

#### `listar_tickers_orderbook_l2`
**Para qué:** Lista de tickers que tienen captura L2 histórica activa en `Trading.OrderBookL2`. Útil para saber qué activos podés pasar a `order_book_historico` antes de pedir data.

**Params:** ninguno.

**Retorna:** `list[str]` con tickers completos.

**Prompts típicos:**
- "¿Qué tickers tienen captura L2 disponible para análisis histórico?"

---

### Atribución de retorno (Lecap / Boncap / Lecer)

#### `descomposicion_retorno`
**Para qué:** Atribución **ex-post** del retorno entre dos fechas. Descompone en `carry` (paso del tiempo), `rolldown` (acortamiento de plazo) y `cambio_tasa` (movimiento real de la curva). Soporta `tasa_fija` (default) y `cer`.

**Params:** `desde: str`, `hasta: str` (ambas YYYY-MM-DD), `metodo: str = "lineal"` (`lineal | cuadratica`), `curva: str = "tasa_fija"` (`tasa_fija | cer`).

**Retorna:** `dict` con `desde`, `hasta`, `dias`, `metodo`, `curva`, `bonos: list[dict]`, `promedio_simple`.

Cada bono trae:
- Comunes: `ticker`, `tipo`, `valor_ini/fin` (precio sucio para tasa_fija, paridad para cer), `tasa_ini` (TEM o TEA real), `r_total`, `carry`, `rolldown`, `cambio_tasa`, `tasa_curva_ini_at_dias_fin`.
- Solo `curva=cer`: `is_zero_coupon` (bool), `cer_accrual` (variación del CER del período), `r_total_ars` (retorno total compuesto: `(1+r_paridad)·(1+cer_accrual) − 1`).

Solo `curva=cer`: el dict raíz incluye `cer_accrual_periodo` y `cer_debug` (CER ini/fin, fechas).

**Prompts típicos:**
- "¿Por qué el TX26 rindió tanto entre marzo y abril?"
- "Atribución de las Lecaps el último mes"
- "Descomponé el retorno de los CER del último mes" → usar `curva="cer"`

**Gotchas:**
- `tasa_fija`: incluye Lecap + Boncap zero coupon.
- `cer`: incluye Lecers (zero coupon) + Boncers cupón en la tabla, pero la **curva de referencia para el rolldown** se construye **solo con Lecers** (excluyendo TX26/TX28 cuyas TEAs comprimidas por cupones ensucian la pendiente).
- Excluye bonos que vencieron entre las fechas.
- `metodo: cuadratica` es más fiel pero requiere ≥3 puntos en la curva.

---

#### `rolldown_esperado`
**Para qué:** **Forward-looking** — qué rinde cada bono a un horizonte si la curva no se mueve. Útil para rankear "qué bono comprar este mes". Soporta `tasa_fija` (default) y `cer`.

**Params:** `horizonte_dias: int = 30` (1..365), `metodo: str = "lineal"`, `curva: str = "tasa_fija"` (`tasa_fija | cer`).

**Retorna:** `dict` con `bonos: list[dict]` ordenado **descendente** por `total_esperado` (tasa_fija) o `total_esperado_ars` (cer).

Cada bono trae:
- Comunes: `ticker`, `tipo`, `valor` (precio o paridad), `tasa` (TEM/TEA), `vto_dias`, `vto_dias_horizonte`, `tasa_curva_at_horizonte`, `carry_esperado`, `rolldown_esperado`, `total_esperado`.
- Solo `curva=cer`: `is_zero_coupon`, `cer_accrual_esperado` (mediana del REM compoundeada), `total_esperado_ars` (retorno ARS compuesto).

Solo `curva=cer`: el dict raíz incluye `cer_accrual_esperado` y `cer_debug` (n_meses_compoundeados, medianas_mensuales).

**Prompts típicos:**
- "¿Cuál es la mejor Lecap para 30 días?"
- "Ranking de Lecers por carry+roll a 60 días" → usar `curva="cer"`

**Gotchas:**
- Asume curva estática — el ranking real depende del movimiento de tasas.
- Excluye bonos que vencen antes del horizonte.
- En CER el accrual forward sale del REM (mediana de inflación esperada) y compoundeada `round(horizonte_dias/30)` meses; horizontes < 15 días pueden tener proyección ruidosa.

---

### Sensibilidad

#### `sensibilidad_retorno`
**Para qué:** Tabla de PnL por escenarios de TIR para hard dollar (curva soberanos por default). Incluye pull-to-par y cobrado de cupones.

**Params:**
- `curva: str = "soberanos"`
- `tirs: str = "4,5,6,7,8,9,10,11"` — **CSV de TIRs en %** (no decimales!)
- `horizonte_dias: int = 0` — 0 = upside instantáneo; >0 incluye pull-to-par + cupones cobrados
- `modo: str = "absoluta"` — `absoluta` (TIRs finales) | `relativa` (shocks pp sobre la TEA actual)
- `tipos: str | None` — CSV opcional, ej `"globales,bonares"`

**Retorna:** `list[dict]` por bono, ordenado por vencimiento. Cada bono trae `precio_actual`, `tea_actual`, `duration`, `cobrado_horizonte`, `n_flujos_horizonte`, y `escenarios: list` con `{shock_pp?, tir, precio_objetivo, upside}`.

**Prompts típicos:**
- "¿Cuánto sube el GD30 si la TIR baja al 6%?"
- "Sensibilidad de Globales a shocks de ±100bps"

**Gotchas:**
- `tirs` es **CSV de %** ("4.5,5,6"), no decimales.
- `modo: relativa` requiere que cada bono tenga `tea_actual` válido — los que no, se skipean.
- `horizonte_dias > 0`: NO reinvierte cupones; solo suma cobrado.
- `upside` es retorno total decimal (0.15 = +15%), no anualizado.

---

### Forwards y breakevens

#### `forwards_live`
**Para qué:** Tasa forward implícita entre todos los pares de una curva, en vivo.

**Params:** `curva: str | None` — `tasa_fija | cer`. Omitir = ambas.

**Retorna:** `list[dict]` desde `Trading.ForwardsLive` — **1 documento por curva** (no uno por par). Cada doc trae:
- `curva: str` — `tasa_fija` o `cer`
- `ts: datetime` — timestamp del cálculo
- `fecha: str` — YYYY-MM-DD
- `ordered: list[str]` — tickers ordenados por vencimiento ascendente
- `tasas: dict[str, float]` — `{ticker: TEA}` (TEA en decimal)
- `matrix: dict[str, dict[str, float]]` — **NxN forward**: `{ticker_largo: {ticker_corto: forward_rate}}`. El forward de A→B se busca en `matrix[B][A]`. Forward en decimal.

**Cómo leer el forward A→B:**
```python
doc = forwards_live(curva="tasa_fija")[0]
forward_a_b = doc["matrix"][ticker_b][ticker_a]
```

**Prompts típicos:**
- "Forwards entre Lecaps"
- "¿Cuál es el forward marzo-junio en CER?"

**Fórmula:** `forward(A,B) = ((1+TEA_B)^t_B / (1+TEA_A)^t_A)^(1/(t_B − t_A)) − 1`.

---

#### `forwards_historico`
**Params:** `curva`, `desde`, `hasta`. **Mismo shape que `forwards_live` pero un doc por (curva, fecha)**.

---

#### `forwards_zscore`
**Para qué:** Z-score del forward live contra su historia rolling de 30 días hábiles. Sirve para detectar pares "caros/baratos" relativo a su distribución reciente.

**Params:** `curva: str | None` — `tasa_fija | cer`. Omitir = ambas.

**Retorna:** `list[dict]` con coeficientes por par (`ticker_a`, `ticker_b`, `media`, `desvio`, `n_obs`). Para z-scorear el forward live: `z = (forward_live − media) / desvio`. Pares con `n_obs < 20` o `desvio ≈ 0` quedan fuera del payload.

**Prompts típicos:**
- "¿El forward TZX26-TZX27 está caro o barato vs los últimos 30 días?"
- "Pares con z-score más extremo en la curva CER"

**Gotchas:**
- Refrescado **1x/día post-cierre** — durante la rueda, comparar contra el último cierre.
- El forward live se obtiene aparte con `forwards_live`; esta tool sólo da los coeficientes.
- Pares con poca data (< 20 ruedas) no aparecen — bonos nuevos no tienen z-score todavía.

---

#### `fair_value`
**Para qué:** Fair value relativo intra-curva. Ajusta una curva cuadrática `TEA(d) = β₀ + β₁·d + β₂·d²` y devuelve el residuo de cada bono + z-scores. Z negativo → bono "rico" (TEA por debajo de la curva, precio por arriba); positivo → "barato".

**Params:**
- `curva: str` — `tasa_fija | cer` (V1; el resto no soportado)
- `modo: str = "live"` — `live` (β del último cierre + TEAs vivas) | `cierre` (snapshot persistido del día)
- `fecha: str | None` — YYYY-MM-DD; sólo aplica con `modo="cierre"`

**Retorna:** `dict` con `bonos: list` por ticker: `tea_obs`, `tea_teorica`, `residuo_bps`, `z_estatico`, `z_temporal`, `duration`. Plus metadata de los betas.

**Z-scores explicados:**
- `z_estatico` = `residuo / σ del universo del día`. Comparación intra-rueda: ¿este bono está más caro/barato que sus pares HOY?
- `z_temporal` = `(residuo_hoy − media_30d) / desvio_30d`. Comparación temporal: ¿este bono está más caro/barato que SU PROPIA historia? `null` con `n_obs < 20`.

**Prompts típicos:**
- "Bonos baratos en la curva tasa fija hoy"
- "¿Qué Lecaps están con z-score temporal < -1?"
- "Fair value al cierre del 25/04"

**Gotchas:**
- Sólo `tasa_fija` y `cer` por ahora. Otras curvas devuelven error.
- `modo="live"` mezcla β congelado del cierre anterior con TEAs vivas — durante la rueda los z-scores estáticos pueden ser ruidosos hasta que la curva se asiente.
- Bonos sin TEA del día no aparecen.

---

#### `fair_value_historico_bono`
**Para qué:** Serie diaria del residuo y z-scores de un bono específico contra su curva fair value. Útil para ver "cómo viene cotizando este bono vs su propia historia".

**Params:**
- `ticker: str` — **debe ser el ticker FULL** (`MERV - XMEV - T30A7 - 24hs`), no el corto.
- `dias: int = 60` — últimos N cierres.

**Retorna:** `dict` con `ticker`, `curva`, `dias`, `serie: list[{fecha, residuo_bps, z_temporal, z_estatico, tea_obs, tea_teorica, duration}]`.

**Prompts típicos:**
- "¿Cómo viene cotizando T30A7 vs su curva?"
- "Evolución del z-score del TZX26 últimos 90 días"

**Gotchas:**
- **Ticker debe ser FULL** — la tool no resuelve corto. Si el usuario da `T30A7`, primero `listar_curva` para sacar el full.
- Sólo días con cierre (no incluye intradía).

---

#### `breakevens_live`
**Para qué:** Inflación mensual implícita entre pares Lecap-CER de mismo vencimiento.

**Params:** ninguno.

**Retorna:** `list[dict]` con `par_lecap`, `par_cer`, `fecha_vencimiento`, `inflacion_mensual_implicita` (decimal).

**Prompts típicos:**
- "Inflación implícita en los breakevens de hoy"
- "¿Qué espera el mercado para el IPC de los próximos meses?"

---

#### `breakevens_historico`
**Params:** `desde`, `hasta`. Mismo shape + `fecha`.

---

### Cauciones, futuros DLR, MEP

#### `cauciones_live`
**Para qué:** TNA del mercado repo (caución colocadora/tomadora).

**Params:** `moneda: str | None` — `ARS | USD`. Omitir = ambas.

**Retorna:** `list[dict]` con `moneda`, `ticker`, `plazo_dias`, `tna_last`, `tna_bid`, `tna_offer`, `tna_open`, `tna_high`, `tna_low`, `tna_closing`. Típicamente 1–2 docs.

**Gotchas:** **TNA en %, NO en decimales** (ej. `26.1` = 26.1% TNA, no 2610%). Hay distintos plazos (1d, 7d, etc) — chequear `plazo_dias`. Para convertir a TEA: `(1 + tna/100/365)**365 - 1`.

---

#### `cauciones_historico`
**Params:** `moneda`, `desde`, `hasta`. Cierre diario. Mismo shape, TNA también en %.

---

#### `futuros_dlr_live`
**Para qué:** Outrights vigentes del futuro de dólar Rofex con tasa implícita.

**Retorna:** `list[dict]` desde `Trading.FuturosDLRSnapshot`. Campos típicos: `ticker` (ej `DLR/MAY26`), `vencimiento`, `precio`, **`tasa_implicita_tna`** (TNA, NO TEA), `timestamp`.

**Gotcha clave:** la tasa implícita está en **TNA** (Tasa Nominal Anual), no TEA. Si querés TEA, capitalizá: `tea = (1 + tna/365)**365 - 1` (siempre que tna venga en decimal — confirmar contra el dato real).

**Prompts típicos:**
- "Curva de futuros DLR"
- "TNA implícita del DLR/JUL26"

---

#### `futuros_dlr_historico`
**Params:** `ticker` (ej `DLR/MAY26`) opcional, `desde`, `hasta`. Cierre diario.

---

#### `mep_actual`
**Para qué:** Última foto de dólar MEP/CCL/canje.

**Params:** ninguno.

**Retorna:** `dict` con `mep` (ARS), `ccl` (ARS), `canje` (**en %**, ej `4.11` = 4.11%), `timestamp`, `source` (`live | cron_close`).

**Gotchas:** Si snapshot live no está, fallback a último cierre cron. Cualquier campo puede ser `null`. **`canje` viene en %**, ojo no convertir.

---

#### `mep_historico`
**Para qué:** Serie histórica del dólar MEP.

**Params:** `desde`, `hasta` (ISO date o datetime).

**Retorna:** `list[dict]` con `mep`, `timestamp`. **No trae CCL ni canje** (solo MEP).

**Gotchas críticos:**
- **NO es cierre diario, es tick intradía cada 15 min** (engine `dolar_mep` corre `*/15 13-20 UTC` los días hábiles → ~32 puntos por día).
- Los primeros valores del día (alrededor de 13:00 UTC = 10:00 ART, apertura) suelen ser **outliers** (precio formado con poco volumen). Para series limpias, descartar el primer punto del día o quedarse con el último por día.
- Para "cierre diario", agregá vos: agrupar por `date(timestamp)` y tomar `last(mep)`.

---

### Cross-asset

#### `canje`
**Para qué:** Serie histórica del canje **CCL/MEP intra-bono** (mismo bono, especies C y D distintas). canje = precio_C / precio_D − 1. Mide la brecha CCL/MEP implícita en ese bono específico.

**OJO con la nomenclatura:** esto NO es "spread legislación" (que sería GD30 vs AL30, bonos distintos al mismo plazo, mide riesgo crediticio diferencial entre jurisdicciones NY y Argentina). Esto es brecha cambiaria intra-bono — los `ticker_c` / `ticker_d` que devuelve son del MISMO bono (ej. `AL30C` y `AL30D`, no `GD30` y `AL30`).

**Params:** `par: str = "AL30"` — `AL30 | GD30` (otros no soportados), `desde`, `hasta`.

**Retorna:** `dict` con `par`, `ticker_c` (ej `AL30C`), `ticker_d` (ej `AL30D`), `serie: list` (cada entrada `{fecha, precio_c, precio_d, canje}`), y `meta` con conteos.

**Default de ventana:** últimos 365 días si no se filtra.

**Gotchas:** Solo días con AMBOS precios disponibles. `canje: 0.024` = +2.4%.

---

#### `carry_trade`
**Para qué:** Retorno de bonos en ARS descontado por variación del dólar (MEP u oficial). Sirve para ver carry trade en USD.

**Params:**
- `curva: str = "tasa_fija"` — `tasa_fija | cer`
- `desde`, `hasta` (default: últimos 180 días)
- `dolar: str = "mep"` — `mep | oficial`

**Retorna:** `dict` con:
- `serie`: una fila por día con valor del dólar y carry % por bono (`null` los días que el bono no operó).
- `tabla`: resumen final por bono, ordenado desc por `carry_usd` (%).

**Prompts típicos:**
- "¿Cuánto rinde la curva CER en USD desde enero?"
- "Carry trade ranking últimos 90 días"

**Gotchas:** Cada bono se normaliza con SU primer día disponible — bases pueden diferir entre bonos. `carry_usd` viene en **%** (no decimales) — única excepción a la regla.

---

### Macro

#### `serie_macro`
**Para qué:** Serie temporal de cualquier variable macro + clasificación percentil/z-score.

**Params:**
- `variable: str` — soportadas: `tamar`, `cer`, `dolar`, `dolar_oficial`, `dolar_mayorista`, `dolar_blue`, `badlar`, `mep`, `ccl`, `canje`, `ipc`, `ipc_interanual`, `riesgo_pais`, `caucion_ars`, `caucion_usd`. También `<TICKER>.<CAMPO>` para series de bonos: `TX26.TEM`, `GD30.paridad`, `AL30.duration`. Campos válidos: `TEA`, `TEM`, `paridad`, `duration`, `price`.
- `ventana_dias: int = 90` (1..3650)

**Retorna:** `dict` con `actual`, `serie`, `cambio_dia_pct`, `cambio_semana_pct`, `cambio_mes_pct`, `percentil_actual`, `zscore_actual`, `clasificacion` (`muy_bajo | bajo | medio | alto | muy_alto | sin_datos`).

**Prompts típicos:**
- "Riesgo país último año"
- "Evolución de TEM del TX26"
- "TAMAR vs últimos 30 días"

**Gotchas:** Variables `ipim`, `repo`, `rem_inflacion` están bloqueadas y devuelven stub con hint. `ipc` es mensual; el resto suele ser diario.

---

#### `clasificar_nivel`
**Para qué:** Versión compacta de `serie_macro`, sin la serie histórica — solo el nivel actual + clasificación.

**Params:** `variable`, `ventana_dias = 90`.

**Retorna:** `dict` con `actual`, `clasificacion`, `percentil_actual`, `zscore_actual`, `contexto` (string corto).

**Prompts típicos:**
- "¿La TAMAR está alta?"
- "¿En qué percentil está el riesgo país?"

**Cuándo usar esta vs `serie_macro`:** Si el usuario solo quiere "alto/bajo/medio", usar `clasificar_nivel`. Si quiere ver evolución o gráfico, `serie_macro`.

---

#### `rem_expectativas`
**Para qué:** Expectativas REM del BCRA — IPC mensual proyectado por consultoras privadas.

**Params:**
- `informe: str | None` — YYYY-MM. Omitir = último informe.
- `periodo_tipo: str | None` — `mensual | anual | trimestral`.
- `periodo_desde`, `periodo_hasta` — YYYY-MM.

**Retorna:** `dict` con `informe`, `items: list` con `periodo`, `mediana`, `promedio`, `desvio`, `min/max`, `p10/p25/p75/p90`, `participantes`.

**Prompts típicos:**
- "Inflación esperada mensual REM"
- "REM último informe"

**Gotchas:** Valores de IPC ya en **%** (no decimales) — única excepción consistente con cómo el BCRA los publica.

---

### Opciones

#### `opciones_chain`
**Para qué:** Chain LIVE del OPEX en curso.

**Params:** `instrumento: str | None` (corto o completo, regex), `tipo: str | None` (`CALL | PUT`).

**Retorna:** `list[dict]` por opción: `instrumento`, `bid`, `offer`, `last`, `open`, `high`, `low`, `spot` (subyacente), `strike`, `tipo`, `vence`, `delta`, `gamma`, `iv`, `theta`, `vega`.

**Prompts típicos:**
- "Chain de calls de GGAL"
- "Puts in-the-money del OPEX"

---

#### `opciones_meta`
**Para qué:** Configuración del módulo opciones — tasa libre de riesgo, valores de referencia GGAL.

**Retorna:** `dict` con `tasa`, `vr_local`, `vr_adr`, `updated_at`.

**Cuándo usar:** Cuando el LLM necesita pricing teórico (ej. para comparar IV implícita con teórica) o entender qué tasa risk-free usa el motor.

---

#### `opciones_historico`
**Para qué:** Histórico tick-level del OPEX en curso para una opción específica.

**Params:** `instrumento` (acepta corto `GFGC10950A` o completo `MERV - XMEV - GFGC10950A - 24hs`), `tipo` (`CALL | PUT`).

**Retorna:** `list[dict]` de hasta 5000 trades, últimos 21 días.

---

## Patrones combinados (recetas)

### "¿Qué Lecap me conviene comprar este mes y por qué?"
```
1. rolldown_esperado(horizonte_dias=30) → top picks por total_esperado
2. liquidez_secundario(ticker=<top1>) → confirmar que se opera
3. listar_curva(curva="tasa_fija", limit=10, ordenar_por="tea") → contexto del mercado
```

### "¿Está cara o barata la curva CER?"
```
1. clasificar_nivel(variable="cer") → ver percentil del CER actual
2. pendiente_curva(curva="cer", fecha_comparacion=<7d ago>) → ver dinámica reciente
3. breakevens_live() → inflación implícita vs REM
4. rem_expectativas() → comparar contra expectativas
```

### "Análisis del soberano AL30 hoy"
```
1. listar_curva(curva="soberanos") → estado actual + duration
2. liquidez_secundario(ticker="AL30")
3. sensibilidad_retorno(curva="soberanos", tirs="6,7,8,9,10,11", horizonte_dias=180, tipos="bonares")
4. canje(par="AL30") → contexto vs GD30
5. serie_macro(variable="riesgo_pais", ventana_dias=90) → contexto macro
```

### "Carry trade ranking en USD"
```
1. carry_trade(curva="tasa_fija", dolar="mep") → ranking + serie
2. mep_historico() → ver dólar usado
3. listar_curva(curva="tasa_fija") → enriquecer ranking con duration
```

### "Foto del mercado de tasas hoy" (briefing)
```
1. listar_curva(curva="tasa_fija", limit=5, ordenar_por="tea")
2. listar_curva(curva="cer", limit=5, ordenar_por="tea")
3. cauciones_live()
4. futuros_dlr_live() → curva DLR
5. mep_actual()
6. clasificar_nivel(variable="tamar")
```

---

## Tips para el LLM consumiendo este connector

1. **Antes de calcular nada**, listar la curva relevante con `listar_curva` para tener tickers reales y duration. No inventar tickers.
2. **Tickers cortos** (`TX26`, `S15D5`) suelen funcionar en todas las tools que aceptan ticker. Si una tool falla, probar con el completo (`MERV - XMEV - TX26 - 24hs`).
3. **Tasas siempre decimales** en outputs salvo `rem_expectativas` (en %) y `carry_trade.tabla.carry_usd` (en %). Para presentar al usuario, multiplicar por 100 y agregar "%".
4. **Si una tool devuelve `[]` o `{}`**, NO inferir que el dato no existe — puede ser ventana de Atlas pausa (04:00–11:20 UTC) o filtro demasiado restrictivo. Reintentar con menos filtros antes de concluir.
5. **No combinar tools sin necesidad**. Si el usuario pide "Lecaps por TEA", `listar_curva(curva="tasa_fija", ordenar_por="tea")` alcanza — no llamar `historico_curva` también.
6. **Histórico vs live**: la regla mental es "live = MarketSnapshot, histórico = TimeSales/Cierres". Live puede tener nulls si está fuera de rueda (L-V 13–20 UTC argentina).
7. **Errores con clave `error`**: cuando una tool devuelve `{"error": "..."}`, NO mostrar el error técnico al usuario; traducir a algo entendible ("no hay datos para esa curva en esa fecha").
8. **Volumen del retorno**: `historico_curva` y `historico_trades` sin filtro pueden devolver muchos KB. Filtrar siempre que sea posible (por instrumento o ventana de fechas).
9. **Composición temporal**: Lecap zero coupon → solo capital al vto. CER → cupones + amortización porcentual. Soberanos → cupones + amortización USD. Esto matters para `descomposicion_retorno` y `sensibilidad_retorno`.

---

## Errores comunes y cómo interpretarlos

| Síntoma | Causa probable | Acción |
|---|---|---|
| `[]` vacío en `listar_curva` | Curva mal escrita o ventana Atlas pausa | Validar nombre de curva; reintentar entre 11:20 y 04:00 UTC |
| `{"error": "curva invalida..."}` | String fuera del set válido | Usar exactamente `tasa_fija | cer | soberanos | tamar | dolar_linked` |
| `{"error": "par desconocido"}` en `canje` | Solo soporta AL30/GD30 | Otros pares no están |
| `null` en TEA / duration / mod_duration | Bono no operó hoy o falta enriquecimiento | Usar `historico_trades` o `snapshot_curva_historico` para datos previos |
| `sensibilidad_retorno` skipea bonos en modo `relativa` | Bonos sin `tea_actual` no pueden tener shock | Usar modo `absoluta` o filtrar tipos |
| Retornos enormes en `historico_*` sin filtro | No filtraste | Agregar `instrumento` o rango de fechas |
| Conexión rechazada / timeout | Atlas pausado (04:00–11:20 UTC) | Esperar a la ventana operativa |

---

## Cuándo decir "no puedo ayudar con esto"

El connector NO tiene datos sobre:
- Posiciones, AuM, portfolio del usuario.
- Operaciones / órdenes.
- Datos de management / RBAC / usuarios.
- Datos no argentinos (sólo MERVAL/ROFEX/BCRA/INDEC).
- Acciones (solo bonos, futuros, opciones GGAL, macro).
- Análisis fundamental de empresas.

Si el usuario pide algo de esto, decirlo explícito — no intentar fabricarlo combinando tools.
