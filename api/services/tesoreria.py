"""api/services/tesoreria.py — Back Office → Tesorería (ingresos/egresos del día).

Fuente: Aunesa `GET cuentas/consultaMovDocsSolicitados` ("Movimientos y Documentos
Solicitados") — movimientos BANCARIOS de dinero (transferencias, transferencia MEP,
e-cheq). La DIRECCIÓN la da el campo `solicitud` (Depósito = ingreso / Extracción =
egreso), NO el signo del `monto` (siempre positivo). Plata efectiva = `estado`
'Procesado' (default). Modelo verificado por discovery 2026-07-03.

Puro (sin FastAPI): lo llama `api/routers/back_office.py`. Se sirve LIVE contra Aunesa
sin persistir — volumen chico (~cientos de mov/día).

FOTO (2026-08-06): el día se congela en `operaciones.tesoreria_snapshots` — la grilla
BANCOS + el detalle de cada celda. UNA por fecha, TTL 30 fechas, la saca
`jobs/tesoreria_snapshot.py` al cierre (y el botón de la vista a demanda). Es lo que
sirve BANCOS cuando se elige una fecha pasada: el día viejo ya no se puede reconstruir
live (Aunesa cambia estados hacia atrás y lo cargado a mano se edita). MOVIMIENTOS es
SIEMPRE del día: el histórico se navega desde la celda que usa esos movimientos, así
la misma data no se guarda dos veces.

CUENTA OPERATIVA (2026-08-06): Aunesa la manda como objeto
`{id: '57461ARS', denominacion: 'BANCO MARIVA TERCEROS'}`. Se aplana a la
DENOMINACIÓN sola (el id no le dice nada a nadie) y pasa a ser el eje del
resumen: una card por banco, no una por moneda. El id igual lleva la moneda
adentro, así que agrupamos por (denominación, unidad).

SALDO INICIAL: lo único que Aunesa NO da. Se carga a mano por banco y por día en
`operaciones.tesoreria_saldos` — con eso la card cierra en saldo final
(inicial + ingresos − egresos). Escritura restringida a la allowlist
`operaciones.tesoreria_escritores` (+ admin), gestionada en Manager → MESA.
Si el back office NO cargó el saldo de un banco, el inicial vale **0** (no null):
el saldo final siempre es un número y la grilla cierra sola. `saldo_cargado`
distingue "cargado en cero" de "nunca lo tocaron" (el front lo muestra apagado).

CATÁLOGO DE BANCOS (2026-08-06): Aunesa no tiene endpoint de cuentas operativas, así
que el universo se descubre viendo movimientos y se persiste en
`operaciones.tesoreria_cuentas`. La grilla se arma con el CATÁLOGO COMPLETO (no con
quién operó hoy): los bancos sin movimientos aparecen en cero en vez de ir brotando
a medida que avanza la rueda. Sembrado hacia atrás con
`python -m scripts.diag_tesoreria_cuentas --registrar`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from api.cache import cached
from api.services._sql import _q
from core import aunesa
from core.postgres import get_pool

_log = logging.getLogger(__name__)

_ENDPOINT = "cuentas/consultaMovDocsSolicitados"
# La dirección la da `solicitud`. Comparamos SIN acentos y en minúscula porque el string
# de Aunesa puede venir en NFC o NFD (la 'ó'/'ó' se ven iguales pero != por bytes) — comparar
# el literal acentuado directo descartaba TODAS las filas (200 OK con 0 resultados).
_INGRESO = "deposito"   # 'Depósito'
_EGRESO = "extraccion"  # 'Extracción'
PRESENCIA_TTL_S = 90    # visto hace ≤90s = conectado (el front pollea cada ~20s)
# TTL del cache de la llamada a Aunesa. MENOR que el poll del front (20s) a propósito:
# cada poll trae datos frescos igual, y el cache solo evita repetir la MISMA llamada
# dentro de esa ventana (varios usuarios en la vista, o abrir el detalle de una celda).
AUNESA_TTL_S = 15

# Estados de Aunesa. Se piden TODOS de una (el selector ESTADO de la tab MOVIMIENTOS
# filtra después en Python) para que una sola llamada sirva a las dos tabs.
ESTADOS = ("Procesado", "Pendiente", "Pendiente de autorizar", "Demorado",
           "Rechazado", "Anulado", "Incompleto")
TODOS_ESTADOS = ";".join(ESTADOS)
# Único estado que representa plata que EFECTIVAMENTE se movió en la cuenta del banco.
# Es lo único que puede entrar a un saldo (ver `ingresos_egresos_dia`).
ESTADO_EFECTIVO = "Procesado"

# Placeholder para las filas que llegan SIN `cuentaOperativa`. Su plata tiene que verse
# (si no, el total del día no cierra), pero NO es un banco: nunca entra al catálogo, o
# quedaría como una columna fantasma en la grilla para siempre.
SIN_CUENTA = "SIN CUENTA OPERATIVA"

# RIEL (campo `tipoDocSoli`) viene '[TR] Transferencia', '[MP] Transferencia MEP',
# '[E CHEQ] E CHEQ'… Los e-cheq se separan del resto de los egresos: el back office
# necesita distinguirlos, así que van en su PROPIA fila de la grilla y NO entran al
# total de egresos. Igual sí restan del saldo final desde su fila propia.
_RE_RIEL_COD = re.compile(r"\[([^\]]+)\]")


def _norm(s: Any) -> str:
    """Minúscula + sin diacríticos, para comparar `solicitud` a prueba de NFC/NFD."""
    d = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in d if not unicodedata.combining(c)).strip().lower()


def es_echeq(riel: Any) -> bool:
    """True si el RIEL es un e-cheq. Matchea por el código entre corchetes,
    tolerante a 'E CHEQ' / 'ECHEQ' / 'E-CHEQ'."""
    m = _RE_RIEL_COD.search(str(riel or ""))
    return "cheq" in _norm(m.group(1) if m else riel).replace("-", " ")


def _hoy_art() -> datetime:
    """Ahora en ART (UTC-3), sin depender de la tz del server."""
    return datetime.now(UTC) - timedelta(hours=3)


def _dia(iso: str | None) -> date:
    """ISO YYYY-MM-DD → date; default = hoy ART."""
    return datetime.strptime(iso, "%Y-%m-%d").date() if iso else _hoy_art().date()


def _fechas(iso: str | None) -> tuple[str, str, str]:
    """(dd/mm/yyyy del día, dd/mm/yyyy del día+1, yyyymmdd del día) desde un ISO YYYY-MM-DD;
    default = hoy ART. El día+1 es para `liquidacionHasta`: Aunesa EXIGE desde < hasta
    (un rango de un solo día con desde==hasta tira 400), así que pedimos [día, día+1] y
    después filtramos las filas al día objetivo."""
    d = _dia(iso)
    return d.strftime("%d/%m/%Y"), (d + timedelta(days=1)).strftime("%d/%m/%Y"), d.strftime("%Y%m%d")


def _cuenta_operativa(v: Any) -> str:
    """El objeto `cuentaOperativa` de Aunesa → su denominación (el id no se muestra)."""
    if isinstance(v, dict):
        return str(v.get("denominacion") or v.get("id") or "").strip()
    return str(v or "").strip()


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _hora(id_: Any, yyyymmdd: str) -> str:
    """Hora HH:MM extraída del `id` (formato YYYYMMDDHHMMSS[ms]). '' si no matchea."""
    s = str(id_ or "")
    if len(s) >= 12 and s[:8] == yyyymmdd and s[8:12].isdigit():
        return f"{s[8:10]}:{s[10:12]}"
    return ""


@cached(ttl=AUNESA_TTL_S)
def traer_crudas(dia: date, estado: str) -> list[dict]:
    """Filas crudas de Aunesa del día `dia`, ya filtradas al día objetivo.

    Aunesa EXIGE desde < hasta (un rango de un día solo tira 400), así que se pide
    [día, día+1] y se descartan las filas del día siguiente.

    @cached(AUNESA_TTL_S): medido en el Droplet (2026-08-07), esta llamada HTTP son
    ~1.6s de los ~2.0s que tarda armar la vista — el 82%. Todo lo demás (16 queries)
    suma ~0.35s. El TTL es más corto que el poll del front (20s), así que la vista no
    se atrasa; lo que evita es pagar Aunesa DE NUEVO cuando, dentro de esa ventana,
    otro usuario pollea o alguien abre el detalle de `saldo_final` (que recalcula la
    grilla entera y volvía a pedir los mismos movimientos).
    """
    ddmmyyyy = dia.strftime("%d/%m/%Y")
    params: dict[str, Any] = {
        "liquidacionDesde": ddmmyyyy,
        "liquidacionHasta": (dia + timedelta(days=1)).strftime("%d/%m/%Y"),
    }
    if estado:
        params["estados"] = estado

    resp = aunesa.get(_ENDPOINT, params)
    if resp.status_code == 204:
        return []
    if resp.status_code != 200:
        raise RuntimeError(f"Aunesa {_ENDPOINT} [{resp.status_code}]: {resp.text[:300]}")
    body = resp.json()
    rows = body if isinstance(body, list) else []
    return [r for r in rows if str(r.get("fecha") or "").strip() == ddmmyyyy]


def aplanar(r: dict, yyyymmdd: str) -> dict:
    """Fila cruda de Aunesa → movimiento de la vista.

    TODOS los campos crudos (persona aplanada a `persona_*`) + derivados
    `_hora`/`_tipo`. Se devuelve todo para inspección directa en el front.
    """
    sol = _norm(r.get("solicitud"))
    mov = {k: v for k, v in r.items() if k != "persona"}
    for pk, pv in (r.get("persona") or {}).items():
        mov[f"persona_{pk}"] = pv
    mov["cuentaOperativa"] = _cuenta_operativa(r.get("cuentaOperativa")) or SIN_CUENTA
    mov["_hora"] = _hora(r.get("id"), yyyymmdd)
    mov["_tipo"] = "ingreso" if sol == _INGRESO else "egreso" if sol == _EGRESO else ""
    mov["_echeq"] = es_echeq(r.get("tipoDocSoli"))
    return mov


def _bucket() -> dict:
    """Acumulador de una celda de la grilla. `egresos_echeq` va SEPARADO: es una
    fila propia y no entra en `egresos`."""
    return {"ingresos": 0.0, "ingresos_echeq": 0.0, "egresos": 0.0, "egresos_echeq": 0.0,
            "mercados": 0.0, "fci": 0.0, "bb_mas": 0.0, "bb_menos": 0.0,
            "neto": 0.0, "n": 0}


def _ref_cheques_emitidos_vencidos(banco: str, unidad: str) -> str:
    # El literal sigue diciendo `emitidos_t1`: es la clave con la que ya están
    # guardados los destildados en `tesoreria_exclusiones`, cambiarlo los huerfanaría.
    return f"emitidos_t1|{banco}|{str(unidad or '').upper()}"


def _cheques_emitidos_vencidos_rows(dia: date) -> list[dict]:
    """Cheques EMITIDOS todavía abiertos que ya se pagan a ese día (`fecha_pago <= día`).

    El día MISMO entra: un cheque con fecha de pago de hoy se debita hoy, y dejarlo
    afuera hacía que el saldo del banco no lo reflejara hasta el día siguiente (era el
    caso de BANCO PATAGONIA COMÚN, 2026-08-10). Es además el mismo corte que usa el
    total "impacta hoy" del tablero EMITIDOS en el front.

    Se agregan por banco+moneda porque en BANCOS tienen que impactar como un solo monto
    y el modal de auditoría debe mostrar un único renglón ("cheques emitidos vencidos"),
    no el detalle cheque por cheque.
    """
    try:
        return _items_sql(
            f"SELECT banco, unidad, COUNT(*) AS cantidad, SUM(importe) AS total "
            f"FROM {_TABLA_CHEQUES} WHERE lado = 'emitido' AND estado = 'emitido' "
            "AND fecha_pago IS NOT NULL AND fecha_pago <= %(d)s "
            "GROUP BY banco, unidad ORDER BY banco, unidad",
            {"d": dia},
        )
    except Exception:
        _log.warning("tesoreria: no pude leer los cheques emitidos vencidos", exc_info=True)
        return []


def mercados_por_banco(dia: date) -> dict[tuple[str, str], dict]:
    """{(banco, unidad): {mercados, fci}} del día, desde la tab MERCADOS.

    Son DOS filas de la grilla BANCOS, ya netas y con signo listo para sumar:
      mercados = ingresos − pagos          (bloque MERCADO, parte de arriba)
      fci      = rescates − suscripciones  (bloque FCI, parte de abajo)
    Se agrega en SQL con un CASE por tipo: una sola pasada, sin traer las filas.
    """
    try:
        rows = _q(
            "SELECT banco, unidad, "
            "SUM(CASE WHEN tipo = 'ingreso' THEN importe "
            "         WHEN tipo = 'pago' THEN -importe ELSE 0 END) AS mercados, "
            "SUM(CASE WHEN tipo = 'rescate' THEN importe "
            "         WHEN tipo = 'suscripcion' THEN -importe ELSE 0 END) AS fci "
            f"FROM {_TABLA_MERCADOS} WHERE fecha = %(d)s "
            f"AND {_sql_no_excluido('mercado')} GROUP BY banco, unidad",
            {"d": dia},
        )
    except Exception:
        _log.warning("tesoreria: no pude leer los movimientos de Mercados", exc_info=True)
        return {}
    return {(r["banco"], r["unidad"]): {"mercados": float(r["mercados"] or 0),
                                        "fci": float(r["fci"] or 0)} for r in rows}


def ingresos_egresos_dia(*, fecha: str | None = None, estado: str = "Procesado",
                         email: str = "") -> dict:
    """Ingresos/egresos bancarios de un día (default hoy ART) desde Aunesa.

    Devuelve:
      - `resumen`: {unidad: {ingresos, egresos, neto, n}} por moneda (ARS/USD), sobre
        los movimientos del `estado` pedido (acompaña a la tabla de MOVIMIENTOS).
      - `cuentas`: TODAS las cuentas operativas del catálogo × moneda (las que no
        operaron ese día vienen en cero), con el saldo inicial cargado a mano (0 si
        nadie lo cargó) y el saldo final = inicial + ingresos − egresos.
      - `movimientos`: filas para la tabla (hora, cuenta, cliente, riel, unidad, tipo,
        monto, estado), ordenadas por hora desc.

    Los REGISTROS MANUALES (modal de la tab BANCOS) entran a `ingresos`/`egresos`
    según su sentido: son movimientos reales del banco, solo que cargados a mano en
    vez de venir de la API. El detalle de la celda los marca como manuales.

    Las dos filas e-cheq salen SEPARADAS de los totales para que el back office las
    distinga (es lo único que buscaba la separación), pero las dos entran al saldo:
      - `egresos_echeq`  = RIEL e-cheq de Aunesa + cheques EMITIDOS vencidos
                           (`fecha_pago <= día`), fuera del total de `egresos`.
      - `ingresos_echeq` = cheques recibidos finalizados (carga manual, tab CHEQUES).
        No vienen en los movimientos de Aunesa, así que sumarlos no duplica nada.

    MERCADOS y FCI vienen de la tab MERCADOS (carga manual) y entran ya NETOS:
      mercados = ingresos − pagos          |  fci = rescates − suscripciones

    BANCO A BANCO son transferencias INTERNAS: `bb_mas` (la cuenta recibió) y
    `bb_menos` (entregó). Suman cero entre todos los bancos — mueven el reparto,
    no el total.

    Saldo final = inicial + ingresos + ingresos_echeq − egresos − egresos_echeq
                  + mercados + fci + bb_mas − bb_menos.

    OJO — `cuentas` (tab BANCOS) NO respeta el filtro `estado` de la barra: un
    movimiento Rechazado / Anulado / Pendiente nunca movió plata en el banco, así que
    no puede entrar en un saldo. La grilla se calcula SIEMPRE sobre `ESTADO_EFECTIVO`.
    El filtro `estado` es de la tabla de MOVIMIENTOS (inspección), no del saldo.
    Por eso se le pide a Aunesa TODOS los estados de una y se separa acá: una sola
    llamada sirve a las dos tabs sin que una condicione a la otra.
    """
    dia = _dia(fecha)
    ddmmyyyy, _, yyyymmdd = _fechas(fecha)
    marcar_presencia(email)  # pollear la vista ES el heartbeat
    # Aunesa es una dependencia EXTERNA y se cae (2026-08-07/09: HTTP 500 en su
    # login, dos días). Si su error sube, muere el endpoint entero y el back office
    # pierde TODA la Tesorería — incluso los saldos, cheques, mercados, banco a banco,
    # registros manuales y VEPs, que viven en Postgres y están perfectamente
    # disponibles. La vista degrada: se arma con lo que hay y DICE qué falta, en vez
    # de devolver un 502 en el que no se distingue "Aunesa caído" de "la API rota".
    aunesa_error: str | None = None
    try:
        crudas = traer_crudas(dia, TODOS_ESTADOS)
    except Exception as e:
        aunesa_error = f"{type(e).__name__}: {e}"[:300]
        _log.warning("tesoreria: Aunesa no responde, sirvo la vista sin sus movimientos",
                     exc_info=True)
        crudas = []
    pedidos = {e.strip() for e in (estado or "").split(";") if e.strip()}
    # Movimientos destildados del saldo (y el default: sin hora no cuenta).
    exc = _exclusiones_dia(dia)

    resumen: dict[str, dict] = {}
    por_cuenta: dict[tuple[str, str], dict] = {}
    vistas: dict[tuple[str, str], str | None] = {}
    movimientos: list[dict] = []
    for r in crudas:
        mov = aplanar(r, yyyymmdd)
        est = str(r.get("estado") or "").strip()
        en_tabla = not pedidos or est in pedidos
        if en_tabla:
            movimientos.append(mov)
        if not mov["_tipo"]:
            continue
        co = r.get("cuentaOperativa")
        cta = mov["cuentaOperativa"]
        unidad = (r.get("unidad") or "?").upper()
        monto = _num(r.get("monto"))
        # El catálogo se alimenta con TODO lo visto: la cuenta operativa existe igual
        # aunque el movimiento que la delató haya terminado rechazado.
        vistas[(cta, unidad)] = str(co["id"]) if isinstance(co, dict) and co.get("id") else None
        destinos = []
        if en_tabla:
            destinos.append(resumen.setdefault(unidad, _bucket()))
        # Sin hora → destildado por default; un override explícito lo puede tildar.
        fuera, _ = _estado_excl(exc, "aunesa", r.get("id"),
                                default_excluido=not mov["_hora"])
        if est == ESTADO_EFECTIVO and not fuera:  # solo plata que se movió y cuenta
            destinos.append(por_cuenta.setdefault(
                (cta, unidad),
                {"cuenta_operativa": cta, "unidad": unidad, **_bucket()}))
        for d in destinos:
            if mov["_tipo"] == "ingreso":
                d["ingresos"] += monto
            elif mov["_echeq"]:
                d["egresos_echeq"] += monto  # fila aparte: NO entra al total de egresos
            else:
                d["egresos"] += monto
            d["neto"] = d["ingresos"] - d["egresos"]
            d["n"] += 1

    for b in resumen.values():
        for k in ("ingresos", "egresos", "egresos_echeq", "neto"):
            b[k] = round(b[k], 2)
    movimientos.sort(key=lambda m: str(m.get("_hora") or ""), reverse=True)

    # El panel de bancos es FIJO: sale del catálogo, no de quién operó hoy. Las cuentas
    # nuevas que aparezcan en el día se registran solas y quedan para siempre.
    registrar_cuentas(vistas, dia)
    saldos = _saldos_dia(dia)
    # Fila "Ingresos e-cheqs": los cheques RECIBIDOS que el equipo marcó finalizados
    # ese día (carga manual, tab CHEQUES). Fila propia, igual que los egresos e-cheq.
    ing_echeq = ingresos_echeq_dia(dia)
    emit_vencidos: dict[tuple[str, str], float] = {}
    for r in _cheques_emitidos_vencidos_rows(dia):
        banco, unidad = r["banco"], str(r["unidad"] or "").upper()
        ref = _ref_cheques_emitidos_vencidos(banco, unidad)
        fuera, _ = _estado_excl(exc, "cheque", ref)
        if not fuera:
            emit_vencidos[(banco, unidad)] = float(r["total"] or 0)
    # Filas MERCADOS y FCI: ya vienen netas y con signo (ver mercados_por_banco).
    mkt = mercados_por_banco(dia)
    # Banco a banco: una transferencia interna suma en un banco y resta en el otro.
    bb = banco_a_banco_por_banco(dia)
    # Registros manuales: FUENTE NUEVA de movimientos, no vienen de la API. Se
    # suman a Ingresos/Egresos según su sentido (el detalle los marca como manuales).
    reg = registros_por_banco(dia)
    # El ABM de bancos y el catálogo de claves salen de la MISMA lectura de
    # `tesoreria_cuentas`: `catalogo()` es un subconjunto de `listar_cuentas()`
    # (las activas, solo nombre+moneda), así que pedir las dos era ir dos veces a
    # la misma tabla en el request que el front pollea cada 20s.
    bancos = listar_cuentas()
    activas = {(b["cuenta_operativa"], b["unidad"]) for b in bancos if b["activa"]}
    cuentas = []
    for clave in sorted(activas | set(por_cuenta) | set(ing_echeq) | set(emit_vencidos)
                        | set(mkt) | set(bb) | set(reg)):
        cta, uni = clave
        c = por_cuenta.get(clave) or {"cuenta_operativa": cta, "unidad": uni, **_bucket()}
        s = saldos.get(clave)
        ini = s["saldo_inicial"] if s else None
        c["ingresos_echeq"] = ing_echeq.get(clave, 0.0)
        c["egresos_echeq"] += emit_vencidos.get(clave, 0.0)
        m = mkt.get(clave) or {}
        c["mercados"], c["fci"] = m.get("mercados", 0.0), m.get("fci", 0.0)
        t = bb.get(clave) or {}
        c["bb_mas"], c["bb_menos"] = t.get("bb_mas", 0.0), t.get("bb_menos", 0.0)
        if (rg := reg.get(clave)):
            c["ingresos"] += rg["ingresos"]
            c["egresos"] += rg["egresos"]
            c["neto"] = c["ingresos"] - c["egresos"]
            c["n"] += rg["n"]
        for k in ("ingresos", "ingresos_echeq", "egresos", "egresos_echeq",
                  "mercados", "fci", "bb_mas", "bb_menos", "neto"):
            c[k] = round(c[k], 2)
        # Sin carga manual el inicial es 0 (no null): así el saldo final siempre cierra
        # como número. `saldo_cargado` es lo que separa "cargado en 0" de "sin cargar".
        c["saldo_cargado"] = ini is not None
        c["saldo_inicial"] = round(ini if ini is not None else 0.0, 2)
        # Las dos filas e-cheq están SEPARADAS solo para que el back office las
        # distinga; las dos entran al saldo. `neto` ya es ingresos − egresos.
        c["saldo_final"] = round(
            c["saldo_inicial"] + c["neto"] + c["ingresos_echeq"] - c["egresos_echeq"]
            + c["mercados"] + c["fci"] + c["bb_mas"] - c["bb_menos"], 2)
        c["saldo_por"] = s["actualizado_por"] if s else None
        c["saldo_at"] = s["actualizado_at"] if s else None
        cuentas.append(c)

    return {"fecha": ddmmyyyy, "fecha_iso": dia.isoformat(), "estado": estado,
            "estado_bancos": ESTADO_EFECTIVO,  # el front lo aclara en la tab BANCOS
            "resumen": resumen, "cuentas": cuentas,
            # TOTAL del panel RESCATE ACA VALORES por moneda: la barra lo muestra al
            # lado de SACAR FOTO para no tener que abrir el modal para verlo.
            "rescate": totales_rescate(dia),
            "puede_editar_saldo": puede_editar_saldo(email),
            "catalogo": bancos,             # ABM de bancos (nombre + número de cuenta)
            "conectados": conectados(), "actualizado_at": datetime.now(UTC).isoformat(),
            # La vista tiene que poder DECIR que le falta media fuente. Sin esto, una
            # caída de Aunesa se ve igual que un día sin movimientos: todo en cero y
            # nadie se entera de que los saldos están incompletos.
            "aunesa_ok": aunesa_error is None, "aunesa_error": aunesa_error,
            "movimientos": movimientos, "n": len(movimientos), "raw": len(movimientos)}


# ──────────────────────────────────────────────────────────────────────────────
# DETALLE DE CELDA — "¿de dónde sale este número?"
#
# Se calcula SERVER-SIDE, con las MISMAS fuentes y filtros que la grilla, para que
# el detalle no pueda contradecir al total: si el modal y la celda no coinciden es
# un bug, no una diferencia de criterio. El front no recalcula nada.
# ──────────────────────────────────────────────────────────────────────────────

# ── EXCLUSIONES: destildar un movimiento para que NO cuente en el saldo ───────
#
# Solo se persisten los OVERRIDES. El default de cada movimiento es contar, con UNA
# excepción: los de Aunesa SIN HORA arrancan DESTILDADOS. El `id` de esos no trae
# fecha-hora, así que no hay forma de distinguirlos de un duplicado — se prefiere no
# contarlos y que alguien los tilde a mano si corresponde.
_TABLA_EXCL = "operaciones.tesoreria_exclusiones"
FUENTES_EXCL = ("aunesa", "cheque", "mercado", "bb", "registro")
OBS_SIN_HORA = "por defecto deseleccionado por duplicidad (movimiento sin hora)"


def _exclusiones_dia(dia: date) -> dict[tuple[str, str], dict]:
    """{(fuente, ref): {excluido, observacion}} — los overrides cargados ese día."""
    try:
        rows = _q(f"SELECT fuente, ref, excluido, observacion FROM {_TABLA_EXCL} "
                  "WHERE fecha = %(d)s", {"d": dia})
    except Exception:
        _log.warning("tesoreria: no pude leer las exclusiones", exc_info=True)
        return {}
    return {(r["fuente"], r["ref"]): {"excluido": r["excluido"],
                                      "observacion": r["observacion"]} for r in rows}


def _estado_excl(exc: dict, fuente: str, ref: str, *, default_excluido: bool = False,
                 obs_default: str = "") -> tuple[bool, str]:
    """(excluido, observación) de un movimiento: el override si existe, si no el default."""
    o = exc.get((fuente, str(ref)))
    if o is not None:
        return bool(o["excluido"]), (o["observacion"] or "")
    return default_excluido, (obs_default if default_excluido else "")


def _sql_no_excluido(fuente: str, col_id: str = "id") -> str:
    """Fragmento WHERE que deja afuera lo destildado. Se inyecta en cada agregación
    para que la grilla y el detalle no puedan divergir."""
    return (f"NOT EXISTS (SELECT 1 FROM {_TABLA_EXCL} e WHERE e.fecha = %(d)s "
            f"AND e.fuente = '{fuente}' AND e.ref = {col_id}::text AND e.excluido)")


def set_exclusion(*, fecha: str | None, fuente: str, ref: str, excluido: bool,
                  actor: str) -> dict:
    """Tilda/destilda un movimiento del saldo, dejando la traza en `observacion`."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para excluir movimientos del saldo")
    if fuente not in FUENTES_EXCL:
        raise ValueError(f"fuente inválida: {fuente} (válidas: {', '.join(FUENTES_EXCL)})")
    ref = str(ref or "").strip()
    if not ref:
        raise ValueError("falta 'ref' (el identificador del movimiento)")
    dia = _dia(fecha)
    quien = (actor or "").lower() or "—"
    hora = _hoy_art().strftime("%H:%M")
    obs = (f"anulado por {quien} a las {hora}" if excluido
           else f"reincorporado por {quien} a las {hora}")
    _exec(
        f"INSERT INTO {_TABLA_EXCL} (fecha, fuente, ref, excluido, observacion, actor, "
        "actualizado_at) VALUES (%(d)s, %(f)s, %(r)s, %(e)s, %(o)s, %(a)s, %(at)s) "
        "ON CONFLICT (fecha, fuente, ref) DO UPDATE SET excluido = EXCLUDED.excluido, "
        "observacion = EXCLUDED.observacion, actor = EXCLUDED.actor, "
        "actualizado_at = EXCLUDED.actualizado_at",
        {"d": dia, "f": fuente, "r": ref, "e": bool(excluido), "o": obs,
         "a": quien, "at": datetime.now(UTC)},
    )
    _audit(actor, "excluir_movimiento" if excluido else "reincorporar_movimiento",
           f"{dia.isoformat()}|{fuente}|{ref}")
    return {"fuente": fuente, "ref": ref, "excluido": bool(excluido), "observacion": obs}


# Cada fila de la grilla → de dónde sale y con qué signo entra al saldo final.
FILAS_DETALLE = ("saldo_inicial", "ingresos", "ingresos_echeq", "egresos",
                 "egresos_echeq", "mercados", "fci", "bb_mas", "bb_menos", "saldo_final")
_SIGNO_FILA = {"saldo_inicial": 1, "ingresos": 1, "ingresos_echeq": 1, "egresos": -1,
               "egresos_echeq": -1, "mercados": 1, "fci": 1, "bb_mas": 1, "bb_menos": -1}

_FUENTE_FILA = {
    "saldo_inicial": "Carga manual del back office (tesoreria_saldos)",
    "ingresos": f"Movimientos de Aunesa (estado {ESTADO_EFECTIVO}) + REGISTROS MANUALES",
    "egresos": f"Movimientos de Aunesa (estado {ESTADO_EFECTIVO}) + REGISTROS MANUALES",
    "egresos_echeq": (f"Movimientos de Aunesa · RIEL e-cheq (estado {ESTADO_EFECTIVO}) + "
                      "cheques EMITIDOS con fecha de pago vencida o del día"),
    "ingresos_echeq": "Cheques RECIBIDOS finalizados (tab CHEQUES)",
    # Estas tres cuentan también lo `pendiente` (decisión del back office), así que el
    # detalle muestra el estado REAL de cada fila: la columna ESTADO no lo disimula.
    "mercados": "Tab MERCADOS · ingreso (+) y pago (−) — incluye pendientes",
    "fci": "Tab MERCADOS · rescate (+) y suscripcion (−) — incluye pendientes",
    "bb_mas": "Tab BANCO A BANCO · recibido — incluye pendientes",
    "bb_menos": "Tab BANCO A BANCO · enviado — incluye pendientes",
    "saldo_final": "Suma de las filas de la grilla (ya con su signo)",
}


def _items_sql(sql: str, params: dict) -> list[dict]:
    try:
        return [dict(r) for r in _q(sql, params)]
    except Exception:
        _log.warning("tesoreria: no pude leer el detalle de la celda", exc_info=True)
        return []


def clave_celda(banco: str, unidad: str, fila: str) -> str:
    """Identificador de una celda dentro del mapa de detalle (y de la FOTO del día)."""
    return f"{banco}|{str(unidad or '').upper()}|{fila}"


def _detalle_dia(dia: date, cuentas: list[dict] | None = None) -> dict[str, dict]:
    """Detalle de TODAS las celdas de la grilla del día, en UNA sola pasada.

    Una llamada a Aunesa + una query por fuente, en vez de repetir ese trabajo celda
    por celda. Lo usan el modal de auditoría (que pide una) y la FOTO del día (que
    guarda todas), así que los dos ven exactamente lo mismo — no pueden divergir.

    Aplica EXACTAMENTE los mismos filtros que `ingresos_egresos_dia` (estado
    Procesado, corte e-cheq del RIEL, exclusiones) para que el detalle nunca pueda
    contradecir al total de la celda.

    `cuentas` = filas ya calculadas de la grilla. Si vienen, se agrega la celda
    `saldo_final` (la ecuación fila por fila); si no, se omite y no se recalcula nada.
    """
    exc = _exclusiones_dia(dia)
    celdas: dict[tuple[str, str, str], list[dict]] = defaultdict(list)

    def _push(banco, unidad, fila, fuente_, ref, detalle, referencia, estado, importe,
              *, default_excluido: bool = False, obs_default: str = "") -> None:
        """Item del detalle con su tilde y su observación (traza de quién lo anuló)."""
        fuera, obs = _estado_excl(exc, fuente_, ref, default_excluido=default_excluido,
                                  obs_default=obs_default)
        celdas[(banco, str(unidad or "?").upper(), fila)].append(
            {"fuente": fuente_, "ref": str(ref), "detalle": detalle,
             "referencia": referencia, "estado": estado, "importe": importe,
             "excluido": fuera, "observacion": obs})

    # 1) Aunesa → ingresos / egresos / egresos_echeq (los e-cheq solo del lado egreso).
    yyyymmdd = dia.strftime("%Y%m%d")
    try:
        # Se piden TODOS los estados y se filtra ESTADO_EFECTIVO acá, igual que hace la
        # grilla. No es un capricho: pedirle a Aunesa el subconjunto era otra llamada
        # HTTP de ~1.6s con distinta cache key, así que abrir el detalle de una celda
        # volvía a pagar Aunesa aunque el poll acabara de traer esos mismos
        # movimientos. Con el mismo pedido que la grilla, el detalle sale del cache.
        crudas = traer_crudas(dia, TODOS_ESTADOS)
    except Exception:
        _log.warning("tesoreria: no pude traer los movimientos de Aunesa", exc_info=True)
        crudas = []
    for r in crudas:
        if str(r.get("estado") or "").strip() != ESTADO_EFECTIVO:
            continue
        m = aplanar(r, yyyymmdd)
        if not m["_tipo"]:
            continue
        fila = ("ingresos" if m["_tipo"] == "ingreso"
                else "egresos_echeq" if m["_echeq"] else "egresos")
        # Sin hora → destildado por default: el `id` no trae fecha-hora, así que no
        # se puede distinguir de un duplicado. Se tilda a mano si corresponde.
        _push(m["cuentaOperativa"], r.get("unidad"), fila, "aunesa", r.get("id") or "",
              m.get("persona_nombreCompleto") or r.get("cuenta") or "—",
              f"{m.get('_hora') or 'sin hora'} · {r.get('tipoDocSoli') or ''}".strip(" ·"),
              r.get("estado"), _num(r.get("monto")),
              default_excluido=not m["_hora"], obs_default=OBS_SIN_HORA)

    # 2) Registros manuales: entran a la MISMA fila que Aunesa, pero se marcan — es lo
    #    único que no viene de la API y tiene que verse de un vistazo. Los dos grupos
    #    (rescate / otros) impactan igual el banco; el detalle dice de cuál viene.
    for r in _items_sql(
            f"SELECT id, tipo, importe, creado_por, banco, unidad, sentido, "
            f"{_SQL_GRUPO} AS grupo "
            f"FROM {_TABLA_REGISTROS} WHERE fecha = %(d)s ORDER BY id", {"d": dia}):
        # ESTADO va vacío a propósito: un registro manual NO tiene estado, y poner
        # "manual" ahí sería inventar uno. Que es manual ya lo dice el detalle.
        _push(r["banco"], r["unidad"],
              "ingresos" if r["sentido"] == "ingreso" else "egresos",
              "registro", r["id"],
              f"registro manual{'' if r['grupo'] == 'rescate' else ' (otros)'} · {r['tipo']}",
              f"cargado por {r['creado_por'] or '—'}", None, float(r["importe"] or 0))

    # 3) Cheques recibidos finalizados → fila ingresos_echeq.
    for r in _items_sql(
            f"SELECT id, comitente, comitente_denominacion, tipo, estado, importe, "
            f"banco, unidad FROM {_TABLA_CHEQUES} WHERE lado = 'recibido' "
            f"AND estado = 'finalizado' "
            f"AND (creado_at AT TIME ZONE '{_TZ_ART}')::date = %(d)s ORDER BY id",
            {"d": dia}):
        _push(r["banco"], r["unidad"], "ingresos_echeq", "cheque", r["id"],
              r["comitente_denominacion"] or r["comitente"] or "—",
              f"cheque {r['tipo'] or ''}".strip(), r["estado"], float(r["importe"] or 0))

    # 3b) Cheques emitidos vencidos → misma fila `egresos_echeq`, pero compactados en UNA
    #     sola línea por banco+moneda para que el modal no explote cheque por cheque.
    for r in _cheques_emitidos_vencidos_rows(dia):
        cantidad = int(r["cantidad"] or 0)
        banco, unidad = r["banco"], str(r["unidad"] or "").upper()
        _push(
            banco,
            unidad,
            "egresos_echeq",
            "cheque",
            _ref_cheques_emitidos_vencidos(banco, unidad),
            "cheques emitidos vencidos",
            f"{cantidad} cheque{'s' if cantidad != 1 else ''} · fecha de pago vencida o del día",
            "emitido",
            float(r["total"] or 0),
        )

    # 4) Mercados / FCI: el signo lo define el tipo (el segundo de cada par resta).
    _resta = {"pago", "suscripcion"}
    for r in _items_sql(
            f"SELECT id, entidad, tipo, estado, importe, banco, unidad "
            f"FROM {_TABLA_MERCADOS} WHERE fecha = %(d)s ORDER BY id", {"d": dia}):
        fila = "mercados" if r["tipo"] in ("ingreso", "pago") else "fci"
        _push(r["banco"], r["unidad"], fila, "mercado", r["id"], r["entidad"] or "—",
              r["tipo"], r["estado"],
              float(r["importe"] or 0) * (-1 if r["tipo"] in _resta else 1))

    # 5) Banco a banco: la misma transferencia suma en la cuenta de crédito y resta
    #    en la de débito → una fila del detalle en cada banco.
    for r in _items_sql(
            f"SELECT id, cta_debito, cta_credito, estado, importe, unidad "
            f"FROM {_TABLA_BB} WHERE fecha = %(d)s ORDER BY id", {"d": dia}):
        imp = float(r["importe"] or 0)
        _push(r["cta_credito"], r["unidad"], "bb_mas", "bb", r["id"],
              f"recibido de {r['cta_debito']}", "transferencia interna", r["estado"], imp)
        _push(r["cta_debito"], r["unidad"], "bb_menos", "bb", r["id"],
              f"enviado a {r['cta_credito']}", "transferencia interna", r["estado"], imp)

    # 6) Saldo inicial: la carga manual del back office.
    for r in _items_sql(
            "SELECT cuenta_operativa, unidad, saldo_inicial, actualizado_por, "
            "actualizado_at FROM operaciones.tesoreria_saldos WHERE fecha = %(d)s",
            {"d": dia}):
        _push(r["cuenta_operativa"], r["unidad"], "saldo_inicial", "saldo", "",
              f"Saldo inicial cargado por {r['actualizado_por'] or '—'}",
              r["actualizado_at"].isoformat() if r["actualizado_at"] else "", None,
              float(r["saldo_inicial"] or 0))

    out: dict[str, dict] = {}
    for (b, u, f), items in celdas.items():
        out[clave_celda(b, u, f)] = {
            "fila": f, "banco": b, "unidad": u, "fecha": dia.strftime("%d/%m/%Y"),
            "fuente": _FUENTE_FILA[f],
            # El TOTAL no cuenta lo destildado: tiene que dar exactamente lo de la celda.
            "total": round(sum(i["importe"] for i in items if not i.get("excluido")), 2),
            "excluidos": sum(1 for i in items if i.get("excluido")),
            "items": items,
        }

    # 7) Saldo final: no tiene operaciones propias, es la ECUACIÓN. Se devuelve el
    #    desglose fila por fila para auditar de dónde sale el número final.
    for c in cuentas or []:
        items = [{"fuente": "fila", "ref": k, "excluido": False, "observacion": "",
                  "detalle": k, "referencia": "fila de la grilla", "estado": None,
                  "importe": _SIGNO_FILA[k] * float(c.get(k) or 0)}
                 for k in FILAS_DETALLE if k != "saldo_final"]
        out[clave_celda(c["cuenta_operativa"], c["unidad"], "saldo_final")] = {
            "fila": "saldo_final", "banco": c["cuenta_operativa"], "unidad": c["unidad"],
            "fecha": dia.strftime("%d/%m/%Y"), "fuente": _FUENTE_FILA["saldo_final"],
            "total": round(sum(i["importe"] for i in items), 2),
            "excluidos": 0, "items": items,
        }
    return out


def detalle_celda(*, fecha: str | None, banco: str, unidad: str, fila: str,
                  email: str = "") -> dict:
    """Las operaciones individuales detrás de una celda de la grilla BANCOS."""
    if fila not in FILAS_DETALLE:
        raise ValueError(f"fila inválida: {fila} (válidas: {', '.join(FILAS_DETALLE)})")
    banco, unidad = (banco or "").strip(), (unidad or "").strip().upper()
    if not banco or not unidad:
        raise ValueError("faltan 'banco' y/o 'unidad'")
    dia = _dia(fecha)
    # La grilla solo se recalcula si se pide el saldo final (es la única celda que la
    # necesita); el resto sale de las fuentes directamente.
    cuentas = (ingresos_egresos_dia(fecha=fecha, email=email)["cuentas"]
               if fila == "saldo_final" else None)
    celda = _detalle_dia(dia, cuentas).get(clave_celda(banco, unidad, fila))
    # Celda en cero: existe en la grilla pero no tiene operaciones detrás.
    return celda or {"fila": fila, "banco": banco, "unidad": unidad,
                     "fecha": dia.strftime("%d/%m/%Y"), "fuente": _FUENTE_FILA[fila],
                     "total": 0.0, "excluidos": 0, "items": []}


# ──────────────────────────────────────────────────────────────────────────────
# FOTO de la grilla BANCOS — congela el día para poder auditarlo después.
#
# Por qué existe: la vista del día es casi toda LIVE contra Aunesa y no se
# persiste. Pasado el día no hay forma de reconstruir lo que mostró la pantalla
# (Aunesa puede cambiar estados hacia atrás, y lo cargado a mano se puede editar).
# La foto lo deja congelado.
#
# QUÉ GUARDA: las filas de la grilla BANCOS del día (todos los bancos del catálogo ×
# moneda) y, por cada CELDA, los movimientos que la componen — o sea, exactamente lo
# que muestra el modal de auditoría. Con eso el histórico se navega desde BANCOS:
# elegís la fecha, ves la grilla de ese día y clickeás una celda para ver sus
# movimientos.
#
# SIN DUPLICAR: la tab MOVIMIENTOS es SIEMPRE del día (live) y no se guarda aparte —
# los movimientos históricos ya viven acá, colgados de la celda que los usa. Guardar
# también la lista plana sería la misma data dos veces.
#
# UNA POR DÍA + TTL: `fecha` es única (re-sacarla PISA la del día, no acumula) y solo
# se conservan las últimas `TTL_SNAPSHOTS` fechas. `hash_sha256` del payload permite
# DETECTAR una alteración hecha por fuera de la API.
# ──────────────────────────────────────────────────────────────────────────────

_TABLA_SNAPSHOTS = "operaciones.tesoreria_snapshots"
TTL_SNAPSHOTS = 30  # fotos que se conservan (una por día) — el resto se borra solo


def _payload_dia(dia: date) -> dict:
    """La grilla BANCOS del día + el detalle de cada celda.

    Se reusan los mismos services que sirven la vista (no se re-consulta a mano)
    para que la foto sea exactamente lo que vio el equipo, incluidas las
    exclusiones y los defaults.
    """
    iso = dia.isoformat()
    # `email=""` a propósito: la foto no marca presencia ni depende de quién la mira.
    vista = ingresos_egresos_dia(fecha=iso, estado=ESTADO_EFECTIVO, email="")
    bancos = vista.get("cuentas", [])
    try:
        detalle = _detalle_dia(dia, bancos)
    except Exception as exc:
        # Si el detalle falla, la foto se toma igual con la grilla y deja constancia
        # del hueco, en vez de perderse entera.
        _log.warning("foto tesorería: falló el detalle de las celdas", exc_info=True)
        detalle = {"_error": f"{type(exc).__name__}: {exc}"}
    return {
        "bancos": bancos,
        "detalle": detalle,
        "estado_bancos": vista.get("estado_bancos"),
        "catalogo_bancos": listar_cuentas(),
        "exclusiones": [{"fuente": f, "ref": r, **v}
                        for (f, r), v in _exclusiones_dia(dia).items()],
    }


def tomar_snapshot(*, fecha: str | None = None, actor: str = "", origen: str = "manual",
                   ) -> dict:
    """Congela la grilla BANCOS de un día. Devuelve el resumen (sin el payload)."""
    if origen not in ("manual", "cron"):
        raise ValueError(f"origen inválido: {origen}")
    if origen == "manual" and not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para sacar la foto de Tesorería")
    dia = _dia(fecha)
    datos = _payload_dia(dia)
    # Hash del payload canónico (claves ordenadas): dos fotos iguales dan el mismo
    # hash, y editar la fila a mano lo rompe.
    crudo = json.dumps(datos, sort_keys=True, default=str, ensure_ascii=False)
    h = hashlib.sha256(crudo.encode("utf-8")).hexdigest()
    ahora = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        # Una foto por día: volver a sacarla actualiza la del día (la traza de cada
        # toma queda en `tesoreria_audit`, así que no se pierde el historial de quién).
        cur.execute(
            f"INSERT INTO {_TABLA_SNAPSHOTS} (fecha, tomado_at, tomado_por, origen, "
            "hash_sha256, datos) VALUES (%(f)s, %(t)s, %(p)s, %(o)s, %(h)s, %(d)s) "
            "ON CONFLICT (fecha) DO UPDATE SET tomado_at = EXCLUDED.tomado_at, "
            "tomado_por = EXCLUDED.tomado_por, origen = EXCLUDED.origen, "
            "hash_sha256 = EXCLUDED.hash_sha256, datos = EXCLUDED.datos "
            "RETURNING id",
            {"f": dia, "t": ahora, "p": (actor or origen).lower() or origen,
             "o": origen, "h": h, "d": Jsonb(datos)})
        nuevo = cur.fetchone()[0]
        # TTL: solo las últimas N fechas. Se limpia acá (no con un cron aparte) para
        # que la tabla no pueda crecer aunque el job de limpieza no exista.
        cur.execute(
            f"DELETE FROM {_TABLA_SNAPSHOTS} WHERE fecha NOT IN "
            f"(SELECT fecha FROM {_TABLA_SNAPSHOTS} ORDER BY fecha DESC LIMIT %(n)s)",
            {"n": TTL_SNAPSHOTS})
        purgadas = cur.rowcount
        conn.commit()
    _audit(actor or origen, "snapshot_tesoreria", str(nuevo),
           {"fecha": dia.isoformat(), "origen": origen, "hash": h})
    return {"id": int(nuevo), "fecha": dia.isoformat(),
            "tomado_at": ahora.isoformat(), "origen": origen, "hash_sha256": h,
            "bytes": len(crudo), "n_bancos": len(datos.get("bancos") or []),
            "n_celdas": len(datos.get("detalle") or {}), "purgadas": purgadas}


def listar_snapshots(*, desde: str | None = None, hasta: str | None = None,
                     limit: int = 200) -> dict:
    """Fotos guardadas, SIN el payload (que puede pesar cientos de KB)."""
    rows = _q(
        "SELECT id, fecha, tomado_at, tomado_por, origen, hash_sha256, "
        "jsonb_array_length(COALESCE(datos->'bancos', '[]'::jsonb)) AS n_bancos "
        f"FROM {_TABLA_SNAPSHOTS} "
        "WHERE (%(d)s = '' OR fecha >= %(d)s::date) "
        "AND (%(h)s = '' OR fecha <= %(h)s::date) "
        "ORDER BY fecha DESC LIMIT %(lim)s",
        {"d": desde or "", "h": hasta or "", "lim": max(1, min(int(limit), 1000))},
    )
    return {"ttl": TTL_SNAPSHOTS, "snapshots": [{
        "id": int(r["id"]), "fecha": r["fecha"].isoformat(),
        "tomado_at": r["tomado_at"].isoformat(), "tomado_por": r["tomado_por"],
        "origen": r["origen"], "hash_sha256": r["hash_sha256"],
        "n_bancos": r["n_bancos"],
    } for r in rows]}


def foto_dia(fecha: str | None = None, *, email: str = "") -> dict:
    """La foto de un día — es lo que sirve BANCOS cuando se elige una fecha pasada.

    Mismo shape que la vista live (`cuentas` + `catalogo`) para que el front la
    renderice con la misma grilla, más el `detalle` de cada celda ya congelado (el
    modal de auditoría de un día viejo NO vuelve a pegarle a Aunesa: lee de acá).
    """
    dia = _dia(fecha)
    rows = _q("SELECT id, fecha, tomado_at, tomado_por, origen, hash_sha256, datos "
              f"FROM {_TABLA_SNAPSHOTS} WHERE fecha = %(d)s", {"d": dia})
    base = {"fecha": dia.strftime("%d/%m/%Y"), "fecha_iso": dia.isoformat(),
            "puede_editar_saldo": puede_editar_saldo(email)}
    if not rows:
        # Sin foto de ese día no hay nada que mostrar: la vista live ya no lo puede
        # reconstruir. El front lo dice explícito en vez de mostrar ceros.
        return {**base, "existe": False, "cuentas": [], "catalogo": [], "detalle": {},
                "ttl": TTL_SNAPSHOTS}
    r = rows[0]
    datos = r["datos"] or {}
    crudo = json.dumps(datos, sort_keys=True, default=str, ensure_ascii=False)
    return {
        **base, "existe": True, "id": int(r["id"]),
        "tomado_at": r["tomado_at"].isoformat(), "tomado_por": r["tomado_por"],
        "origen": r["origen"], "hash_sha256": r["hash_sha256"],
        # False = alguien tocó la fila por fuera de la API.
        "hash_ok": hashlib.sha256(crudo.encode("utf-8")).hexdigest() == r["hash_sha256"],
        "estado_bancos": datos.get("estado_bancos") or ESTADO_EFECTIVO,
        "cuentas": datos.get("bancos") or [],
        "catalogo": datos.get("catalogo_bancos") or [],
        "detalle": datos.get("detalle") or {},
        "ttl": TTL_SNAPSHOTS,
    }


# ──────────────────────────────────────────────────────────────────────────────
# TAB CHEQUES — RECIBIDOS (izquierda) | EMITIDOS (derecha). Los DOS lados se
# cargan A MANO: acá no aparece nada automático, todo lo registra el back office.
#
# NO es un listado del día: es un TABLERO DE SEGUIMIENTO. La lista NO se filtra
# por fecha — un cheque de hace un año que nunca se cerró tiene que seguir a la
# vista, y uno con fecha de pago futura también (ese va pintado). Lo único que
# saca una fila de la vista es cerrarla: 'completado' (emitidos) / 'finalizado'
# (recibidos). La fila NO se borra: queda en la tabla para auditoría.
#
# Los recibidos FINALIZADOS alimentan la fila "Ingresos e-cheqs" de la grilla
# BANCOS, imputados al día en que se los marcó (`cerrado_at`).
# ──────────────────────────────────────────────────────────────────────────────

_TABLA_CHEQUES = "operaciones.tesoreria_cheques"
LADOS = ("emitido", "recibido")
# Estados por lado. El ÚLTIMO de cada tupla es el que cierra la fila y la saca
# de la vista (ver `ESTADO_CIERRE`).
ESTADOS_CHEQUE: dict[str, tuple[str, ...]] = {
    "emitido": ("pendiente", "emitido", "completado"),
    "recibido": ("pendiente", "finalizado"),
}
ESTADO_CIERRE = {lado: est[-1] for lado, est in ESTADOS_CHEQUE.items()}
TIPOS_RECIBIDO = ("echeq", "fisico")

_CAMPOS_CHEQUE = ("lado", "tipo", "comitente", "comitente_denominacion", "cuit",
                  "banco", "unidad", "importe", "estado", "fecha_pago", "cerrado_at")
_COLS_CHEQUE = ("id, lado, tipo, comitente, comitente_denominacion, cuit, banco, unidad, "
                "importe, estado, fecha_pago, cerrado_at, creado_por, creado_at")
# ART: `cerrado_at` es timestamptz, y el día de la grilla es día ARGENTINO.
_TZ_ART = "America/Argentina/Buenos_Aires"


def _fila_cheque(r: dict) -> dict:
    return {
        "id": int(r["id"]),
        "lado": r["lado"],
        "tipo": r["tipo"],
        "comitente": r["comitente"],
        "comitente_denominacion": r["comitente_denominacion"],
        "cuit": r["cuit"],
        "banco": r["banco"],
        "unidad": r["unidad"],
        "importe": float(r["importe"] or 0),
        "estado": r["estado"],
        "fecha_pago": r["fecha_pago"].isoformat() if r["fecha_pago"] else None,
        "cerrado_at": r["cerrado_at"].isoformat() if r["cerrado_at"] else None,
        "creado_por": r["creado_por"],
        "creado_at": r["creado_at"].isoformat() if r["creado_at"] else None,
    }


def cheques(*, fecha: str | None = None, incluir_cerrados: bool = False,
            email: str = "") -> dict:
    """Las dos mitades de la tab CHEQUES. Cada lado tiene su propio horizonte:

    EMITIDOS  — TABLERO DE SEGUIMIENTO, SIN filtro de fecha: un cheque de hace un año
                sin cerrar sigue a la vista, y uno con pago futuro también. Solo las
                filas abiertas ('completado' las saca); `incluir_cerrados=True` las
                trae igual para auditoría.
    RECIBIDOS — son TODOS DEL DÍA: se registran intradía y no se arrastran. Se filtran
                por el día de carga (`creado_at` en hora ARG) y se muestran los dos
                estados — los finalizados son los que alimentan BANCOS y hay que
                poder verlos.
    """
    marcar_presencia(email)
    dia = _dia(fecha)
    emitidos = _q(
        f"SELECT {_COLS_CHEQUE} FROM {_TABLA_CHEQUES} WHERE lado = 'emitido' "
        "AND (%(todos)s OR estado <> %(cierre)s) "
        "ORDER BY fecha_pago DESC NULLS LAST, id DESC LIMIT 5000",
        {"todos": bool(incluir_cerrados), "cierre": ESTADO_CIERRE["emitido"]},
    )
    recibidos = _q(
        f"SELECT {_COLS_CHEQUE} FROM {_TABLA_CHEQUES} WHERE lado = 'recibido' "
        f"AND (creado_at AT TIME ZONE '{_TZ_ART}')::date = %(d)s ORDER BY id DESC",
        {"d": dia},
    )
    return {
        "fecha_iso": dia.isoformat(), "fecha": dia.strftime("%d/%m/%Y"),
        "emitidos": [_fila_cheque(r) for r in emitidos],
        "recibidos": [_fila_cheque(r) for r in recibidos],
        "bancos": [{"banco": c, "unidad": u} for c, u in catalogo()],
        "estados": {k: list(v) for k, v in ESTADOS_CHEQUE.items()},
        "estado_cierre": ESTADO_CIERRE,
        "tipos": list(TIPOS_RECIBIDO),
        "hoy": _hoy_art().date().isoformat(),   # el front pinta fecha_pago > hoy
        "puede_editar": puede_editar_saldo(email),
        "conectados": conectados(), "actualizado_at": datetime.now(UTC).isoformat(),
    }


def ingresos_echeq_dia(dia: date) -> dict[tuple[str, str], float]:
    """{(banco, unidad): importe} de los RECIBIDOS finalizados ese día.

    Alimenta la fila "Ingresos e-cheqs" de BANCOS, que **SÍ suma al saldo final**:
    esta plata NO viene en los movimientos de Aunesa, así que no hay doble conteo.
    Se imputa por el día de CARGA (`creado_at`) — el mismo con el que la tab CHEQUES
    lista los recibidos, así lo que se ve en una pantalla es lo que suma en la otra.
    """
    try:
        rows = _q(
            f"SELECT banco, unidad, SUM(importe) AS total FROM {_TABLA_CHEQUES} "
            "WHERE lado = 'recibido' AND estado = 'finalizado' "
            f"AND (creado_at AT TIME ZONE '{_TZ_ART}')::date = %(d)s "
            f"AND {_sql_no_excluido('cheque')} "
            "GROUP BY banco, unidad",
            {"d": dia},
        )
    except Exception:
        _log.warning("tesoreria: no pude leer los e-cheq recibidos del día", exc_info=True)
        return {}
    return {(r["banco"], r["unidad"]): float(r["total"] or 0) for r in rows}


def buscar_comitentes(q: str = "", limit: int = 20) -> list[dict]:
    """Autocomplete del form de cheques. Reusa la búsqueda de SENEBIS (misma tabla
    `clientes.cuentas`) y le suma el CUIT de `clientes.comitentes` para que el back
    office no tenga que tipearlo."""
    from api.services.senebis import buscar_comitentes as _buscar
    encontrados = _buscar(q=q, limit=limit)
    if not encontrados:
        return []
    docs = {
        r["id_cuenta"]: r["nro_doc"]
        for r in _q("SELECT id_cuenta, nro_doc FROM clientes.comitentes "
                    "WHERE id_cuenta = ANY(%(ids)s) AND upper(COALESCE(tipo_doc, '')) "
                    "LIKE '%%CUIT%%'",
                    {"ids": [c["id_cuenta"] for c in encontrados]})
    }
    return [{**c, "cuit": docs.get(c["id_cuenta"])} for c in encontrados]


def _validar_cheque(datos: dict) -> dict:
    """Normaliza + valida el payload de un cheque. Devuelve los campos listos."""
    lado = str(datos.get("lado") or "emitido").strip().lower()
    if lado not in LADOS:
        raise ValueError(f"'lado' inválido: {lado} (válidos: {', '.join(LADOS)})")
    banco = str(datos.get("banco") or "").strip()
    if not banco:
        raise ValueError("falta 'banco' (elegí una cuenta operativa)")
    unidad = (str(datos.get("unidad") or "ARS").strip().upper() or "ARS")
    # El banco DEBE existir en el catálogo: el desplegable del front no es una
    # defensa (se puede pegarle al endpoint directo), y un banco inventado se
    # colaría como columna fantasma en la grilla BANCOS con plata imputada.
    # Si el catálogo no se puede leer viene vacío → no bloqueamos la carga.
    conocidos = catalogo()
    if conocidos and (banco, unidad) not in conocidos:
        raise ValueError(
            f"'{banco}' [{unidad}] no es una cuenta operativa del catálogo de Tesorería")
    try:
        importe = float(datos.get("importe"))
    except (TypeError, ValueError) as e:
        raise ValueError("'importe' tiene que ser un número") from e
    if importe <= 0:
        raise ValueError("'importe' tiene que ser mayor a 0")
    est = str(datos.get("estado") or "pendiente").strip().lower()
    validos = ESTADOS_CHEQUE[lado]
    if est not in validos:
        raise ValueError(f"'estado' inválido para {lado}: {est} (válidos: {', '.join(validos)})")
    tipo = str(datos.get("tipo") or "").strip().lower() or None
    if lado == "recibido":
        if tipo not in TIPOS_RECIBIDO:
            raise ValueError(f"'tipo' inválido: {tipo} (válidos: {', '.join(TIPOS_RECIBIDO)})")
    else:
        tipo = None  # el tipo es solo del lado recibido
    fp = str(datos.get("fecha_pago") or "").strip()
    return {
        "lado": lado,
        "tipo": tipo,
        "comitente": str(datos.get("comitente") or "").strip() or None,
        "comitente_denominacion": str(datos.get("comitente_denominacion") or "").strip() or None,
        "cuit": str(datos.get("cuit") or "").strip() or None,
        "banco": banco,
        "unidad": unidad,
        "importe": importe,
        "estado": est,
        "fecha_pago": datetime.strptime(fp, "%Y-%m-%d").date() if fp else None,
        # Se sella al cerrar; si se reabre (vuelve a un estado abierto) se limpia.
        "cerrado_at": datetime.now(UTC) if est == ESTADO_CIERRE[lado] else None,
    }


def crear_cheque(datos: dict, actor: str) -> dict:
    """Alta de un cheque, emitido o recibido (allowlist de Tesorería + admin)."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para cargar cheques de Tesorería")
    f = _validar_cheque(datos)
    f |= {"por": (actor or "").lower() or None, "at": datetime.now(UTC)}
    campos = ", ".join(_CAMPOS_CHEQUE)
    valores = ", ".join(f"%({c})s" for c in _CAMPOS_CHEQUE)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {_TABLA_CHEQUES} ({campos}, creado_por, creado_at) "
            f"VALUES ({valores}, %(por)s, %(at)s) RETURNING id",
            f,
        )
        nuevo = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "crear_cheque", str(nuevo),
           {"lado": f["lado"], "banco": f["banco"], "estado": f["estado"]})
    return {"id": int(nuevo)}


def editar_cheque(id_: int, datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar cheques de Tesorería")
    f = _validar_cheque(datos)
    f |= {"id": int(id_), "por": (actor or "").lower() or None, "at": datetime.now(UTC)}
    sets = ", ".join(f"{c} = %({c})s" for c in _CAMPOS_CHEQUE)
    n = _exec(f"UPDATE {_TABLA_CHEQUES} SET {sets}, "
              "actualizado_por = %(por)s, actualizado_at = %(at)s WHERE id = %(id)s", f)
    if not n:
        raise ValueError(f"no existe el cheque {id_}")
    _audit(actor, "editar_cheque", str(id_),
           {"lado": f["lado"], "banco": f["banco"], "estado": f["estado"]})
    return {"id": int(id_)}


def set_estado_cheque(id_: int, estado: str, actor: str) -> dict:
    """Cambia SOLO el estado — es el click sobre la celda ESTADO en la vista, sin
    tener que reabrir la operación. Sella (o limpia) `cerrado_at` según corresponda."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar cheques de Tesorería")
    filas = _q(f"SELECT lado, estado FROM {_TABLA_CHEQUES} WHERE id = %(id)s", {"id": int(id_)})
    if not filas:
        raise ValueError(f"no existe el cheque {id_}")
    lado = filas[0]["lado"]
    est = (estado or "").strip().lower()
    validos = ESTADOS_CHEQUE[lado]
    if est not in validos:
        raise ValueError(f"'estado' inválido para {lado}: {est} (válidos: {', '.join(validos)})")
    cierra = est == ESTADO_CIERRE[lado]
    _exec(
        f"UPDATE {_TABLA_CHEQUES} SET estado = %(e)s, cerrado_at = %(cerr)s, "
        "actualizado_por = %(por)s, actualizado_at = %(at)s WHERE id = %(id)s",
        {"id": int(id_), "e": est, "cerr": datetime.now(UTC) if cierra else None,
         "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "estado_cheque", str(id_), {"de": filas[0]["estado"], "a": est})
    return {"id": int(id_), "estado": est, "cerrado": cierra}


def borrar_cheque(id_: int, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para borrar cheques de Tesorería")
    n = _exec(f"DELETE FROM {_TABLA_CHEQUES} WHERE id = %(id)s", {"id": int(id_)})
    _audit(actor, "borrar_cheque", str(id_))
    return {"borrado": n}


# ──────────────────────────────────────────────────────────────────────────────
# TAB MERCADOS — 4 tableros de carga manual, todos del DÍA (igual que los cheques
# recibidos: se registran intradía y no se arrastran). Son el MISMO modelo con
# distinto `tipo`, por eso comparten tabla, validación, permisos y auditoría:
#
#   bloque MERCADO → ingreso (izq)  | pago        (der)
#   bloque FCI     → rescate (izq)  | suscripcion (der)
#
# `entidad` es el mercado (BYMA, MAE…) o el nombre del FCI, según el bloque.
# ──────────────────────────────────────────────────────────────────────────────

_TABLA_MERCADOS = "operaciones.tesoreria_mercados"
# tipo → (bloque, lado). El front arma la grilla 2×2 con esto, sin hardcodear.
TIPOS_MERCADO: dict[str, tuple[str, str]] = {
    "ingreso": ("mercado", "izq"), "pago": ("mercado", "der"),
    "rescate": ("fci", "izq"), "suscripcion": ("fci", "der"),
}
ESTADOS_MERCADO = ("pendiente", "completado")
_CAMPOS_MERCADO = ("fecha", "tipo", "entidad", "banco", "unidad", "importe", "estado")
_COLS_MERCADO = ("id, fecha, tipo, entidad, banco, unidad, importe, estado, "
                 "creado_por, creado_at")


def _fila_mercado(r: dict) -> dict:
    return {
        "id": int(r["id"]), "fecha": r["fecha"].isoformat(), "tipo": r["tipo"],
        "entidad": r["entidad"], "banco": r["banco"], "unidad": r["unidad"],
        "importe": float(r["importe"] or 0), "estado": r["estado"],
        "creado_por": r["creado_por"],
    }


def mercados(*, fecha: str | None = None, email: str = "") -> dict:
    """Los 4 tableros de la tab MERCADOS para un día."""
    marcar_presencia(email)
    dia = _dia(fecha)
    filas = [_fila_mercado(r) for r in _q(
        f"SELECT {_COLS_MERCADO} FROM {_TABLA_MERCADOS} "
        "WHERE fecha = %(d)s ORDER BY id DESC", {"d": dia})]
    return {
        "fecha": dia.strftime("%d/%m/%Y"), "fecha_iso": dia.isoformat(),
        # Un array por tipo: el front no tiene que filtrar ni conocer los tipos.
        "filas": {t: [f for f in filas if f["tipo"] == t] for t in TIPOS_MERCADO},
        "tipos": {t: {"bloque": b, "lado": l} for t, (b, l) in TIPOS_MERCADO.items()},
        "estados": list(ESTADOS_MERCADO),
        # Catálogo real (ABM en la vista), agrupado por bloque para el desplegable.
        "entidades": {b: catalogo_entidades(b) for b in BLOQUES},
        "bancos": [{"banco": c, "unidad": u} for c, u in catalogo()],
        "puede_editar": puede_editar_saldo(email),
        "conectados": conectados(), "actualizado_at": datetime.now(UTC).isoformat(),
    }


# ── Catálogo de MERCADOS / FCI (ABM desde la vista, con auditoría) ────────────

_TABLA_ENTIDADES = "operaciones.tesoreria_entidades"
BLOQUES = ("mercado", "fci")


def _etiqueta(codigo: str | None, nombre: str) -> str:
    """'[BYMA] BYMA' — como lo muestra el sistema de origen. Sin código, el nombre."""
    return f"[{codigo}] {nombre}" if codigo else nombre


def catalogo_entidades(bloque: str | None = None, *, solo_activas: bool = True) -> list[dict]:
    """Mercados y FCI del catálogo. Es lo que se ofrece en el desplegable."""
    try:
        rows = _q(
            f"SELECT id, bloque, codigo, nombre, activa FROM {_TABLA_ENTIDADES} "
            "WHERE (%(b)s = '' OR bloque = %(b)s) AND (NOT %(act)s OR activa) "
            "ORDER BY bloque, nombre, codigo",
            {"b": (bloque or "").strip().lower(), "act": bool(solo_activas)},
        )
    except Exception:
        _log.warning("tesoreria: no pude leer el catálogo de mercados", exc_info=True)
        return []
    return [{"id": int(r["id"]), "bloque": r["bloque"], "codigo": r["codigo"],
             "nombre": r["nombre"], "activa": r["activa"],
             "etiqueta": _etiqueta(r["codigo"], r["nombre"])} for r in rows]


def _validar_entidad(datos: dict) -> dict:
    bloque = str(datos.get("bloque") or "").strip().lower()
    if bloque not in BLOQUES:
        raise ValueError(f"'bloque' inválido: {bloque} (válidos: {', '.join(BLOQUES)})")
    nombre = " ".join(str(datos.get("nombre") or "").split())
    if not nombre:
        raise ValueError("falta el nombre")
    codigo = " ".join(str(datos.get("codigo") or "").split()) or None
    return {"bloque": bloque, "codigo": codigo, "nombre": nombre}


def crear_entidad(datos: dict, actor: str) -> dict:
    """Alta de un mercado / FCI en el catálogo."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar el catálogo de Mercados")
    f = _validar_entidad(datos) | {"por": (actor or "").lower() or None,
                                   "at": datetime.now(UTC)}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {_TABLA_ENTIDADES} (bloque, codigo, nombre, creado_por, "
                "creado_at) VALUES (%(bloque)s, %(codigo)s, %(nombre)s, %(por)s, %(at)s) "
                "RETURNING id", f)
            nuevo = cur.fetchone()[0]
            conn.commit()
    except Exception as e:  # el índice único es la defensa real contra duplicados
        raise ValueError(f"ya existe una entidad con ese código/nombre en {f['bloque']}") from e
    _audit(actor, "crear_entidad", str(nuevo), f | {"por": None, "at": None})
    return {"id": int(nuevo)}


def editar_entidad(id_: int, datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar el catálogo de Mercados")
    f = _validar_entidad(datos) | {"id": int(id_), "por": (actor or "").lower() or None,
                                   "at": datetime.now(UTC)}
    n = _exec(f"UPDATE {_TABLA_ENTIDADES} SET bloque = %(bloque)s, codigo = %(codigo)s, "
              "nombre = %(nombre)s, actualizado_por = %(por)s, actualizado_at = %(at)s "
              "WHERE id = %(id)s", f)
    if not n:
        raise ValueError(f"no existe la entidad {id_}")
    _audit(actor, "editar_entidad", str(id_), {"nombre": f["nombre"], "codigo": f["codigo"]})
    return {"id": int(id_)}


def baja_entidad(id_: int, actor: str, *, activa: bool = False) -> dict:
    """Baja LÓGICA: los movimientos históricos siguen apuntando a esta entidad, así
    que nunca se borra la fila — se saca del desplegable."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar el catálogo de Mercados")
    n = _exec(f"UPDATE {_TABLA_ENTIDADES} SET activa = %(a)s, actualizado_por = %(por)s, "
              "actualizado_at = %(at)s WHERE id = %(id)s",
              {"id": int(id_), "a": bool(activa), "por": (actor or "").lower() or None,
               "at": datetime.now(UTC)})
    if not n:
        raise ValueError(f"no existe la entidad {id_}")
    _audit(actor, "baja_entidad" if not activa else "alta_entidad", str(id_))
    return {"id": int(id_), "activa": bool(activa)}


def _validar_mercado(datos: dict) -> dict:
    """Normaliza + valida una fila de MERCADOS. Mismas reglas que los cheques:
    el banco tiene que existir en el catálogo (el desplegable no es una defensa)."""
    tipo = str(datos.get("tipo") or "").strip().lower()
    if tipo not in TIPOS_MERCADO:
        raise ValueError(f"'tipo' inválido: {tipo} (válidos: {', '.join(TIPOS_MERCADO)})")
    banco = str(datos.get("banco") or "").strip()
    unidad = (str(datos.get("unidad") or "ARS").strip().upper() or "ARS")
    if not banco:
        raise ValueError("falta 'banco' (elegí una cuenta operativa)")
    conocidos = catalogo()
    if conocidos and (banco, unidad) not in conocidos:
        raise ValueError(
            f"'{banco}' [{unidad}] no es una cuenta operativa del catálogo de Tesorería")
    try:
        importe = float(datos.get("importe"))
    except (TypeError, ValueError) as e:
        raise ValueError("'importe' tiene que ser un número") from e
    if importe <= 0:
        raise ValueError("'importe' tiene que ser mayor a 0")
    estado = str(datos.get("estado") or "pendiente").strip().lower()
    if estado not in ESTADOS_MERCADO:
        raise ValueError(f"'estado' inválido: {estado} "
                         f"(válidos: {', '.join(ESTADOS_MERCADO)})")
    # La entidad tiene que existir en el catálogo del bloque (mercado o FCI). Igual
    # que con el banco: el desplegable del front no es una defensa.
    bloque = TIPOS_MERCADO[tipo][0]
    entidad = " ".join(str(datos.get("entidad") or "").split()) or None
    if entidad:
        validas = {e["etiqueta"] for e in catalogo_entidades(bloque)}
        if validas and entidad not in validas:
            raise ValueError(f"'{entidad}' no está en el catálogo de {bloque}")
    return {
        "fecha": _dia(str(datos.get("fecha") or "") or None),
        "tipo": tipo, "entidad": entidad,
        "banco": banco, "unidad": unidad, "importe": importe, "estado": estado,
    }


def crear_mercado(datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para cargar movimientos de Mercados")
    f = _validar_mercado(datos)
    f |= {"por": (actor or "").lower() or None, "at": datetime.now(UTC)}
    campos = ", ".join(_CAMPOS_MERCADO)
    valores = ", ".join(f"%({c})s" for c in _CAMPOS_MERCADO)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {_TABLA_MERCADOS} ({campos}, creado_por, creado_at) "
            f"VALUES ({valores}, %(por)s, %(at)s) RETURNING id", f)
        nuevo = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "crear_mercado", str(nuevo), {"tipo": f["tipo"], "banco": f["banco"]})
    return {"id": int(nuevo)}


def editar_mercado(id_: int, datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar movimientos de Mercados")
    f = _validar_mercado(datos)
    f |= {"id": int(id_), "por": (actor or "").lower() or None, "at": datetime.now(UTC)}
    sets = ", ".join(f"{c} = %({c})s" for c in _CAMPOS_MERCADO)
    n = _exec(f"UPDATE {_TABLA_MERCADOS} SET {sets}, actualizado_por = %(por)s, "
              "actualizado_at = %(at)s WHERE id = %(id)s", f)
    if not n:
        raise ValueError(f"no existe el movimiento {id_}")
    _audit(actor, "editar_mercado", str(id_), {"tipo": f["tipo"], "banco": f["banco"]})
    return {"id": int(id_)}


def set_estado_mercado(id_: int, estado: str, actor: str) -> dict:
    """Cambia SOLO el estado — el click sobre la celda, sin reabrir la operación."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar movimientos de Mercados")
    est = (estado or "").strip().lower()
    if est not in ESTADOS_MERCADO:
        raise ValueError(f"'estado' inválido: {est} (válidos: {', '.join(ESTADOS_MERCADO)})")
    n = _exec(f"UPDATE {_TABLA_MERCADOS} SET estado = %(e)s, actualizado_por = %(por)s, "
              "actualizado_at = %(at)s WHERE id = %(id)s",
              {"id": int(id_), "e": est, "por": (actor or "").lower() or None,
               "at": datetime.now(UTC)})
    if not n:
        raise ValueError(f"no existe el movimiento {id_}")
    _audit(actor, "estado_mercado", str(id_), {"a": est})
    return {"id": int(id_), "estado": est}


def borrar_mercado(id_: int, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para borrar movimientos de Mercados")
    n = _exec(f"DELETE FROM {_TABLA_MERCADOS} WHERE id = %(id)s", {"id": int(id_)})
    _audit(actor, "borrar_mercado", str(id_))
    return {"borrado": n}


# ──────────────────────────────────────────────────────────────────────────────
# REGISTROS MANUALES (modal de la tab BANCOS) — FUENTE NUEVA de movimientos que NO
# viene de la API. Cada registro impacta el saldo del banco elegido según su
# `sentido` (egreso por default), sumándose a las filas Ingresos / Egresos de la
# grilla. En el detalle de la celda salen marcados "registro manual" para que se
# distingan de los de Aunesa.
#
# El modal tiene DOS tabs, y `grupo` es lo único que las separa:
#   'rescate' → RESCATE ACA VALORES: `tipo` acotado al catálogo fijo (PROVEEDORES,
#               VEP, …). Es el panel cuyo TOTAL se muestra en la barra de la vista.
#   'otros'   → OTROS REGISTROS: `tipo` es texto LIBRE. Impacta el saldo del banco
#               EXACTAMENTE igual, pero NO entra al resumen ni al TOTAL del rescate.
#
# Cada tab es 50/50: izquierda la carga, derecha el resumen por TIPO. La fila
# SALDOS del resumen del rescate es MANUAL y no sale de los registros → vive en su
# propia tabla (`tesoreria_registros_saldo`), uno por día y moneda.
# ──────────────────────────────────────────────────────────────────────────────

_TABLA_REGISTROS = "operaciones.tesoreria_registros"
_TABLA_REG_SALDO = "operaciones.tesoreria_registros_saldo"
SENTIDOS = ("egreso", "ingreso")
# Tipos cargables del grupo 'rescate'. SALDOS queda AFUERA a propósito: es la fila
# manual del resumen. El grupo 'otros' NO valida contra esta lista (tipo libre).
TIPOS_REGISTRO = ("PROVEEDORES", "FONDOS FIJOS", "VEP", "HABERES", "IMPUESTO",
                  "TARJETA VISA", "OTROS")
TIPO_SALDOS = "SALDOS"
GRUPOS_REGISTRO = ("rescate", "otros")
GRUPO_DEFAULT = "rescate"
# Las filas viejas (previas a la columna) son del rescate: ese era el único panel.
_SQL_GRUPO = "COALESCE(NULLIF(grupo, ''), 'rescate')"


def _rescate_por_unidad(dia: date) -> dict[str, dict]:
    """{unidad: {por_tipo, saldo, saldo_por, total}} del panel RESCATE ACA VALORES.

    Única fuente del panel: la usan el modal Y la leyenda de la barra, así que el
    número de la barra no puede contradecir al del modal. Los registros del grupo
    'otros' quedan afuera a propósito — impactan el banco, no el rescate.
    """
    out: dict[str, dict] = {}

    def _u(uni: str) -> dict:
        return out.setdefault((uni or "ARS").strip().upper(),
                              {"por_tipo": {}, "saldo": 0.0, "saldo_por": None})

    try:
        for r in _q(f"SELECT unidad, importe, actualizado_por FROM {_TABLA_REG_SALDO} "
                    "WHERE fecha = %(d)s", {"d": dia}):
            u = _u(r["unidad"])
            u["saldo"] = float(r["importe"] or 0)
            u["saldo_por"] = r["actualizado_por"]
    except Exception:
        _log.warning("tesoreria: no pude leer el saldo manual del rescate", exc_info=True)
    try:
        for r in _q(f"SELECT unidad, tipo, SUM(importe) AS imp FROM {_TABLA_REGISTROS} "
                    f"WHERE fecha = %(d)s AND {_SQL_GRUPO} = 'rescate' "
                    "GROUP BY unidad, tipo", {"d": dia}):
            _u(r["unidad"])["por_tipo"][r["tipo"]] = float(r["imp"] or 0)
    except Exception:
        _log.warning("tesoreria: no pude leer los registros del rescate", exc_info=True)

    for u in out.values():
        u["total"] = round(u["saldo"] + sum(u["por_tipo"].values()), 2)
    return out


def totales_rescate(dia: date) -> dict[str, float]:
    """{unidad: TOTAL del panel RESCATE ACA VALORES} — lo que muestra la barra."""
    return {u: v["total"] for u, v in _rescate_por_unidad(dia).items()}


def registros(*, fecha: str | None = None, unidad: str = "ARS", email: str = "") -> dict:
    """Modal REGISTROS MANUALES: las cargas del día + el resumen de cada tab."""
    marcar_presencia(email)
    dia = _dia(fecha)
    uni = (unidad or "ARS").strip().upper()
    filas = [{
        "id": int(r["id"]), "tipo": r["tipo"], "banco": r["banco"], "unidad": r["unidad"],
        "importe": float(r["importe"] or 0), "sentido": r["sentido"],
        "grupo": r["grupo"], "usuario": r["creado_por"],
        "hora": r["creado_at"].astimezone(UTC).strftime("%H:%M") if r["creado_at"] else "",
    } for r in _q(
        f"SELECT id, tipo, banco, unidad, importe, sentido, {_SQL_GRUPO} AS grupo, "
        f"creado_por, creado_at "
        f"FROM {_TABLA_REGISTROS} WHERE fecha = %(d)s ORDER BY id DESC", {"d": dia})]

    # Tab RESCATE: SALDOS (manual) primero, después la suma por tipo del catálogo.
    r_uni = _rescate_por_unidad(dia).get(uni, {"por_tipo": {}, "saldo": 0.0,
                                               "saldo_por": None, "total": 0.0})
    resumen = ([{"tipo": TIPO_SALDOS, "importe": round(r_uni["saldo"], 2), "manual": True}]
               + [{"tipo": t, "importe": round(r_uni["por_tipo"].get(t, 0.0), 2),
                   "manual": False} for t in TIPOS_REGISTRO])
    # Tab OTROS: el tipo es libre, así que el resumen se arma con los que hay cargados.
    otros: dict[str, float] = {}
    for f in filas:
        if f["grupo"] == "otros" and f["unidad"] == uni:
            otros[f["tipo"]] = otros.get(f["tipo"], 0.0) + f["importe"]
    resumen_otros = [{"tipo": t, "importe": round(v, 2)}
                     for t, v in sorted(otros.items())]
    return {
        "fecha": dia.strftime("%d/%m/%Y"), "fecha_iso": dia.isoformat(), "unidad": uni,
        "filas": filas, "resumen": resumen,
        "total": r_uni["total"],
        "resumen_otros": resumen_otros,
        "total_otros": round(sum(otros.values()), 2),
        "saldo_manual": round(r_uni["saldo"], 2),
        "saldo_por": r_uni["saldo_por"],
        "tipos": list(TIPOS_REGISTRO), "sentidos": list(SENTIDOS),
        "grupos": list(GRUPOS_REGISTRO),
        "bancos": [{"banco": c, "unidad": u} for c, u in catalogo()],
        "puede_editar": puede_editar_saldo(email),
        "actualizado_at": datetime.now(UTC).isoformat(),
    }


def registros_por_banco(dia: date) -> dict[tuple[str, str], dict]:
    """{(banco, unidad): {ingresos, egresos, n}} de los registros manuales del día.

    Se suman a las filas Ingresos / Egresos de la grilla: son movimientos reales
    del banco, solo que cargados a mano en vez de venir de la API. Los DOS grupos
    ('rescate' y 'otros') cuentan igual — el grupo solo separa los resúmenes.
    """
    try:
        rows = _q(
            "SELECT banco, unidad, "
            "SUM(CASE WHEN sentido = 'ingreso' THEN importe ELSE 0 END) AS ing, "
            "SUM(CASE WHEN sentido = 'egreso'  THEN importe ELSE 0 END) AS egr, "
            f"COUNT(*) AS n FROM {_TABLA_REGISTROS} WHERE fecha = %(d)s "
            f"AND {_sql_no_excluido('registro')} GROUP BY banco, unidad", {"d": dia})
    except Exception:
        _log.warning("tesoreria: no pude leer los registros manuales", exc_info=True)
        return {}
    return {(r["banco"], r["unidad"]): {"ingresos": float(r["ing"] or 0),
                                        "egresos": float(r["egr"] or 0),
                                        "n": int(r["n"])} for r in rows}


def _validar_registro(datos: dict) -> dict:
    grupo = str(datos.get("grupo") or GRUPO_DEFAULT).strip().lower()
    if grupo not in GRUPOS_REGISTRO:
        raise ValueError(f"'grupo' inválido: {grupo} (válidos: {', '.join(GRUPOS_REGISTRO)})")
    tipo = " ".join(str(datos.get("tipo") or "").split()).upper()
    # El rescate valida contra el catálogo fijo; en 'otros' el tipo es texto libre.
    if grupo == "rescate":
        if tipo not in TIPOS_REGISTRO:
            raise ValueError(f"'tipo' inválido: {tipo} (válidos: {', '.join(TIPOS_REGISTRO)})")
    elif not tipo:
        raise ValueError("falta 'tipo' (escribí de qué es el registro)")
    banco = str(datos.get("banco") or "").strip()
    unidad = (str(datos.get("unidad") or "ARS").strip().upper() or "ARS")
    if not banco:
        raise ValueError("falta 'banco' (elegí una cuenta operativa)")
    conocidos = catalogo()
    if conocidos and (banco, unidad) not in conocidos:
        raise ValueError(f"'{banco}' [{unidad}] no es una cuenta operativa del catálogo")
    sentido = str(datos.get("sentido") or "egreso").strip().lower()
    if sentido not in SENTIDOS:
        raise ValueError(f"'sentido' inválido: {sentido} (válidos: {', '.join(SENTIDOS)})")
    try:
        importe = float(datos.get("importe"))
    except (TypeError, ValueError) as e:
        raise ValueError("'importe' tiene que ser un número") from e
    if importe <= 0:
        raise ValueError("'importe' tiene que ser mayor a 0")
    return {"fecha": _dia(str(datos.get("fecha") or "") or None), "tipo": tipo,
            "banco": banco, "unidad": unidad, "importe": importe, "sentido": sentido,
            "grupo": grupo}


_CAMPOS_REGISTRO = ("fecha", "tipo", "banco", "unidad", "importe", "sentido", "grupo")


def crear_registro(datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para cargar registros manuales")
    f = _validar_registro(datos) | {"por": (actor or "").lower() or None,
                                    "at": datetime.now(UTC)}
    campos = ", ".join(_CAMPOS_REGISTRO)
    valores = ", ".join(f"%({c})s" for c in _CAMPOS_REGISTRO)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {_TABLA_REGISTROS} ({campos}, creado_por, creado_at) "
                    f"VALUES ({valores}, %(por)s, %(at)s) RETURNING id", f)
        nuevo = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "crear_registro", str(nuevo),
           {"grupo": f["grupo"], "tipo": f["tipo"], "banco": f["banco"],
            "sentido": f["sentido"]})
    # Un registro de tipo VEP aparece SOLO en la tab VEPS, con lo que este registro
    # sabe (importe/banco/moneda); el número, el concepto y el vencimiento se cargan
    # después desde ahí. El espejo NO vuelve a sumar al saldo — el egreso ya lo puso
    # este registro, y contarlo dos veces era el riesgo de tener las dos pantallas.
    # El try//except es defensa en profundidad: acá arriba el INSERT del registro YA
    # commiteó. Si el espejo explotara, propagar el error le mostraría "falló" al
    # usuario por algo que en realidad se guardó, y lo cargaría DOS VECES — que en
    # esta tabla sí mueve el saldo.
    vep_id = None
    if str(f["tipo"] or "").strip().upper() == "VEP":
        try:
            vep_id = espejar_vep_de_registro(int(nuevo), f, actor)
        except Exception:
            _log.warning("tesoreria: el registro %s se guardó pero no pude espejar el VEP",
                         nuevo, exc_info=True)
    return {"id": int(nuevo), "vep_id": vep_id}


def editar_registro(id_: int, datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar registros manuales")
    f = _validar_registro(datos) | {"id": int(id_), "por": (actor or "").lower() or None,
                                    "at": datetime.now(UTC)}
    sets = ", ".join(f"{c} = %({c})s" for c in _CAMPOS_REGISTRO)
    n = _exec(f"UPDATE {_TABLA_REGISTROS} SET {sets}, actualizado_por = %(por)s, "
              "actualizado_at = %(at)s WHERE id = %(id)s", f)
    if not n:
        raise ValueError(f"no existe el registro {id_}")
    _audit(actor, "editar_registro", str(id_),
           {"grupo": f["grupo"], "tipo": f["tipo"], "banco": f["banco"]})
    return {"id": int(id_)}


def borrar_registro(id_: int, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para borrar registros manuales")
    n = _exec(f"DELETE FROM {_TABLA_REGISTROS} WHERE id = %(id)s", {"id": int(id_)})
    _audit(actor, "borrar_registro", str(id_))
    return {"borrado": n}


def set_saldo_registros(*, fecha: str | None, unidad: str, importe: float,
                        actor: str) -> dict:
    """Fila SALDOS del resumen: carga manual, no sale de los registros."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar el saldo de registros manuales")
    uni = (unidad or "ARS").strip().upper()
    if uni not in UNIDADES:
        raise ValueError(f"unidad inválida: {uni}")
    try:
        val = float(importe)
    except (TypeError, ValueError) as e:
        raise ValueError("'importe' tiene que ser un número") from e
    dia = _dia(fecha)
    _exec(f"INSERT INTO {_TABLA_REG_SALDO} (fecha, unidad, importe, actualizado_por, "
          "actualizado_at) VALUES (%(d)s, %(u)s, %(i)s, %(por)s, %(at)s) "
          "ON CONFLICT (fecha, unidad) DO UPDATE SET importe = EXCLUDED.importe, "
          "actualizado_por = EXCLUDED.actualizado_por, "
          "actualizado_at = EXCLUDED.actualizado_at",
          {"d": dia, "u": uni, "i": val, "por": (actor or "").lower() or None,
           "at": datetime.now(UTC)})
    _audit(actor, "set_saldo_registros", f"{dia.isoformat()}|{uni}", {"importe": val})
    return {"fecha": dia.isoformat(), "unidad": uni, "importe": val}


# ──────────────────────────────────────────────────────────────────────────────
# TAB BANCO A BANCO — transferencias INTERNAS entre cuentas propias. El equipo
# mueve saldo de un banco a otro para dejarlos cubiertos; no es plata que entra o
# sale de la ALyC, así que la SUMA de las dos patas es cero y el total del día no
# cambia. Lo que cambia es CÓMO queda repartido entre bancos.
#
# Una fila toca DOS bancos → en la grilla BANCOS se abre en dos filas:
#   banco a banco (+) → la cuenta CRÉDITO recibe   (suma)
#   banco a banco (−) → la cuenta DÉBITO entrega   (resta)
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# VEPS (tab VEPS) — agenda de vencimientos. TODOS egresos, no hay ingresos.
#
# Mismo horizonte que los cheques EMITIDOS: es un tablero de SEGUIMIENTO, NO filtra
# por fecha (un VEP viejo sin pagar sigue a la vista) y la fila no se borra al
# pagarse — queda para el histórico.
#
# NO IMPACTA EL SALDO de la grilla BANCOS, a propósito: el egreso ya entra al banco
# por REGISTROS MANUALES (tipo 'VEP'). Si esta tabla también sumara, el mismo VEP se
# contaría DOS VECES. Por eso ninguna función de acá la llama `ingresos_egresos_dia`.
#
# Dos formas de que nazca un VEP, y conviven:
#   1) carga manual en la tab (`origen='manual'`);
#   2) espejo automático al crear un registro manual de tipo 'VEP' (`origen='registro'`)
#      — se copia lo que ese registro tiene (importe/banco/moneda) y el número, el
#      concepto y el vencimiento se completan después desde la tab.
# ──────────────────────────────────────────────────────────────────────────────

_TABLA_VEPS = "operaciones.tesoreria_veps"
ESTADOS_VEP = ("pendiente", "pagado")
ESTADO_VEP_CIERRE = "pagado"
# El banco que viene precargado en el alta: es el que usa el back office casi siempre.
VEP_BANCO_DEFAULT = "AL2"
_CAMPOS_VEP = ("numero_vep", "concepto", "importe", "banco", "unidad", "vencimiento",
               "estado")
_COLS_VEP = ("id, numero_vep, concepto, importe, banco, unidad, vencimiento, estado, "
             "origen, registro_id, pagado_at, creado_por, creado_at")


def _fila_vep(r: dict, hoy: date) -> dict:
    """Fila del tablero. `vencido` lo decide el BACKEND (no el front): es el que
    sabe qué día es en ART, y así la marca amarilla no depende del reloj del navegador."""
    venc = r["vencimiento"]
    pagado = r["estado"] == ESTADO_VEP_CIERRE
    return {
        "id": int(r["id"]),
        "numero_vep": r["numero_vep"],
        "concepto": r["concepto"],
        "importe": float(r["importe"] or 0),
        "banco": r["banco"],
        "unidad": r["unidad"],
        "vencimiento": venc.isoformat() if venc else None,
        "estado": r["estado"],
        # AMARILLO en la vista: ya venció y todavía no se pagó. Un VEP pagado no se
        # marca aunque su vencimiento haya pasado — ya no hay nada que hacer con él.
        "vencido": bool(venc and venc < hoy and not pagado),
        "origen": r["origen"],
        "registro_id": int(r["registro_id"]) if r["registro_id"] is not None else None,
        "pagado_at": r["pagado_at"].isoformat() if r["pagado_at"] else None,
        "creado_por": r["creado_por"],
        "creado_at": r["creado_at"].isoformat() if r["creado_at"] else None,
    }


def veps(*, incluir_pagados: bool = False, email: str = "") -> dict:
    """Tab VEPS. Sin filtro de fecha (tablero de seguimiento).

    Por default trae solo los PENDIENTES: 'pagado' saca la fila de la vista pero NO
    la borra (`incluir_pagados=True` la trae igual, para auditoría).
    """
    marcar_presencia(email)
    hoy = _hoy_art().date()
    donde = "" if incluir_pagados else f"WHERE estado <> '{ESTADO_VEP_CIERRE}'"
    try:
        rows = _q(f"SELECT {_COLS_VEP} FROM {_TABLA_VEPS} {donde} "
                  # NULLS LAST: un VEP sin vencimiento cargado (típico del espejo) no
                  # puede encabezar el tablero como si fuera el más urgente.
                  "ORDER BY vencimiento ASC NULLS LAST, id DESC")
    except Exception:
        _log.warning("tesoreria: no pude listar los VEPs", exc_info=True)
        rows = []
    items = [_fila_vep(r, hoy) for r in rows]
    # Totales por moneda, separando lo ya vencido: es el número que el back office
    # mira para saber cuánto tiene encima HOY.
    tot: dict[str, dict] = {}
    for v in items:
        if v["estado"] == ESTADO_VEP_CIERRE:
            continue
        t = tot.setdefault(v["unidad"], {"total": 0.0, "vencido": 0.0, "n": 0})
        t["total"] += v["importe"]
        t["n"] += 1
        if v["vencido"]:
            t["vencido"] += v["importe"]
    for t in tot.values():
        t["total"], t["vencido"] = round(t["total"], 2), round(t["vencido"], 2)
    return {"items": items, "totales": tot, "hoy": hoy.isoformat(),
            "banco_default": VEP_BANCO_DEFAULT, "estados": list(ESTADOS_VEP),
            "bancos": [{"banco": c, "unidad": u} for c, u in catalogo()],
            "puede_editar": puede_editar_saldo(email)}


def _validar_vep(datos: dict) -> dict:
    banco = str(datos.get("banco") or VEP_BANCO_DEFAULT).strip()
    unidad = (str(datos.get("unidad") or "ARS").strip().upper() or "ARS")
    if not banco:
        raise ValueError("falta 'banco' (elegí una cuenta operativa)")
    conocidos = catalogo()
    if conocidos and (banco, unidad) not in conocidos:
        raise ValueError(f"'{banco}' [{unidad}] no es una cuenta operativa del catálogo")
    try:
        importe = float(datos.get("importe"))
    except (TypeError, ValueError) as e:
        raise ValueError("'importe' tiene que ser un número") from e
    if importe <= 0:
        raise ValueError("'importe' tiene que ser mayor a 0")
    estado = str(datos.get("estado") or "pendiente").strip().lower()
    if estado not in ESTADOS_VEP:
        raise ValueError(f"estado inválido: {estado} (válidos: {', '.join(ESTADOS_VEP)})")
    venc = str(datos.get("vencimiento") or "").strip()
    return {
        "numero_vep": str(datos.get("numero_vep") or "").strip() or None,
        "concepto": str(datos.get("concepto") or "").strip() or None,
        "importe": importe, "banco": banco, "unidad": unidad,
        # El vencimiento puede faltar (el espejo nace sin él) y se completa después.
        "vencimiento": _dia(venc) if venc else None,
        "estado": estado,
    }


def crear_vep(datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para cargar VEPs")
    f = _validar_vep(datos) | {"por": (actor or "").lower() or None,
                               "at": datetime.now(UTC)}
    campos = ", ".join(_CAMPOS_VEP)
    valores = ", ".join(f"%({c})s" for c in _CAMPOS_VEP)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {_TABLA_VEPS} ({campos}, origen, creado_por, creado_at) "
                    f"VALUES ({valores}, 'manual', %(por)s, %(at)s) RETURNING id", f)
        nuevo = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "crear_vep", str(nuevo),
           {"numero_vep": f["numero_vep"], "banco": f["banco"], "importe": f["importe"]})
    return {"id": int(nuevo)}


def espejar_vep_de_registro(registro_id: int, reg: dict, actor: str) -> int | None:
    """Crea el VEP espejo de un registro manual de tipo 'VEP'.

    BEST-EFFORT a propósito: si esto falla NO puede voltear la carga del registro
    manual, que es lo que mueve el saldo. El VEP nace con lo único que el registro
    sabe (importe, banco, moneda); número, concepto y vencimiento quedan vacíos para
    completarlos desde la tab. `registro_id` es único → re-intentar no duplica.
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {_TABLA_VEPS} (importe, banco, unidad, estado, origen, "
                "registro_id, creado_por, creado_at) "
                "VALUES (%(importe)s, %(banco)s, %(unidad)s, 'pendiente', 'registro', "
                "%(rid)s, %(por)s, %(at)s) "
                "ON CONFLICT (registro_id) WHERE registro_id IS NOT NULL DO NOTHING "
                "RETURNING id",
                {"importe": reg["importe"], "banco": reg["banco"], "unidad": reg["unidad"],
                 "rid": int(registro_id), "por": (actor or "").lower() or None,
                 "at": datetime.now(UTC)})
            fila = cur.fetchone()
            conn.commit()
        return int(fila[0]) if fila else None
    except Exception:
        _log.warning("tesoreria: no pude espejar el VEP del registro %s", registro_id,
                     exc_info=True)
        return None


def editar_vep(id_: int, datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar VEPs")
    f = _validar_vep(datos) | {"id": int(id_), "por": (actor or "").lower() or None,
                               "at": datetime.now(UTC)}
    sets = ", ".join(f"{c} = %({c})s" for c in _CAMPOS_VEP)
    n = _exec(f"UPDATE {_TABLA_VEPS} SET {sets}, actualizado_por = %(por)s, "
              f"actualizado_at = %(at)s WHERE id = %(id)s", f)
    if not n:
        raise ValueError(f"no existe el VEP {id_}")
    _audit(actor, "editar_vep", str(id_), {"numero_vep": f["numero_vep"]})
    return {"id": int(id_), "actualizado": n}


def set_estado_vep(id_: int, estado: str, actor: str) -> dict:
    """Cambia SOLO el estado (click en la celda ESTADO). Sella `pagado_at` al cerrar."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para cambiar el estado de un VEP")
    est = (estado or "").strip().lower()
    if est not in ESTADOS_VEP:
        raise ValueError(f"estado inválido: {est} (válidos: {', '.join(ESTADOS_VEP)})")
    ahora = datetime.now(UTC)
    n = _exec(f"UPDATE {_TABLA_VEPS} SET estado = %(e)s, "
              # Volver a 'pendiente' limpia la marca de pago: si no, quedaría una
              # fecha de pago de algo que no está pagado.
              "pagado_at = CASE WHEN %(e)s = %(cierre)s THEN %(at)s ELSE NULL END, "
              "actualizado_por = %(por)s, actualizado_at = %(at)s WHERE id = %(id)s",
              {"e": est, "cierre": ESTADO_VEP_CIERRE, "at": ahora, "id": int(id_),
               "por": (actor or "").lower() or None})
    if not n:
        raise ValueError(f"no existe el VEP {id_}")
    _audit(actor, "estado_vep", str(id_), {"estado": est})
    return {"id": int(id_), "estado": est}


def borrar_vep(id_: int, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para borrar VEPs")
    n = _exec(f"DELETE FROM {_TABLA_VEPS} WHERE id = %(id)s", {"id": int(id_)})
    if not n:
        raise ValueError(f"no existe el VEP {id_}")
    _audit(actor, "borrar_vep", str(id_), {})
    return {"id": int(id_), "borrado": n}


_TABLA_BB = "operaciones.tesoreria_banco_a_banco"
ESTADOS_BB = ("pendiente", "completado")
_CAMPOS_BB = ("fecha", "cta_debito", "cta_credito", "unidad", "importe", "estado")


def banco_a_banco(*, fecha: str | None = None, email: str = "") -> dict:
    """Transferencias internas del día + catálogo de bancos para el form."""
    marcar_presencia(email)
    dia = _dia(fecha)
    rows = _q(
        f"SELECT id, fecha, cta_debito, cta_credito, unidad, importe, estado, creado_por "
        f"FROM {_TABLA_BB} WHERE fecha = %(d)s ORDER BY id DESC", {"d": dia})
    return {
        "fecha": dia.strftime("%d/%m/%Y"), "fecha_iso": dia.isoformat(),
        "filas": [{
            "id": int(r["id"]), "fecha": r["fecha"].isoformat(),
            "cta_debito": r["cta_debito"], "cta_credito": r["cta_credito"],
            "unidad": r["unidad"], "importe": float(r["importe"] or 0),
            "estado": r["estado"], "creado_por": r["creado_por"],
        } for r in rows],
        "bancos": [{"banco": c, "unidad": u} for c, u in catalogo()],
        "estados": list(ESTADOS_BB),
        "puede_editar": puede_editar_saldo(email),
        "conectados": conectados(), "actualizado_at": datetime.now(UTC).isoformat(),
    }


def banco_a_banco_por_banco(dia: date) -> dict[tuple[str, str], dict]:
    """{(banco, unidad): {bb_mas, bb_menos}} del día.

    Cada fila aporta a DOS bancos, así que se desarma con un UNION ALL (crédito en
    positivo, débito en negativo) y se agrupa: una sola pasada, sin traer las filas.
    """
    try:
        rows = _q(
            "SELECT banco, unidad, SUM(mas) AS bb_mas, SUM(menos) AS bb_menos FROM ("
            "  SELECT cta_credito AS banco, unidad, importe AS mas, 0 AS menos "
            f"  FROM {_TABLA_BB} WHERE fecha = %(d)s AND {_sql_no_excluido('bb')} "
            "  UNION ALL "
            "  SELECT cta_debito AS banco, unidad, 0 AS mas, importe AS menos "
            f"  FROM {_TABLA_BB} WHERE fecha = %(d)s AND {_sql_no_excluido('bb')}"
            ") t GROUP BY banco, unidad",
            {"d": dia},
        )
    except Exception:
        _log.warning("tesoreria: no pude leer banco a banco", exc_info=True)
        return {}
    return {(r["banco"], r["unidad"]): {"bb_mas": float(r["bb_mas"] or 0),
                                        "bb_menos": float(r["bb_menos"] or 0)} for r in rows}


def _validar_bb(datos: dict) -> dict:
    """Las dos cuentas TIENEN que existir en el catálogo, ser distintas y de la
    misma moneda (con un solo importe no se puede representar un cambio de divisa)."""
    deb = str(datos.get("cta_debito") or "").strip()
    cre = str(datos.get("cta_credito") or "").strip()
    unidad = (str(datos.get("unidad") or "ARS").strip().upper() or "ARS")
    if not deb or not cre:
        raise ValueError("hay que elegir la cuenta de DÉBITO y la de CRÉDITO")
    if deb == cre:
        raise ValueError("la cuenta de débito y la de crédito tienen que ser distintas")
    conocidos = catalogo()
    if conocidos:
        for etiqueta, cta in (("débito", deb), ("crédito", cre)):
            if (cta, unidad) not in conocidos:
                raise ValueError(
                    f"la cuenta de {etiqueta} '{cta}' [{unidad}] no está en el "
                    "catálogo de Tesorería")
    try:
        importe = float(datos.get("importe"))
    except (TypeError, ValueError) as e:
        raise ValueError("'importe' tiene que ser un número") from e
    if importe <= 0:
        raise ValueError("'importe' tiene que ser mayor a 0")
    estado = str(datos.get("estado") or "pendiente").strip().lower()
    if estado not in ESTADOS_BB:
        raise ValueError(f"'estado' inválido: {estado} (válidos: {', '.join(ESTADOS_BB)})")
    return {"fecha": _dia(str(datos.get("fecha") or "") or None),
            "cta_debito": deb, "cta_credito": cre, "unidad": unidad,
            "importe": importe, "estado": estado}


def crear_bb(datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para cargar transferencias banco a banco")
    f = _validar_bb(datos) | {"por": (actor or "").lower() or None, "at": datetime.now(UTC)}
    campos = ", ".join(_CAMPOS_BB)
    valores = ", ".join(f"%({c})s" for c in _CAMPOS_BB)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {_TABLA_BB} ({campos}, creado_por, creado_at) "
                    f"VALUES ({valores}, %(por)s, %(at)s) RETURNING id", f)
        nuevo = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "crear_banco_a_banco", str(nuevo),
           {"debito": f["cta_debito"], "credito": f["cta_credito"], "unidad": f["unidad"]})
    return {"id": int(nuevo)}


def editar_bb(id_: int, datos: dict, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar transferencias banco a banco")
    f = _validar_bb(datos) | {"id": int(id_), "por": (actor or "").lower() or None,
                              "at": datetime.now(UTC)}
    sets = ", ".join(f"{c} = %({c})s" for c in _CAMPOS_BB)
    n = _exec(f"UPDATE {_TABLA_BB} SET {sets}, actualizado_por = %(por)s, "
              "actualizado_at = %(at)s WHERE id = %(id)s", f)
    if not n:
        raise ValueError(f"no existe la transferencia {id_}")
    _audit(actor, "editar_banco_a_banco", str(id_),
           {"debito": f["cta_debito"], "credito": f["cta_credito"]})
    return {"id": int(id_)}


def set_estado_bb(id_: int, estado: str, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar transferencias banco a banco")
    est = (estado or "").strip().lower()
    if est not in ESTADOS_BB:
        raise ValueError(f"'estado' inválido: {est} (válidos: {', '.join(ESTADOS_BB)})")
    n = _exec(f"UPDATE {_TABLA_BB} SET estado = %(e)s, actualizado_por = %(por)s, "
              "actualizado_at = %(at)s WHERE id = %(id)s",
              {"id": int(id_), "e": est, "por": (actor or "").lower() or None,
               "at": datetime.now(UTC)})
    if not n:
        raise ValueError(f"no existe la transferencia {id_}")
    _audit(actor, "estado_banco_a_banco", str(id_), {"a": est})
    return {"id": int(id_), "estado": est}


def borrar_bb(id_: int, actor: str) -> dict:
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para borrar transferencias banco a banco")
    n = _exec(f"DELETE FROM {_TABLA_BB} WHERE id = %(id)s", {"id": int(id_)})
    _audit(actor, "borrar_banco_a_banco", str(id_))
    return {"borrado": n}


# ── Export TXT para HYGIRUS (asiento de ajuste) ────────────────────────────────
# El back office hoy tipea el asiento a mano. El archivo es:
#   línea 1  →  "DD/MM/AAAA HH:MM:SS Asiento de ajuste"
#   luego, por cada transferencia NO completada, DOS líneas separadas por TAB:
#     -importe <TAB> nro HYGIRUS de la cuenta DÉBITO  <TAB> moneda
#      importe <TAB> nro HYGIRUS de la cuenta CRÉDITO <TAB> moneda
# La cuenta que se escribe NO es la denominación del banco sino su
# `numero_hygirus` (catálogo `tesoreria_cuentas`); si falta, el asiento saldría
# con la cuenta vacía y HYGIRUS lo rechaza → se corta con error nombrándola.

NOMBRE_TXT_BB = "Bco a Bco.txt"
_SIN_HYGIRUS = {"", "-", "—", "--", "N/A", "S/D"}


def _importe_hygirus(valor: Any) -> str:
    """1234.5 → '1234,5' | 6000000000 → '6000000000'. Coma decimal, sin miles.

    Se formatea desde Decimal (los importes vienen `numeric` de Postgres): pasar
    por float redondearía mal los montos grandes.
    """
    d = Decimal(str(valor or 0)).quantize(Decimal("0.01"))
    txt = format(d, "f").rstrip("0").rstrip(".")
    return (txt or "0").replace(".", ",")


def _hygirus_por_cuenta() -> dict[tuple[str, str], str]:
    """{(cuenta_operativa, unidad): numero_hygirus}. La PK del catálogo es el par."""
    return {(c["cuenta_operativa"], c["unidad"]): (c["numero_hygirus"] or "").strip()
            for c in listar_cuentas()}


def armar_txt_bb(filas: list[dict], hygirus: dict[tuple[str, str], str],
                 ahora: datetime) -> tuple[str, list[str]]:
    """Contenido del TXT + cuentas sin N° HYGIRUS cargado. Lógica pura."""
    lineas = [f"{ahora.strftime('%d/%m/%Y %H:%M:%S')} Asiento de ajuste"]
    faltantes: list[str] = []
    for f in filas:
        importe = _importe_hygirus(f["importe"])
        unidad = f["unidad"]
        for cta, signo in ((f["cta_debito"], "-"), (f["cta_credito"], "")):
            num = (hygirus.get((cta, unidad)) or "").strip()
            if num.upper() in _SIN_HYGIRUS:
                faltantes.append(f"{cta} ({unidad})")
                num = ""
            lineas.append(f"{signo}{importe}\t{num}\t{unidad}")
    return "\n".join(lineas) + "\n", sorted(set(faltantes))


def txt_banco_a_banco(*, fecha: str | None = None) -> dict:
    """Asiento de ajuste del día con las transferencias NO completadas.

    El archivo se genera SIEMPRE: si no quedan pendientes sale solo con la
    cabecera y sin filas. Nunca levanta excepción — el diagnóstico (`filas`,
    `faltantes`) viaja como dato, porque el proxy de Next mapea cualquier error
    del backend a un 502 sin mensaje.
    """
    dia = _dia(fecha)
    filas = _q(
        f"SELECT cta_debito, cta_credito, unidad, importe FROM {_TABLA_BB} "
        "WHERE fecha = %(d)s AND estado <> 'completado' ORDER BY id", {"d": dia})
    txt, faltantes = armar_txt_bb([dict(f) for f in filas], _hygirus_por_cuenta(),
                                  _hoy_art())
    return {"nombre": NOMBRE_TXT_BB, "contenido": txt,
            "filas": len(filas), "faltantes": faltantes}


# ──────────────────────────────────────────────────────────────────────────────
# Catálogo de cuentas operativas (bancos). Aunesa no tiene endpoint de cuentas:
# el universo se descubre viendo movimientos, así que se persiste acá.
# ──────────────────────────────────────────────────────────────────────────────

def catalogo() -> list[tuple[str, str]]:
    """[(cuenta_operativa, unidad)] activas. Vacío si la tabla no existe todavía."""
    try:
        rows = _q("SELECT cuenta_operativa, unidad FROM operaciones.tesoreria_cuentas "
                  "WHERE activa ORDER BY cuenta_operativa, unidad")
    except Exception:
        _log.warning("tesoreria: no pude leer el catálogo de cuentas", exc_info=True)
        return []
    return [(r["cuenta_operativa"], r["unidad"]) for r in rows]


UNIDADES = ("ARS", "USD")


def listar_cuentas(*, solo_activas: bool = False) -> list[dict]:
    """Catálogo de bancos con TODOS sus campos — alimenta el ABM de la vista."""
    try:
        rows = _q("SELECT cuenta_operativa, unidad, numero_cuenta, numero_hygirus, "
                  "aunesa_id, activa, primera_vez, ultima_vez "
                  "FROM operaciones.tesoreria_cuentas "
                  "WHERE (NOT %(act)s OR activa) ORDER BY cuenta_operativa, unidad",
                  {"act": bool(solo_activas)})
    except Exception:
        _log.warning("tesoreria: no pude listar cuentas", exc_info=True)
        return []
    return [{
        "cuenta_operativa": r["cuenta_operativa"], "unidad": r["unidad"],
        "numero_cuenta": r["numero_cuenta"],
        # Identificador en HYGIRUS: se guarda pero NO se muestra en la grilla.
        "numero_hygirus": r["numero_hygirus"], "aunesa_id": r["aunesa_id"],
        "activa": r["activa"],
        # `descubierta` = la trajo Aunesa sola; las de alta manual no tienen id hasta
        # que el banco opere por primera vez.
        "descubierta": bool(r["aunesa_id"]),
        "ultima_vez": r["ultima_vez"].isoformat() if r["ultima_vez"] else None,
    } for r in rows]


def _validar_cuenta(cuenta_operativa: str, unidad: str) -> tuple[str, str]:
    cta = " ".join((cuenta_operativa or "").split()).upper()   # colapsa espacios
    uni = (unidad or "").strip().upper()
    if not cta:
        raise ValueError("falta el nombre de la cuenta operativa")
    if uni not in UNIDADES:
        raise ValueError(f"unidad inválida: {uni} (válidas: {', '.join(UNIDADES)})")
    return cta, uni


def crear_cuenta(cuenta_operativa: str, unidad: str, actor: str,
                 numero_cuenta: str | None = None,
                 numero_hygirus: str | None = None) -> dict:
    """Alta MANUAL de una cuenta operativa (banco) desde la vista.

    Existe porque el catálogo se descubre viendo movimientos: un banco que todavía
    no operó nunca no aparece, y el back office igual necesita cargarle el saldo.
    Se modela EXACTAMENTE igual que las auto-descubiertas (misma tabla, misma PK)
    — `aunesa_id` queda NULL y se completa solo la primera vez que el banco opere.
    """
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para dar de alta bancos de Tesorería")
    cta, uni = _validar_cuenta(cuenta_operativa, unidad)
    if (cta, uni) in set(catalogo()):
        raise ValueError(f"'{cta}' [{uni}] ya está en el catálogo")
    nro = (numero_cuenta or "").strip() or None
    hyg = (numero_hygirus or "").strip() or None
    _exec(
        "INSERT INTO operaciones.tesoreria_cuentas "
        "(cuenta_operativa, unidad, numero_cuenta, numero_hygirus, activa) "
        "VALUES (%(c)s, %(u)s, %(n)s, %(h)s, true) "
        "ON CONFLICT (cuenta_operativa, unidad) DO UPDATE SET activa = true, "
        "numero_cuenta = COALESCE(EXCLUDED.numero_cuenta, "
        "                         operaciones.tesoreria_cuentas.numero_cuenta), "
        "numero_hygirus = COALESCE(EXCLUDED.numero_hygirus, "
        "                          operaciones.tesoreria_cuentas.numero_hygirus)",
        {"c": cta, "u": uni, "n": nro, "h": hyg},
    )
    _audit(actor, "crear_cuenta", f"{cta}|{uni}",
           {"numero_cuenta": nro, "numero_hygirus": hyg})
    return {"cuenta_operativa": cta, "unidad": uni, "numero_cuenta": nro,
            "numero_hygirus": hyg}


def editar_cuenta(cuenta_operativa: str, unidad: str, actor: str, *,
                  numero_cuenta: str | None = None, numero_hygirus: str | None = None,
                  nuevo_nombre: str | None = None, activa: bool | None = None) -> dict:
    """Edita una cuenta del catálogo (número, nombre y alta/baja lógica).

    OJO — la PK es (cuenta_operativa, unidad) y los saldos, cheques y movimientos de
    Mercados referencian el NOMBRE. Renombrar tiene que arrastrar esas tablas o los
    históricos quedan huérfanos: se hace todo en UNA transacción.
    """
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar bancos de Tesorería")
    cta, uni = _validar_cuenta(cuenta_operativa, unidad)
    nuevo = " ".join((nuevo_nombre or "").split()).upper() or cta
    if nuevo != cta and (nuevo, uni) in set(catalogo()):
        raise ValueError(f"'{nuevo}' [{uni}] ya está en el catálogo")
    p = {"c": cta, "u": uni, "nuevo": nuevo,
         "n": (numero_cuenta or "").strip() or None,
         "h": (numero_hygirus or "").strip() or None,
         "act": activa, "set_act": activa is not None}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operaciones.tesoreria_cuentas SET cuenta_operativa = %(nuevo)s, "
            "numero_cuenta = %(n)s, numero_hygirus = %(h)s, "
            "activa = CASE WHEN %(set_act)s THEN %(act)s ELSE activa END "
            "WHERE cuenta_operativa = %(c)s AND unidad = %(u)s", p)
        if not cur.rowcount:
            raise ValueError(f"no existe el banco '{cta}' [{uni}]")
        if nuevo != cta:
            # Arrastre del renombre a todo lo que apunta al banco por nombre.
            for tabla, col in (("operaciones.tesoreria_saldos", "cuenta_operativa"),
                               ("operaciones.tesoreria_cheques", "banco"),
                               ("operaciones.tesoreria_mercados", "banco")):
                cur.execute(f"UPDATE {tabla} SET {col} = %(nuevo)s "
                            f"WHERE {col} = %(c)s AND unidad = %(u)s", p)
        conn.commit()
    _audit(actor, "editar_cuenta", f"{cta}|{uni}",
           {"nuevo_nombre": nuevo if nuevo != cta else None,
            "numero_cuenta": p["n"], "numero_hygirus": p["h"], "activa": activa})
    return {"cuenta_operativa": nuevo, "unidad": uni, "numero_cuenta": p["n"],
            "numero_hygirus": p["h"]}


def registrar_cuentas(vistas: dict[tuple[str, str], str | None], dia: date) -> None:
    """Da de alta las cuentas vistas en un día (idempotente).

    El UPDATE tiene guarda para que el poll de la vista (cada 20s) sea un no-op
    cuando no hay nada nuevo que anotar.
    """
    # El placeholder de "fila sin cuenta operativa" no es un banco: si se persiste,
    # queda como columna fantasma en la grilla aunque nunca más vuelva a aparecer.
    filas = [{"c": c, "u": u, "id": aid, "d": dia}
             for (c, u), aid in vistas.items() if c != SIN_CUENTA]
    if not filas:
        return
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO operaciones.tesoreria_cuentas "
                "(cuenta_operativa, unidad, aunesa_id, primera_vez, ultima_vez) "
                "VALUES (%(c)s, %(u)s, %(id)s, %(d)s, %(d)s) "
                "ON CONFLICT (cuenta_operativa, unidad) DO UPDATE SET "
                "aunesa_id = COALESCE(EXCLUDED.aunesa_id, operaciones.tesoreria_cuentas.aunesa_id), "
                "primera_vez = LEAST(operaciones.tesoreria_cuentas.primera_vez, EXCLUDED.primera_vez), "
                "ultima_vez = GREATEST(operaciones.tesoreria_cuentas.ultima_vez, EXCLUDED.ultima_vez) "
                "WHERE operaciones.tesoreria_cuentas.ultima_vez IS NULL "
                "   OR operaciones.tesoreria_cuentas.primera_vez IS NULL "
                "   OR operaciones.tesoreria_cuentas.aunesa_id IS NULL "
                "   OR EXCLUDED.ultima_vez > operaciones.tesoreria_cuentas.ultima_vez "
                "   OR EXCLUDED.primera_vez < operaciones.tesoreria_cuentas.primera_vez",
                filas,
            )
            conn.commit()
    except Exception:
        _log.warning("tesoreria: no pude registrar cuentas operativas", exc_info=True)


# ─────────────────────────────────────────────────────────────────────
# Presencia (quién tiene la vista abierta) — mismo patrón que SENEBIS
# ─────────────────────────────────────────────────────────────────────

def marcar_presencia(email: str) -> None:
    e = (email or "").lower().strip()
    if not e:
        return
    try:
        _exec("INSERT INTO operaciones.tesoreria_presencia (email, visto_at) "
              "VALUES (%(e)s, %(at)s) "
              "ON CONFLICT (email) DO UPDATE SET visto_at = EXCLUDED.visto_at",
              {"e": e, "at": datetime.now(UTC)})
    except Exception:
        _log.warning("tesoreria: no pude marcar presencia", exc_info=True)


def conectados() -> list[dict]:
    """Quiénes vieron la vista en los últimos PRESENCIA_TTL_S segundos."""
    try:
        rows = _q("SELECT email, visto_at FROM operaciones.tesoreria_presencia "
                  "WHERE visto_at >= now() - make_interval(secs => %(ttl)s) ORDER BY email",
                  {"ttl": PRESENCIA_TTL_S})
    except Exception:
        return []
    return [{"email": r["email"], "visto_at": r["visto_at"].isoformat()} for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Saldo inicial por banco (carga manual — Aunesa solo da los movimientos del día)
# ──────────────────────────────────────────────────────────────────────────────

def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _saldos_dia(dia: date) -> dict[tuple[str, str], dict]:
    """{(cuenta_operativa, unidad): {saldo_inicial, actualizado_por, actualizado_at}}.

    Degrada a vacío si la tabla todavía no existe (schema sin aplicar): el detalle
    de movimientos es lo crítico de la vista y no puede caerse por esto.
    """
    try:
        rows = _q("SELECT cuenta_operativa, unidad, saldo_inicial, actualizado_por, "
                  "actualizado_at FROM operaciones.tesoreria_saldos WHERE fecha = %(f)s",
                  {"f": dia})
    except Exception:
        _log.warning("tesoreria: no pude leer operaciones.tesoreria_saldos", exc_info=True)
        return {}
    return {
        (r["cuenta_operativa"], r["unidad"]): {
            "saldo_inicial": float(r["saldo_inicial"]) if r["saldo_inicial"] is not None else None,
            "actualizado_por": r["actualizado_por"],
            "actualizado_at": r["actualizado_at"].isoformat() if r["actualizado_at"] else None,
        }
        for r in rows
    }


def set_saldo_inicial(*, fecha: str | None, cuenta_operativa: str, unidad: str,
                      saldo: float | None, actor: str) -> dict:
    """Fija (o borra, con `saldo=None`) el saldo inicial de un banco para un día."""
    if not puede_editar_saldo(actor):
        raise PermissionError("sin permiso para editar saldos de Tesorería")
    cta = (cuenta_operativa or "").strip()
    uni = (unidad or "").strip().upper()
    if not cta or not uni:
        raise ValueError("faltan 'cuenta_operativa' y/o 'unidad'")
    dia = _dia(fecha)
    if saldo is None:
        _exec("DELETE FROM operaciones.tesoreria_saldos WHERE fecha = %(f)s "
              "AND cuenta_operativa = %(c)s AND unidad = %(u)s",
              {"f": dia, "c": cta, "u": uni})
    else:
        _exec(
            "INSERT INTO operaciones.tesoreria_saldos "
            "(fecha, cuenta_operativa, unidad, saldo_inicial, actualizado_por, actualizado_at) "
            "VALUES (%(f)s, %(c)s, %(u)s, %(s)s, %(por)s, %(at)s) "
            "ON CONFLICT (fecha, cuenta_operativa, unidad) DO UPDATE SET "
            "saldo_inicial = EXCLUDED.saldo_inicial, actualizado_por = EXCLUDED.actualizado_por, "
            "actualizado_at = EXCLUDED.actualizado_at",
            {"f": dia, "c": cta, "u": uni, "s": saldo,
             "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
        )
    _audit(actor, "set_saldo_inicial", f"{dia.isoformat()}|{cta}|{uni}", {"saldo_inicial": saldo})
    return {"fecha": dia.isoformat(), "cuenta_operativa": cta, "unidad": uni,
            "saldo_inicial": saldo}


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Evento de auditoría — nunca rompe la operación principal."""
    try:
        _exec(
            "INSERT INTO operaciones.tesoreria_audit (ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": (actor or "").lower() or None, "action": action,
             "target": target, "data": Jsonb(data or {})},
        )
    except Exception:
        _log.exception("tesoreria: audit insert falló")


# ──────────────────────────────────────────────────────────────────────────────
# Allowlist de escritura del saldo inicial (Manager → MESA). Default-deny.
# ──────────────────────────────────────────────────────────────────────────────

def puede_editar_saldo(email: str) -> bool:
    e = (email or "").lower().strip()
    if not e:
        return False
    try:
        from core.roles import get_user_role
        if get_user_role(e) == "admin":
            return True
        return bool(_q("SELECT 1 FROM operaciones.tesoreria_escritores WHERE email = %(e)s",
                       {"e": e}))
    except Exception:
        _log.warning("tesoreria: no pude resolver permiso de saldo", exc_info=True)
        return False


def listar_escritores() -> dict:
    rows = _q("SELECT email, agregado_por, agregado_at FROM operaciones.tesoreria_escritores "
              "ORDER BY email")
    return {"escritores": [
        {"email": r["email"], "agregado_por": r["agregado_por"],
         "agregado_at": r["agregado_at"].isoformat() if r["agregado_at"] else None}
        for r in rows
    ]}


def candidatos_escritores(q: str = "", limit: int = 30) -> dict:
    """Usuarios de la app que todavía no están en la allowlist de Tesorería."""
    rows = _q(
        "SELECT u.email, u.role FROM manager.manager_users u "
        "WHERE u.email NOT IN (SELECT email FROM operaciones.tesoreria_escritores) "
        "AND u.email ILIKE %(t)s ORDER BY u.email LIMIT %(lim)s",
        {"t": f"%{(q or '').strip()}%", "lim": limit},
    )
    return {"candidatos": rows}


def agregar_escritor(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    _exec(
        "INSERT INTO operaciones.tesoreria_escritores (email, agregado_por, agregado_at) "
        "VALUES (%(e)s, %(por)s, %(at)s) ON CONFLICT (email) DO NOTHING",
        {"e": e, "por": (actor or "").lower() or None, "at": datetime.now(UTC)},
    )
    _audit(actor, "add_escritor", e)
    return {"email": e}


def quitar_escritor(email: str, actor: str) -> dict:
    e = (email or "").lower().strip()
    if not e:
        raise ValueError("falta 'email'")
    borrado = _exec("DELETE FROM operaciones.tesoreria_escritores WHERE email = %(e)s", {"e": e})
    _audit(actor, "remove_escritor", e)
    return {"borrado": borrado}
