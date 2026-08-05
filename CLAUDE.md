# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **📌 REMOTES POR PROYECTO (actualizado 2026-07-27).** DOS remotes por repo, ambos
> de la MISMA cuenta corporativa **NMolloAV** (por eso nunca piden login aparte):
> - **`origin` → NMolloAV (PRINCIPAL, default)**
>   - Backend: `github.com/NMolloAV/acaquant-backend.git`
>   - Frontend: `github.com/NMolloAV/acaquant-frontend.git`
> - **`org` → organización ACA (acacoop) — misma cuenta NMolloAV**
>   - Backend: `github.com/acacoop/acaquant-backend.git`
>   - Frontend: `github.com/acacoop/acaquant-frontend.git`
>
> El remote `personal` (cuenta NicolasEzequielMollo) fue ELIMINADO: usaba OTRA
> cuenta y pedía login en cada push. Tampoco existe ya un remote `corp` (ese URL
> ES `origin`). Cada remote tiene UN solo fetch/push URL a su propio repo.
>
> **AUTH automática (Git Credential Manager, Windows local).** Los dos remotes usan
> la cuenta **NMolloAV**, pinneada en `~/.gitconfig` global:
> `credential.https://github.com/NMolloAV.username = NMolloAV`,
> `credential.https://github.com/acacoop.username = NMolloAV`
> (+ `credential.usehttppath = true`). NO hay que hacer `gh auth switch` ni loguearse.
>
> **REGLA — sincronizar los 2 remotes:** alias global **`git pushall`** (= `git push
> origin HEAD && git push org HEAD`). Un `git push` normal va solo a `origin`.
>
> Vercel (frontend) deploya de `origin` (`NMolloAV/acaquant-frontend`) y el Droplet
> (backend) tira de `origin` corp — ambos migrados 2026-07-27. Un push a `origin`
> (o `git pushall`) cubre deploy + Droplet.

## Overview

TradingAV — plataforma quant MERVAL/ROFEX. pyRofex WS → **Postgres/Supabase** → FastAPI (`api.acaquant.com`) → **acaquant-web** Next.js en Vercel (`trading.acaquant.com`). Server en `/root/TradingAV` (Droplet DO), venv en `/root/TradingAV/venv`.

> **MONGO DECOMISADO (2026-06-29).** El sistema es 100% Postgres/Supabase: motores,
> jobs, API y MCP leen y escriben SQL. NO queda una sola referencia a
> Mongo en el código (`grep -rE "from core.mongo|import pymongo|MongoClient"` → 0).
> El cliente Mongo (`core/mongo.py`), `api/db.py` y el tooling Mongo fueron borrados.
> Si ves "Mongo"/"colección"/"Atlas" en algún doc viejo, es residual — la fuente de
> verdad es `sql/schema.sql` + `docs/SQL.md`.

> **⚡ PROGRAMA DE IA EN CURSO → `docs/QUANTAI.md` (LEER al arrancar la sesión).**
> Es el roadmap VIVO del programa "QuantAI": proveedor DeepSeek, marca AI
> (módulo `ia` del RBAC), Fase 0 (gateway core/ai + observabilidad + evals) y
> 5 proyectos (briefing modal en HOME, triage de incidentes, copiloto de mesa,
> prep comercial, analista ad-hoc). Ahí viven las decisiones tomadas, el estado
> de cada proyecto, los principios de ingeniería que guían TODO el desarrollo
> de IA y qué está diferido/descartado (no re-proponer sin novedad). REGLA:
> cualquier trabajo de IA se hace leyendo ese doc primero, y todo avance/
> cambio/descarte se actualiza AHÍ en el mismo commit — si el doc no refleja
> el estado real, el trabajo está incompleto.

**Schemas SQL** (Postgres/Supabase; `sql/schema.sql` es la fuente — OJO: no siempre 100% aplicado en la DB real, ver "Capa SQL"):
- `mercado` — núcleo de mercado: `curvas` (master RF, antes Trading.Curvas+BondsMaster), `market_snapshot`, `snapshots_cierre(+_hist)`, `canje_cierre`, `timesales`, `dias_habiles`; renta fija derivada `forwards_zscore`, `fit_params`, `fair_value_residuos`, `ons_ignoradas`; `futuros_dlr_snapshot`, `caucion_snapshot`; opciones `options_data(+_hist)`, `options_snapshot`, `options_metadata`, `options_vr`; renta variable `cedears`(master), `cedears_snapshot`, `adr_snapshot`, `precios_acciones`, `cedears_time_sales`, `day_trading_stats`; agro `agro_snapshot`, `agro_opciones_snapshot`, `agro_pizarra`, `camara_cereales`, `volumen_mercado_agro`; `snapshots_sinteticos`; `mercado_hist`; `rubros`, `adhoc_subscriptions`.
- `macro` — series macro `series_macro` (DOLAR/CER/BADLAR/TAMAR/RiesgoPais/Inflación), `uva`, `rem`.
- `valuaciones` — `consolidado`, `pnl_totales_cache`, `portfolio_snapshot`, `dolar`, `dolar_snapshot`, `dolar_oficial_live` (MEP/CCL).
- `portafolio` — `tenencia` (AuM, fuente única), `assets` (catálogo de títulos), `backfill_log`.
- `operaciones` — `operaciones` (vista MOVIMIENTOS), `negocio_movimientos` (cost-basis), `acreencias`, `movimientos`, `tipos_operacion`; órdenes `ordenes_live`, `ordenes_audit`, `ordenes_idempotency`, `triggers_mep`, `brackets_live`, `operativas_mep`, `motor_heartbeat`, `accounts_descubiertas`; Mesa de Dinero `mesa_dinero` (ops manuales compra+venta, montos/resultado/% derivados server-side), `mesa_dinero_tc` (TC manual por día), `mesa_dinero_traders` (catálogo), `mesa_dinero_escritores` (allowlist de escritura por email, admin siempre puede), `mesa_dinero_audit` (trazabilidad before/after) — vista NEGOCIO `/mesa-dinero`, gestión en Manager → MESA; SENEBIS `senebis` (órdenes que cargan los TRADERS para que el BACK OFFICE las procese afuera y marque `completada` — 2 estados; server-side: monto=vn×px/100, concertación=HOY, cp='255', plazo CI/24 ↔ liquidación inferidos con día hábil; export .xlsx formato sistema destino ID·OPERACION·INSTRUMENTO·PLAZO·PRECIO·CANTIDAD·CONTRAPARTE·COMITENTE·CARTERA PROPIA·MERCADO — el archivo/espejo lleva SOLO pendientes no-MAE (se sube varias veces por día; lo completado ya está cargado) — con ID = secuencia GLOBAL que espeja la numeración Quantex y no se resetea — visible y ajustable desde la vista ("PRÓXIMO ID", `POST /proximo-id`, SOLO admin — la secuencia es global; si Quantex ya consumió el número y la carga falló por mercado, `POST /ops/{id}/reasignar-id` le da a ESA orden el siguiente ID libre y quema el viejo, sin renumerar el resto); contraparte del senebi: `tipo_contraparte` interno/externo — externo elige agente del catálogo `senebis_agentes` (nombre→número) y en el Excel va COMITENTE vacío + CONTRAPARTE=número; interno resuelve `cc` por número o denominación contra `clientes.cuentas` y en el Excel COMITENTE=cp si GARANTIZADO, sino cc; `es_mae`=true → la orden se carga en el MAE: excluida del Excel/espejo y tipo fijo 'MAE' — la vista filtra CON / SIN / SOLO MAE (`?mae=solo|sin`); `cargan_ellos` es flag SI/NO (era texto libre, migrado 2026-08-05): SI = la orden la carga la CONTRAPARTE en Quantex → también excluida del Excel/espejo, pero visible en la vista con su flujo pendiente→completada normal; marcas de edición persistentes que reemplazan al amarillo que se pintaba a mano en la planilla vieja: `campos_editados` acumula qué campos se tocaron post-alta (el front les pone `*`, el estado NO cambia) y `editada_completada` se prende si se editó algo que YA estaba completada (el back office la cargó en Quantex con los datos viejos → fila amarilla + botón ⚠ EDITADA que la baja vía `POST /ops/{id}/visto`); se calculan server-side en `editar_op` comparando before/after de lo persistido, no del payload), `senebis_presencia` (heartbeat: quién tiene la vista abierta, para no pisarse), `senebis_audit` (before/after) — vista BACK OFFICE, módulo `back-office`, router `api/routers/senebis.py`. `acavalores_retorno` (informe Excel "OP Aca Valores FCI" del fondo ACA R.TOTAL, carga NO diaria y EXCLUSIVA por `scripts/import_acavalores_retorno.py`, idempotente por `periodo` YYYY-MM — alimenta la tab "ACA VALORES RETORNO TOTAL": Σ Valor Nominal por operación/agente/papel).
- `clientes` — `comitentes`, `cuentas`, `contrapartes`, `accionistas`, `actividad_mensual`, `operadores`, `objetivos_comerciales` (segmentación/operador).
- `manager` — `manager_users`, `role_matrix`, `role_audit`, `grupos`, `job_runs`, `pyrofex_instruments`/`pyrofex_discovery`; `latencia_endpoints` (telemetría de LATENCIA endpoint × hora — agregado que flushea el middleware `api/telemetria.py`, lo lee `GET /api/manager/latencia`; la vieja `uso_modulos` fue ELIMINADA 2026-08-04); `asistente_chats`/`asistente_mappings` (ASISTENTE DE NEGOCIO — QuantAI P7: transcript real + mapping ficha↔identidad de la aduana `core/pii_gateway.py`; módulo RBAC `asistente`, admin-only; NO tiene endpoint propio — es la vista `negocio` del copiloto, cerebro en `api/services/asistente.py`).
- `home` — `market_quotes` (watchlist HOME), `news_headlines`.
- `mcp` — `oauth_clients`/`oauth_codes`/`oauth_tokens` (TTL automático).
- `ia` — observabilidad del gateway de IA (`core/ai.py`, ver `docs/QUANTAI.md`): `trazas` (cada llamada LLM: tarea, modelo, tokens, latencia, ok/error, feedback, detalle/respuesta/razonamiento, conv_id) y `config` (presupuestos editables desde Manager). Router HTTP: `api/routers/ia.py` (bearer + `require_module("ia")`): briefing, observabilidad, presupuestos/saldo, y el COPILOTO de mesa (`/api/ia/copiloto*` — **doc vivo con changelog OBLIGATORIO: `docs/COPILOTO.md`**, leerlo antes de tocar `api/services/copiloto.py`). También: `triage_incidentes`/`triage_estado` (triage IA de jobs fallidos, `jobs/triage.py` cada 10') y `research` (mail diario 1816 vía IMAP, `jobs/research_mail.py` — cuerpo crudo + destilado LLM + FTS español).
- `research` — market data 1816 para la vista Research: `mkt_1816_series`/`mkt_1816_watch`/`mkt_1816_instrumentos` (feed SEPARADO de `mercado.curvas` — ver `docs/VISTA_RESEARCH.md`); tab BCRA: `bcra_variables`/`bcra_watch`/`bcra_series` (ver `docs/RESEARCH_BCRA.md`).
- `estrategia` — ESTRATEGIA QUANT (señal intradía con trazabilidad, tab ESTRATEGIA de Trading — **doc vivo: `docs/ESTRATEGIA_QUANT.md`**): `senales` (ledger append-only), `resultados` (resolver intradía por horizonte), `modelo_pesos` (versionado), `eval_live` (última evaluación por ticker).

## Contexto por subdirectorio

Cada carpeta grande tiene su propio `CLAUDE.md` con lo que aplica SOLO ahí —
se carga automáticamente al trabajar en esa carpeta. Este archivo (raíz)
tiene lo que aplica a todo el repo.

- **`api/CLAUDE.md`** — ⚠️ REGLA #1 (validar imports), RBAC, services `@cached`, filtros de cuenta, live fallback, motor de PnL.
- **`engines/CLAUDE.md`** — patrón de escritura a `mercado.market_snapshot`, motores stale.
- **`jobs/CLAUDE.md`** — filtros de exclusión del AuM, patrón de jobs nuevos (`JobRunLogger`).
- **`scripts/CLAUDE.md`** — REGLA #0 aplicada, minimalismo (REGLA #5), backfills seguros (REGLA #4).

## ⚠️ REGLA #0 — Cómo entregar trabajo al usuario (LEER PRIMERO)

**Claude NO tiene ni va a tener acceso al Droplet.** Todo lo que tenga que correr en producción se entrega como código en el repo, no como comando para copiar.

- **Nada de bloques de comandos / queries / snippets para que el user copie y pegue.** Operar el Droplet desde la consola web de DigitalOcean hace que copiar y pegar sea doloroso (line wrapping, multilinea, caracteres especiales). Esta regla ya se pidió varias veces y se sigue violando.
- **Workflow correcto**: Claude escribe el código → archivo en el repo (`scripts/<x>.py`, `jobs/<x>.py`, endpoint en `api/`) → commit + push a `main` → el user hace `git pull` en el Droplet y lo ejecuta con `python -m scripts.<x>`.
- **Diagnóstico one-shot también va a `scripts/`** (ej. `scripts/diag_*.py`). Una query SQL de 5 líneas igual va en archivo, no en chat.
- **Excepción mínima**: si es UNA sola línea trivial (`systemctl status x`, `tail logs`), se puede pasar inline — pero el default es siempre script.
- **Cero "probá esto, si no andá probá esto otro"**. Una solución por vez, comiteada al repo.

## ⚠️ REGLA #2 — NUNCA ASUMIR: verificar antes de afirmar o codear

**Bloqueante. Es la causa #1 de romper cosas.** Claude NO tiene acceso al
Droplet ni a la DB de prod (Postgres/Supabase) → no puede inferir nada sobre los datos reales. Afirmar
hechos sobre prod sin medir (proporciones, volúmenes, esquema, qué campos
existen, qué valores tienen, cómo se comporta algo) y después escribir código
en función de eso es lo que rompe todo.

- **Prohibido afirmar hechos no verificados sobre los datos/prod.** Nada de
  "X es una minoría", "esto normalmente trae…", "probablemente el campo…",
  "la mayoría de los docs…". Si no lo mediste, no es un hecho.
- **Distinguir SIEMPRE hipótesis de hecho verificado**, explícito y en voz alta.
  "Hipótesis (sin medir): …" vs "Verificado (corriste el diag): …".
- **NUNCA escribir código cuya CORRECTITUD dependa de una suposición no
  verificada.** Si la decisión necesita un dato de prod, primero conseguirlo.
- **Para conseguir un dato de prod**: escribir un diag read-only
  (`scripts/diag_*.py`, REGLA #0), el user lo corre y devuelve el número. Recién
  ahí se decide/codea. Si no se puede medir, decir explícito "no puedo verificar
  esto" y esperar confirmación — no avanzar a ciegas.
- **Optimizar = medir primero** (`explain()` / timing), después tocar. Nada de
  optimizaciones justificadas por una corazonada sobre cómo lucen los datos.

## ⚠️ REGLA #3 — TODO cambio lleva EXPLICACIÓN EJECUTIVA (a raja tabla)

**Obligatorio, sin excepción.** Cada cambio que se entrega (commit, script,
endpoint, fix, refactor, config) va acompañado de una explicación ejecutiva
breve, en lenguaje claro (no técnico-críptico), con DOS partes:

- **Qué soluciona** — el problema concreto que resuelve / la pregunta que
  responde. Por qué se hizo.
- **Qué genera / qué impacto tiene** — qué cambia en el sistema a partir de
  ahora: comportamiento nuevo, efectos colaterales, qué hay que correr/deployar,
  qué se gana (perf, plata, visibilidad), qué riesgo introduce si lo hay.

Formato sugerido (corto, va en el mensaje al user, no necesariamente en el código):

```
📋 Qué soluciona: …
📋 Qué genera:    …
```

El user opera solo un proyecto enorme y necesita entender el "qué" y el "para
qué" de cada cambio sin leer el diff. Un cambio sin esta explicación está
INCOMPLETO. Aplica también a los diags y a los cambios de doc.

## ⚠️ REGLA #4 — Backfills/migraciones JAMÁS escanean prod a ciegas

**Causó dos veces el CPU 100% en el M10. Bloqueante.** Ningún backfill,
migración o `--full` se corre sin cumplir TODO esto:

- **Scopeado**: apuntar SOLO a los docs que realmente cambian (ej. `bruto=0`),
  nunca un scan de toda la tabla si se puede filtrar por índice.
- **Batcheado + throttle**: procesar en lotes con `sleep` entre lotes para no
  starvar a los motores. Nada de un `bulk_write` gigante de una.
- **Medir el costo ANTES** (REGLA #2): `explain()` / contar docs afectados. Si
  toca un scan grande, decirlo explícito y decidir.
- **Vía `run_job.sh`** (lock + timeout) y, salvo que sea liviano y scopeado,
  **fuera de rueda** (no 13-20 UTC L-V, cuando corren los motores).
- **Idempotente**: cortarlo a la mitad y re-correrlo no rompe nada.

Un `--full` a ciegas en horario de mercado es exactamente el anti-patrón del
incidente 2026-06-03. Si dudás del volumen, NO lo corras: medí primero.

## ⚠️ REGLA #5 — Minimalismo en `scripts/` Y `docs/`: se BORRA lo cumplido

**Minimalismo, no cementerio.** Aplica a código Y documentación.

- **`scripts/`:** cada `diag_*`/`fix_*`/`backfill_*`/`seed_*` one-shot, una vez que
  el user confirma que el tema cerró, **se elimina**. Queda solo lo recurrente
  (generadores, monitoreo, perf, audit, feeds) + lo referenciado por skills/CI/cron.
- **`docs/`:** las auditorías/análisis point-in-time, los `wip_*`, las imágenes
  scratch y todo lo superseded **se borran o se consolidan**. La arquitectura/
  datos/estrategia/roadmap viven en **UN doc madre: `docs/ARQUITECTURA.md`** — no
  esparcidos en N archivos. El resto de `docs/` es referencia operativa viva (API,
  RUNBOOK, MCP, seguridad, etc.) + el `vault/` auto-generado.
- Ante la duda, preguntar "¿lo borro?" — no acumular por las dudas.

## ⚠️ REGLA #6 — Credencial/acceso faltante: se pide UNA vez, no se insiste

Si para avanzar hace falta una credencial/usuario/permiso que **solo el user
puede crear** (ej. una env var o un rol de Postgres/Supabase), se
dice **una vez**, claro, y se marca como PENDIENTE. **No repetir el pedido cada
turno ni bloquear todo en eso** — seguir con lo que sí se puede hacer. El user
lo provee cuando puede.

## ⚠️ REGLA #7 — IR MÁS ALLÁ: enseñar y proponer, no solo ejecutar

El user es PM (no dev) y depende de Claude para crecer técnicamente: *"no tengo
manera de capacitarme y aprendo si no es con vos"*. En CADA trabajo, además de
resolver lo pedido:

- **Detectar lo que él no sabe pedir**: joins innecesarios, queries ineficientes,
  tablas mal modeladas, código que se puede simplificar, deuda técnica,
  riesgos de datos. Traerlo proactivamente aunque no lo haya pedido.
- **Enseñar el porqué**: explicar el concepto nuevo en lenguaje claro (gerencial
  + técnico), no solo aplicarlo. Que aprenda algo en cada interacción.
- **Proponer estructura nueva**, no solo optimizar lo existente al máximo. Leer
  como arquitecto SR: cuestionar el diseño de base.
- Esto NO reemplaza la REGLA #2 (no asumir, medir primero) ni el formato ejecutivo
  (REGLA #3). Va arriba de eso: hacer el trabajo Y dejar conocimiento.

Ver memorias [[feedback_proactive_architect]] y [[feedback_autonomy_lanes]].

## ⚠️ REGLA #8 — Portal INVITADO (www.acaquant.com): SOLO mercado/research, nunca filtrar datos del negocio

**Bloqueante.** Conviven dos portales:

- **trading.acaquant.com** — app interna de la mesa. Usuarios de la mesa
  (admin/trader/sales/etc.), ven todo según su rol.
- **www.acaquant.com** — **portal INVITADO**: lo usa gente de **OTRO SECTOR de
  la MISMA empresa** (grupo ACA — aclaración del user 2026-07-21; NO son
  terceros, así que el contenido licenciado 1816/Reuters no sale de la
  compañía). Ven **mercado + research** (read-only) + los copilotos de IA de
  esas vistas (identidad `guest:<email>`, tope 100k/día c/u, sin la guía).

Lo que NO cambia y JAMÁS se pasa por alto: cualquier cosa del **NEGOCIO de la
mesa** (portfolios, operaciones, manager, back-office, acreencias, gestión de
ONs, clientes, AuM, P&L, contrapartes, segmentación, la guía de la plataforma,
etc.) **NUNCA** puede quedar accesible al invitado — otro sector tampoco ve el
negocio de la mesa. Si un desarrollo nuevo no es de mercado/research, no se
mete en el portal www — punto.

- El backend fuerza rol `invitado` (default-deny) cuando ve el header
  `x-acaquant-portal: guest` (`api/auth.py::is_guest_portal` + check contra
  `core.roles.INVITADO_MODULES`). Agregar algo a `INVITADO_MODULES` es una
  decisión de SEGURIDAD — solo mercado.
- El frontend `acaquant-web` filtra nav/vistas por módulo; el invitado no debe
  ver ni el link de algo que no sea mercado.
- **Default-deny**: ante la duda, NO exponer al invitado.

Ver memoria [[feedback_portal_invitado_www]].

## Reglas que rompen todo si se olvidan

- **`python -m <módulo>` desde la raíz siempre**. `python engines/x.py` falla (`core` no es discoverable).
- **Conexión SQL**: pool singleton `core.postgres.get_pool()` (no cerrarlo).
- **Regla de capas**: `core/` no importa nada del proyecto. `engines/` y `jobs/` usan `core/` + `quant/`. `api/services/` es puro (sin FastAPI), `api/routers/` solo HTTP plumbing.
- **Commits**: estilo `feat/fix/docs/refactor(scope): mensaje` en español, como el `git log`.
- **Constantes globales y feature flags** viven en `config.py` (raíz): `TICKERS_EXTRA_PRECIOS`, `TICKERS_BOOK_FULL`, etc. Env vars en `.env` local / systemd unit files en el Droplet (`MANAGER_EMAILS`, `DEFAULT_ROLE`, `MCP_*`, `POSTGRES_URI`).

> Validar imports antes de pushear router/service (REGLA #1) y la regla de
> services `@cached` → ver `api/CLAUDE.md`.

## Estructura

```
core/        # infra (postgres, pg_mirror, ai [gateway LLM], ai_resumen, curvas_sql, dolar_sql, grupos_sql, roles_sql, series_macro, market_snapshot, websocket, rofex_session, rofex_orders_session, roles, job_runs, profiler, byma, mae, cafci, finnhub, yahoo, argentina_datos, dolar_oficial)
engines/     # motores WS → SQL (always-on L-V 13-20 UTC) — incluye motor_cedears (alimenta Scanner CEDEARs)
jobs/        # batch/cron — incluye precios_acciones_daily (alimenta scanner via SQL mercado.precios_acciones)
quant/       # cálculo puro (black_scholes, stats, curve_fit, pivot_points, rolling_stats)
api/services # lógica pura (invocada por routers y por el agente)
api/routers  # thin HTTP wrappers. manager/ es paquete de sub-routers
api/mcp/     # MCP server (FastMCP) + OAuth 2.1 provider + discovery
scripts/     # one-shot / migraciones / smoke
tests/       # pytest — unit/ + integration/ (marker `integration`, excluido por defecto via addopts)
evals/       # datasets de evaluación del programa QuantAI (ver docs/QUANTAI.md)
sql/         # schema.sql — espejo relacional Postgres/Supabase (ver "Capa SQL")
deploy/      # systemd + crontab.txt (fuente de verdad)
.claude/     # settings.json + hooks + commands + skills + agents (ver .claude/INDEX.md)
docs/        # documentación (ver "Mapa de docs" abajo) + vault/ (cerebro Obsidian, auto-generado)
```

## Mapa de docs — cuál leer ANTES de tocar cada dominio

`docs/ARQUITECTURA.md` es el **DOC MADRE** (arquitectura/datos/estrategia/roadmap).
Los marcados **[VIVO]** tienen changelog obligatorio: si tocás ese dominio y no
actualizaste su doc en el mismo commit, el trabajo está incompleto.

| Dominio / si vas a tocar… | Doc |
|---|---|
| Arquitectura, datos, roadmap | `ARQUITECTURA.md` (madre) |
| Modelo SQL / schema | `SQL.md` + `SQL_MODELO.md` + `sql/schema.sql` |
| Programa de IA (gateway `core/ai`, briefing, triage) | `QUANTAI.md` **[VIVO]** |
| Copiloto de mesa (`api/services/copiloto.py`) | `COPILOTO.md` **[VIVO]** |
| Agregar una TOOL al asistente/copiloto | `TOOLS_IA.md` **[VIVO]** (auditoría de huecos + tandas) |
| Vista `/research` (1816, mail diario) | `VISTA_RESEARCH.md` **[VIVO]** |
| Research → tab BCRA / FRED | `RESEARCH_BCRA.md` · `RESEARCH_FRED.md` |
| Feed Eikon live / tab REUTERS (`eikon_*`) | `INTEGRACION_REUTERS.md` **[VIVO]** |
| Renta fija / curvas | `RENTA_FIJA.md` · `SALUD_CURVAS.md` |
| Renta variable / scanner | `RENTA_VARIABLE.md` |
| Estrategia Quant (señal intradía, tab ESTRATEGIA de Trading) | `ESTRATEGIA_QUANT.md` **[VIVO]** |
| Derivados · sintéticos · agro | `DERIVADOS.md` · `SINTETICOS.md` · `AGRO.md` |
| Valuaciones / PnL | `MOTOR_VALUACIONES.md` |
| MCP server / tools | `MCP.md` · `MCP_TOOLS.md` |
| Operación, incidentes, monitoreo | `RUNBOOK.md` · `OBSERVABILIDAD_ROBUSTEZ.md` |
| Seguridad / credenciales | `SECURITY.md` · `SECRETS.md` |
| Clientes / grupos / segmentación | `GRUPOS.md` · `SEGMENTACION_PATRIMONIAL.md` |
| API HTTP (contratos) | `API.md` |

Auto-generados (NO editar a mano): `HERRAMIENTAS.md`, `vault/`, `deploy/SISTEMA.md`.

## Plano del sistema — `deploy/SISTEMA.md`

Fuente de verdad de TODO lo que corre: servicios systemd, motores, crons y
cómo se conectan. **Si agregás / quitás / modificás un servicio systemd o un
cron** (tocás `deploy/systemd/*.service` o `deploy/crontab.txt`), en el MISMO
cambio regenerá el plano:

```bash
python -m scripts.gen_sistema          # regenera las tablas (no editar a mano entre marcadores AUTOGEN)
python -m scripts.gen_sistema --check  # falla si SISTEMA.md quedó desincronizado
```

El inventario (servicios/motores/crons) es auto-generado desde la fuente
real → no puede mentir. La narrativa (topología, flujo de datos, bases) se
mantiene a mano. Si cambió cómo se conectan los servicios, actualizá esa
parte también. Skill: `/sistema`.

## Cerebro Obsidian — `docs/vault/`

Grafo navegable de TODO el sistema (módulos, componentes, rutas, crons,
colecciones, vistas, services, libs) como vault de Obsidian. El generador
`scripts/gen_obsidian.py` es **determinista**: parsea el código y reconstruye
archivos, links y backlinks de cada nota. La sección _Qué hace_ se completa con
una pasada de enriquecimiento con IA; el resto NO se edita a mano.

```bash
python -m scripts.gen_obsidian          # regenera las notas
python -m scripts.gen_obsidian --check  # CI: falla si el vault quedó stale
```

Mismo contrato que `gen_sistema`: tras un cambio estructural (router/cron/
colección/componente nuevo) el vault queda desincronizado → regenerar en el
mismo cambio. Cómo abrirlo: `docs/vault/README.md`.

## Comandos

```bash
uvicorn api.main:app --reload --port 8000
python -m engines.<motor> | jobs.<job> | scripts.<cmd>
ruff check . [--fix]                           # line-length=100, py312
pytest -ra                                     # unit (pyproject ya excluye integration via addopts)
pytest tests/<path>::<test_name>               # single test
pytest -m integration                          # integration (requiere Postgres accesible)
python -m scripts.perf_scan [--strict]         # anti-patterns de queries
```

CI (`.github/workflows/ci.yml`): en cada push/PR a `main` corre `ruff check .` (bloqueante) + `perf_scan` (informativo, `continue-on-error`) + `pytest -ra` (solo unit). Python 3.12. No buildea el frontend.

## Frontend en repo hermano

`../acaquant-web/` (Next.js 16, deploy auto a Vercel sobre `main` — `src/proxy.ts`, no middleware). **No es submodule** — es checkout paralelo. Cambios de API con impacto en UI se editan ahí con rutas absolutas (`C:\...\acaquant-web\...`). Las routes de Next que consumen endpoints "live fallback" necesitan `dynamic = "force-dynamic"` + `revalidate = 0` + `Cache-Control: no-store` (ver `api/CLAUDE.md`).

## Tablero Comercial (lente por operador)

El Tablero Comercial se sirve SQL-only desde `api/services/comercial_sql.py` (el router `operaciones.py::_com_motor` siempre devuelve SQL; `comercial.py` quedó como helpers/funciones SQL — ver "Capa SQL"). Cruza todo por `id_cuenta`: QUIÉN (`clientes.comitentes` → operador + `nivel_1`), ACTIVIDAD (`operaciones.negocio_movimientos`/`operaciones.operaciones` → última op), TAMAÑO (`portafolio.tenencia`, `aum='si'`), operador↔usuario (`manager.manager_users`, para cuentas huérfanas). Estado comercial por días desde última op: ACTIVA ≤45 / ENFRIANDOSE 45-90 / DORMIDA / NUEVA. Agrega EN VIVO con índices (sin precompute — no se recrean rollups).

## Operaciones — SQL (migrado de Mongo 2026-06-16, CRÍTICO no inferible)

`operaciones.operaciones` (SQL Postgres) es la fuente de la vista MOVIMIENTOS (`/api/operaciones/ops/*`) + Contrapartes (`/operaciones/flujo`). **`CashFlow.Operaciones` (Mongo) y el rollup `CashFlow.OpsSerieDiaria` fueron ELIMINADOS** — ver `docs/SQL.md`. Origen: `jobs.operaciones_informes` (API informes Aunesa) que escribe SQL directo vía `operaciones_informes.ingestar_filas_sql` (normaliza + enriquece inline `moneda`/`mercado`/`operacion`/`nivel_3`/`segmento`/`es_cierre`/`commodity`/`mep`). `jobs.fci_bilateral` escribe el FCI bilateral (campo `etapa`) — upsert por boleto que NO pisa el resto. El catálogo `tipos_operacion` vive en SQL (`operaciones.tipos_operacion`).

**Series: HOT/COLD (decisión 2026-08-04, revierte el "no precomputes").** Los días CERRADOS viven pre-agregados en `operaciones.ops_agregado_diario` (mantenida por `jobs/ops_agregado` cada hora en rueda — recomputa POR DÍA SUCIO vía `ingestado_en`, así los backfills históricos re-agregan su día solo y NO puede driftear como el viejo `ops_rollup`); HOY se agrega EN VIVO. `/ops/serie` sin filtros lee agregado+hoy; con filtros va 100% en vivo (`GROUP BY` + índices, `api/services/operaciones_sql.py`). `/ops/aranceles` sigue 100% en vivo (candidato a adoptar el agregado).

- **El arancel y el bruto NO comparten filtro de cierre**: para volumen `bruto` excluye `es_cierre=true`; para `arancel` se INCLUYEN los cierres (el **arancel de caución vive SOLO en el cierre**). `etapa <> 'solicitud'` siempre (la liquidación CL ya cuenta).
- **`es_cierre`** materializado (bool) separa volumen de arancel sin regex.
- El motor de PnL no usa esta tabla (cost-basis sale de `negocio_movimientos`); acá viven volumen/arancel comercial.
- Opciones mantiene su rollup propio (`jobs/options_rollup.py`) sobre tablas SQL.

## mercado.curvas — shape de flujos (CRÍTICO, no inferible)

- **CER**: porcentual. `amortizacion_pct` + `cupon_sobre_residual` YA resuelto (NO re-multiplicar por `residual_previo_pct`). `cupon_anual=0` si zero coupon. Requiere `cer_emision`.
- **tasa_fija**: absolutos. `amortizacion` + `interes`. Requiere `flujo_vencimiento`.
- **soberanos** (`tipo='globales'|'bonares'`): mismo shape que CER, `cupon_sobre_residual` ya en USD.

Agregar instrumento: fila en `mercado.curvas` (vía `core/curvas_sql.py`; `data` jsonb = doc completo) + fila en `portafolio.assets` con `ticker == ticker_corto`. Sin el segundo no aparece en AuM/Portfolios.

`config.TICKERS_EXTRA_PRECIOS`: tickers que `motor_rofex` suscribe pero `motor_curvas` ignora. Default `['MERV - XMEV - AL30C - 24hs']` para `/api/analitica/canje`.

## Fórmulas no inferibles

**AuM / tenencias** — fuente única SQL `portafolio.tenencia` (writer diario
`jobs/portafolio_backfill --diario`, 11:00 UTC L-V). Mongo `Valuaciones.AuM` fue
**ELIMINADA** (2026-06-15) junto con `jobs/aum.py::run` (queda solo de librería de
helpers Aunesa) y la tabla SQL `aum`. El divisor de la valuación lo decide la
**CARTERA** (no más `tipoTitulo`, que se quedaba NULL):
- Renta fija (cartera `HD / DL / ARS`, cotiza en paridad) → `cantidad × precio / 100`
- Cash (`MONEDAS`), `FCI`, `RENTA VARIABLE` → `cantidad × precio` (NUNCA ÷100)
- Futuros (`DERIVADOS`) → `(precio + 1) × cantidad`
- El motor de PnL (`pnl.py::_aplicar_normalizer`) usa la MISMA regla por cartera.

**Breakevens** (`engines/breakevens.py`, método Buscar Objetivo, cupón cero):
```
retorno_lecap = flujo_vto_lecap / precio_lecap − 1
X = [(1 + retorno_lecap) · (precio_cer · cer_emision) / (vn_cer · cer_actual)]^(1/meses) − 1
```
Match **mismo vto** Lecap↔CER (`MAX_DIFF_DIAS=20`). Anualización con `dias_cer` = vto − 10 hábiles. Filtro `mes_inflacion ≤ último IPC publicado`. Fallback Fisher si faltan datos.

**Forwards**: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Lee última TEA por ticker desde `MarketSnapshot.metrics.TEA` (escrita por `motor_curvas` en cada update). Igual patrón usan `breakevens.py` y los services de portfolio/renta-fija. **No leer TimeSales agregado** — es estrictamente más caro y devuelve el mismo valor que el snapshot live.

**TC Breakeven** (`api/services/renta_fija.py::_tc_breakeven`, sólo tasa fija nativa o CER fijado): `TC_BE = MEP × (flujo_vencimiento / precio_actual)`. Lee `flujo_vencimiento` de `mercado.curvas`, `last_price` del trade más reciente y MEP de `get_ultimo_mep` (live, TTL 5s). Se calcula on-the-fly en `get_renta_fija` y `listar_curva` — no se persiste.

**AuM join chain**: `mercado.curvas` (campo `curva`) → `ticker_corto` → `portafolio.assets.ticker` → `unidad` → `portafolio.tenencia` (SQL, filtrar `aum='si'`).

**Enriquecimiento CER**: `motor_curvas` usa CER con settlement T-10 hábiles. Si un bono no opera un día, el último trade puede quedar con CER de ayer.

## Asistente legacy — ELIMINADO

`api/agent/` + `POST /api/chat` fueron **borrados del repo** (no existen más;
no documentar ni referenciar). El asistente con IA del producto es el MCP
server (sección siguiente).

## MCP server (Custom Connector)

`api/mcp/` montado en `https://api.acaquant.com/mcp` — asistente **100% de RENTA VARIABLE**: 13 tools de SOLO LECTURA sobre equities ARG (universo CEDEARs/ADRs, tablero live ARS+USD, time sales intradía, retornos/quant del subyacente USD, pivot points, day-trading lab, Mesa de Estrategia: correlación/trade_analysis/book_analysis). NO expone portfolio/operaciones/cuentas/AuM/manager (datos privados). Las tools de estrategia operan solo sobre posiciones que el usuario pasa por parámetro — no leen cuentas reales. Las tools registradas viven en `api/mcp/tools/renta_variable.py`; `server.py` es solo wiring. **Los dominios de mercado no-RV (renta fija, derivados, opciones, forwards, breakevens, cauciones, futuros DLR, MEP, macro) están PAUSADOS** en `api/mcp/tools/parked_mercado.py` (código intacto, no registrado — descomentar `register(mcp)` en `server.py` para reactivar). Cliente principal: Claude Desktop / claude.ai vía Custom Connector. Doc completo de cada tool: `docs/MCP_TOOLS.md`.

**Auth**: OAuth 2.1 + PKCE + DCR (RFC 7591), Cloudflare Access como IdP. Flow completo en `docs/MCP.md`. Env vars: `MCP_BEARER_TOKEN` (static fallback dev/curl), `MCP_JWT_SECRET` (firma OAuth JWTs), `MCP_OAUTH_ISSUER` (default `https://api.acaquant.com`). Sin ninguno, `/mcp` queda deshabilitado.

**Dos cosas críticas que rompen el connector** (se aprendieron a los golpes; doc completo en memoria `project_mcp_cf_access.md`):

1. **CF Access path scoping**. App `acaquant-mcp-bypass` (BYPASS + Everyone) cubre 5 paths: `/mcp`, `/oauth/token`, `/oauth/register`, `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`. Si CF Access tapa `/mcp`, el cliente recibe HTML de login en vez de 401 → muere silencioso. `/oauth/authorize` SÍ debe estar protegido (ahí logea el user). 5/5 destinations al tope.
2. **`TransportSecuritySettings` en `api/mcp/server.py`** con `allowed_hosts` (`api.acaquant.com`) y `allowed_origins` (`https://claude.ai`, `https://claude.com`). El default del SDK MCP solo acepta localhost → 421 Misdirected Request. El smoke local NO replica esta condición.

## Capa SQL — Postgres/Supabase (ÚNICA base; Mongo decomisado 2026-06-29)

**Postgres/Supabase ES el sistema.** Todo lee y escribe SQL: motores, jobs, API,
MCP. Mongo fue decomisado por completo — no hay dual-run, ni flags de
engine, ni espejo. Doc de referencia del modelo: **`docs/SQL.md`** + `sql/schema.sql`.

Esquema: `sql/schema.sql` (OJO: NO siempre 100% aplicado en la DB real — algún
`CREATE TABLE`/columna del archivo puede no existir en Postgres todavía; `scripts/apply_schema.py`
las crea). Pool/conn: `core.postgres.get_pool` (lee `.env` propia).

- Convención: cada dominio tiene su módulo de lectura/escritura SQL (`*_sql.py`
  o helpers en `core/`): `curvas_sql`, `macro_sql`, `renta_fija_sql`, `comercial_sql`,
  `valuaciones_sql`, `pnl_sql`, `agro_sql`, `operaciones_sql`, `grupos_sql`, `roles_sql`,
  `market_snapshot`, `series_macro`, etc. Los selectores `_motor()`/`_engine` y los flags
  `*_SQL` quedaron obsoletos (ya no hay rama Mongo) — si ves uno, es vestigial.
- Escrituras SQL-native vía `core.pg_mirror` (`write_native`/`append_native`/`write_hist`).
- El motor de PnL (`pnl.py::_pnl_por_cuenta_core`) es lógica PURA sobre dicts inyectados
  desde SQL (`pnl_sql._deps_sql`) — no lee la base directo.

## Deploy

Push a `main` → Vercel auto-deploya acaquant-web. Backend: `git pull` + `python -m scripts.apply_schema` (si hubo cambios de schema) + `systemctl restart api.service` en el Droplet, o skill `/deploy`. Motores de mercado los controla cron (start/stop L-V). Cron fuente de verdad: `deploy/crontab.txt`.

> **Todo lee SQL.** Tenencias/catálogo en `portafolio.tenencia`/`portafolio.assets`;
> el join de instrumentos + normalización de assets lo hace `api/services/titulos_flujos.py`
> (lee `portafolio.assets`). Las viejas colecciones espejo `*API` y los syncs Mongo→Mongo
> (`sync_api_copies`, `api_migrate`) ya no existen.

Jobs críticos diarios: `jobs.bcra --today` (22 UTC L-V, pide hoy+21d para CER forward), `jobs.argentina_datos` (12 UTC, RiesgoPais/IPC/REM), `jobs.portafolio_backfill --diario` (11 UTC L-V, writer de tenencias SQL — reemplazó a `jobs.aum`/Mongo, eliminado), `jobs.cleanup_curvas` + `jobs.cleanup_futuros_dlr` (12:30 UTC L-V, antes de motores), `jobs.snapshot_cierre` (20:25 UTC L-V, post-cierre — lee `mercado.market_snapshot` y persiste cierre por bono en `mercado.snapshots_cierre`), `jobs.negocio_movimientos` (cada hora 15-22 UTC L-V, pega a Aunesa `consolidadosGenerales`, parsea/categoriza/agrupa por boleto y persiste idempotente en **SQL `operaciones.negocio_movimientos`** para la vista `/operaciones/negocio`), `jobs.guardrails` (20:45 UTC L-V, post-cierre — invariantes de sanidad de datos; report en el log + stat en `manager.job_runs`), `jobs.research_mail` (cada 30' 10-14 UTC L-V, ingesta el mail 1816 a `ia.research`), `jobs.mercado_1816_series` (22 UTC L-V, append diario a `research.mkt_1816_series`), `jobs.bcra_research` (12/16/20/23 UTC L-S).

Dólar oficial: única fuente live es `valuaciones.dolar_oficial_live` (feed MAE mayorista UST$T plazo 000, script local en PC oficina). Histórico/anchors (7d/MTD/YTD del watchlist `/argy`) deshabilitado hasta que MAE acumule histórico suficiente. Para series macro (`serie_macro` con `dolar_oficial`/`dolar_mayorista`) usar `macro.series_macro` clave DOLAR (BCRA A3500 fixing diario).
