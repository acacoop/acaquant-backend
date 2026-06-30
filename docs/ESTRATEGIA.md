# ESTRATEGIA — mapa de datos (SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **ESTRATEGIA** (ruta `/retorno` en acaquant-web; en el nav se llama
> "Estrategia"). Qué muestra, de qué tabla SQL sale y relaciones.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable se marca
> `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.
>
> **Nota (decomiso Mongo 2026-06-29):** las fuentes que este doc describía en
> Mongo viven hoy en SQL (Postgres); ej. REM pasó de `Trading.REM` a la tabla
> `rem`. Ver RENTA_FIJA.md / RENTA_VARIABLE.md para el detalle por tabla.

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/retorno` (`RetornoTotalView`) con **5 pestañas**:
**Trade Lab** (day-trading de CEDEARs intradía), **Coberturas** (hedge-finder),
**Comparar Inversión** (bono A vs B), **Análisis Sensibilidad** (soberanos), y
**Descomposición** (atribución de retorno realizado/esperado).

📋 **De dónde sale todo:** ESTRATEGIA es, en gran parte, una vista **"consumidora"**:
**reusa las tablas de RENTA FIJA y de RENTA VARIABLE**. No tiene datos
propios — lo que agrega es **cálculo** (atribución carry/rolldown, sensibilidad
TIR, hedge-finder). Todo sale de SQL.

📋 **Estado SQL (lo importante):** tras el decomiso de Mongo (2026-06-29), la
lectura es 100% SQL (Postgres). No tiene tablas propias: reusa las de RENTA FIJA
y RENTA VARIABLE — ver RENTA_FIJA.md y RENTA_VARIABLE.md para el detalle.

---

## 2. Las 5 pestañas y sus endpoints

| Pestaña | Endpoint(s) backend | Service | ¿Datos propios o reusados? |
|---|---|---|---|
| **Trade Lab** (day-trading CEDEARs) | `GET /api/scanner/day-trading`, `/companeros/{t}`, `/cedears/trades`, `/cedears/intraday`, `/returns/{t}` | `day_trading.py`, `scanner.py` | **Reusa RENTA VARIABLE** |
| **Coberturas** (hedge-finder) | `GET /api/scanner/trade-analysis`, `/cedears` | `rv_motor.py`, `scanner.py` | **Reusa RENTA VARIABLE** |
| **Comparar Inversión** (bono A vs B) | `GET /api/analitica/comparar`, `/comparar/bonos` | `comparar_inversion.py` (+ `renta_fija.listar_curva`) | **Reusa RENTA FIJA** |
| **Análisis Sensibilidad** (soberanos) | `GET /api/analitica/sensibilidad-retorno` | `sensibilidad.py` | Reusa tablas de RF |
| **Descomposición** (realizado / esperado) | `GET /api/analitica/descomposicion-retorno`, `/rolldown-esperado`, `/historico-curva` | `descomposicion_retorno.py` | Reusa tablas de RF |
| **(Retorno total, mini)** | `GET /api/analitica/retorno-total`, `/carry-trade` | `renta_fija.py`, `carry_trade.py` | **Reusa RENTA FIJA** |
| **Operar** (book + boleta, solo si rol `operar`) | `/api/operar/order-book`, `/api/ordenes` | (módulo OPERAR) | Reusa OPERAR |

Endpoints `analitica` verificados en `api/routers/analitica.py`; `scanner` en
`api/routers/scanner.py`.

---

## 3. Tablas SQL que toca (todas reusadas)

| Tabla | Viene de la vista | Detalle de quién la llena |
|---|---|---|
| `curvas`, `market_snapshot`, `snapshots_cierre`, time sales | RENTA FIJA | ver **RENTA_FIJA.md** |
| `series_macro` (CER, DOLAR) | RENTA FIJA | `jobs/bcra.py` |
| **`rem`** | (descomposición esperada) | `jobs/argentina_datos.py` |
| equities (cedears / precios_acciones) | RENTA VARIABLE | ver **RENTA_VARIABLE.md** |
| `valuaciones.dolar` (MEP) | RENTA FIJA | motores dólares |

> **No introduce ninguna tabla nueva.** Es la única vista de MERCADOS
> que se arma 100% sobre datos de otras vistas.

---

## 4. Qué calcula (lo propio de ESTRATEGIA)

Lo que distingue a esta vista no son datos nuevos, sino **cálculo sobre los
existentes** (todo en Python, en el service o en el navegador):

1. **Descomposición de retorno** (`descomposicion_retorno.py`): parte el retorno
   de un bono en **carry + rolldown + cambio de tasa** (+ `cer_accrual` para CER).
   Realizado = entre dos fechas (`snapshots_cierre` + CER de `series_macro`);
   Esperado = proyección a horizonte usando la mediana de inflación de **`rem`**.
2. **Sensibilidad** (`sensibilidad.py`): matriz precio/TIR de soberanos —
   `retorno = (precio_objetivo + carry) / precio_actual − 1`. Solo `curva=soberanos`.
3. **Hedge-finder** (`rv_motor.py`): beta/correlación vs SPY/QQQ sobre
   los precios de acciones (`mercado.precios_acciones`) para sugerir coberturas.
4. **Comparar A vs B** (`comparar_inversion.py`): flujos + TIR + retorno de dos
   bonos lado a lado.

---

## 5. Estado SQL

Tras el decomiso de Mongo (2026-06-29), todos los services de esta vista
(`renta_fija`, `analitica`, `sensibilidad`, `descomposicion_retorno`,
`carry_trade`, `comparar_inversion`, `scanner`, `day_trading`, `rv_motor`) leen
SQL (Postgres). Las tablas que tocan son las de RENTA FIJA y RENTA VARIABLE:
`curvas`, `market_snapshot`, `snapshots_cierre(_hist)`, `series_macro` (CER/DOLAR),
`rem`, y las de equities — ver RENTA_FIJA.md §5 y RENTA_VARIABLE.md.

**Conclusión:** ESTRATEGIA no tiene datos propios que migrar; depende del estado
SQL de las vistas de las que reusa tablas.

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

1. **Estructura exacta de las expectativas de `rem`** que consume
   `rolldown_esperado` (`descomposicion_retorno.py:264-273`) — confirmar el shape
   en prod si se va a tocar esa proyección.

---

## 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/retorno/page.tsx` + `retorno-total-view.tsx`,
  `trade-lab-view.tsx`, `coberturas-view.tsx`, `comparar-inversion-view.tsx`,
  `sensibilidad-table.tsx`, `descomposicion-tab.tsx`, `estrategia-shared.tsx`.
- **Routers:** `api/routers/analitica.py`, `api/routers/scanner.py`.
- **Services:** `renta_fija.py`, `analitica.py`, `sensibilidad.py`,
  `descomposicion_retorno.py`, `carry_trade.py`, `comparar_inversion.py`,
  `scanner.py`, `day_trading.py`, `rv_motor.py`, `rem.py`.
- **Tablas SQL:** ver **RENTA_FIJA.md** y **RENTA_VARIABLE.md** (esta vista
  no agrega ninguna).
