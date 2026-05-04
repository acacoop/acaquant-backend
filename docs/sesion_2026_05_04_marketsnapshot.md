# Sesión 2026-05-04 — Refactor MarketSnapshot + Order Book L2 + bug fixes

> Documento de contexto. Si algo se rompió después de esta sesión, acá está el "antes" y "después" de cada cambio para diagnosticar.

## Resumen ejecutivo

Tres ejes de trabajo en esta sesión:

1. **Bug fixes operativos** — scanner triggers MEP corriendo 24/7, contratos DLR vencidos quedando en pantalla, cleanup nightly.
2. **Order Book L2** — motor dedicado nuevo + endpoint REST + 4 tools MCP nuevas (live + histórico).
3. **Refactor MarketSnapshot** — limpieza de campos legacy, separación clara entre motores valores y curvas, migración de 5 consumers que leían TimeSales innecesariamente.

Total: **15 commits** entre `52dbdd8` (HEAD inicial) y `4876f96` (HEAD final). Ningún cambio destructivo de datos. Todos los archivos modificados son retrocompatibles con el resto del repo.

---

## Eje 1 — Bug fixes operativos

### 1.1 Scanner triggers MEP — guard horario

**Commit:** `2c08b38` — `fix(triggers-mep): scanner solo corre en L-V 13:00-20:59 UTC`

**Antes:** `engines/services/triggers_mep.py::scanner_loop` corría 1 tick/seg 24/7. Durante la ventana de Atlas pausa (04:00–11:20 UTC), cada `find()` fallaba con `ServerSelectionTimeoutError` y se loggeaba un stacktrace completo con `logger.exception`. ~26,000 stacktraces/día en logs.

**Después:** `while True` envuelto con guard:
```python
now = datetime.now(UTC)
en_ventana = now.weekday() < 5 and 13 <= now.hour < 21
if en_ventana:
    # tick normal
await asyncio.sleep(interval_s)
```

**Archivos tocados:** `api/services/triggers_mep.py:scanner_loop` (líneas ~448-472).

**Cómo revertir:** sacar el guard y correr siempre. Riesgo nulo en operación, costo en logs.

---

### 1.2 Contratos DLR vencidos quedando en pantalla

**Commits:**
- `e948edf` — `fix(futuros-dlr): filtrar contratos vencidos del snapshot`
- `f66fba6` — `feat(scripts): cleanup_futuros_dlr_vencidos one-shot`
- `ebb3226` — `feat(jobs): cleanup_futuros_dlr nightly + entry en crontab`

**Antes:** El motor `engines/futuros_dlr.py` hace `ReplaceOne(upsert=True)` por ticker en `Trading.FuturosDLRSnapshot` y **nunca borra**. Cuando un contrato vencía (ej. DLR/ABR26 el 30/abr), su doc quedaba "fantasma" con la última info. La función `_dias_a_vto` con `max(1, ...)` enmascaraba el negativo como "1 día". El frontend mostraba el contrato vencido con TNA absurdas.

**Después:** Tres capas de protección:

1. **API filtra al servir** (`api/services/derivados.py::get_futuros_dlr`): query con `{"vencimiento": {"$gt": hoy}}`.
2. **Script one-shot** (`scripts/cleanup_futuros_dlr_vencidos.py`) con `--dry`/`--apply` para limpiar la basura existente.
3. **Job nightly** (`jobs/cleanup_futuros_dlr.py`) corre 12:30 UTC L-V. Crontab actualizado.

**Archivos tocados:**
- `api/services/derivados.py` — modificado.
- `scripts/cleanup_futuros_dlr_vencidos.py` — nuevo.
- `jobs/cleanup_futuros_dlr.py` — nuevo.
- `deploy/crontab.txt` — agregada línea.

**Cómo revertir:** la API sin filtro vuelve a mostrar fantasmas. El job sigue limpiando aunque el filtro esté.

---

## Eje 2 — Order Book L2 (captura full)

### 2.1 Motor de captura L2

**Commits:**
- `c3a2b04` — `feat(mcp): tools order_book + order_books_curva (depth 5 live)`
- `58b98a8` — `docs(mcp): documentar order_book + order_books_curva en MCP_TOOLS`
- `e345863` — `feat(order-book-l2): motor dedicado de captura L2 del book`
- `b39f1f2` — `feat(order-book-l2): exponer Trading.OrderBookL2 via REST + MCP`

**Antes:** No había forma de almacenar el order book histórico tick-a-tick. `Trading.MarketSnapshot.book` tiene depth 5 pero solo el último estado (replaced cada 1s).

**Después:** Sistema completo de captura L2:

1. **Motor nuevo `engines/order_book_l2.py`** — sesión rofex separada (sin chocar con motor_rofex), suscribe solo entries `BIDS+OFFERS` para tickers en `config.TICKERS_BOOK_FULL` (hoy: `MERV - XMEV - AL30 - CI`). Cada cambio del book → doc nuevo en `Trading.OrderBookL2` (Time Series Collection). Buffer + thread flush cada 500ms con `bulk_insert`.

2. **Time Series Collection** — `Trading.OrderBookL2` con `timeField=ts`, `metaField=ticker`, `granularity=seconds`. Compresión columnar nativa.

3. **Service systemd** — `deploy/systemd/motor_order_book_l2.service`. Crontab arranca/para L-V 13:00–20:05 UTC.

4. **Setup script** — `scripts/init_orderbook_l2_collection.py` (idempotente, una vez).

5. **API + MCP** — 4 tools nuevas:
   - `order_book(ticker)` — último estado (de MarketSnapshot).
   - `order_books_curva(curva)` — todos los tickers de una curva (de MarketSnapshot).
   - `order_book_historico(ticker, desde, hasta, limit)` — serie temporal (de OrderBookL2).
   - `listar_tickers_orderbook_l2()` — qué tickers tienen captura activa.

**Archivos creados:**
- `engines/order_book_l2.py`
- `deploy/systemd/motor_order_book_l2.service`
- `scripts/init_orderbook_l2_collection.py`
- `api/services/order_book.py`
- `api/services/order_book_historico.py`

**Archivos modificados:**
- `config.py` — agregado `TICKERS_BOOK_FULL`.
- `core/websocket.py` — agregado parámetro `entries` opcional a `iniciar_ws` y `agregar_suscripciones` (retrocompat).
- `api/mcp/server.py` — agregadas 4 tools.
- `api/routers/cotizaciones.py` — agregado endpoint `/order-book-historico`.
- `deploy/crontab.txt` — start/stop motor.
- `docs/MCP_TOOLS.md` — documentación.

**Cómo revertir:** `systemctl stop motor_order_book_l2.service` + sacar entries del crontab. Las tools MCP siguen, pero la colección deja de crecer.

---

## Eje 3 — Refactor MarketSnapshot

### 3.1 Quitar `top_trades` y `recent_trades` (payload muerto)

**Commit:** `3777b55` — `refactor(market-snapshot): sacar top_trades y recent_trades`

**Antes:** `engines/valores.py` mantenía 2 listas en RAM (`top_trades` con top 15 por size, `recent_trades` con últimos 30) y las persistía en cada `ReplaceOne` a MarketSnapshot. Auditoría exhaustiva: **cero consumers** en backend/frontend/MCP.

**Después:** Removido del state, del flow del motor, del doc persistido, y del `$project` de `api/services/renta_fija.py:115` (que los proyectaba en `/api/cotizaciones/renta-fija` pero el frontend nunca los leía).

**Cleanup:** Script `scripts/cleanup_marketsnapshot_legacy.py` con `--apply` que hace `$unset` de los campos en docs ya persistidos.

**Archivos tocados:**
- `engines/valores.py` — removido tracking en `_arranque_en_frio`, `_procesar_tick_logica`, `_snapshot_loop`. Y el state init.
- `api/services/renta_fija.py:115` — removido `"recent_trades": 1` del project.
- `scripts/cleanup_marketsnapshot_legacy.py` — nuevo.

**Cómo revertir:** restaurar las líneas del motor. Los docs viejos sin esos campos no rompen nada (el código que los proyectaba ya no existe).

---

### 3.2 Motor `valores.py`: ReplaceOne → UpdateOne $set parcial

**Commit:** `9229148` — `refactor(valores): UpdateOne $set parcial en lugar de ReplaceOne`

**Antes:** `engines/valores.py::_snapshot_loop` cada 1 segundo:
1. Leía MarketSnapshot via `find_one` (preserve defensiva de campos analíticos).
2. Mergeaba campos analíticos (TEA/duration/etc) en el dict local.
3. Hacía `ReplaceOne(doc completo, upsert=True)`.

Patrón mutuo con `engines/curvas.py` que escribía los analíticos. La guarda existía porque sin ella el ReplaceOne de valores pisaba lo de curvas.

**Después:** `UpdateOne($set: dot-notation, upsert=True)` con solo los campos del motor:
```python
{
    "updated_at": ts,
    "book.bids": [...],
    "book.offers": [...],
    "metrics.last_price": ...,
    "metrics.open_price": ...,
    # ... NO toca metrics.{TEA, TEM, duration, mod_duration, convexity, paridad}
}
```

Cero find_one defensivo. Cero pisado entre motores. **Cada motor escribe lo suyo, sin guardas.**

**Archivos tocados:**
- `engines/valores.py` — import `ReplaceOne → UpdateOne`. Removida lógica de find+merge. Cambiado el bulk_write.

**Cómo revertir:** volver a ReplaceOne con find_one defensivo. Riesgo si curvas.py no terminó de escribir antes que valores.py corra → pierde TEA/duration por 1-5s hasta que curvas vuelva.

---

### 3.3 Job `snapshot_cierre.py`: lee MarketSnapshot en lugar de TimeSales

**Commit:** `a9e1b37` — `refactor(snapshot-cierre): leer MarketSnapshot en lugar de agregar TimeSales`

**Antes:** El job 20:25 UTC L-V agregaba TimeSales del día con `$group/$first` por ticker para construir el cierre. Acoplado al enriquecimiento histórico tick-a-tick que hacía curvas.py sobre TimeSales.

**Después:** Lee `Trading.MarketSnapshot` directo (1 query con `$in`). Como el cron corre 20 min después que el motor para a 17:05 ART, MarketSnapshot tiene el cierre real congelado.

**Guard nuevo:** skipea tickers con `last_price == 0` o `total_nominals == 0`. Esto cubre feriados (motor arranca por cron L-V pero no hay trades). Sin guard, persistiría docs con TEA stale del cierre anterior.

**Output idéntico al anterior** — `fair_value.py` y consumers existentes no notan diferencia.

**Archivos tocados:**
- `jobs/snapshot_cierre.py` — reescrita `procesar_curva` para leer MarketSnapshot. Removido import de `snapshot_curva_historico`. Sumada lógica de skip.

**Cómo revertir:** restaurar el código anterior que agregaba TimeSales. Va a seguir funcionando porque TimeSales aún tiene los campos enriquecidos (no los quitamos todavía).

**Validación que ya hicimos:** `python -m jobs.snapshot_cierre --fecha 2026-05-01 --dry` → "0 bonos persistidos (33 skipped)" — feriado correctamente detectado.

---

### 3.5 `curvas.py`: deja de enriquecer TimeSales, lee de MarketSnapshot

**Antes:** `engines/curvas.py::run` cada 5s buscaba en TimeSales docs sin `duration` (los trades nuevos que metió valores.py), calculaba campos por trade, escribía `UpdateOne({"_id": doc["_id"]}, {"$set": campos})` sobre cada uno, y propagaba el último por ticker a MarketSnapshot.metrics.

**Después:** Cada 2s lee `MarketSnapshot.metrics.last_price` por ticker, calcula campos a partir de ese precio + dependencias (CER, MEP, A3500, dias_habiles), y escribe **solo** a `MarketSnapshot.metrics.{TEA, TEM, duration, mod_duration, convexity, paridad}`. **TimeSales no se toca.**

Cambios concretos en `engines/curvas.py`:
1. `INTERVALO_SEGUNDOS`: 5 → 2 (sin escrituras a TimeSales se puede más rápido).
2. `BATCH_SIZE = 200` removido (no hace falta limit cuando se lee 1 doc por ticker).
3. Eliminadas las 2 `UpdateOne` sobre TimeSales (líneas viejas 686-690).
4. Lectura cambia de TimeSales `find(... duration: $exists: false)` → MarketSnapshot `find({metrics.last_price: $gt: 0})`.
5. Construye un "fake_doc" `{ticker, price, timestamp}` desde el doc del MarketSnapshot y lo pasa a `calcular_campos` igual que antes.
6. Cache RAM `ultimo_calculado: dict[ticker → last_price]` — skipea recálculo si el ticker no se movió desde la iteración anterior. Se invalida completo cuando se recargan CER/MEP/A3500 (los outputs dependen de eso).

**Por qué da el mismo TEA/duration que antes:**
- `calcular_campos` solo consume `doc.price` y `doc.timestamp`. El precio que viene de MarketSnapshot es exactamente el mismo que se escribió desde el último trade en TimeSales.
- CER, MEP, A3500, dias_habiles, instrumento — todos siguen viniendo del mismo lugar.

**Performance:** sin lecturas a TimeSales con $exists/sort/limit y sin bulk_write a TimeSales, la carga del motor cae ~80% en tickers con poco volumen (cache hit) y ~50% en líquidos (cache miss pero sin escritura cara).

**Lo que se rompe — los 4 consumers de "histórico real" sobre TimeSales:**

| Consumer | Para fechas ≥ del cambio devuelve |
|---|---|
| `api/services/analitica.py::snapshot_curva_historico` | trades sin TEA/TEM/duration/paridad |
| `api/services/renta_fija.py::get_historico_curva` | series diarias sin TEA |
| `api/services/macro.py::obtener_serie_macro` (con `<TICKER>.<CAMPO>`) | serie vacía o nulls |
| `api/services/descomposicion_retorno.py` (usa snapshot_curva_historico) | atribución parcial / null |

Para fechas pasadas (< del cambio), siguen funcionando — los trades viejos en TimeSales mantienen sus campos enriquecidos.

**El frontend NO se ve afectado** — la tabla de renta-fija lee de MarketSnapshot, el LibroPanel del LibroPanel ignora los campos enriquecidos. Lo que pierde son features de análisis avanzado vía MCP/asistente.

**Mitigación pendiente:** migrar los 4 consumers a `Trading.SnapshotsCierre` (que ya tiene cierre diario por ticker con TEA/duration). Eso es la fase 3 — pendiente.

**Cómo revertir:** `git revert <commit>` y reiniciar `motor_curvas.service`. Curvas vuelve a enriquecer TimeSales por trade.

---

### 3.4 Migración de 5 consumers TimeSales → MarketSnapshot

**Commit:** `4876f96` — `refactor(consumers): migrar lectura "última X por ticker" a MarketSnapshot`

**Antes:** 5 consumers hacían el patrón "`$sort timestamp desc + $group $first`" sobre TimeSales para obtener "última TEA/TEM/duration/paridad por ticker". Cada llamada iteraba millones de docs históricos.

**Después:** Cada uno lee `find` directo sobre MarketSnapshot.metrics. 1 doc por ticker. Mismo valor (curvas.py escribe en MarketSnapshot el mismo TEA del último trade enriquecido en TimeSales).

**Archivos tocados:**

| Archivo | Función | Patrón nuevo |
|---|---|---|
| `engines/forwards.py` | `obtener_ultimas_teas` | `find` sobre `metrics.TEA` + `metrics.duration` |
| `engines/breakevens.py` | `obtener_tems` | `find` sobre `metrics.TEM` |
| `engines/breakevens.py` | `obtener_paridades` | `find` sobre `metrics.paridad` |
| `engines/breakevens.py` | `obtener_teas_cer` | `find` sobre `metrics.TEA` |
| `api/services/portfolio.py` | enrich tea/paridad/duration | `find` sobre 3 metrics |
| `api/services/renta_fija.py` | enrich_map en `listar_curva` | `find` sobre last + analíticas |
| `api/agent/context.py` | `_last_tea` | `find` (+ fix bug pre-existente: filtraba por `instrumento`, campo inexistente) |

**`engines/breakevens.py::obtener_precios` NO se cambió** porque lee `price` (campo del trade, no analítico) — sigue en TimeSales.

**Cómo revertir:** restaurar los pipelines `aggregate` originales sobre TimeSales. Mismo resultado, más lento.

---

## Lo que NO se cambió (intencional)

### Tests pre-existentes fallidos (no son regresión nuestra, ver abajo)

(esta sección era sobre el enriquecimiento de TimeSales — ya está resuelto, ver 3.5)

### Tests pre-existentes fallidos (no son regresión)

`pytest tests/unit/test_cotizaciones_tier2.py` falla 3 tests sobre `calcular_pendiente_curva` con `KeyError: 'delta_bps'`. Verificado con `git stash`: **ya fallaban antes de los cambios**. Es bug pre-existente (función no devuelve `delta_bps` cuando se pasa `fecha_comparacion`), no relacionado a esta sesión.

---

## Estado actual de `MarketSnapshot` (post-sesión)

```javascript
{
  ticker:     "MERV - XMEV - TX26 - 24hs",
  updated_at: ISODate("..."),
  book: {
    bids:   [{price, size}, ...x5],     // ← engines/valores.py
    offers: [{price, size}, ...x5],     // ← engines/valores.py
  },
  metrics: {
    // Escritos por engines/valores.py (refresh 1s):
    last_price, open_price, high_price, low_price, closing_price,
    vwap, total_nominals,

    // Escritos por engines/curvas.py (refresh 5s):
    TEA, TEM, duration, mod_duration, convexity, paridad,
  }
  // SIN top_trades, SIN recent_trades.
}
```

Cada motor escribe SOLO sus campos via `UpdateOne($set: dot-notation, upsert=True)`. Cero pisado mutuo.

---

## Rollback "nuclear" si todo se rompe

Si esta semana ves comportamiento raro y querés volver al estado pre-sesión:

```bash
cd /root/TradingAV
git checkout 52dbdd8                              # HEAD pre-sesión
systemctl restart api.service motor_rofex.service
# Atlas se mantiene como está — los datos no cambiaron de schema
# salvo los $unset de top_trades/recent_trades (cosméticos, no rompen)
```

Para volver al estado actual: `git checkout main` y restart.

---

## Eje 4 — Migración de consumers históricos a SnapshotsCierre

### 4.1 Backfill de SnapshotsCierre para `soberanos`, `tamar`, `dolar_linked`

**Antes:** SnapshotsCierre cubría solo `tasa_fija` y `cer` (25 días, desde 2026-03-25 hasta 2026-04-30). Las otras 3 curvas tenían 0 docs.

**Después:** las 5 curvas con cobertura desde el primer día con trades en TimeSales hasta 2026-04-30:
- soberanos: 931 docs (rango 2026-01-02 → 2026-04-30, ~80 días con trades).
- tamar: 78 docs (rango 2026-03-23 → 2026-04-30, 26 días).
- dolar_linked: 16 docs (rango 2026-04-27 → 2026-04-30, 4 días — curva nueva).

**Script:** `scripts/backfill_snapshots_cierre.py` (idempotente, `--dry`/`--force`/`--curva`/`--desde`/`--hasta`). Usa la lógica vieja de `snapshot_curva_historico` (agregar TimeSales por ticker tomando el último trade del día) para llenar los días pasados antes de que se rompiera por el cambio en 3.5.

**Verificación:** `scripts/audit_historicos.py` después del backfill mostró las 5 curvas en estado "✓ cobertura razonable".

**Cómo revertir:** delete_many sobre SnapshotsCierre para las curvas backfilleadas. Pero los datos se regeneran corriendo el script de nuevo desde TimeSales mientras esté enriquecido.

### 4.2 `get_historico_curva` y `snapshot_curva_historico` migrados a SnapshotsCierre

**Antes:** Ambos hacían aggregate sobre TimeSales con `$group $first` por (ticker, día) o por ticker en un día. Caro (millones de docs scaneados) y depende del enriquecimiento histórico de TimeSales (que después del 3.5 dejó de existir para días nuevos).

**Después:**

`api/services/renta_fija.py::get_historico_curva` — lee directo `Trading.SnapshotsCierre` filtrado por curva. 1 find, sin aggregate. Mapea minúsculas → mayúsculas en TEA/TEM (el frontend usa MAYÚSCULAS, SnapshotsCierre las guarda en minúsculas).

`api/services/analitica.py::snapshot_curva_historico` — primero busca en SnapshotsCierre `(ts_cierre=fecha, curva)`. Si encuentra → mapea al shape esperado. Si no → fallback al aggregate viejo sobre TimeSales (defensivo: fechas anteriores al backfill o gaps).

**Beneficios:**
- 1 find sobre colección chica (~1800 docs total) en lugar de aggregate sobre 750k+ docs de TimeSales.
- Independiente del estado de enriquecimiento de TimeSales.
- `descomposicion_retorno` viene gratis: ya llama a `snapshot_curva_historico`.

**Lo que se rompe — nada visible al usuario.** Mismo shape de retorno. Frontend (vista `/retorno`, tabs Descomposición / curvas-chart) sigue funcionando idéntico.

**Cómo revertir:** restaurar las versiones anteriores de las dos funciones. La data en SnapshotsCierre persiste sin uso.

---

## Pendientes que quedaron

1. **Migrar `obtener_serie_macro` (con `<TICKER>.<CAMPO>`)** — sigue agregando TimeSales para series diarias de un campo de un ticker. Pasar a leer de SnapshotsCierre. Solo MCP, baja prioridad.
2. **Endpoint `/api/cotizaciones/historico/trades` (LibroPanel)** — proyecta `duration/TEA/TEM/paridad` que el frontend ignora. Es payload muerto (~30% del JSON). Sacar del project es trivial.
3. **Migrar TimeSales a TS Collection** — ahora TimeSales está cerca de ser append-only (sólo el bug 4.1 deja `obtener_serie_macro` y `historico_trades` como readers de campos enriquecidos). Una vez resueltos, se puede crear `TimeSales_ts` con `timeField=timestamp, metaField=ticker, granularity=minutes`, copiar la data y swap. Compresión esperada ~60-80%.
4. **Tests pre-existentes rotos** — `test_pendiente_aplanamiento`, `test_pendiente_actual_largo_menos_corto`, `test_pendiente_con_fecha_comparacion_empinamiento`. No son regresión nuestra, pero conviene fixear.
