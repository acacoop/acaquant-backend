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
