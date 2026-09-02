# .claude/ — índice

Todo lo que vive acá se versiona en el repo y se comparte con quien abra el
proyecto (excepto `settings.local.json`). Claude descubre
commands/skills/agents/hooks automáticamente; este índice es para vos (humano).

## Contexto (CLAUDE.md)

- `CLAUDE.md` (raíz) — contexto project-wide.
- `api/CLAUDE.md`, `engines/CLAUDE.md`, `jobs/CLAUDE.md`, `scripts/CLAUDE.md`
  — contexto por subdirectorio; se carga solo al trabajar en esa carpeta.

## Slash commands (`/nombre`)

| Comando | Qué hace |
|---|---|
| `/perf` | `scripts.perf_scan --strict` + resumen de findings por código PERF001…4. |
| `/motor-status` | Estado systemd + última actividad en Postgres de los motores. |
| `/deploy` | Push a main + pull + `systemctl restart api.service` en el Droplet. |
| `/sistema` | Regenera + muestra el plano único del sistema (`deploy/SISTEMA.md`) desde systemd + crontab. |

## Skills (procedimientos — Claude los aplica cuando la tarea matchea)

| Skill | Cuándo |
|---|---|
| `add-bono` | Agregar un instrumento nuevo (tasa_fija / CER / soberano). |
| `add-endpoint` | Crear un endpoint REST end-to-end (service → router → proxy + RBAC). |
| `add-job` | Crear un job batch/cron (`JobRunLogger`, `run_job.sh`, crontab, índices). |
| `debug-motor` | Troubleshooting de cualquier motor (systemd, journalctl, Postgres). |
| `backfill-mes` | Rehacer el snapshot de AuM de un mes (delete + backfill + fix precios). |
| `audit-obsoleto` | Auditar scripts/tests/docs obsoletos → listas BORRAR/REVISAR/MANTENER. On-demand. |
| `safe-backfill` | Backfill/migración/`--full` que NO tira el CPU: medir → scopear → batchear+throttle → run_job → fuera de rueda (REGLA #4, post-incidente 2026-06-04). |
| `index-health` | Auditar que los índices REALMENTE se usen (explain, no index_information). Caza el trap partial→COLLSCAN. |
| `security-review` | Checklist de seguridad antes de exponer endpoint/auth: RBAC, secretos, CF Access. |
| `add-habilidad` | Agregar una habilidad al AV AGENT: las siete piezas (catálogo, detector, fuente, test, diario, cita, conteo) y aviso vs. trabajo (REGLA #10). |

## Agents (subagentes — corren en contexto limpio)

| Agent | Cuándo |
|---|---|
| `pre-deploy-check` | Validaciones pre-deploy (imports + ruff + perf_scan + tests) → veredicto GO / NO-GO. |

## Hooks (definidos en `.claude/settings.json`, scripts en `.claude/hooks/`)

| Hook | Qué hace |
|---|---|
| PreToolUse · `git push` | `check_imports.sh` — corre `from api.main import app` y **BLOQUEA** el push si no importa (enforcement de la REGLA #1). |
| PreToolUse · `git push` | `check_agente.py` — si el push toca `agente/`, `docs/AGENT.md`, el registro de diagnóstico o el crontab, corre los tests del agente y **BLOQUEA** si están rojos (REGLA #10). |
| PostToolUse · `Write\|Edit` | `ruff_check.sh` — `ruff check` sobre el `.py` editado, informativo (no bloquea). |
| PostToolUse · `Write\|Edit` | `sistema_drift.sh` — drift de docs autogenerados: `deploy/systemd/*`/`crontab.txt` → `SISTEMA.md`; `scripts/*.py` → `docs/HERRAMIENTAS.md`. Avisa si quedaron desincronizados (no bloquea). |

## settings

- `settings.json` — **versionado** (team-wide): permisos durables y seguros + los hooks.
- `settings.local.json` — config personal por máquina (permisos auto-aprobados). Gitignoreado.

## Agregar nuevos

- **Command**: `.claude/commands/<n>.md`, frontmatter `description`. Cuerpo = prompt.
- **Skill**: `.claude/skills/<n>.md`, frontmatter `name` + `description`. Cuerpo = pasos.
- **Agent**: `.claude/agents/<n>.md`, frontmatter `name` + `description` (+ `tools`, `model`).
- **Hook**: editar `.claude/settings.json` → `hooks`; el script va en `.claude/hooks/`.

Criterio para sumar: **solo si la fricción de no tenerlo es real**. Pocos y
bien mantenidos > muchos mediocres.
