"""gen_sistema.py — genera/actualiza deploy/SISTEMA.md desde la fuente real.

Herramienta: infra · Regenera deploy/SISTEMA.md (plano de servicios/crons) desde systemd + crontab.

El plano del sistema (`deploy/SISTEMA.md`) tiene dos partes:
  - NARRATIVA (topología, flujo de datos, bases) → escrita a mano.
  - INVENTARIO (servicios, motores, crons) → AUTO-GENERADO desde
    `deploy/systemd/*.service` + `deploy/crontab.txt`, entre marcadores
    <!-- AUTOGEN:x --> ... <!-- /AUTOGEN:x -->. Así NO puede desincronizarse:
    el inventario sale de la fuente real, no de la memoria.

Uso:
    python -m scripts.gen_sistema          # regenera las tablas en SISTEMA.md
    python -m scripts.gen_sistema --check  # falla (exit 1) si está desincronizado

Cuándo correrlo: cada vez que agregás/quitás/modificás un servicio systemd
o un cron. Lo recuerda la regla en CLAUDE.md y el skill /sistema.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYSTEMD_DIR = ROOT / "deploy" / "systemd"
CRONTAB = ROOT / "deploy" / "crontab.txt"
SISTEMA = ROOT / "deploy" / "SISTEMA.md"

_DOW = {"*": "diario", "1-5": "L-V", "2-6": "Mar-Sáb", "0": "Dom", "6": "Sáb"}


# ── Parsing de la fuente real ────────────────────────────────────────────────

def _grab(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def _target_from_exec(exec_: str) -> str:
    m = re.search(r"-m\s+(\S+)", exec_)
    if m:
        return f"`{m.group(1)}`"
    m = re.search(r"uvicorn\s+(\S+)", exec_)
    if m:
        return f"`{m.group(1)}` (uvicorn)"
    return "`?`"


def _port_from_exec(exec_: str) -> str:
    m = re.search(r"--port\s+(\d+)", exec_)
    return m.group(1) if m else "—"


def parse_services() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(SYSTEMD_DIR.glob("*.service")):
        text = f.read_text(encoding="utf-8")
        exec_ = _grab(text, r"ExecStart=(.+)")
        out[f.stem] = {
            "desc": _grab(text, r"Description=(.+)"),
            "target": _target_from_exec(exec_),
            "port": _port_from_exec(exec_),
        }
    return out


def _clean_cmd(cmd: str) -> str:
    """Limpia un comando de cron para mostrar: saca el `cd ... &&`, la
    redirección de logs y el prefijo absoluto del repo."""
    cmd = re.sub(r"^cd\s+\S+\s+&&\s+", "", cmd)
    cmd = re.sub(r"\s*>>?\s*\S+\s*2>&1\s*$", "", cmd)
    cmd = cmd.replace("/root/TradingAV/", "")
    return cmd.strip()


def parse_crontab() -> tuple[dict[str, dict], list[dict], list[dict]]:
    """Devuelve (windows, jobs, otros).

    windows: {service: {start, stop}} — servicios prendidos/apagados por cron.
    jobs:    [{schedule, modules}]     — invocaciones `python -m jobs/engines`.
    otros:   [{schedule, cmd}]         — CUALQUIER otra línea de cron (scripts
             shell, etc.) — para no perder nada que no sea systemctl ni python.
    """
    starts: dict[str, str] = {}
    stops: dict[str, str] = {}
    jobs: list[dict] = []
    otros: list[dict] = []
    for raw in CRONTAB.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        sched = " ".join(parts[:5])
        cmd = parts[5]
        m = re.search(r"systemctl\s+(start|stop)\s+(\S+)\.service", cmd)
        if m:
            (starts if m.group(1) == "start" else stops)[m.group(2)] = sched
            continue
        mods = re.findall(r"-m\s+(jobs\.\S+|engines\.\S+)", cmd)
        if mods:
            jobs.append({"schedule": sched, "modules": mods})
        else:
            otros.append({"schedule": sched, "cmd": _clean_cmd(cmd)})
    windows = {
        svc: {"start": starts.get(svc), "stop": stops.get(svc)}
        for svc in set(starts) | set(stops)
    }
    return windows, jobs, otros


# ── Humanización de expresiones cron ─────────────────────────────────────────

def _hhmm(expr: str | None) -> str:
    if not expr:
        return "?"
    mn, hr = expr.split()[:2]
    try:
        return f"{int(hr):02d}:{int(mn):02d}"
    except ValueError:
        return f"{hr}:{mn}"


def _humano(expr: str) -> str:
    mn, hr, _dom, _mon, dow = expr.split()
    d = _DOW.get(dow, dow)
    if mn.startswith("*/"):
        return f"cada {mn[2:]}min · {hr}h · {d}"
    if "-" in hr:
        return f"cada hora · {hr}h · {d}"
    return f"{_hhmm(expr)} · {d}"


def _ventana(w: dict) -> str:
    dow = (w.get("start") or w.get("stop") or "* * * * 1-5").split()[4]
    return f"{_hhmm(w.get('start'))}–{_hhmm(w.get('stop'))} {_DOW.get(dow, dow)}"


# ── Construcción de las tablas (bloques AUTOGEN) ──────────────────────────────

def build_blocks() -> dict[str, str]:
    services = parse_services()
    windows, jobs, otros = parse_crontab()
    cron_services = set(windows)

    # 1) Always-on (no los toca el cron)
    rows = ["| Servicio | Puerto | Target | Qué hace |", "|---|---|---|---|"]
    for name in sorted(services):
        if name in cron_services:
            continue
        s = services[name]
        rows.append(f"| `{name}` | {s['port']} | {s['target']} | {s['desc']} |")
    always_on = "\n".join(rows)

    # 2) Motores de mercado (cron start/stop)
    rows = ["| Servicio | Horario | Target | Qué hace |", "|---|---|---|---|"]
    for name in sorted(cron_services):
        s = services.get(name, {"target": "`?`", "desc": "(sin unit)"})
        rows.append(f"| `{name}` | {_ventana(windows[name])} | {s['target']} | {s['desc']} |")
    motores = "\n".join(rows)

    # 3) Jobs / crons (python -m jobs|engines)
    rows = ["| Horario | Módulo(s) |", "|---|---|"]
    for j in sorted(jobs, key=lambda x: x["schedule"]):
        mods = " + ".join(f"`{m}`" for m in j["modules"])
        rows.append(f"| {_humano(j['schedule'])} | {mods} |")
    crons = "\n".join(rows)

    # 4) Otros crons (scripts shell, etc.) — todo lo que NO es python ni systemctl
    rows = ["| Horario | Comando |", "|---|---|"]
    for o in sorted(otros, key=lambda x: x["schedule"]):
        rows.append(f"| {_humano(o['schedule'])} | `{o['cmd']}` |")
    otros_tbl = "\n".join(rows)

    return {
        "servicios": always_on,
        "motores": motores,
        "crons": crons,
        "otros": otros_tbl,
    }


# ── Inyección entre marcadores ────────────────────────────────────────────────

def _inject(text: str, blocks: dict[str, str]) -> str:
    for key, content in blocks.items():
        pat = re.compile(
            rf"(<!-- AUTOGEN:{key} -->).*?(<!-- /AUTOGEN:{key} -->)",
            re.DOTALL,
        )
        text = pat.sub(rf"\1\n{content}\n\2", text)
    return text


def _template(blocks: dict[str, str]) -> str:
    """Doc completo (narrativa + bloques) — solo se usa si SISTEMA.md no existe."""
    return f"""# SISTEMA — plano único de TradingAV

> **Fuente de verdad del sistema corriendo.** Las tablas de inventario se
> AUTO-GENERAN desde `deploy/systemd/*.service` + `deploy/crontab.txt` con
> `python -m scripts.gen_sistema` (no editar a mano entre los marcadores
> AUTOGEN). La narrativa (topología, flujo, bases) se mantiene a mano.

## Topología — cómo se conecta todo

```
   mae_forex.py              pyRofex (broker ROFEX/MAE)
   ⚠️ MANUAL ────┐            │ WS market data    ▲ envío/cancel órdenes
   (dólar MAE)   │            ▼                   │
                 │  ┌──── motores de mercado ───┐ │
                 │  │ rofex, options, curvas, … │ │   (motor_ordenes escucha
                 ▼  │  (L-V 13–20 UTC → SQL)    │ │    order_report → ordenes_live)
              Postgres / Supabase ◄── crons (portafolio, bcra, negocio, …)
                 ▲  ▲                            │
            lee  │  └────────────────────────────┘
   api.service (:8000) ──────────────────────────┘   + /mcp (Custom Connector Claude)
        ▲  nginx → Cloudflare Access (gate de identidad)
        │ HTTPS
   acaquant-web (Vercel) ── trading.acaquant.com
```

## Servicios always-on
<!-- AUTOGEN:servicios -->
{blocks['servicios']}
<!-- /AUTOGEN:servicios -->

## Motores de mercado (cron start/stop L-V)
<!-- AUTOGEN:motores -->
{blocks['motores']}
<!-- /AUTOGEN:motores -->

## Jobs / crons (batch)
<!-- AUTOGEN:crons -->
{blocks['crons']}
<!-- /AUTOGEN:crons -->

## Otros crons (scripts / shell)
<!-- AUTOGEN:otros -->
{blocks['otros']}
<!-- /AUTOGEN:otros -->

> Las tablas de arriba solo listan lo **agendado** en `crontab.txt`. Jobs
> manuales / on-demand (backfills, archival: `jobs.*backfill*`,
> etc.) se corren a mano y NO aparecen. Helpers
> (`jobs._*`, `aunesa_client`, `dias_habiles`) son librerías, no procesos.

## Componentes que NO están en systemd/cron
- **`mae_forex.py` — ⚠️ MANUAL (alguien le tiene que dar play):** feed live del
  dólar mayorista MAE (UST$T plazo 000) → escribe `Valuaciones.DolarOficialLive`.
  **No está automatizado** (ni systemd ni cron). Si nadie lo arranca, el TC
  dólar-linked (`motor_curvas`, `futuros_dlr`, `/argy`, `macro`) se queda con el
  dólar viejo. Es el único proceso del sistema que depende de que un humano lo prenda.
- **acaquant-web (Vercel)**: frontend Next.js, deploy auto sobre `main`. Sin crons propios.
- **Postgres / Supabase**: la base única (decomiso Mongo 2026-06-29), acceso vía `core.postgres.get_pool`.
- **Cloudflare Access**: gate de identidad (quién entra). **nginx** (Droplet): reverse proxy `api`→:8000.

## Integraciones externas (fuentes de datos)
- **pyRofex** (ROFEX/MAE) — market data WS + envío de órdenes.
- **Aunesa** — movimientos/posiciones (`jobs.cashflow`, `negocio_movimientos`, `descubrir_cuentas`).
- **BYMA Primarias** (licitaciones) · **MAE** (repos/cauciones) · **Finnhub** (data externa) · **BCRA / argentina_datos** (macro).

## Bases de datos (quién escribe qué)
- **`Trading`** — motores de mercado (MarketSnapshot, Curvas, TimeSales, OrderBookL2, DOLAR, SnapshotsCierre, CedearsSnapshot, PreciosAcciones).
- **`Valuaciones`** — `jobs.aum` (AuM, Assets), PnL precompute, DolarOficialLive (PC oficina).
- **`CashFlow`** — `jobs.cashflow`, `jobs.negocio_movimientos`.
- **`Manager`** — Users, RoleMatrix, Grupos, JobRuns, OrdenesIdempotency.
- **`Operaciones`** — `motor_ordenes` (OrdenesLive/Audit), OperativasMep.
- **`CuentasAPI` / `*API`** — copias derivadas (`jobs.sync_api_copies`).
- **`MCP`** — tokens OAuth (TTL).

## Cómo se opera
- Servicios: `systemctl {{start|stop|restart|status}} <servicio>`; logs `journalctl -u <servicio>`.
- Los motores los prende/apaga el **cron** (fuente: `deploy/crontab.txt`); no arrancarlos a mano fuera de horario (ver RUNBOOK: pausa de Atlas).
- Deploy backend: `git pull` + `systemctl restart api.service`. Frontend: push → Vercel.

> Diagnóstico de incidentes: `docs/RUNBOOK.md` · Secretos: `docs/SECRETS.md`.
"""


def main() -> int:
    check = "--check" in sys.argv
    blocks = build_blocks()

    if not SISTEMA.exists():
        if check:
            print("deploy/SISTEMA.md no existe — corré `python -m scripts.gen_sistema`.")
            return 1
        SISTEMA.write_text(_template(blocks), encoding="utf-8")
        print(f"Creado {SISTEMA.relative_to(ROOT)} (plano inicial).")
        return 0

    actual = SISTEMA.read_text(encoding="utf-8")
    nuevo = _inject(actual, blocks)

    if check:
        if nuevo != actual:
            print("DESINCRONIZADO: deploy/SISTEMA.md no refleja los systemd/crontab actuales.")
            print("Corré `python -m scripts.gen_sistema` y commiteá.")
            return 1
        print("OK: el plano está sincronizado con systemd + crontab.")
        return 0

    if nuevo == actual:
        print("Sin cambios: el plano ya estaba sincronizado.")
    else:
        SISTEMA.write_text(nuevo, encoding="utf-8")
        print(f"Actualizado {SISTEMA.relative_to(ROOT)} (tablas regeneradas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
