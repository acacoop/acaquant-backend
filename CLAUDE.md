# CLAUDE.md

Guía para Claude Code en este repo. **Solo lo que aplica a TODA sesión.** Lo que aplica a un dominio
vive en `.claude/rules/<dominio>.md` y se carga solo al tocar sus archivos; lo histórico (por qué
algo quedó así) vive en `docs/`. Un test (`tests/unit/test_contexto_claude.py`) fija el techo de
este archivo: 200 líneas, 16 KB, sin fechas. Si algo no entra, no se achica la letra: se mueve a su
regla.

## Remotes, auth y autor de los commits

- `origin` → `github.com/NMolloAV/acaquant-backend` (PRINCIPAL; el Droplet tira de acá) · `org` →
  `github.com/acacoop/…` (espejo). **`git pushall`** = push a los dos. Misma cuenta **NMolloAV** en ambos
  (Git Credential Manager): no hay que loguearse.
- **No pisar `user.name`/`user.email` al commitear.** Vercel bloquea el deploy del front si el autor
  no es miembro del proyecto, **sin error visible**: el código está en `main`, compila, y la app no
  cambia. Si algo no aparece, mirar Deployments en Vercel antes que el código.

## Qué es

TradingAV — plataforma quant MERVAL/ROFEX. pyRofex WS → **Postgres/Supabase** → FastAPI
(`api.acaquant.com`, Droplet DO nyc1, `/root/TradingAV`) → `acaquant-web` Next.js en Vercel
(`trading.acaquant.com`; sus rutas `src/app/api` son PROXY al backend). **100% SQL: Mongo no existe
más.** Doc madre: `docs/ARQUITECTURA.md`.

## Contexto que se carga solo (no hace falta leerlo de antemano)

| Al tocar… | Se carga |
|---|---|
| `api/`, `engines/`, `jobs/`, `scripts/` | el `CLAUDE.md` de esa carpeta (REGLA #1 vive en `api/CLAUDE.md`) |
| `agente/` · `asistente/`, `core/modelos.py` | `rules/agente.md` — el AV AGENT · `rules/asistente.md` — EL ASISTENTE (un agente = un archivo) |
| `api/**`, `gen_mapa_app` | `rules/api-superficie.md` — `MAPA_APP.md` y la trampa de `app.routes` |
| `deploy/**` | `rules/sistema-deploy.md` — `SISTEMA.md`, el deploy NO reinicia motores |
| operaciones / comercial | `rules/operaciones.md` — HOT/COLD, arancel vs bruto, tablero |
| curvas / renta fija | `rules/curvas.md` — shape de flujos, símbolo vs blob, breakevens, forwards |
| PnL / tenencias / assets | `rules/valuacion.md` — divisor por cartera, join chain de AuM |
| `jobs/**`, crontab | `rules/jobs.md` — los jobs críticos, sus horas y sus trampas |

## ⚠️ REGLA #0 — Cómo entregar trabajo (LEER PRIMERO)

**Claude no tiene ni va a tener acceso al Droplet.** Todo lo que corre en prod se entrega como
código en el repo: `scripts/<x>.py`, `jobs/<x>.py`, endpoint en `api/` → commit + push a `main` → el
user hace `git pull` y `python -m scripts.<x>`.

- Nada de queries/snippets/diagnósticos para pegar en el chat (la consola web de DO lo hace doloroso):
  **un diag de 5 líneas igual va a `scripts/diag_*.py`.** Cero «probá esto, si no esto otro»: una
  solución por vez, commiteada. Única excepción: UNA línea trivial (`systemctl status x`) inline.
- **El comando para correr lo entregado SÍ va en un bloque copy-paste**, uno solo, al final del
  mensaje, con `git pull && deploy && python -m …` encadenados. **Nunca con `--con-motores`** salvo
  que el cambio toque un motor y el user lo pida.

## ⚠️ REGLA #2 — NUNCA ASUMIR: verificar antes de afirmar o codear

Claude no ve prod ni la base. Afirmar hechos sobre los datos sin medir y codear en función de eso es
la causa #1 de romper cosas.

- Prohibido «X es minoría», «probablemente el campo…». Distinguir SIEMPRE, en voz alta:
  «Hipótesis (sin medir): …» vs «Verificado: …».
- Ningún código cuya CORRECTITUD dependa de una suposición no verificada. Si hace falta un dato de
  prod: diag read-only (`scripts/diag_*.py`), el user lo corre, devuelve el número, y recién ahí se
  codea. Si no se puede medir, decirlo y esperar.
- Optimizar = medir primero (`EXPLAIN` / timing), después tocar.

## ⚠️ REGLA #3 — TODO cambio lleva EXPLICACIÓN EJECUTIVA

Sin excepción, incluidos diags y docs. Dos partes, en lenguaje claro:

```
📋 Qué soluciona: el problema concreto / la pregunta que responde.
📋 Qué genera:    qué cambia desde ahora, qué hay que correr, qué se gana, qué riesgo trae.
```

El user es PM y necesita el «qué» y el «para qué» sin leer el diff. Sin esto, el cambio está INCOMPLETO.

## ⚠️ REGLA #4 — Backfills/migraciones JAMÁS escanean prod a ciegas

Ningún backfill, migración o `--full` sin TODO esto: **scopeado** (solo las filas que cambian, por
índice) · **batcheado + throttle** (lotes con `sleep`) · **costo medido ANTES** (`EXPLAIN`, contar
afectadas) · **vía `run_job.sh`** (lock + timeout) y **fuera de rueda** (no 13-20 UTC L-V) ·
**idempotente**. Si dudás del volumen, no lo corras: medí. Skill `safe-backfill`.

## ⚠️ REGLA #5 y #6 — Minimalismo: se BORRA lo cumplido, se pide UNA vez

Cada `diag_*`/`fix_*`/`backfill_*`/`seed_*` one-shot se elimina cuando el tema cierra. Queda lo
recurrente + lo referenciado por skills/CI/cron. En `docs/`, lo point-in-time y lo superseded se
borra o se consolida: **un dominio = un doc**. Ante la duda, preguntar «¿lo borro?», no acumular.

**REGLA #6 — Credencial/acceso faltante: se pide UNA vez.** Si hace falta algo que solo el user puede
crear (env var, rol de Postgres), se dice una vez, se marca PENDIENTE, y se sigue con lo demás.

## ⚠️ REGLA #7 — IR MÁS ALLÁ: enseñar y proponer, no solo ejecutar

El user aprende técnica solo a través de Claude. En cada trabajo, además de lo pedido: detectar lo
que él no sabe pedir (queries ineficientes, modelado, deuda, riesgos de datos), explicar el porqué en
lenguaje gerencial + técnico, y proponer estructura nueva, no solo optimizar. No reemplaza a #2 ni #3.

## ⚠️ REGLA #8 — Portal INVITADO (www.acaquant.com): SOLO mercado/research

Dos portales sobre el mismo deploy. **trading.acaquant.com** = la mesa, ve todo según rol.
**www.acaquant.com** = invitado (otro sector del grupo ACA): mercado + research read-only, y de IA
solo el BRIEFING. **Nada del negocio de la mesa** (portfolios, operaciones, manager, back-office,
clientes, AuM, P&L, contrapartes, ACA) puede quedarle accesible, jamás.

- El backend fuerza rol `invitado` con el header `x-acaquant-portal: guest`
  (`api/auth.py::is_guest_portal` + `core.roles.INVITADO_MODULES`). Agregar algo a
  `INVITADO_MODULES` es una decisión de SEGURIDAD.
- **Default-deny**: ante la duda, no exponer. Congelado por test: un endpoint nuevo de `/api/ia`
  sin `require_admin` rompe `test_rbac`.

## ⚠️ REGLA #9 — La IDENTIDAD no es el NOMBRE, y dos copias necesitan ÁRBITRO

Dos patrones con el mismo modo de falla: **cuando se rompe, no falla nada** — cada mitad es
coherente consigo misma y contesta segura con el dato equivocado.

- **(A) Emparejar registros → por FICHA, nunca por string.** Los tickers están topeados en 5
  caracteres: `AL30→AL30D` anda por casualidad, `BPOA7→BPA7D` rompe cualquier regla. Usar
  **`core/pareo.hermanas()`** (trae las guardas adentro); no reimplementar — emparejar mal es peor
  que no emparejar.
- **(B) El mismo dato en dos lugares → declararlo en `core/duplicados.DUPLICADOS`** (qué dato, dónde
  vive cada copia, quién gana). El agente lo controla de noche.

Historia: `docs/AGENT.md` §0.y y §0.aa.

## ⚠️ REGLA #10 — LEY DE CONEXIÓN del AV AGENT: nada nuevo queda suelto

Toda funcionalidad nueva del agente es **una fila en `agente/catalogo.py`**, que obliga a declarar
qué mira, cuándo, qué arreglo tiene (o que no tiene), dónde escribe (solo por `agente/registro.py`)
y qué hacer con cada hallazgo. Detalle e invariantes: `.claude/rules/agente.md` y
`docs/AGENT.md` §8. Skill `add-habilidad`.

## Reglas que rompen todo si se olvidan

- **`python -m <módulo>` desde la raíz siempre.** `python engines/x.py` falla.
- **Conexión SQL**: pool singleton `core.postgres.get_pool()` (no cerrarlo).
- **Capas**: `core/` no importa nada del proyecto. `engines/` y `jobs/` usan `core/` + `quant/`.
  `api/services/` es puro (sin FastAPI); `api/routers/` solo HTTP.
- **Validar imports antes de pushear** router/service (REGLA #1, `api/CLAUDE.md`): un import roto
  tumba TODA la API. El hook de push lo chequea.
- **Commits**: `feat/fix/docs/refactor(scope): mensaje` en español.
- **Constantes y feature flags** en `config.py`; env vars en `.env` / unit files.
- **Docs [VIVO]** (tabla de abajo): si tocás el dominio y no actualizás su doc en el mismo commit,
  el trabajo está incompleto.

## Estructura

```
core/ infra (postgres, pg_mirror, *_sql, market_snapshot, websocket, roles)   quant/ cálculo puro
engines/ motores WS → SQL (L-V 13-20 UTC)   jobs/ batch/cron (JobRunLogger, run_job.sh, crontab)
agente/ EL AV AGENT (catalogo · registro · motor · detectores/ · arreglos · vista)
api/ services (lógica pura) + routers (HTTP) · auth.py · superficie.py    scripts/ one-shot, diags
sql/ schema.sql   deploy/ systemd + crontab   tests/ unit   evals/ ruteo esperado   .claude/ INDEX.md
```

## Mapa de docs — cuál leer ANTES de tocar cada dominio

Un dominio = un doc. `docs/ARQUITECTURA.md` es el doc madre. Los **[VIVO]** tienen changelog
obligatorio en el mismo commit.

| Dominio | Doc |
|---|---|
| Arquitectura, datos, roadmap | `ARQUITECTURA.md` |
| Vistas / tabs / endpoints / permisos (superficie completa) | `MAPA_APP.md` **[VIVO]** — §0 autogenerado y verificado por CI |
| EL AV AGENT · EL ASISTENTE (LangGraph) | `AGENT.md` **[VIVO]** (parte B es diario) · `AvAgentAI.md` **[VIVO]** |
| Modelo SQL · API HTTP (payloads) | `SQL.md` + `sql/schema.sql` · `API.md` |
| API EXTERNA para accionistas (`/ext`) | `API_EXTERNA.md` **[VIVO]** — fail-closed, no reusa `verify_api_key` |
| Renta fija / curvas · Renta variable + Reuters · FCI | `RENTA_FIJA.md` · `RENTA_VARIABLE.md` · `FCI.md` **[VIVO]** |
| Vista `/research` | `RESEARCH.md` **[VIVO]** |
| Derivados · Valuaciones / PnL | `DERIVADOS.md` · `MOTOR_VALUACIONES.md` |
| Clientes (grupos · segmentación · tablero comercial) | `CLIENTES.md` |
| Vista `/aca` | `ACA.md` **[VIVO]** |
| Interbanking | `INTERBANKING.md` **[VIVO]** |
| Postrade A3/ACyRSA (⚠️ PUEDE OPERAR: escritura default-deny, doble llave) | `POSTRADE.md` **[VIVO]** |
| Operación e incidentes · Seguridad y credenciales | `RUNBOOK.md` · `SECURITY.md` |

Auto-generados (no editar a mano): `HERRAMIENTAS.md`, `deploy/SISTEMA.md`, `MAPA_APP.md` §0.

## Comandos

```bash
uvicorn api.main:app --reload --port 8000
python -m engines.<motor> | jobs.<job> | scripts.<cmd>
ruff check . [--fix]                    # line-length=100, py312
pytest -ra                              # toda la suite (unit)
python -m scripts.perf_scan [--strict]  # anti-patterns de queries
python -m scripts.gen_mapa_app --check | gen_sistema --check   # CI bloquea si el AUTOGEN quedó viejo
```

CI en cada push/PR a `main`: `ruff` (bloqueante) + `perf_scan` (informativo) + `pytest -ra`. **No hay
suite de integración**: `pytest -m integration` no selecciona nada y sale en verde — no significa nada.

## Capa SQL, deploy y lo que ya no existe

- **Postgres/Supabase es el sistema.** Un módulo `*_sql.py` por dominio; escrituras vía `core.pg_mirror`;
  el motor de PnL es lógica pura sobre dicts. `sql/schema.sql` puede ir adelante de la base real:
  `scripts/apply_schema.py` crea lo que falte. Ref: `docs/SQL.md`.
- **Deploy backend**: `cd /root/TradingAV && git pull && bash deploy/deploy.sh` (pull → schema → restart
  de `api.service` **y nada más** → smoke). Motores: los maneja cron; reiniciarlos en rueda es decisión
  de la mesa. Push a `main` deploya el front en Vercel solo.
- **EL ASISTENTE** (`asistente/`): grafo LangGraph de agentes por tema, tab LAB del AV AGENT. Doc única:
  `docs/AvAgentAI.md`. Alcance por `ASISTENTE_CUENTAS` (`.env`, fail-closed).
- **Frontend**: repo hermano `../acaquant-web/` (checkout paralelo). Proxies de data live: `force-dynamic` + `revalidate = 0`.
