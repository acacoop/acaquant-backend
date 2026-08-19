"""api/superficie.py — LA ÚNICA FORMA DE RECORRER LA SUPERFICIE HTTP.

**Por qué existe** (2026-08-19). Había TRES lugares que recorrían las rutas de la
app para auditarlas —`scripts/gen_mapa_app.py`, `scripts/audit_rbac.py` y
`tests/unit/test_rbac_superficie.py`— y **dos de los tres estaban ciegos**:

    gen_mapa_app (bien)                       541 rutas
    audit_rbac · test_rbac_superficie          37 rutas

O sea que el test llamado «RBAC de superficie» auditaba el **7%** de la
superficie, y **pasaba en verde**. Un endpoint sin gate en cualquiera de las 504
restantes no lo cazaba nadie. No es una hipótesis: está medido.

LA TRAMPA DE FASTAPI, ESCRITA UNA SOLA VEZ
===========================================

`app.routes` **no devuelve las rutas de los `include_router`**: devuelve
envoltorios `_IncludedRouter`, y las rutas reales cuelgan de
`.original_router.routes`. Peor: los includes se ANIDAN (los 28 sub-routers de
Manager entran a `manager.router` y recién ese va a la app), y en esa cadena

  · `route.path` del hijo **no trae el prefijo del padre** (dice
    `/aunesa/boletos`, no `/api/manager/aunesa/boletos`), y
  · las `dependencies=` del include **no bajan a cada ruta**: viven en
    `route.include_context`.

Un `for r in app.routes` ingenuo no explota — **devuelve poco, en silencio**. Es
el peor modo de falla posible para una herramienta de seguridad: no avisa que no
sabe, afirma que está todo bien.

Por eso esto es UN módulo y no un helper copiado: mientras la técnica viva en
tres lados, dos van a quedar viejos. Ya pasó.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.routing import APIRoute

# Los gates que ALCANZAN para considerar una ruta protegida por identidad.
# `require_module_*`/`require_any_module_*` se detectan por prefijo (son
# generados) y por eso no están acá.
GATES_DUROS = frozenset({"require_admin", "require_manager", "require_no_invitado"})


@dataclass
class Ruta:
    """Una ruta REAL de la app, con su path completo y su gate EFECTIVO."""
    path: str
    metodos: frozenset[str]
    # Todas las dependencies que aplican: las heredadas del include (que no
    # bajan solas) + las del APIRouter + las del decorador.
    gates: tuple[str, ...] = ()
    router: str = ""
    endpoint: object = None
    # Los módulos RBAC que exige, resueltos (`require_module_back_office` →
    # `back-office`). Vacío = no exige ninguno.
    modulos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def escribe(self) -> bool:
        return bool(self.metodos & {"POST", "PUT", "PATCH", "DELETE"})

    @property
    def pide_bearer(self) -> bool:
        return "verify_api_key" in self.gates

    @property
    def es_admin(self) -> bool:
        return "require_admin" in self.gates or "require_manager" in self.gates

    @property
    def sin_gate(self) -> bool:
        """Ni bearer, ni módulo, ni gate duro, ni token propio. **Alcanzable por
        cualquiera que sepa la URL.**"""
        return not (self.pide_bearer or self.modulos or self.gate_duro
                    or "verify_ingest_token" in self.gates)

    @property
    def gate_duro(self) -> str | None:
        return next((g for g in self.gates if g in GATES_DUROS), None)


def rutas(app=None) -> list[Ruta]:
    """**TODAS** las rutas de la app, con prefijo y gates acumulados.

    Es la función que las tres herramientas tienen que usar. Si alguien vuelve a
    escribir `for r in app.routes` para auditar algo, va a ver el 7%.
    """
    if app is None:
        from api.main import app as _app
        app = _app
    return sorted(_bajar(app.routes), key=lambda r: (r.path, sorted(r.metodos)))


def _bajar(routes, prefijo: str = "", heredados: tuple[str, ...] = (),
           router: str = "(raíz)") -> list[Ruta]:
    """El nivel de la app. Cada `_IncludedRouter` se baja con `_bajar_router`."""
    out: list[Ruta] = []
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            out.extend(_bajar_router(r, prefijo, heredados))
        elif isinstance(r, APIRoute):
            out.append(_ruta(r, prefijo, heredados, router))
    return [r for r in out if r.metodos]


def _bajar_router(inc, heredado: str, heredados: tuple[str, ...]) -> list[Ruta]:
    """⚠️ **La regla que `gen_mapa_app` tenía mal, y por eso publicaba 395 de 541
    paths con el prefijo DUPLICADO** (`/api/ia/api/ia/observabilidad`).

    El `prefix` de un `APIRouter` **ya está aplicado a sus propias `APIRoute`** —
    FastAPI lo concatena al registrarlas— pero **NO a las rutas de los routers
    que él incluye**, porque `_IncludedRouter` es lazy y no las copia.

    O sea que el prefijo faltante es distinto para cada tipo de hijo:

        APIRoute propia   →  le falta el de los ANCESTROS (ya tiene el propio)
        include anidado   →  le falta ancestros + el propio

    Sumarle el propio a las dos cosas es lo que duplicaba. Y no explotaba: daba
    un path que no existe, que es peor — cualquier herramienta que intente
    PROBAR esa URL (como el chequeo de endpoints sin gate) recibiría un 404 y
    concluiría que está protegida.
    """
    ctx = inc.include_context
    propio = inc.original_router.prefix or ""
    gates = heredados + tuple(getattr(d.dependency, "__name__", "?")
                              for d in (getattr(ctx, "dependencies", None) or []))
    etiqueta = (heredado + propio) or "(raíz)"
    out: list[Ruta] = []
    for r in inc.original_router.routes:
        if type(r).__name__ == "_IncludedRouter":
            out.extend(_bajar_router(r, heredado + propio, gates))
        elif isinstance(r, APIRoute):
            out.append(_ruta(r, heredado, gates, etiqueta))
    return out


def _ruta(r: APIRoute, prefijo: str, gates: tuple[str, ...], router: str) -> Ruta:
    propios = _gates_propios(r)
    todos = tuple(gates) + tuple(propios)
    return Ruta(
        path=prefijo + r.path,
        metodos=frozenset(m for m in (r.methods or ())
                          if m not in ("HEAD", "OPTIONS")),
        gates=todos, router=router, endpoint=r.endpoint,
        modulos=_modulos(r, todos))


def _gates_propios(route: APIRoute) -> list[str]:
    """Las dependencies que SÍ bajan a la ruta, recursivo.

    Recursivo porque una dependency puede declarar otras (`require_module` usa
    `get_user_email`), y el gate que importa puede estar un nivel adentro.
    """
    out, stack = [], list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        call = getattr(d, "call", None)
        if call is not None:
            out.append(getattr(call, "__name__", ""))
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


def _modulos(route: APIRoute, gates: tuple[str, ...]) -> tuple[str, ...]:
    """Los módulos RBAC que la ruta exige, RESUELTOS a su nombre canónico.

    Se leen de dos formas porque las dos existen: el atributo `rbac_modules` que
    `require_module` cuelga de la función (lo confiable) y, para los gates
    heredados de un include —donde no tenemos el objeto sino el nombre—, el
    parseo del nombre generado contra la lista canónica.
    """
    from core.roles import MODULES

    # 1) Lo confiable: el atributo, si tenemos el callable a mano.
    stack = list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        mods = getattr(getattr(d, "call", None), "rbac_modules", None)
        if mods:
            return tuple(mods)
        stack.extend(getattr(d, "dependencies", []) or [])

    # 2) Los heredados llegan como nombre: `require_module_back_office`. El
    #    módulo tiene guiones y el nombre guión bajo, así que NO se adivina —
    #    se resuelve contra `MODULES`.
    out: list[str] = []
    for g in gates:
        for pref in ("require_module_", "require_any_module_"):
            if not g.startswith(pref):
                continue
            crudo = g[len(pref):]
            out.extend(m for m in MODULES if m.replace("-", "_") == crudo)
    return tuple(dict.fromkeys(out))


def resumen(app=None) -> dict:
    """Contadores de la superficie. Lo consume el AV AGENT."""
    rs = rutas(app)
    return {
        "total": len(rs),
        "sin_gate": [r.path for r in rs if r.sin_gate],
        "escrituras": sum(1 for r in rs if r.escribe),
        "escrituras_sin_gate": [r.path for r in rs if r.escribe and r.sin_gate],
        "admin_only": sum(1 for r in rs if r.es_admin),
        "con_modulo": sum(1 for r in rs if r.modulos),
    }
