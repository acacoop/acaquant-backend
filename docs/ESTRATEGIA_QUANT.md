# ESTRATEGIA QUANT — señal intradía con trazabilidad

> **DOC VIVO con CHANGELOG OBLIGATORIO.** Este concepto arranca el 2026-07-29 y
> se perfecciona iterando. REGLA: todo cambio al modelo (factores, pesos,
> umbrales, resolver, vista) se refleja acá EN EL MISMO COMMIT — si el doc no
> refleja el estado real, el trabajo está incompleto.

## 1. Qué es (el problema que resuelve)

El trader opera CEDEARs intradía contra pivots (vista TRADING → PIVOTS). El
dolor: **los reversals/continuaciones los define el ÍNDICE** (QQQ/SPY) y el
trader los ve tarde — el trade se le va en contra o lo cierra antes de tiempo.

ESTRATEGIA QUANT responde con **UNA señal** (no un tablero): un score
`-100..+100` cuyo signo es la dirección (LONG/SHORT) y cuya magnitud es la
convicción, construido con 4 factores deterministas. Y —la parte que lo hace
un MODELO y no una superstición— **cada señal queda registrada y cada
resultado medido**: hit-rate, expectativa y edge por factor son visibles en la
misma vista.

**Principio rector (de docs/QUANTAI.md): el número lo calcula el código
determinista; la IA (cuando se enchufe) solo narra, nunca calcula.**

## 2. Restricción de datos que definió la arquitectura

Verificado 2026-07-29: `mercado.cedears_time_sales` (el único camino de precios
intradía ARS) **SE VACÍA al cierre** (cron cleanup). Consecuencias:

1. **No hay backtest histórico posible** del CEDEAR ARS intradía. El modelo se
   valida **hacia adelante** (forward-testing): emisión y medición en vivo.
2. **El resolver corre INTRADÍA** (cada 5' durante la rueda): etiqueta el
   resultado apenas vence el horizonte, antes de que el cleanup borre la
   evidencia. A las 20:10 UTC una corrida `--cierre` etiqueta las señales cuyo
   horizonte no llegó a vencer (marcadas `parcial=true`, excluidas de stats).
3. Cada día sin el motor prendido es evidencia perdida para siempre.

## 3. Arquitectura (ciclo completo)

```
inputs live (tablas existentes)          schema estrategia (nuevo)
┌──────────────────────────┐
│ cedears_ohlc_daily (piv) │   engines/estrategia.py        jobs/estrategia_resolver.py
│ cedears_snapshot (live)  │──▶ EMITE cada 60s ──┬─▶ senales (ledger  ──▶ RESUELVE intradía
│ day_trading_stats (rng)  │                     │    append-only)        └─▶ resultados
│ precios_acciones (corr,  │                     └─▶ eval_live (upsert)
│  pivots USD 4 frames)    │                              │                     │
└──────────────────────────┘                              ▼                     ▼
                                            GET /api/estrategia/live   GET /api/estrategia/
                                                     │                  track-record|senales
                                                     ▼                         │
                                          vista TRADING → tab ESTRATEGIA ◀─────┘
                                          (zona LIVE + zona TRACK-RECORD)

              modelo_pesos (versionado) ──▶ engine │ calibración futura ◀── resultados
```

| Pieza | Archivo | Rol |
|---|---|---|
| Cerebro puro | `quant/estrategia.py` | 4 factores + score. Sin I/O, testeado en `tests/unit/test_estrategia.py` |
| I/O SQL | `core/estrategia_sql.py` | readers de inputs + ledger (lo comparten engine y API) |
| Emisor | `engines/estrategia.py` | motor always-on L-V 13:20-20:05 UTC (`motor_estrategia.service`) |
| Resolver | `jobs/estrategia_resolver.py` | cron `*/5 13-20` + `--cierre` 20:10 |
| API | `api/services/estrategia.py` + `api/routers/estrategia.py` | `/api/estrategia/{live,track-record,senales}` — gate módulo `trading` (admin) |
| Vista | `acaquant-web` → TRADING → PIVOTS → radar, tab ESTRATEGIA | `trading-estrategia-radar.tsx` (compacta) + `estrategia-view.tsx` (overlay TRACK-RECORD) |
| Config | `config.py::ESTRATEGIA_*` | universo, umbrales, horizontes |
| Schema | `sql/schema.sql` → schema `estrategia` | senales, resultados, modelo_pesos, eval_live |

**El ÚNICO emisor de señales es el engine.** La API no recalcula nada (dos
verdades = trazabilidad rota). El universo es FIJO (`ESTRATEGIA_TICKERS`) a
propósito: si se emitiera solo cuando el trader mira, el track-record quedaría
sesgado por cuándo miraba.

## 4. El modelo v1 (los 4 factores)

Cada factor aporta en `[-1, +1]` (+ favorece LONG, − favorece SHORT).
`score = Σ wᵢ·fᵢ × 100`. Factores sin dato (None) NO diluyen: su peso se
redistribuye y `cobertura` lo transparenta.

| # | Factor | Peso v1 | Qué mide | Fórmula (resumen) |
|---|---|---|---|---|
| F1 | `recorrido_indice` | 0.35 | nafta del ÍNDICE de referencia | `0.6·room + 0.4·rango`. room = (dist a próx. resistencia − dist a próx. soporte)/(suma), sobre pivots ARS del índice. rango = posición invertida en el high-low del día |
| F2 | `alineacion` | 0.30 | ¿el índice confirma al papel? | % vs PP de ambos, saturado a ±1 en ±0.5%. Mismo lado → `|corr|·prom(s_p,s_i)` (confirma). Lados opuestos → `−|corr|·s_p` (divergencia = castiga la dirección del papel). corr = correlación diaria 60 ruedas papel↔índice |
| F3 | `nafta_papel` | 0.20 | ¿ya hizo su movida típica? | `uso = rango_hoy / rango_prom_20r`. uso<0.8 → apoyo leve (≤0.4) a la dirección actual; uso≥0.8 → castiga perseguir, hasta −1 en uso=1.5 |
| F4 | `confluencia` | 0.15 | pisos/techos multi-timeframe (USD) | zonas donde ≥2 pivotes de timeframes distintos (diario/semanal/mensual/anual del subyacente USD) caen a <0.35% entre sí. Soporte cerca abajo empuja +, resistencia cerca arriba −, alcance 1.5%, fuerza = # niveles |

- **Índice de referencia por ticker**: QQQ o SPY, el de mayor `|corr|` diaria
  (60 ruedas de `mercado.precios_acciones`). Se decide al boot del motor (el
  cron lo reinicia cada mañana).
- **Pesos v1 = hipótesis manual** (sin calibrar). Viven en
  `estrategia.modelo_pesos` (versionados, fila `activo=true`). Cada señal
  registra su `pesos_version` → siempre se sabe con qué modelo se emitió.
- Decisión consciente: **NADA de volumen/order-flow** — el volumen local es
  poco confiable (pedido del trader, 2026-07-29).

### Emisión al ledger

Accionable = `|score| ≥ 40` (`ESTRATEGIA_SCORE_UMBRAL`). Cooldown 15' por
ticker (`ESTRATEGIA_COOLDOWN_MIN`) salvo cambio de dirección. La evaluación
completa (accionable o no) va SIEMPRE a `eval_live` para la zona LIVE.

## 5. Trazabilidad (el contrato de honestidad)

- **Ledger inmutable**: `estrategia.senales` es append-only con el valor crudo
  de cada factor. JAMÁS se edita — el resultado va en `estrategia.resultados`.
- **Sin look-ahead**: el resolver solo usa cierres por minuto POSTERIORES al
  `ts` de la señal.
- **Resultado por horizonte** (15/30/60 min, `ESTRATEGIA_HORIZONTES`):
  `ret_pct` direccional (positivo = la señal ganó), `mfe/mae` (máx excursión a
  favor/en contra), `toco_objetivo` (+0.5% antes que −0.5%), `gano`.
- **Idempotencia**: PK `(senal_id, horizonte_min)` + ON CONFLICT DO NOTHING.
- **Muestra mínima**: el track-record marca `muestra_suficiente=false` con
  <30 señales resueltas — antes de eso los stats son ruido y la vista lo avisa.
- **Edge por factor**: expectativa cuando el factor empujaba en la dirección
  emitida vs en contra. Si "a favor" no supera a "en contra", el factor no
  predice y es candidato a recalibrar/eliminar.

## 6. Fases

| Fase | Qué | Estado |
|---|---|---|
| 1 | Motor + ledger + emisión sistemática | ✅ 2026-07-29 |
| 2 | Resolver intradía + resultados | ✅ 2026-07-29 |
| 3 | Vista (LIVE + TRACK-RECORD) | ✅ 2026-07-29 |
| 4 | Calibración: regresión logística `P(gano \| factores)` sobre resultados reales → pesos v2 | ⏳ requiere ~N≥200 señales resueltas |
| 5 | Capa IA: narración a demanda de los factores ya calculados (gateway `core/ai`, ver QUANTAI.md) | ⏳ diferida hasta validar el número |

## 7. Operación

```bash
# Droplet (una vez, tras el pull):
python -m scripts.apply_schema                     # crea schema estrategia
cp deploy/systemd/motor_estrategia.service /etc/systemd/system/ && systemctl daemon-reload
crontab /root/TradingAV/deploy/crontab.txt         # motor 13:20-20:05 + resolver */5
systemctl restart api.service
# El motor arranca solo con el cron de mañana; para hoy: systemctl start motor_estrategia
```

- Monitoreo: `manager.job_runs` tipo `estrategia_resolver`; log del motor via
  `journalctl -u motor_estrategia`.
- Los stats del track-record NO se creen hasta `muestra_suficiente=true`.

## 8. Decisiones tomadas / descartadas (no re-proponer sin novedad)

- **Volumen/tape como factor** — DESCARTADO (volumen local mentiroso).
- **Alertas de proximidad a pivote, radar múltiple** — descartado como
  producto separado: el concepto es UNA señal, no N indicadores.
- **Backtest histórico ARS** — IMPOSIBLE (tape se borra); prior opcional sobre
  el subyacente USD (Yahoo 1m, ventana corta) queda como idea diferida.
- **Recalcular señal en la API** — descartado (dos verdades).
- **Precio USD live para F4** — v1 usa el último close diario del subyacente
  como proxy (los niveles semanal/mensual/anual se mueven lento). Mejora
  pendiente: leer `mercado.adr_snapshot`/`eikon_snapshot` si está fresco.

## Changelog

- **2026-07-29 (4)** — El CONTEXTO ya se muestra en la vista. La tab ESTRATEGIA del
  RADAR de TRADING deja de mostrar la señal (modelo v1 PARKEADO) y muestra el panel
  de contexto de los 5 papeles foco (`config.ESTRATEGIA_CONTEXTO_TICKERS` = QQQ SPY
  SNDK NVDA RKLB): **ATR%** (rango típico diario) + **ER 30 / ER día** (Efficiency
  Ratio) con chip de régimen (CHOPPY / MIXTO / LIMPIO, umbral `ESTRATEGIA_ER_CHOPPY`
  = 0.30). Backend nuevo: `GET /api/estrategia/contexto` (`svc.get_contexto`, lee
  `core.estrategia_sql.atr_ultima_rueda` + `core.bars_sql.efficiency_ratio_live`).
  Frontend: `trading-estrategia-radar.tsx` reescrito; `estrategia-view.tsx` (overlay
  del track-record del modelo v1) queda sin usar pero no se borra.

- **2026-07-29 (3)** — Cimientos de CONTEXTO determinista (arranque de un enfoque
  nuevo; el modelo de señal v1 de arriba queda PARQUEADO, no se usa). Dos métricas
  puras en `quant/rango.py` (`atr`, `efficiency_ratio` + tests
  `tests/unit/test_rango.py`):
  - **ATR-20 por ticker** (ARS) — se persiste en una columna nueva `atr` de
    `mercado.cedears_ohlc_daily`; la ventana móvil del job `jobs/cedears_ohlc_daily.py`
    subió de 20→60 ruedas (se necesitan 21 para el ATR-20 + deja historia). Se
    calcula UNA vez por rueda, post-cierre.
  - **Efficiency Ratio intradía (Kaufman)** — nuevo ARCHIVO permanente de barras de
    1 minuto `mercado.cedears_bars_1m` (job `jobs/cedears_bars_1m.py`, 20:20 UTC L-V,
    resamplea el tape ANTES del cleanup 23:50). El ER se DERIVA de las barras (vivo
    desde el tape, histórico desde el archivo) con `core/bars_sql.py`
    (`efficiency_ratio_live`/`_hist`); no se persiste el número — la fuente de verdad
    son las barras. Universo foco: QQQ SPY SNDK NVDA RKLB (el job impacta a TODOS los
    CEDEARs igual). Aún sin consumidor (motor/API): es la capa de datos.

- **2026-07-29 (2)** — La vista se muda ADENTRO de la pantalla de trading (pedido
  del user: "mejor en vez de una nueva vista, meterlo donde ya miro"): la caja de
  abajo del RADAR pasa a tener tabs PIVOTES | ESTRATEGIA. La tab muestra la señal
  live compacta (click en fila = carga el ticker en el chart, como el radar de
  pivotes) y un botón TRACK-RECORD abre la trazabilidad completa en overlay.
  Se eliminó la tab ESTRATEGIA del shell de /trading. Backend sin cambios.

- **2026-07-29** — Nace el proyecto. Fases 1-3 completas: schema `estrategia`
  (senales/resultados/modelo_pesos/eval_live), cerebro puro
  `quant/estrategia.py` (4 factores, pesos v1-manual .35/.30/.20/.15),
  `core/estrategia_sql.py`, motor `engines/estrategia.py` (60s, umbral 40,
  cooldown 15'), resolver intradía `jobs/estrategia_resolver.py` (5', 15/30/60,
  objetivo/stop ±0.5%, `--cierre` 20:10), API `/api/estrategia/*` (gate
  `trading`), vista TRADING → tab ESTRATEGIA (LIVE + TRACK-RECORD), tests unit,
  deploy (systemd + cron). Universo inicial: 16 CEDEARs líquidos en
  `config.ESTRATEGIA_TICKERS`. Pendiente: calibración (F4) y capa IA (F5).
