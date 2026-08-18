"""api/routers/interbanking.py — tab INTERBANKING del BACK OFFICE.

Plumbing HTTP puro: la lógica vive en `api/services/bancos.py`.

⚠️ **Hacia INTERBANKING no se escribe NUNCA** — `core/interbanking.py` implementa
únicamente GET y hay un test que lo congela. Esa es la línea que importa: la
integración no puede mover plata ni por error.

Lo que SÍ escribe (desde 2026-08-18) es la CLASIFICACIÓN DE GASTOS BANCARIOS, y
va a tablas NUESTRAS (`bancos.gastos_reglas` / `gastos_overrides`): no toca el
extracto, no toca el saldo y no sale a internet. Son 3 endpoints, todos detrás de
`bancos.puede_escribir` (allowlist de Tesorería + admin) y todos auditados. Un
test enumera exactamente cuáles son, así que uno nuevo no entra sin que alguien
lo decida.

Gate: se monta en `api/main.py` con `_BACK_OFFICE`, o sea `require_module(
"back-office")`. **JAMÁS al portal invitado** (REGLA #8) — son los saldos
bancarios de la casa; hay un test que lo congela.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from api.auth import get_user_email
from api.services import bancos as _svc

router = APIRouter(prefix="/api/back-office/interbanking", tags=["Interbanking"])


def _fecha(fecha: date | None) -> date:
    """La fecha que mira la vista. **Una sola, no un rango.**

    El back office la cambió el 2026-08-18: «la fecha es una sola, es siempre el
    mismo día». Antes eran `desde`/`hasta` y el consolidado terminaba mostrando
    la apertura de un día contra el cierre de otro — una variación de nada.

    Sin `fecha`, el default lo decide el SERVICE (`bancos.fecha_default`), no el
    navegador: así la pantalla no depende del reloj ni de la zona horaria del
    cliente.
    """
    if fecha and fecha > _svc.fecha_default():
        raise HTTPException(400, "No se puede pedir una fecha futura.")
    return fecha or _svc.fecha_default()


@router.get("/vista")
def vista(
    cuenta_id: int | None = Query(None, description="cuenta de bancos.cuentas"),
    fecha: date | None = Query(None, description="día a mostrar (default: hoy)"),
    email: str = Depends(get_user_email),
) -> dict:
    """Todo lo que muestra la tab, en UN request: cuentas, extracto del día,
    movimientos, resumen de conciliación y cuándo fue la última sincronización."""
    return _svc.vista(email, cuenta_id, _fecha(fecha))


@router.get("/consolidado")
def consolidado(
    fecha: date | None = Query(None, description="día a mostrar (default: hoy)"),
    email: str = Depends(get_user_email),
) -> dict:
    """CONSOLIDADO BANCOS: una fila por cuenta, agrupada por banco, con la
    apertura y el cierre de ESE día."""
    return _svc.consolidado(email, _fecha(fecha))


@router.get("/cuentas")
def cuentas() -> list[dict]:
    """Solo el selector de cuentas (sin CBU ni número completo)."""
    return _svc.listar_cuentas()


# --------------------------------------------------------------------------- #
# Gastos bancarios — la ÚNICA escritura de esta vista
# --------------------------------------------------------------------------- #
def _exigir_escritura(email: str) -> str:
    if not _svc.puede_escribir(email):
        raise HTTPException(403, "No tenés permiso para editar los gastos bancarios.")
    return email


@router.get("/gastos/reglas")
def reglas() -> list[dict]:
    """El catálogo de reglas que clasifican un movimiento como gasto bancario."""
    return _svc.listar_reglas()


@router.post("/gastos/reglas")
def crear_regla(
    campo: str = Body(..., embed=True),
    operador: str = Body(..., embed=True),
    valor: str = Body(..., embed=True),
    nota: str = Body("", embed=True),
    email: str = Depends(get_user_email),
) -> dict:
    """Alta de regla. Se valida SERVER-SIDE contra las constantes del service."""
    try:
        return _svc.crear_regla(_exigir_escritura(email), campo, operador, valor, nota)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/gastos/reglas/{regla_id}")
def borrar_regla(regla_id: int, email: str = Depends(get_user_email)) -> dict:
    if not _svc.borrar_regla(_exigir_escritura(email), regla_id):
        raise HTTPException(404, "Esa regla no existe.")
    return {"ok": True}


@router.put("/gastos/movimiento")
def marcar_gasto(
    mov_hash: str = Body(..., embed=True),
    # `None` BORRA la marca y devuelve el movimiento al criterio de las reglas.
    es_gasto: bool | None = Body(None, embed=True),
    email: str = Depends(get_user_email),
) -> dict:
    """Marca o desmarca UN movimiento. La marca manual GANA sobre la regla."""
    try:
        return _svc.marcar_gasto(_exigir_escritura(email), mov_hash, es_gasto)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
