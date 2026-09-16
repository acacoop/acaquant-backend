"""Router /api/back-office — sección Back Office.

Por ahora solo expone Títulos / Mercado (qué títulos hay que enviar y
recibir hoy con el mercado, derivado de `CashFlow.NegocioMovimientos`).
Más sub-vistas se irán sumando acá conforme se vayan definiendo.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import acreencias as svc_acr
from api.services import comisiones_fci as svc_fci
from api.services import contabilidad_sql as svc_conta
from api.services import tenencia_hd as svc_ten
from api.services import tesoreria as svc_tes
from api.services import titulos_negativos as svc_negativos
from api.services._grupos_scope import verificar_id_cuenta
from api.services.back_office_titulos import get_titulos_mercado

router = APIRouter(prefix="/api/back-office", tags=["BackOffice"])


# Escritura de Tesorería (saldo inicial + cheques): allowlist
# `operaciones.tesoreria_escritores` + admin, gestionada en Manager → MESA.
# Va como DEPENDENCY, no como chequeo dentro del handler, para que la auditoría
# de superficie lo vea: `scripts/audit_rbac.py` lee el árbol de deps, no el
# cuerpo de la función. El service igual revalida (defensa en profundidad: lo
# invoca también el MCP/scripts, que no pasan por este router).
def require_escritura_tesoreria(actor: str = Depends(get_user_email)) -> str:
    if not svc_tes.puede_editar_saldo(actor):
        raise HTTPException(
            403, "sin permiso de escritura en Tesorería (allowlist propia — "
                 "se gestiona en Manager → MESA)")
    return actor


@router.get("/tesoreria/dia")
def tesoreria_dia(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    estado: str = Query("Procesado", description="Estado Aunesa: Procesado | Pendiente | "
                        "Pendiente de autorizar | Demorado | Rechazado | Anulado | Incompleto"),
    email: str = Depends(get_user_email),
):
    """Ingresos/egresos bancarios del día (Aunesa consultaMovDocsSolicitados).

    Ingreso = solicitud 'Depósito', Egreso = 'Extracción'. Resumen por moneda
    (ARS/USD) + una card por CUENTA OPERATIVA (banco) con su saldo inicial/final
    + detalle de movimientos. Live contra Aunesa (sin persistir)."""
    return svc_tes.ingresos_egresos_dia(fecha=fecha, estado=estado, email=email)


@router.get("/tesoreria/detalle")
def tesoreria_detalle(
    banco: str = Query(..., min_length=1, max_length=256, description="Cuenta operativa"),
    unidad: str = Query(..., min_length=1, max_length=8),
    fila: str = Query(..., min_length=1, max_length=32,
                      description="saldo_inicial | ingresos | ingresos_echeq | egresos | "
                                  "egresos_echeq | mercados | fci | bb_mas | bb_menos | "
                                  "saldo_final"),
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    email: str = Depends(get_user_email),
):
    """Auditoría de una celda de la grilla BANCOS: las operaciones individuales que
    componen ese número, calculadas con las MISMAS fuentes y filtros que la grilla."""
    try:
        return svc_tes.detalle_celda(fecha=fecha, banco=banco, unidad=unidad,
                                     fila=fila, email=email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── FOTO de la grilla BANCOS (una por día, TTL 30) ────────────────────────────

class _Snapshot(BaseModel):
    fecha: str | None = None    # ISO YYYY-MM-DD; default = hoy


@router.get("/tesoreria/foto")
def tesoreria_foto(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    email: str = Depends(get_user_email),
):
    """La grilla BANCOS congelada de un día + el detalle de cada celda.

    Es lo que sirve la vista cuando se elige una fecha pasada: el día viejo ya no se
    puede reconstruir live contra Aunesa. `existe: false` = no hay foto de ese día.
    """
    return svc_tes.foto_dia(fecha, email=email)


@router.get("/tesoreria/snapshots")
def tesoreria_snapshots(
    desde: str | None = Query(None, description="ISO YYYY-MM-DD"),
    hasta: str | None = Query(None, description="ISO YYYY-MM-DD"),
    _email: str = Depends(get_user_email),
):
    """Fotos guardadas (metadata + hash), sin el payload."""
    return svc_tes.listar_snapshots(desde=desde, hasta=hasta)


@router.post("/tesoreria/snapshots")
def tesoreria_snapshot_tomar(req: _Snapshot = Body(default=_Snapshot()),
                             actor: str = Depends(require_escritura_tesoreria)):
    """Congela la grilla BANCOS de un día. Una foto por día: re-sacarla actualiza la
    de esa fecha (la traza de cada toma queda en tesoreria_audit)."""
    try:
        return svc_tes.tomar_snapshot(fecha=req.fecha, actor=actor, origen="manual")
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _Exclusion(BaseModel):
    fecha: str | None = None
    fuente: str = Field(..., min_length=1, max_length=16)   # aunesa|cheque|mercado|bb|registro
    ref: str = Field(..., min_length=1, max_length=128)
    excluido: bool = True


@router.put("/tesoreria/exclusion")
def tesoreria_exclusion(req: _Exclusion = Body(...),
                        actor: str = Depends(require_escritura_tesoreria)):
    """Tilda/destilda un movimiento del saldo. Deja la traza en `observacion`
    ('anulado por x@y a las 14:32') y el evento en tesoreria_audit."""
    try:
        return svc_tes.set_exclusion(fecha=req.fecha, fuente=req.fuente, ref=req.ref,
                                     excluido=req.excluido, actor=actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _SaldoInicial(BaseModel):
    fecha: str | None = None
    cuenta_operativa: str = Field(..., min_length=1, max_length=256)
    unidad: str = Field(..., min_length=1, max_length=8)
    saldo_inicial: float | None = None  # null = borrar la carga del día


@router.put("/tesoreria/saldo-inicial")
def tesoreria_saldo_inicial(
    req: _SaldoInicial = Body(...),
    actor: str = Depends(require_escritura_tesoreria),
):
    """Carga manual del saldo inicial de un banco para un día (allowlist + admin)."""
    try:
        return svc_tes.set_saldo_inicial(
            fecha=req.fecha, cuenta_operativa=req.cuenta_operativa, unidad=req.unidad,
            saldo=req.saldo_inicial, actor=actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Tab CHEQUES: recibidos (del día) | emitidos (seguimiento). Los dos a mano. ──

class _Cheque(BaseModel):
    lado: str = Field("emitido", max_length=16)                 # emitido | recibido
    tipo: str | None = Field(None, max_length=16)               # recibidos: echeq | fisico
    comitente: str | None = Field(None, max_length=64)          # id_cuenta
    comitente_denominacion: str | None = Field(None, max_length=256)
    cuit: str | None = Field(None, max_length=32)
    banco: str = Field(..., min_length=1, max_length=256)       # cuenta operativa
    unidad: str = Field("ARS", min_length=1, max_length=8)
    importe: float
    estado: str = Field("pendiente", max_length=32)
    fecha_pago: str | None = None                               # ISO YYYY-MM-DD


class _EstadoCheque(BaseModel):
    estado: str = Field(..., min_length=1, max_length=32)


# ── VEPS: agenda de vencimientos (todos EGRESOS). No toca el saldo de BANCOS —
#    el egreso ya entra por REGISTROS MANUALES (tipo 'VEP'), ver el service. ──────
class _Vep(BaseModel):
    numero_vep: str | None = Field(None, max_length=64)
    concepto: str | None = Field(None, max_length=512)
    importe: float
    # El default lo pone el service (VEP_BANCO_DEFAULT) si el front no manda banco.
    banco: str | None = Field(None, max_length=256)
    unidad: str = Field("ARS", min_length=1, max_length=8)
    vencimiento: str | None = None                              # ISO YYYY-MM-DD
    estado: str = Field("pendiente", max_length=32)


class _EstadoVep(BaseModel):
    estado: str = Field(..., min_length=1, max_length=32)


@router.get("/tesoreria/veps")
def tesoreria_veps(
    incluir_pagados: bool = Query(False, description="también los ya pagados"),
    email: str = Depends(get_user_email),
):
    """Tab VEPS: tablero de seguimiento, SIN filtro de fecha (igual que los cheques
    emitidos). Cada fila trae `vencido` calculado server-side —vencimiento pasado y
    todavía sin pagar— que es lo que la vista pinta de amarillo."""
    return svc_tes.veps(incluir_pagados=incluir_pagados, email=email)


@router.post("/tesoreria/veps")
def tesoreria_vep_crear(req: _Vep = Body(...),
                        actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.crear_vep(req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/veps/{id_}")
def tesoreria_vep_editar(id_: int, req: _Vep = Body(...),
                         actor: str = Depends(require_escritura_tesoreria)):
    """Edición completa. Es también el camino para completar un VEP que nació como
    espejo de un registro manual (sin número, concepto ni vencimiento)."""
    try:
        return svc_tes.editar_vep(id_, req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/veps/{id_}/estado")
def tesoreria_vep_estado(id_: int, req: _EstadoVep = Body(...),
                         actor: str = Depends(require_escritura_tesoreria)):
    """Cambia SOLO el estado (click en la celda). 'pagado' saca la fila de la vista
    pero NO la borra: el histórico se conserva."""
    try:
        return svc_tes.set_estado_vep(id_, req.estado, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/tesoreria/veps/{id_}")
def tesoreria_vep_borrar(id_: int, actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.borrar_vep(id_, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/tesoreria/cheques")
def tesoreria_cheques(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy (solo recibidos)"),
    incluir_cerrados: bool = Query(False, description="emitidos: también los completados"),
    email: str = Depends(get_user_email),
):
    """Tab CHEQUES: `emitidos` + `recibidos`, los dos de carga manual.

    EMITIDOS no se filtran por fecha (tablero de seguimiento); solo se listan los
    abiertos — 'completado' los saca de la vista, pero la fila queda en la tabla.
    RECIBIDOS son todos del día (`fecha`): se registran intradía y no se arrastran."""
    return svc_tes.cheques(fecha=fecha, incluir_cerrados=incluir_cerrados, email=email)


@router.get("/tesoreria/cheques/comitentes")
def tesoreria_cheques_comitentes(
    q: str = Query(..., min_length=1, description="número de cuenta o denominación"),
    _email: str = Depends(get_user_email),
):
    """Autocomplete del form de cheques: busca por número o nombre y trae el CUIT."""
    return {"comitentes": svc_tes.buscar_comitentes(q=q)}


@router.post("/tesoreria/cheques")
def tesoreria_cheque_crear(req: _Cheque = Body(...),
                           actor: str = Depends(require_escritura_tesoreria)):
    """Alta de un cheque emitido (allowlist de Tesorería + admin)."""
    try:
        return svc_tes.crear_cheque(req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/cheques/{id_}")
def tesoreria_cheque_editar(id_: int, req: _Cheque = Body(...),
                            actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.editar_cheque(id_, req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/cheques/{id_}/estado")
def tesoreria_cheque_estado(id_: int, req: _EstadoCheque = Body(...),
                            actor: str = Depends(require_escritura_tesoreria)):
    """Cambia SOLO el estado (el click en la celda ESTADO de la vista, sin reabrir
    la operación). El estado de cierre saca la fila de la vista."""
    try:
        return svc_tes.set_estado_cheque(id_, req.estado, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/tesoreria/cheques/{id_}")
def tesoreria_cheque_borrar(id_: int, actor: str = Depends(require_escritura_tesoreria)):
    """Los espejo de Aunesa (`origen='aunesa'`) NO se borran: el próximo poll los
    recrea. Se cierran con el estado 'completado'."""
    try:
        return svc_tes.borrar_cheque(id_, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Catálogo de bancos: alta manual desde la vista (los descubiertos por Aunesa
#    se auto-registran; esto es para los que todavía no operaron nunca) ─────────

class _CuentaNueva(BaseModel):
    cuenta_operativa: str = Field(..., min_length=1, max_length=256)
    unidad: str = Field(..., min_length=1, max_length=8)
    numero_cuenta: str | None = Field(None, max_length=64)
    # Identificador del banco en HYGIRUS: se guarda para otra funcionalidad, NO se
    # muestra en la grilla.
    numero_hygirus: str | None = Field(None, max_length=64)


class _CuentaEdit(_CuentaNueva):
    nuevo_nombre: str | None = Field(None, max_length=256)
    activa: bool | None = None


@router.get("/tesoreria/cuentas")
def tesoreria_cuentas(_email: str = Depends(get_user_email)):
    """Catálogo de bancos con nombre, moneda y número de cuenta (ABM de la vista)."""
    return {"cuentas": svc_tes.listar_cuentas()}


@router.post("/tesoreria/cuentas")
def tesoreria_cuenta_crear(req: _CuentaNueva = Body(...),
                           actor: str = Depends(require_escritura_tesoreria)):
    """Da de alta una cuenta operativa (banco) en el catálogo de Tesorería."""
    try:
        return svc_tes.crear_cuenta(req.cuenta_operativa, req.unidad, actor,
                                    numero_cuenta=req.numero_cuenta,
                                    numero_hygirus=req.numero_hygirus)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/cuentas")
def tesoreria_cuenta_editar(req: _CuentaEdit = Body(...),
                            actor: str = Depends(require_escritura_tesoreria)):
    """Edita un banco: número de cuenta, nombre (arrastra los históricos) y alta/baja."""
    try:
        return svc_tes.editar_cuenta(
            req.cuenta_operativa, req.unidad, actor, numero_cuenta=req.numero_cuenta,
            numero_hygirus=req.numero_hygirus, nuevo_nombre=req.nuevo_nombre,
            activa=req.activa)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/tesoreria/cuentas")
def tesoreria_cuenta_borrar(
    cuenta_operativa: str = Query(..., min_length=1, max_length=256),
    unidad: str = Query(..., min_length=1, max_length=8),
    actor: str = Depends(require_escritura_tesoreria),
):
    """Saca un banco del catálogo: borrado físico si nadie lo referencia, baja
    lógica si ya tiene históricos o lo descubrió Aunesa."""
    try:
        return svc_tes.borrar_cuenta(cuenta_operativa, unidad, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Catálogo de MERCADOS / FCI (ABM en modal desde la tab MERCADOS) ───────────

class _Entidad(BaseModel):
    bloque: str = Field(..., min_length=1, max_length=16)   # mercado | fci
    codigo: str | None = Field(None, max_length=64)
    nombre: str = Field(..., min_length=1, max_length=128)


@router.get("/tesoreria/entidades")
def tesoreria_entidades(
    bloque: str = Query("", description="mercado | fci; vacío = ambos"),
    incluir_inactivas: bool = Query(False),
    _email: str = Depends(get_user_email),
):
    """Catálogo de mercados y FCI."""
    return {"entidades": svc_tes.catalogo_entidades(
        bloque or None, solo_activas=not incluir_inactivas)}


@router.post("/tesoreria/entidades")
def tesoreria_entidad_crear(req: _Entidad = Body(...),
                            actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.crear_entidad(req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/entidades/{id_}")
def tesoreria_entidad_editar(id_: int, req: _Entidad = Body(...),
                             actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.editar_entidad(id_, req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/tesoreria/entidades/{id_}")
def tesoreria_entidad_baja(id_: int, actor: str = Depends(require_escritura_tesoreria)):
    """Baja LÓGICA: la fila queda (los movimientos históricos la referencian), se
    saca del desplegable."""
    try:
        return svc_tes.baja_entidad(id_, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Tab MERCADOS: 4 tableros del día (mercado ingresos/pagos, FCI rescates/
#    suscripciones). Carga manual, mismo modelo con distinto `tipo`. ───────────

class _Mercado(BaseModel):
    fecha: str | None = None                                # ISO YYYY-MM-DD
    tipo: str = Field(..., min_length=1, max_length=16)     # ingreso|pago|rescate|suscripcion
    entidad: str | None = Field(None, max_length=128)       # mercado o FCI
    banco: str = Field(..., min_length=1, max_length=256)
    unidad: str = Field("ARS", min_length=1, max_length=8)
    importe: float
    estado: str = Field("pendiente", max_length=32)


@router.get("/tesoreria/mercados")
def tesoreria_mercados(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    email: str = Depends(get_user_email),
):
    """Los 4 tableros de la tab MERCADOS del día + catálogo de bancos para el form."""
    return svc_tes.mercados(fecha=fecha, email=email)


@router.post("/tesoreria/mercados")
def tesoreria_mercado_crear(req: _Mercado = Body(...),
                            actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.crear_mercado(req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/mercados/{id_}")
def tesoreria_mercado_editar(id_: int, req: _Mercado = Body(...),
                             actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.editar_mercado(id_, req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/mercados/{id_}/estado")
def tesoreria_mercado_estado(id_: int, req: _EstadoCheque = Body(...),
                             actor: str = Depends(require_escritura_tesoreria)):
    """Cambia SOLO el estado desde la celda (reusa el body {estado})."""
    try:
        return svc_tes.set_estado_mercado(id_, req.estado, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/tesoreria/mercados/{id_}")
def tesoreria_mercado_borrar(id_: int, actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.borrar_mercado(id_, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


# ── Tab BANCO A BANCO: transferencias internas entre cuentas propias ──────────

class _BancoABanco(BaseModel):
    fecha: str | None = None                                    # ISO YYYY-MM-DD
    cta_debito: str = Field(..., min_length=1, max_length=256)  # de dónde sale
    cta_credito: str = Field(..., min_length=1, max_length=256)  # a dónde entra
    unidad: str = Field("ARS", min_length=1, max_length=8)
    importe: float
    estado: str = Field("pendiente", max_length=32)


@router.get("/tesoreria/banco-a-banco")
def tesoreria_bb(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    email: str = Depends(get_user_email),
):
    """Transferencias internas del día + catálogo de bancos para el form."""
    return svc_tes.banco_a_banco(fecha=fecha, email=email)


@router.post("/tesoreria/banco-a-banco")
def tesoreria_bb_crear(req: _BancoABanco = Body(...),
                       actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.crear_bb(req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/banco-a-banco/{id_}")
def tesoreria_bb_editar(id_: int, req: _BancoABanco = Body(...),
                        actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.editar_bb(id_, req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/banco-a-banco/{id_}/estado")
def tesoreria_bb_estado(id_: int, req: _EstadoCheque = Body(...),
                        actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.set_estado_bb(id_, req.estado, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/tesoreria/banco-a-banco/{id_}")
def tesoreria_bb_borrar(id_: int, actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.borrar_bb(id_, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@router.get("/tesoreria/banco-a-banco/export-txt")
def tesoreria_bb_txt(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    email: str = Depends(get_user_email),
):
    """Asiento de ajuste para HYGIRUS con las transferencias NO completadas.

    Responde SIEMPRE 200 con el contenido dentro de un JSON: el proxy de Next
    parsea todo como JSON y mapea cualquier error a un 502 sin mensaje, así que
    un `text/plain` o un 400 acá llegarían al browser como un 502 mudo.
    """
    return svc_tes.txt_banco_a_banco(fecha=fecha)


# ── REGISTROS MANUALES (modal de la tab BANCOS) — fuente de movimientos que NO
#    viene de la API; impacta el saldo del banco elegido según su sentido ────────

class _Registro(BaseModel):
    fecha: str | None = None
    tipo: str = Field(..., min_length=1, max_length=64)
    banco: str = Field(..., min_length=1, max_length=256)
    unidad: str = Field("ARS", min_length=1, max_length=8)
    importe: float
    sentido: str = Field("egreso", max_length=16)   # egreso | ingreso
    # Tab del modal: 'rescate' (tipo del catálogo, entra al TOTAL de Rescate ACA
    # Valores) | 'otros' (tipo libre, NO entra a ese total). Los dos tocan el banco.
    grupo: str = Field("rescate", max_length=16)


class _SaldoRegistros(BaseModel):
    fecha: str | None = None
    unidad: str = Field("ARS", min_length=1, max_length=8)
    importe: float


@router.get("/tesoreria/registros")
def tesoreria_registros(
    fecha: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    unidad: str = Query("ARS", description="Moneda del resumen"),
    email: str = Depends(get_user_email),
):
    """Registros manuales del día + resumen de cada tab (RESCATE / OTROS).

    La fila SALDOS del rescate es manual y no sale de los registros.
    """
    return svc_tes.registros(fecha=fecha, unidad=unidad, email=email)


@router.post("/tesoreria/registros")
def tesoreria_registro_crear(req: _Registro = Body(...),
                             actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.crear_registro(req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/tesoreria/registros/{id_}")
def tesoreria_registro_editar(id_: int, req: _Registro = Body(...),
                              actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.editar_registro(id_, req.model_dump(), actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/tesoreria/registros/{id_}")
def tesoreria_registro_borrar(id_: int,
                              actor: str = Depends(require_escritura_tesoreria)):
    try:
        return svc_tes.borrar_registro(id_, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@router.put("/tesoreria/registros-saldo")
def tesoreria_registros_saldo(req: _SaldoRegistros = Body(...),
                              actor: str = Depends(require_escritura_tesoreria)):
    """Fila SALDOS del resumen: carga manual, no sale de los registros."""
    try:
        return svc_tes.set_saldo_registros(fecha=req.fecha, unidad=req.unidad,
                                           importe=req.importe, actor=actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


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


# ── Control de títulos NEGATIVOS (posición T0) ────────────────────────────────
@router.get("/titulos-negativos")
def titulos_negativos(
    incluir_todo: bool = Query(
        False, description="Sumar también MONEDAS y DERIVADOS. Default: no — en los "
                           "dos el negativo es normal (descubierto bancario / posición "
                           "vendida), no un problema de custodia"),
    _email: str = Depends(get_user_email),
):
    """Títulos con nominales NEGATIVOS, en los DOS horizontes.

    T0 = liquidada a HOY (lo que está en custodia AHORA: un negativo es un
    descubierto real). T1 = liquidada a MAÑANA con lo concertado hoy adentro (un
    negativo que todavía se puede resolver).

    Lee `portafolio.tenencia_live`, que refresca el daemon durante la rueda — la
    vista pollea y por eso es "tiempo real". La respuesta trae `actualizado_at`
    para que la pantalla pueda distinguir "no hay negativos" de "el daemon no
    está corriendo".
    """
    # `@cached` arma la key por NOMBRE de argumento → siempre kwargs (api/CLAUDE.md).
    data = svc_negativos.titulos_negativos(incluir_todo=incluir_todo)
    # La PRESENCIA se resuelve ACÁ y no adentro del service, por dos motivos que
    # el `@cached` de arriba hace obligatorios:
    #   · es POR USUARIO — metida en la respuesta cacheada, a uno le llegaría la
    #     presencia de otro;
    #   · marcar presencia es un EFECTO, y durante los 10s de cache el service
    #     ni se ejecuta: el que pollea en esa ventana nunca quedaría registrado.
    # El `{**data}` arma un dict nuevo: mutar el cacheado lo envenenaría para
    # todos los que lo lean después.
    return {**data, "presencia": svc_negativos.presencia("saldos", _email)}


class _OcultarPayload(BaseModel):
    id_cuenta: str = Field(..., min_length=1, max_length=32,
                           description="Número de cuenta, sin corchetes (ej. '805')")
    motivo: str | None = Field(None, max_length=200,
                               description="Por qué se oculta — queda a la vista de todos")


@router.put("/saldos/ocultas")
def ocultar_cuenta(req: _OcultarPayload = Body(...),
                   actor: str = Depends(get_user_email)) -> dict:
    """Oculta una cuenta del control de saldos. Queda quién y cuándo.

    OCULTAR NO ES EXCLUIR: el saldo se sigue guardando igual, solo deja de
    mostrarse en esta pantalla. Es para las cuentas que aparecen siempre y que
    nadie tiene que mirar — que cada uno las saltee con el ojo todos los días es
    lo que termina haciendo que el control se deje de mirar.
    """
    try:
        return svc_negativos.ocultar_cuenta(req.id_cuenta, actor=actor,
                                            motivo=req.motivo or "")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/saldos/ocultas/{id_cuenta}")
def mostrar_cuenta(id_cuenta: str, actor: str = Depends(get_user_email)) -> dict:
    """Saca una cuenta de la lista de ocultas — vuelve a verse en el control."""
    try:
        return svc_negativos.mostrar_cuenta(id_cuenta, actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


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


@router.get("/acreencias/cliente", dependencies=[Depends(verificar_id_cuenta)])
def acreencias_cliente(
    id_cuenta: str = Query(..., description="id_cuenta del cliente"),
    desde: str | None = Query(None, description="ISO YYYY-MM-DD; default = hoy"),
    _email: str = Depends(get_user_email),
):
    """Próximos cobros de un cliente.

    `verificar_id_cuenta` → 403 si la cuenta está fuera del grupo del usuario.
    No-op para admin / usuarios sin grupo (scope None)."""
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


@router.get("/tenencia-hd/en-alquiler")
def tenencia_hd_en_alquiler(
    desde: str | None = None,
    _email: str = Depends(get_user_email),
):
    """TODOS los (título, cuenta) de 100/255/256 tenidos entre `desde` (default
    01/06/2026, arranque del proceso legal) y HOY + su marca de alquiler (SI/NO,
    cantidad, desde, hasta). Un título tenido en el rango pero ya no hoy aparece
    igual. Alimenta la vista 'Títulos en alquiler'."""
    return svc_ten.titulos_en_alquiler(desde=desde)


@router.post("/tenencia-hd/alquiler")
def tenencia_hd_alquiler(
    id_cuenta:   str = Body(..., embed=True),
    unidad:      str = Body(..., embed=True),
    en_alquiler: bool = Body(..., embed=True),
    cantidad:    float | None = Body(None, embed=True, description="nominales en alquiler"),
    desde:       str | None = Body(None, embed=True, description="ISO YYYY-MM-DD; fecha desde"),
    hasta:       str | None = Body(None, embed=True, description="ISO YYYY-MM-DD; fecha hasta (vacío = sigue)"),
    email: str = Depends(get_user_email),
):
    """Marca DURABLE de alquiler por (título, cuenta): SI/NO, nominales y período
    desde/hasta. NO es por día — persiste hasta que el back office la cambie. Netea
    la posición en las vistas de tenencia dentro de [desde, hasta]."""
    return svc_ten.set_alquiler_marca(
        id_cuenta=id_cuenta, unidad=unidad, en_alquiler=en_alquiler,
        cantidad=cantidad, desde=desde, hasta=hasta, email=email)


# ── PORTFOLIO ALQUILER (tab dentro de Títulos en Alquiler) ──
# Lista CURADA de títulos (el back office los agrega con el "+") mostrada como
# Tenencia Valorizada: serie diaria + posiciones por día, cuentas 100/255/256,
# valuación en bruto (sin netear marcas).
@router.get("/tenencia-hd/portfolio-alquiler")
def tenencia_hd_portfolio_alquiler(
    _email: str = Depends(get_user_email),
):
    """Serie diaria (fecha · tc · 100/255/256 · total) de los títulos ELEGIDOS
    + la lista de elegidos. Sin títulos elegidos → dias=[]."""
    return svc_ten.portfolio_alquiler_dias()


@router.get("/tenencia-hd/portfolio-alquiler/posiciones")
def tenencia_hd_portfolio_alquiler_posiciones(
    fecha: str = Query(..., description="ISO YYYY-MM-DD"),
    _email: str = Depends(get_user_email),
):
    """Posiciones del día SOLO de los títulos elegidos (PX · 100/255/256 · Total).
    Un elegido sin posición ese día aparece igual, en cero."""
    return svc_ten.portfolio_alquiler_posiciones(fecha=fecha)


@router.get("/tenencia-hd/portfolio-alquiler/instrumentos")
def tenencia_hd_portfolio_alquiler_instrumentos(
    _email: str = Depends(get_user_email),
):
    """Catálogo completo de instrumentos (assets ∪ tenencia de cuentas propias)
    para el buscador del '+'."""
    return {"unidades": svc_ten.portfolio_alquiler_instrumentos()}


@router.post("/tenencia-hd/portfolio-alquiler")
def tenencia_hd_portfolio_alquiler_set(
    unidad:       str = Body(..., embed=True),
    en_portfolio: bool = Body(..., embed=True, description="True = agregar, False = quitar"),
    email: str = Depends(get_user_email),
):
    """Agrega o quita un título de la lista del Portfolio Alquiler (durable,
    compartida por todo el back office)."""
    return svc_ten.set_portfolio_alquiler(unidad=unidad, en_portfolio=en_portfolio, email=email)


@router.post("/tenencia-hd/portfolio-alquiler/nominal")
def tenencia_hd_portfolio_alquiler_nominal(
    unidad:    str = Body(..., embed=True),
    id_cuenta: str = Body(..., embed=True),
    fecha:     str = Body(..., embed=True, description="ISO YYYY-MM-DD (el día que se está editando)"),
    cantidad:  float | None = Body(None, embed=True,
                                   description="nominales en alquiler; None = borrar la edición de ese día"),
    email: str = Depends(get_user_email),
):
    """Nominales en alquiler de (título, cuenta) A PARTIR de `fecha` — carry
    forward: rigen hasta la próxima edición. 0 = apaga de ese día en adelante.
    Es la fuente del filtro SIN ALQUILER de Tenencia Valorizada."""
    return svc_ten.set_portfolio_alquiler_nominal(
        unidad=unidad, id_cuenta=id_cuenta, fecha=fecha, cantidad=cantidad, email=email)


# ── CONTABILIDAD (resultado mensual por título de las cuentas propias) ────────
# DOS canales y nada más: total = TENENCIA + INTERMEDIACIÓN. NO hay rentas —
# cupones/dividendos no entran al informe. La valuación depende de la SITUACIÓN
# (ocho casos) y el cuadre viven en el service
# (api/services/contabilidad_sql.py). Lectura = módulo back-office; escritura
# del ABM de cuentas = allowlist de Tesorería + admin (mismo gate que
# Interbanking), como dependency para que audit_rbac la vea.

_RE_MES = r"^\d{4}-(0[1-9]|1[0-2])$"


@router.get("/contabilidad/cuentas")
def contabilidad_cuentas(email: str = Depends(get_user_email)):
    """Cuentas propias del proceso (las que eligió el equipo desde la vista).

    `elegibles` es el universo del que se puede elegir: las que TIENEN
    movimientos en `operaciones.movimientos_propias`, que es de donde sale el
    informe. Viaja acá para que el ABM ofrezca la lista en vez de obligar a
    tipear un id a ciegas."""
    return {"cuentas": svc_conta.cuentas(), "elegibles": svc_conta.cuentas_elegibles()}


@router.post("/contabilidad/cuentas")
def contabilidad_cuenta_alta(
    id_cuenta: str = Body(..., embed=True),
    etiqueta: str | None = Body(None, embed=True),
    actor: str = Depends(require_escritura_tesoreria),
):
    """Suma una cuenta al proceso (o le cambia la etiqueta si ya estaba).
    ⚠️ Solo cuentas CON movimientos en `movimientos_propias` (2026-09-05): sin
    eso, un id mal tipeado mostraba un informe vacío indistinguible de un mes
    sin actividad."""
    return svc_conta.agregar_cuenta(actor, id_cuenta, etiqueta)


@router.delete("/contabilidad/cuentas/{id_cuenta}")
def contabilidad_cuenta_baja(id_cuenta: str, actor: str = Depends(require_escritura_tesoreria)):
    """Saca una cuenta del proceso (no borra ningún dato de tenencia/boletos)."""
    return svc_conta.borrar_cuenta(actor, id_cuenta)


@router.get("/contabilidad/excluidos")
def contabilidad_excluidos(
    id_cuenta: str = Query(...),
    mes: str = Query(..., pattern=_RE_MES),
    email: str = Depends(get_user_email),
):
    """Los movimientos que el back office sacó del mes, con quién y cuándo."""
    return {"excluidos": svc_conta.excluidos(id_cuenta, mes)}


@router.post("/contabilidad/excluir")
def contabilidad_excluir(
    id_cuenta: str = Body(..., embed=True),
    fecha: str = Body(..., embed=True),
    id_linea: str = Body(..., embed=True),
    ocurrencia: int = Body(1, embed=True),
    motivo: str | None = Body(None, embed=True),
    actor: str = Depends(require_escritura_tesoreria),
):
    """Saca un movimiento del resultado del mes. Cambia un número que después se
    informa, así que va con el mismo gate de escritura que el resto de Tesorería
    y queda firmado."""
    return svc_conta.excluir(actor, id_cuenta=id_cuenta, fecha=fecha,
                             id_linea=id_linea, ocurrencia=ocurrencia, motivo=motivo)


@router.post("/contabilidad/incluir")
def contabilidad_incluir(
    fecha: str = Body(..., embed=True),
    id_linea: str = Body(..., embed=True),
    ocurrencia: int = Body(1, embed=True),
    actor: str = Depends(require_escritura_tesoreria),
):
    """Vuelve a contabilizar un movimiento excluido."""
    return svc_conta.incluir(actor, fecha=fecha, id_linea=id_linea, ocurrencia=ocurrencia)


@router.get("/contabilidad/resumen")
def contabilidad_resumen(
    id_cuenta: str = Query(...),
    mes: str = Query(..., pattern=_RE_MES, description="YYYY-MM del mes a contabilizar"),
    email: str = Depends(get_user_email),
):
    """El informe del mes: una fila por título con RxT / intermediación / total
    + cuadre de nominales, y los totales sumados en el backend.

    El id se normaliza a la CANÓNICA del grupo (`config.CUENTAS_UNIFICADAS`)
    ANTES de entrar: `resumen` está cacheado por argumento, así que pedir «100»
    y pedir «255» calcularían dos veces exactamente el mismo informe."""
    return svc_conta.resumen(id_cuenta=svc_conta.canonica(id_cuenta), mes=mes)


@router.get("/contabilidad/detalle")
def contabilidad_detalle(
    id_cuenta: str = Query(...),
    mes: str = Query(..., pattern=_RE_MES),
    key: str = Query(..., description="key de la fila del resumen (ticker/CAFCI)"),
    email: str = Depends(get_user_email),
):
    """Drill-down auditable: los boletos del mes que componen la fila."""
    return svc_conta.detalle(id_cuenta=svc_conta.canonica(id_cuenta), mes=mes, key=key)


# ?????????????????????????????????????????????????????????????????????????????
# COMISIONES FCI ? qu? cobra ACA Valores por la tenencia de fondos
# ?????????????????????????????????????????????????????????????????????????????
# C?lculo y decisiones: api/services/comisiones_fci.py. Solo lectura: no hay ABM,
# todo sale de `portafolio.tenencia` (la misma foto que AuM) y del `fee_admin` de
# Manager ? T?TULOS. El filtro es por MES y no desde/hasta: la comisi?n se liquida
# por mes, y un rango libre invitar?a a comparar per?odos que no son comparables.


@router.get("/comisiones-fci")
def comisiones_fci_mes(
    mes: str = Query(..., pattern=_RE_MES, description="YYYY-MM"),
    _email: str = Depends(get_user_email),
):
    """Tabla por fondo + acumulado por sociedad gerente + totales ARS/USD del mes.

    `arancel_dia` es el devengamiento del d?a de CORTE (la ?ltima foto del mes: el
    mes en curso corta en la m?s reciente, uno cerrado en su ?ltimo h?bil) y
    `arancel_acum` es lo acumulado del 1? hasta ese corte.

    Los fondos SIN `fee_admin` cargado vienen con `sin_fee: true` y NO suman a los
    totales ? ah? el arancel no es cero, es desconocido, y el bloque `sin_fee` dice
    cu?ntos son y cu?nta valuaci?n qued? sin poder devengar."""
    return svc_fci.resumen_mes(mes)


@router.get("/comisiones-fci/detalle")
def comisiones_fci_detalle(
    mes: str = Query(..., pattern=_RE_MES, description="YYYY-MM"),
    unidad: str = Query(..., min_length=1, max_length=256, description="Fondo"),
    _email: str = Depends(get_user_email),
):
    """Las CUENTAS que tuvieron ese fondo en el mes ? trazabilidad de la fila.

    Usa el MISMO c?lculo y los mismos tramos que la tabla, as? el detalle no puede
    contradecir al n?mero que lo abri?."""
    return svc_fci.detalle_fondo(mes, unidad)


@router.get("/comisiones-fci/serie")
def comisiones_fci_serie(_email: str = Depends(get_user_email)):
    """Acumulado MENSUAL hist?rico por moneda (gr?fico de barras)."""
    return svc_fci.serie_mensual()


@router.get("/comisiones-fci/meses")
def comisiones_fci_meses(_email: str = Depends(get_user_email)):
    """Meses que tienen foto de tenencia FCI ? alimenta el selector."""
    return svc_fci.meses_disponibles()


@router.get("/comisiones-fci/fees")
def comisiones_fci_fees(_email: str = Depends(get_user_email)):
    """Fee de cada fondo/gerente + la f?rmula, para el modal de ayuda (`?`).

    Viajan el honorario ENTERO y la mitad que cobra ACA por separado: el modal
    muestra la cuenta completa en vez de pedir que se conf?e en el ?2."""
    return svc_fci.fees_vigentes()
