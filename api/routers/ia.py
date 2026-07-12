"""api/routers/ia.py — endpoints del módulo IA (QuantAI, docs/QUANTAI.md).

Solo HTTP plumbing (la lógica vive en api/services/). El gate es
estructural: se monta en api/main.py con `_IA` (bearer + require_module("ia")),
y el prefijo /api/ia ya mapea al módulo en ENDPOINT_MODULE_PREFIXES.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email, require_admin
from api.services import briefing, copiloto, ia_obs

router = APIRouter(prefix="/api/ia", tags=["ia"])


@router.get("/observabilidad")
def observabilidad(dias: int = 14, limit: int = 30):
    """Trazas del gateway de IA para OBSERVABILIDAD → IA: resumen de hoy
    (+% presupuesto), serie por día, agregado por tarea y últimas llamadas."""
    return ia_obs.observabilidad(dias=dias, limit=limit)


class PresupuestosBody(BaseModel):
    global_dia: int | None = None   # tokens/día de TODO el sistema (techo duro)
    usuario_dia: int | None = None  # tokens/día por usuario (≤ global)


@router.get("/presupuesto")
def presupuesto_get():
    """Límites de tokens vigentes del gateway (tabla > env > default)."""
    return ia_obs.get_presupuestos()


@router.get("/saldo")
def saldo_proveedor():
    """Saldo REAL de la cuenta DeepSeek (/user/balance del proveedor)."""
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


@router.get("/copiloto/vistas")
def copiloto_vistas(email: str = Depends(get_user_email)):
    """Vistas del copiloto habilitadas para este usuario (gate por módulo
    RBAC de cada vista). El frontend lo usa como probe: 403 del montaje =
    sin módulo `ia` = ocultar el botón."""
    return {"vistas": copiloto.vistas_para(email=email)}


@router.get("/copiloto/historial")
def copiloto_historial(limit: int = 8, email: str = Depends(get_user_email)):
    """Última CONVERSACIÓN del usuario con el copiloto (memoria persistente
    desde ia.trazas — cada chat es su propio mundo)."""
    return copiloto.historial_persistido(usuario=email, limit=limit)


@router.post("/copiloto")
def copiloto_preguntar(body: PreguntaCopiloto, email: str = Depends(get_user_email)):
    """Una pregunta sobre la tabla de una vista de mercado. La IA solo ve los
    datos de ESA vista (armados server-side); degrada con ok=False."""
    if body.vista in copiloto.VISTAS and not copiloto.puede_usar(email, body.vista):
        raise HTTPException(status_code=403, detail="módulo de la vista no autorizado")
    return copiloto.preguntar(
        vista=body.vista, pregunta=body.pregunta,
        historial=body.historial, usuario=email, conv_id=body.conv_id,
        params=body.params,
    )


@router.post("/copiloto/feedback")
def copiloto_feedback(body: FeedbackCopiloto, email: str = Depends(get_user_email)):
    """👍/👎 del usuario sobre una respuesta del copiloto → ia.trazas.feedback."""
    return copiloto.registrar_feedback(
        traza_id=body.traza_id, valor=body.feedback, usuario=email,
    )
