"""Router AP5 — /api/ap5 (vista POSICIONES Y DIFERENCIAS).

La posición de futuros que informa **la CÁMARA** (A3 Mercados / ACyRSA), no
nuestro registro de boletos. Router aparte de `operaciones` justamente por eso:
son dos fuentes distintas para los mismos pesos, y mezclarlas haría que en un
incidente nadie sepa cuál de los dos números manda.

Se monta en `api/main.py` con el gate del módulo `operaciones` (trader + admin),
que es el mismo que ya protege la vista NEGOCIO donde vive la tab.

⚠️ **La ESCRITURA no suma un gate propio**: el `grupo` de una cuenta —lo único
que se carga a mano acá— lo pone la MESA, que es exactamente quien tiene el
módulo `operaciones`. Si mañana hay que angostarlo (allowlist per-usuario, como
Mesa de Dinero), se cambia `_ESCRIBE` y lo heredan todos los endpoints — el
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
from api.services import mesa_dinero as _mesa

router = APIRouter(prefix="/api/ap5", tags=["AP5"])

# El único punto de control de la escritura (ver el docstring del módulo).
_ESCRIBE = Depends(get_user_email)


def require_escritura_mesa(actor: str = Depends(get_user_email)) -> str:
    """Gate del ACTIVO INTEGRADO manual: escritura de **Mesa de Dinero**.

    ⚠️ **Es un gate MÁS ANGOSTO que el del resto del router**, y a propósito.
    Todo `/api/ap5` lo cubre el módulo `operaciones` (trader + admin), pero este
    número es plata que va al reporte de la mesa y se tipea a mano: escribirlo
    lo puede hacer sólo quien ya tiene escritura en Mesa de Dinero
    (`operaciones.mesa_dinero_escritores` + admin), que es la allowlist
    per-PERSONA que la mesa administra en Manager → MESA. Pedido del user
    (2026-08-27).

    ⚠️ **Reusa `_mesa.puede_escribir`, no una copia de la lista.** Dos
    allowlists para el mismo permiso se separan sin fallar: se saca a alguien de
    Manager → MESA y acá sigue pudiendo escribir, sin que nada lo grite.

    Va como dependency y no como chequeo adentro del handler para que
    `scripts/audit_rbac.py` lo vea al recorrer el árbol de deps.
    """
    if not _mesa.puede_escribir(actor):
        raise HTTPException(403, "sin permiso de escritura en Mesa de Dinero")
    return actor


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
        None, description="'YYYY-MM-DD'; default = el último día CON POSICIÓN"),
          actor: str = Depends(get_user_email)) -> dict:
    """TODA la pantalla en UN request.

    Un solo endpoint y no cinco porque los bloques tienen que hablar de la MISMA
    fecha: con llamadas separadas, un job corriendo en el medio dejaría el
    resumen en un día y el ranking en otro **sin que nada falle**.

    ⚠️ `puede_editar_activo_integrado` lo resuelve el ROUTER y no el service:
    depende de QUIÉN pide, y `api/services/` es puro. Es sólo para que la
    pantalla no ofrezca un lápiz que va a devolver 403 — **el permiso real lo
    aplica `require_escritura_mesa` en el POST**, que es el único enforcement.
    """
    v = _ok(_svc.vista, fecha)
    return {**v, "puede_editar_activo_integrado": _mesa.puede_escribir(actor)}


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


class ActivoIntegradoBody(BaseModel):
    moneda: str = Field(..., min_length=1, description="La moneda de la card")
    # `None` BORRA la carga manual y vuelve al calculado. Es la única forma de
    # deshacer sin dejar un cero, que se ve igual que «no hay dato».
    importe: float | None = Field(None, description="null = borrar y volver al calculado")
    nota: str | None = Field(None, max_length=500, description="Por qué se corrigió")
    fecha: str | None = Field(None, description="YYYY-MM-DD; por defecto el último día")


@router.post("/activo-integrado", dependencies=[Depends(require_escritura_mesa)])
def guardar_activo_integrado(body: ActivoIntegradoBody,
                             actor: str = _ESCRIBE) -> dict:
    """Carga a mano el ACTIVO INTEGRADO de una moneda, o borra la carga.

    Es TEMPORAL: el número que sale de la cámara trae errores y por un tiempo lo
    escribe la mesa. **No borra el calculado** — la card sigue mostrando lo que
    decía la cámara al lado, con quién lo cargó y cuándo.
    """
    hoy, _ = _svc._fecha_valida(body.fecha)
    if not hoy:
        raise HTTPException(400, "no hay días con posición para cargar")
    return _ok(_svc.guardar_activo_integrado, hoy, body.moneda, body.importe,
               nota=body.nota, por=actor)


@router.get("/consolidado/detalle")
def consolidado_detalle(
    tab: str = Query(..., description="agro | dolar"),
    moneda: str = Query(..., description="La moneda del cuadro"),
    producto: str = Query(..., description="Código del producto: SOJ, MAI, DLR…"),
    fecha: str | None = Query(None, description="YYYY-MM-DD"),
) -> dict:
    """De qué está hecha UNA fila del cuadro CONSOLIDADOS: cuenta por cuenta.

    ⚠️ **Es AUDITORÍA, no una vista nueva.** Sale del mismo service y de los
    mismos predicados que el cuadro, así que su total tiene que dar igual que la
    fila que explica — y viaja en la respuesta para poder mostrarlo al lado.
    """
    hoy, ayer = _svc._fecha_valida(fecha)
    if not hoy:
        return {"tab": tab, "moneda": moneda, "producto": producto,
                "etiqueta": producto, "filas": [], "total": {},
                "fecha": None, "fecha_anterior": None}
    return _ok(_svc.consolidado_detalle, hoy, ayer, tab, moneda, producto)


@router.get("/aca/cuentas")
def aca_cuentas() -> list[dict]:
    """Las cuentas propias que se pueden elegir en POSICIONES DE ACA.

    La lista es una ALLOWLIST de `config`, no un filtro por defecto: cualquier
    otra cuenta de `ap5.portfolio` es de un comitente y no se ofrece.
    """
    return _ok(_svc.cuentas_aca)


@router.get("/aca")
def aca(cuenta: str = Query(..., description="Número de cuenta propia"),
        fecha: str | None = Query(None, description="YYYY-MM-DD")) -> dict:
    """La posición abierta de UNA cuenta propia, una fila por símbolo.

    ⚠️ **La cuenta se valida contra la allowlist en el SERVICE**, no acá: si el
    gate viviera en el router, un caller nuevo (un job, el MCP) podría llamar al
    service y saltearlo. Una cuenta que no está devuelve `permitida: false` en
    vez de un 403 — la vista tiene que poder decir *por qué* no hay datos.
    """
    hoy, _ = _svc._fecha_valida(fecha)
    if not hoy:
        return {"cuenta": cuenta, "permitida": True, "excluida": False,
                "filas": [], "totales": [], "fecha": None}
    return {**_ok(_svc.posiciones_aca, hoy, cuenta), "fecha": hoy}
