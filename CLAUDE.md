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
> **⚠️ AUTOR DE LOS COMMITS — Vercel BLOQUEA por identidad.** Vercel tiene proteccion
> de despliegue por autor: si el commit lo firma alguien que NO es miembro del
> proyecto, el deploy queda **Blocked** y produccion sigue sirviendo la version
> anterior **sin ningun error visible**. Paso el 2026-08-09: cuatro deploys seguidos
> (VEPS, saldo final, pantalla SALUD) quedaron bloqueados por venir firmados con
> `mollonicolas95@gmail.com`, que GitHub asocia a la cuenta PERSONAL
> `NicolasEzequielMollo` — la misma que se saco del flujo de remotes. Sintoma: el
> codigo esta en `main`, el build compila, y aun asi la app no cambia.
> **REGLA: no pisar `user.name`/`user.email` al commitear.** El default del checkout
> es el que Vercel acepta. Si algo no aparece en la app, mirar
> Deployments en Vercel ANTES de buscar el bug en el codigo.
>
> Vercel (frontend) deploya de `origin` (`NMolloAV/acaquant-frontend`) y el Droplet
> (backend) tira de `origin` corp — ambos migrados 2026-07-27. Un push a `origin`
> (o `git pushall`) cubre deploy + Droplet.

## Overview

TradingAV — plataforma quant MERVAL/ROFEX. pyRofex WS → **Postgres/Supabase** → FastAPI (`api.acaquant.com`) → **acaquant-web** Next.js en Vercel (`trading.acaquant.com`). Server en `/root/TradingAV` (Droplet DO **nyc1**, Nueva York — verificado 2026-08-13), venv en `/root/TradingAV/venv`. Vercel corre las Functions en **iad1** (Washington DC): las funciones de Next son un PROXY (las **73** rutas de `src/app/api` pegan a `api.acaquant.com` y el front NO tiene cliente de base — recontado 2026-08-31; el número queda viejo solo, ya pasó de 20 a 40 a 73), asi que la pata que paga Vercel es Vercel→Droplet, ~330km de distancia. Mover la region de Vercel NO toca el viaje Droplet→Supabase.

> **MONGO DECOMISADO (2026-06-29).** El sistema es 100% Postgres/Supabase: motores,
> jobs y API leen y escriben SQL. NO queda una sola referencia a
> Mongo en el código (`grep -rE "from core.mongo|import pymongo|MongoClient"` → 0).
> El cliente Mongo (`core/mongo.py`), `api/db.py` y el tooling Mongo fueron borrados.
> Si ves "Mongo"/"colección"/"Atlas" en algún doc viejo, es residual — la fuente de
> verdad es `sql/schema.sql` + `docs/SQL.md`.

> **⚡ EL AV AGENT → `docs/AGENT.md` (LEER al arrancar la sesión).**
> **Se rehízo ENTERO el 2026-08-24.** El agente viejo eran 37 services / 24.319
> líneas / 18 tablas / **cuatro relojes** haciendo lo mismo con distinta
> frecuencia, y el user lo resumió así: *«tiene muchas cosas positivas pero en
> su conjunto es algo totalmente inútil»*. Lo que se cambió no fueron los bugs:
> fue que **no hubiera dónde equivocarse**.
>
> **El agente vive en `agente/` (paquete raíz, al lado de `engines/` y `jobs/`).**
>
> **CUATRO tablas llevan el MODELO** del agente, y ninguna otra decide si algo
> es un problema: `habilidades` (el catálogo — y **cuándo corrió cada una**) ·
> `hallazgos` (los eventos, con `id` único) · `reincidencias` (**la que DEBE
> estar vacía**) · `acciones` (el libro: qué escribió, de qué valor a qué valor).
>
> ⚠️ El schema tiene **nueve** tablas, no cuatro: las otras cinco son de
> INFRAESTRUCTURA y ninguna nace de un detector — `latido` (el pulso del daemon,
> UNA fila) · `silenciados` (lo que una persona mandó a callar; **no borra el
> hallazgo, evita crearlo de nuevo**) · `db_peso` (la serie del tamaño de la
> base) · `avisos_dirigidos` (la bandeja hacia un usuario) · `tasa_1816` (el
> dato que trae el único cron del agente). Decir «cuatro» a secas hacía que
> `silenciados` pareciera no existir, y es la que explica por qué un problema
> real no aparece en pantalla.
>
> **Sumar una habilidad es UNA fila en `agente/catalogo.py`.** No hay que tocar
> un reloj, ni una lista de tipos, ni un mapa de dominios, ni un test que
> recuerde declararla — en el agente viejo eran CINCO listas paralelas en dos
> archivos.
>
> **UN reloj**: el daemon `agente.service` (`jobs/agente.py`). Cada habilidad
> declara su ritmo y su ventana; el motor pregunta a quién le toca. El único
> cron que queda es `agente_tasa` (cuesta créditos de 1816).
>
> **Los invariantes (`AGENT.md` §8), congelados por `tests/unit/test_agente.py`:**
> 1. Una habilidad que no corrió **no cierra nada**. Solo un resultado `ok`
>    puede cerrar por ausencia: una corrida ciega que cierra 40 problemas deja
>    el tablero en verde justo el día que menos ve.
> 2. Un hallazgo **sin `que_hacer` no se guarda** (CHECK en la base).
> 3. Todo lo que se muestra lleva **fecha y hora**.
> 4. **Solo lo cerrado POR ACCIÓN puede reincidir.** Ante la duda, por ausencia.
> 5. `agente/registro.py` es **la única puerta** que escribe hallazgos.
> 6. «¿Qué hago si no puedo mirar?» se contesta **una vez, en el motor** — los
>    detectores levantan `SinDatos` y no deciden nada.
> 7. Cada habilidad **declara su arreglo, o declara que no tiene**.
> 8. Ningún detector vive adentro de un job ajeno.
> 9. A ENCONTRÓ **solo entra lo que tiene arreglo**; lo demás es aviso y vive en AHORA.
> 10. **Un arreglo ESCRIBE.** Un botón que vuelve a mirar no es un arreglo.
> 11. Ninguna pantalla deriva nada; **ningún contador se suma en el navegador**.
> 12. **El agente no se autoevalúa**: sin votos, sin eval set, sin confianza.
>
> **TRES pantallas** (`/api/agente`, admin-only, REGLA #8 congelada por test):
> **AHORA** = hallazgos de HOY sin leer y sin resolver (informativo; el único
> botón es «leído», que **no resuelve**) · **ENCONTRÓ** = lo abierto que tiene
> arreglo · **HISTORIAL** = el libro, paginado del backend.
>
> **`docs/AGENT.md` es HISTÓRICO**: explica por qué las cosas quedaron como
> quedaron (cada §0.x es un bug real), **no cómo funciona el agente hoy**.

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

> **⚠️ EL COMANDO PARA CORRER LO ENTREGADO SÍ VA EN BLOQUE COPY-PASTE (pedido
> explícito del user, 2026-08-16).** No contradice lo de arriba: lo prohibido es
> mandar el TRABAJO como snippet para pegar (queries, diagnósticos, código). Lo que
> el user pide es que, una vez que el trabajo está commiteado, el `git pull &&
> deploy && python -m …` venga **en un solo bloque listo para copiar**, con todos
> los comandos encadenados y en orden — no desperdigado en la prosa, obligándolo a
> ir armándolo a mano. **Un bloque por entrega, al final del mensaje.**

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
  RUNBOOK, seguridad, etc.).
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

## ⚠️ REGLA #9 — La IDENTIDAD no es el NOMBRE, y dos copias necesitan ÁRBITRO

**Dos patrones, un mismo modo de falla: cuando esto se rompe NO falla nada.** No
hay excepción, no hay log, cada mitad del sistema sigue siendo coherente consigo
misma, y contesta con seguridad usando el dato equivocado. Por eso se descubren
siempre tarde y mirando una pantalla.

**(A) Emparejar registros → por FICHA, nunca por el string.** Cuando hay que
decir «este y aquel son la misma cosa» sin una clave que los una, se emparejan
por los atributos de la fuente autoritativa, no por sufijos ni prefijos. El
nombre es una convención de quien lo emitió: los tickers están topeados en **5
caracteres**, así que `AL30 → AL30D` anda *por casualidad* y `BPOA7 → BPA7D`
(se cae una letra del medio) rompe cualquier regla de string.

Usar **`core/pareo.hermanas()`**, que trae las cuatro guardas adentro: solo la
fuente autoritativa · ficha completa · tope de grupo · «no pude» ≠ «no existe».
No reimplementarlas — *emparejar mal es peor que no emparejar*.

**(B) El mismo dato en dos lugares → declarar quién manda.** Tener el dato
duplicado a veces hace falta (un blob que leen 500 lugares no se migra de un día
para el otro). Lo que no se puede es dejarlo sin árbitro y sin chequeo: declararlo
en **`core/duplicados.DUPLICADOS`** (qué dato, dónde vive cada copia, quién gana,
qué se rompe si difieren) hace que el agente lo mire todas las noches y que la
próxima divergencia dure horas y no cuatro días.

Costó tres incidentes en cuatro días: el símbolo columna-vs-blob (2 bonos enteros
en `--` teniendo el precio), el `ticker_corto` invertido por el renombre, y
`preferencia` escrita tres veces eligiendo distinto en cada una. Ver
`docs/AGENT.md` §0.y y §0.aa.

## ⚠️ REGLA #10 — LEY DE CONEXIÓN del AV AGENT: nada nuevo queda suelto

**Irrompible** (pedido del user, 2026-08-23: *«todo lo nuevo que se desarrolle
no tiene que estar suelto como si nada — acá todo se tiene que conectar»*).

En **AGENT 2.0** esta regla dejó de depender de que alguien se acuerde: la
estructura la cumple sola. Toda funcionalidad nueva del agente es **una fila en
`agente/catalogo.py`**, y esa fila obliga a declarar las cinco cosas:

1. **QUÉ mira**, en castellano — sin eso el dataclass no se construye.
2. **CUÁNDO** — su ritmo y su ventana. El motor lee de ahí; no hay reloj aparte.
3. **QUÉ arreglo** tiene cada una de sus reglas, **o que no tiene ninguno**.
   Vacío es una declaración explícita, no un olvido.
4. **DÓNDE escribe** lo que encuentra: por `agente/registro.py` y por ningún
   otro lado (un test prohíbe el resto).
5. **QUÉ HACER** con cada hallazgo — un hallazgo sin `que_hacer` no se guarda,
   y eso lo exige un CHECK de la base.

Historia de por qué hizo falta escribirla: `docs/AGENT.md` §0.cw–§0.cx.
Cómo se cumple hoy: `docs/AGENT.md` §3 y §8.

## ⚠️ REGLA #8 — Portal INVITADO (www.acaquant.com): SOLO mercado/research, nunca filtrar datos del negocio

**Bloqueante.** Conviven dos portales:

- **trading.acaquant.com** — app interna de la mesa. Usuarios de la mesa
  (admin/trader/sales/etc.), ven todo según su rol.
- **www.acaquant.com** — **portal INVITADO**: lo usa gente de **OTRO SECTOR de
  la MISMA empresa** (grupo ACA — aclaración del user 2026-07-21; NO son
  terceros, así que el contenido licenciado 1816/Reuters no sale de la
  compañía). Ven **mercado + research** (read-only). Lo unico de IA que les
  queda es el BRIEFING, que es dato de mercado — los copilotos se dieron de baja
  el 2026-08-19. **Congelado por test**: cualquier endpoint nuevo de `/api/ia`
  sin `require_admin` hace fallar `test_rbac`, asi el AV AGENT (que habla del
  estado interno del sistema) no puede quedar alcanzable por www sin que se note.

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
- **Constantes globales y feature flags** viven en `config.py` (raíz): `TICKERS_EXTRA_PRECIOS`, `GUARDRAILS_UMBRALES`, `AP5_CONCEPTOS_ACTIVO_INTEGRADO`, etc. Env vars en `.env` local / systemd unit files en el Droplet (`MANAGER_EMAILS`, `DEFAULT_ROLE`, `POSTGRES_URI`).

> Validar imports antes de pushear router/service (REGLA #1) y la regla de
> services `@cached` → ver `api/CLAUDE.md`.

## Estructura

```
core/        # infra (postgres, pg_mirror, ai + llm [gateway LLM, SIN tareas hoy], curvas_sql, dolar_sql, grupos_sql, roles_sql, series_macro, market_snapshot, websocket, rofex_session, rofex_orders_session, roles, job_runs, byma, mae, cafci, finnhub, yahoo, argentina_datos, dolar_oficial)
engines/     # motores WS → SQL (always-on L-V 13-20 UTC) — incluye motor_cedears (alimenta Scanner CEDEARs)
jobs/        # batch/cron — incluye precios_acciones_daily (alimenta scanner via SQL mercado.precios_acciones)
quant/       # cálculo puro (black_scholes, stats, curve_fit, pivot_points, rolling_stats)
agente/      # EL AV AGENT (`docs/AGENT.md`) — catalogo · registro · motor · detectores/ · arreglos · vista
api/services # lógica pura (invocada por routers y por el agente)
api/routers  # thin HTTP wrappers. manager/ es paquete de sub-routers
scripts/     # one-shot / migraciones / smoke
tests/       # pytest — TODO es unit/. ⚠️ NO hay suite de integración (ver abajo)
sql/         # schema.sql — espejo relacional Postgres/Supabase (ver "Capa SQL")
deploy/      # systemd + crontab.txt (fuente de verdad)
.claude/     # settings.json + hooks + commands + skills + agents (ver .claude/INDEX.md)
docs/        # documentación (ver "Mapa de docs" abajo)
```

## Mapa de docs — cuál leer ANTES de tocar cada dominio

**19 docs, uno por dominio.** Eran 32 el 2026-08-30. La regla que los mantiene en
19: **un dominio = un doc**. Si dos archivos explican el mismo tema, el que lee
abre uno de los dos y no sabe cuál manda — es la REGLA #9 aplicada a la
documentación, y ya pasó (el agente tenía DOS docs, la vista `/research` TRES).

`docs/ARQUITECTURA.md` es el **DOC MADRE** (arquitectura/datos/estrategia/roadmap).
Los marcados **[VIVO]** tienen changelog obligatorio: si tocás ese dominio y no
actualizaste su doc en el mismo commit, el trabajo está incompleto.

| Dominio / si vas a tocar… | Doc |
|---|---|
| Arquitectura, datos, roadmap | `ARQUITECTURA.md` (madre) |
| Qué vistas/tabs/endpoints/permisos hay (superficie completa) | `MAPA_APP.md` **[VIVO]** |
| **EL AV AGENT** | `AGENT.md` **[VIVO]** — ⚠️ **doc ÚNICO, dos partes**: A = cómo funciona (manda), B = el diario histórico (no describe el código actual). Congelado por `tests/unit/test_doc_agente.py` |
| Modelo SQL / schema | `SQL.md` (inventario + principios) + `sql/schema.sql` |
| API HTTP (contratos de payload) | `API.md` — ⚠️ el **inventario** de rutas es `MAPA_APP.md` §0, autogenerado y verificado por CI |
| **API EXTERNA para accionistas** (`/ext`) | `API_EXTERNA.md` **[VIVO]** — ⚠️ superficie hacia AFUERA: el permiso es un dato (`ext.cuentas_autorizadas`), el scope es fail-closed y **no se reusa `verify_api_key` ni `cuentas_visibles`** |
| Renta fija / curvas (incluye salud de la valuación, §9) | `RENTA_FIJA.md` |
| Renta variable: scanner local **+ feed Reuters/Eikon** | `RENTA_VARIABLE.md` **[VIVO]** |
| Vista `/research` — las 6 tabs (1816, BCRA, FRED, sensibilidad, reportes) | `RESEARCH.md` **[VIVO]** |
| Derivados: futuros · sintéticos · agro | `DERIVADOS.md` |
| Estrategia Quant (señal intradía, tab ESTRATEGIA de Trading) | `ESTRATEGIA_QUANT.md` **[VIVO]** |
| Valuaciones / PnL | `MOTOR_VALUACIONES.md` |
| Clientes: grupos · segmentación patrimonial · tablero comercial | `CLIENTES.md` |
| Vista `/aca` (resumen ejecutivo de la cartera propia) | `ACA.md` **[VIVO]** |
| Interbanking (bancos: cuentas, saldos, extractos, transferencias) | `INTERBANKING.md` **[VIVO]** |
| Postrade A3/ACyRSA (post-trade: cuentas, posiciones, garantías, márgenes) | `POSTRADE.md` **[VIVO]** — ⚠️ esta API PUEDE OPERAR (suscribe/rescata FCI, cancela órdenes): la escritura es default-deny con doble llave |
| Operación, incidentes, telemetría y guardrails | `RUNBOOK.md` |
| Seguridad, gates y credenciales | `SECURITY.md` |

Auto-generados (NO editar a mano): `HERRAMIENTAS.md`, `deploy/SISTEMA.md`.

## Mapa de la app — `docs/MAPA_APP.md` (LEER al empezar una sesión)

**Es el índice de TODA la superficie**: 19 vistas, sus tabs, sus filtros, qué endpoint
consume cada cosa, quién la ve y qué puede escribir. Leerlo AHORRA re-relevar la app
(que cuesta horas y cientos de miles de tokens). Si vas a tocar cualquier vista,
empezá por ahí.

**Se mantiene en DOS capas, a propósito:**

- **§0 AUTO-GENERADA** (inventario de endpoints + gate efectivo por router + matriz
  rol × módulo). La regenera un script determinista desde la app FastAPI montada —
  no puede mentir y no cuesta tokens:

  ```bash
  python -m scripts.gen_mapa_app          # regenera los bloques AUTOGEN
  python -m scripts.gen_mapa_app --check  # CI: falla si quedó desincronizado
  python -m scripts.gen_mapa_app --full   # las 400+ rutas, a stdout (no al doc)
  ```

  **CI corre `--check` y BLOQUEA el merge** si agregaste un router y no regeneraste.

- **El resto, A MANO.** Qué hace cada vista, sus tabs, sus filtros, las acciones de
  escritura y la sección de huecos/rarezas. Cambia despacio y ahí está el criterio.
  **Si agregás o cambiás una VISTA, una TAB o un FILTRO, actualizá su sección en el
  MISMO commit** — igual que los docs `[VIVO]`. Un endpoint nuevo lo detecta el
  script; una tab nueva no la detecta nadie.

> ⚠️ **Trampa de FastAPI que ya rompió al tooling de seguridad**: `app.routes` NO
> trae las rutas de los `include_router` — trae envoltorios `_IncludedRouter`, y las
> rutas reales cuelgan de `.original_router.routes`. Un `for r in app.routes` ingenuo
> ve **5 de 428** y no falla: devuelve poco, en silencio. Lo mismo con los gates: las
> `dependencies=` del include viven en `route.include_context`, no bajan a cada ruta.
> **RESUELTO 2026-08-19**: la técnica vive UNA sola vez en **`api/superficie.py`**
> y las tres herramientas delegan ahí. **No la reimplementes** — al medirlo,
> `audit_rbac` y `test_rbac_superficie` veían **37 de 541 rutas** y pasaban en
> verde (auditaban el 7% y afirmaban que estaba todo bien), y `gen_mapa_app`
> duplicaba el prefijo en **395 de 541 paths** (el `prefix` de un `APIRouter` ya
> viene aplicado a sus propias rutas). Ver `AGENT.md` §0.s.

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

## Comandos

```bash
uvicorn api.main:app --reload --port 8000
python -m engines.<motor> | jobs.<job> | scripts.<cmd>
ruff check . [--fix]                           # line-length=100, py312
pytest -ra                                     # toda la suite (1.550 tests, todos unit)
pytest tests/<path>::<test_name>               # single test
# ⚠️ NO existe suite de integración. `pytest -m integration` deselecciona los 1.550
# y sale en VERDE con exit 0 — «no miré nada» indistinguible de «está todo bien», que
# es justo lo que prohíbe el invariante #1 del agente. El marker queda declarado en
# pyproject (`--strict-markers` lo exige el día que se escriba la primera), y
# `tests/integration/` se borró el 2026-08-31 por estar vacío desde siempre.
python -m scripts.perf_scan [--strict]         # anti-patterns de queries
```

CI (`.github/workflows/ci.yml`): en cada push/PR a `main` corre `ruff check .` (bloqueante) + `perf_scan` (informativo, `continue-on-error`) + `pytest -ra` (solo unit). Python 3.12. No buildea el frontend.

## Frontend en repo hermano

`../acaquant-web/` (Next.js 16, deploy auto a Vercel sobre `main` — `src/proxy.ts`, no middleware). **No es submodule** — es checkout paralelo. Cambios de API con impacto en UI se editan ahí con rutas absolutas (`C:\...\acaquant-web\...`). Las routes de Next que consumen endpoints "live fallback" necesitan `dynamic = "force-dynamic"` + `revalidate = 0` + `Cache-Control: no-store` (ver `api/CLAUDE.md`).

## Tablero Comercial (lente por operador)

El Tablero Comercial se sirve SQL-only desde `api/services/comercial_sql.py` (el router `operaciones.py::_com_motor` siempre devuelve SQL; `comercial.py` quedó como helpers/funciones SQL — ver "Capa SQL"). Cruza todo por `id_cuenta`: QUIÉN (`clientes.comitentes` → operador + `nivel_1`), ACTIVIDAD (`operaciones.negocio_movimientos`/`operaciones.operaciones` → última op), TAMAÑO (`portafolio.tenencia`, `aum='si'`), operador↔usuario (`manager.manager_users`, para cuentas huérfanas). Estado comercial por días desde última op: ACTIVA ≤45 / ENFRIANDOSE 45-90 / DORMIDA / NUEVA. Agrega EN VIVO con índices (sin precompute — no se recrean rollups).

**DÍAS SIN OPERAR es auditable por fila** (2026-08-07, mismo patrón que el modal por celda de Tesorería → BANCOS): click en una fila de la tabla ESTADO COMERCIAL abre `GET /api/operaciones/comercial/analisis/detalle?id_cuenta&fecha` (`comercial_sql.detalle_ultima_op`) y muestra **cuál boleto** fija el número — todos los boletos de ese día, el historial reciente, y los que **NO** cuentan con su motivo (anulados, posteriores al corte en modo foto). El insumo es `operaciones.operaciones`: cuenta CUALQUIER boleto no anulado, sin filtro de tipo/mercado/etapa. Ese predicado vive UNA sola vez (`comercial_sql._ULT_OP_WHERE`) y lo comparten la tabla y el modal — el front no recalcula nada, así el detalle no puede contradecir al número.

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

Agregar instrumento: fila en `mercado.curvas` (vía `core/curvas_sql.py`; `data` jsonb = doc completo) + fila en `portafolio.assets` con el MISMO `ticker`. Sin el segundo no aparece en AuM/Portfolios.

**Nombres de columna (renombre 2026-08-15).** En `mercado.curvas` la PK es **`ticker`** (`AL30` — el que joinea con `portafolio.assets.ticker`) e **`instrumento`** es el SÍMBOLO DE MERCADO (`MERV - XMEV - AL30 - 24hs`, lo que se le manda a Primary). Estaban invertidos: la PK se llamaba `ticker_corto` y `ticker` guardaba el símbolo. El eje bono/letra, que ocupaba el nombre `instrumento`, fue **ELIMINADO** (1816 solo lo afirma en 3 de sus 28 curvas → quedó vacío en los 221 bonos; ningún bono cambió de pill al sacarlo). ⚠️ Al borrarlo hubo que desarmar el **paso 1 del bloque `DO`** de `sql/schema.sql`, que renombraba `instrumento → tipo_instrumento` con una condición que la base YA MIGRADA cumple: dejarlo habría hecho que el próximo `apply_schema` renombrara el SÍMBOLO DE MERCADO y la vista se quedaba sin precios en silencio. ⚠️ **El blob `data` NO se renombró**: sus claves siguen siendo `ticker_corto`/`ticker` con el significado VIEJO, y son las que leen ~500 lugares vía `core/curvas_sql.py` (que hace `SELECT data`). Por eso los `SELECT` directos llevan alias (`instrumento AS ticker, ticker AS ticker_corto`): la base quedó correcta sin tocar una línea de lógica.

> ⚠️⚠️ **Y NADIE MANTENÍA IGUALES A LOS DOS (incidente 2026-08-19).** Mientras el blob y la columna dijeran lo mismo no pasaba nada, y por eso durante cuatro días no pasó. Pero **el MOTOR escribe el precio leyendo el BLOB** (`engines/curvas.py` → `instrumento.get("ticker")`) y **la VISTA lo busca por la COLUMNA** (`LEFT JOIN market_snapshot s ON s.ticker = c.instrumento`), así que el día que divergieron el sistema quedó partido en dos mitades que no se hablan: el motor suscribía una pata, la pantalla buscaba la otra y **la fila salía entera en `--` con el precio existiendo**. Ningún detector lo veía porque el AGENTE también lee por el blob. Nada fallaba — simplemente no se encontraban. Medido: **2 de 229** (AO29 y CO32), en los dos la columna tenía la pata correcta (la D, en dólares) y el blob la vieja en pesos. **RESUELTO**: `curvas_sql._ALIAS_DEL_BLOB` suma los dos campos de símbolo al merge donde **la columna gana** (el mismo mecanismo que ya regía para el emisor y los ejes), con ALIAS porque los nombres están cruzados — sin alias, `doc["ticker"]` pasaría a valer `AL30` y los ~500 lugares que lo usan como símbolo de mercado se romperían todos juntos. **Ojo al aplicarlo: el motor arma su universo al arrancar**, así que hasta reiniciarlo (fuera de rueda) esos bonos quedan SIN suscribir — y el agente los canta como `no_suscripto`, que es justo la señal que faltaba. Migrar el blob entero sigue siendo el paso siguiente.

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

**AuM join chain**: `mercado.curvas` (campo `curva`) → `ticker` (era `ticker_corto`) → `portafolio.assets.ticker` → `unidad` → `portafolio.tenencia` (SQL, filtrar `aum='si'`).

**Enriquecimiento CER**: `motor_curvas` usa CER con settlement T-10 hábiles. Si un bono no opera un día, el último trade puede quedar con CER de ayer.

## Asistente legacy — ELIMINADO

`api/agent/` + `POST /api/chat` fueron **borrados del repo** (no existen más;
no documentar ni referenciar). El MCP server, que lo reemplazó, también se
borró el 2026-08-28 (sección siguiente): **hoy el producto no tiene asistente
conversacional de ningún tipo.**

## MCP server — ELIMINADO (2026-08-28)

`api/mcp/` (server FastMCP + provider OAuth 2.1 + discovery), sus 13 tools de
renta variable, el schema `mcp` y `docs/MCP.md` / `docs/MCP_TOOLS.md` **se
borraron**. No documentar ni referenciar: no existen.

**La secuencia fue apagar y después borrar**, y vale como método: primero se le
sacaron las env vars al `.env` del Droplet y se reinició la API (reversible en
una línea), se verificó contra el uvicorn que `/mcp` diera **404** y
`/api/health` **200**, y recién con eso borrado el código.

**Por qué se fue**: no lo consumía nadie —ninguna vista, job ni motor— y no era
gratis tenerlo. Con `MCP_JWT_SECRET` seteada quedaba expuesto el provider OAuth
entero, y `/oauth/register` y `/oauth/token` estaban en la allowlist de **BYPASS
de Cloudflare Access**, o sea alcanzables **sin autenticar**. Eso ya había
causado un problema real: cada request disparaba `CREATE SCHEMA/TABLE` + 2
`DELETE` sobre el pool web que sirve a la mesa.

**Lo que sobrevive**: `api/services/rv_motor.py::get_correlation_matrix()`, que
NO era MCP-only — lo usa `day_trading` para `GET /api/scanner/companeros/{ticker}`.
Sus dos hermanas (`get_trade_analysis`, `get_book_analysis`) se fueron con el MCP.

⚠️ **PENDIENTE que este repo no puede resolver**: sacar la app
`acaquant-mcp-bypass` del panel de **Cloudflare Access**. Sigue dejando esos 5
paths sin login (hoy contra 404s) y ocupa **5/5 destinations**, la cuota entera,
por una superficie que ya no existe.

## Capa SQL — Postgres/Supabase (ÚNICA base; Mongo decomisado 2026-06-29)

**Postgres/Supabase ES el sistema.** Todo lee y escribe SQL: motores, jobs y
API. Mongo fue decomisado por completo — no hay dual-run, ni flags de
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

Push a `main` → Vercel auto-deploya acaquant-web. Backend, **un solo comando en el Droplet**: `cd /root/TradingAV && git pull && bash deploy/deploy.sh` (pull → `apply_schema` → **restart de api.service Y NADA MÁS** → smoke a `/api/health`, cortando al primer fallo; `--sin-schema` saltea el schema). También existe la skill `/deploy` como wrapper del procedimiento. Motores de mercado los controla cron (start/stop L-V). Cron fuente de verdad: `deploy/crontab.txt`.

> **⚠️ EL DEPLOY NO REINICIA LOS MOTORES** (regla del user, 2026-08-18: *«no puedo
> estar reiniciando todos los motores en vivo… antes era git pull y luego reinicio
> la API, con eso estamos»*). Reiniciar un motor EN RUEDA corta el feed de precios
> de la mesa, y **el 95% de los deploys tocan la API y no los motores**: pagar ese
> corte en cada entrega es puro costo. `deploy.sh` llamaba a `restart_all.sh`, que
> hacía `try-restart` de todos los motores activos — eso se sacó; la skill
> `/deploy` ya decía «no toca motores» y el script se había desviado.
>
> El script **detecta** si el código nuevo tocó `engines/`, `core/`, `quant/` o
> `config.py` y lo **avisa nombrando los motores activos con el comando exacto**,
> pero **no los reinicia**: enterarse tres días después de que un motor corre
> código viejo es peor que el aviso, y reiniciar en rueda es una decisión de la
> mesa, no un efecto secundario. Para hacerlo igual: `--con-motores`, o
> `systemctl try-restart motor_X.service` a mano **fuera de rueda** (no 13-20 UTC
> L-V). `deploy/restart_all.sh` queda para ese caso explícito.
>
> **Al entregar trabajo (REGLA #0), el bloque copy-paste NUNCA lleva
> `--con-motores`** salvo que el cambio toque un motor y el user lo pida.

> **Todo lee SQL.** Tenencias/catálogo en `portafolio.tenencia`/`portafolio.assets`;
> el join de instrumentos + normalización de assets lo hace `api/services/titulos_flujos.py`
> (lee `portafolio.assets`). Las viejas colecciones espejo `*API` y los syncs Mongo→Mongo
> (`sync_api_copies`, `api_migrate`) ya no existen.

Jobs críticos diarios: `jobs.bcra --today` (22 UTC L-V, pide hoy+21d para CER forward), `jobs.argentina_datos` (12 UTC, RiesgoPais/IPC/REM), `jobs.portafolio_backfill --diario` (11 UTC L-V, writer de tenencias SQL — reemplazó a `jobs.aum`/Mongo, eliminado), `jobs.assets_autofill` (11:40 UTC L-V, completa en `portafolio.assets` lo que se deriva de la `unidad` — FINANCIAMIENTO, FCI y el TICKER del resto del catálogo; solo campos vacíos, nunca pisa la carga manual. **Excepción heurística: `financiamiento_clase`** — el `clase_activo` HD/DL de los pagarés NO sale de la unidad sino del NOMINAL de la última tenencia (≤ **5.000.000** → HD, > → DL), porque la vista FINANCIAMIENTO no puede graficar juntas dos escalas tan distintas y clasificar 2.000 assets a mano no era viable. Es un bootstrap: se corrige en Manager → ASSETS y el job no vuelve a opinar sobre lo corregido. Backfill = el mismo comando, `--regla financiamiento_clase`. **Regla `herencia` (2026-08-12) — el REBAUTIZO de Aunesa**: un cambio normativo reemitió los instrumentos con otro id de especie, y como `unidad` es la PK de `assets` la renombrada (`[28902] CAFCI1910-6461 - …` donde antes decía `[6461] …`) entra como asset NUEVO: CARTERA y TICKER se derivan solos, pero EMISOR/CALIFICACIÓN/INSTRUMENTO/CLASE_ACTIVO/CÓDIGO CNV/FEE ADMIN —lo que carga la mesa a mano— nace VACÍO y la carga vieja queda pegada a una unidad que NO se puede borrar (la tenencia histórica la referencia). La regla copia esos campos entre unidades que son el MISMO instrumento, en las DOS direcciones. Identidad = **código CAFCI** (lo que el rebautizo no toca) y, de respaldo, el **nombre del fondo**. Tres cosas la hacen segura: los donantes tienen que ESTAR DE ACUERDO (dos valores distintos → no escribe, reporta la divergencia — eso es lo que sostiene la clave por nombre), nunca pisa lo cargado, y hoy corre **solo para FCI**: fuera de FCI la identidad sería el ticker, que todavía no está medido, así que el job CUENTA cuántos assets completaría (stat `herencia_no_fci_receptoras`) sin escribir hasta poner `HEREDAR_NO_FCI=True`. Ver qué copiaría: `--regla herencia --dry`. **Regla `especies` (2026-08-15) — los DOS símbolos de mercado**: `assets.instrumento` es lo que el motor de portfolio le SUSCRIBE a Primary (`engines/_universo_portfolio.py`), o sea de dónde sale el `last_price` de la tenencia, y se venía cargando A MANO — con lo cual el catálogo de market data quedó desparramado en tres lugares que se contradicen (assets, `mercado.curvas` y el universo real de Primary). Ahora `assets` es un DERIVADO de **`mercado.especies`**, el único lugar donde vive la relación ticker → sus patas: la regla relaciona por **TICKER** y baja las DOS, `instrumento` = pata en PESOS e **`instrumento_usd`** = pata en DÓLARES (MEP; el cable NO — es otra cosa y mezclarlo volvería a esconder cuál es cuál). Dentro de cada pata gana la `es_default` y después 24hs sobre CI, que es donde hay liquidez y por lo tanto precio. **No puede cambiar una valuación**: rige el invariante del job (nunca pisa), así que lo cargado hoy sigue igual y el motor suscribe exactamente lo mismo; un símbolo distinto al de especies se REPORTA como conflicto — que es justo el listado que no existía. Los dos campos siguen siendo editables en Manager → TÍTULOS · ASSETS porque el catálogo de Primary a veces está viejo y ahí manda la mesa), `jobs.cleanup_curvas` + `jobs.cleanup_futuros_dlr` (12:30 UTC L-V, antes de motores), `jobs.snapshot_cierre` (20:25 UTC L-V, post-cierre — lee `mercado.market_snapshot` y persiste cierre por bono en `mercado.snapshots_cierre`), `jobs.negocio_movimientos` (cada hora 15-22 UTC L-V, pega a Aunesa `consolidadosGenerales`, parsea/categoriza/agrupa por boleto y persiste idempotente en **SQL `operaciones.negocio_movimientos`** para la vista `/operaciones/negocio`), `jobs.guardrails` (20:45 UTC L-V, post-cierre — invariantes de sanidad de datos; report en el log + stat en `manager.job_runs`), **`jobs.validar_instrumentos` (23:00 UTC L-V, 2026-08-15)** — dos pasos en uno. **(1) VIGENCIA**: `portafolio.assets` no tenía forma de decir si un título sigue existiendo, y un papel que amortizó NO se puede borrar (la tenencia histórica lo referencia) → columna **`vigente`** + `vigencia_motivo`/`vigencia_at`. El job la apaga cuando la fecha de vencimiento ya pasó (de `assets.vencimiento` o `mercado.curvas.fecha_vencimiento`), con motivo `vencido`, y es REVERSIBLE: si la fecha estaba mal cargada y se corrige, el título vuelve solo. **Nunca pisa una marca humana** — tildar/destildar en Manager → TÍTULOS · ASSETS sella `vigencia_motivo='manual'` y el job deja de opinar sobre esa fila (sin ese sello se la daría vuelta esa misma noche). **(2) VALIDACIÓN**: cruza cada símbolo de `mercado.especies` contra el universo real de Primary — el que existe queda marcado **`validado`** con su fecha, y **el que no existe se BORRA**. Borrar es lo correcto porque una especie ES, por definición, una pata que cotiza: si Primary no la lista no es una pata, es basura que reaparece en cada reporte. Nada se pierde — `scripts/sembrar_especies` reconstruye la tabla desde el master + Primary, así que el día que exista de verdad vuelve sola. Y por eso mismo **el seeder ya NO siembra el símbolo del master cuando Primary no lo lista** (esa rama existía por la hipótesis de que el discovery quedaba viejo; el 2026-08-15 se lo refrescó y los faltantes quedaron idénticos — no era el catálogo atrasado): si siguiera, recrearía cada noche lo que el job acaba de borrar. **Lo que IMPIDE suscribirse a un símbolo muerto es otra cosa: `core/instrumentos_validos`, aplicado en `core/websocket.py::agregar_suscripciones`** — el ÚNICO punto por el que pasan todas las suscripciones de todos los motores, así que un motor nuevo lo hereda sin escribir una línea y no se puede saltear por olvido. Las dos leen la MISMA fuente (`manager.pyrofex_instruments`) para que no puedan contradecirse. **Degradación elegida a propósito**: si Postgres no responde o el catálogo tiene menos de 100 símbolos, `validos()` devuelve `None` y NO se filtra nada — filtrar de más deja la mesa sin precios, no filtrar deja pasar símbolos muertos como siempre; ante la duda, lo segundo. Por eso mismo el job ABORTA sin marcar si el catálogo está vacío. El orden de los dos pasos importa: sin el 1, cada bono amortizado quedaría marcado como inválido todos los días), **`jobs.ficha_1816` (22:30 UTC L-V, 2026-08-15)** — **1816 es la FUENTE DE VERDAD del EMISOR**. Se cargaba a mano y a mano el mismo emisor se escribe de N formas (`BCO.COMAFI`/`B. Comafi`, `BANCO HIPOTECARIO`/`B. Hipotecario`): medido, **74 strings para 67 emisores reales** + 43 bonos sin emisor. No es cosmético — cualquier cosa que AGRUPE por emisor cuenta mal, y no se nota porque las dos filas existen y suman bien por separado. 1816 publica UN nombre por emisor y cubre **140/140 de nuestros corporativos** (verificado). **Acá SÍ se pisa**, al revés que `assets_autofill`: completar es distinto de ESTANDARIZAR (escribir `BCO.COMAFI` en vez de `Banco Comafi` no es criterio, es tipeo), y el diag mostró **0 casos** donde 1816 diga un emisor distinto de verdad. Escribe en las DOS tablas donde vive un emisor (`mercado.curvas` y `portafolio.assets`) — si no, agrupar daría distinto según de dónde se lea. **NO pega a la API**: lee `research.mkt_1816_instrumentos`, que llena `jobs.mercado_1816_discovery --apply --catalogo` (recorre las 28 curvas de 1816, 1 crédito c/u → 887 instrumentos; el WATCH sigue curado porque ESE es el que cuesta créditos de series todos los días). La **moneda divergente** (`monedaDenom` de 1816 vs `curvas.moneda_eje`) se REPORTA y no se escribe: un emisor mal escrito es un reporte feo, una moneda mal puesta es plata mal contada), **`jobs.tamar_1816` (cada 30' de 10 a 17 ART = `0,30 13-19` + `0 20` UTC L-V, 2026-08-16)** — los 18 bonos con pata TAMAR **no tenían ningún cálculo**: caen en el `else` de `engines/curvas.py` (solo duration). No estaban mal valuados, estaban SIN valuar — y de un TAMAR lo que la mesa mira es el **MARGEN sobre la TAMAR**, que ni existía en el sistema. **No los valuamos nosotros**: un TAMAR es una nota de tasa PROMEDIO (promedia la TAMAR de bancos privados entre T−10 hábiles de emisión y T−10 del vencimiento, + margen de licitación) y medido contra la planilla de la mesa 1816 da lo mismo (TXMD9: TEA 38,62% vs 38,55%, margen 9,73% vs 9,71%, MISMO precio 84,10) → la mesa ya valida contra ellos. **De yapa resuelve los DUALES**: 1816 publica cada pata como TICKER APARTE (`TXMD9 @CER` / `@TAMAR`) y rinden distinto de verdad — 6,82% real por CER contra 38,62% nominal por TAMAR, ~2.900 bps. Hasta hoy la vista mostraba **el mismo número en las dos tablas** porque `mercado.market_snapshot` tiene una fila por símbolo y por lo tanto UNA sola TEA; por eso `mercado.tamar_1816` tiene PK **(ticker, pata)**. **NO escribe en `market_snapshot`** (eso es del motor: Primary, live, 5s; esto es 1816/BYMA con delay): se juntan en la LECTURA (`curvas_vista`) y cada fila viaja con `pata`/`tea_fuente`/`tea_fecha`/`margen`. Cuatro trampas ya pagadas: (1) `indicadores` sin `fechaOperacion` usa HOY y un domingo devuelve todo `null` → el job manda fecha explícita y retrocede hábil por hábil; (2) el campo del margen es **`spread`** (`margen`/`margin`/`spreadTamar` dan HTTP 400 — se probaron de a uno porque la API rechaza la llamada entera si un campo no existe); (3) escala **FRACCIONES** (0.0973 = 9,73%, igual que `market_snapshot.tea` — no convertir); (4) la grafía del ticker se DERIVA de `research.mkt_1816_instrumentos`, no se concatena (en TTD26/TTS26 el catálogo dice `@TASA FIJA` y la denominación `@BONCAP`, y solo la segunda trae datos → se piden las dos). De los 9 corporativos con pata TAMAR 1816 solo cubre ZPC1O; los otros 8 + CO2D7 quedan sin tasa y sin margen (celda vacía) y el job lo CUENTA en `sin_dato`. Doc: `docs/RENTA_FIJA.md` paso 18), `jobs.research_mail` (cada 30' 10-14 UTC L-V, ingesta el mail 1816 a `ia.research`), `jobs.mercado_1816_series` (22 UTC L-V, append diario a `research.mkt_1816_series`), `jobs.bcra_research` (12/16/20/23 UTC L-S).

Dólar oficial: única fuente live es `valuaciones.dolar_oficial_live` (feed MAE mayorista UST$T plazo 000, script local en PC oficina). Histórico/anchors (7d/MTD/YTD del watchlist `/argy`) deshabilitado hasta que MAE acumule histórico suficiente. Para series macro (`serie_macro` con `dolar_oficial`/`dolar_mayorista`) usar `macro.series_macro` clave DOLAR (BCRA A3500 fixing diario).
