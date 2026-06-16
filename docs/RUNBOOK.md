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

**Causa típica: motores arrancaron durante la pausa de Atlas.** Atlas M10 se
pausa de madrugada (~04:00–11:20 UTC); si un motor quedó vivo, sirve snapshots
viejos. Los motores deberían arrancar a las 13 UTC (post-resume), pero un start
manual fuera de hora lo rompe.
```
systemctl status motor_curvas.service  # mirá 'Active: since' — ¿arrancó antes de las 11:20 UTC?
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

## 🟠 Un job/cron falló (llegó alerta de Telegram)

**Síntoma:** alerta `🔴 Job <x> → error` en Telegram (o número raro en una vista
que alimenta un cron: aum, bcra, negocio_movimientos, etc.).

```
# Detalle completo del run (la alerta solo trae el resumen):
# en /manager → JOBS, o consultá Manager.JobRuns por tipo=<x>.
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

## 🟠 No llegan las alertas de Telegram

```
venv/bin/python -m scripts.diag_telegram   # dice si el problema es token o chat_id
```
**Fix:** si `getMe` da 401 → token mal en `.env` (regeneralo en @BotFather).
Si `sendMessage` da 400 → chat_id mal. Ver memoria `project_telegram_alertas`.

---

## 🔴 Atlas (MongoDB) no responde

**Síntoma:** todo lo que toca DB falla; en logs "ServerSelectionTimeout" o el
warmup de la API loguea "Mongo pool warmup falló".

**Fix:** revisar el cluster en MongoDB Atlas (¿pausado? ¿IP whitelist? ¿límite de
conexiones?). El resume diario lo dispara `atlas_cluster.sh resume` ~11:20 UTC.
Si quedó pausado, resumir manualmente desde el panel de Atlas.

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
- **Backups:** verificar que Atlas tenga backups activos + hacer una restauración
  de prueba periódica (documentar RPO/RTO). *(pendiente de implementar)*
