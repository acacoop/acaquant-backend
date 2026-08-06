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
from api.services import tenencia_hd as svc_ten
from api.services import tesoreria as svc_tes
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


@router.get("/tesoreria/al2")
def tesoreria_al2(
    dias: int = Query(60, ge=1, le=365, description="Ventana hacia atrás (default 60)"),
    persona: str = Query("todas", description="todas | fisica | juridica"),
    unidad: str = Query("", description="ARS / USD; vacío = todas"),
    estado: str = Query("Procesado", description="Estado Aunesa; vacío = todos"),
    _email: str = Depends(get_user_email),
):
    """SALDO AL2 — movimientos del banco FERSI SA + serie diaria acumulada.

    Lee `operaciones.tesoreria_al2` (lo escribe `jobs.tesoreria_al2`), no Aunesa: la
    serie de 60 días implicaría 60 llamadas por pantallazo. Es lo ÚNICO que se
    persiste de tesorería."""
    return svc_tes.saldo_al2(dias=dias, persona=persona, unidad=unidad, estado=estado)


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
    try:
        return svc_tes.borrar_cheque(id_, actor)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


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


# ── REGISTROS MANUALES (modal de la tab BANCOS) — fuente de movimientos que NO
#    viene de la API; impacta el saldo del banco elegido según su sentido ────────

class _Registro(BaseModel):
    fecha: str | None = None
    tipo: str = Field(..., min_length=1, max_length=64)
    banco: str = Field(..., min_length=1, max_length=256)
    unidad: str = Field("ARS", min_length=1, max_length=8)
    importe: float
    sentido: str = Field("egreso", max_length=16)   # egreso | ingreso


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
    """Registros manuales del día + resumen por tipo (la fila SALDOS es manual)."""
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
