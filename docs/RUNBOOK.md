# RUNBOOK — respuesta a incidentes

Manual de emergencias. Cuando algo se rompe: buscá el síntoma, seguí los pasos.
Todo corre en el Droplet (`/root/TradingAV`, venv en `venv/`). Comandos asumen
`cd /root/TradingAV`.

**Reflejo #0:** ¿qué cambió recién? Un `git pull && systemctl restart api.service`
o un deploy de Vercel suele ser la causa. Mirá el último commit (`git log -1`).

---

## 🔴 La web entera tira 502 / no carga

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

## 🟠 A la mañana los números están viejos / no actualizan

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

## 🟠 Un motor de mercado puntual no actualiza

**Síntoma:** una vista específica (ej. opciones, agro) está congelada; el resto OK.

```
systemctl status motor_<x>.service
journalctl -u motor_<x>.service -n 50
```
**Fix:** `systemctl restart motor_<x>.service`. Los motores corren L-V 13–20 UTC
(cron los prende/apaga). Fuera de ese horario es normal que estén `inactive`.

---

## 🔴 Las órdenes no se actualizan de estado (quedan en PENDING)

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

## 🟠 Un job/cron falló

**Síntoma:** run en rojo en /manager → JOBS (`manager.job_runs`), o número raro
en una vista que alimenta un cron: aum, bcra, negocio_movimientos, etc.

```
# Detalle completo del run:
# en /manager → JOBS, o consultá manager.job_runs por tipo=<x>.
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

## 🟠 Tesorería dice "AUNESA CAÍDO" / error 500 del custodio

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
(`detectar.proveedor_caido`, `AV_AGENT.md` §0.ad): el registro sale de `_login`, así
que la caída queda anotada en segundos con el motivo exacto, sin esperar a que
alguien abra la pantalla.

**Cómo aguanta el código** (`core/aunesa.py`): el login reintenta `LOGIN_INTENTOS`
veces ante 5xx y corte de red; ante 4xx corta en el primer intento (credencial
nuestra); y detrás hay un cortacircuito de `FALLO_TTL_S` para que cada poll de la
vista no vuelva a pagar la tanda entera. El error que sube es `AunesaCaido`, con el
motivo escrito en criollo — es lo que muestra el tooltip.

---

## 🔴 La base de datos (Postgres/Supabase) no responde

**Síntoma:** todo lo que toca DB falla; en logs errores de conexión a Postgres
o el warmup de la API loguea fallo de pool.

**Fix:** revisar el estado de la DB en Supabase (¿proyecto pausado? ¿reglas de
red / IP allowlist? ¿límite de conexiones / pool agotado?) y la `POSTGRES_URI`
del `.env`. Reiniciar la API tras corregir: `systemctl restart api.service`.

---

## 🟠 El MCP connector (Claude Desktop) dejó de andar

**Síntoma:** el Custom Connector no conecta / pide login en loop.

**Causas conocidas** (detalle en memoria `project_mcp_cf_access`):
1. CF Access tapando `/mcp` (debe estar en BYPASS; solo `/oauth/authorize`
   protegido).
2. `TransportSecuritySettings` en `api/mcp/server.py` (allowed_hosts/origins).

---

## Rutina (no es incidente, es prevención)

- **Revisión de accesos (trimestral):** `/manager → USUARIOS`. La tabla muestra
  el último acceso de cada usuario, marca "(nunca entró)" y un badge **INACTIVO**
  para los que no se ven hace >90 días → deshabilitarlos con el toggle ENABLED.
  Ver `docs/SECRETS.md` para rotación de credenciales.
- **Backups:** verificar que la DB (Supabase) tenga backups activos + hacer una restauración
  de prueba periódica (documentar RPO/RTO). *(pendiente de implementar)*
