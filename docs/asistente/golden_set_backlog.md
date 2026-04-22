# Backlog — tools y data pendientes del Golden Set

Este archivo se va llenando a medida que procesamos preguntas del golden set
con el user. Cada ítem pendiente incluye **quién lo pidió** (qué test case)
para priorizar por frecuencia de uso.

---

## 🔴 Tools nuevas que hacen falta construir

### Tier 1 (alta frecuencia, prioritarias)

- [ ] **`listar_curva(curva, ordenar_por='vencimiento', vencimiento_min_meses=None, vencimiento_max_meses=None)`** — usada en look-001, look-003, look-004, cart-001 a cart-006, conn-002, conn-003, conn-004, lici-*
  - Parámetros: `curva: "cer"|"tasa_fija"|"tamar"|"soberanos"|"dolar_linked"`, filtros de horizonte, `limit`.
  - Return: lista de `{ticker, ticker_corto, vencimiento, tea, tem, paridad, duration, convexity, ultimo_precio, total_money_dia, meses_al_vto}`.
  - Construcción: `Trading.Curvas` (filter) + join con último TimeSales (TEA/TEM) + MarketSnapshot (volumen).
  - **High priority** — usada en 15+ test cases ya procesados.

- [ ] **`clasificar_nivel(variable, ventana_dias=90)`** — usada en look-*, cart-*, lici-*, conn-*
  - Ya en sección "Framework stats" abajo. Tool #1 del módulo stats.
  - **High priority** — aparece en 12+ test cases.

- [ ] **`obtener_serie_macro(variable, ventana_dias=90)`** — conn-001, conn-004, look-*, fwd-003
  - Tool **genérica** que reemplaza la familia de tools específicas (`obtener_tamar_actual`, `obtener_cer_actual`, `obtener_dolar_actual`, `obtener_badlar_actual`, `obtener_riesgo_pais`, `obtener_canje`, `obtener_inflacion`, `obtener_repo_stock`).
  - Parámetros: `variable: str` — acepta:
    - Macros: `tamar | cer | dolar | badlar | riesgo_pais | ccl | mep | canje | ipc | ipim | repo | rem_inflacion`.
    - Series por ticker: `<TICKER>.TEA | <TICKER>.TEM | <TICKER>.paridad | <TICKER>.duration` (fwd-003).
  - Return: `{actual, serie[], cambio_dia_pct, cambio_semana_pct, min, max, media, desvio, percentil_actual, clasificacion}`.
  - **Decisión de diseño**: 1 tool genérica en vez de 8 específicas + extensión natural a series por ticker. Reduce surface area que ve el modelo + lógica reusable.
  - Internamente despacha a la colección correcta según el prefijo (macro vs ticker).

### Tier 2 (mediana frecuencia)

- [ ] **`snapshot_curva_historico(curva, fecha)`** — look-003, estr-002
  - Return: curva completa en fecha específica (iterando TimeSales por cada ticker).
  - Usado para comparar curva actual vs X días atrás.
  - Para estr-002 (butterfly Z-score): el modelo reconstruye la serie histórica del spread usando esta tool iterativamente. No hace falta un dataset pre-calculado de butterfly spreads — la fórmula `BF = y_cuerpo - (y_corto + y_largo)/2` se recomputa con cada snapshot histórico.

- [ ] **`calcular_pendiente_curva(curva, fecha='hoy')`** — look-003
  - Derivable de listar_curva: `TEM(último bono) - TEM(primer bono)`.
  - Opcionalmente aceptar 2 fechas y devolver delta de pendiente.

- [ ] **`top_volumen_por_curva(curva, n=5)`** — look-004
  - Puede ser un parámetro de `listar_curva` (`ordenar_por='volumen'`, `limit=n`).

- [ ] **`obtener_inflacion_reciente()`** — look-001
  - Últimos N meses de IPC INDEC + variación mensual + expectativa REM si está disponible.
  - **Bloqueada**: falta data (ver sección de colecciones).

- [ ] **`obtener_canje_ccl_mep()`** — look-002
  - Devuelve CCL, MEP, spread (CCL-MEP)/MEP, clasificación vs historia.
  - **Bloqueada**: falta CCL en Mongo (ver sección de colecciones).

- [ ] **`liquidez_secundario(ticker, dias=20)`** — cart-005
  - Devuelve `{volumen_promedio_dia, volumen_dia_actual, ratio_vs_promedio, clasificacion: "baja"|"media"|"alta"}`.
  - Data base: `Trading.MarketSnapshot.metrics.total_money` diario.
  - ~30 líneas.

- [ ] **`obtener_riesgo_pais()`** / **`obtener_riesgo_pais_historia(ventana_dias=90)`** — cart-004
  - Valor actual + clasificación vs historia.
  - **Bloqueada**: falta serie en Mongo (ver sección de colecciones).

- [ ] (condicional) **`valor_relativo_en_curva(ticker)`** — cart-003
  - Residuo vs fit de curva (rico/barato).
  - Se posterga hasta ver si el modelo lo resuelve por sí solo con los datos de `listar_curva`.
  - Si falla en evaluación, se construye.

### Licitaciones (nuevo grupo — Categoría 3 del golden set)

- [ ] **`obtener_menu_licitacion(fecha=None)`** — lici-001 a lici-005
  - Devuelve: lista de `{instrumento, tipo, curva, vencimiento, descripcion, emision_nominal, cupon_step_up?, ajuste?}`.
  - **Bloqueada**: falta colección `Licitaciones.Menu` (ver sección de colecciones).

- [ ] **`obtener_resultado_licitacion(fecha=None)`** — lici-006 a lici-008
  - Devuelve: lista de `{instrumento, tasa_corte, monto_adjudicado, monto_ofertado, bid_to_cover, bcra_tomo?, rolleo_pct?, retiro_neto_ars}`.
  - **Bloqueada**: falta colección `Licitaciones.Resultados` (ver sección).

- [ ] **`obtener_repo_stock()`** — lici-008
  - Stock actual de pases pasivos BCRA + serie 60 días.
  - **Bloqueada**: falta colección `Macro.REPO` (ver sección).

- [ ] **`obtener_futuros_rofex(subyacente='DOLAR')`** — lici-003
  - Curva de futuros Rofex (tasa implícita en USD/ARS por vencimiento).
  - **Bloqueada**: falta colección o integración pyRofex (ver sección).

- [ ] **`calcular_forward_bono_nuevo(nuevo_ticker, vencimiento, tea_estimada, curva_secundario)`** — lici-001, lici-004
  - Dado un bono hipotético (que aún no cotiza), calcular forward implícita vs bono secundario más cercano.
  - Extiende `forwards_por_curva` existente para aceptar bono hipotético.

- [ ] **`calcular_breakeven_dl_vs_lecap(dl_ticker, lecap_ticker)`** — lici-003
  - Depreciación spot implícita que iguala DL con Lecap comparable.
  - Fórmula: `(TEA_DL × plazo) - (TEA_Lecap × plazo)` en términos de variación cambiaria.
  - Tool fina, puede ser parte de un módulo `quant/calculos_licitacion.py`.

- [ ] **`calcular_tamar_breakeven(spread_propuesto, plazo_meses)`** — lici-001
  - TAMAR proyectada mínima que iguala el Dual con Lecap comparable del mismo plazo.
  - Útil para decidir qué spread pedir en licitación.

---

## 🟡 Tools existentes a adaptar

- [ ] **`engines/curvas.py`**: extender enriquecimiento a hard dollar (Globales/Bonares) — cart-002, cart-004, fwd-001
  - Hoy enriquece CER (TEA/paridad) y tasa fija (TEA/TEM/duration).
  - Necesario para que `listar_curva("soberanos")` devuelva YTM/duration/paridad de GD30, AL30, etc.
  - Fórmula: YTM con Newton-Raphson sobre los flujos USD del bono.
  - Dependencia previa: `seed_soberanos` ejecutado para que Globales/Bonares estén en `Trading.Curvas`.

- [ ] **`engines/curvas.py`**: agregar cálculo de **convexidad** — risk-002, conn-002
  - Hoy calcula duration, falta convexity (segunda derivada de precio vs yield).
  - Fórmula: `C = (1/P) × Σ [t(t+1) × CF_t / (1+y)^(t+2)]` — ~15 líneas.
  - Aplicar a CER, tasa fija y HD (todas las curvas que enriquece).
  - Agregar campo `convexity` a `Trading.TimeSales` (stampea como `duration`).
  - Incluir en `listar_curva` return.

- [ ] **`engines/forwards.py` + `/api/cotizaciones/forwards`**: extender a `curva=soberanos` — fwd-001
  - Hoy soporta solo `tasa_fija` y `cer`. Matemáticamente igual (ratio de TEAs), pero aplicado sobre YTM USD.
  - Dependencia previa: `engines/curvas.py` enriqueciendo hard dollar.

- [ ] **Exponer `/api/cotizaciones/historico/forwards` como tool** — fwd-002
  - Endpoint ya existe (`Trading.ForwardsHistorico`).
  - Falta envolver como tool `forwards_historico(curva, desde, hasta)` para que el asistente lo pueda invocar.
  - ~10 líneas, wrap trivial.

- [ ] **Verificar/exponer `/api/cotizaciones/historico/trades` como tool** — edge-001
  - Endpoint existe (historial de trades por instrumento en Trading.TimeSales).
  - Chequear si ya está en el catálogo de tools del asistente. Si no, wrappear como `historico_trades(instrumento, desde, hasta)`.
  - Habilita lookup histórico de precios (edge-001: "precio de Boncer de hace 3 meses").

---

## 📊 Series / colecciones que faltarían en Mongo

- [ ] **Contado con Liqui (CCL)** — bloqueante para look-002.
  - Se calcula como AL30C/AL30 o GD30C/GD30 (paridad local/cable).
  - Opciones: (a) nuevo motor `engines/dolar_ccl.py`, (b) extender `engines/dolar_mep.py` para capturar ambos.
  - Persistir en `Valuaciones.Dolar` con campo `ccl` (hoy solo tiene `mep`).

- [ ] **Inflación (IPC + IPIM INDEC)** — bloqueante para look-001, cart-001, conn-004, be-001, be-002, be-003 y cualquier pregunta sobre inflación.
  - Series mensuales: `ipc_mom`, `ipc_yoy`, `ipim_mom`, `ipim_yoy`.
  - Fuente: INDEC web/API. Workaround temporal: bloque INTEL del último IntelDoc.
  - Colección propuesta: `Macro.Inflacion` con schema unificado {fecha, indice: "IPC"|"IPIM", variacion_mensual, variacion_anual}.
  - Motor/job: `jobs/inflacion.py` (scraping o parse de PDF INDEC).

- [ ] **REM — Relevamiento de Expectativas de Mercado (BCRA)** — bloqueante para be-001 y cualquier pregunta de "vs expectativas".
  - Serie mensual: expectativas de consultoras (mediana / percentiles) para inflación, FX, PBI, tasa de política monetaria.
  - Schema: `{fecha_publicacion, horizonte_meses, indicador: "inflacion"|"fx"|"pbi"|"tasa", mediana, percentil_10, percentil_90}`.
  - Fuente: BCRA publica CSV/Excel mensual. URL fija, fácil de scrapear.
  - Colección propuesta: `Macro.REM`.
  - Motor/job: `jobs/rem.py`, mensual después de publicación BCRA (día ~20 del mes).

- [ ] **Riesgo país (EMBI+ Argentina)** — bloqueante para cart-004 y cualquier pregunta sobre "riesgo país" / "spread soberano".
  - Serie diaria: fecha → `riesgo_pais_bps`.
  - Fuente: ámbito, Rava, Bolsar (scraping), o API paga de JP Morgan.
  - Colección propuesta: `Macro.RiesgoPais`.
  - Motor propuesto: `jobs/riesgo_pais.py --today` encadenado en crontab con BCRA.

### Licitaciones (nuevo — Categoría 3)

- [ ] **Menú de licitaciones** — bloqueante para lici-001 a lici-005 (pre-lici).
  - Colección propuesta: `Licitaciones.Menu`.
  - Schema: `{fecha_licitacion, fecha_liquidacion, instrumentos: [{ticker, tipo, curva, vencimiento, descripcion, cupon_step_up?, ajuste?}]}`.
  - Carga: manual + scraping del comunicado del Ministerio de Economía (se publica 2-3 días antes de cada lici, ~cada 2 semanas).

- [ ] **Resultados de licitaciones** — bloqueante para lici-006 a lici-008 (post-lici).
  - Colección propuesta: `Licitaciones.Resultados`.
  - Schema: `{fecha, instrumento, tasa_corte, monto_ofertado, monto_adjudicado, bid_to_cover, bcra_tomo_ars, vencimientos_mismo_dia_ars, rolleo_pct, retiro_neto_ars}`.
  - Carga: manual post-lici + scraping del comunicado oficial.

- [ ] **Stock de pases (REPO BCRA)** — bloqueante para lici-008.
  - Serie diaria: fecha → `stock_pases_pasivos_ars`.
  - Fuente: BCRA (hay variable en la API BCRA que ya usás). Probablemente serie ID en `jobs/bcra.py`.
  - Colección propuesta: `Trading.REPO` o agregar a existente.

- [ ] **Futuros Rofex USD/ARS** — bloqueante para lici-003 (DL vs implícito).
  - Curva de futuros MATBA-Rofex de dólar por vencimiento.
  - Fuente: pyRofex (ya conectado en el proyecto). Habría que agregar motor de captura.
  - Colección propuesta: `Trading.FuturosRofex`.

- [ ] **Dólar Linked cargado en `Trading.Curvas` con `curva: "dolar_linked"`** — lici-003, cart-002 (parcial)
  - Bonos típicos: TZV26, TZV28, D15F7, TVPD26, etc.
  - Carga similar a como se hizo con soberanos (JSON en `docs/soberanos/` pero para DL → `docs/dolar_linked/`).

- [ ] (Tier 2/3, nice-to-have) **Benchmarks EM para comparación** — fwd-001
  - Bonos soberanos de pares regionales/rating similar (Ecuador, Egipto, Pakistán, Turquía).
  - Útil para interpretar si los spreads argentinos están "en línea con la calificación".
  - Fuentes: Investing.com (scraping), Refinitiv/Bloomberg (pago), Finnhub (free tier tiene algo de bonds).
  - **No bloqueante** — el asistente puede omitir la comparación internacional si no hay data, limitándose a "vs historia propia".

- [ ] (Tier 3, mejora de modelado) **Campo `regla_cupon` en `Trading.Curvas`** — esc-003
  - Hoy los Duales TAMAR se cargan como bonos normales sin expresar la regla `max(TAMAR+spread, tasa_fija)`.
  - Propuesta: agregar campo `regla_cupon: "dual_tamar_tasafija" | "fijo" | "cer" | "zero_coupon" | ...` + sub-params.
  - Permite al enriquecimiento proyectar cupones en escenarios hipotéticos (TAMAR estable, TAMAR alcista, etc.).
  - **Workaround actual**: la rubric del test case puede explicitar la regla textualmente; el modelo razona sin campo estructural.

---

## 🧪 Framework stats (infra compartida — probable high-priority)

Basado en el meta-framework de "benchmarks dinámicos" acordado con el user
antes de empezar a procesar preguntas.

- [ ] **Nuevo módulo** `api/agent/stats.py` — funciones reusables:
  - `percentile(coleccion, campo, ventana_dias, fecha_ref=None) -> float`
  - `zscore(coleccion, campo, ventana_dias, fecha_ref=None) -> float`
  - `classify_level(coleccion, campo, ventana_dias) -> str`
    - Devuelve una etiqueta categórica: `"minimo"`, `"bajo"`, `"medio"`,
      `"alto"`, `"maximo"`, `"tendencia_alcista"`, `"tendencia_bajista"`, `"lateral"`
  - `spread_vs_benchmark(coleccion, campo, benchmark_coleccion, benchmark_campo)`
  - Cacheado con TTL corto por `(coleccion, campo, ventana)`.

- [ ] **Tool nueva**: `clasificar_nivel(variable, ventana_dias=90)`
  - Devuelve `{label, percentile, zscore, min, max, current, mean}`.
  - `variable` acepta enum: `tamar`, `cer`, `dolar`, `badlar`, `breakeven`, `forward`.

- [ ] **Tool nueva**: `zscore_variable(variable, ventana_dias=30)`
  - Devuelve desvíos estándar vs media de la ventana.

- [ ] **Tool nueva**: `comparar_contra_historia(variable, ventana_dias)`
  - Percentil actual + dirección de tendencia reciente.

- [ ] **Tool nueva**: `es_coherente_con_tendencia(variable, direccion)`
  - Valida "recomiendo long CER" vs tendencia/nivel actual.
  - Requiere criterio del dominio (no es puro stats). Se puede diferir.

---

## ✅ Completado

- [x] **`listar_curva(curva, ordenar_por, vencimiento_min/max_meses, limit)`** — `api/services/cotizaciones.py`, expuesta como `/api/analitica/listar-curva` (commit `c37e119`/`5c587b6`). Incluye `convexity` desde 2026-04-21.
- [x] **`obtener_serie_macro(variable, ventana_dias)`** — `api/services/macro.py` con delega a `quant/stats.py`. Soporta `tamar|cer|dolar|badlar|mep|ccl|canje|caucion_ars|caucion_usd` + `<TICKER>.<CAMPO>`. Bloqueadas (faltan data): `ipc, ipim, riesgo_pais, repo, rem_inflacion`.
- [x] **`clasificar_nivel(variable, ventana_dias)`** — wrapper compacto (idem).
- [x] **`snapshot_curva_historico(curva, fecha)`** — curva entera en fecha pasada (commit `255cb82`, 2026-04-21).
- [x] **`calcular_pendiente_curva(curva, metrica, fecha_comparacion?)`** — slope + delta histórico (commit `255cb82`).
- [x] **`liquidez_secundario(ticker, dias)`** — ratio vs promedio + clasificación (commit `255cb82`).
- [x] **Convexity en `engines/curvas.py`** — calculada con `(1/P)·Σ[t(t+1)·CF_t/(1+y)^(t+2)]`, persistida en `Trading.TimeSales.convexity` (commit `0a5ad25`).
- [x] **CCL + canje** — `engines/dolares.py` (WS live, `Valuaciones.DolarSnapshot`) + `engines/dolar_mep.py` (cron 15 min, histórico). Variables desbloqueadas en `_MACROS`.
- [x] **Tool `caucion_actual` + `caucion_historica`** — motor `engines/caucion.py` con plazo dinámico (1D/3D/4D según próximo hábil). Colecciones `Trading.CaucionSnapshot` + `Trading.Caucion`.
- [x] **Tool `futuros_dlr` + `futuros_dlr_historico`** — motor `engines/futuros_dlr.py`. Discovery dinámico outrights (`underlying='Dólar USA A3500'`, `cficode='FXXXSX'`, un solo `/`, sin sufijo `M`). Tasa implícita TNA calculada vs MEP spot.
- [x] **Tool `argy_overview`** — endpoint `/api/cotizaciones/argy` agrega MEP/CCL/canje/caución ARS/USD con returns %Día/%7d/%MTD/%YTD calculados vs anchors históricos.
- [x] **Wrappers de historical endpoints como tools** (`historico_trades`, `historico_forwards`, `historico_breakevens`, `historico_mep`, `historico_curva`, `historico_opciones`).
- [x] **Scaffolding cliente BYMA Primarias Placements** (`core/byma.py`) — OAuth2 client_credentials con cache, rate limit, wrappers de los 4 métodos. Tests unitarios con mocks (`tests/unit/test_byma_client.py`). Smoke test (`scripts/test_byma.py`). **Pendiente desbloqueo portal** para ingesta y tools.

---

## Notas

- Prioridad = frecuencia en el golden set. Tool que aparece en 5+ test cases
  pasa a tier 1.
- Cuando aparezca repetida una variable que no tiene serie histórica cargada,
  la flaggeamos en "Series que faltarían en Mongo" para resolver con data job.

---

## Detalle operativo del eval script (no es "tool" pero sí trabajo)

- [ ] **Manejo de placeholders en `history` de test cases multi-turn** — mt-001, mt-002, mt-003
  - Los test cases multi-turn definen `history` con la respuesta del asistente del turno previo como placeholder (`"[respuesta con forward actual y contexto]"`).
  - Propuesta: sintaxis `<RUN_PREVIOUS_TURN_AND_CAPTURE/>` que el eval runner detecta y resuelve:
    1. Identifica el último mensaje del user en el history.
    2. Corre el asistente con ese query como stand-alone.
    3. Reemplaza el placeholder por la respuesta capturada.
    4. Con ese history ya resuelto, ejecuta el test del turno actual.
  - Cachea resultados por hash del history prefix para no rerun cada ejecución.
  - Bloqueante para evaluar correctamente multi-turn; sin esto, o el trader hardcodea respuestas (que driftean) o el test no vale.
