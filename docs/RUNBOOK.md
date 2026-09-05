# RUNBOOK — operación, incidentes y observabilidad

> **Un doc.** Absorbió a `OBSERVABILIDAD_ROBUSTEZ.md` el 2026-08-31: durante un
> incidente se abren los dos, y el que mira sólo el RUNBOOK no se entera de qué
> telemetría tiene disponible para diagnosticarlo.


---

# PARTE A — Operación e incidentes

Manual de emergencias. Cuando algo se rompe: buscá el síntoma, seguí los pasos.
Todo corre en el Droplet (`/root/TradingAV`, venv en `venv/`). Comandos asumen
`cd /root/TradingAV`.

**Reflejo #0:** ¿qué cambió recién? Un `git pull && systemctl restart api.service`
o un deploy de Vercel suele ser la causa. Mirá el último commit (`git log -1`).

---

### 🔴 La web entera tira 502 / no carga

**Síntoma:** `trading.acaquant.com` o `api.acaquant.com` devuelven 502; todas
las vistas caídas a la vez.

**Causa #1 (la más común): import error en la API.** Un `from X import Y` roto
en cualquier router/service tumba TODO el proceso uvicorn (REGLA #1).
```
systemctl status api.service          # ¿está en 'failed'?
journalctl -u api.service -n 50        # buscá ImportError/AttributeError/NameError
```
**Fix:** corregir el import en el repo → `git pull` → `systemctl restart api.service`.
Validá ANTES de pushear con: `venv/bin/python -c "from api.main import app"`.

**Causa #2: la API arranca pero falla el boot por auth.** Si pusiste `ENV=prod`
sin `API_KEY`, `_validar_postura_auth()` aborta a propósito (EXT-AUTH1).
```
journalctl -u api.service -n 20        # buscá "EXT-AUTH1: ENV=prod pero API_KEY vacía"
```
**Fix:** setear `API_KEY` en `.env` (o `/etc/...` unit) y reiniciar.

**Causa #3: Vercel/CF.** Si la API directa anda (`curl` interno OK) pero la web
no, es el frontend (Vercel) o Cloudflare. Mirá el último deploy en Vercel.

---

### 🟠 A la mañana los números están viejos / no actualizan

**Síntoma:** curvas, AuM o snapshots muestran datos del día anterior aunque el
mercado está abierto.

**Causa típica: un motor quedó vivo fuera de hora y sirve snapshots viejos.**
Los motores deberían arrancar a las 13 UTC (los prende/apaga el cron L-V); un
start manual fuera de hora o un motor que no se reinició deja datos stale.
```
systemctl status motor_curvas.service  # mirá 'Active: since' — ¿arrancó cuándo?
systemctl status motor_rofex.service
```
**Fix:** reiniciar los motores afectados → `systemctl restart motor_<x>.service`.
Lista de motores: rofex, curvas, options, forwards, breakevens, caucion,
futuros_dlr, dolares, cedears, agro, agro_opciones, portfolio_snapshot.

---

### 🟠 Un motor de mercado puntual no actualiza

**Síntoma:** una vista específica (ej. opciones, agro) está congelada; el resto OK.

```
systemctl status motor_<x>.service
journalctl -u motor_<x>.service -n 50
```
**Fix:** `systemctl restart motor_<x>.service`. Los motores corren L-V 13–20 UTC
(cron los prende/apaga). Fuera de ese horario es normal que estén `inactive`.

---

### 🔴 Las órdenes no se actualizan de estado (quedan en PENDING)

**Síntoma:** mandás una orden, el broker la toma, pero en la UI no pasa a
FILLED/REJECTED.

**Causa: `motor_ordenes` caído.** Es el servicio que escucha los execution
reports y persiste `OrdenesLive`. Corre L-V 13:30–20:05 UTC.
```
systemctl status motor_ordenes.service
journalctl -u motor_ordenes.service -n 50
```
**Fix:** `systemctl restart motor_ordenes.service`. Al arrancar hace recovery
de las órdenes PENDING. **Ojo:** el ENVÍO de órdenes lo hace la API (sesión
propia), así que esto afecta el seguimiento, no el envío.

---

### 🟠 Un job/cron falló

**Síntoma:** run en rojo en /manager → JOBS (`manager.job_runs`), o número raro
en una vista que alimenta un cron: aum, bcra, negocio_movimientos, etc.

```
## Detalle completo del run:
## en /manager → JOBS, o consultá manager.job_runs por tipo=<x>.
tail -100 logs/<x>.log                  # ej. logs/aum.log
```
**Fix:** corregir la causa y re-correr a mano:
```
venv/bin/python -m jobs.<x>             # ej. jobs.portafolio_backfill --diario, jobs.bcra --today
```
Jobs que mueven plata / críticos: `portafolio_backfill --diario` (11 UTC, tenencias SQL),
`bcra --today` (22 UTC), `negocio_movimientos` (cada 30 min 14–22 UTC), `cashflow` (02 UTC),
`argentina_datos` (12 UTC).

---

### 🟠 Tesorería dice "AUNESA CAÍDO" / error 500 del custodio

**Síntoma:** Back Office → Tesorería muestra el punto en ROJO con **AUNESA
CAÍDO**, y el tooltip trae algo como `500 Server Error … /Irmo/api/login`. Los
ingresos, los egresos y el saldo final quedan incompletos; todo lo cargado a mano
(saldos, cheques, mercados, banco a banco, registros, VEPs) sigue estando.

**Lo primero: ese 500 NO es nuestro.** Lo devuelve `POST /login` de Aunesa, o sea
el servidor del custodio. Nuestra API contesta 200 y degrada a propósito
(`aunesa_ok: false`) — si nuestra API estuviera rota la vista diría "sin conexión"
o tiraría 502, que es otro cartel. Ya pasó dos veces: 2026-08-07/09 y 2026-08-20.

**Paso 1 — medir de quién es** (read-only, no escribe nada, no imprime la clave):
```
python -m scripts.diag_aunesa
```
Da un veredicto de cuatro salidas:
- **es de ELLOS** (5xx en todos los intentos) → no hay nada que tocar acá. Avisarle
  al custodio y esperar; la vista se recupera sola.
- **INTERMITENTE** (algunos entran) → `core/aunesa.py` ya reintenta; se recupera solo.
- **es NUESTRO / credenciales** (400/401/403, o falta una `AUNESA_*` en el `.env`) →
  pedir la credencial nueva y actualizar el `.env`. **No reintentar en loop:
  bloquean la cuenta.**
- **Aunesa responde BIEN** y la vista igual dice caído → es el cortacircuito del
  proceso (dura 45s). Esperá un refresh; si sigue, `systemctl restart api.service`.

**Paso 2 — qué más se ve afectado.** Aunesa es el custodio: si su login está abajo,
también fallan `jobs.negocio_movimientos`, `jobs.operaciones_informes`,
`jobs.tenencia_live`, `jobs.control_saldos`, `jobs.portafolio_backfill` y
`jobs.tesoreria_echeq_recibidos`. Mirar `manager.job_runs` / el AV AGENT antes de
diagnosticar cada uno por separado: es UNA sola causa. El agente ya lo canta solo
(`detectar.proveedor_caido`, `AGENT.md` §0.ad): el registro sale de `_login`, así
que la caída queda anotada en segundos con el motivo exacto, sin esperar a que
alguien abra la pantalla.

**Cómo aguanta el código** (`core/aunesa.py`): el login reintenta `LOGIN_INTENTOS`
veces ante 5xx y corte de red; ante 4xx corta en el primer intento (credencial
nuestra); y detrás hay un cortacircuito de `FALLO_TTL_S` para que cada poll de la
vista no vuelva a pagar la tanda entera. El error que sube es `AunesaCaido`, con el
motivo escrito en criollo — es lo que muestra el tooltip.

---

### 🔴 La base de datos (Postgres/Supabase) no responde

**Síntoma:** todo lo que toca DB falla; en logs errores de conexión a Postgres
o el warmup de la API loguea fallo de pool.

**Fix:** revisar el estado de la DB en Supabase (¿proyecto pausado? ¿reglas de
red / IP allowlist? ¿límite de conexiones / pool agotado?) y la `POSTGRES_URI`
del `.env`. Reiniciar la API tras corregir: `systemctl restart api.service`.

---

### 🟠 Di de baja un motor/cron y el servidor lo sigue corriendo

**El síntoma es un proceso que falla en silencio.** `git pull` borra archivos del
repo; **no apaga un proceso ni edita el crontab vivo**. Las units de systemd se
COPIAN a `/etc/systemd/system/` (no son symlinks al repo), y el crontab real es un
archivo del sistema, no `deploy/crontab.txt` — ese es la *fuente de verdad*, no el
que corre.

Y si el mismo deploy corrió `apply_schema` con un `DROP` adentro, el proceso que
quedó vivo escribe contra tablas que ya no existen: **falla cada ciclo y nadie se
entera**, porque la pieza de diagnóstico que lo vigilaba se borró en el mismo
commit.

Baja completa = **tres pasos, y el `git pull` es solo el primero**:

1. `git pull` + `bash deploy/deploy.sh` (código y schema).
2. `systemctl stop` + `disable` + borrar la unit de `/etc/systemd/system/` +
   `daemon-reload`.
3. Aplicar `deploy/crontab.txt` al crontab vivo (**con backup** — `crontab archivo`
   reemplaza el crontab entero: si alguien editó a mano en el servidor y eso nunca
   volvió al repo, se pierde. Por eso primero se mira el `diff`).

Caso resuelto — **ESTRATEGIA QUANT (2026-09-01)**: `bash deploy/baja_estrategia.sh`
hace los pasos 2 y 3 (idempotente; el cron solo con `--con-cron`, tras ver el diff).
Sirve de plantilla para la próxima baja, junto con `deploy/install_tenencia_live.sh`
que es el mismo patrón al revés.

---

### Rutina (no es incidente, es prevención)

- **Revisión de accesos (trimestral):** `/manager → USUARIOS`. La tabla muestra
  el último acceso de cada usuario, marca "(nunca entró)" y un badge **INACTIVO**
  para los que no se ven hace >90 días → deshabilitarlos con el toggle ENABLED.
  Ver `docs/SECURITY.md` parte B para rotación de credenciales.
- **Backups:** verificar que la DB (Supabase) tenga backups activos + hacer una restauración
  de prueba periódica (documentar RPO/RTO). *(pendiente de implementar)*

---

# PARTE B — Telemetría, guardrails y robustez

> **DOC VIVO** de las 3 features de observabilidad/robustez pedidas el 2026-07-18
> (3 commits separados, en orden). Cada avance se asienta acá en el mismo commit.
> Reemplaza como "documento de sesión" al viejo `MAR_14_JULIO_00_28_AM.md`
> (borrado en esta tanda — era temporal y auto-destruible por diseño).

Las tres piezas atacan la misma pregunta desde tres lados: **¿podemos confiar en
lo que la plataforma muestra y saber cómo se usa?**

1. **Telemetría de uso** — saber QUIÉN usa QUÉ (decidir producto con datos).
2. **Guardrails de datos** — detectar un número PODRIDO antes de que alguien
   decida con él (vigila que el número esté bien, no solo que el motor esté
   vivo).
3. **Golden tests** — red de regresión sobre el camino que produce los números
   (ingesta/normalización/AuM), hoy sin cobertura.

---

### COMMIT 1 — Telemetría de uso por módulo ❌ DECOMISADA (2026-08-04)

> **Reemplazada por la telemetría de LATENCIA por endpoint** (pedido del user:
> la tab USO nunca se usó; lo operativamente útil es saber QUÉ endpoint está
> lento y su tendencia). Piezas nuevas: `manager.latencia_endpoints` (schema),
> `api/telemetria.py` (reescrito: acumula n/total_ms/max_ms/lentas/errores por
> endpoint normalizado × hora, mismo patrón de flush best-effort), middleware
> en `api/main.py` (mide `perf_counter` alrededor de cada request),
> `api/services/latencia_endpoints.py` y `GET /api/manager/latencia`.
> `manager.uso_modulos` se DROPea vía apply_schema.

> **El diseño original (36 líneas: el contador `manager.uso_modulos`, su
> middleware, `api/services/uso_modulos.py`, `api/routers/manager/uso.py` y la
> pill USO) se borró de acá el 2026-08-31.** Ninguna de esas piezas existe —
> verificado contra el repo— y describir en presente un panel decomisado hace
> que alguien lo busque. Está en git. Lo que corre hoy es la telemetría de
> LATENCIA de arriba: `manager.latencia_endpoints` + `GET /api/manager/latencia`.

---

### COMMIT 2 — Guardrails de datos → BORRADO (2026-09-02)

`jobs/guardrails.py` existió del 2026-07-18 al 2026-09-02. Sus umbrales nacieron
en `None` («sin calibrar») y nunca se calibraron: corría todas las noches y no
podía marcar una sola violación. Se borró entero (job, test, config, cron).
Decisión: `docs/AGENT.md` §0.dj.

### COMMIT 3 — Golden tests del pipeline crítico ✅ HECHO (2026-07-18)

**Qué es:** `tests/unit/test_golden_pipeline.py` (8 tests) + fixture anonimizado
`tests/unit/fixtures/golden_operaciones.json`. Los outputs esperados se
CONGELARON corriendo las funciones REALES (characterization) — un cambio futuro
que altere el resultado rompe a propósito. Cobertura:
1. **Ingesta de operaciones** (`operaciones_informes`): `normalizar_fila`
   (headers con alias/acentos, montos es-AR, cuenta numérica→string, fechas
   DD/MM e ISO), `_aplicar_enrich` (commodity con la excepción agro-OTC del
   2026-06-03, es_cierre materializado, mep estampado, mercado/operacion/
   segmento/nivel_3 con maps inyectados) y `es_otc_excluido` (NDF/Opciones sí,
   Concurrencia no).
2. **Negocio** (`negocio_movimientos`): `_boleto_a_doc` completo (con el mep
   snapshot inmutable) + `_extract_id_cuenta`.
3. **AuM** (`_aum_filters.is_excluded`): las 5 reglas de exclusión (USDL,
   OTC/CDC, contraparte por id, contraparte por nombre, ARS de cuentas propias)
   con sets inyectados — cero SQL.
4. **Valuación por CARTERA** (`pnl._aplicar_normalizer` — la fórmula no
   inferible): ÷100 para HD/DL/ARS, ×1 para MONEDAS/FCI, (precio+1)×cant para
   futuros, fallback.

NO hizo falta refactor: los núcleos ya eran puros/inyectables (maps, mep_cache,
sets de contrapartes van por parámetro).

#### ⚠ HALLAZGO de la characterization (marcado, NO tapado — decide el user)

**`_to_float("1.500")` → 1.5.** El parser trata un punto ÚNICO como decimal,
pero en el Excel es-AR histórico "1.500" casi siempre es MIL QUINIENTOS. Es una
ambigüedad intrínseca (una cantidad "1.500" ≠ un precio "1.500") y NO se tocó la
lógica de producción (regla de oro). Mitigantes: la API informes manda números
JSON (no strings) → el camino diario probablemente no lo pisa; el riesgo vive en
cargas históricas por Excel. **Decisión pendiente del user:** (a) dejarlo así
(documentado + golden congelado), o (b) cambiar la heurística (ej. un punto con
exactamente 3 dígitos detrás y sin coma = miles) sabiendo que rompe el golden y
obliga a revisar lo ya ingestado con ese patrón.

---

### Qué corre el user en el Droplet (checklist de activación)

```
git pull
python -m scripts.apply_schema        # crea manager.latencia_endpoints
systemctl restart api.service          # activa el middleware de telemetría
```
- El panel **Manager → OBSERVABILIDAD → LATENCIA** muestra datos tras ~1 min de uso
  (la tab USO se decomisó — ver arriba).
- **Decidir el hallazgo `_to_float("1.500")`** (ver commit 3).

### Registro (con fecha)

- **2026-08-03 — Auditoría de observabilidad de jobs** (`scripts/audit_jobs_observabilidad.py`,
  estática, sin DB ni deps; corre en CI como informativa). Nace del caso
  `jobs.economic_calendar`: falló desde el día 1 sin dejar rastro (no usaba
  JobRunLogger) y el Diagnóstico lo "vigilaba" contra una colección Mongo
  decomisada. Cruza crontab × JobRunLogger × `diagnostico_registry`.
  Hallazgos corregidos en el mismo commit:
  - **8 piezas apuntaban a Mongo** (`db`/`coll` sin `tabla`) → daban `sin_datos`
    permanente. Re-apuntadas a SQL o a `run_tipo`: market_quotes, market_anchors,
    motor_ordenes, motor_rofex (trades), bcra, fair_value, pnl_totales_precompute,
    consolidado_cuentas.
  - **4 piezas ignoraban el JobRunLogger que el job ya emitía** → el árbol se caía
    al frescor de la tabla y un run en ERROR con datos viejos se veía sano. Ojo con
    `jobs.portafolio_backfill`, que registra con `tipo="aum"` (legado).
  - **`diagnostico.py::_leer_frescura` ignoraba `ts_kind`/`assume` en el path SQL**
    (hardcodeaba UTC) → ahora usa `_parse_ts`. Sin eso, el tape (`mercado.timesales`,
    `ts` naive en ART) se leía 3h corrido y se veía crítico siempre.
  - **Pendiente [A]: 16 crons sin JobRunLogger** → si fallan, silencio total.
    Correr el script para la lista actualizada.

- **2026-08-03 — Tanda [A]: rastro para los 5 jobs diarios ciegos.** Criterio: un job
  de alta frecuencia (`market_quotes`, cada minuto, umbral 10') ya queda cubierto por
  la frescura de su tabla; uno **diario con umbral de 3 días** puede fallar el lunes y
  descubrirse el jueves. Se le puso `JobRunLogger` + `run_tipo` a `jobs.fair_value`,
  `jobs.cierre_canje`, `jobs.forwards_zscore`, `jobs.precios_acciones_daily` y
  `engines.dolar_mep` (que además se tragaba TODA excepción en un `except: print`).
  Convención: los modos manuales (`--dry`, `--ticker`, `--backfill`) NO registran, para
  no ensuciar `manager.job_runs` — mismo criterio que `portafolio_backfill`.
  Fallas parciales (una curva/ticker) → `jr.error()` → status `partial`, que NO es
  alerta: queda registrado sin pintar el panel de rojo. Solo un crash da `error`.
  Quedan 10 en [A]: 6 de limpieza/infra (si no corren, no rompen nada) y 4 de alta
  frecuencia ya cubiertos por frescura de tabla.

- **2026-07-18 — Los 3 commits construidos y verdes** (344 tests totales, ruff/
  typecheck/import-chain OK; vault regenerado). Borrado `MAR_14_JULIO_00_28_AM.md`
  (temporal, auto-destruible, ya cumplió). Hallazgo del golden (`_to_float`)
  pendiente de decisión del user.

## Pendiente fuera del repo — Cloudflare Access

Sacar la app `acaquant-mcp-bypass` del panel de **Cloudflare Access**. El MCP
server se borró el 2026-08-28, pero esa app sigue dejando 5 paths (`/mcp`,
`/oauth/register`, `/oauth/token` y compañía) sin login —hoy contra 404s— y
ocupa **5/5 destinations**, la cuota entera, por una superficie que ya no existe.
