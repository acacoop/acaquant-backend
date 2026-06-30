# SINTÉTICOS — mapa de datos (SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **SINTÉTICOS** (`/sinteticos` en acaquant-web): tasa implícita (TNA/TE) de
> sintéticos armados con LECAP+Rofex y DLK+Rofex. Qué muestra, de qué tabla
> SQL sale, relaciones y cómo se persiste.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable se marca
> `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.
> Actualizado tras el **decomiso total de Mongo (2026-06-29)**: todo lee/escribe
> SQL-native (Postgres/Supabase).

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/sinteticos` (`DerivadosSinteticosView`): dos tablas y dos
gráficos de **tasa sintética** —
**(a) Long Rofex − Long LECAP** y **(b) Short Rofex − Long DLK**— con su TE/TNA y
la curva de TNA por plazo.

📋 **De dónde sale todo:** **un solo endpoint** (`GET /api/derivados/sinteticos`)
que **calcula en vivo** combinando 3 tablas SQL del schema `mercado` + el dólar
oficial. No hay datos "sintéticos" crudos: el sintético es un **cálculo** sobre
futuros DLR + bonos + precios vivos.

📋 **Estado SQL (lo importante):** **SQL-native (migración COMPLETA).** El service
`sinteticos.py` lee SQL (conexión `core.postgres.get_pool()`, vía los helpers
`*_sql.py` / `core`). Las tablas de entrada son `mercado.curvas` y
`mercado.market_snapshot` (heredadas de RENTA FIJA) más `mercado.futuros_dlr_snapshot`
(la pata Rofex viva). El histórico se persiste en `mercado.snapshots_sinteticos`
vía `core.pg_mirror`.

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

## 3. Tablas SQL — quién las lee y quién las llena (verificado)

`get_sinteticos` lee del schema `mercado` (conexión `core.postgres.get_pool()`):

| Tabla SQL | Schema | Qué aporta al cálculo | La llena (verificado) |
|---|---|---|---|
| **futuros_dlr_snapshot** | `mercado` | Precio vivo de los futuros DLR (la pata Rofex) | **`engines/futuros_dlr.py`** |
| **curvas** (`curva="tasa_fija"`) | `mercado` | LECAPs: `flujo_vencimiento`, vto (la pata LECAP) | maestro editable |
| **curvas** (`curva="dolar_linked"`) | `mercado` | DLK: `dolar_emision`, vto (la pata DLK) | maestro editable |
| **market_snapshot** | `mercado` | `metrics.last_price` de cada LECAP/DLK | motores rofex + curvas |
| *(spot)* | — | Dólar oficial vía `mid_oficial_live("oficial")` (MAE mayorista) | script local MAE |

**Histórico:** `jobs/snapshot_sinteticos.py` (cron **20:40 UTC** L-V) llama a
`get_sinteticos()` y persiste el resultado en **`mercado.snapshots_sinteticos`**
(1 fila por ts/tipo/ticker) vía `core.pg_mirror`, para la serie temporal de TNA/TE.

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
> motor `futuros_dlr` o el que escribe `market_snapshot` están stale (motor caído),
> la tasa sale vieja.

---

## 5. Estado SQL

**Verificado (migración COMPLETA, decomiso Mongo 2026-06-29):**
- `api/services/sinteticos.py` lee SQL vía `core.postgres.get_pool()` (helpers
  `*_sql.py` / `core`).
- Entrada viva: `mercado.curvas`, `mercado.market_snapshot` (heredadas de RENTA
  FIJA) y `mercado.futuros_dlr_snapshot` (pata Rofex). El histórico de cierre de
  futuros DLR vive en `mercado.futuros_dlr_snapshot`.
- Histórico del sintético: `mercado.snapshots_sinteticos`, escrito vía
  `core.pg_mirror`.

**Conclusión:** la vista SINTÉTICOS se calcula 100% SQL-native; su resultado
(vivo e histórico) está espejado en SQL. No queda nada atado a Mongo.

---

## 6. Cache

`get_sinteticos` → `@cached(ttl=5)` (misma cadencia que los motores). Front poll 5s.

---

## 7. ⚠️ Pendiente de verificar / medir en prod

1. **Poblamiento de `mercado.curvas`** (de dónde entran LECAPs/DLKs): es un maestro
   editable; el engine/ingest exacto no se confirmó línea a línea. No afecta el
   mapa de la vista. ⚠️ a verificar.
2. **Conteos reales** (¿`mercado.snapshots_sinteticos` tiene historia?, ¿cuántos
   futuros DLR vivos?) → consultar SQL.

---

## 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/sinteticos/page.tsx` +
  `derivados-sinteticos-view.tsx`.
- **Router:** `api/routers/derivados_sinteticos.py`.
- **Service:** `api/services/sinteticos.py`.
- **Motores/jobs:** `engines/futuros_dlr.py` (futuros DLR vivos →
  `mercado.futuros_dlr_snapshot`), `engines/valores.py` + `engines/curvas.py`
  (`mercado.market_snapshot`), `jobs/snapshot_sinteticos.py` (histórico
  `mercado.snapshots_sinteticos` vía `core.pg_mirror`).
- **SQL:** `mercado.curvas`, `mercado.market_snapshot`,
  `mercado.futuros_dlr_snapshot`, `mercado.snapshots_sinteticos`.
