"""api/routers/ia.py — endpoints del módulo IA (QuantAI, docs/QUANTAI.md).

Solo HTTP plumbing (la lógica vive en api/services/). El gate es
estructural: se monta en api/main.py con `_IA` (bearer + require_module("ia")),
y el prefijo /api/ia ya mapea al módulo en ENDPOINT_MODULE_PREFIXES.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import get_user_email, is_guest_portal, require_admin
from api.services import briefing, copiloto, ia_obs

router = APIRouter(prefix="/api/ia", tags=["ia"])


@router.get("/observabilidad", dependencies=[Depends(require_admin)])
def observabilidad(dias: int = 14, limit: int = 60, offset: int = 0,
                   tarea: str | None = None, usuario: str | None = None,
                   solo_error: bool = False, q: str | None = None):
    """Trazas del gateway de IA para OBSERVABILIDAD → IA: resumen de hoy
    (+% presupuesto), serie por día, agregados por tarea y por PROVEEDOR, y
    el historial de llamadas paginado y filtrable (tarea/usuario/errores/texto).

    ADMIN-ONLY (require_admin, no delegable desde la matriz). Devuelve
    `detalle`/`respuesta`/`razonamiento` de ia.trazas — o sea la PREGUNTA y la
    RESPUESTA literal de las conversaciones de TODOS los usuarios, más su email,
    y acepta filtros `usuario`/`q` para buscar dentro de ellas. Con el gate del
    módulo `ia` solamente quedaba alcanzable por cualquier rol con `ia` tildado
    y —peor— por el portal INVITADO (`ia` ∈ INVITADO_MODULES, core/roles.py),
    que habría leído conversaciones del NEGOCIO de la mesa (REGLA #8).
    require_admin además rechaza al guest de forma dura."""
    return ia_obs.observabilidad(dias=dias, limit=limit, offset=offset, tarea=tarea,
                                 usuario=usuario, solo_error=solo_error, q=q)


class PresupuestosBody(BaseModel):
    global_dia: int | None = None   # tokens/día de TODO el sistema (techo duro)
    usuario_dia: int | None = None  # tokens/día por usuario (≤ global)


@router.get("/presupuesto", dependencies=[Depends(require_admin)])
def presupuesto_get():
    """Límites de tokens vigentes del gateway (tabla > env > default).

    ADMIN-ONLY: es el par de lectura de `POST /presupuesto` (ya admin-only).
    Config interna de costos — no la ve un invitado ni un rol con `ia`."""
    return ia_obs.get_presupuestos()


@router.get("/saldo", dependencies=[Depends(require_admin)])
def saldo_proveedor():
    """Estado de los proveedores LLM configurados: modelos por tier, si se
    comprometen a no entrenar, y saldo real del que lo expone.

    ADMIN-ONLY: expone el saldo real de la cuenta del proveedor (dato
    financiero de la empresa) — nunca para el portal invitado."""
    return ia_obs.saldo()


class PresupuestoUsuarioBody(BaseModel):
    email: str
    valor: int | None = None  # None = borrar la excepción (vuelve al tope general)


@router.post("/presupuesto/usuario", dependencies=[Depends(require_admin)])
def presupuesto_usuario_set(body: PresupuestoUsuarioBody, email: str = Depends(get_user_email)):
    """Excepción PERSONAL de tope diario para un usuario (SOLO admin) — pisa
    el tope general solo para ese email. valor null la borra."""
    try:
        return ia_obs.set_presupuesto_usuario(email=body.email, valor=body.valor, actor=email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/presupuesto", dependencies=[Depends(require_admin)])
def presupuesto_set(body: PresupuestosBody, email: str = Depends(get_user_email)):
    """Edita los topes diarios (SOLO admin). El global es techo duro del día;
    el tope por usuario no puede superarlo. Queda auditado quién y cuándo."""
    try:
        return ia_obs.set_presupuestos(
            global_dia=body.global_dia, usuario_dia=body.usuario_dia, actor=email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/briefing")
def briefing_apertura():
    """Briefing de apertura (modal de HOME, 10:00 ART). v1 determinista —
    futuros US, oficial (MAE live + A3500) y cierres MEP/CCL con variaciones."""
    return briefing.briefing_hoy()


# ── P3 Copiloto de Mesa (contextual por vista, ver api/services/copiloto.py) ──


class PreguntaCopiloto(BaseModel):
    vista: str
    pregunta: str
    # pares {pregunta, respuesta} previos de ESTE panel (ventana corta)
    historial: list[dict] = Field(default_factory=list)
    # conversación a la que pertenece la pregunta (cada chat su mundo)
    conv_id: str | None = None
    # parámetros de la vista (ej. trading: tickers de las 8 tarjetas, foco,
    # overrides de máx/mín/cierre) — se SANEAN en el service, jamás son datos
    params: dict | None = None


class FeedbackCopiloto(BaseModel):
    traza_id: int
    feedback: int  # 1 = 👍, -1 = 👎


def _identidad(request: Request, email: str) -> str:
    """Portal invitado (2026-07-21, decisión user: IA para invitados): cada
    invitado conserva SU identidad, marcada con el prefijo "guest:" — así
    persiste su propia conversación, tiene su tope diario propio (100k default
    en core/ai) y el acceso a vistas se resuelve contra INVITADO_MODULES (la
    guía queda excluida), nunca por rol-del-email."""
    from core.roles import GUEST_PREFIX

    return f"{GUEST_PREFIX}{email}" if is_guest_portal(request) else email


@router.get("/copiloto/vistas")
def copiloto_vistas(request: Request, email: str = Depends(get_user_email)):
    """Vistas del copiloto habilitadas para este usuario (gate por módulo
    RBAC de cada vista). El frontend lo usa como probe: 403 del montaje =
    sin módulo `ia` = ocultar el botón."""
    return {"vistas": copiloto.vistas_para(email=_identidad(request, email))}


@router.get("/copiloto/historial")
def copiloto_historial(request: Request, limit: int = 8, email: str = Depends(get_user_email)):
    """Última CONVERSACIÓN del usuario con el copiloto (memoria persistente
    desde ia.trazas — cada chat es su propio mundo). Los invitados también
    persisten LA SUYA (identidad guest:<email> — pedido del user 2026-07-21:
    cada correo conserva lo que usó)."""
    return copiloto.historial_persistido(usuario=_identidad(request, email), limit=limit)


@router.post("/copiloto")
def copiloto_preguntar(request: Request, body: PreguntaCopiloto,
                       email: str = Depends(get_user_email)):
    """Una pregunta sobre la tabla de una vista de mercado. La IA solo ve los
    datos de ESA vista (armados server-side); degrada con ok=False."""
    quien = _identidad(request, email)
    if body.vista in copiloto.VISTAS and not copiloto.puede_usar(quien, body.vista):
        raise HTTPException(status_code=403, detail="módulo de la vista no autorizado")
    return copiloto.preguntar(
        vista=body.vista, pregunta=body.pregunta,
        historial=body.historial, usuario=quien, conv_id=body.conv_id,
        params=body.params,
    )


class VigiaBody(BaseModel):
    params: dict | None = None  # tickers de las tarjetas + overrides (se sanean)


@router.post("/copiloto/vigia")
def copiloto_vigia(body: VigiaBody, email: str = Depends(get_user_email)):
    """El vigía de la vista TRADING: evalúa los disparadores (tarjeta en
    nivel / candidato del radar) por CÓDIGO — cero tokens. El front lo pollea."""
    if not copiloto.puede_usar(email, "trading"):
        raise HTTPException(status_code=403, detail="módulo trading no autorizado")
    return copiloto.vigia(params=body.params)


@router.post("/copiloto/feedback")
def copiloto_feedback(request: Request, body: FeedbackCopiloto,
                      email: str = Depends(get_user_email)):
    """👍/👎 del usuario sobre una respuesta del copiloto → ia.trazas.feedback."""
    return copiloto.registrar_feedback(
        traza_id=body.traza_id, valor=body.feedback,
        usuario=_identidad(request, email),
    )


# ── AV AGENT (docs/AV_AGENT.md) ──────────────────────────────────────────────
#
# Vive bajo /api/ia a propósito: hereda el gate `ia` de forma ESTRUCTURAL en vez
# de estrenar un prefijo que habría que acordarse de sumar a
# ENDPOINT_MODULE_PREFIXES. Un endpoint de IA que nace fuera de ese prefijo nace
# sin gate, y eso no se ve hasta que alguien lo prueba sin permisos.
#
# **TODO admin-only** (decisión del user 2026-08-16, apretado desde el módulo
# `ia`): el agente expone el estado interno de la valuación — qué bonos están mal
# cargados, cuáles no entran al AuM, qué le falta al catálogo. Eso no es
# información de mercado: es cómo está hecho el sistema por dentro, y con la
# matriz de roles dándole `ia` a la mesa para los copilotos, gatearlo solo por
# módulo se lo mostraría a un comercial. Mismo criterio que SALUD.


class RespuestaAvAgent(BaseModel):
    id: int = Field(..., ge=1)
    respuesta: str = Field(..., min_length=1, max_length=32)
    nota: str = Field("", max_length=500)


@router.get("/av-agent/vista", dependencies=[Depends(require_admin)])
def av_agent_vista():
    """Toda la pantalla en UN request: hallazgos de la última corrida, preguntas
    abiertas, lo ya decidido y los ignorados."""
    from api.services import av_agent_vista as vista_svc
    return vista_svc.vista()


@router.post("/av-agent/responder", dependencies=[Depends(require_admin)])
def av_agent_responder(body: RespuestaAvAgent, email: str = Depends(get_user_email)):
    """Contesta una pregunta del agente y **aplica su efecto**."""
    from api.services import av_agent_preguntas as preg
    try:
        r = preg.responder(body.id, body.respuesta, por=email or "", nota=body.nota)
    except preg.RespuestaInvalida as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "id": body.id, "respuesta": r.get("respuesta"),
            "aplicada": r.get("aplicada", False)}


class IgnorarAvAgent(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=40)
    motivo: str = ""


@router.post("/av-agent/ignorar", dependencies=[Depends(require_admin)])
def av_agent_ignorar(body: IgnorarAvAgent, email: str = Depends(get_user_email)):
    """«No me interesa»: el ticker deja de aparecer en TODOS los tipos de hallazgo.

    Antes ignorar solo se podía hacer contestando una pregunta del agente, y solo
    valía para los faltantes: en el resto de la lista no había forma de decir que
    algo no interesa. Surte efecto **en la lectura siguiente**, no en la próxima
    corrida — apretar el botón y que la fila siga ahí es lo que hace desconfiar de
    toda la lista.

    Reversible desde la tab DECIDIDO (`designorar`)."""
    from api.services import av_agent_preguntas as preg
    try:
        return preg.ignorar(body.ticker, motivo=body.motivo, por=email or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class DesignorarAvAgent(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=32)


@router.post("/av-agent/designorar", dependencies=[Depends(require_admin)])
def av_agent_designorar(body: DesignorarAvAgent, email: str = Depends(get_user_email)):
    """Deshace un «no me interesa»: el ticker vuelve a proponerse.

    Existe porque **la reversibilidad es lo que hace barata la decisión**. Sin
    este botón, marcar `ignorar` es irreversible desde la app y la única salida es
    tocar SQL a mano — con lo cual la respuesta segura pasa a ser no contestar
    nada, y el mecanismo entero deja de usarse.

    ⚠️ Es POST y no DELETE **a propósito**: el proxy de Next para /api/ia es un
    catch-all que hoy expone solo GET y POST. Agregarle DELETE habilitaría el
    verbo para TODOS los endpoints de /api/ia —presentes y futuros— a cambio de
    la elegancia REST de uno solo. La superficie mínima gana."""
    from api.services import av_agent_preguntas as preg
    try:
        return preg.designorar(body.ticker, por=email or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class ResolverAviso(BaseModel):
    id: int = Field(..., ge=1)
    deshacer: bool = False


@router.post("/av-agent/aviso", dependencies=[Depends(require_admin)])
def av_agent_aviso(body: ResolverAviso, email: str = Depends(get_user_email)):
    """Marca un aviso como HECHO, o lo reabre (`deshacer`).

    Los avisos son la lista de trabajo manual que dejó un alta: el agente hace el
    95% y anota el 5% que ninguna fuente publica. **Los cierra una persona** —
    derivarlos del estado del master los haría desaparecer sin dejar ver qué
    había pendiente ni qué se hizo.

    Reversible por la misma razón que `designorar`: sin poder deshacer, marcar
    algo se vuelve una decisión cara y el mecanismo se deja de usar."""
    from api.services import av_agent_vista as vista
    r = vista.resolver_aviso(body.id, por=email or "", deshacer=body.deshacer)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "no se pudo"))
    return r


class CompletarAviso(BaseModel):
    id: int = Field(..., ge=1)
    valor: str = Field(..., min_length=1, max_length=200)


@router.post("/av-agent/aviso/completar", dependencies=[Depends(require_admin)])
def av_agent_aviso_completar(body: CompletarAviso,
                             email: str = Depends(get_user_email)):
    """Carga el dato que faltaba **desde la misma lista de avisos** y cierra el
    aviso en el mismo acto (pedido del user, 2026-08-17).

    Antes el aviso decía «falta el CER de emisión» y te mandaba a Manager →
    Títulos a buscar el bono: el agente hacía el 95% y te dejaba el 5% en otra
    pantalla. Ahora el valor se escribe donde se lee el aviso.

    Escribe SOLO los campos del catálogo `_CAMPO_AVISO` y **verifica** contra el
    master antes de cerrar: si el dato no quedó cargado, el aviso sigue abierto.
    """
    from api.services import av_agent_vista as vista
    r = vista.completar_aviso(body.id, body.valor, por=email or "")
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error", "no se pudo"))
    return r


class DiagnosticoSalud(BaseModel):
    chequeo_id: str = Field(..., min_length=2, max_length=120)
    # La lente con IA es la ÚNICA que gasta tokens y va última. Poder apagarla deja
    # el análisis determinista disponible siempre — con presupuesto agotado, sin
    # key, o simplemente cuando no hace falta.
    con_ia: bool = True


class VotoEval(BaseModel):
    """El voto humano sobre un diagnóstico. **Es el insumo del eval set.**"""
    caso: str = Field(..., min_length=2, max_length=200)
    dominio: str = Field("bono", pattern="^(bono|salud)$")
    causa: str = Field(..., min_length=2, max_length=60)
    acierta: bool
    nota: str = Field("", max_length=1000)
    causa_correcta: str = Field("", max_length=60)


class FlujosAvAgent(BaseModel):
    ticker: str = Field(..., min_length=2, max_length=32)
    # El CER de emisión tipeado en la cadena: sin ese número la rama `cer` del
    # motor no devuelve TEA ni paridad, y el cotejo contra 1816 no se puede hacer.
    cer_emision: float | None = Field(None, gt=0)


@router.post("/av-agent/simular-flujos", dependencies=[Depends(require_admin)])
def av_agent_simular_flujos(body: FlujosAvAgent):
    """E3 — el hallazgo `flujos_vacios` se vuelve accionable. **No escribe nada.**

    Baja el cronograma de 1816 para un bono que YA está en `mercado.curvas`, lo
    convierte con la rama que ese bono ya tiene, simula la TEA y la coteja contra
    1816 (paridad + duration). Es la pregunta del user: *«lo que hay que chequear
    es si con el flujo que agregaríamos y nuestro modelo nos da una TEA y esos
    datos como a 1816»*."""
    from api.services import av_agent_alta
    return av_agent_alta.simular_flujos(body.ticker, cer_emision=body.cer_emision)


@router.post("/av-agent/aplicar-flujos", dependencies=[Depends(require_admin)])
def av_agent_aplicar_flujos(body: FlujosAvAgent, email: str = Depends(get_user_email)):
    """E3 — escribe el cronograma, **y nada más**.

    Es un UPDATE puntual sobre el blob: los ejes, el emisor y el símbolo ni
    siquiera están en el payload, así que no se pueden pisar por accidente.

    La ÚNICA excepción es `cer_emision`, y solo si el user lo tipeó en la cadena
    **y** el bono no lo tenía: sin ese número la rama `cer` del motor no devuelve
    tasa y el bono quedaría con cronograma y sin valuar."""
    from api.services import av_agent_alta
    return av_agent_alta.aplicar_flujos(body.ticker, actor=email or "",
                                        cer_emision=body.cer_emision)


@router.post("/av-agent/simular-arreglo", dependencies=[Depends(require_admin)])
def av_agent_simular_arreglo(body: FlujosAvAgent):
    """E3.j — qué INSUMO está mal en un bono con tasa sospechosa. **No escribe.**

    Corre el motor dos veces (el bono como está HOY y con la propuesta de 1816) y
    coteja las dos contra ellos. El ANTES es la mitad del diagnóstico: sin esa
    columna no hay forma de saber si el arreglo mejora algo."""
    from api.services import av_agent_alta
    return av_agent_alta.simular_arreglo(body.ticker, cer_emision=body.cer_emision)


@router.post("/av-agent/aplicar-arreglo", dependencies=[Depends(require_admin)])
def av_agent_aplicar_arreglo(body: FlujosAvAgent, email: str = Depends(get_user_email)):
    """E3.j — **la única puerta que PISA un dato existente.**

    Por eso su cadena exige las DOS mitades: que la propuesta coincida con 1816 y
    que lo de hoy NO. Sin la segunda se pisaría un bono que ya estaba bien, que es
    estrictamente peor que no hacer nada."""
    from api.services import av_agent_alta
    return av_agent_alta.aplicar_arreglo(body.ticker, actor=email or "",
                                         cer_emision=body.cer_emision)


class SimularAvAgent(BaseModel):
    ticker: str = Field(..., min_length=2, max_length=32)
    curva_1816: str = Field(..., min_length=2, max_length=80)
    # El dato que ninguna fuente publica, tipeado en la misma pantalla. Opcional:
    # sin él la simulación es la de siempre.
    cer_emision: float | None = Field(None, gt=0)


@router.post("/av-agent/salud", dependencies=[Depends(require_admin)])
def av_agent_salud(body: DiagnosticoSalud):
    """**SALUD, razonada por el agente** — ocho lentes con la misma forma que las
    de un bono, para que el modal las dibuje igual.

    Siete son deterministas y gratis; la octava lee el log con IA y es la única
    que gasta tokens (`con_ia=false` la apaga). **No escribe nada**: SALUD sigue
    siendo el dueño de su estado."""
    from api.services import av_agent_salud as svc
    return svc.diagnosticar(body.chequeo_id, con_ia=body.con_ia)


@router.post("/av-agent/eval", dependencies=[Depends(require_admin)])
def av_agent_eval(body: VotoEval, email: str = Depends(get_user_email)):
    """**El voto humano sobre un diagnóstico**: ¿la causa que dijo el agente es la
    correcta?

    Es el insumo del EVAL SET, y sin él no hay forma de saber si el agente acierta
    — o sea que no hay forma de darle más autonomía sin fe. Un ✔/✖ por
    diagnóstico, con el motivo cuando falla."""
    from api.services import av_agent_evals
    return av_agent_evals.votar(
        caso=body.caso, dominio=body.dominio, causa=body.causa,
        acierta=body.acierta, nota=body.nota,
        causa_correcta=body.causa_correcta, por=email or "")


@router.get("/av-agent/eval", dependencies=[Depends(require_admin)])
def av_agent_eval_resumen():
    """La PRECISIÓN medida por causa: cuántos votos, cuántos aciertos, y dónde se
    equivoca. Es el tablero que decide qué arreglo se puede automatizar."""
    from api.services import av_agent_evals
    return av_agent_evals.resumen()


@router.post("/av-agent/simular", dependencies=[Depends(require_admin)])
def av_agent_simular(body: SimularAvAgent):
    """E2 — calcula la TEA que TENDRÍA el bono, **sin escribir nada**.

    `cer_emision` permite RE-simular con el número tipeado a mano: nuestra serie
    CER no llega a la fecha de emisión de un bono nuevo, así que sin él la rama
    CER no devuelve tasa. Con él, el user ve la TEA antes de aplicar."""
    from api.services import av_agent_alta
    return av_agent_alta.simular(body.ticker, curva_1816=body.curva_1816,
                                 cer_emision=body.cer_emision)


@router.post("/av-agent/aplicar-alta", dependencies=[Depends(require_admin)])
def av_agent_aplicar_alta(body: SimularAvAgent, email: str = Depends(get_user_email)):
    """E2 — simula y, si la rama lo permite, da de alta el bono en
    `mercado.curvas` por la MISMA puerta que usa la mesa (`upsert_bono`).

    Si viene `cer_emision`, el bono nace CON el dato: no se crea pelado para
    después completarlo, y por lo tanto tampoco genera el aviso."""
    from api.services import av_agent_alta
    return av_agent_alta.aplicar(body.ticker, curva_1816=body.curva_1816,
                                 actor=email or "", cer_emision=body.cer_emision)
