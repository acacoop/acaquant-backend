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

### TimeSales sigue enriquecido por curvas.py

`engines/curvas.py` sigue escribiendo `UpdateOne({"_id": doc["_id"]}, {"$set": campos})` sobre TimeSales (líneas 686-690). **No se tocó.** Las funciones de "histórico real" (descomposicion_retorno, historico_curva, serie_macro, snapshot_curva_historico) siguen leyendo de TimeSales.

**Por qué:** un test empírico (commits `cee8b74`, `ecb5d7a`) confirmó que TS Collections de Mongo 8 NO permiten `update_one` ni `update by (ticker, timestamp)` — solo updates por metaField (`ticker`). Migrar TimeSales a TS rompe el patrón actual de curvas.py. Esa migración requiere:
- Refactor de `engines/curvas.py` para mover el cálculo ANTES del insert.
- O migrar consumers de "histórico" a leer de `Trading.SnapshotsCierre`.

Ese trabajo quedó pendiente.

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

## Pendientes que quedaron

1. **Migración de "histórico real" a `SnapshotsCierre`** — los 4 consumers (descomposicion_retorno, historico_curva, serie_macro para tickers, snapshot_curva_historico) pueden refactorearse para leer de `Trading.SnapshotsCierre` en lugar de agregar TimeSales. Una vez hecho:
   - Se puede dejar de enriquecer TimeSales por completo (sacar las 2 UpdateOne de `engines/curvas.py:686-690`).
   - Se puede migrar TimeSales a TS Collection (append-only puro).
2. **Endpoint `/api/cotizaciones/historico/trades` (LibroPanel)** — proyecta `duration/TEA/TEM/paridad` que el frontend ignora. Es payload muerto (~30% del JSON). Sacar del project es trivial cuando hagamos pasada de cleanup.
3. **Tests pre-existentes rotos** — `test_pendiente_aplanamiento`, `test_pendiente_actual_largo_menos_corto`, `test_pendiente_con_fecha_comparacion_empinamiento`. No son regresión nuestra, pero conviene fixear.
