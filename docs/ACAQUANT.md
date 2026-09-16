# ACAQUANT — cómo funciona todo

**Última actualización:** 2026-09-16 19:32 (hora Buenos Aires) · huella `36371a32`
> **Este es EL documento oficial de AcaQuant.** Tiene la posta de cómo está armado y
> cómo funciona el sistema. Si algo de acá contradice al código, es un bug de uno de
> los dos y se arregla en el mismo commit. Es **privado**: no sale de la empresa.
>
> Las tablas marcadas ⚙️ se **generan solas** desde lo que corre de verdad
> (`deploy/systemd/`, `deploy/crontab.txt`, `sql/schema.sql`) con
> `python -m scripts.gen_sistema`. La línea de arriba se reescribe sola en cada
> regeneración, y el CI falla si el doc se editó a mano sin regenerar. Lo escrito a
> mano es corto a propósito: si no entra en una pantalla, va al manual del dominio (§11).
>
> ⚠️ **No hay credenciales acá, nunca.** Dónde vive cada secreto lo sabe el `.env`
> del servidor y nadie más.

---

## 1. Qué es

AcaQuant es la plataforma de la mesa de ACA Valores. Lee el mercado argentino en tiempo
real (bonos, acciones, opciones, dólar, futuros, FCI), guarda todo en una base, y lo
muestra en una terminal web. Sobre esos datos también lleva las operaciones, las
tenencias de los clientes, el back-office y el tablero comercial.

Son dos portales sobre el mismo deploy:

| Portal | Quién | Qué ve |
|---|---|---|
| `trading.acaquant.com` | la mesa | todo, según el rol de cada persona |
| `www.acaquant.com` | invitados de otro sector de ACA | solo mercado y research, en lectura. **Nada del negocio de la mesa** |

El código está en dos repos: `acaquant-backend` (Python) y `acaquant-frontend` (Next.js).
La carpeta del servidor se llama `/root/TradingAV` por razones históricas: es el mismo sistema.

## 2. Dónde corre cada cosa

| Pieza | Dónde | Qué hace |
|---|---|---|
| **Frontend** (`acaquant-web`) | Vercel | La terminal web. No tiene lógica ni base propia: todo lo pide al backend. Deploya solo al pushear a `main` |
| **Backend** (API) | Un servidor (Droplet) en DigitalOcean, Nueva York | FastAPI en el puerto 8000, detrás de nginx. Contesta todo lo que la web muestra |
| **Motores y jobs** | El mismo servidor | Procesos que leen el mercado en vivo (motores) y tareas programadas (jobs). Los maneja systemd y cron |
| **Base de datos** | Supabase (Postgres administrado) | La única base. Si se cae, se cae todo |
| **Identidad y acceso** | Cloudflare Access | Decide quién puede entrar a cada portal, antes de llegar a Vercel |
| **Feed del dólar MAE** | Una PC de la oficina | `mae_forex.py`, **manual**: alguien lo prende. Si nadie lo prende, el dólar oficial queda viejo |

## 3. Cómo se conecta todo

```
   mae_forex.py (PC oficina)      pyRofex (broker ROFEX/MAE)
   ⚠️ MANUAL ────┐                 │ market data (WS)      ▲ envío / cancelación de órdenes
   (dólar MAE)   │                 ▼                       │
                 │   ┌──── motores de mercado ────┐        │
                 │   │ rofex · curvas · options … │        │   motor_ordenes escucha los
                 ▼   │  (L-V en rueda → SQL)      │        │   reportes y guarda el estado
             Postgres / Supabase ◄── jobs (cron) ── Aunesa · BCRA · 1816 · FRED · bancos …
                 ▲   ▲                                     │
            lee  │   └─────────────────────────────────────┘
        api.service :8000 ── nginx
                 ▲  HTTPS
        acaquant-web (Vercel) ◄── Cloudflare Access ◄── la mesa / el invitado
```

En una frase: **el mercado entra por los motores, el resto entra por los jobs, todo cae
en Postgres, la API lo lee y la web lo dibuja.** Nada se calcula en el frontend.

## 4. Qué corre y cuándo ⚙️

Horarios en **UTC** (Buenos Aires = UTC−3). La rueda es L-V, 13:20 a 20:05 UTC.

**Siempre prendidos** (los reinicia systemd si se caen):
<!-- AUTOGEN:servicios -->
| Servicio | Puerto | Módulo | Qué hace |
|---|---|---|---|
| `agente` | — | `jobs.agente` | AV Agent — el agente de AcaQuant |
| `api` | 8000 | `api.main:app` (uvicorn) | AcaQuant API (FastAPI + uvicorn) |
<!-- /AUTOGEN:servicios -->

**Motores y procesos de rueda** (cron los prende y los apaga; fuera de horario es normal verlos `inactive`):
<!-- AUTOGEN:motores -->
| Servicio | Horario (UTC) | Módulo | Qué hace |
|---|---|---|---|
| `control_saldos` | 11:00–21:05 L-V | `jobs.control_saldos` | Control de Saldos - saldo LIQUIDADO del dia por cuenta y moneda |
| `motor_agro` | 13:20–20:05 L-V | `engines.motor_agro` | Motor Futuros Agro - Trigo/Maiz/Soja Rosario (FXXXSX) |
| `motor_agro_opciones` | 13:20–20:05 L-V | `engines.motor_agro_opciones` | Motor Opciones Agro - Trigo/Maiz/Soja Rosario (OCAFXS/OPAFXS) |
| `motor_breakevens` | 13:20–20:05 L-V | `engines.breakevens` | Motor Breakevens - Inflacion implicita CER/Lecap en tiempo real |
| `motor_caucion` | 13:20–20:05 L-V | `engines.caucion` | Motor Caucion - TNA caucion ARS y USD a corto plazo (1D, viernes 3D) |
| `motor_cedears` | 13:20–20:05 L-V | `engines.motor_cedears` | Motor CEDEARs - AcaQuant |
| `motor_curvas` | 13:20–20:05 L-V | `engines.curvas` | Motor Curvas - Enriquecimiento TEA/Duration TimeSales |
| `motor_dolares` | 13:20–20:05 L-V | `engines.dolares` | Motor Dolares - MEP/CCL/canje en tiempo real (WS) |
| `motor_forwards` | 13:20–20:05 L-V | `engines.forwards` | Motor Forwards - Tasas forward en tiempo real |
| `motor_futuros_dlr` | 13:20–20:05 L-V | `engines.futuros_dlr` | Motor Futuros DLR - Curva outright Dolar A3500 con tasa implicita |
| `motor_options` | 13:20–20:05 L-V | `engines.options` | Motor Opciones GGAL - AcaQuant |
| `motor_ordenes` | 13:30–20:05 L-V | `engines.motor_ordenes` | Motor Ordenes - escucha order_report y persiste OrdenesLive/Audit |
| `motor_portfolio_snapshot` | 13:20–20:05 L-V | `engines.portfolio_snapshot` | Motor de captura del último precio para tickers de tenencia (valuaciones.portfolio_snapshot) |
| `motor_rofex` | 13:20–20:05 L-V | `engines.valores` | Motor de Captura Rofex a SQL (main_valores) |
| `tenencia_live` | 11:00–21:05 L-V | `jobs.tenencia_live` | Tenencia Live - posicion T0/T1 del dia refrescada durante la rueda |
<!-- /AUTOGEN:motores -->

**Jobs programados** (cada uno registra su corrida en `manager.job_runs`; se ven en Manager → JOBS):
<!-- AUTOGEN:crons -->
| Horario (UTC) | Módulo(s) |
|---|---|
| cada hora · 0-1h · Mar-Sáb | `jobs.market_quotes` |
| cada hora · 10-23h · L-V | `jobs.market_quotes` |
| cada 30min · 10-14h · L-V | `jobs.research_mail` |
| cada 15min · 12-23h · diario | `jobs.news_ingesta` |
| cada 30min · 12-21h · L-V | `jobs.tesoreria_echeq_recibidos` |
| cada 30min · 12-23h · diario | `jobs.news_finnhub` |
| a las 12, 14, 16, 18, 20, 22h · L-V | `jobs.interbanking_sync` |
| a las 12, 16, 20, 23h · L-V | `jobs.fred_research` |
| a las 12, 16, 20, 23h · L-Sáb | `jobs.bcra_research` |
| cada 15min · 13-15h · L-V | `jobs.mayor_sync` |
| cada 15min · 13-19h · L-V | `jobs.agente_tasa` |
| cada 15min · 13-20h · L-V | `jobs.adr_live` |
| cada 15min · 13-20h · L-V | `engines.dolar_mep` |
| cada 4min · 13-20h · L-V | `jobs.comercial_warm` |
| min 0,30 · 13-19h · L-V | `jobs.tamar_1816` |
| cada hora · 13-21h · L-V | `jobs.operaciones_informes` |
| cada 30min · 14-22h · L-V | `jobs.negocio_movimientos` + `jobs.aranceles` + `jobs.fci_bilateral` + `jobs.ops_tasa_mav` |
| cada hora · 14-22h · L-V | `jobs.operaciones_informes` |
| min 15,45 · 14-22h · L-V | `jobs.movimientos_propias` |
| cada hora · 14-22h · L-V | `jobs.ops_agregado` |
| min 5,35 · 15-22h · L-V | `jobs.pnl_totales_precompute` |
| min 0,30 · 16-17h · L-V | `jobs.mayor_sync` |
| cada hora · 18-21h · L-V | `jobs.mayor_sync` |
| 02:00 · Mar-Sáb | `jobs.cashflow` |
| 02:50 · Mar-Sáb | `jobs.tesoreria_snapshot` |
| 03:20 · diario | `jobs.cleanup_retencion` |
| 11:00 · L-V | `jobs.portafolio_backfill` |
| 11:35 · diario | `jobs.news_ingesta` |
| 11:35 · diario | `jobs.news_finnhub` |
| 11:40 · L-V | `jobs.assets_autofill` |
| 12:00 · diario | `jobs.argentina_datos` |
| 12:00 · L-V | `jobs.mercado_1816_discovery` |
| 12:20 · L-V | `jobs.fci_universo` |
| 12:30 · L-V | `jobs.cleanup_curvas` |
| 12:30 · L-V | `jobs.cleanup_futuros_dlr` |
| 12:30 · L-V | `jobs.consolidado_cuentas` |
| 12:45 · L-V | `jobs.acreencias` |
| 13:00 · diario | `jobs.ap5_portfolio` |
| 14:00 · L-V | `jobs.sync_comitentes` |
| 17:00 · L-V | `jobs.sync_comitentes` |
| 19:45 · L-V | `jobs.saldos_a_operadores` |
| 20:00 · L-V | `jobs.volatilidad_ggal` |
| 20:00 · L-V | `jobs.tamar_1816` |
| 20:06 · L-V | `jobs.day_trading_stats` |
| 20:15 · L-V | `jobs.options_rollup` |
| 20:15 · L-V | `jobs.cedears_ohlc_daily` |
| 20:16 · L-V | `jobs.bonos_ohlc_daily` |
| 20:20 · L-V | `jobs.cedears_bars_1m` |
| 20:25 · L-V | `jobs.snapshot_cierre` + `jobs.fair_value` |
| 20:30 · L-V | `jobs.fci_vcp` |
| 20:30 · L-V | `jobs.forwards_zscore` |
| 20:35 · L-V | `jobs.cierre_canje` |
| 20:40 · L-V | `jobs.snapshot_sinteticos` |
| 20:50 · L-V | `jobs.archive_options_data` |
| 21:00 · L-V | `jobs.sync_comitentes` |
| 21:10 · L-V | `jobs.eikon_cierres` |
| 22:00 · L-V | `jobs.precios_acciones_daily` |
| 22:00 · L-V | `jobs.bcra` |
| 22:00 · L-V | `jobs.market_anchors` |
| 22:00 · L-V | `jobs.mercado_1816_series` |
| 22:30 · L-V | `jobs.actividad_mensual` |
| 22:30 · L-V | `jobs.ficha_1816` |
| 23:00 · L-V | `jobs.validar_instrumentos` |
| 23:50 · L-V | `jobs.cleanup_cedears_timesales` |
<!-- /AUTOGEN:crons -->

**Otros crons** (scripts que no son jobs):
<!-- AUTOGEN:otros -->
| Horario (UTC) | Comando |
|---|---|
| 12:15 · L-V | `deploy/run_job.sh discovery_pyrofex 10m python -m scripts.discovery_pyrofex` |
<!-- /AUTOGEN:otros -->

Lo que se corre a mano (backfills, migraciones) no figura: no está agendado. El cron
real del servidor es una copia de `deploy/crontab.txt`; un `git pull` no lo cambia.

## 5. Dónde están los datos ⚙️

Una sola base (Postgres en Supabase), un schema por dominio. El modelo completo está en
`sql/schema.sql`; el porqué de cada decisión, en `docs/SQL.md`.

<!-- AUTOGEN:schemas -->
| Schema | Tablas | Qué guarda | Quién escribe |
|---|---|---|---|
| `aca` | 8 | Resumen ejecutivo de inversiones (vista ACA), carga manual | la vista ACA |
| `agente` | 10 | Hallazgos, sujetos y corridas del AV AGENT | SOLO `agente/registro.py` |
| `ap5` | 5 | Posiciones y diferencias contra A3/ACyRSA (Postrade) | jobs de Postrade |
| `bancos` | 19 | Movimientos bancarios, gastos, conciliación (Interbanking y Tesorería) | jobs de Interbanking + carga manual |
| `clientes` | 9 | Cuentas, comitentes, operadores, contrapartes, accionistas | jobs de Aunesa + Manager |
| `ext` | 4 | API externa para accionistas (claves, cuentas, auditoría) | `api/ext` |
| `home` | 2 | Cotizaciones, calendario y noticias de la portada | `jobs.market_quotes`, news |
| `ia` | 4 | Briefing diario y conversaciones del ASISTENTE | `asistente/`, briefing |
| `macro` | 3 | Series BCRA, UVA, REM, dólar A3500 | `jobs.bcra`, `jobs.argentina_datos` |
| `manager` | 19 | Usuarios, roles, grupos, corridas de jobs, diagnóstico | la vista Manager y `JobRunLogger` |
| `mercado` | 59 | Todo lo que producen los motores: curvas, snapshots, opciones, agro, FCI, cierres | `engines.*` y jobs de mercado |
| `operaciones` | 49 | Boletos, movimientos, órdenes, tipos de operación | `operaciones_informes`, `negocio_movimientos`, `motor_ordenes` |
| `partner` | 0 | Reservado (sin tablas hoy) | — |
| `portafolio` | 11 | Tenencias (AuM) y ficha de cada activo | `portafolio_backfill`, `tenencia_live`, AV AGENT |
| `research` | 10 | Datos de 1816, BCRA y FRED para la vista Research | jobs de research |
| `valuaciones` | 6 | PnL, dólar (oficial live, MEP/CCL), último precio por tenencia | motores de dólar y snapshot, `mae_forex.py` |
<!-- /AUTOGEN:schemas -->

Tres reglas que no se negocian: **todo se cruza por `id_cuenta`**; **el mismo dato no
vive en dos lugares sin árbitro declarado** (`core/duplicados.py`); **los registros se
emparejan por ficha, nunca por el nombre** (`core/pareo.py`).

## 6. De dónde vienen los datos

| Fuente | Qué trae | Cómo entra |
|---|---|---|
| **pyRofex** (ROFEX / MAE) | Precios en tiempo real, envío de órdenes | motores (WebSocket) |
| **Aunesa** (el custodio) | Cuentas, movimientos, tenencias, boletos | jobs, varias veces por día |
| **BCRA · argentina_datos** | Series macro, dólar A3500, UVA | jobs diarios |
| **1816 · FRED** | Research: renta fija argentina, datos internacionales | jobs diarios |
| **Reuters / Eikon** | Renta variable internacional | job |
| **Interbanking** | Movimientos bancarios | jobs |
| **Postrade** (A3 / ACyRSA) | Posiciones y diferencias. ⚠️ **Esta API puede operar**: escritura cerrada por defecto | jobs |
| **BYMA Custodia** | Tenencias en custodia, para cruzar contra Aunesa | corre en la PC de la oficina |
| **MAE** (dólar mayorista) | Tipo de cambio oficial en vivo | `mae_forex.py`, manual |

Si un proveedor se cae, la vista lo dice (por ejemplo «AUNESA CAÍDO») y el AV AGENT lo
registra solo. No se reintenta en loop: bloquean la cuenta.

## 7. Cómo se protege

Cinco capas, de afuera hacia adentro. Las tres primeras son infraestructura; las dos
últimas, código.

1. **Cloudflare Access**: quién entra a cada portal (login por email). Es el único gate real de identidad.
2. **Clave compartida** entre la web y la API: la API no le contesta a nadie que no sea el frontend.
3. **Identidad firmada**: la API valida la firma de Cloudflare, no confía en un header suelto.
4. **Roles por módulo** (`core/roles.py`): cada email tiene un rol, cada rol ve ciertos módulos. Esconder una solapa en la web no es un permiso: el permiso es el 403 del backend.
5. **Portal invitado, default-deny**: el header `x-acaquant-portal: guest` fuerza el rol `invitado`. Agregar un módulo a ese rol es una decisión de seguridad, y un test lo congela.

Pendiente fuera del repo: en el panel de Cloudflare Access sigue la app `acaquant-mcp-bypass`,
que exime del login cinco rutas de un MCP server que ya no existe. Hoy apuntan a 404. Sacarla es
una acción en Cloudflare, no en el código.

## 8. Los dos sistemas de IA

| | EL AV AGENT | EL ASISTENTE |
|---|---|---|
| **Qué es** | Un vigilante de los datos. Corre solo, de noche y en rueda, detecta problemas (precios que faltan, fichas incompletas, proveedores caídos) y propone arreglos que una persona aprueba | Un chat que contesta preguntas de la mesa sobre carteras, clientes, operaciones y mercado. Solo lee |
| **Cómo está hecho** | Un catálogo de habilidades (`agente/catalogo.py`): cada una declara qué mira, cuándo, qué arreglo tiene y dónde escribe. Escribe **solo** por `agente/registro.py` | Un grafo LangGraph: ruteo por reglas, agentes por tema en paralelo (cartera, cliente, operaciones, mercado), junta y control de números |
| **Dónde se ve** | Vista IA, admin | Tab LAB de la vista IA, admin |
| **Doc** | `docs/AGENT.md` | `docs/AvAgentAI.md` |

Los dos son `admin`. El invitado solo ve el BRIEFING.

## 9. Cómo llega el código a producción

| Repo | Cómo |
|---|---|
| **Frontend** | Push a `main` → Vercel construye y publica solo. Si el autor del commit no es miembro del proyecto en Vercel, **no deploya y no avisa**: mirar Deployments antes que el código |
| **Backend** | En el servidor: `cd /root/TradingAV && git pull && bash deploy/deploy.sh`. Trae el código, aplica el schema que falte, reinicia la API y el AV AGENT (`api.service` + `agente.service`) **y nada más**, y hace un smoke a `/api/health` |

**El deploy no reinicia los motores.** Reiniciar un motor en rueda corta el feed de la mesa;
es una decisión de la mesa, fuera de horario (`systemctl try-restart motor_x.service`).

En cada push corre el CI: `ruff`, contratos de capas, tests, y los checks de que este doc y
`MAPA_APP.md` no quedaron viejos.

## 10. Si se rompe

Reflejo cero: **¿qué cambió recién?** Un deploy suele ser la causa (`git log -1`).

| Síntoma | Causa más común | Qué hacer |
|---|---|---|
| La web entera tira 502 | Un import roto tumbó la API | `journalctl -u api.service -n 50`, corregir, `git pull`, `systemctl restart api.service` |
| Los números están viejos de mañana | Un motor quedó vivo fuera de hora o no arrancó | `systemctl status motor_x.service`, `systemctl restart motor_x.service` |
| Una sola vista congelada | Su motor está caído | ídem, para ese motor |
| Las órdenes quedan en PENDING | `motor_ordenes` caído (sigue las órdenes, no las envía) | `systemctl restart motor_ordenes.service` |
| Un job falló (rojo en Manager → JOBS) | Ver su log en `logs/<job>.log` | Corregir y re-correr a mano: `python -m jobs.<x>` |
| Tesorería dice «AUNESA CAÍDO» | El custodio está caído, no nosotros | `python -m scripts.diag_aunesa` dice de quién es. Si es de ellos, esperar |
| Todo lo que toca la base falla | Supabase pausado, red, o pool agotado | Revisar el proyecto en Supabase, después `systemctl restart api.service` |
| Di de baja un motor y sigue corriendo | `git pull` no apaga procesos ni edita el cron vivo | `systemctl stop` + `disable` + borrar la unit + aplicar `crontab.txt` con backup |

Comandos en el servidor, desde `/root/TradingAV`, con `venv/bin/python`.

## 11. Dónde está el detalle

Un dominio = un doc. Este documento dice cómo funciona todo; estos dicen cómo funciona cada cosa.

| Tema | Doc |
|---|---|
| Superficie completa de la app: vistas, tabs, endpoints, permisos (inventario generado) | `MAPA_APP.md` |
| Modelo SQL · contratos de la API HTTP | `SQL.md` · `API.md` |
| Renta fija y curvas · Renta variable y Reuters · FCI · Derivados | `RENTA_FIJA.md` · `RENTA_VARIABLE.md` · `FCI.md` · `DERIVADOS.md` |
| Valuaciones y PnL · Clientes y tablero comercial | `MOTOR_VALUACIONES.md` · `CLIENTES.md` |
| Research · ACA | `RESEARCH.md` · `ACA.md` |
| Interbanking · Postrade · BYMA Custodia · API externa para accionistas | `INTERBANKING.md` · `POSTRADE.md` · `BYMA_CUSTODIA.md` · `API_EXTERNA.md` |
| EL AV AGENT · EL ASISTENTE | `AGENT.md` · `AvAgentAI.md` |

Las reglas de trabajo para Claude viven en `CLAUDE.md` y `.claude/rules/`.
