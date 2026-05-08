# .claude/ — índice

Este directorio contiene **commands** (slash) y **skills** (procedimientos) que Claude Code usa durante una sesión. Todo lo que vive acá se versiona en el repo y se comparte con quien abra el proyecto.

Claude descubre commands/skills automáticamente, pero vos (humano) necesitás este índice.

## Slash commands

Se invocan tipeando `/nombre` en el chat.

| Comando | Qué hace |
|---|---|
| `/perf` | Corre `scripts.perf_scan --strict` y resume findings agrupados por código (PERF001…4). No aplica fixes. |
| `/motor-status` | Estado systemd + última actividad Mongo de los 8 motores de mercado. Respeta la ventana Atlas. |
| `/deploy` | Push a main + pull + `systemctl restart api.service` en el Droplet. Cada paso destructivo pide confirmación. |

## Skills (procedimientos estructurados)

Claude los aplica automáticamente cuando la tarea matchea la descripción del skill.

| Skill | Cuándo |
|---|---|
| `add-bono` | Agregar un instrumento nuevo (tasa_fija / CER / soberano) al sistema. Cubre seed en `Trading.Curvas` + registro en `Valuaciones.Assets` + validación en motor + frontend. |
| `add-endpoint` | Crear un endpoint REST end-to-end: service puro → router thin → proxy Next + RBAC + registro en el agente si corresponde. |
| `add-job` | Crear un job batch/cron: patrón `JobRunLogger`, entrada en crontab, encadenamiento con `sync_api_copies`, índices. |
| `debug-motor` | Playbook de troubleshooting para cualquier motor: status systemd, journalctl, queries a Mongo, recovery. |

## Agregar nuevos

- **Commands**: `.claude/commands/<nombre>.md` con frontmatter `description`. El cuerpo es el prompt que Claude ejecuta al invocar `/nombre`.
- **Skills**: `.claude/skills/<nombre>.md` con frontmatter `name` + `description`. Cuerpo = instrucciones paso a paso que Claude sigue.

Criterio para sumar: **solo si la fricción de no tenerlo es real** (workflows repetitivos con pasos fáciles de olvidar). 3-4 skills bien mantenidos > 10 mediocres.

## No tracked

`settings.local.json` tiene config personal por máquina (permisos auto-aprobados) y está en `.gitignore`.
