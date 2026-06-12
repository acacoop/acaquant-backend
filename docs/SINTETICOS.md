# SINTÉTICOS — mapa de datos (Mongo + SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **SINTÉTICOS** (`/sinteticos` en acaquant-web): tasa implícita (TNA/TE) de
> sintéticos armados con LECAP+Rofex y DLK+Rofex. Qué muestra, de qué colección
> Mongo sale, relaciones y qué hay en SQL.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable se marca
> `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/sinteticos` (`DerivadosSinteticosView`): dos tablas y dos
gráficos de **tasa sintética** —
**(a) Long Rofex − Long LECAP** y **(b) Short Rofex − Long DLK**— con su TE/TNA y
la curva de TNA por plazo.

📋 **De dónde sale todo:** **un solo endpoint** (`GET /api/derivados/sinteticos`)
que **calcula en vivo** combinando 3 colecciones de `Trading` + el dólar oficial.
No hay datos "sintéticos" crudos: el sintético es un **cálculo** sobre futuros DLR
+ bonos + precios vivos.

📋 **Estado SQL (lo importante):** **100% afuera de SQL.** El service `sinteticos.py`
no lee Postgres (0 refs), y la colección de su histórico (`SnapshotsSinteticos`)
**no existe en `sql/schema.sql`** (0 apariciones). Las colecciones de entrada
(`Curvas`, `MarketSnapshot`) sí tienen espejo (heredado de RENTA FIJA), pero
`FuturosDLRSnapshot` y el resultado sintético **no**.

---

## 2. Endpoint y bloques

> **Router:** `api/routers/derivados_sinteticos.py` (`GET /api/derivados/sinteticos`).
> El front pega vía proxy `/api/derivados-sinteticos`. **Poll: 5s.** Acceso
> público (todos los roles).

| Bloque (en pantalla) | Qué muestra | Endpoint |
|---|---|---|
| **Header** | Spot + fuente + último update | `GET /api/derivados/sinteticos` |
| **Tabla Long Rofex − Long LECAP** | ticker, futuro, px, vto, plazo, descalce, TE, TNA | (mismo) |
| **Tabla Short Rofex − Long DLK** | ticker DLK, futuro, px, DLR ajuste, vtos, TE, TNA | (mismo) |
| **Curva TNA (Long LECAP)** | TNA % vs plazo (días), `descalce==0` | (mismo) |
| **Curva TNA (Short DLK)** | TNA % vs plazo | (mismo) |

**Toda la vista se sirve de UN endpoint.** Service: `sinteticos.py::get_sinteticos`
(verificado, `api/services/sinteticos.py:82-182`, cache 5s).

---

## 3. Colecciones Mongo — quién las lee y quién las llena (verificado)

`get_sinteticos` lee de `get_mongo_client_read()["Trading"]`:

| Colección | Base | Qué aporta al cálculo | La llena (verificado) |
|---|---|---|---|
| **FuturosDLRSnapshot** | `Trading` | Precio vivo de los futuros DLR (la pata Rofex) | **`engines/futuros_dlr.py`** |
| **Curvas** (`curva="tasa_fija"`) | `Trading` | LECAPs: `flujo_vencimiento`, vto (la pata LECAP) | maestro editable |
| **Curvas** (`curva="dolar_linked"`) | `Trading` | DLK: `dolar_emision`, vto (la pata DLK) | maestro editable |
| **MarketSnapshot** | `Trading` | `metrics.last_price` de cada LECAP/DLK | motores rofex + curvas |
| *(spot)* | — | Dólar oficial vía `mid_oficial_live("oficial")` (MAE mayorista) | script local MAE |

**Histórico:** `jobs/snapshot_sinteticos.py` (cron **20:40 UTC** L-V) llama a
`get_sinteticos()` y persiste el resultado en **`Trading.SnapshotsSinteticos`**
(1 doc por ts/tipo/ticker) para la serie temporal de TNA/TE.

---

## 4. Relaciones / lógica del cálculo (verificado)

El sintético **empareja por (año, mes) de vencimiento** la pata de futuro DLR con
la pata de bono (LECAP o DLK), y calcula la tasa con el spot oficial:

- **Long Rofex − Long LECAP:** `TE = (cobro/px_futuro) / (px_lecap/spot) − 1`;
  `TNA = TE × 365/plazo`.
- **Short Rofex − Long DLK:** `TE = (100×px_futuro/dolar_emision) / px_dlk − 1`;
  `TNA = TE × 365/plazo`.

Las filas con **descalce ≠ 0** (vencimientos que no matchean) se muestran en
tabla pero se **excluyen de la curva TNA**.

> **Dependencia clave:** el sintético no existe sin las 3 fuentes vivas. Si el
> motor `futuros_dlr` o el `MarketSnapshot` están stale, la tasa sale vieja.

---

## 5. Estado SQL

**Verificado:**
- `api/services/sinteticos.py` lee SQL: **0** (no `get_pool`, no `_sql`).
- `SnapshotsSinteticos` en `sql/schema.sql`: **0** apariciones ❌.
- `FuturosDLRSnapshot` (la pata Rofex viva): **0** en schema ❌ (su histórico
  `FuturosDLR` sí va a `mercado_hist` — ver AGRO.md / RENTA_FIJA.md).
- `Curvas` → `curvas` y `MarketSnapshot` → `market_snapshot` (sí, heredado).

**Conclusión:** la vista SINTÉTICOS se calcula 100% sobre Mongo y **su resultado
(vivo e histórico) no tiene ningún espejo en SQL**. Para llevarla a SQL habría que
crear tabla para `SnapshotsSinteticos` (y para el snapshot vivo de futuros DLR) —
hoy no existen.

---

## 6. Cache

`get_sinteticos` → `@cached(ttl=5)` (misma cadencia que los motores). Front poll 5s.

---

## 7. ⚠️ Pendiente de verificar / medir en prod

1. **Poblamiento de `Trading.Curvas`** (de dónde entran LECAPs/DLKs): es un maestro
   editable; el engine/ingest exacto no se confirmó línea a línea. No afecta el
   mapa de la vista. ⚠️ a verificar.
2. **Conteos reales** (¿`SnapshotsSinteticos` tiene historia?, ¿cuántos futuros DLR
   vivos?) → `python -m scripts.diag_inventario_mongo_sql`.

---

## 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/sinteticos/page.tsx` +
  `derivados-sinteticos-view.tsx`.
- **Router:** `api/routers/derivados_sinteticos.py`.
- **Service:** `api/services/sinteticos.py`.
- **Motores/jobs:** `engines/futuros_dlr.py` (futuros DLR vivos),
  `engines/valores.py` + `engines/curvas.py` (MarketSnapshot),
  `jobs/snapshot_sinteticos.py` (histórico `Trading.SnapshotsSinteticos`).
- **SQL:** ninguna tabla. (Verificado: `SnapshotsSinteticos` y `FuturosDLRSnapshot`
  = 0 en `sql/schema.sql`.)
