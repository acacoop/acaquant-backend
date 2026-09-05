---
paths:
  - "engines/curvas.py"
  - "engines/breakevens.py"
  - "engines/forwards.py"
  - "core/curvas_sql.py"
  - "api/services/renta_fija.py"
  - "api/services/renta_fija_sql.py"
  - "config.py"
---
# mercado.curvas — shape, símbolos y fórmulas (no inferible)

## mercado.curvas — shape de flujos (CRÍTICO, no inferible)

- **CER**: porcentual. `amortizacion_pct` + `cupon_sobre_residual` YA resuelto (NO re-multiplicar por `residual_previo_pct`). `cupon_anual=0` si zero coupon. Requiere `cer_emision`.
- **tasa_fija**: absolutos. `amortizacion` + `interes`. Requiere `flujo_vencimiento`.
- **soberanos** (`tipo='globales'|'bonares'`): mismo shape que CER, `cupon_sobre_residual` ya en USD.

Agregar instrumento: fila en `mercado.curvas` (vía `core/curvas_sql.py`; `data` jsonb = doc completo) + fila en `portafolio.assets` con el MISMO `ticker`. Sin el segundo no aparece en AuM/Portfolios.

**Nombres de columna (renombre 2026-08-15).** En `mercado.curvas` la PK es **`ticker`** (`AL30` — el que joinea con `portafolio.assets.ticker`) e **`instrumento`** es el SÍMBOLO DE MERCADO (`MERV - XMEV - AL30 - 24hs`, lo que se le manda a Primary). Estaban invertidos: la PK se llamaba `ticker_corto` y `ticker` guardaba el símbolo. El eje bono/letra, que ocupaba el nombre `instrumento`, fue **ELIMINADO** (1816 solo lo afirma en 3 de sus 28 curvas → quedó vacío en los 221 bonos; ningún bono cambió de pill al sacarlo). ⚠️ Al borrarlo hubo que desarmar el **paso 1 del bloque `DO`** de `sql/schema.sql`, que renombraba `instrumento → tipo_instrumento` con una condición que la base YA MIGRADA cumple: dejarlo habría hecho que el próximo `apply_schema` renombrara el SÍMBOLO DE MERCADO y la vista se quedaba sin precios en silencio. ⚠️ **El blob `data` NO se renombró**: sus claves siguen siendo `ticker_corto`/`ticker` con el significado VIEJO, y son las que leen ~500 lugares vía `core/curvas_sql.py` (que hace `SELECT data`). Por eso los `SELECT` directos llevan alias (`instrumento AS ticker, ticker AS ticker_corto`): la base quedó correcta sin tocar una línea de lógica.

> ⚠️⚠️ **Y NADIE MANTENÍA IGUALES A LOS DOS (incidente 2026-08-19).** Mientras el blob y la columna dijeran lo mismo no pasaba nada, y por eso durante cuatro días no pasó. Pero **el MOTOR escribe el precio leyendo el BLOB** (`engines/curvas.py` → `instrumento.get("ticker")`) y **la VISTA lo busca por la COLUMNA** (`LEFT JOIN market_snapshot s ON s.ticker = c.instrumento`), así que el día que divergieron el sistema quedó partido en dos mitades que no se hablan: el motor suscribía una pata, la pantalla buscaba la otra y **la fila salía entera en `--` con el precio existiendo**. Ningún detector lo veía porque el AGENTE también lee por el blob. Nada fallaba — simplemente no se encontraban. Medido: **2 de 229** (AO29 y CO32), en los dos la columna tenía la pata correcta (la D, en dólares) y el blob la vieja en pesos. **RESUELTO**: `curvas_sql._ALIAS_DEL_BLOB` suma los dos campos de símbolo al merge donde **la columna gana** (el mismo mecanismo que ya regía para el emisor y los ejes), con ALIAS porque los nombres están cruzados — sin alias, `doc["ticker"]` pasaría a valer `AL30` y los ~500 lugares que lo usan como símbolo de mercado se romperían todos juntos. **Ojo al aplicarlo: el motor arma su universo al arrancar**, así que hasta reiniciarlo (fuera de rueda) esos bonos quedan SIN suscribir — y el agente los canta como `no_suscripto`, que es justo la señal que faltaba. Migrar el blob entero sigue siendo el paso siguiente.

`config.TICKERS_EXTRA_PRECIOS`: tickers que `motor_rofex` suscribe pero `motor_curvas` ignora. Default `['MERV - XMEV - AL30C - 24hs']` para `/api/analitica/canje`.


**Breakevens** (`engines/breakevens.py`, método Buscar Objetivo, cupón cero):
```
retorno_lecap = flujo_vto_lecap / precio_lecap − 1
X = [(1 + retorno_lecap) · (precio_cer · cer_emision) / (vn_cer · cer_actual)]^(1/meses) − 1
```
Match **mismo vto** Lecap↔CER (`MAX_DIFF_DIAS=20`). Anualización con `dias_cer` = vto − 10 hábiles. Filtro `mes_inflacion ≤ último IPC publicado`. Fallback Fisher si faltan datos.

**Forwards**: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Lee última TEA por ticker desde `MarketSnapshot.metrics.TEA` (escrita por `motor_curvas` en cada update). Igual patrón usan `breakevens.py` y los services de portfolio/renta-fija. **No leer TimeSales agregado** — es estrictamente más caro y devuelve el mismo valor que el snapshot live.

**TC Breakeven** (`api/services/renta_fija.py::_tc_breakeven`, sólo tasa fija nativa o CER fijado): `TC_BE = MEP × (flujo_vencimiento / precio_actual)`. Lee `flujo_vencimiento` de `mercado.curvas`, `last_price` del trade más reciente y MEP de `get_ultimo_mep` (live, TTL 5s). Se calcula on-the-fly en `get_renta_fija` y `listar_curva` — no se persiste.

**Enriquecimiento CER**: `motor_curvas` usa CER con settlement T-10 hábiles. Si un bono no opera un día, el último trade puede quedar con CER de ayer.
