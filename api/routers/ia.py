"""api/routers/ia.py — endpoints del módulo IA (observabilidad + briefing).

Solo HTTP plumbing (la lógica vive en api/services/). El gate es
estructural: se monta en api/main.py con `_IA` (bearer + require_module("ia")),
y el prefijo /api/ia ya mapea al módulo en ENDPOINT_MODULE_PREFIXES.

⚠️ **El AV AGENT ya NO vive acá.** Sus 44 endpoints se fueron a
`api/routers/agente.py` con el rediseño 2.0 (`docs/AGENT_2.0.md`):
estaban colgados de este router por herencia, no por pertenencia.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_user_email, require_admin
from api.services import briefing, ia_obs

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
# matriz de roles dándole `ia` a la mesa, gatearlo solo por módulo se lo
# mostraría a un comercial.
