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

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from api.auth import get_user_email, is_guest_portal, require_admin

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
    ventana**, que es una condición del agente. Sin esto el botón corría
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



# ── EL DIAGNÓSTICO de un hallazgo (asistente/diagnostico.py, AvAgentAI.md §15) ──
#
# Lo que AHORA muestra es la conclusión; esto es el CICLO que la produjo, para
# verlo en el LAB: cada vuelta, qué dijo el modelo, qué pidió, qué le volvió.
# Los runs son del agente, no del que mira: por eso no van por `/lab/runs`.

@router.get("/diagnostico/{hallazgo_id}")
def diagnostico_traza(hallazgo_id: int, run_id: str | None = None):
    from asistente import diagnostico
    return diagnostico.traza(hallazgo_id, run_id)


@router.post("/diagnostico/{hallazgo_id}/pedir")
def diagnostico_pedir(hallazgo_id: int):
    """Encola el diagnóstico de UN hallazgo ahora, por la misma puerta que el
    disparo automático (invariante 14 de AGENT.md §8). Sin topes: los topes son
    del daemon. Si ya hay uno en cola, devuelve ese."""
    from asistente import diagnostico
    return diagnostico.pedir(hallazgo_id)


# ── EL LABORATORIO — el asistente (asistente/, docs/AvAgentAI.md) ──────────
#
# Cada pregunta se encola como run durable; SSE muestra el ciclo sin hacer que
# la conexión HTTP sea dueña de la ejecución.

class Preguntar(BaseModel):
    pregunta: str = Field(..., min_length=1, max_length=2000)
    # La conversación a la que pertenece. Vacía = empieza una nueva. La memoria
    # y el foco viven en `ia.conversaciones`, no en el navegador.
    sesion: str = Field("", max_length=64)


@router.post("/lab/runs")
def lab_run_crear(body: Preguntar, request: Request,
                  email: str = Depends(get_user_email)):
    """Encola una pregunta durable. El resultado llega por SSE o por GET."""
    from asistente import ejecuciones
    from core.roles import get_user_role

    return ejecuciones.crear(
        body.pregunta, usuario=email, rol=get_user_role(email),
        portal="guest" if is_guest_portal(request) else "trading",
        sesion=body.sesion or None)


@router.get("/lab/runs/{run_id}")
def lab_run(run_id: str, email: str = Depends(get_user_email)):
    from asistente import ejecuciones

    run = ejecuciones.obtener(run_id, email)
    if not run:
        raise HTTPException(404, "esa ejecución no existe o no es tuya")
    return run


@router.get("/lab/runs/{run_id}/events")
def lab_run_eventos(run_id: str, request: Request,
                    email: str = Depends(get_user_email)):
    """SSE recuperable. Cierra a los 20 s; EventSource reconecta con Last-Event-ID."""
    from asistente import ejecuciones

    if not ejecuciones.obtener(run_id, email):
        raise HTTPException(404, "esa ejecución no existe o no es tuya")
    ultimo_header = request.headers.get("last-event-id", "0")
    try:
        ultimo = max(0, int(ultimo_header))
    except ValueError:
        ultimo = 0

    def stream():
        cursor = ultimo
        inicio = time.monotonic()
        ultimo_ping = inicio
        yield "retry: 1000\n\n"
        while time.monotonic() - inicio < 20:
            eventos = ejecuciones.eventos_desde(run_id, email, cursor)
            for evento in eventos:
                cursor = evento["id"]
                data = json.dumps(evento, ensure_ascii=False, default=str)
                yield f"id: {cursor}\ndata: {data}\n\n"
            run = ejecuciones.obtener(run_id, email)
            if run and run["estado"] in ejecuciones.TERMINALES:
                if not eventos:
                    data = json.dumps({"tipo": "snapshot", "run": run}, ensure_ascii=False,
                                      default=str)
                    yield f"data: {data}\n\n"
                return
            if time.monotonic() - ultimo_ping >= 5:
                yield ": ping\n\n"
                ultimo_ping = time.monotonic()
            time.sleep(0.5)

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@router.post("/lab/runs/{run_id}/cancelar")
def lab_run_cancelar(run_id: str, email: str = Depends(get_user_email)):
    from asistente import ejecuciones

    run = ejecuciones.cancelar(run_id, email)
    if not run:
        raise HTTPException(404, "esa ejecución no existe o no es tuya")
    return run


@router.get("/lab/metricas/runs")
def lab_run_metricas(dias: int = 30):
    from asistente import ejecuciones

    return ejecuciones.metricas(dias)


@router.post("/lab/preguntar")
def lab_preguntar(body: Preguntar, email: str = Depends(get_user_email)):
    """Una pregunta dentro de una conversación del usuario. Devuelve la
    respuesta, los eventos del grafo paso a paso, el foco y el costo de la
    conversación; la conversación queda guardada."""
    from asistente import sesiones

    return sesiones.preguntar(body.pregunta, usuario=email, sesion=body.sesion or None)


@router.get("/lab/sesiones")
def lab_sesiones(email: str = Depends(get_user_email)):
    """Las conversaciones del usuario, la más reciente primero."""
    from asistente import sesiones

    return sesiones.listar(email)


@router.get("/lab/sesiones/{sesion}")
def lab_sesion(sesion: str, email: str = Depends(get_user_email)):
    """Una conversación entera para reabrirla: turnos, foco y costo."""
    from asistente import sesiones

    c = sesiones.abrir(sesion, email)
    if c is None:
        raise HTTPException(404, "esa conversación no existe o no es tuya")
    return c


@router.post("/lab/sesiones/{sesion}/borrar")
def lab_sesion_borrar(sesion: str, email: str = Depends(get_user_email)):
    """Borra una conversación del usuario. POST y no DELETE: el proxy del
    front habla GET y POST, nada más."""
    from asistente import sesiones

    return sesiones.borrar(sesion, email)


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
    # La TAREA, no un rol: `asistente_cartera`, `agente_emisor`, … Una fila de la
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

    Sin `modelo` vuelve al default que declara `core/modelos.py` — «elegí mal y
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
