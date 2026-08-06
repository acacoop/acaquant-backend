"""api/services/tesoreria.py — Back Office → Tesorería (ingresos/egresos del día).

Fuente: Aunesa `GET cuentas/consultaMovDocsSolicitados` ("Movimientos y Documentos
Solicitados") — movimientos BANCARIOS de dinero (transferencias, transferencia MEP,
e-cheq). La DIRECCIÓN la da el campo `solicitud` (Depósito = ingreso / Extracción =
egreso), NO el signo del `monto` (siempre positivo). Plata efectiva = `estado`
'Procesado' (default). Modelo verificado por discovery 2026-07-03.

Puro (sin FastAPI): lo llama `api/routers/back_office.py`. Se sirve LIVE contra Aunesa
sin persistir — volumen chico (~cientos de mov/día). El histórico de saldos (si se
necesita) se congelará con un job aparte más adelante.

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

SALDO AL2 (2026-08-06): tab con los movimientos del banco FERSI SA (`[00001713]`) de
los últimos N días + la sumatoria acumulada. Necesita SERIE, y Aunesa se pide día por
día → SOLO esos movimientos se persisten, en `operaciones.tesoreria_al2`
(`jobs/tesoreria_al2.py`, idempotente por `id`). El resto de los movimientos NO se
guarda: la vista del día sigue siendo live.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from api.services._sql import _q
from core import aunesa
from core.pg_mirror import write_native
from core.postgres import get_pool

_log = logging.getLogger(__name__)

_ENDPOINT = "cuentas/consultaMovDocsSolicitados"
# La dirección la da `solicitud`. Comparamos SIN acentos y en minúscula porque el string
# de Aunesa puede venir en NFC o NFD (la 'ó'/'ó' se ven iguales pero != por bytes) — comparar
# el literal acentuado directo descartaba TODAS las filas (200 OK con 0 resultados).
_INGRESO = "deposito"   # 'Depósito'
_EGRESO = "extraccion"  # 'Extracción'
PRESENCIA_TTL_S = 90    # visto hace ≤90s = conectado (el front pollea cada ~20s)

# Estados de Aunesa. AL2 se ingesta con TODOS (el estado se guarda como columna y la
# lectura filtra) para no perder las filas que todavía no liquidaron.
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
# total de egresos (por lo tanto tampoco restan del saldo final).
_RE_RIEL_COD = re.compile(r"\[([^\]]+)\]")

_TABLA_AL2 = "operaciones.tesoreria_al2"
AL2_BANCO_CODIGO = "00001713"   # FERSI SA — el único banco que se persiste
_RE_BANCO_COD = re.compile(r"\[(\w+)\]")


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


def traer_crudas(dia: date, estado: str) -> list[dict]:
    """Filas crudas de Aunesa del día `dia`, ya filtradas al día objetivo.

    Aunesa EXIGE desde < hasta (un rango de un día solo tira 400), así que se pide
    [día, día+1] y se descartan las filas del día siguiente.
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
    fila propia y no entra ni en `egresos` ni, por lo tanto, en el saldo final."""
    return {"ingresos": 0.0, "ingresos_echeq": 0.0, "egresos": 0.0, "egresos_echeq": 0.0,
            "mercados": 0.0, "fci": 0.0, "bb_mas": 0.0, "bb_menos": 0.0,
            "neto": 0.0, "n": 0}


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
      - `egresos_echeq`  = RIEL e-cheq de Aunesa, fuera del total de `egresos`.
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
    crudas = traer_crudas(dia, TODOS_ESTADOS)
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
    # Filas MERCADOS y FCI: ya vienen netas y con signo (ver mercados_por_banco).
    mkt = mercados_por_banco(dia)
    # Banco a banco: una transferencia interna suma en un banco y resta en el otro.
    bb = banco_a_banco_por_banco(dia)
    # Registros manuales: FUENTE NUEVA de movimientos, no vienen de la API. Se
    # suman a Ingresos/Egresos según su sentido (el detalle los marca como manuales).
    reg = registros_por_banco(dia)
    cuentas = []
    for clave in sorted(set(catalogo()) | set(por_cuenta) | set(ing_echeq)
                        | set(mkt) | set(bb) | set(reg)):
        cta, uni = clave
        c = por_cuenta.get(clave) or {"cuenta_operativa": cta, "unidad": uni, **_bucket()}
        s = saldos.get(clave)
        ini = s["saldo_inicial"] if s else None
        c["ingresos_echeq"] = ing_echeq.get(clave, 0.0)
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
            "puede_editar_saldo": puede_editar_saldo(email),
            "catalogo": listar_cuentas(),   # ABM de bancos (nombre + número de cuenta)
            "conectados": conectados(), "actualizado_at": datetime.now(UTC).isoformat(),
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


def _items_aunesa(dia: date, banco: str, unidad: str, fila: str,
                  exc: dict) -> list[dict]:
    """Movimientos de Aunesa que componen ingresos / egresos / egresos_echeq.

    Aplica EXACTAMENTE los mismos filtros que `ingresos_egresos_dia`: estado
    Procesado, banco+moneda, y el corte e-cheq del RIEL.
    """
    yyyymmdd = dia.strftime("%Y%m%d")
    quiere_echeq = fila == "egresos_echeq"
    tipo = "ingreso" if fila == "ingresos" else "egreso"
    out = []
    for r in traer_crudas(dia, ESTADO_EFECTIVO):
        m = aplanar(r, yyyymmdd)
        if (m["cuentaOperativa"] != banco
                or str(r.get("unidad") or "?").upper() != unidad
                or m["_tipo"] != tipo):
            continue
        # ingresos/egresos excluyen e-cheq; egresos_echeq los toma solo a ellos.
        if fila != "ingresos" and m["_echeq"] is not quiere_echeq:
            continue
        # Sin hora → destildado por default: el `id` no trae fecha-hora, así que no
        # se puede distinguir de un duplicado. Se tilda a mano si corresponde.
        fuera, obs = _estado_excl(exc, "aunesa", r.get("id"),
                                  default_excluido=not m["_hora"],
                                  obs_default=OBS_SIN_HORA)
        out.append({
            "fuente": "aunesa", "ref": str(r.get("id") or ""),
            "detalle": m.get("persona_nombreCompleto") or r.get("cuenta") or "—",
            "referencia": f"{m.get('_hora') or 'sin hora'} · {r.get('tipoDocSoli') or ''}".strip(" ·"),
            "estado": r.get("estado"),
            "importe": _num(r.get("monto")),
            "excluido": fuera, "observacion": obs,
        })
    return out


def _items_sql(sql: str, params: dict) -> list[dict]:
    try:
        return [dict(r) for r in _q(sql, params)]
    except Exception:
        _log.warning("tesoreria: no pude leer el detalle de la celda", exc_info=True)
        return []


def detalle_celda(*, fecha: str | None, banco: str, unidad: str, fila: str,
                  email: str = "") -> dict:
    """Las operaciones individuales detrás de una celda de la grilla BANCOS."""
    if fila not in FILAS_DETALLE:
        raise ValueError(f"fila inválida: {fila} (válidas: {', '.join(FILAS_DETALLE)})")
    banco, unidad = (banco or "").strip(), (unidad or "").strip().upper()
    if not banco or not unidad:
        raise ValueError("faltan 'banco' y/o 'unidad'")
    dia = _dia(fecha)
    p = {"d": dia, "b": banco, "u": unidad}
    exc = _exclusiones_dia(dia)
    items: list[dict] = []
    fuente = ""

    def _fila(fuente_: str, ref, detalle: str, referencia: str, estado, importe: float):
        """Item del detalle con su tilde y su observación (traza de quién lo anuló)."""
        fuera, obs = _estado_excl(exc, fuente_, ref)
        return {"fuente": fuente_, "ref": str(ref), "detalle": detalle,
                "referencia": referencia, "estado": estado, "importe": importe,
                "excluido": fuera, "observacion": obs}

    if fila == "saldo_final":
        # No tiene operaciones propias: es la ECUACIÓN. Se devuelve el desglose
        # fila por fila para poder auditar de dónde sale el número final.
        cta = next((c for c in ingresos_egresos_dia(fecha=fecha, email=email)["cuentas"]
                    if c["cuenta_operativa"] == banco and c["unidad"] == unidad), None)
        for k in FILAS_DETALLE:
            if k == "saldo_final" or not cta:
                continue
            items.append({"fuente": "fila", "ref": k, "excluido": False,
                          "observacion": "", "detalle": k,
                          "referencia": "fila de la grilla", "estado": None,
                          "importe": _SIGNO_FILA[k] * float(cta.get(k) or 0)})
        return {"fila": fila, "banco": banco, "unidad": unidad,
                "fecha": dia.strftime("%d/%m/%Y"),
                "fuente": "Suma de las filas de la grilla (ya con su signo)",
                "total": round(sum(i["importe"] for i in items), 2), "items": items}

    if fila == "saldo_inicial":
        fuente = "Carga manual del back office (tesoreria_saldos)"
        items = [{"fuente": "saldo", "ref": "", "excluido": False, "observacion": "",
                  "detalle": f"Saldo inicial cargado por {r['actualizado_por'] or '—'}",
                  "referencia": r["actualizado_at"].isoformat() if r["actualizado_at"] else "",
                  "estado": None, "importe": float(r["saldo_inicial"] or 0)}
                 for r in _items_sql(
                     "SELECT saldo_inicial, actualizado_por, actualizado_at FROM "
                     "operaciones.tesoreria_saldos WHERE fecha = %(d)s AND "
                     "cuenta_operativa = %(b)s AND unidad = %(u)s", p)]
    elif fila in ("ingresos", "egresos", "egresos_echeq"):
        fuente = f"Movimientos de Aunesa (estado {ESTADO_EFECTIVO})"
        items = _items_aunesa(dia, banco, unidad, fila, exc)
        if fila in ("ingresos", "egresos"):
            # Los registros manuales entran en la MISMA fila, pero se marcan: es lo
            # único que no viene de la API y tiene que verse de un vistazo.
            fuente += " + REGISTROS MANUALES"
            items += [_fila("registro", r["id"], f"registro manual · {r['tipo']}",
                            f"cargado por {r['creado_por'] or '—'}", "manual",
                            float(r["importe"] or 0))
                      for r in _items_sql(
                          f"SELECT id, tipo, importe, creado_por FROM {_TABLA_REGISTROS} "
                          "WHERE fecha = %(d)s AND banco = %(b)s AND unidad = %(u)s "
                          "AND sentido = %(s)s ORDER BY id",
                          {**p, "s": "ingreso" if fila == "ingresos" else "egreso"})]
    elif fila == "ingresos_echeq":
        fuente = "Cheques RECIBIDOS finalizados (tab CHEQUES)"
        items = [_fila("cheque", r["id"],
                       r["comitente_denominacion"] or r["comitente"] or "—",
                       f"cheque {r['tipo'] or ''}".strip(), r["estado"],
                       float(r["importe"] or 0))
                 for r in _items_sql(
                     f"SELECT id, comitente, comitente_denominacion, tipo, estado, importe "
                     f"FROM {_TABLA_CHEQUES} WHERE lado = 'recibido' AND estado = 'finalizado' "
                     f"AND banco = %(b)s AND unidad = %(u)s "
                     f"AND (creado_at AT TIME ZONE '{_TZ_ART}')::date = %(d)s ORDER BY id", p)]
    elif fila in ("mercados", "fci"):
        tipos = ("ingreso", "pago") if fila == "mercados" else ("rescate", "suscripcion")
        fuente = f"Tab MERCADOS · {tipos[0]} (+) y {tipos[1]} (−)"
        # El signo lo define el tipo: el segundo de cada par resta.
        items = [_fila("mercado", r["id"], r["entidad"] or "—", r["tipo"], r["estado"],
                       float(r["importe"] or 0) * (-1 if r["tipo"] == tipos[1] else 1))
                 for r in _items_sql(
                     f"SELECT id, entidad, tipo, estado, importe FROM {_TABLA_MERCADOS} "
                     "WHERE fecha = %(d)s AND banco = %(b)s AND unidad = %(u)s "
                     "AND tipo = ANY(%(t)s) ORDER BY id", {**p, "t": list(tipos)})]
    else:  # bb_mas / bb_menos
        propia, otra = (("cta_credito", "cta_debito") if fila == "bb_mas"
                        else ("cta_debito", "cta_credito"))
        verbo = "recibido de" if fila == "bb_mas" else "enviado a"
        fuente = f"Tab BANCO A BANCO · {verbo.split()[0]}"
        items = [_fila("bb", r["id"], f"{verbo} {r['otra']}", "transferencia interna",
                       r["estado"], float(r["importe"] or 0))
                 for r in _items_sql(
                     f"SELECT id, {otra} AS otra, estado, importe FROM {_TABLA_BB} "
                     f"WHERE fecha = %(d)s AND {propia} = %(b)s AND unidad = %(u)s ORDER BY id", p)]

    # El TOTAL no cuenta lo destildado: tiene que dar exactamente lo de la celda.
    return {"fila": fila, "banco": banco, "unidad": unidad,
            "fecha": dia.strftime("%d/%m/%Y"), "fuente": fuente,
            "total": round(sum(i["importe"] for i in items
                               if not i.get("excluido")), 2),
            "excluidos": sum(1 for i in items if i.get("excluido")),
            "items": items}


# ──────────────────────────────────────────────────────────────────────────────
# SALDO AL2 — SOLO los movimientos del banco FERSI SA se persisten
# (`operaciones.tesoreria_al2`, lo alimenta `jobs/tesoreria_al2.py`). El resto de
# la tesorería NO se guarda: la serie de 60 días es lo único que no se puede
# resolver live, porque Aunesa se pide día por día.
# ──────────────────────────────────────────────────────────────────────────────

def _banco_codigo(v: Any) -> str | None:
    """'[00001713] FERSI SA' → '00001713'. None si el banco no trae código."""
    m = _RE_BANCO_COD.search(str(v or ""))
    return m.group(1) if m else None


def es_al2(mov: dict) -> bool:
    return _banco_codigo(mov.get("banco")) == AL2_BANCO_CODIGO


def _fila_sql(mov: dict, dia: date) -> dict:
    """Movimiento aplanado → fila de `operaciones.tesoreria_al2`."""
    return {
        "id": str(mov.get("id") or ""),
        "fecha": dia,
        "hora": str(mov.get("_hora") or "") or None,
        "tipo": mov.get("_tipo") or None,
        "monto": _num(mov.get("monto")),
        "unidad": str(mov.get("unidad") or "?").upper(),
        "estado": str(mov.get("estado") or "") or None,
        "banco": str(mov.get("banco") or "") or None,
        "cuenta": str(mov.get("cuenta") or "") or None,
        "cuenta_operativa": mov.get("cuentaOperativa") or None,
        "riel": str(mov.get("tipoDocSoli") or "") or None,
        "persona": str(mov.get("persona_nombreCompleto") or "") or None,
        # Normalizado (sin acentos, mayúscula) para que el filtro FISICA/JURIDICA
        # no dependa de cómo venga escrito en Aunesa.
        "persona_tipo": _norm(mov.get("persona_tipoPersona")).upper() or None,
        "persona_doc": str(mov.get("persona_documento") or "") or None,
        "persona_cuit": str(mov.get("persona_cuit") or "") or None,
        "cbu_cvu": str(mov.get("cbuCVU") or "") or None,
        "id_externo": str(mov.get("idExterno") or "") or None,
        "ingestado_en": datetime.now(UTC),
    }


def ingestar_dia(dia: date, estado: str = TODOS_ESTADOS) -> int:
    """Persiste los movimientos AL2 de `dia` (upsert por `id`). Idempotente."""
    return persistir(preparar_dia(dia, estado))


def preparar_dia(dia: date, estado: str = TODOS_ESTADOS) -> list[dict]:
    """Filas AL2 de `dia` listas para SQL (sin escribir nada). Descarta el resto."""
    yyyymmdd = dia.strftime("%Y%m%d")
    movs = (aplanar(r, yyyymmdd) for r in traer_crudas(dia, estado) if r.get("id"))
    return [_fila_sql(m, dia) for m in movs if es_al2(m)]


def persistir(filas: list[dict]) -> int:
    """Upsert por `id` en `operaciones.tesoreria_al2`."""
    return write_native(_TABLA_AL2, ["id"], filas)


def saldo_al2(*, dias: int = 60, persona: str = "todas", unidad: str = "",
              estado: str = "Procesado") -> dict:
    """Movimientos + serie diaria acumulada del banco AL2 (FERSI SA).

    `persona`: 'todas' | 'fisica' | 'juridica'. `unidad`: '' = todas las monedas.
    La serie se agrega en SQL (no sobre la muestra de movimientos) para que el
    acumulado sea correcto aunque la tabla se trunque en el LÍMITE de filas.
    """
    dias = max(1, min(int(dias or 60), 365))
    desde = _hoy_art().date() - timedelta(days=dias - 1)
    p = _norm(persona)
    where = {
        "desde": desde,
        "estado": (estado or "").strip(),
        "persona": p.upper() if p in ("fisica", "juridica") else "",
        "unidad": (unidad or "").strip().upper(),
    }
    filtro = (
        "WHERE fecha >= %(desde)s "
        "AND (%(estado)s = '' OR estado = %(estado)s) "
        "AND (%(persona)s = '' OR persona_tipo = %(persona)s) "
        "AND (%(unidad)s = '' OR unidad = %(unidad)s)"
    )

    movs = _q(
        "SELECT id, fecha, hora, tipo, monto, unidad, estado, cuenta, cuenta_operativa, "
        f"riel, persona, persona_tipo, persona_doc, persona_cuit FROM {_TABLA_AL2} {filtro} "
        "ORDER BY fecha DESC, hora DESC NULLS LAST LIMIT 5000",
        where,
    )
    diario = _q(
        "SELECT fecha, "
        "SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE 0 END) AS ingresos, "
        "SUM(CASE WHEN tipo = 'egreso'  THEN monto ELSE 0 END) AS egresos, "
        f"COUNT(*) AS n FROM {_TABLA_AL2} {filtro} GROUP BY fecha ORDER BY fecha",
        where,
    )
    unidades = [r["unidad"] for r in _q(
        f"SELECT DISTINCT unidad FROM {_TABLA_AL2} "
        "WHERE fecha >= %(desde)s AND unidad IS NOT NULL ORDER BY unidad", where)]

    serie, acum = [], 0.0
    tot_in = tot_out = 0.0
    for r in diario:
        ing, egr = float(r["ingresos"] or 0), float(r["egresos"] or 0)
        acum += ing - egr
        tot_in, tot_out = tot_in + ing, tot_out + egr
        serie.append({"fecha": r["fecha"].isoformat(), "ingresos": round(ing, 2),
                      "egresos": round(egr, 2), "neto": round(ing - egr, 2),
                      "acumulado": round(acum, 2), "n": int(r["n"])})

    return {
        "banco_codigo": AL2_BANCO_CODIGO, "desde": desde.isoformat(),
        "hasta": _hoy_art().date().isoformat(), "dias": dias,
        "persona": persona, "unidad": where["unidad"], "estado": where["estado"],
        "unidades": unidades,
        "movimientos": [
            {**m, "fecha": m["fecha"].isoformat(), "monto": float(m["monto"] or 0)}
            for m in movs
        ],
        "n": len(movs),
        "serie": serie,
        "resumen": {"ingresos": round(tot_in, 2), "egresos": round(tot_out, 2),
                    "neto": round(tot_in - tot_out, 2),
                    "n": sum(s["n"] for s in serie)},
        "actualizado_at": datetime.now(UTC).isoformat(),
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
# El modal es 50/50: izquierda la carga, derecha el resumen por TIPO. La fila
# SALDOS del resumen es MANUAL y no sale de los registros → vive en su propia
# tabla (`tesoreria_registros_saldo`), uno por día y moneda.
# ──────────────────────────────────────────────────────────────────────────────

_TABLA_REGISTROS = "operaciones.tesoreria_registros"
_TABLA_REG_SALDO = "operaciones.tesoreria_registros_saldo"
SENTIDOS = ("egreso", "ingreso")
# Tipos cargables. SALDOS queda AFUERA a propósito: es la fila manual del resumen.
TIPOS_REGISTRO = ("PROVEEDORES", "FONDOS FIJOS", "VEP", "HABERES", "IMPUESTO",
                  "TARJETA VISA", "OTROS")
TIPO_SALDOS = "SALDOS"


def registros(*, fecha: str | None = None, unidad: str = "ARS", email: str = "") -> dict:
    """Modal REGISTROS MANUALES: las cargas del día + el resumen por tipo."""
    marcar_presencia(email)
    dia = _dia(fecha)
    uni = (unidad or "ARS").strip().upper()
    filas = [{
        "id": int(r["id"]), "tipo": r["tipo"], "banco": r["banco"], "unidad": r["unidad"],
        "importe": float(r["importe"] or 0), "sentido": r["sentido"],
        "usuario": r["creado_por"],
        "hora": r["creado_at"].astimezone(UTC).strftime("%H:%M") if r["creado_at"] else "",
    } for r in _q(
        f"SELECT id, tipo, banco, unidad, importe, sentido, creado_por, creado_at "
        f"FROM {_TABLA_REGISTROS} WHERE fecha = %(d)s ORDER BY id DESC", {"d": dia})]

    # Resumen: SALDOS (manual) primero, después la suma de lo cargado por tipo.
    saldo = _q(f"SELECT importe, actualizado_por FROM {_TABLA_REG_SALDO} "
               "WHERE fecha = %(d)s AND unidad = %(u)s", {"d": dia, "u": uni})
    saldo_manual = float(saldo[0]["importe"] or 0) if saldo else 0.0
    por_tipo = {t: 0.0 for t in TIPOS_REGISTRO}
    for f in filas:
        if f["unidad"] == uni:
            por_tipo[f["tipo"]] = por_tipo.get(f["tipo"], 0.0) + f["importe"]
    resumen = ([{"tipo": TIPO_SALDOS, "importe": round(saldo_manual, 2), "manual": True}]
               + [{"tipo": t, "importe": round(por_tipo.get(t, 0.0), 2), "manual": False}
                  for t in TIPOS_REGISTRO])
    return {
        "fecha": dia.strftime("%d/%m/%Y"), "fecha_iso": dia.isoformat(), "unidad": uni,
        "filas": filas, "resumen": resumen,
        "total": round(sum(r["importe"] for r in resumen), 2),
        "saldo_manual": round(saldo_manual, 2),
        "saldo_por": saldo[0]["actualizado_por"] if saldo else None,
        "tipos": list(TIPOS_REGISTRO), "sentidos": list(SENTIDOS),
        "bancos": [{"banco": c, "unidad": u} for c, u in catalogo()],
        "puede_editar": puede_editar_saldo(email),
        "actualizado_at": datetime.now(UTC).isoformat(),
    }


def registros_por_banco(dia: date) -> dict[tuple[str, str], dict]:
    """{(banco, unidad): {ingresos, egresos, n}} de los registros manuales del día.

    Se suman a las filas Ingresos / Egresos de la grilla: son movimientos reales
    del banco, solo que cargados a mano en vez de venir de la API.
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
    tipo = " ".join(str(datos.get("tipo") or "").split()).upper()
    if tipo not in TIPOS_REGISTRO:
        raise ValueError(f"'tipo' inválido: {tipo} (válidos: {', '.join(TIPOS_REGISTRO)})")
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
            "banco": banco, "unidad": unidad, "importe": importe, "sentido": sentido}


_CAMPOS_REGISTRO = ("fecha", "tipo", "banco", "unidad", "importe", "sentido")


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
           {"tipo": f["tipo"], "banco": f["banco"], "sentido": f["sentido"]})
    return {"id": int(nuevo)}


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
    _audit(actor, "editar_registro", str(id_), {"tipo": f["tipo"], "banco": f["banco"]})
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
