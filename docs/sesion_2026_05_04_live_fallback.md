# Sesión 2026-05-04 (PM) — Live fallback en SnapshotsCierre + cache fix Vercel

> Documento de contexto. Continuación de `sesion_2026_05_04_marketsnapshot.md`. Si algo se rompió después de esta sesión, acá está el "antes" y "después" para diagnosticar.

## Resumen ejecutivo

Tres ejes de trabajo:

1. **Migración Carry Trade a SnapshotsCierre** — `carry_trade._precios_diarios_curva` pasa de `aggregate $group` sobre TimeSales a `find` directo sobre SnapshotsCierre. Sin domingos fantasma.
2. **Patrón "live fallback"** — `snapshot_curva_historico`, `get_historico_curva` y `_precios_diarios_curva` ahora usan `MarketSnapshot.metrics` cuando la fecha pedida es hoy y el cron 20:25 UTC aún no corrió.
3. **Cache fix en Vercel** — `acaquant-web` tenía cache server-side en las routes de proxy que servía respuestas viejas hasta 15 min. Fix con `dynamic="force-dynamic"` + `Cache-Control: no-store`.

Total: **5 commits TRD-FX + 1 commit acaquant-web**. Cero cambios destructivos de datos.

---

## Síntoma inicial

Usuario reportó:
- **Retorno Total** parado en 29/04. Hoy 04/05 (lunes hábil), el motor de mercado arriba.
- **Carry Trade** mostrando "última fecha = 03/05" (que fue domingo, ningún mercado).

Ambas vistas viven en `acaquant-web` (`/retorno`, tab Descomposición + tab Carry).

---

## Diagnóstico

Script `scripts/diagnostico_retorno_carry.py` (read-only) confirmó:
1. `Trading.SnapshotsCierre` cubre las 5 curvas hasta 30/04 (último día hábil persistido).
2. `Trading.MarketSnapshot` con todos los bonos de tasa_fija/cer y precio live (motor arriba).
3. `Valuaciones.Dolar` sin sábados/domingos en los últimos 7 días → el "03/05" del Carry NO venía de la API.

Conclusión: la API estaba bien. Dos problemas distintos:
- Carry Trade leía TimeSales con `$group` sobre `$dateToString(timestamp)` — patrón propenso a fechas raras + caro.
- El frontend cacheaba la respuesta vieja de `/api/historico-curva` (lo que arma el selector de fechas) por hasta 15 min en el CDN de Vercel.

---

## Eje 1 — Carry Trade a SnapshotsCierre

**Commit:** `c9e95de` — `refactor(carry-trade): leer SnapshotsCierre + punto live para hoy`

**Antes:** `_precios_diarios_curva` hacía:
```python
db["TimeSales"].aggregate([
  {"$match": {"ticker": {"$in": [...]}, "timestamp": {"$gte": ..., "$lt": ...}, "price": {"$gt": 0}}},
  {"$addFields": {"fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}}},
  {"$sort": {"timestamp": -1}},
  {"$group": {"_id": {"ticker": "$ticker", "fecha": "$fecha"}, "price": {"$first": "$price"}}},
])
```
Patrón caro (millones de docs scaneados) + propenso a dupes/fantasmas si el motor escribió docs con timestamp raro.

**Después:**
```python
db["SnapshotsCierre"].find(
  {"curva": curva, "ts_cierre": {"$gte": desde.isoformat(), "$lte": hasta.isoformat()}, "ultimo_precio": {"$gt": 0}},
  {...},
)
```
Una sola query simple. SnapshotsCierre tiene 1 doc por `(curva, ts_cierre, ticker)` con cierre real persistido — los días no hábiles ni siquiera existen en la colección. Adiós domingo fantasma.

**Helper nuevo `_precios_live_curva`:** si el rango incluye hoy y SnapshotsCierre no tiene cierre del día (cron 20:25 UTC todavía no corrió), agrega un punto desde `MarketSnapshot.metrics.last_price` para los tickers de la curva. Lookup mínimo a `Trading.Curvas` para el mapeo `ticker → ticker_corto`.

**Archivos tocados:**
- `api/services/carry_trade.py` — `_precios_diarios_curva` reescrito + nuevo helper `_precios_live_curva`. Removido import `UTC` que quedó sin uso.

**Cómo revertir:** restaurar el aggregate. Va a seguir andando porque TimeSales aún tiene los precios raw.

---

## Eje 2 — Live fallback generalizado

### 2.1 `snapshot_curva_historico` — fallback A para fecha=hoy

**Commit:** `29a8a01` — `refactor(retorno-total): live fallback a MarketSnapshot para fecha=hoy`

**Antes:** Tres caminos:
1. SnapshotsCierre tiene docs para `fecha` → mapear y devolver.
2. SnapshotsCierre vacío → fallback a `aggregate $group` sobre TimeSales del día.

El (2) es lento y para fechas posteriores al refactor del 04/05 AM (eje 3.5 de la sesión anterior, motor_curvas dejó de enriquecer TimeSales) devuelve precios sin TEA/duration/paridad. Para `fecha=hoy` específicamente, el cron snapshot_cierre corre 20:25 UTC, así que cualquier consulta antes de esa hora caía en el (2) y devolvía analíticas null.

**Después:** Tres caminos:
1. SnapshotsCierre tiene docs → mapear y devolver. (igual)
2. SnapshotsCierre vacío Y `fecha == today_utc` → leer `MarketSnapshot.metrics` directo. Mismos campos (`last_price, TEA, TEM, paridad, duration, mod_duration, convexity`). Mismo formato de salida.
3. SnapshotsCierre vacío Y fecha pasada → fallback B (aggregate TimeSales) — sólo válido para fechas pre-refactor 04/05.

**Archivos tocados:**
- `api/services/analitica.py` — bloque "Fallback A" agregado entre el camino primario y el fallback B. Import de `date` agregado.

### 2.2 `get_historico_curva` — fila live de hoy

**Commit:** `cb8bbd3` — `refactor(historico-curva): live fallback a MarketSnapshot para hoy`

**Antes:** `find` directo sobre SnapshotsCierre, ordenado por `(ts_cierre, ticker)`. Si hoy no estaba persistido, no aparecía en la lista.

**Después:** Después del find, si `today_str` no está en el conjunto de fechas devueltas, agrega una fila por ticker desde MarketSnapshot.metrics (mismo shape: `fecha, ticker, tipo, price, TEA, TEM, duration, paridad`).

**Por qué importa:** El frontend usa `/api/cotizaciones/historico/curva` para armar el listado de fechas disponibles del selector. Sin esta fila, "hoy" nunca aparecía como opción → el selector tope era 30/04 → el usuario veía "Retorno Total parado en 30/04".

**Archivos tocados:**
- `api/services/renta_fija.py::get_historico_curva` — bloque live fallback agregado al final de la función. Import de `date` agregado.

---

## Eje 3 — Cache fix en acaquant-web (Vercel)

**Commit (acaquant-web):** `764ec54` — `fix(api-routes): no cachear analítica + historico-curva en Vercel`

**Antes:** Dos archivos con cache agresivo:

1. `src/app/api/historico-curva/route.ts`:
   - `apiFetch(..., { revalidate: 300 })` → Next cachea la respuesta upstream 5 min.
   - Header `Cache-Control: s-maxage=300, stale-while-revalidate=600` → CDN de Vercel sirve la respuesta hasta 15 min después.

   Resultado combinado: aunque el backend tuviera datos frescos, el frontend leía la respuesta vieja del CDN. Más grave: la cache se "envenena" — un fetch que ocurrió a las 14:00 con `fechas=[..., "2026-04-30"]` queda servido hasta 14:15.

2. `src/app/api/analitica/[...path]/route.ts`:
   - `fetch(..., { cache: "no-store" })` ✓ (estaba bien adentro)
   - Pero **sin `export const dynamic = "force-dynamic"`** Next puede cachear el handler entero en el edge.

**Después:**

```typescript
// historico-curva/route.ts
export const dynamic = "force-dynamic";
export const revalidate = 0;

const NO_CACHE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate",
};
// ... apiFetch sin revalidate
// ... return NextResponse.json(data, { headers: NO_CACHE_HEADERS })
```

```typescript
// analitica/[...path]/route.ts
export const dynamic = "force-dynamic";
export const revalidate = 0;
// (resto igual, ya tenía cache:"no-store" en el fetch)
```

**Cómo revertir:** sacar los exports y volver al `revalidate: 300`. Va a volver a aparecer el problema.

---

## Eje 4 — Tooling de diagnóstico/validación

Dos scripts committeados:

1. `scripts/diagnostico_retorno_carry.py` — read-only. Reporta:
   - Cobertura de SnapshotsCierre por curva (última fecha, n docs, primero).
   - Cobertura del 30/04 por curva (sanity check post-cron).
   - MarketSnapshot live por curva (n bonos con `last_price > 0`).
   - Valuaciones.Dolar últimos 7 días con flag de fin de semana.

2. `scripts/validar_retorno_carry.py` — llama services directos (sin HTTP). Reporta:
   - `serie_carry_trade` para tasa_fija y cer → `fecha_base/fecha_final/n días`.
   - `descomposicion_realizada(desde-20d, hasta=hoy)` para tasa_fija y cer → `bonos`, `cer_accrual`.
   - `get_historico_curva` para tasa_fija y cer → última fecha que ve el selector del front.

Ambos pensados para correr en el Droplet sin queries Mongo manuales:
```bash
source venv/bin/activate
python -m scripts.diagnostico_retorno_carry
python -m scripts.validar_retorno_carry
```

---

## Estado actual de los endpoints afectados (post-sesión)

| Endpoint | Fuente primaria | Fallback | Cache frontend |
|---|---|---|---|
| `/api/cotizaciones/historico/curva` | `Trading.SnapshotsCierre` | `MarketSnapshot.metrics` para hoy | `no-store` (Vercel route) |
| `/api/analitica/snapshot-curva-historico` | `Trading.SnapshotsCierre` | `MarketSnapshot.metrics` (hoy) → TimeSales (pasado) | proxy `dynamic="force-dynamic"` |
| `/api/analitica/descomposicion-retorno` | `snapshot_curva_historico` × 2 | (hereda) | proxy `dynamic="force-dynamic"` |
| `/api/analitica/rolldown-esperado` | `listar_curva` (siempre live) | n/a | proxy `dynamic="force-dynamic"` |
| `/api/analitica/carry-trade` | `Trading.SnapshotsCierre` | `MarketSnapshot.last_price` para hoy | proxy `dynamic="force-dynamic"` |

---

## Pendientes que quedaron

1. **`/checks/curvas-pendientes`** quedó obsoleto (ya marcado en API.md). Como `motor_curvas` ya no enriquece TimeSales, todos los trades nuevos aparecen acá → el check siempre da rojo. Decisión: mantener para auditar histórico previo, o borrar.

2. **`obtener_serie_macro`** con `<TICKER>.<CAMPO>` — sigue agregando TimeSales. Migrar a SnapshotsCierre. Pendiente en eje 4 de la sesión anterior.

3. **`/api/cotizaciones/historico/trades` (LibroPanel)** — proyecta `duration/TEA/TEM/paridad` que para fechas recientes son null. Sacar del project (~30% del JSON).

4. **TimeSales como Time Series Collection** — ahora TimeSales es esencialmente append-only (sólo `engines/valores.py` escribe). Migración a TS Collection esperada con compresión 60-80%. Riesgos auditados (LIBRO no se rompe).

---

## Rollback "nuclear" si todo se rompe

```bash
cd /root/TradingAV
git checkout 1d00d8d                              # HEAD pre-sesión
systemctl restart api.service
# acaquant-web: revertir 764ec54 → push → Vercel redeploya solo
```

Datos no cambiaron de schema en esta sesión. Los scripts nuevos quedan, no rompen nada.
