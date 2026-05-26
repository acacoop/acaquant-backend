# Herramientas — auditoría, performance y mantenimiento

Catálogo de los scripts **reusables** del repo (los que se corren cada tanto, no
los one-shot). Cada uno es read-only o trae su propio `--dry-run`; todos se
invocan con `python -m scripts.<nombre>` desde la raíz.

> Esta tabla se genera sola: cada herramienta se declara con una línea
> `Herramienta: <categoría> · <descripción>` en su docstring. Para regenerar:
> `python -m scripts.gen_herramientas`. No editar entre los marcadores AUTOGEN.

<!-- AUTOGEN:START — generado por scripts.gen_herramientas, no editar a mano -->

*11 herramientas en 4 categorías.*

### DBA / base de datos

| herramienta | qué hace |
|---|---|
| `python -m scripts.audit_db` | Auditoría read-only del cluster Mongo: inventario + índices + TTL + FINDINGS. |
| `python -m scripts.db_maintenance` | Aplica optimizaciones DBA (crear/dropear índices + TTL). Idempotente, dry-run por default. |
| `python -m scripts.diag_index_usage` | Uso real de índices ($indexStats): marca MUERTOS (0 ops) y redundantes. |

### Performance / profiling

| herramienta | qué hace |
|---|---|
| `python -m scripts.perf_scan` | Anti-patterns de queries Mongo (PERF001-4); modo --strict para CI / pre-deploy. |
| `python -m scripts.perf_sweep` | Barrido de latencia de los services (cold/warm + split Mongo vs CPU); --text para consola. |
| `python -m scripts.profile_motor` | Perfila un motor always-on en vivo con py-spy (top/record/dump), sin reiniciarlo. |
| `python -m scripts.profile_quant` | Microbenchmark del cálculo puro de quant/ — ¿vale optimizar/reescribir en C++/Rust? |

### Infra / sincronización

| herramienta | qué hace |
|---|---|
| `python -m scripts.api_migrate` | Re-sincroniza las colecciones *API derivadas (drop+insert por contrato de API). |
| `python -m scripts.gen_herramientas` | Regenera docs/HERRAMIENTAS.md (catálogo de herramientas) desde los docstrings. |
| `python -m scripts.gen_sistema` | Regenera deploy/SISTEMA.md (plano de servicios/crons) desde systemd + crontab. |

### seguridad

| herramienta | qué hace |
|---|---|
| `python -m scripts.security_audit` | Escaneo de secretos filtrados, deps con CVE (pip-audit) y código inseguro (bandit). |

<!-- AUTOGEN:END -->
