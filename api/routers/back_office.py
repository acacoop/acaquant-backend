"""Router /api/back-office — sección Back Office.

Por ahora solo expone Títulos / Mercado (qué títulos hay que enviar y
recibir hoy con el mercado, derivado de `CashFlow.NegocioMovimientos`).
Más sub-vistas se irán sumando acá conforme se vayan definiendo.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from api.auth import get_user_email
from api.services import acreencias as svc_acr
from api.services import tenencia_hd as svc_ten
from api.services import tesoreria as svc_tes
from api.services.back_office_titulos import get_titulos_mercado

router = APIRouter(prefix="/api/back-office", tags=["BackOffice"])


@router.get("/tesoreria/dia")
def tesoreria_dia(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    estado: str = Query("Procesado", description="Estado Aunesa: Procesado | Pendiente | "
                        "Pendiente de autorizar | Demorado | Rechazado | Anulado | Incompleto"),
    _email: str = Depends(get_user_email),
):
    """Ingresos/egresos bancarios del día (Aunesa consultaMovDocsSolicitados).

    Ingreso = solicitud 'Depósito', Egreso = 'Extracción'. Resumen por moneda
    (ARS/USD) + detalle de movimientos. Live contra Aunesa (sin persistir)."""
    return svc_tes.ingresos_egresos_dia(fecha=fecha, estado=estado)


@router.get("/titulos-mercado")
def titulos_mercado(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    _email: str = Depends(get_user_email),
):
    """Títulos a enviar/recibir al mercado para `fecha` (default hoy).

    Settlement = (ops de `fecha` con plazo CI/Inm) + (ops del día hábil
    anterior con plazo 24hs). Si `fecha` no es día hábil devuelve
    estructura vacía con `mercado_cerrado: true`.
    """
    return get_titulos_mercado(fecha=fecha)


# ── Acreencias clientes (cobros futuros, precompute CashFlow.Acreencias) ──
@router.get("/acreencias/por-dia")
def acreencias_por_dia(
    desde: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    hasta: str | None = Query(None, description="ISO YYYY-MM-DD"),
    _email: str = Depends(get_user_email),
):
    """Por día: total a cobrar (por moneda) + #clientes + #pagos."""
    return svc_acr.por_dia(desde=desde, hasta=hasta)


@router.get("/acreencias/dia")
def acreencias_dia(
    fecha: str = Query(..., description="ISO YYYY-MM-DD"),
    _email: str = Depends(get_user_email),
):
    """Quién cobra en una fecha y cuánto (por cliente·ticker)."""
    return svc_acr.del_dia(fecha)


@router.get("/acreencias/cliente")
def acreencias_cliente(
    id_cuenta: str = Query(..., description="id_cuenta del cliente"),
    desde: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    _email: str = Depends(get_user_email),
):
    """Próximos cobros de un cliente."""
    return svc_acr.del_cliente(id_cuenta, desde=desde)


# ── Tenencia Valorizada (cuentas propias 100/255/256) — SQL-native ──
# `cartera`: 'HD' (Cartera USD, default) o 'ARS' (todo lo no-HD). Lee
# portafolio.tenencia directo (api/services/tenencia_hd.py).
@router.get("/tenencia-hd")
def tenencia_hd(
    cartera: str = Query("HD", description="HD (Cartera USD) | ARS (todo lo no-HD)"),
    _email: str = Depends(get_user_email),
):
    """Serie diaria: AuM por cuenta (100/255/256) de la cartera elegida — tabla izquierda."""
    return svc_ten.tenencia_dias(cartera=cartera)


@router.get("/tenencia-hd/posiciones")
def tenencia_hd_posiciones(
    fecha: str = Query(..., description="ISO YYYY-MM-DD del día a ver"),
    cartera: str = Query("HD", description="HD (Cartera USD) | ARS (todo lo no-HD)"),
    _email: str = Depends(get_user_email),
):
    """Posiciones por título (desglose por cuenta) de un día — tabla derecha."""
    return svc_ten.tenencia_posiciones(fecha=fecha, cartera=cartera)


@router.post("/tenencia-hd/precio")
def tenencia_hd_precio(
    fecha:  str = Body(..., embed=True, description="ISO YYYY-MM-DD"),
    unidad: str = Body(..., embed=True),
    precio: float = Body(..., embed=True, description="precio nuevo"),
    dividir_100: bool = Body(True, embed=True, description="True = paridad (÷100); False = valor pleno"),
    cartera: str = Body("HD", embed=True, description="HD | ARS — para recalcular el total del día"),
    _email: str = Depends(get_user_email),
):
    """Edita a mano el PRECIO de una unidad en un día → recalcula la valuación de las
    3 cuentas + los totales, DIRECTO en SQL portafolio.tenencia. `dividir_100`=True
    (default, paridad) → cantidad × precio / 100; False → cantidad × precio."""
    return svc_ten.actualizar_precio_posicion(
        fecha=fecha, unidad=unidad, precio=precio, dividir_100=dividir_100, cartera=cartera)


@router.post("/tenencia-hd/alquiler")
def tenencia_hd_alquiler(
    unidad:   str = Body(..., embed=True),
    cantidad: float | None = Body(None, embed=True, description="nominales en alquiler; 0/null = quitar"),
    email: str = Depends(get_user_email),
):
    """Marca DURABLE de alquiler por título (nominales en alquiler). Se setea una
    vez y dura hasta que se cambie — no es por día. cantidad 0/null quita la marca."""
    return svc_ten.set_alquiler(unidad=unidad, cantidad=cantidad, email=email)
