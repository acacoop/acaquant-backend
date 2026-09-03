"""`api/routers/agente.py` — el AV AGENT por HTTP. Doc: `docs/AGENT.md`.

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


class Explicar(BaseModel):
    habilidad: str = Field(..., min_length=1, max_length=64)


@router.post("/explicar")
def explicar(body: Explicar, email: str = Depends(get_user_email)):
    """«Explicámelo»: el último error de esa habilidad, contado por la IA con el
    código, las fuentes y el diario en la mano (`agente/explicar.py`, §0.dh).
    Solo a pedido, cacheado por error, con la firma de quién lo pidió.
    """
    from agente import explicar as ex
    return ex.explicar(body.habilidad, por=email)


# ── EL LABORATORIO (`lab/langgraph/`) ──────────────────────────────────────
#
# El INVESTIGADOR: cuando el agente detecta algo y se queda ahí —16 de sus 24
# habilidades son avisos sin botón—, esto averigua por qué y propone qué hacer.
#
# ⚠️ **NO HAY UN ENDPOINT QUE INVESTIGUE Y DEVUELVA EL RESULTADO.** Una
# investigación son 8 a 18 idas y vueltas al modelo: uno o dos minutos. El
# proxy de Next corta a los 30 s (`maxDuration`) y ese corte se ve en pantalla
# **idéntico a un backend caído**. Así que se PIDE (contesta en milisegundos con
# un número) y después se PREGUNTA cómo va. Lo corre el daemon del agente.
#
# ⚠️ Estas rutas heredan `require_admin` del router, como todas. No hay que
# acordarse: está puesto en el `APIRouter`, no en cada función.
class Investigar(BaseModel):
    tipo: str = Field(..., min_length=1, max_length=40)
    caso: str = Field(..., min_length=1, max_length=120)


@router.post("/lab/investigar")
def lab_investigar(body: Investigar, email: str = Depends(get_user_email)):
    """Encola una investigación. **No la corre**: devuelve el id para seguirla.

    Si ya hay una igual sin terminar devuelve ESA en vez de encolar otra: dos
    investigaciones del mismo caso a la vez son el mismo trabajo hecho dos
    veces, y pagado dos veces.
    """
    from lab.langgraph import cola
    from lab.langgraph.investigaciones import INVESTIGACIONES
    if body.tipo not in INVESTIGACIONES:
        return {"ok": False, "error": f"«{body.tipo}» no es un tipo de "
                f"investigación. Hay: {', '.join(INVESTIGACIONES)}"}
    return cola.encolar(body.tipo, body.caso.strip(), por=email)


@router.get("/lab/pedido/{pedido_id}")
def lab_pedido(pedido_id: int):
    """Cómo va un pedido: su estado, los pasos hechos HASTA AHORA, y el
    veredicto si ya terminó. Es lo que pollea la pantalla mientras corre."""
    from lab.langgraph import cola
    p = cola.ver(pedido_id)
    return p or {"ok": False, "error": "ese pedido no existe"}


@router.get("/lab")
def lab(limite: int = 20):
    """La tab LAB: los últimos pedidos y qué sabe investigar.

    El catálogo viaja con la lista porque la pantalla **no puede inventarse los
    tipos**: si los tuviera escritos en el navegador, agregar una investigación
    nueva del lado del backend no aparecería, y sacar una dejaría un botón que
    falla. Es la misma ley que el resto del modal — nada se deriva acá.
    """
    from lab.langgraph import cola
    from lab.langgraph.investigaciones import INVESTIGACIONES
    r = cola.ultimos(limite)
    return {
        # `ok` viene de si se pudo LEER, no se pone a mano: una lista vacía y
        # una lectura fallida no se pueden ver iguales en la pantalla.
        "ok": r["ok"], "error": r["error"],
        "pedidos": r["pedidos"],
        "tipos": [{"nombre": i.nombre, "que_es": i.que_es,
                   "piso": list(i.piso)} for i in INVESTIGACIONES.values()],
    }
