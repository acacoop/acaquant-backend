# Herramientas — auditoría, performance y mantenimiento

Catálogo de los scripts **reusables** del repo (los que se corren cada tanto, no
los one-shot). Cada uno es read-only o trae su propio `--dry-run`; todos se
invocan con `python -m scripts.<nombre>` desde la raíz.

> Esta tabla se genera sola: cada herramienta se declara con una línea
> `Herramienta: <categoría> · <descripción>` en su docstring. Para regenerar:
> `python -m scripts.gen_herramientas`. No editar entre los marcadores AUTOGEN.

<!-- AUTOGEN:START — generado por scripts.gen_herramientas, no editar a mano -->

*6 herramientas en 4 categorías.*

### Performance / profiling

| herramienta | qué hace |
|---|---|
| `python -m scripts.perf_scan` | Anti-patterns de queries PERF001-4; modo --strict para CI / pre-deploy. |
| `python -m scripts.profile_motor` | Perfila un motor always-on en vivo con py-spy (top/record/dump), sin reiniciarlo. |

### Infra / sincronización

| herramienta | qué hace |
|---|---|
| `python -m scripts.gen_herramientas` | Regenera docs/HERRAMIENTAS.md (catálogo de herramientas) desde los docstrings. |
| `python -m scripts.gen_sistema` | Regenera deploy/SISTEMA.md (plano de servicios/crons) desde systemd + crontab. |

### diag

| herramienta | qué hace |
|---|---|
| `python -m scripts.diag_clientes_enfriandose` | Mide ANTES de diseñar el detector (REGLA #2). |

### seguridad

| herramienta | qué hace |
|---|---|
| `python -m scripts.security_audit` | Escaneo de secretos filtrados, deps con CVE (pip-audit) y código inseguro (bandit). |

<!-- AUTOGEN:END -->
