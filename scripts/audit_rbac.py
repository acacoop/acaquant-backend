"""Auditoría de la superficie HTTP: qué gate tiene cada endpoint y quién llega.

Read-only, no toca nada. Complementa `tests/unit/test_rbac_superficie.py`: el
test dice PASA/FALLA sobre invariantes; esto imprime el MAPA para revisarlo a
ojo ("¿quién puede entrar a /api/back-office/senebis?").

La verdad sale del árbol de dependencies ya armado de `api.main.app` — no de
leer el código. Un gate se compone en 3 lugares (montaje en main.py,
`dependencies=` del sub-router, decorador del endpoint) y sólo la app armada
sabe cuál aplica de verdad.

La matriz de roles se lee VIVA de Postgres (`core.roles.get_matrix`), que es la
que edita el admin en /manager → ROLES Y PERMISOS. Si la DB no responde, cae a
DEFAULT_MATRIX y lo avisa. El rol `invitado` es la excepción: NO vive en la
matriz editable sino en `INVITADO_MODULES` (código) — el portal www se define
por el header, no por el rol del email (REGLA #8).

LIMITACIÓN: soló ve gates declarados como dependency. Un chequeo hecho DENTRO
del handler es invisible acá — por eso los permisos se declaran con `Depends`.

    python -m scripts.audit_rbac                    # todo
    python -m scripts.audit_rbac --rol back_office  # a qué llega ese rol
    python -m scripts.audit_rbac --rol invitado     # qué alcanza www.acaquant.com
    python -m scripts.audit_rbac --modulo senebis   # endpoints de un módulo
    python -m scripts.audit_rbac --sin-gate         # SOLO lo abierto (lo que importa)
    python -m scripts.audit_rbac --escrituras       # POST/PATCH/PUT/DELETE
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from api.main import app

ESCRITURAS = {"POST", "PATCH", "PUT", "DELETE"}

# Gates que NO son de módulo: no se delegan desde la matriz de roles.
GATES_DUROS = {
    "require_admin": "ADMIN (duro, no delegable)",
    "require_control_comercial": "CONTROL COMERCIAL (permiso por usuario)",
}


@dataclass
class Ruta:
    path: str
    methods: list[str]
    modulos: tuple[str, ...]      # módulos que habilitan (OR); () = sin gate de módulo
    gate_duro: str | None
    bearer: bool
    token_ingesta: bool
    bloquea_invitado: bool
    extras: tuple[str, ...]       # gates adicionales (allowlists per-usuario)

    @property
    def gate(self) -> str:
        if self.gate_duro:
            base = GATES_DUROS[self.gate_duro]
        elif self.modulos:
            base = " | ".join(self.modulos)
        elif self.token_ingesta:
            base = "TOKEN DE INGESTA (X-Ingest-Token)"
        else:
            base = "— SIN GATE DE MÓDULO —"
        if self.extras:
            base += " + " + " + ".join(self.extras)
        return base


def _deps(route) -> list:
    """Todas las dependencies resueltas de la ruta (recursivo)."""
    dep = getattr(route, "dependant", None)
    if dep is None:
        return []
    out, stack = [], [dep]
    while stack:
        d = stack.pop()
        call = getattr(d, "call", None)
        if call is not None:
            out.append(call)
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


def _rutas() -> list[Ruta]:
    rutas: list[Ruta] = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = {m for m in (getattr(r, "methods", None) or set())
                   if m not in ("HEAD", "OPTIONS")}
        if path is None or not methods:
            continue
        calls = _deps(r)
        nombres = {getattr(c, "__name__", "") for c in calls}
        modulos: tuple[str, ...] = ()
        for c in calls:
            mods = getattr(c, "rbac_modules", None)
            if mods:
                modulos = tuple(mods)
                break
        duro = next((n for n in nombres if n in GATES_DUROS), None)
        # Cualquier otro require_* declarado a mano (allowlists per-usuario).
        extras = tuple(sorted(
            n for n in nombres
            if n.startswith("require_") and n not in GATES_DUROS
            and not n.startswith(("require_module_", "require_any_module_"))
            and n != "require_no_invitado"
        ))
        ingesta = "verify_ingest_token" in nombres
        rutas.append(Ruta(
            path=path,
            methods=sorted(methods),
            modulos=modulos,
            gate_duro=duro,
            bearer="verify_api_key" in nombres,
            token_ingesta=ingesta,
            bloquea_invitado="require_no_invitado" in nombres,
            extras=extras,
        ))
    return sorted(rutas, key=lambda x: x.path)


def _matriz() -> dict[str, tuple[str, ...]]:
    from core.roles import DEFAULT_MATRIX, get_matrix
    try:
        m = get_matrix()
        if m:
            return m
    except Exception as e:  # el audit no debe morir porque la DB no responde
        print(f"⚠ no se pudo leer la matriz viva ({e}) — usando DEFAULT_MATRIX\n")
    return dict(DEFAULT_MATRIX)


def _alcanza(r: Ruta, rol: str, modulos_rol: tuple[str, ...]) -> bool:
    if r.token_ingesta:
        return False  # auth propia, no depende del rol
    if r.gate_duro == "require_admin":
        return rol == "admin"
    if r.gate_duro == "require_control_comercial":
        return False  # es por usuario, no por rol — no se puede saber acá
    if rol == "invitado" and r.bloquea_invitado:
        return False
    if not r.modulos:
        return True   # sin gate de módulo: lo alcanza cualquiera autenticado
    return any(m in modulos_rol for m in r.modulos)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rol", help="a qué endpoints llega ese rol (ej. back_office, invitado)")
    ap.add_argument("--modulo", help="endpoints gateados por ese módulo")
    ap.add_argument("--sin-gate", action="store_true", help="solo rutas sin gate de módulo")
    ap.add_argument("--escrituras", action="store_true", help="solo POST/PATCH/PUT/DELETE")
    args = ap.parse_args()

    rutas = _rutas()
    matriz = _matriz()
    # El invitado NO está en la matriz editable: se define por el header del
    # portal www y sus módulos son la constante INVITADO_MODULES (REGLA #8).
    from core.roles import INVITADO_MODULES
    matriz.setdefault("invitado", tuple(INVITADO_MODULES))

    if args.rol:
        if args.rol not in matriz:
            print(f"rol {args.rol!r} no existe. Roles: {', '.join(sorted(matriz))}")
            return
        mods = tuple(matriz[args.rol])
        rutas = [r for r in rutas if _alcanza(r, args.rol, mods)]
    if args.modulo:
        rutas = [r for r in rutas if args.modulo in r.modulos]
    if args.sin_gate:
        rutas = [r for r in rutas if not r.modulos and not r.gate_duro
                 and not r.token_ingesta]
    if args.escrituras:
        rutas = [r for r in rutas if set(r.methods) & ESCRITURAS]

    titulo = "SUPERFICIE HTTP"
    if args.rol:
        titulo += f" — alcanzable por rol {args.rol.upper()}"
    if args.modulo:
        titulo += f" — módulo {args.modulo}"
    if args.sin_gate:
        titulo += " — SIN GATE DE MÓDULO"
    if args.escrituras:
        titulo += " — solo ESCRITURAS"
    print(f"\n{titulo}  ({len(rutas)} rutas)\n" + "=" * 110)
    print(f"{'MÉTODOS':<22} {'PATH':<52} GATE")
    print("-" * 110)
    for r in rutas:
        marca = "" if (r.bearer or r.token_ingesta) else "  ⚠ SIN BEARER"
        guard = "  [no-invitado]" if r.bloquea_invitado else ""
        print(f"{','.join(r.methods):<22} {r.path:<52} {r.gate}{guard}{marca}")

    if not args.rol and not args.modulo:
        abiertas = [r for r in rutas if not r.modulos and not r.gate_duro
                    and not r.token_ingesta and r.path.startswith("/api/")]
        print("\n" + "=" * 110)
        print(f"Rutas /api/* sin gate de módulo: {len(abiertas)} "
              "(revisar que sean públicas a propósito — health, /api/me, mercado abierto)")
        print("Roles: " + ", ".join(sorted(matriz)))
        print("OJO: solo se ven gates declarados con Depends() — un chequeo dentro "
              "del handler no aparece acá.")


if __name__ == "__main__":
    main()
