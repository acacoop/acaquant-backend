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
    no se puede derivar— CUÁNDO CORRIÓ por última vez.

    ⚠️ **Alimenta DOS pantallas, no una** (§0.ev): el listado de HABILIDADES y
    el MAPA —la misma tabla leída como estructura, para contestar qué NO se
    está mirando—. El mapa no agregó ni un campo: se dibuja con lo que esta
    respuesta ya traía, `ultima_corrida_at` y `ultimo_resultado` incluidos, que
    es lo que lo convierte en un tablero de salud y no en un diagrama muerto.
    Un endpoint aparte que contara lo mismo habría sido una copia sin árbitro."""
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


class NoSonContrapartes(BaseModel):
    id: int = Field(..., ge=1)
    cuentas: list[str] = Field(default_factory=list, max_length=500)
    todas: bool = False


@router.post("/contrapartes/no-interesan")
def contrapartes_no_interesan(body: NoSonContrapartes,
                              email: str = Depends(get_user_email)):
    """«Estas NO son contraparte» (por cuenta), no el aviso entero: dejan de
    ofrecerse y el aviso vuelve solo con las cuentas institucionales nuevas
    (`clientes.contrapartes_descartadas`, §0.es)."""
    from agente import vista as v
    return v.no_interesan_contrapartes(body.id, body.cuentas, todas=body.todas,
                                       por=email)


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



# ── EL LABORATORIO — el asistente conversacional ───────────────────────────
#
# La tab LAB del modal. Es la única boca HTTP de `asistente/`: acá no hay
# lógica, solo se traduce un pedido de la pantalla a una llamada a `ciclo` y se
# junta lo que el ciclo va contando por el camino.
#
# ⚠️ **SÍNCRONO A PROPÓSITO, Y ESTÁ MEDIDO.** Una pregunta con una herramienta
# tarda 4,5 s de punta a punta; el peor caso (6 vueltas) queda por debajo de los
# 30 s que aguanta el proxy de Vercel. Una cola con estado sería infraestructura
# para un problema que todavía no existe.

class Preguntar(BaseModel):
    pregunta: str = Field(..., min_length=1, max_length=2000)
    # Los mensajes de las preguntas anteriores, tal cual los devolvió la
    # respuesta anterior. El modelo no recuerda nada: la conversación la
    # sostiene la pantalla mandando esto de vuelta.
    historial: list[dict] = Field(default_factory=list, max_length=60)
    # Lo que quedó en foco (la cuenta de la que se viene hablando), tal cual lo
    # devolvió la respuesta anterior. Viaja APARTE del historial porque el
    # achicado del historial no lo toca. El backend lo reduce a lo declarado
    # en `asistente/estado.py` antes de usarlo: acá sólo se recibe.
    estado: dict = Field(default_factory=dict)


@router.post("/lab/preguntar")
def lab_preguntar(body: Preguntar, email: str = Depends(get_user_email)):
    """Una pregunta al asistente. Devuelve la respuesta MÁS todo lo que pasó.

    `eventos` es la traza del ciclo paso por paso (qué herramienta pidió, con
    qué argumentos, qué devolvió). Viaja a la pantalla porque el punto de esta
    tab es VER el ciclo, no solo su resultado.
    """
    from asistente import ciclo

    eventos: list[dict] = []
    r = ciclo.preguntar(body.pregunta, usuario=email, historial=body.historial,
                        estado=body.estado, ver=eventos.append)
    return {**r, "eventos": eventos}


# ── EL PANEL DEL LAB — qué gastamos y con qué modelo corremos ───────────────
#
# Lectura y escritura de `asistente/panel.py`. Acá no hay lógica: la superficie
# HTTP del asistente son estos tres endpoints más el de preguntar.

@router.get("/lab/panel")
def lab_panel(dias: int = 30):
    """Todo el panel en UN request: gasto por tarea, hit rate del caché, y qué
    modelo cumple cada rol en cada proveedor."""
    from asistente import panel
    return panel.vista(dias)


class ElegirModelo(BaseModel):
    # La TAREA, no un rol: `asistente`, `agente_texto`, … Una fila de la
    # pantalla es una cosa que corre.
    tarea: str = Field(..., min_length=1, max_length=48)
    proveedor: str = Field("", max_length=32)
    modelo: str = Field("", max_length=120)


@router.post("/lab/modelo")
def lab_modelo(body: ElegirModelo, email: str = Depends(get_user_email)):
    """Deja fijado con qué proveedor y modelo corre UNA tarea. **Prueba antes de
    guardar**: si el modelo no sirve, no se guarda y queda el anterior.

    Eso no es un paso opcional que la pantalla podría saltear — vive adentro de
    `panel.elegir_modelo`. Qué se le exige depende de la tarea: si ofrece
    herramientas, el modelo tiene que PEDIR una; un modelo que ignora `tools`
    deja al asistente contestando de memoria, sin un solo error.

    Sin `modelo` vuelve al default que declara `core/ai.py` — «elegí mal y
    quiero deshacerlo» no se resuelve eligiendo otra cosa.
    """
    from asistente import panel
    if not body.modelo.strip():
        return panel.volver_al_default(body.tarea, por=email)
    return panel.elegir_modelo(body.tarea, body.proveedor, body.modelo, por=email)


class PonerPrecio(BaseModel):
    modelo: str = Field(..., min_length=1, max_length=120)
    # USD por MILLÓN de tokens. Los TRES juntos: una tarifa a medias calcula un
    # costo equivocado sin fallar.
    entrada: float = Field(..., ge=0, le=10_000)
    cache: float = Field(..., ge=0, le=10_000)
    salida: float = Field(..., ge=0, le=10_000)


@router.post("/lab/precio")
def lab_precio(body: PonerPrecio, email: str = Depends(get_user_email)):
    """La tarifa de un modelo, para poder ver plata y no sólo tokens.

    Son TRES precios y el del caché es el que más cambia el número: la entrada
    que pega en el caché del proveedor cuesta una fracción (en gpt-5.6-luna,
    diez veces menos). Cobrar todo a precio de entrada infla la factura justo en
    la parte que venimos optimizando.

    Van en `ia.config` y no en el código: una tarifa vieja hardcodeada no falla,
    miente — y encima se usa para decidir.
    """
    from asistente import panel
    return panel.poner_precio(body.modelo, entrada=body.entrada, cache=body.cache,
                              salida=body.salida, por=email)
