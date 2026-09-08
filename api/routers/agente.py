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
    d = v.vista()
    _marcar_investigables(d)
    return d


def _marcar_investigables(d: dict) -> None:
    """Le pone `investigable` a cada hallazgo. **Acá y no en el navegador.**

    La pantalla dibuja el botón «investigar» sólo donde el backend sabe
    investigar. Si el mapa estuviera en el front, habría DOS copias de la misma
    verdad (REGLA #9): agregar una investigación no mostraría el botón y sacar
    una dejaría uno que falla, sin que nada avise.

    ⚠️ Va en el ROUTER y no en `agente/vista.py` a propósito: `agente/` no puede
    depender del laboratorio. Si el lab no está o revienta, el modal se dibuja
    igual — sin el botón, que es la degradación correcta.
    """
    try:
        from lab.langgraph.investigaciones import tipo_de
    except Exception:
        return
    for clave in ("ahora", "encontro"):
        for f in (d.get(clave) or {}).get("filas") or []:
            f["investigable"] = bool(tipo_de(f.get("habilidad", "")))


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


@router.get("/cronicos")
def cronicos(limite: int = 60):
    """**LO QUE PASA SIEMPRE** — el ranking que dice dónde están las MEJORAS.

    Un problema que aparece treinta veces en un mes no es un incidente: es una
    configuración mal puesta, y arreglarlo cada vez lo tapa. Mirando un hallazgo
    por vez los dos casos se ven idénticos; sólo el patrón los separa.
    """
    from agente import vista as v
    return v.cronicos(limite)


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


class NoInteresan(BaseModel):
    id: int = Field(..., ge=1)
    tickers: list[str] = Field(default_factory=list, max_length=500)
    todas: bool = False


@router.post("/ons/no-interesan")
def ons_no_interesan(body: NoInteresan, email: str = Depends(get_user_email)):
    """«No me interesan ESTAS» (por ticker), no el aviso entero: las
    descartadas dejan de contarse y el aviso vuelve solo cuando 1816 publique
    una que no esté en la lista (`mercado.ons_ignoradas`, §0.eh)."""
    from agente import vista as v
    return v.no_interesan_ons(body.id, body.tickers, todas=body.todas, por=email)


@router.post("/cedears/no-interesan")
def cedears_no_interesan(body: NoInteresan, email: str = Depends(get_user_email)):
    """«No me interesan ESTOS» (por ticker), no el aviso entero: los
    descartados dejan de contarse y el aviso vuelve solo cuando Primary liste
    uno nuevo (quedan apagados en `mercado.cedears`, §0.eh)."""
    from agente import vista as v
    return v.no_interesan_cedears(body.id, body.tickers, todas=body.todas, por=email)


class Correr(BaseModel):
    habilidad: str = Field("", max_length=64)


@router.post("/correr")
def correr(body: Correr):
    """Corre UNA habilidad ahora, o una pasada entera si no se nombra ninguna.

    Existe para no tener que entrar al Droplet a pedir lo que la pantalla ya
    está mostrando desactualizado.

    ⚠️ **`forzar=True`, y esa es la diferencia con el daemon** (§0.dz). Lo aprieta
    una persona: anula el RITMO —«con cada 2 h alcanza» es una decisión de
    frecuencia y el botón la está anulando a propósito— pero **nunca la
    ventana**, que es una condición del mundo. Sin esto el botón corría
    exactamente lo mismo que el daemon iba a correr solo, o sea casi siempre
    nada, y parecía roto.
    """
    from agente import fuentes, motor
    fuentes.refrescar()
    if body.habilidad:
        return motor.correr_una(body.habilidad)
    # ⚠️ Presupuesto CORTO: del otro lado hay un proxy que corta a los 30 s
    # (§0.eb). Lo que no entra vuelve en `faltaron` y se resuelve apretando de
    # nuevo — mejor que un botón que falla entero.
    r = motor.tick(forzar=True, presupuesto_s=motor.PRESUPUESTO_PEDIDO_S)
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
# El INVESTIGADOR: cuando el agente detecta algo y se queda ahí —la mayoría de
# sus habilidades son avisos sin botón—, esto averigua por qué y propone qué
# hacer.
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
    # ⚠️ **LOS CASOS QUE SE PUEDEN INVESTIGAR VIENEN DE ACÁ, NO DE UN CAMPO DE
    # TEXTO.** Antes había que adivinar qué escribir. Ahora la pantalla ofrece
    # lo que está REALMENTE abierto, derivado de los hallazgos y reincidencias
    # del agente: lo nuevo aparece solo y lo resuelto desaparece solo.
    inv = cola.investigables()
    return {
        "casos": inv["casos"],
        "casos_error": inv["error"],
        # `ok` viene de si se pudo LEER, no se pone a mano: una lista vacía y
        # una lectura fallida no se pueden ver iguales en la pantalla.
        "ok": r["ok"], "error": r["error"],
        "pedidos": r["pedidos"],
        "tipos": [{"nombre": i.nombre, "que_es": i.que_es,
                   "piso": list(i.piso)} for i in INVESTIGACIONES.values()],
    }
