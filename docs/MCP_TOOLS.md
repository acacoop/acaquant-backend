# MCP Tools Reference — TradingAV (Renta Variable)

El MCP server de TradingAV es un **asistente 100% de renta variable** (equities
ARG): CEDEARs, ADRs, acciones, time sales intradía, pivots, day-trading y la
Mesa de Estrategia. **13 tools de SOLO LECTURA, todas datos de MERCADO.**

> El código vive en `api/mcp/tools/renta_variable.py` (registro) y los servicios
> en `api/services/{scanner_sql,day_trading,rv_motor}.py`. Setup del cliente y
> auth: `docs/MCP.md`.

## Qué expone

Universo de CEDEARs, tablero live (ARS + ADR USD + CCL), tape intradía, retornos
y estadística quant del subyacente USD, pivot points y herramientas de estrategia
(correlación, dimensionado de trades, análisis de book). Todo se lee de SQL
`mercado.*` (cutover 2026-06-24) salvo la tape (`Trading.CedearsTimeSales`,
efímera) y el CCL (`Valuaciones.Dolar`, live).

## Qué NO expone (y no hay que asumir que existe)

Nada privado de la mesa: **portfolio, operaciones, cuentas, AuM, manager,
clientes, P&L, contrapartes** — no viven en este MCP por diseño (REGLA #8). Las
tools de estrategia (`trade_analysis`, `book_analysis`) trabajan SOLO sobre
posiciones que el usuario describe como parámetro: **no leen ninguna cuenta ni
tenencia real.**

También están **PAUSADOS** (código en `tools/parked_mercado.py`, no registrado):
renta fija, derivados, opciones, forwards, breakevens, cauciones, futuros DLR,
MEP y macro. No están disponibles como tools hoy.

## Convenciones críticas (leer antes de usar cualquier tool)

- **`ticker` = ticker_corto BYMA** (ej. `AAPL`, `YPFD`, `NVDA`), NO el símbolo
  US ni el ticker completo. Las tools resuelven el underlying internamente
  (`YPFD` → `YPF`) cuando trabajan sobre el subyacente USD.
- **Dos precios distintos por papel**: el CEDEAR cotiza en **ARS** (mercado
  local), el subyacente/ADR cotiza en **USD**. El scanner trae ambos. Retornos,
  quant stats, pivots y estrategia se calculan SIEMPRE sobre el **subyacente USD**.
- **`vs_1d_usd_pct`** = variación del CEDEAR descontando el movimiento del CCL
  (retorno "real" en dólares). `vs_1d_pct` es en ARS.
- **Horario de rueda**: `cedears_tape`, `cedears_intraday` y `day_trading_scanner`
  solo tienen datos en rueda (la tape se vacía al cierre). Fuera de hora,
  `day_trading_scanner` devuelve `en_rueda=false`.
- **Notional negativo = short** en `book_analysis`.

## Cheat sheet — qué tool para qué pregunta

| Pregunta del usuario | Tool |
|---|---|
| ¿Qué CEDEARs existen / del sector X / de IA? | `rv_universo` |
| Foto del tablero ahora (todos los papeles) | `cedears_scanner` |
| ¿A cuánto está el CCL? | `ccl_live` |
| Trades de hoy de AAPL / ¿quién está comprando? | `cedears_tape` |
| Chart minuto a minuto de hoy | `cedears_intraday` |
| Distribución de retornos del último año | `acciones_retornos` |
| Beta / vol / correlación vs SPY-QQQ | `acciones_quant_stats` |
| Soportes y resistencias | `pivot_points` |
| ¿Qué scalpear hoy? | `day_trading_scanner` |
| ¿Con qué se mueve / contra qué? | `day_trading_companeros` |
| Matriz de correlación de un grupo | `correlacion_matriz` |
| Dimensionar un trade + hedge | `trade_analysis` |
| Riesgo de una cartera hipotética | `book_analysis` |

---

## Tools detalladas

### Universo

#### `rv_universo()`
Catálogo de CEDEARs activos **sin precios**. Una fila por papel:
`ticker_corto`, `nombre`, `underlying`, `ratio_cedear`, `sector`, `rubro`
(clasificación de negocio más granular), `es_ia` (bool), `industria`, `region`,
`pais`. Ordenado A→Z. **Usar primero** para descubrir el universo y filtrar
antes de pedir live/quant.

### Live de mercado

#### `cedears_scanner()`
Tablero LIVE de todos los CEDEARs activos. Por papel: master + CEDEAR en ARS
(`last`, OHLC, `intraday_pct`, `vs_1d_pct`, `vs_1d_usd_pct`, `bid`/`offer`/
`spread`/`spread_pct`, `vwap`, `volume`, `total_money`) + ADR en USD (`adr_last`,
`adr_intraday`, `adr_vs_1d_pct`, retornos `adr_ret_wtd/7d/15r/mtd/ytd_pct`,
`adr_dollar_vol`) + `updated_at`. Refresca cada 1s en rueda. Una sola llamada =
foto completa.

#### `ccl_live()`
Dólar CCL live: `{value, vs_1d_pct, timestamp}`. Es el TC que separa retorno ARS
de retorno USD.

### Time sales / tape intradía

#### `cedears_tape(ticker, limite=200)`
Trades crudos de HOY, más recientes primero: `{timestamp, price, size, side
('BUY'|'SELL'|'MID'), money}`. `limite` 1-1000. Para leer el flujo agresor.

#### `cedears_intraday(ticker)`
Tape de hoy agregado por minuto: velas OHLC + volumen, orden ascendente. Para
reconstruir el chart intradía.

### Histórico EOD + quant (subyacente USD)

#### `acciones_retornos(ticker)`
Retornos diarios aritméticos del último año (~252) del subyacente USD:
`{ticker, returns:[float], last_return, last_fecha}`. Para histogramas / análisis
de cola.

#### `acciones_quant_stats(ticker)`
Beta, alpha anualizada y correlación vs SPY y QQQ; vol realizada anualizada a 30
y 60 días; z-score del último retorno. Caracteriza el riesgo de mercado.

#### `pivot_points(ticker)`
Pivot points Floor Trader en 4 timeframes (diario/semanal/mensual/anual) del
subyacente USD: PP, R1-R3, S1-S3 con el OHLC del período previo. `last` pisado
por el live del ADR si está disponible.

### Day-trading lab

#### `day_trading_scanner(objetivo_pct=0.5)`
Ranking de CEDEARs para scalping según objetivo de captura (% 0.1-5). Por papel:
vueltas (zigzag ≥ objetivo hechas hoy), rango y posición en él, pata en curso,
momentum 15', lado del VWAP, spread %, flujo comprador (% del día y de los
últimos 30'), volumen cash/nominal, minutos sin operar, costumbre histórica
(~20 ruedas) e idea heurística LONG/SHORT con motivo. Solo en rueda.

#### `day_trading_companeros(ticker, n=6)`
Top `n` más correlacionados (`con`) y anti-correlacionados (`contra`) por Pearson
de cierres diarios USD (ventana 252). Para pares, espejos short, no duplicar
apuestas.

### Mesa de Estrategia

#### `correlacion_matriz(tickers=None, ventana_dias=252)`
Matriz de correlación de retornos diarios USD + vol anualizada por papel.
`tickers`: CSV de ticker_corto (`"AAPL,MSFT,NVDA"`) o vacío = todo el universo.
Devuelve `{tickers, excluidos, n_obs, fecha_desde/hasta, matriz[[float|None]]
(1.0 en diagonal), vol_anual}`. Base para hedging y diversificación.

#### `trade_analysis(ticker, monto, direccion="long")`
Dimensiona un trade **hipotético** (no lee posiciones reales). `monto` en USD,
`direccion` `long`|`short`. Devuelve caracterización (last, vol 30/60d, beta
SPY/QQQ, z-score, VaR 1d 95% en USD y %, peor mes 1σ, exposición de mercado
equivalente), hedge por beta (notional vs SPY/QQQ) y hedge-finder (universo
rankeado por correlación con hedge_ratio de mínima varianza y reducción de vol).

#### `book_analysis(posiciones)`
Riesgo de un book **que el usuario describe** (no lee AuM). `posiciones`: CSV
`"TICKER:NOTIONAL"` separado por comas, notional USD, **negativo = short**
(ej. `"AAPL:10000,TSLA:-5000"`). Devuelve composición (gross/net/n), exposición
por sector y región, concentración (pct_top5, HHI), riesgo agregado (vol book,
VaR 1d 95% USD, exposición de mercado USD), contribución de riesgo por papel y
excluidos (sin historia suficiente).

---

## Patrones combinados (recetas)

**"¿Qué papel de IA scalpeo hoy?"**
`rv_universo` (filtrar `es_ia=true`) → `day_trading_scanner` (cruzar con los
tickers IA) → para el elegido, `cedears_tape` (confirmar flujo) + `pivot_points`
(niveles).

**"Quiero comprar NVDA, ¿cómo me cubro?"**
`trade_analysis(ticker="NVDA", monto=..., direccion="long")` → mirar `hedge_beta`
(cobertura vs SPY/QQQ) y `hedge_finder` (mejores pares por reducción de vol).

**"¿Está concentrada mi idea de cartera?"**
`book_analysis("AAPL:10000,MSFT:8000,GOOGL:6000,...")` → revisar `concentracion`
(HHI, top5), `exposicion.por_sector` y `contribucion_riesgo`.

**"¿Cómo viene el papel vs el mercado?"**
`acciones_quant_stats(ticker)` (beta/vol/corr) + `acciones_retornos(ticker)`
(distribución) + `cedears_scanner` (vs_1d_usd_pct hoy).

---

## Tips para el LLM consumiendo este connector

- Empezá por `rv_universo` o `cedears_scanner` para anclar qué papeles existen y
  cómo se llaman (ticker_corto) antes de pedir tools por ticker.
- Para "el mercado hoy", una llamada a `cedears_scanner` ya trae todo (ARS+USD).
  No iteres papel por papel.
- Distinguí SIEMPRE ARS vs USD al reportar variaciones. Si el usuario piensa en
  dólares, usá `vs_1d_usd_pct` / las métricas del ADR.
- `cedears_tape`/`cedears_intraday`/`day_trading_scanner` solo sirven en rueda.
  Fuera de hora, decilo en vez de devolver vacío sin contexto.
- VaR y vol salen de ~252 ruedas del subyacente USD; papeles nuevos con poca
  historia caen en `excluidos`.

## Errores comunes

- **Tool de renta fija / opciones / macro no aparece** → está PAUSADA (este MCP es
  RV-only hoy). No inventes el resultado.
- **`book_analysis` con formato malo** → devuelve `{error: ...}`. El formato es
  `TICKER:NOTIONAL` separado por comas.
- **Papel sin datos** → `acciones_retornos` devuelve `returns:[]`; quant stats con
  `null`. Es falta de historia, no un bug.

## Cuándo decir "no puedo ayudar con esto"

Si la pregunta es sobre datos privados de la mesa (cuentas, AuM, operaciones,
P&L, clientes, manager) o sobre renta fija / opciones / macro: este MCP no lo
expone. Decilo explícito; no aproximes con datos de RV.
