# AUDIT — TradingAV

Auditoría técnica. Material de referencia sobre conceptos, técnicas, servicios y tecnologías implementadas en el código. Estructurado como speech para conversaciones con el equipo de desarrollo e infraestructura.

---

## 1. Visión general

TradingAV es una plataforma **quant de mercados argentinos** (MERVAL / ROFEX) que cubre cuatro funciones independientes:

1. **Streaming de microestructura** — consumo de ticks vía WebSocket pyRofex, derivación de métricas (VWAP, VPIN, imbalance, micro-price), persistencia en MongoDB Atlas.
2. **Motores cuantitativos** — pricing de opciones (Black-Scholes), enriquecimiento de curvas de renta fija (XIRR, Macaulay duration, TEA/TEM, paridad), cálculo de tasas forward y breakevens de inflación.
3. **Pipelines ETL contra Aunesa y BCRA** — carteras, AuM time-series, cash flow, movimientos, variables macro (CER/TAMAR/DOLAR/BADLAR), flujo de contrapartes.
4. **Dashboard Streamlit** — vistas de mercado, opciones, portfolios, operaciones, AuM, Manager (solo admins).

La plataforma corre en un **Droplet de DigitalOcean** (root, venv local) y se administra vía `systemd` + `crontab`. Se expone al mundo a través de **Cloudflare Tunnel** con **Cloudflare Access** (email OTP) al frente en `www.acaquant.com`.

---

## 2. Arquitectura

### 2.1 Layout del repo

```
TradingAV/
├── streamlit_app.py      # entrypoint del dashboard (router + sidebar)
├── config.py             # carga .env (credenciales, MANAGER_EMAILS)
│
├── core/                 # infra compartida (no importa nada interno)
│   ├── mongo.py          # singletons get_mongo_client() / get_mongo_client_read()
│   ├── rofex_session.py  # auth pyRofex única
│   ├── websocket.py      # WebSocketManager (suscripciones + dispatch)
│   └── snapshot_writer.py # writer background con change-detection
│
├── engines/              # motores always-on (WS → Mongo)
│   ├── valores.py        # Trading.TimeSales + MarketSnapshot
│   ├── curvas.py         # enriquecimiento TEA/TEM/Duration/Paridad
│   ├── options.py        # Opciones.OptionsSnapshot (Greeks + IV)
│   ├── forwards.py       # ForwardsLive + ForwardsHistorico
│   ├── breakevens.py     # BreakevensLive + BreakevensHistorico
│   └── dolar_mep.py      # snapshot MEP intradía
│
├── jobs/                 # batch / cron (sin WebSocket)
│   ├── aunesa_client.py  # cliente API Aunesa
│   ├── carteras.py       # posicionValuada → Valuaciones.Carteras
│   ├── aum.py            # snapshot AuM diario + sync CarterasII
│   ├── aum_backfill.py   # reconstrucción histórica (Manager)
│   ├── cashflow.py       # movimientos cash → CashFlow.Movimientos
│   ├── flujo_contrapartes.py  # operaciones del día → CashFlow.Flujo
│   ├── segmento_contrapartes.py  # Fondos/ALYC/Bancos
│   ├── volatilidad_ggal.py # VR histórica GGAL al cierre
│   ├── bcra.py           # CER/TAMAR/DOLAR/BADLAR
│   └── dias_habiles.py   # calendario hábil argentino
│
├── quant/                # cálculo puro
│   └── black_scholes.py  # bs_price/delta/gamma/vega/theta + find_iv
│
├── dashboard/            # todo Streamlit
│   ├── shared/           # auth, db, format, styles
│   └── views/            # mercado, opciones, portfolios, operaciones, aum, manager
│
├── scripts/              # diagnóstico one-shot
├── deploy/               # systemd units + crontab.txt (fuente de verdad)
└── logs/                 # git-ignored
```

**Regla de capas**: `core/` no importa a nadie. `engines/` y `jobs/` importan `core/` + `quant/`. `dashboard/` solo lee (excepto Manager). Todo se invoca como `python -m <módulo>` con `cwd=<raíz>`.

### 2.2 Patrón dominante: event-driven multi-motor

```
ROFEX WebSocket (pyRofex)
        │
        ▼
core.websocket.WebSocketManager   ── suscripción en chunks de 50 tickers
        │
   ┌────┴────┬──────────┬──────────┐
   ▼         ▼          ▼          ▼
engines.  engines.  engines.   engines.
valores   options   curvas    forwards / breakevens
   │         │          │          │
   └────┬────┴──────────┴──────────┘
        ▼
core.snapshot_writer  ── thread background, bulk_write idempotente
        │
        ▼
MongoDB Atlas (Trading / Opciones / Valuaciones / CashFlow)
        │
        ▼
Streamlit dashboard
```

**Por qué importa**: cada motor recibe los mismos ticks pero mantiene estado in-memory propio; no se bloquean entre sí; la persistencia está desacoplada del camino caliente.

---

## 3. Gestión de conexiones

### 3.1 MongoDB — dos singletons con double-checked locking

`core/mongo.py` expone **dos clientes singleton** thread-safe:

- `get_mongo_client()` → `MONGO_URI` (read-write) — motores, crons, Manager.
- `get_mongo_client_read()` → `MONGO_URI_READ` (read-only) — todas las vistas del dashboard. Fallback a `MONGO_URI` si no está definido.

Patrón de creación (double-checked locking):

1. Check rápido sin lock (hot path).
2. Si no hay cliente, adquirir `threading.Lock`.
3. Re-check dentro del lock.
4. Crear cliente con `maxPoolSize=10`, validar con `admin.command('ping')`.

**Nunca llamar `client.close()`** — son singletons de larga vida; cerrarlos rompe el pool compartido con Streamlit y tira `InvalidOperation` en todas las queries subsiguientes.

### 3.2 pyRofex — sesión global

`core/rofex_session.py::inicializar_sesion()` fija credenciales en el módulo pyRofex una sola vez. Todos los motores lo llaman; la segunda llamada es no-op. Eso permite que `engines.valores`, `engines.options`, etc. compartan WebSocket.

### 3.3 Aunesa API — lazy re-auth thread-safe

`jobs/aunesa_client.py` maneja tokens JWT Bearer. Patrón: ante 401, re-autenticar y reintentar **una sola vez**. No hay refresh preventivo — el token es barato de regenerar.

En `jobs/aum.py` la re-auth es thread-safe: headers compartidos entre 8 workers con `threading.Lock`. Cuando un worker recibe 401, adquiere el lock, regenera el token, y los siguientes usan el nuevo.

---

## 4. Concurrencia y threading

### 4.1 Threads daemon

`engines/valores.py` corre tres threads daemon:

- `_worker` — consume de `queue.Queue()` y procesa ticks (productor-consumidor).
- `_flush_loop` — vacía buffer de trades nuevos a Mongo cada N segundos.
- `_snapshot_loop` — escribe `MarketSnapshot` cada 1s con bulk upsert.

**Daemon**: cuando el proceso principal termina, mueren con él. No quedan zombies.

### 4.2 ThreadPoolExecutor acotado

- `jobs/aum.py` — `max_workers=8` para ~300 cuentas Aunesa en paralelo. Acotado a propósito: un pool ilimitado dispara rate-limiting del broker.
- `engines/options.py` — `max_workers=4` para cálculo de Greeks. Mismo principio.

### 4.3 Locks

Solo donde hay estado compartido mutable:

- `headers_lock` en AuM → protege re-autenticación compartida.
- Lock en `core/mongo.py` → protege creación de cada singleton.
- Lock en `core/snapshot_writer.py` → protege buffers in-memory con múltiples escritores.

El 95% del código es lock-free.

### 4.4 Señales — graceful shutdown

`engines/options.py` registra `SIGTERM` y `SIGINT`. Cuando `systemctl stop` manda SIGTERM, el servicio termina el batch en vuelo antes de morir. Previene escrituras a medio hacer.

---

## 5. Estrategia de persistencia

### 5.1 Bulk writes + `ordered=False`

Todos los escritores masivos usan:

```python
collection.bulk_write(ops, ordered=False)
```

- **Bulk** → una sola round-trip por N operaciones.
- **ordered=False** → si una op falla, las siguientes siguen. Importante cuando escribís 300 posiciones y una tiene un problema de datos.

### 5.2 UpdateOne / ReplaceOne upsert idempotente

Patrón repetido:

```python
UpdateOne(
    {"id_cuenta": r["id_cuenta"], "unidad": r["unidad"], "fecha_snapshot": ...},
    {"$set": r},
    upsert=True,
)
```

El mismo snapshot puede correr dos veces sin duplicar. Si el cron se re-ejecuta por falla, no hay daño. **Idempotencia = operabilidad**.

### 5.3 ReplaceOne + delete_many($nor) — atomicidad de snapshot

`jobs/carteras.py`:

1. `ReplaceOne` upsert por cada `(id_cuenta, unidad)` del nuevo snapshot.
2. `delete_many({"$nor": claves_actuales})` para purgar posiciones que ya no existen.

**Por qué no `delete_many + insert_many`**: ese patrón tiene una ventana de pérdida de datos — entre el delete y el insert, el dashboard puede leer la colección vacía. Con upsert + delta-delete, la colección siempre tiene un estado consistente.

### 5.4 Hash-based change detection

`core/snapshot_writer.py` calcula un MD5 del documento serializado. Si el hash no cambió, no escribe. Para un motor que dispara cada 0.5s pero cuyo dato real cambia mucho menos, reduce las escrituras en **órdenes de magnitud**.

### 5.5 Índices compuestos estratégicos

`scripts/crear_indices.py` crea:

| Colección | Índice |
|---|---|
| `Trading.TimeSales` | `(ticker, timestamp)`, `(ticker, duration, timestamp)`, `(ticker, TEA, timestamp)`, `(ticker, TEM, timestamp)`, `(ticker, paridad, timestamp)` |
| `Trading.MarketSnapshot` | `ticker` |
| `Trading.ForwardsHistorico` | `(curva, fecha)` |
| `Trading.BreakevensHistorico` | `fecha` |
| `Trading.CER` | `fecha` |
| `Trading.Curvas` | `curva`, `ticker_corto` |
| `Valuaciones.AuM` | `fecha_snapshot`, `(unidad, fecha_snapshot)`, `(id_cuenta, fecha_snapshot)` |
| `Valuaciones.Carteras` | `(id_cuenta, unidad)` |
| `Valuaciones.Assets` | `unidad`, `(EMISOR, CARTERA)` |
| `CashFlow.Flujo` | `(contraparte, moneda)`, `concertacion`, `boleto` |
| `CashFlow.Movimientos` | `fecha` |

Sin estos índices, el enriquecimiento de curvas haría full-scan y el lag sería inaceptable. Idempotente — se puede correr en cualquier momento.

### 5.6 Modelo time-series

`Valuaciones.AuM` usa clave `(id_cuenta, unidad, fecha_snapshot)`: cada día es un doc distinto, no se pisa historia. Mismo principio en `ForwardsHistorico` (1 por `fecha, curva`) y `BreakevensHistorico` (1 por `fecha`).

---

## 6. Motores de streaming — conceptos cuantitativos

### 6.1 Microstructure (`engines/valores.py`)

- **Lee-Ready classification** — trades clasificados BUY/SELL/MID comparando precio vs mid-quote. Sin esto no hay `buy_money` / `sell_money`.
- **VPIN (Volume-synchronized PIN)** — toxic flow medido sobre buckets de volumen fijo (no tiempo). `VOLUME_BUCKET_SIZE` por ticker porque los bonos no operan con la intensidad de una acción.
- **Micro-price** — `(bid × size_ask + ask × size_bid) / (size_bid + size_ask)`. Es la verdad más cercana al siguiente trade.
- **VWAP intraday** — reseteado al inicio de rueda.
- **Hourly stats (10-17)** — bucketing por hora para ver concentración de volumen.
- **Cold start** — al arrancar, lee REST inicial + último snapshot de Mongo para no arrancar con estado vacío.

### 6.2 Opciones (`engines/options.py` + `quant/black_scholes.py`)

- **Black-Scholes closed-form** — price, delta, gamma, vega, theta para call y put.
- **IV vía Newton-Raphson** — itera 20 veces, corta si `vega < 0.01` (zona flat). Semilla inicial: HV.
- **HV anualizada** — `std(log_returns) × sqrt(260)` (260 ruedas ~ año hábil).
- **Filtrado CFI** — selecciona opciones por código CFI (`OCASPS` = call americana, `OPASPS` = put americana) + underlying + próximo vencimiento.
- **Batch snapshot cada 5s**, no cada tick. Greeks se recalculan solo cuando cambia el underlying.

### 6.3 Curvas (`engines/curvas.py`)

- **XIRR** — `scipy.optimize.newton` con **múltiples seeds** (`0.10`, `0.40`, `-0.20`) para manejar superficies con múltiples mínimos. Si una seed no converge, prueba la siguiente.
- **Macaulay duration** — `Σ(t × PV_cupón) / precio`.
- **TEA / TEM** — conversión efectivas anual/mensual.
- **CER** — settlement = siguiente día hábil; CER de liquidación = **10 días hábiles previos** a settlement (convención CNV).
- **Paridad CER** = `precio / (VN × CER_liq / CER_emisión) × 100`.
- **Orden DESC** — busca docs sin `duration` ordenados por timestamp DESC. Evita que un bono vencido con miles de trades irresolubles bloquee el enriquecimiento de trades nuevos.

### 6.4 Forwards (`engines/forwards.py`)

- `forward(A→B) = ((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`
- Usa **`duration` como proxy de plazo** (no `días_a_vencimiento`): el duration refleja el tiempo efectivo de exposición al flujo.
- **Validación de sanidad**: descarta forwards fuera de `[-0.5, 50]`.
- Matriz triangular superior (N×N, solo `i < j`).

### 6.5 Breakevens (`engines/breakevens.py`)

- Cada Lecap se empareja con el CER de vencimiento más cercano (máx 60 días de diferencia).
- Fórmulas encadenadas:
  - `retorno = (1 + TEM)^(días/30) − 1`
  - `inflación_implícita = (1 + retorno) × (paridad_CER / 100) − 1`
  - `breakeven_mensual = (1 + inflación)^(30/días) − 1`

---

## 7. Pipelines ETL

### 7.1 Días hábiles

`jobs/dias_habiles.py` genera `Trading.DiasHabiles` anual (ejecutar 1×/año). Saltea sábados, domingos y feriados argentinos.

### 7.2 Signos invertidos

Aunesa devuelve depósitos con signo negativo (plata saliendo de cámara hacia cuenta). Se guardan con signo positivo (plata entrando). Se maneja en `jobs/cashflow.py` con un `* -1` explícito.

### 7.3 Retry con backoff

`jobs/aum.py`:

```python
for intento in range(1, 4):
    try: run(); break
    except requests.exceptions.Timeout:
        time.sleep(60)
```

3 intentos × 60s entre cada uno. Maneja timeouts intermitentes de Aunesa sin alertar falsamente.

### 7.4 Deduplicación

- `jobs/flujo_contrapartes.py` — deduplica por `boleto` con `$group` + `delete_many($in)`.
- `jobs/carteras.py` — dedup `Assets` por `unidad`.

### 7.5 Filtros de limpieza

`jobs/aum.py` aplica dos filtros sistemáticos:

1. Elimina registros con "OTC" en `cuenta` o `unidad` (regulación interna).
2. Elimina cash (ARS/USD) con cantidad negativa (overdrafts que no valuamos).

`jobs/carteras.py::filtrar_unidades()` excluye unidades exactas `{ARS, USDL}` y las que contienen `[1] Depósito U$`, `OTC`, `2024`, `2025`, `DLR`.

### 7.6 Reglas de valuación AuM

`_calcular_valuacion()`:

- **Divisor 100** para renta fija (Títulos Públicos, Letras, ONs, Fideicomisos, CPDs) — el precio se publica por cada 100 de VN.
- **+1 al precio** para futuros/forwards/derivados.
- **Directo** (P × Q) para el resto.

### 7.7 Snapshot `CarterasII`

Al final de `jobs/aum.py` y `jobs/aum_backfill.py`, si la fecha es el primer día hábil del mes, se vuelca el snapshot a `Valuaciones.CarterasII`. Alimenta la comparación "mes anterior" del informe ejecutivo de Portfolios.

### 7.8 BCRA

`jobs/bcra.py` consume API BCRA para CER/TAMAR/DOLAR/BADLAR. SSL verificado (`verify=True`). `--today` para cron, sin flag hace backfill desde 2023-01-01.

---

## 8. Dashboard Streamlit

### 8.1 `@st.fragment(run_every=N)`

Vistas que necesitan refresh en vivo usan `@st.fragment(run_every=2|30|3600)`. Re-ejecuta solo el fragment, no la página entera. Sin fragments, cada refresh recargaría sidebar, router, todo.

- Libro (Mercado) — `run_every=2` segundos.
- Vistas time-series (AuM, Portfolios) — `run_every=30` o `run_every=3600` según la volatilidad del dato.

### 8.2 Caching

- `@st.cache_resource(ttl=3600)` — conexiones DB (objetos pesados no serializables).
- `@st.cache_data(ttl=N)` — DataFrames y resultados de consulta (invalida al cambiar parámetros).

### 8.3 Auth + control de acceso

- **Cloudflare Access** pone auth por email OTP al frente. Cuando pasa, inyecta el header HTTP `Cf-Access-Authenticated-User-Email`.
- `dashboard/shared/auth.py::is_manager_allowed()` lee ese header y lo valida contra `MANAGER_EMAILS` (`.env`).
- En local el header no existe → el Manager queda accesible para desarrollo.

### 8.4 Session state + selection

Interacciones "click fila → detalle":

```python
st.dataframe(df, selection_mode="single-row", on_select="rerun")
```

Se lee `st.session_state.<key>.selection.rows`. Reemplaza forms + botones.

### 8.5 Subprocess para jobs manuales

`dashboard/views/manager.py` lanza ETLs con `subprocess.Popen` + archivo temporal de logs. Estado keyed por `dm_<script>_status` en `st.session_state`. Permite disparar un cron manualmente desde la UI sin bloquear el dashboard.

### 8.6 Altair — primitivas usadas

- `mark_arc(innerRadius=N)` — donut charts.
- `mark_rule(strokeDash=[6,3])` — líneas de referencia (VWAP).
- `resolve_scale(y="independent")` — eje Y doble (Flujo vs AuM).
- `zero=False` con `domain=[min-15%, max+15%]` — curvas que no se pegan al borde.
- `sort=` con encoding `:O` — evita ticks duplicados en ejes categóricos.
- `strftime` + string-agg — barras mensuales sin que Altair "infiera" y duplique.
- `mark_text(radius=N, color="white")` — labels dentro del arco (Altair v4).

### 8.7 Vistas

| Vista | Sub-tabs | Descripción |
|---|---|---|
| Mercado | Mercado · Libro · Curvas · Breakevens · Forwards · Retorno Total · Volúmenes | Microstructure, VWAP, volumen intraday, Libro 2s, curvas, breakevens (live/histórico/gráfico/simulador), forwards, retorno total. |
| Opciones | Mercado · Estrategias | Cadena GGAL con SPOT/VR/ADR/Tasa RF + volatility smile. Estrategias: spreads pre-configurados con payoff y costo histórico. |
| Portfolios | Reportes | Informe ejecutivo mensual por cuenta. `Valuaciones.Carteras` (mes actual) + `Valuaciones.CarterasII` (mes anterior). |
| Operaciones | Cash Flow · Contrapartes · Análisis · Flujo vs AuM | Cash Flow (filtros accionistas / cooperativas), contrapartes por segmento, análisis comparativo, flujo dual vs AuM para Fondos. |
| AuM | FCI · Análisis SG · Tasa Fija · CER | Snapshot por fecha, evolución, detalle por sociedad gerente, toggle VN vs valuación. |
| Manager | Diagnóstico · Backfills · Validaciones · Logs · Historial · Setup · Latencia | Solo admins (whitelist). Audit log en `Manager.ChangeLog`. |

---

## 9. Deployment y observabilidad

### 9.1 Servicios always-on (systemd)

- `cloudflared.service` — tunnel Cloudflare.
- `streamlit.service` — dashboard.

### 9.2 Servicios de mercado (L-V, horario de rueda)

Definidos en `deploy/systemd/`:

- `motor_rofex.service` → `python -m engines.valores`
- `motor_options.service` → `python -m engines.options`
- `motor_curvas.service` → `python -m engines.curvas`
- `motor_forwards.service` → `python -m engines.forwards`
- `motor_breakevens.service` → `python -m engines.breakevens`

Todos `Type=simple`, `User=root`, `Restart=always`, `RestartSec=10`. `WorkingDirectory=/root/TradingAV` + `ExecStart=/root/TradingAV/venv/bin/python -m engines.<nombre>`.

### 9.3 Crontab

Fuente de verdad: **`deploy/crontab.txt`**. Aplicar con `crontab /root/TradingAV/deploy/crontab.txt`.

| Horario UTC | Job | Frecuencia |
|---|---|---|
| 13:00 / 20:05 | start/stop motores de mercado | L-V |
| 10:00 / 11:30 / 14:00 / 16:00 | `jobs.carteras` | L-V |
| 14:00 / 19:57 | `engines.dolar_mep` | L-V |
| 20:00 | `jobs.volatilidad_ggal` | L-V |
| 20:00 | `jobs.bcra --today` | diario |
| 23:00 | `jobs.aum` | L-V |
| 02:00 | `jobs.cashflow --today` + `jobs.flujo_contrapartes` | Mar-Sáb |

### 9.4 Logs

Los cron escriben a `/root/TradingAV/logs/*.log` con `>> ... 2>&1`. Los motores systemd escriben a `journalctl`. La vista **Manager → Logs** lee estos archivos en vivo con tail + filtro.

### 9.5 Observabilidad en-app

- **Status panel** con umbrales por colección: si `MarketSnapshot.updated_at` atrasa más de X segundos **durante rueda**, alerta visual. `_es_hora_rueda()` evita falsos positivos fuera de 10:00-17:05 ART.
- **Audit log**: cambios manuales desde Manager se registran en `Manager.ChangeLog` (usuario, timestamp, colección, acción).
- **Latencia**: tab Manager→Latencia benchmarkea en tiempo real todas las queries Mongo del dashboard (ms, docs, ms/doc).

---

## 10. Datos de referencia

- **`Trading.CER` / `TAMAR` / `DOLAR` / `BADLAR`** — series BCRA. Cron diario `jobs.bcra --today`. Upsert por fecha → idempotente.
- **`Trading.DiasHabiles`** — calendario anual precomputado (1×/año con `jobs.dias_habiles`).
- **`Trading.Curvas`** — definición **estática** de renta fija. Carga manual. Flujos de cada bono (amortizaciones + cupones) que el motor de curvas usa para XIRR.

Separación datos referenciales vs transaccionales: no se mezclan, los referenciales cambian sin redeploy.

---

## 11. Patrones de resiliencia

| Patrón | Dónde |
|---|---|
| Retry con backoff | `jobs.aum`, `jobs.cashflow` |
| Re-auth en 401 | `jobs.aunesa_client` (carteras, AuM, flujo, cash flow) |
| Double-checked locking | `core.mongo.get_mongo_client()` |
| Idempotencia | Todos los upserts (cron re-ejecutables) |
| ordered=False | Todos los `bulk_write` |
| Cold start warm-up | `engines.valores` lee REST + Mongo al arranque |
| Graceful shutdown | `engines.options` con SIGTERM handler |
| Chunk-based subscription | `core.websocket` (50 tickers + sleep 0.01s) |
| Restart automático | systemd `Restart=always` |
| Auto-reconnect Mongo | `snapshot_writer` pone `_collection=None` ante fallo |
| Tunnel externo auth-gated | Cloudflare Tunnel + Cloudflare Access (OTP) |

---

## 12. Stack técnico

| Capa | Stack |
|---|---|
| Lenguaje | Python 3 + venv local |
| Ingesta market data | `pyRofex` (WebSocket + REST a ROFEX) |
| Fuente externa carteras | API Aunesa (auth JWT + `posicionValuada`) |
| Fuente externa macro | API BCRA (CER, TAMAR, A3500, BADLAR) |
| Persistencia | MongoDB Atlas (DBs `Trading`, `Opciones`, `Valuaciones`, `CashFlow`) — sin ORM, `pymongo` directo |
| Frontend | `streamlit` ≥ 1.42 + `altair` < 5 |
| Cálculo | `numpy` / `pandas`, Black-Scholes propio, `scipy.optimize.newton` para XIRR e IV |
| HV histórica | `yfinance` |
| Calendario | `holidays` (Argentina) |
| HTTP | `requests` (BCRA + Aunesa) |
| Logs/UI terminal | `rich` |
| Config | `python-dotenv` (`.env` git-ignored) |
| Infra | Droplet DigitalOcean (systemd + cron) |
| Edge | Cloudflare Tunnel (`cloudflared`) + Cloudflare Access (email OTP) |
| Dominio | `www.acaquant.com` |

**No hay**: ORM, microservicios, tests automáticos, CI/CD formal, queue broker externo. Decisiones conscientes para un equipo chico: queries explícitas, deploy por `git pull + systemctl restart`, validación manual + audit log.

---

## 13. Puntos fuertes para destacar

1. **Desacoplamiento del camino caliente** — motores nunca bloquean en I/O; delegan a writers background.
2. **Idempotencia end-to-end** — cualquier cron se puede re-ejecutar sin efectos colaterales.
3. **Hash diffing** — reduce escrituras a Mongo en órdenes de magnitud.
4. **Reconciliación atómica** (ReplaceOne + delete_many($nor)) — estado siempre consistente.
5. **Thread safety explícito** — locks solo donde hace falta; resto lock-free.
6. **Retry + re-auth automáticos** frente a fallas típicas de broker.
7. **Time-series nativo** en AuM / ForwardsHistorico / BreakevensHistorico — reconstrucción de cualquier día.
8. **Separación datos/código** — `Trading.Curvas` cambia sin redeploy.
9. **Observabilidad proactiva** — status panel con umbrales + audit log + latencia en vivo.
10. **Fragments de Streamlit** — evitan full reruns; dashboard fluido con 10+ vistas.
11. **Edge auth** — Cloudflare Access delante del dashboard: no hay acceso directo al Droplet desde Internet; el Manager está protegido por whitelist de emails.

---

## 14. Resumen ejecutivo (elevator)

> TradingAV es una plataforma event-driven multi-motor que consume ticks de ROFEX vía WebSocket, deriva métricas cuantitativas en tiempo real (VWAP, VPIN, Greeks, duración, XIRR, tasas forward, breakevens de inflación), y las persiste en MongoDB Atlas usando bulk writes idempotentes con change-detection por hash. El camino caliente está desacoplado del I/O: los motores nunca bloquean esperando a Mongo. Corre en DigitalOcean orquestada por systemd y crontab, con retry automático y re-auth ante fallas de broker. El dashboard Streamlit lee Mongo directo con fragments + caching, expone controles operativos (ETL, logs, audit, latencia), y está protegido por Cloudflare Tunnel + Access con email OTP más whitelist por rol para vistas administrativas.
