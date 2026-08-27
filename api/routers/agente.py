"""`api/routers/agente.py` — el AV AGENT por HTTP. Doc: `docs/AGENT_2.0.md`.

Solo plumbing: la lógica vive en `agente/`. Se monta con el mismo gate que
`/api/ia` (bearer + módulo `ia`) **y además `require_admin` en cada ruta**: el
agente habla del estado interno del sistema, y con la matriz de roles dándole
`ia` a la mesa, gatearlo solo por módulo se lo mostraría a un comercial.

⚠️ REGLA #8 del repo: ningún endpoint de acá puede quedar alcanzable desde el
portal invitado. `require_admin` en TODAS las rutas es lo que lo garantiza, y
`test_rbac` falla si alguna se olvida.

**Tres pantallas, tres lecturas.** El agente viejo tenía 44 endpoints para un
modal de siete tabs, con el contador principal sumándose en el navegador a
partir de dos de ellos con frescuras distintas.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth import get_user_email, require_admin

router = APIRouter(prefix="/api/agente", tags=["agente"],
                   dependencies=[Depends(require_admin)])


# ── LEER ───────────────────────────────────────────────────────────────────
@router.get("/vista")
def vista():
    """Todo el modal en UN request y con UNA sola noción de «ahora»."""
    from agente import vista as v
    return v.vista()


@router.get("/ahora")
def ahora():
    """Los hallazgos de HOY sin leer. El badge y la lista, de la misma query."""
    from agente import vista as v
    return v.ahora()


@router.get("/encontro")
def encontro():
    """Lo abierto que TIENE ARREGLO. Un aviso no entra acá."""
    from agente import vista as v
    return v.encontro()


@router.get("/historial")
def historial(limite: int = 200, desde_id: int | None = None,
              q: str = "", solo_malas: bool = False):
    """El LIBRO, paginado del backend — con el filtro también del backend."""
    from agente import vista as v
    return v.historial(limite=limite, desde_id=desde_id, q=q,
                       solo_malas=solo_malas)


@router.get("/reincidencias")
def reincidencias(limite: int = 100):
    """**La que debe estar vacía.** Si trae filas, un arreglo no sirvió."""
    from agente import vista as v
    return v.reincidencias(limite)


@router.get("/habilidades")
def habilidades():
    """El catálogo con sus contadores: cuántos hallazgos, el último, y —lo que
    no se puede derivar— CUÁNDO CORRIÓ por última vez."""
    from agente import catalogo
    return {"habilidades": catalogo.estado()}


# ── ESCRIBIR ───────────────────────────────────────────────────────────────
class Leidos(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)
    deshacer: bool = False


@router.post("/leidos")
def leidos(body: Leidos, email: str = Depends(get_user_email)):
    """«Ya me enteré». Lo saca de AHORA y de ningún otro lado: **leer no
    resuelve** — el hallazgo sigue abierto y sigue con su botón."""
    from agente import vista as v
    if body.deshacer:
        return v.desmarcar_leidos(body.ids)
    return v.marcar_leidos(body.ids, por=email)


class UnHallazgo(BaseModel):
    id: int = Field(..., ge=1)


@router.post("/preview")
def preview(body: UnHallazgo):
    """CALCULA qué escribiría el arreglo. **No muta.**

    Se recalcula al mirar y `aplicar` vuelve a calcular antes de escribir: lo
    que se aplica es lo cierto AHORA, no lo que era cierto cuando alguien abrió
    la pantalla.
    """
    from agente import arreglos
    return arreglos.preview(body.id)


class Aplicar(BaseModel):
    id: int = Field(..., ge=1)
    # Lo que se cargó en el listado editable, para los arreglos que declaran
    # `pide_datos` (hoy solo `completar_ficha`). El resto lo ignora.
    #
    # ⚠️ **ESTO VIENE DEL NAVEGADOR Y NO SE ESCRIBE COMO LLEGA.** El arreglo
    # recalcula el conjunto permitido antes de tocar nada: sin eso, esta puerta
    # aceptaría escribir cualquier unidad del catálogo, incluida una que el
    # detector no está mirando.
    datos: list[dict] | None = None


@router.post("/aplicar")
def aplicar(body: Aplicar, email: str = Depends(get_user_email)):
    """ESCRIBE. Deja la línea en el libro y el hallazgo queda `en_curso`:

    que la escritura saliera bien **no prueba que el problema se fue** — eso lo
    dice el detector la próxima vez que mire, y solo ese cierre habilita la
    reincidencia.
    """
    from agente import arreglos
    return arreglos.aplicar(body.id, por=email, datos=body.datos)


class Ignorar(BaseModel):
    id: int = Field(..., ge=1)
    deshacer: bool = False
    motivo: str = Field("", max_length=200)


@router.post("/ignorar")
def ignorar(body: Ignorar, email: str = Depends(get_user_email)):
    """«No me interesa». Reversible, y **no es un arreglo**: esconde, no resuelve.

    Silencia el PROBLEMA (habilidad + sujeto + regla), no la fila: si no, el
    detector lo vuelve a crear en la pasada siguiente. `deshacer` lo revive.
    """
    from agente import vista as v
    return v.ignorar(body.id, por=email, motivo=body.motivo,
                     deshacer=body.deshacer)


class Correr(BaseModel):
    habilidad: str = Field("", max_length=64)


@router.post("/correr")
def correr(body: Correr):
    """Corre UNA habilidad ahora, o una pasada entera si no se nombra ninguna.

    Existe para no tener que entrar al Droplet a pedir lo que la pantalla ya
    está mostrando desactualizado.
    """
    from agente import fuentes, motor
    fuentes.refrescar()
    if body.habilidad:
        return motor.correr_una(body.habilidad)
    r = motor.tick()
    motor.latir(r)
    return r
