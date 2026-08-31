"""gen_mapa_app.py — mantiene `docs/MAPA_APP.md` sincronizado con el código.

Mismo contrato que `gen_sistema`: los bloques entre marcadores
`<!-- AUTOGEN:x --> ... <!-- /AUTOGEN:x -->` se regeneran desde la FUENTE REAL
(la app FastAPI montada) y NO se editan a mano. La narrativa del doc (qué hace
cada vista, sus tabs, sus filtros, las rarezas) se mantiene a mano: eso cambia
despacio y es donde está el criterio.

    python -m scripts.gen_mapa_app          # regenera los bloques
    python -m scripts.gen_mapa_app --check  # exit 1 si quedó desincronizado (CI)
    python -m scripts.gen_mapa_app --full   # imprime las 400+ rutas (no va al doc)

POR QUÉ ESTE SCRIPT EXISTE
--------------------------
El inventario de endpoints y la matriz de permisos son lo que MÁS drift tiene
(cada router nuevo los desactualiza) y lo más caro de relevar a mano. Que los
genere un script determinista significa que el doc no puede mentir sobre eso, y
que una sesión nueva se pone al día leyendo un doc corto en vez de re-relevar
toda la app.

EL DETALLE QUE HACE QUE ESTO FUNCIONE (y que rompía al tooling viejo)
--------------------------------------------------------------------
Esta versión de FastAPI NO mete las rutas de un `include_router` directo en
`app.routes`: mete un envoltorio `_IncludedRouter` y las rutas reales quedan en
`.original_router.routes`. Un `for r in app.routes` ingenuo ve **5 rutas de las
428** y no falla — devuelve poco y en silencio. Por eso `scripts/audit_rbac.py`
y `tests/unit/test_rbac_superficie.py` estaban ciegos: sus invariantes de
seguridad pasaban sobre un conjunto casi vacío.

Lo mismo pasa con los GATES: las `dependencies=[...]` que se pasan en
`app.include_router(...)` tampoco bajan a cada ruta — viven en
`route.include_context.dependencies` del envoltorio. El gate EFECTIVO de un
endpoint es la unión de:
  1. las dependencies del include (nivel app, en el envoltorio),
  2. las dependencies del `APIRouter(...)` y del propio decorador (nivel ruta).
Este script mira las dos. Si mirás solo una, te mentís.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

from api.auth import ENDPOINT_MODULE_PREFIXES, get_module_for_path
from api.main import app
from core.roles import DEFAULT_MATRIX, MODULES

DOC = Path(__file__).resolve().parents[1] / "docs" / "MAPA_APP.md"
ESCRITURA = {"POST", "PUT", "PATCH", "DELETE"}


# ── Recorrido REAL de la app ─────────────────────────────────────────────────

def _rutas(routes=None) -> list[dict]:
    """Delega en `api/superficie.py` — **la única forma de recorrer la superficie**.

    Su versión propia tenía DOS bugs que este generador existía para evitar:
    veía bien las 541 rutas pero **duplicaba el prefijo en 395 de ellas**
    (`/api/ia/api/ia/observabilidad`), porque el `prefix` de un `APIRouter` ya
    viene aplicado a sus propias `APIRoute` y se lo volvía a sumar. Los gates
    estaban bien; los paths publicados en MAPA_APP.md §0, no.
    """
    from api import superficie

    return [{"path": r.path, "router": r.router, "metodos": set(r.metodos),
             "gates": list(r.gates)}
            for r in superficie.rutas(app if routes is None else None)]


def _modulo_de_gate(nombre: str) -> str | None:
    """`require_module_back_office` → `back-office`. None si no es gate de módulo."""
    if not nombre.startswith("require_module_"):
        return None
    crudo = nombre[len("require_module_"):]
    # Los guiones del módulo llegan como guión bajo (no son identificadores
    # válidos): se resuelve contra la lista canónica en vez de adivinar.
    for m in MODULES:
        if m.replace("-", "_") == crudo:
            return m
    return crudo


# ── Bloques ──────────────────────────────────────────────────────────────────

def _bloques() -> dict[str, str]:
    rutas = _rutas(app.routes)

    por_router: dict[str, list[dict]] = defaultdict(list)
    for r in rutas:
        por_router[r["router"]].append(r)

    escriben = [r for r in rutas if r["metodos"] & ESCRITURA]

    # ── resumen ──
    resumen = (
        f"- **{len(rutas)} endpoints** montados en `api.main.app`, en "
        f"**{len(por_router)} routers**.\n"
        f"- **{len(escriben)} escriben** (POST/PUT/PATCH/DELETE); "
        f"{len(rutas) - len(escriben)} son de solo lectura.\n"
        f"- **{len(MODULES)} módulos** canónicos y **{len(DEFAULT_MATRIX)} roles** "
        f"en `core/roles.py`."
    )

    # ── tabla por router ──
    filas = ["| Router | Rutas | Escriben | Gate efectivo | Módulo declarado | |",
             "|---|---:|---:|---|---|---|"]
    sin_gate: list[str] = []
    for prefijo in sorted(por_router):
        rs = por_router[prefijo]
        n_esc = sum(1 for r in rs if r["metodos"] & ESCRITURA)
        # El gate del ROUTER es el que tienen TODAS sus rutas (intersección). La
        # unión mentiría: alcanzaba UNA ruta con `require_module("manager")` para
        # que todo /api/cotizaciones figurara como admin-only. Lo que solo tienen
        # algunas rutas se reporta aparte como "gate extra".
        por_ruta = [set(r["gates"]) for r in rs]
        comunes = set.intersection(*por_ruta) if por_ruta else set()
        extras = set().union(*por_ruta) - comunes if por_ruta else set()
        gates = sorted(comunes)
        modulos = sorted({m for g in comunes if (m := _modulo_de_gate(g))})
        n_extra = sum(1 for s in por_ruta if s & extras)
        # Manager gatea CADA sub-router con un módulo distinto (`manager_clientes`,
        # `manager_titulos`, …): la intersección queda vacía aunque no haya una
        # sola ruta abierta. Lo que importa para el ⚠️ es si alguna ruta se queda
        # SIN ningún gate de módulo, no si todas comparten el mismo.
        def _tiene_modulo(s: set[str]) -> bool:
            return any(g.startswith(("require_module_", "require_any_module")) for g in s)
        sin_modulo = sum(1 for s in por_ruta if not _tiene_modulo(s))
        if not modulos and sin_modulo == 0:
            efectivo_varia = True
        else:
            efectivo_varia = False
        # `get_module_for_path` NO se aplica en runtime (solo lo usa un test):
        # es la INTENCIÓN declarada. Compararla con el gate real es justamente
        # lo que detecta el drift.
        declarado = get_module_for_path(prefijo if prefijo != "(raíz)" else "/")
        if efectivo_varia:
            efectivo = "varía por ruta (todas gateadas)"
        else:
            efectivo = ", ".join(f"`{m}`" for m in modulos) or "—"
        otros = [g for g in gates if g.startswith(("require_admin", "require_escritura",
                                                   "require_no_invitado", "require_any_module",
                                                   "require_control", "verify_ingest"))]
        if otros:
            efectivo += (" + " if modulos else "") + ", ".join(f"`{o}`" for o in sorted(set(otros)))
        if n_extra and not efectivo_varia:
            efectivo += f" · {n_extra} ruta{'s' if n_extra > 1 else ''} con gate extra"
        marca = ""
        if declarado and not efectivo_varia and declarado not in modulos:
            marca = "⚠️"
            sin_gate.append(f"`{prefijo}` (declara `{declarado}`, no lo aplica)")
        elif sin_modulo and not otros:
            marca = "⚠️"
            sin_gate.append(f"`{prefijo}` ({sin_modulo} de {len(rs)} rutas sin gate de módulo)")
        filas.append(f"| `{prefijo}` | {len(rs)} | {n_esc} | {efectivo} | "
                     f"{f'`{declarado}`' if declarado else '—'} | {marca} |")
    tabla_routers = "\n".join(filas)

    aviso = (
        "\n\n**⚠️ Routers sin gate de módulo, o cuyo gate real no coincide con el "
        "módulo que declaran en `ENDPOINT_MODULE_PREFIXES`:**\n\n"
        + "\n".join(f"- {s}" for s in sin_gate)
        + "\n\nNo es necesariamente un bug: `ENDPOINT_MODULE_PREFIXES` **no se "
          "aplica en runtime** (solo lo consume un test), y para los módulos que "
          "todos los roles tienen se decidió no gatear. Lo que sí implica es que "
          "**destildar esos módulos en Manager → Roles no bloquea nada server-side**: "
          "solo esconde el link en el menú.\n"
        if sin_gate else ""
    )

    # ── matriz rol × módulo ──
    roles = list(DEFAULT_MATRIX)
    cab = "| Módulo | " + " | ".join(roles) + " |"
    sep = "|---|" + "|".join([":-:"] * len(roles)) + "|"
    mfilas = [cab, sep]
    for m in MODULES:
        celdas = ["✓" if m in DEFAULT_MATRIX[rol] else "·" for rol in roles]
        mfilas.append(f"| `{m}` | " + " | ".join(celdas) + " |")
    matriz = "\n".join(mfilas) + (
        "\n\n> Esta matriz es el **DEFAULT del código** (`core/roles.py::DEFAULT_MATRIX`). "
        "La tabla SQL `manager.role_matrix` la **PISA**: el enforcement real es lo que "
        "esté ahí, editable desde `/manager → ROLES Y PERMISOS`. Para ver la de "
        "producción hay que consultarla en la base."
    )

    return {
        "resumen": resumen,
        "routers": tabla_routers + aviso,
        "rbac": matriz,
    }


def _inject(texto: str, bloques: dict[str, str]) -> str:
    for clave, contenido in bloques.items():
        pat = re.compile(rf"(<!-- AUTOGEN:{clave} -->).*?(<!-- /AUTOGEN:{clave} -->)",
                         re.DOTALL)
        if not pat.search(texto):
            raise SystemExit(
                f"❌ falta el marcador <!-- AUTOGEN:{clave} --> en {DOC.name}. "
                f"Agregalo (con su cierre) donde quieras que se inyecte el bloque.")
        # `contenido` se liga como default: sin eso el lambda leería la variable
        # del loop al ejecutarse y todos los bloques quedarían con el último.
        texto = pat.sub(lambda m, c=contenido: f"{m.group(1)}\n{c}\n{m.group(2)}", texto)
    return texto


def main() -> None:
    if "--full" in sys.argv:
        rutas = sorted(_rutas(app.routes), key=lambda r: r["path"])
        for r in rutas:
            metodos = ",".join(sorted(r["metodos"] - {"HEAD", "OPTIONS"}))
            marca = "W" if r["metodos"] & ESCRITURA else " "
            print(f"{marca} {metodos:18s} {r['path']}")
        print(f"\n{len(rutas)} rutas.")
        return

    if not DOC.exists():
        raise SystemExit(f"❌ no existe {DOC}. Este script NO crea el doc: la narrativa "
                         f"se escribe a mano y los bloques AUTOGEN se inyectan acá.")
    original = DOC.read_text(encoding="utf-8")
    nuevo = _inject(original, _bloques())

    if "--check" in sys.argv:
        if nuevo != original:
            print("❌ docs/MAPA_APP.md está DESINCRONIZADO con el código.\n"
                  "   Corré: python -m scripts.gen_mapa_app")
            raise SystemExit(1)
        print("✅ docs/MAPA_APP.md sincronizado.")
        return

    if nuevo == original:
        print("✅ docs/MAPA_APP.md ya estaba al día (sin cambios).")
        return
    DOC.write_text(nuevo, encoding="utf-8")
    print(f"✅ docs/MAPA_APP.md actualizado ({len(_rutas(app.routes))} endpoints, "
          f"{len(ENDPOINT_MODULE_PREFIXES)} prefijos declarados).")


if __name__ == "__main__":
    main()
