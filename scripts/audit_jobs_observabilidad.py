"""scripts/audit_jobs_observabilidad.py — ¿qué job puede fallar sin que nadie se entere?

Auditoría ESTÁTICA (no toca la DB, no necesita deps: corre en cualquier lado y en CI).
Cruza las tres fuentes que tienen que estar alineadas para que el Manager diga la verdad:

  1. `deploy/crontab.txt`              → qué corre DE VERDAD.
  2. `jobs/*.py` / `engines/*.py`      → quién registra en manager.job_runs (JobRunLogger)
                                          y con qué string `tipo`.
  3. `api/services/diagnostico_registry.py` → el árbol de MANAGER → DIAGNÓSTICO.

Motivo (2026-08-03): `jobs.economic_calendar` venía fallando desde el día que se
creó — la tabla destino nunca tuvo una fila — y no aparecía en `manager.job_runs`
porque no usaba JobRunLogger. El Manager lo mostraba mirando una colección Mongo
que ya no existe. Tres capas de observabilidad y ninguna gritó.

Qué detecta:
  [A] Cron que corre y NO usa JobRunLogger → si explota, no deja rastro.
  [B] `run_tipo` del registro que ningún job emite → la fila queda "sin_datos" para
      siempre (el propio registry lo admite en su docstring).
  [C] Job con JobRunLogger que el registro NO mira por run_tipo → el Manager se cae
      al frescor de la tabla de salida: un run en ERROR con datos viejos se ve "ok".
  [D] Pieza cuya frescura sale de Mongo (`db`/`coll` sin `tabla`) → Mongo se decomisó
      el 2026-06-29: ese chequeo está muerto.

Uso:
    python -m scripts.audit_jobs_observabilidad            # informativo
    python -m scripts.audit_jobs_observabilidad --strict   # exit 1 si hay [A] o [D]
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRONTAB = ROOT / "deploy" / "crontab.txt"
REGISTRY = ROOT / "api" / "services" / "diagnostico_registry.py"

# Windows con la salida piped usa cp1252 y revienta con los acentos/flechas.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _crons_reales() -> set[str]:
    txt = CRONTAB.read_text(encoding="utf-8")
    return set(re.findall(r"-m\s+((?:jobs|engines)\.[A-Za-z0-9_]+)", txt))


def _tipos_por_modulo() -> dict[str, set[str]]:
    """`jobs.x` → {tipos que ese archivo le pasa a JobRunLogger(...)}."""
    out: dict[str, set[str]] = {}
    for carpeta in ("jobs", "engines"):
        for py in sorted((ROOT / carpeta).glob("*.py")):
            if py.name == "__init__.py":
                continue
            arbol = ast.parse(py.read_text(encoding="utf-8"))
            # Algunos jobs pasan una constante de módulo (ej. TIPO_JOB) en vez del literal.
            consts = {
                t.id: n.value.value
                for n in arbol.body if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name)
                and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
            }
            tipos: set[str] = set()
            for node in ast.walk(arbol):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "JobRunLogger"
                        and node.args):
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    tipos.add(arg.value)
                elif isinstance(arg, ast.Name) and arg.id in consts:
                    tipos.add(consts[arg.id])
            out[f"{carpeta}.{py.stem}"] = tipos
    return out


def _piezas_registro() -> list[dict]:
    """Lee los `Pieza(...)` del registry por AST (sin importar: cero deps)."""
    piezas: list[dict] = []
    for node in ast.walk(ast.parse(REGISTRY.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Pieza"):
            continue
        p: dict = {"label": None, "tipo": None}
        posicionales = ["vista", "tipo", "label"]
        for i, arg in enumerate(node.args):
            if i < len(posicionales) and isinstance(arg, ast.Constant):
                p[posicionales[i]] = arg.value
        for kw in node.keywords:
            if isinstance(kw.value, ast.Constant):
                p[kw.arg] = kw.value.value
            elif kw.arg in ("db", "coll", "tabla", "run_tipo", "unidad"):
                p[kw.arg] = "<expr>"
        piezas.append(p)
    return piezas


def _ignorados() -> set[str]:
    """La lista de escape del test anti-drift (jobs excluidos del árbol a propósito)."""
    test = ROOT / "tests" / "test_diagnostico_registry.py"
    if not test.exists():
        return set()
    for node in ast.walk(ast.parse(test.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Set)
                and any(getattr(t, "id", "") == "_CRONS_IGNORADOS" for t in node.targets)):
            return {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    return set()


def main() -> int:
    crons = _crons_reales()
    tipos = _tipos_por_modulo()
    piezas = _piezas_registro()
    ignorados = _ignorados()

    con_logger = {m for m, t in tipos.items() if t}
    tipos_emitidos = {t for ts in tipos.values() for t in ts}
    run_tipos_registro = {p["run_tipo"] for p in piezas if p.get("run_tipo")}
    unidades_registro = {p["unidad"] for p in piezas if p.get("unidad")}

    # [A] corre pero no registra
    a = sorted(c for c in crons if c not in con_logger)
    # [B] el registro espera un tipo que nadie emite
    b = sorted(run_tipos_registro - tipos_emitidos)
    # [C] registra pero el árbol no lo mira por run_tipo
    c = sorted(m for m in con_logger
               if m in crons and m in unidades_registro
               and not (tipos[m] & run_tipos_registro))
    # [D] frescura apoyada en Mongo (decomisado)
    d = [p for p in piezas if p.get("db") and not p.get("tabla")]

    print("=== INVENTARIO ===")
    print(f"crons en crontab.txt         : {len(crons)}")
    print(f"módulos con JobRunLogger     : {len(con_logger)}")
    print(f"piezas en el Diagnóstico     : {len(piezas)}")
    print(f"jobs excluidos del árbol      : {len(ignorados)} (lista de escape del test)")

    print(f"\n=== [A] CORREN SIN DEJAR RASTRO ({len(a)}) ===")
    print("Sin JobRunLogger no hay fila en manager.job_runs: si fallan, silencio total.")
    for m in a:
        print(f"  {m}{'   (además, fuera del árbol del Diagnóstico)' if m in ignorados else ''}")
    if not a:
        print("  (ninguno)")

    print(f"\n=== [B] run_tipo HUÉRFANO ({len(b)}) ===")
    print("El árbol espera este `tipo` pero ningún job lo emite → fila 'sin_datos' eterna.")
    for t in b:
        print(f"  run_tipo={t!r}")
    if not b:
        print("  (ninguno)")

    print(f"\n=== [C] REGISTRAN, PERO EL ÁRBOL NO LOS MIRA ({len(c)}) ===")
    print("El Diagnóstico cae al frescor de la tabla de salida: un run en ERROR con")
    print("datos viejos-pero-presentes se ve igual que uno sano.")
    for m in c:
        print(f"  {m}  (emite tipo={sorted(tipos[m])})")
    if not c:
        print("  (ninguno)")

    print(f"\n=== [D] CHEQUEOS APOYADOS EN MONGO ({len(d)}) ===")
    print("Mongo se decomisó el 2026-06-29: estas piezas miran una fuente inexistente.")
    for p in d:
        print(f"  [{p.get('vista')}] {p.get('label')} → {p.get('db')}.{p.get('coll')}")
    if not d:
        print("  (ninguna)")

    if "--strict" in sys.argv and (a or d):
        print("\nFALLA (--strict): hay jobs sin rastro o chequeos contra Mongo.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
