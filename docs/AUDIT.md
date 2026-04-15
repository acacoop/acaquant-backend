# AUDIT.md — TradingAV

Documento de auditoría técnica. Material de referencia para revisar conceptos, técnicas y prácticas implementadas en el código. Estructurado como *speech* para conversaciones con el equipo de desarrollo e infraestructura.

---

## 1. Visión general

TradingAV es una plataforma **quant de mercados argentinos** (MERVAL / ROFEX) que cubre cuatro funciones independientes:

1. **Streaming de microestructura** — consumo de ticks vía WebSocket pyRofex, derivación de métricas (VWAP, VPIN, imbalance, micro-price), persistencia en MongoDB Atlas.
2. **Motores cuantitativos** — pricing de opciones (Black-Scholes), enriquecimiento de curvas de renta fija (XIRR, Macaulay duration, TEA/TEM, paridad), cálculo de tasas forward y breakevens de inflación.
3. **Pipelines ETL contra Aunesa y BCRA** — carteras, AuM time-series, cash flow, movimientos, variables macro (CER/TAMAR/DOLAR/BADLAR).
4. **Dashboard Streamlit** — vistas de mercado, opciones, portfolios, operaciones, AuM. Mismo proceso Python, lectura directa de Mongo.

La plataforma corre en un **Droplet de Digital Ocean** (root, venv local) y se administra vía `systemd` + `crontab`.

---

## 2. Arquitectura general

### 2.1 Patrón dominante: *event-driven* multi-motor

```
ROFEX WebSocket (pyRofex)
        │
        ▼
WebSocketManager  ── subscripción en chunks de 50 tickers
        │
   ┌────┴────┬──────────┬──────────┬──────────┐
   ▼         ▼          ▼          ▼          ▼
TimeSales  FX      Options     Microstr.   Valores
 engine    engine   engine     engine       engine
   │         │          │          │          │
   └────┬────┴──────────┴──────────┴──────────┘
        ▼
SnapshotWriter  ── thread background, bulk_write cada ~0.5s
        │
        ▼
MongoDB Atlas (Trading / Opciones / Valuaciones / CashFlow)
        │
        ▼
Streamlit dashboard
```

**Por qué importa contarlo así:**
- Cada motor recibe los mismos ticks pero mantiene su propio estado in-memory.
- Los motores **no se bloquean entre sí**: la fan-out se hace al momento de la suscripción.
- La persistencia está **desacoplada** del camino caliente — el motor escribe a un buffer, un writer separado empuja a Mongo.

### 2.2 Separación de responsabilidades

| Componente | Responsabilidad única |
|---|---|
| `session_manager.py` | Autenticación pyRofex (LIVE env) |
| `websocket_manager.py` | Subscripción y dispatch de market data a handlers |
| `mongo_manager.py` | Cliente singleton + reconexión |
| `snapshot_writer.py` | Escritura bulk con detección de cambios |
| `main_*.py` | Lógica de negocio (un motor = una preocupación) |
| `views/*.py` | Presentación (Streamlit) |
| `Opciones/`, `live_pricing_bonds/`, `arbitraje_fx/` | Primitivas cuantitativas reutilizables |

Cada archivo tiene **un rol acotado**. Los motores de streaming no saben de Mongo — le pasan datos al writer. El writer no sabe de pyRofex. Esto es lo que permite matar un motor sin afectar al resto.

---

## 3. Gestión de conexiones

### 3.1 MongoDB — Singleton con *double-checked locking*

`mongo_manager.get_mongo_client()` implementa el patrón **double-checked locking singleton**:

1. Check rápido sin lock (caso hot path).
2. Si no hay cliente, adquirir `threading.Lock`.
3. Re-check dentro del lock (porque otro thread pudo haberlo creado entre el step 1 y el 2).
4. Crear cliente con `maxPoolSize=10`, verificar con `admin.command('ping')`.

**Por qué importa:**
- Evita la estampida de conexiones cuando arrancan múltiples motores en paralelo.
- `ping` fuerza que el driver valide la conexión antes de devolverla al caller (no sirve un `MongoClient` que todavía no resolvió DNS).
- El reuse del pool reduce latencia de cada query.

### 3.2 pyRofex — sesión global

`session_manager.inicializar_sesion()` fija credenciales en el módulo pyRofex una sola vez. Todos los motores lo llaman; la segunda llamada es no-op. Esto es lo que permite que `main_valores`, `main_options`, `main_fx`, etc., compartan WebSocket.

### 3.3 Aunesa API — *lazy re-auth*

`Excel/aunesa_api_manager.py` maneja tokens JWT Bearer. Patrón: si una llamada devuelve 401, re-autenticar y reintentar **una sola vez**. No hay refresh preventivo — el token es desechable y barato de regenerar.

En `Excel/main_aum.py` esta re-auth es **thread-safe**: headers compartidos entre 8 workers con `threading.Lock`. Cuando un worker recibe 401, adquiere el lock, regenera el token, y los workers siguientes usan el nuevo.

---

## 4. Concurrencia y threading

### 4.1 Threads daemon

`main_valores.py` corre **tres threads daemon**:

- `_worker` — consume de `queue.Queue()` y procesa ticks (productor-consumidor).
- `_flush_loop` — vacía el buffer de trades nuevos a Mongo cada N segundos.
- `_snapshot_loop` — escribe `MarketSnapshot` cada 1s con bulk upsert.

**Daemon threads:** cuando el proceso principal termina, los threads mueren con él. No quedan zombies.

### 4.2 ThreadPoolExecutor acotado

`Excel/main_aum.py` usa `ThreadPoolExecutor(max_workers=8)` para consultar ~300 cuentas Aunesa en paralelo. **Acotado a propósito** — un pool ilimitado rompería al broker y dispararía rate limiting.

`main_options_service.py` usa `max_workers=4` para cálculo de Greeks. Mismo principio.

### 4.3 Locks

- `headers_lock` en AuM → protege re-autenticación compartida.
- `threading.Lock` en `mongo_manager` → protege la creación del singleton.
- `threading.Lock` en writers → protege buffers in-memory que reciben escrituras desde varios threads.

**Principio:** locks solo donde hay estado compartido mutable. El 95% del código no los necesita.

### 4.4 Señales (graceful shutdown)

`main_options_service.py` registra `SIGTERM` y `SIGINT`. Cuando `systemctl stop` manda SIGTERM, el servicio tiene tiempo de terminar el batch en vuelo antes de morir. Esto es lo que previene escrituras a medio hacer.

---

## 5. Estrategia de persistencia

### 5.1 Bulk writes + `ordered=False`

Todos los escritores masivos usan:

```python
collection.bulk_write(ops, ordered=False)
```

- **Bulk** → una sola round-trip al servidor por N operaciones (amortiza latencia de red).
- **ordered=False** → si una operación falla, las siguientes **siguen ejecutándose**. Importante cuando escribís un snapshot de 300 posiciones y una tiene un problema de datos: el resto entra igual.

### 5.2 `UpdateOne` / `ReplaceOne` upsert idempotente

Patrón repetido en toda la codebase:

```python
UpdateOne(
    {"id_cuenta": r["id_cuenta"], "unidad": r["unidad"], "fecha_snapshot": ...},
    {"$set": r},
    upsert=True
)
```

**Por qué es importante:**
- Podés correr el mismo snapshot dos veces sin duplicar.
- Si el cron se reejecuta por falla, no hay daño.
- Idempotencia = operabilidad.

### 5.3 `ReplaceOne` + `delete_many($nor)` — atomicidad de snapshot

`Excel/main_carteras.py` hace:

1. `ReplaceOne` upsert por cada `(id_cuenta, unidad)` del nuevo snapshot.
2. `delete_many({"$nor": claves_actuales})` para purgar posiciones que ya no existen.

**Por qué no un `delete_many + insert_many`:** ese patrón tiene una **ventana de pérdida de datos** — entre el delete y el insert, el dashboard puede leer la colección vacía. Con upsert + delta-delete, la colección **siempre** tiene un estado consistente.

### 5.4 Hash-based change detection

`snapshot_writer.py` calcula un **MD5 hash** del documento serializado. Si el hash no cambió, no escribe. Para un motor que dispara cada 0.5s pero cuyo dato real cambia mucho menos, esto reduce **órdenes de magnitud** la cantidad de escrituras.

### 5.5 Índices compuestos

`crear_indices.py` crea índices estratégicos sobre `TimeSales`:

- `(ticker, timestamp)` — la base; cubre queries del dashboard.
- `(ticker, duration, timestamp)` — acelera `main_curvas.py` cuando busca docs sin `duration`.
- `(ticker, TEA, timestamp)` — queries del motor de forwards.
- `(ticker, TEM, timestamp)` y `(ticker, paridad, timestamp)` — motor de breakevens.

**Concepto clave:** el orden de los campos en el índice importa. El primer campo es el que filtra más; los siguientes sirven para ordenar o filtrar residual.

### 5.6 Modelo time-series

`Valuaciones.AuM` usa clave compuesta **`(id_cuenta, unidad, fecha_snapshot)`**. Cada día es un doc distinto. No se pisa historia, se agrega. Mismo principio en `ForwardsHistorico` (1 por `fecha, curva`) y `BreakevensHistorico` (1 por `fecha`).

---

## 6. Motores de streaming — conceptos cuantitativos

### 6.1 Microstructure (`main_valores.py`)

- **Lee-Ready classification** — clasifica trades como BUY/SELL/MID comparando precio de ejecución vs mid-quote. Sin esto no tenés `buy_money` / `sell_money`.
- **VPIN (Volume-synchronized PIN)** — probabilidad de toxic flow medida sobre buckets de volumen fijo (no tiempo). Cada ticker tiene su `VOLUME_BUCKET_SIZE` porque los bonos no operan con la misma intensidad que una acción.
- **Micro-price** — precio ponderado por imbalance de book: `(bid × size_ask + ask × size_bid) / (size_bid + size_ask)`. Es la "verdad" más cercana al siguiente trade.
- **VWAP intraday** — volume-weighted average price reseteado al inicio de rueda.
- **Hourly stats** (10-17) — bucketing por hora para ver dónde se concentra el volumen.
- **Cold start** — al arrancar, el motor lee un REST inicial + el último snapshot de Mongo para no arrancar con estado vacío.

### 6.2 Opciones (`main_options_service.py`, `Opciones/calculos_cuantitativos.py`)

- **Black-Scholes closed-form** — precio, delta, gamma, vega, theta para call y put.
- **Implied volatility vía Newton-Raphson** — itera 20 veces, corta si `vega < 0.01` (zona flat). Semilla inicial: HV.
- **HV anualizada** — `std(log_returns) × sqrt(260)` (260 ruedas ~= año hábil).
- **Filtrado CFI** — selecciona opciones por código CFI (`OCASPS` = call americana, `OPASPS` = put americana) + underlying + próximo vencimiento. Esto es lo que garantiza que no se meten opciones raras.
- **Batch snapshot** cada 5s — no cada tick. Greeks se recalculan solo cuando cambia el underlying.

### 6.3 Curvas (`main_curvas.py`)

- **XIRR** — tasa interna de retorno con flujos en fechas irregulares, vía `scipy.optimize.newton` con **múltiples seeds** (`0.10`, `0.40`, `-0.20`) para manejar superficies con múltiples mínimos locales. Si una seed no converge, prueba la siguiente.
- **Macaulay duration** — `Σ(t × PV_cupón) / precio`. Es la medida de sensibilidad del bono a cambios en tasa.
- **TEA / TEM** — conversión entre tasas efectivas anuales y mensuales.
- **CER** — settlement = siguiente día hábil; CER de liquidación = **10 días hábiles previos** a la fecha de settlement (esto es una convención CNV, no elección del motor).
- **Paridad CER** = `precio / (VN × CER_liq / CER_emisión) × 100`.
- **Orden de proceso DESC** — busca docs sin `duration` ordenados por timestamp descendente. Esto evita que un bono vencido con miles de trades irresolubles bloquee el enriquecimiento de trades nuevos.

### 6.4 Forwards (`main_forwards.py`)

- Fórmula: `forward(A→B) = ((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B - t_A)) - 1`
- **Usa `duration` como proxy de plazo** (no `días_a_vencimiento`), porque el duration refleja el *tiempo efectivo* de exposición al flujo.
- **Validación de sanidad** — descarta forwards fuera de `[-0.5, 50]` (tasas efectivas absurdas).
- Matriz triangular superior (N×N, solo i<j).

### 6.5 Breakevens (`main_breakevens.py`)

- **Emparejamiento Lecap ↔ CER** — cada Lecap se empareja con el CER de vencimiento más cercano (máx 20 días de diferencia).
- Fórmulas encadenadas:
  - `retorno = (1 + TEM)^(días/30) - 1` — lo que paga el Lecap al vencimiento.
  - `inflación_implícita = (1 + retorno) × (paridad_CER / 100) - 1` — lo que el mercado *espera* que la inflación comera al comprar CER a esa paridad.
  - `breakeven_mensual = (1 + inflación)^(30/días) - 1` — inflación mensual implícita.

---

## 7. Pipelines ETL

### 7.1 Días hábiles (T+2)

`holidays.Argentina()` + función `proximo_habil()` que salta sábados, domingos y feriados. Usada en AuM (T+2 desde hoy para query Aunesa) y cash flow.

### 7.2 Signos invertidos

El API de Aunesa devuelve depósitos con signo **negativo** (es plata "saliendo" de la cámara hacia la cuenta). Nosotros lo guardamos con signo **positivo** (plata entrando). Esto se maneja en `main_cashflow.py` con un `* -1` explícito y está documentado. Lo mismo pasa con `cantidad` en AuM (se invierte porque Aunesa publica desde el lado contrario).

### 7.3 Retry con backoff

`Excel/main_aum.py`:

```python
for intento in range(1, 4):
    try: run(); break
    except requests.exceptions.Timeout:
        time.sleep(60)
```

3 intentos, 60 segundos entre cada uno. Maneja timeouts intermitentes de Aunesa sin alertar falsamente.

### 7.4 Deduplicación

- `main_flujo_contrapartes.py` — deduplica por `boleto` con `$group` + `delete_many($in)`.
- `main_carteras.py` — deduplica `Assets` por `unidad`.

Ambos casos: mantener el primer doc, borrar el resto.

### 7.5 Filtros de limpieza

`main_aum.py` aplica dos filtros sistemáticos:

1. Elimina cualquier registro con "OTC" en `cuenta` o `unidad` (lo ignoramos por regulación interna).
2. Elimina cash (ARS/USD) con cantidad negativa (overdrafts que no queremos valuar).

### 7.6 Reglas de valuación AuM

`_calcular_valuacion()` aplica divisores según tipo:

- **Divisor 100** para renta fija (Títulos Públicos, Letras, ONs, Fideicomisos, CPDs). El precio se publica "por cada 100 de VN".
- **+1 al precio** para futuros/forwards/derivados (ajuste técnico).
- **Directo** (precio × cantidad) para el resto.

---

## 8. Dashboard Streamlit

### 8.1 `@st.fragment(run_every=N)`

Vistas que necesitan refresh en vivo usan `@st.fragment(run_every=2)` o `run_every=30`. Esto re-ejecuta **solo el fragment**, no la página entera. Sin fragments, cada refresh recargaría la sidebar, el router, todo. Con fragments, el Libro actualiza cada 2s y el resto queda estático.

### 8.2 Caching

- `@st.cache_resource(ttl=3600)` — para conexiones DB (una por worker).
- `@st.cache_data(ttl=300)` — para queries de datos (5 min de TTL, invalida al cambiar parámetros).

**Principio:** `cache_resource` para objetos pesados que NO se serializan (conexiones, clientes). `cache_data` para DataFrames y resultados de consulta.

### 8.3 Session state + selection

Interacciones tipo "clickear en tabla → mostrar detalle" usan:

```python
st.dataframe(df, selection_mode="single-row", on_select="rerun")
```

Luego leés `st.session_state.<key>.selection.rows` para saber qué fila se eligió. Esto reemplaza completamente a tener una UI con forms y botones.

### 8.4 Subprocess para jobs manuales

`views/data_manager.py` lanza scripts ETL con `subprocess.Popen` + archivo temporal de logs. El estado del job vive en `st.session_state` keyed por `dm_<script>_status`. Permite disparar un cron manual desde la UI sin bloquear el dashboard.

### 8.5 Altair — primitivas usadas

- `mark_arc(innerRadius=N)` — pie charts tipo donut.
- `mark_rule(strokeDash=[6,3])` — líneas de referencia (VWAP).
- `resolve_scale(y="independent")` — eje Y doble para gráficos como "Flujo vs AuM".
- `zero=False` con `domain=[min-15%, max+15%]` — para que la curva no se pegue al borde.
- `sort=` con encoding `:O` — para evitar que Altair duplique ticks en ejes categóricos.
- `strftime` + string-agg — para barras mensuales sin que Altair "infiera" y duplique.

---

## 9. Deployment y observabilidad

### 9.1 Systemd

- `motor_rofex.service` — WebSocket principal (main_ts/valores).
- `motor_options.service` — pricing de opciones headless.
- `motor_curvas.service` — enriquecimiento TEA/Duration.
- `motor_forwards.service` — matriz de forwards.
- `motor_breakevens.service` — breakevens.
- `streamlit.service` — dashboard.

Todos `Type=simple`, `User=root`, `Restart=always`, `RestartSec=10`. Si un motor crashea, systemd lo levanta 10s después.

### 9.2 Crontab

Controla el ciclo diario: a las **13:00 UTC (10 ART)** arrancan motores, a las **20:05 UTC (17:05 ART)** se apagan. Los cron ETL (AuM al cierre, CashFlow 02:00 UTC martes a sábado, BCRA 20:00 UTC todos los días) corren con `/root/TradingAV/venv/bin/python` — venv local fijo.

### 9.3 Logs

Todos los cron escriben a `/root/TradingAV/logs/*.log` con `>> ... 2>&1`. Los motores systemd escriben a `journalctl`. La vista **Manager → Logs** lee estos archivos en vivo con tail + filtro.

### 9.4 Observabilidad en-app

**Status panel** con umbrales por colección: si `MarketSnapshot.updated_at` atrasa más de X segundos durante rueda, alerta visual. `_es_hora_rueda()` detecta si estamos en L-V 10:00-17:05 ART — fuera de ese horario no alerta (es normal que no haya flujo).

**Audit log** — cambios manuales desde Manager se registran en `Manager.ChangeLog` con usuario, timestamp, colección, acción. Trazabilidad completa.

---

## 10. Datos de referencia

- **`Trading.CER` / `TAMAR` / `DOLAR` / `BADLAR`** — series BCRA. Cron diario `data_bcra.py --today`. Upsert por fecha → idempotente.
- **`Trading.DiasHabiles`** — calendario anual precomputado. Se regenera una vez al año con `data_diashabiles.py`.
- **`Trading.Curvas`** — definición **estática** de instrumentos de renta fija. Se carga manualmente. Contiene flujos de cada bono (amortizaciones + cupones) que el motor de curvas usa para XIRR.

**Separación de referencia vs transaccional.** Las variables macro y el calendario son datos referenciales; los trades, snapshots y valuaciones son transaccionales. No se mezclan.

---

## 11. Patrones de resiliencia

| Patrón | Dónde |
|---|---|
| Retry con backoff | AuM, movimientos cash flow |
| Re-auth en 401 | Aunesa (carteras, AuM, flujo, cash flow) |
| Double-checked locking | `get_mongo_client()` |
| Idempotencia | Todos los upserts (cron reejecutables) |
| ordered=False | Todos los `bulk_write` |
| Cold start warm-up | `main_valores.py` lee REST + Mongo al arranque |
| Graceful shutdown | `main_options_service.py` con SIGTERM handler |
| Chunk-based subscription | `websocket_manager.py` (50 tickers + sleep 0.01s) |
| Restart automático | systemd `Restart=always` |
| Auto-reconnect Mongo | `snapshot_writer` pone `_collection=None` ante fallo |

---

## 12. Stack técnico

- **Python 3** + `venv` local.
- **pyRofex** — WebSocket y REST a ROFEX.
- **pymongo** — MongoDB Atlas (sin ORM, queries directas).
- **streamlit ≥ 1.42** — dashboard.
- **altair < 5** — visualización.
- **scipy** — `optimize.newton` para XIRR e IV.
- **numpy / pandas** — dataframes y vectorización.
- **holidays** — calendario argentino.
- **requests** — HTTP a BCRA y Aunesa.
- **yfinance** — HV histórica para Greeks.
- **python-dotenv** — credenciales en `.env` (no en repo).

---

## 13. Índices MongoDB creados (`crear_indices.py`)

```
TimeSales:
  (ticker, timestamp)                 — base
  (ticker, duration, timestamp)       — curvas
  (ticker, TEA, timestamp)            — forwards
  (ticker, TEM, timestamp)            — breakevens
  (ticker, paridad, timestamp)        — breakevens
ForwardsHistorico:   (curva, fecha)
BreakevensHistorico: (fecha)
MarketSnapshot:      (ticker) UNIQUE
AuM:                 (fecha_snapshot)
CashFlow.Movimientos: (comprobante) UNIQUE
```

Todos los motores se apoyan en estos índices. Sin ellos, las queries de enriquecimiento serían full scan y el lag del motor de curvas sería inaceptable.

---

## 14. Puntos fuertes para destacar

1. **Desacoplamiento del camino caliente.** Motores nunca bloquean en I/O de Mongo — delegan a writers background.
2. **Idempotencia end-to-end.** Cualquier cron se puede reejecutar sin efectos colaterales.
3. **Hash diffing.** Reduce órdenes de magnitud las escrituras a Mongo.
4. **Reconciliación atómica** (ReplaceOne + delete_many($nor)) — el estado siempre es consistente.
5. **Thread safety explícito.** Locks solo donde hace falta; resto es lock-free.
6. **Retry + re-auth** automáticos frente a fallas típicas de broker.
7. **Time-series nativo** en AuM, ForwardsHistorico, BreakevensHistorico — permite reconstruir cualquier día.
8. **Separación datos/código.** `Trading.Curvas` cambia sin redeploy.
9. **Observabilidad proactiva.** Status panel con umbrales por colección + audit log de cambios manuales.
10. **Fragments de Streamlit** evitan full reruns → dashboard fluido incluso con 10+ vistas.

---

## 15. Hallazgos conocidos (documentados transparentemente en `CLAUDE.md`)

Auditoría realizada 2026-04-11. **Están documentados explícitamente** — no son sorpresas, son decisiones con contexto.

| ID | Severidad | Descripción breve |
|----|-----------|---|
| C-1 | CRÍTICO | `verify=False` en requests BCRA → riesgo MITM |
| C-2 | CRÍTICO | Credenciales Aunesa como env vars a subprocesos |
| C-3 | CRÍTICO | Sin validación path traversal en scripts disparados desde UI |
| A-1 | ALTO | Dashboard sin autenticación (hardening pausado — discutiendo Cloudflare Tunnel + Access vs Tailscale) |
| A-2 | ALTO | `os.system()` en motores FX / ON |
| A-3 | ALTO | `except: pass` silenciosos en ON / data_manager |
| A-4 | ALTO | Faltan `socketTimeoutMS` y `connectTimeoutMS` en MongoClient |
| A-5 | ALTO | Race condition en reconexión Mongo (ping fuera del lock) |
| M-1..M-6 | MEDIO | Detalles menores (credenciales en session_state, archivos temporales sin limpieza, XSS con `unsafe_allow_html`, etc.) |
| B-1, B-2 | BAJO | `.env` en disco (correcto en .gitignore), JWT sin TTL verificado |

**Lectura del equipo:** la plataforma funciona en producción y genera data transaccional real. Los hallazgos son priorizados y la decisión explícita fue priorizar estabilidad funcional sobre hardening. Todo está auditado, nada es desconocido.

---

## 16. Lo que NO hay (y por qué no es un problema)

- **No hay ORM.** Queries pymongo directas → performance predecible, sin sorpresas de lazy loading.
- **No hay microservicios.** Todo es un monolito con múltiples procesos systemd. Más simple de operar para un equipo chico.
- **No hay tests automáticos.** Validación manual contra sheets espejo en Google Sheets y control visual del dashboard. Trade-off consciente de velocidad de desarrollo.
- **No hay CI/CD formal.** Deploy = `git pull` + `systemctl restart` en el droplet. Es suficiente para una persona operando.
- **No hay queue broker** (Kafka/Redis). `queue.Queue` in-process alcanza porque los consumers viven en el mismo proceso que el productor.

---

## 17. Resumen ejecutivo (elevator)

> *TradingAV es una plataforma event-driven multi-motor que consume ticks de ROFEX vía WebSocket, deriva métricas cuantitativas en tiempo real (VWAP, VPIN, Greeks, duración, XIRR, tasas forward, breakevens de inflación), y las persiste en MongoDB Atlas usando bulk writes idempotentes con detección de cambios por hash. Todo el camino caliente está desacoplado del I/O: los motores nunca bloquean esperando a Mongo. La plataforma corre en Digital Ocean orquestada por systemd y crontab, con retry automático y re-auth ante fallas de broker. El dashboard Streamlit lee Mongo directo con fragments + caching para performance, y expone controles operativos (disparo de ETL, logs, audit trail) en vivo. Los hallazgos de seguridad están documentados transparentemente y priorizados — la decisión explícita fue estabilizar funcionalidad antes de hardening.*

---
