"""Mapa de la superficie HTTP con el gate EFECTIVO de cada ruta.

Herramienta de AUDITORÍA recurrente (no es un one-shot: se re-corre cada vez
que se agrega un router para revisar qué quedó expuesto). Read-only — importa
la app y camina `app.routes`; no toca la base ni la red.

    python -m scripts.audit_superficie_http            # resumen (lo que importa)
    python -m scripts.audit_superficie_http --todo     # + inventario completo
    python -m scripts.audit_superficie_http --json     # para diffear entre commits

Por qué mirar `app.routes` y no el código: el gate de una ruta se compone del
montaje en `api/main.py`, del `dependencies=` de su sub-router y del decorador.
Sólo el árbol ya resuelto dice qué protege realmente a cada endpoint. Leyendo
`api/main.py` a ojo se pasan por alto justo los casos que importan.

El enforcement automático vive en `tests/unit/test_rbac_superficie.py` (corre en
CI). Este script es para MIRAR la superficie; el test es para que no se afloje.
"""
from __future__ import annotations

import argparse
import json
import sys

from api.main import app

# Gates reconocidos. `require_module("x")` produce `require_module_x` (api/auth.py).
_GATES_MODULO = ("require_module_", "require_any_module_")
_GATES_DUROS = ("require_admin", "require_control_comercial")

# Sin bearer a propósito (ver tests/unit/test_rbac_superficie.py).
_SIN_BEARER_OK = {
    "/api/health", "/api/me",
    "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect",
}
_AUTH_PROPIA = "/api/ingest/"


def _deps(route) -> set[str]:
    dep = getattr(route, "dependant", None)
    if dep is None:
        return set()
    out: set[str] = set()
    stack = [dep]
    while stack:
        d = stack.pop()
        call = getattr(d, "call", None)
        if call is not None:
            out.add(getattr(call, "__name__", ""))
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


def _tiene_modulo(deps: set[str]) -> bool:
    return any(d.startswith(_GATES_MODULO) or d in _GATES_DUROS for d in deps)


def recolectar() -> list[dict]:
    filas = []
    for r in app.routes:
        path = getattr(r, "path", None)
        if path is None:
            continue
        methods = sorted(m for m in (getattr(r, "methods", None) or set())
                         if m not in ("HEAD", "OPTIONS"))
        if not methods:
            continue
        deps = _deps(r)
        gates = sorted(d for d in deps
                       if d.startswith(_GATES_MODULO)
                       or d in _GATES_DUROS
                       or d in ("verify_api_key", "verify_ingest_token",
                                "require_no_invitado", "get_user_email"))
        filas.append({
            "path": path,
            "methods": methods,
            "gates": gates,
            "bearer": "verify_api_key" in deps or "verify_ingest_token" in deps,
            "modulo": _tiene_modulo(deps),
            "no_invitado": "require_no_invitado" in deps,
            "escritura": any(m in ("POST", "PUT", "PATCH", "DELETE") for m in methods),
        })
    return sorted(filas, key=lambda f: f["path"])


def _bloque(titulo: str, filas: list[dict], nota: str = "") -> None:
    print("=" * 78)
    print(titulo)
    if nota:
        print(f"  ({nota})")
    print("=" * 78)
    if not filas:
        print("  — ninguna —")
    for f in filas:
        print(f"  {','.join(f['methods']):20} {f['path']:52} {f['gates']}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--todo", action="store_true", help="imprime el inventario completo")
    ap.add_argument("--json", action="store_true", help="salida JSON (diffeable)")
    args = ap.parse_args()

    filas = recolectar()

    if args.json:
        json.dump(filas, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return 0

    print(f"\nSUPERFICIE HTTP — {len(filas)} rutas\n")

    _bloque(
        "A) SIN BEARER (ni verify_api_key ni verify_ingest_token)",
        [f for f in filas
         if not f["bearer"] and f["path"] not in _SIN_BEARER_OK],
        "excluye health/me/swagger, que son sin-bearer a propósito",
    )
    _bloque(
        "B) SIN GATE DE MODULO — cualquier autenticado, INCLUIDO el invitado",
        [f for f in filas if f["path"].startswith("/api/") and not f["modulo"]],
        "esperado para mercado/research; revisar que nada de NEGOCIO caiga acá",
    )
    _bloque(
        "C) ESCRITURAS SIN GATE DE MODULO",
        [f for f in filas
         if f["escritura"] and f["path"].startswith("/api/")
         and not f["modulo"] and not f["no_invitado"]
         and not f["path"].startswith(_AUTH_PROPIA)],
        "REGLA #8: el invitado no debe poder ejecutarlas",
    )
    _bloque(
        "D) ADMIN DURO (require_admin — no delegable desde la matriz)",
        [f for f in filas if "require_admin" in f["gates"]],
    )

    if args.todo:
        _bloque("E) INVENTARIO COMPLETO", filas)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
