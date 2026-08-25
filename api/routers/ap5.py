"""Router AP5 — /api/ap5 (vista POSICIONES Y DIFERENCIAS).

La posición de futuros que informa **la CÁMARA** (A3 Mercados / ACyRSA), no
nuestro registro de boletos. Router aparte de `operaciones` justamente por eso:
son dos fuentes distintas para los mismos pesos, y mezclarlas haría que en un
incidente nadie sepa cuál de los dos números manda.

Se monta en `api/main.py` con el gate del módulo `operaciones` (trader + admin),
que es el mismo que ya protege la vista NEGOCIO donde vive la tab.

⚠️ **La ESCRITURA no suma un gate propio**: el `grupo` de una cuenta y el
arrastre del acumulado los carga la MESA, que es exactamente quien tiene el
módulo `operaciones`. Si mañana hay que angostarlo (allowlist per-usuario, como
Mesa de Dinero), se cambia `_ESCRIBE` y lo heredan los cinco endpoints — el
punto de control queda en UN solo lugar y no repartido por handler.

Thin HTTP plumbing: TODA la lógica y toda fórmula derivada viven en
`api/services/ap5_posiciones.py`. El front no recalcula nada. Doc:
`docs/POSTRADE.md`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import ap5_posiciones as _svc

router = APIRouter(prefix="/api/ap5", tags=["AP5"])

# El único punto de control de la escritura (ver el docstring del módulo).
_ESCRIBE = Depends(get_user_email)


def _ok(fn, *args, **kwargs):
    """ValueError del service → 400; PermissionError → 403.

    El service valida y explica porque es él quien conoce las reglas; el router
    solo traduce a HTTP.
    """
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Lectura ──────────────────────────────────────────────────────────────────

@router.get("/vista")
def vista(fecha: str | None = Query(
    None, description="'YYYY-MM-DD'; default = el último día CON POSICIÓN")) -> dict:
    """TODA la pantalla en UN request.

    Un solo endpoint y no cinco porque los bloques tienen que hablar de la MISMA
    fecha: con llamadas separadas, un job corriendo en el medio dejaría el
    resumen en un día y el ranking en otro **sin que nada falle**.
    """
    return _ok(_svc.vista, fecha)


@router.get("/fechas")
def fechas() -> list[dict]:
    """Los días con posición, del más reciente al más viejo.

    El universo propio de esta vista: no se ancla al calendario de operaciones
    porque la cámara tiene el suyo y puede ir rezagada.
    """
    return _ok(_svc.fechas)


@router.get("/cuentas")
def cuentas() -> list[dict]:
    """El padrón para el ABM: número, los DOS nombres (el humano y el de la
    cámara), el CUIT y el grupo."""
    return _ok(_svc.cuentas)


# ── Escritura — las DOS cosas que ninguna fuente sabe ────────────────────────

class CuentaIn(BaseModel):
    account: str = Field(..., min_length=1, description="número de cuenta de la cámara")
    # `None` = no tocar ese campo; `""` = BORRAR el override y volver al de la
    # cámara. Son cosas distintas y por eso el default es None y no "".
    name: str | None = Field(None, description="nombre propio; vacío borra el override")
    grupo: str | None = Field(None, description="Cooperativas / MUNDO ACA; vacío lo saca")


@router.post("/cuentas")
def guardar_cuenta(body: CuentaIn, actor: str = _ESCRIBE) -> dict:
    """Nombre propio y GRUPO de una cuenta.

    El `grupo` es lo que parte el reporte en sus dos rankings y **la cámara no lo
    sabe**: no se deduce del nombre (REGLA #9 — el día que una cuenta se llame
    distinto cambiaría de ranking sin que nadie se entere).
    """
    return _ok(_svc.guardar_cuenta, body.account,
               name=body.name, grupo=body.grupo, por=actor)


class AcumuladoIn(BaseModel):
    account: str = Field(..., min_length=1)
    # Las dos monedas SIEMPRE, en columnas separadas. No hay un campo `moneda`
    # con un importe: eso permitiría cargar una y dejar la otra sin saber si
    # está en cero o sin cargar. Y no hay un total: no existe: el agro liquida
    # en Dólar MtR y el dólar futuro en Pesos, y sumarlos no significa nada.
    acumulado_pesos: float = 0
    acumulado_mtr: float = 0
    # EXCLUSIVA: los importes ya contienen todo hasta ese día inclusive.
    fecha: str = Field(..., description="'YYYY-MM-DD'; hasta acá llegan los dos importes")


@router.post("/acumulado")
def guardar_acumulado(body: AcumuladoIn, actor: str = _ESCRIBE) -> dict:
    """El ARRASTRE de una cuenta, en sus dos monedas.

    La cámara manda la diferencia DEL DÍA, no el arrastre: lo anterior a nuestra
    serie solo existe en la planilla de la mesa y se carga acá una vez. De ahí en
    adelante el acumulado se mueve solo — **no se persiste, se deriva** en la
    lectura (misma decisión que el histórico de `/aca`).
    """
    return _ok(_svc.guardar_acumulado, body.account, body.acumulado_pesos,
               body.acumulado_mtr, body.fecha, por=actor)
