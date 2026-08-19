"""api/routers/interbanking.py — tab INTERBANKING del BACK OFFICE.

Plumbing HTTP puro: la lógica vive en `api/services/bancos.py`.

⚠️ **Hacia INTERBANKING no se escribe NUNCA** — `core/interbanking.py` implementa
únicamente GET y hay un test que lo congela. Esa es la línea que importa: la
integración no puede mover plata ni por error.

Lo que SÍ escribe (desde 2026-08-18) va todo a tablas NUESTRAS: la clasificación
de gastos (`gastos_reglas` / `gastos_overrides` / `gastos_baldes` /
`movimientos_ignorados`), y lo manual
(`movimientos_manuales` + las cuentas con `origen='manual'`). **Nada de eso toca
el extracto del banco ni sale a internet.** Son 14 endpoints, todos detrás de
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


@router.post("/conciliar")
def conciliar(
    cuenta_id: int = Body(..., embed=True),
    # La grilla CRUDA del Excel: el navegador solo abre el archivo, y qué columna
    # es el saldo lo decide el service, donde se puede testear.
    filas: list = Body(..., embed=True),
    fecha: date | None = Body(None, embed=True),
    email: str = Depends(get_user_email),
) -> dict:
    """Nuestro saldo al cierre contra el último saldo del mayor contable.

    Es un POST pero **no escribe nada**: el archivo va en el cuerpo porque no
    entra en una query string. Nada se persiste.
    """
    try:
        return _svc.conciliar(email, cuenta_id, _fecha(fecha), filas)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/diferencias")
def diferencias(
    fecha: date | None = Query(None, description="día a controlar (default: el de la vista)"),
    email: str = Depends(get_user_email),
) -> dict:
    """¿La variación del saldo de cada cuenta está EXPLICADA por sus movimientos?

    Lo que sobra es la diferencia sin explicar, y casi siempre es el banco
    registrando un movimiento con fecha de anteayer que impacta en el saldo de
    ayer. Ver `bancos.diferencias`.
    """
    return _svc.diferencias(email, _fecha(fecha))


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


# ── Bancos y movimientos MANUALES — lo que Interbanking no tiene ───────────── #
# Van bajo `/manual/*` para que el proxy de Next los deje pasar por una regla
# explícita y no por estar mezclados con otra cosa. Escriben en `bancos.cuentas`
# (con `origen='manual'`) y en `bancos.movimientos_manuales`; hacia el banco no
# sale nada.
@router.get("/manual/movimientos")
def listar_manuales(
    fecha: date | None = Query(None, description="día a listar"),
) -> list[dict]:
    """Los movimientos manuales del día, de todas las cuentas."""
    return _svc.listar_manuales(_fecha(fecha))


@router.post("/manual/cuentas")
def crear_cuenta_manual(
    banco: str = Body(..., embed=True),
    numero: str = Body(..., embed=True),
    tipo: str = Body("CC", embed=True),
    moneda: str = Body("ARS", embed=True),
    etiqueta: str = Body("", embed=True),
    email: str = Depends(get_user_email),
) -> dict:
    """Alta de una cuenta que Interbanking no informa. El banco se resuelve por
    NOMBRE: si ya existe, la cuenta queda agrupada abajo de él."""
    try:
        return _svc.crear_cuenta_manual(
            _exigir_escritura(email), banco, numero, tipo, moneda, etiqueta)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/manual/cuentas/{cuenta_id}")
def borrar_cuenta_manual(cuenta_id: int, email: str = Depends(get_user_email)) -> dict:
    try:
        if not _svc.borrar_cuenta_manual(_exigir_escritura(email), cuenta_id):
            raise HTTPException(404, "Esa cuenta no existe.")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@router.post("/manual/movimientos")
def crear_movimiento_manual(
    cuenta_id: int = Body(..., embed=True),
    descripcion: str = Body(..., embed=True),
    importe: float = Body(..., embed=True),
    tipo: str = Body(..., embed=True),
    fecha: date | None = Body(None, embed=True),
    email: str = Depends(get_user_email),
) -> dict:
    """Registra un movimiento que el banco no informa. **Impacta siempre el saldo
    al cierre** del día que se le cargue; sin `fecha`, el día que muestra la
    vista."""
    try:
        return _svc.crear_movimiento_manual(
            _exigir_escritura(email), cuenta_id, _fecha(fecha), descripcion, importe, tipo)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/manual/movimientos/{mov_id}")
def borrar_movimiento_manual(mov_id: int, email: str = Depends(get_user_email)) -> dict:
    if not _svc.borrar_movimiento_manual(_exigir_escritura(email), mov_id):
        raise HTTPException(404, "Ese movimiento no existe.")
    return {"ok": True}


# ── El DESGLOSE: qué columnas hay y qué texto cae en cada una ──────────────── #
# Esto lo edita el EQUIPO, no el que programa. Era una constante en Python hasta
# el 2026-08-18 y sumar la grafía que usa un banco nuevo costaba un commit y un
# deploy: el back office tenía que pedirlo y esperar. El desglose se deriva en la
# lectura, así que un cambio acá se ve en el próximo poll.
@router.post("/gastos/desglose")
def guardar_balde(
    etiqueta: str = Body(..., embed=True),
    grupo: str = Body("otros", embed=True),
    orden: int = Body(100, embed=True),
    # Sin `clave` es ALTA (se deriva de la etiqueta); con `clave` es edición.
    clave: str = Body("", embed=True),
    email: str = Depends(get_user_email),
) -> dict:
    try:
        return _svc.guardar_balde(_exigir_escritura(email), etiqueta, grupo, orden, clave)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/gastos/desglose/orden")
def reordenar_baldes(
    claves: list[str] = Body(..., embed=True),
    email: str = Depends(get_user_email),
) -> list[dict]:
    """Fija el orden de las columnas — lo único que decide los empates cuando dos
    se pisan. Se manda la lista COMPLETA, en el orden nuevo."""
    try:
        return _svc.reordenar_baldes(_exigir_escritura(email), claves)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/gastos/desglose/{clave}")
def borrar_balde(clave: str, email: str = Depends(get_user_email)) -> dict:
    """Baja de una columna. **Solo si no tiene textos cargados** — ver
    `bancos.borrar_balde`."""
    try:
        if not _svc.borrar_balde(_exigir_escritura(email), clave):
            raise HTTPException(404, "Ese balde no existe.")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@router.post("/gastos/desglose/matchers")
def agregar_matcher(
    balde: str = Body(..., embed=True),
    campo: str = Body(..., embed=True),
    operador: str = Body(..., embed=True),
    valor: str = Body(..., embed=True),
    email: str = Depends(get_user_email),
) -> dict:
    """Suma una GRAFÍA a un balde: el mismo impuesto escrito como lo escribe ESE
    banco. Es el 90% del uso del ABM."""
    try:
        return _svc.agregar_matcher(_exigir_escritura(email), balde, campo, operador, valor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/gastos/desglose/matchers/{matcher_id}")
def borrar_matcher(matcher_id: int, email: str = Depends(get_user_email)) -> dict:
    if not _svc.borrar_matcher(_exigir_escritura(email), matcher_id):
        raise HTTPException(404, "Ese matcher no existe.")
    return {"ok": True}


@router.put("/gastos/ignorar")
def ignorar_movimiento(
    mov_hash: str = Body(..., embed=True),
    ignorar: bool = Body(..., embed=True),
    motivo: str = Body("", embed=True),
    email: str = Depends(get_user_email),
) -> dict:
    """IGNORA (o des-ignora) UN movimiento: el equivalente al destildado por
    celda de Tesorería. Vive bajo `/gastos/` porque lo que deja de sumar son los
    GASTOS y su desglose — el extracto del banco no se toca.
    """
    try:
        return _svc.ignorar_movimiento(_exigir_escritura(email), mov_hash, ignorar, motivo)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
