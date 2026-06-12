# ESTRATEGIA — mapa de datos (Mongo + SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **ESTRATEGIA** (ruta `/retorno` en acaquant-web; en el nav se llama
> "Estrategia"). Qué muestra, de qué colección Mongo sale, relaciones y qué hay
> en SQL.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable se marca
> `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.
>
> ⚠️ **Corrección de un dato:** el relevamiento inicial reportó la colección REM
> como `Valuaciones.REM`. **Es falso:** verificado en `api/services/rem.py:20-27`,
> REM vive en **`Trading.REM`** (`get_db_trading()`).

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/retorno` (`RetornoTotalView`) con **5 pestañas**:
**Trade Lab** (day-trading de CEDEARs intradía), **Coberturas** (hedge-finder),
**Comparar Inversión** (bono A vs B), **Análisis Sensibilidad** (soberanos), y
**Descomposición** (atribución de retorno realizado/esperado).

📋 **De dónde sale todo:** ESTRATEGIA es, en gran parte, una vista **"consumidora"**:
**reusa las colecciones de RENTA FIJA y de RENTA VARIABLE**. No tiene colecciones
propias — lo que agrega es **cálculo** (atribución carry/rolldown, sensibilidad
TIR, hedge-finder). Todo en Mongo.

📋 **Estado SQL (lo importante):** **lectura 100% Mongo** — ninguno de sus services
lee Postgres (verificado: `renta_fija`, `analitica`, `sensibilidad`,
`descomposicion_retorno`, `carry_trade`, `comparar_inversion`, `scanner`,
`day_trading`, `rv_motor` → 0 refs SQL). El estado de espejo de sus colecciones
es el mismo que ya está documentado en RENTA_FIJA.md y RENTA_VARIABLE.md.

---

## 2. Las 5 pestañas y sus endpoints

| Pestaña | Endpoint(s) backend | Service | ¿Datos propios o reusados? |
|---|---|---|---|
| **Trade Lab** (day-trading CEDEARs) | `GET /api/scanner/day-trading`, `/companeros/{t}`, `/cedears/trades`, `/cedears/intraday`, `/returns/{t}` | `day_trading.py`, `scanner.py` | **Reusa RENTA VARIABLE** |
| **Coberturas** (hedge-finder) | `GET /api/scanner/trade-analysis`, `/cedears` | `rv_motor.py`, `scanner.py` | **Reusa RENTA VARIABLE** |
| **Comparar Inversión** (bono A vs B) | `GET /api/analitica/comparar`, `/comparar/bonos` | `comparar_inversion.py` (+ `renta_fija.listar_curva`) | **Reusa RENTA FIJA** |
| **Análisis Sensibilidad** (soberanos) | `GET /api/analitica/sensibilidad-retorno` | `sensibilidad.py` | Reusa colecciones de RF |
| **Descomposición** (realizado / esperado) | `GET /api/analitica/descomposicion-retorno`, `/rolldown-esperado`, `/historico-curva` | `descomposicion_retorno.py` | Reusa colecciones de RF |
| **(Retorno total, mini)** | `GET /api/analitica/retorno-total`, `/carry-trade` | `renta_fija.py`, `carry_trade.py` | **Reusa RENTA FIJA** |
| **Operar** (book + boleta, solo si rol `operar`) | `/api/operar/order-book`, `/api/ordenes` | (módulo OPERAR) | Reusa OPERAR |

Endpoints `analitica` verificados en `api/routers/analitica.py`; `scanner` en
`api/routers/scanner.py`.

---

## 3. Colecciones Mongo que toca (todas reusadas)

| Colección | Base | Viene de la vista | Detalle de quién la llena |
|---|---|---|---|
| `Curvas`, `MarketSnapshot`, `SnapshotsCierre`, `TimeSales` | `Trading` | RENTA FIJA | ver **RENTA_FIJA.md** |
| `CER`, `DOLAR` | `Trading` | RENTA FIJA | `jobs/bcra.py` |
| **`REM`** | `Trading` | (descomposición esperada) | `jobs/argentina_datos.py` — **`Trading.REM`** (verificado) |
| `Cedears`, `CedearsSnapshot`, `CedearsTimeSales`, `PreciosAcciones` | `Trading` | RENTA VARIABLE | ver **RENTA_VARIABLE.md** |
| `Dolar` (MEP) | `Valuaciones` | RENTA FIJA | motores dólares |

> **No introduce ninguna colección ni base nueva.** Es la única vista de MERCADOS
> que se arma 100% sobre datos de otras vistas.

---

## 4. Qué calcula (lo propio de ESTRATEGIA)

Lo que distingue a esta vista no son datos nuevos, sino **cálculo sobre los
existentes** (todo en Python, en el service o en el navegador):

1. **Descomposición de retorno** (`descomposicion_retorno.py`): parte el retorno
   de un bono en **carry + rolldown + cambio de tasa** (+ `cer_accrual` para CER).
   Realizado = entre dos fechas (`SnapshotsCierre` + `CER`); Esperado = proyección
   a horizonte usando la mediana de inflación del **`Trading.REM`**.
2. **Sensibilidad** (`sensibilidad.py`): matriz precio/TIR de soberanos —
   `retorno = (precio_objetivo + carry) / precio_actual − 1`. Solo `curva=soberanos`.
3. **Hedge-finder** (`rv_motor.py`): beta/correlación vs SPY/QQQ sobre
   `PreciosAcciones` para sugerir coberturas.
4. **Comparar A vs B** (`comparar_inversion.py`): flujos + TIR + retorno de dos
   bonos lado a lado.

---

## 5. Estado SQL

**Verificado: ningún service de esta vista lee SQL** (`get_pool`/`_sql` = 0 en
`renta_fija`, `analitica`, `sensibilidad`, `descomposicion_retorno`,
`carry_trade`, `comparar_inversion`, `scanner`, `day_trading`, `rv_motor`).

El estado de **espejo de escritura** de sus colecciones es heredado:
- `Curvas`→`curvas`, `MarketSnapshot`→`market_snapshot`, `SnapshotsCierre`→
  `snapshots_cierre(_hist)`, `CER`/`DOLAR`→`series_macro`, `REM`→`rem` (todos vía
  `sync_postgres` batch — ver RENTA_FIJA.md §5).
- `Cedears*` y `PreciosAcciones` → **sin espejo SQL** (ver RENTA_VARIABLE.md).

**Conclusión:** ESTRATEGIA lee 100% de Mongo. No hay nada específico de esta vista
que migrar a SQL (sus datos ya están —o no— según las vistas de las que depende).

---

## 6. Cache (verificado)

| Endpoint | TTL del service |
|---|---|
| `retorno-total` / `carry-trade` / `comparar` | 60s |
| `sensibilidad-retorno` | (front pollea 5 min) |
| `descomposicion-retorno` / `rolldown-esperado` | on-demand |
| `day-trading` | 15s; `companeros`/correlaciones 300s |

---

## 7. ⚠️ Pendiente de verificar / medir en prod

1. **Estructura exacta de `Trading.REM.expectativas`** que consume
   `rolldown_esperado` (`descomposicion_retorno.py:264-273`) — confirmar el shape
   en prod si se va a tocar esa proyección.
2. **Conteos reales** → `python -m scripts.diag_inventario_mongo_sql`.

---

## 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/retorno/page.tsx` + `retorno-total-view.tsx`,
  `trade-lab-view.tsx`, `coberturas-view.tsx`, `comparar-inversion-view.tsx`,
  `sensibilidad-table.tsx`, `descomposicion-tab.tsx`, `estrategia-shared.tsx`.
- **Routers:** `api/routers/analitica.py`, `api/routers/scanner.py`.
- **Services:** `renta_fija.py`, `analitica.py`, `sensibilidad.py`,
  `descomposicion_retorno.py`, `carry_trade.py`, `comparar_inversion.py`,
  `scanner.py`, `day_trading.py`, `rv_motor.py`, `rem.py`.
- **Colecciones/SQL:** ver **RENTA_FIJA.md** y **RENTA_VARIABLE.md** (esta vista
  no agrega ninguna).
