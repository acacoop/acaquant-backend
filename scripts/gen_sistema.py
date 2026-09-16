"""gen_sistema.py — mantiene docs/ACAQUANT.md, EL documento oficial de cómo funciona todo.

Herramienta: infra · Regenera los inventarios de docs/ACAQUANT.md (procesos, crons, schemas) y su estampa.

El doc tiene dos clases de contenido:
  - NARRATIVA (qué es, dónde corre, cómo se conecta, cómo se protege, qué hacer
    si se rompe) → escrita a mano, corta, en palabras simples.
  - INVENTARIOS (servicios, motores, crons, schemas) → AUTO-GENERADOS desde la
    fuente real (`deploy/systemd/*.service`, `deploy/crontab.txt`,
    `sql/schema.sql`), entre marcadores <!-- AUTOGEN:x --> … <!-- /AUTOGEN:x -->.
    Así no pueden desincronizarse: salen de lo que corre, no de la memoria.

La ESTAMPA. La segunda línea del doc dice cuándo se actualizó por última vez y
lleva una HUELLA (hash corto del contenido sin esa línea). Este script la
reescribe en cada escritura. `--check` recalcula la huella: si alguien editó el
doc a mano y no regeneró, la huella no coincide y el check falla. El arreglo es
UNA línea: `python -m scripts.gen_sistema`. Sin esto, «siempre tiene fecha» es
un deseo; con esto, es un test.

Uso:
    python -m scripts.gen_sistema          # regenera inventarios + estampa
    python -m scripts.gen_sistema --check  # exit 1 si quedó desincronizado o sin re-estampar

Cuándo correrlo: al tocar un service/cron/schema, y al editar el doc a mano.
El hook `sistema_drift.sh` lo corre solo cuando Claude edita el doc.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYSTEMD_DIR = ROOT / "deploy" / "systemd"
CRONTAB = ROOT / "deploy" / "crontab.txt"
SCHEMA = ROOT / "sql" / "schema.sql"
DOC = ROOT / "docs" / "ACAQUANT.md"

_DOW = {"*": "diario", "1-5": "L-V", "1-6": "L-Sáb", "2-6": "Mar-Sáb", "0": "Dom", "6": "Sáb"}

# Qué guarda cada schema y quién escribe. Es la única parte «a mano» del bloque
# de schemas: la lista de schemas y el conteo de tablas salen de schema.sql. Un
# schema nuevo sin fila acá aparece igual, marcado, para que no quede mudo.
_SCHEMAS = {
    "clientes": ("Cuentas, comitentes, operadores, contrapartes, accionistas", "jobs de Aunesa + Manager"),
    "operaciones": ("Boletos, movimientos, órdenes, tipos de operación", "`operaciones_informes`, `negocio_movimientos`, `motor_ordenes`"),
    "portafolio": ("Tenencias (AuM) y ficha de cada activo", "`portafolio_backfill`, `tenencia_live`, AV AGENT"),
    "valuaciones": ("PnL, dólar (oficial live, MEP/CCL), último precio por tenencia", "motores de dólar y snapshot, `mae_forex.py`"),
    "mercado": ("Todo lo que producen los motores: curvas, snapshots, opciones, agro, FCI, cierres", "`engines.*` y jobs de mercado"),
    "macro": ("Series BCRA, UVA, REM, dólar A3500", "`jobs.bcra`, `jobs.argentina_datos`"),
    "manager": ("Usuarios, roles, grupos, corridas de jobs, diagnóstico", "la vista Manager y `JobRunLogger`"),
    "home": ("Cotizaciones, calendario y noticias de la portada", "`jobs.market_quotes`, news"),
    "research": ("Datos de 1816, BCRA y FRED para la vista Research", "jobs de research"),
    "ia": ("Briefing diario y conversaciones del ASISTENTE", "`asistente/`, briefing"),
    "agente": ("Hallazgos, sujetos y corridas del AV AGENT", "SOLO `agente/registro.py`"),
    "aca": ("Resumen ejecutivo de inversiones (vista ACA), carga manual", "la vista ACA"),
    "bancos": ("Movimientos bancarios, gastos, conciliación (Interbanking y Tesorería)", "jobs de Interbanking + carga manual"),
    "ap5": ("Posiciones y diferencias contra A3/ACyRSA (Postrade)", "jobs de Postrade"),
    "ext": ("API externa para accionistas (claves, cuentas, auditoría)", "`api/ext`"),
    "partner": ("Reservado (sin tablas hoy)", "—"),
}


# ── Parsing de la fuente real ────────────────────────────────────────────────

def _grab(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def _target_from_exec(exec_: str) -> str:
    m = re.search(r"-m\s+([\w.]+)", exec_)
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
    redirección de logs, las comillas del wrapper y el prefijo del repo."""
    cmd = cmd.replace("'", "")
    cmd = re.sub(r"cd\s+\S+\s+&&\s+", "", cmd)
    cmd = re.sub(r"\s*>>?\s*\S+\s*2>&1\s*$", "", cmd)
    cmd = cmd.replace("/root/TradingAV/", "").replace("venv/bin/python", "python")
    return cmd.strip()


def parse_crontab() -> tuple[dict[str, dict], list[dict], list[dict]]:
    """Devuelve (windows, jobs, otros).

    windows: {service: {start, stop}} — servicios prendidos/apagados por cron.
             `systemctl restart` cuenta como arranque: es lo que usa el crontab
             para prender los motores (antes solo se leía `start` y el plano
             decía «?» en la hora de arranque de TODOS los motores).
    jobs:    [{schedule, modules}]     — invocaciones `python -m jobs/engines`.
    otros:   [{schedule, cmd}]         — cualquier otra línea (scripts, shell).
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
        m = re.search(r"systemctl\s+(start|restart|stop)\s+(\S+)\.service", cmd)
        if m:
            (stops if m.group(1) == "stop" else starts)[m.group(2)] = sched
            continue
        # `[\w.]+` y no `\S+`: el comando viene entre comillas por run_job.sh y
        # `\S+` se llevaba la comilla de cierre (`jobs.market_quotes'`).
        mods = re.findall(r"-m\s+(jobs\.[\w.]+|engines\.[\w.]+)", cmd)
        if mods:
            jobs.append({"schedule": sched, "modules": mods})
        else:
            otros.append({"schedule": sched, "cmd": _clean_cmd(cmd)})
    windows = {
        svc: {"start": starts.get(svc), "stop": stops.get(svc)}
        for svc in set(starts) | set(stops)
    }
    return windows, jobs, otros


_TABLE = re.compile(
    r"^\s*CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_0-9]+)\.([a-z_0-9]+)",
    re.I | re.M,
)
_SCHEMA_DECL = re.compile(r"^\s*CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_0-9]+)", re.I | re.M)


def parse_schema() -> dict[str, int]:
    """{schema: cantidad de tablas} desde sql/schema.sql. Un schema declarado
    sin tablas aparece con 0: mejor verlo que no saber que existe."""
    text = SCHEMA.read_text(encoding="utf-8")
    counts: dict[str, int] = {s.lower(): 0 for s in _SCHEMA_DECL.findall(text)}
    for schema, _table in _TABLE.findall(text):
        counts[schema.lower()] = counts.get(schema.lower(), 0) + 1
    return counts


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
    if "," in mn:
        # Minutos explícitos (ej. "15,45"): dos corridas por hora, desfasadas.
        return f"min {mn} · {hr}h · {d}"
    if "-" in hr:
        return f"cada hora · {hr}h · {d}"
    if "," in hr:
        # Horas explícitas (ej. "12,16,20,23"): varias corridas al día.
        return f"a las {hr.replace(',', ', ')}h · {d}"
    return f"{_hhmm(expr)} · {d}"


def _orden(expr: str) -> tuple:
    """Para listar los crons en orden de lectura: primero los recurrentes (cada
    N min / cada hora), después los puntuales por hora del día."""
    mn, hr = expr.split()[:2]
    recurrente = mn.startswith("*/") or "-" in hr or "," in hr or "," in mn
    try:
        h = int(hr.split("-")[0].split(",")[0])
    except ValueError:
        h = 99
    try:
        m = int(mn.split(",")[0])
    except ValueError:
        m = 0
    return (0 if recurrente else 1, h, m, expr)


def _ventana(w: dict) -> str:
    dow = (w.get("start") or w.get("stop") or "* * * * 1-5").split()[4]
    return f"{_hhmm(w.get('start'))}–{_hhmm(w.get('stop'))} {_DOW.get(dow, dow)}"


# ── Construcción de las tablas (bloques AUTOGEN) ──────────────────────────────

def build_blocks() -> dict[str, str]:
    services = parse_services()
    windows, jobs, otros = parse_crontab()
    cron_services = set(windows)

    rows = ["| Servicio | Puerto | Módulo | Qué hace |", "|---|---|---|---|"]
    for name in sorted(services):
        if name in cron_services:
            continue
        s = services[name]
        rows.append(f"| `{name}` | {s['port']} | {s['target']} | {s['desc']} |")
    always_on = "\n".join(rows)

    rows = ["| Servicio | Horario (UTC) | Módulo | Qué hace |", "|---|---|---|---|"]
    for name in sorted(cron_services):
        s = services.get(name, {"target": "`?`", "desc": "(sin unit)"})
        rows.append(f"| `{name}` | {_ventana(windows[name])} | {s['target']} | {s['desc']} |")
    motores = "\n".join(rows)

    rows = ["| Horario (UTC) | Módulo(s) |", "|---|---|"]
    for j in sorted(jobs, key=lambda x: _orden(x["schedule"])):
        mods = " + ".join(f"`{m}`" for m in j["modules"])
        rows.append(f"| {_humano(j['schedule'])} | {mods} |")
    crons = "\n".join(rows)

    rows = ["| Horario (UTC) | Comando |", "|---|---|"]
    for o in sorted(otros, key=lambda x: _orden(x["schedule"])):
        rows.append(f"| {_humano(o['schedule'])} | `{o['cmd']}` |")
    otros_tbl = "\n".join(rows)

    rows = ["| Schema | Tablas | Qué guarda | Quién escribe |", "|---|---|---|---|"]
    for schema, n in sorted(parse_schema().items()):
        que, quien = _SCHEMAS.get(schema, ("⚠️ sin descripción — agregar en `gen_sistema._SCHEMAS`", "?"))
        rows.append(f"| `{schema}` | {n} | {que} | {quien} |")
    schemas = "\n".join(rows)

    return {
        "servicios": always_on,
        "motores": motores,
        "crons": crons,
        "otros": otros_tbl,
        "schemas": schemas,
    }


# ── Inyección entre marcadores + estampa ─────────────────────────────────────

def _inject(text: str, blocks: dict[str, str]) -> str:
    for key, content in blocks.items():
        pat = re.compile(
            rf"(<!-- AUTOGEN:{key} -->).*?(<!-- /AUTOGEN:{key} -->)",
            re.DOTALL,
        )
        text = pat.sub(lambda m, c=content: f"{m.group(1)}\n{c}\n{m.group(2)}", text)
    return text


_STAMP = re.compile(r"^\*\*Última actualización:\*\* .*?· huella `([0-9a-f]{8})`\s*$", re.M)


def _ahora_ba() -> str:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Argentina/Buenos_Aires")
    except Exception:  # sin tzdata (Windows pelado): Argentina no tiene horario de verano
        tz = _dt.timezone(_dt.timedelta(hours=-3))
    return _dt.datetime.now(tz).strftime("%Y-%m-%d %H:%M")


def _huella(text: str) -> str:
    """Hash corto del doc SIN la línea de estampa (si no, cambiaría siempre)."""
    cuerpo = _STAMP.sub("", text)
    return hashlib.sha256(cuerpo.encode("utf-8")).hexdigest()[:8]


def _estampar(text: str) -> str:
    linea = f"**Última actualización:** {_ahora_ba()} (hora Buenos Aires) · huella `{_huella(text)}`"
    if _STAMP.search(text):
        return _STAMP.sub(linea, text, count=1)
    # Sin estampa: va después del título (primera línea), con su línea en blanco.
    titulo, _, resto = text.partition("\n")
    return f"{titulo}\n\n{linea}\n\n{resto.lstrip()}"


def _estampa_vigente(text: str) -> bool:
    m = _STAMP.search(text)
    return bool(m) and m.group(1) == _huella(text)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    check = "--check" in sys.argv
    rel = DOC.relative_to(ROOT).as_posix()

    if not DOC.exists():
        print(f"{rel} no existe. Es el doc oficial: no se crea solo, se restaura desde git.")
        return 1

    actual = DOC.read_text(encoding="utf-8")
    con_bloques = _inject(actual, build_blocks())
    bloques_ok = con_bloques == actual

    if check:
        if not bloques_ok:
            print(f"DESINCRONIZADO: {rel} no refleja systemd/crontab/schema.sql actuales.")
        if not _estampa_vigente(actual):
            print(f"SIN RE-ESTAMPAR: {rel} se editó y la huella no coincide con el contenido.")
        if not bloques_ok or not _estampa_vigente(actual):
            print("Corré `python -m scripts.gen_sistema` y commiteá.")
            return 1
        print(f"OK: {rel} sincronizado y estampado.")
        return 0

    if bloques_ok and _estampa_vigente(actual):
        print(f"Sin cambios: {rel} ya estaba sincronizado y estampado.")
        return 0
    DOC.write_text(_estampar(con_bloques), encoding="utf-8")
    print(f"Actualizado {rel} ({'inventarios regenerados' if not bloques_ok else 'contenido'} + estampa).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
