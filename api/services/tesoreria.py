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

_TABLA_AL2 = "operaciones.tesoreria_al2"
AL2_BANCO_CODIGO = "00001713"   # FERSI SA — el único banco que se persiste
_RE_BANCO_COD = re.compile(r"\[(\w+)\]")


def _norm(s: Any) -> str:
    """Minúscula + sin diacríticos, para comparar `solicitud` a prueba de NFC/NFD."""
    d = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in d if not unicodedata.combining(c)).strip().lower()


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
    mov["cuentaOperativa"] = _cuenta_operativa(r.get("cuentaOperativa")) or "SIN CUENTA OPERATIVA"
    mov["_hora"] = _hora(r.get("id"), yyyymmdd)
    mov["_tipo"] = "ingreso" if sol == _INGRESO else "egreso" if sol == _EGRESO else ""
    return mov


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
            destinos.append(resumen.setdefault(
                unidad, {"ingresos": 0.0, "egresos": 0.0, "neto": 0.0, "n": 0}))
        if est == ESTADO_EFECTIVO:  # el saldo del banco, solo plata que se movió
            destinos.append(por_cuenta.setdefault(
                (cta, unidad),
                {"cuenta_operativa": cta, "unidad": unidad,
                 "ingresos": 0.0, "egresos": 0.0, "neto": 0.0, "n": 0}))
        for d in destinos:
            if mov["_tipo"] == "ingreso":
                d["ingresos"] += monto
            else:
                d["egresos"] += monto
            d["neto"] = d["ingresos"] - d["egresos"]
            d["n"] += 1

    for b in resumen.values():
        b["ingresos"], b["egresos"], b["neto"] = (
            round(b["ingresos"], 2), round(b["egresos"], 2), round(b["neto"], 2))
    movimientos.sort(key=lambda m: str(m.get("_hora") or ""), reverse=True)

    # El panel de bancos es FIJO: sale del catálogo, no de quién operó hoy. Las cuentas
    # nuevas que aparezcan en el día se registran solas y quedan para siempre.
    registrar_cuentas(vistas, dia)
    saldos = _saldos_dia(dia)
    cuentas = []
    for clave in sorted(set(catalogo()) | set(por_cuenta)):
        cta, uni = clave
        c = por_cuenta.get(clave) or {"cuenta_operativa": cta, "unidad": uni,
                                      "ingresos": 0.0, "egresos": 0.0, "neto": 0.0, "n": 0}
        s = saldos.get(clave)
        ini = s["saldo_inicial"] if s else None
        c["ingresos"], c["egresos"], c["neto"] = (
            round(c["ingresos"], 2), round(c["egresos"], 2), round(c["neto"], 2))
        # Sin carga manual el inicial es 0 (no null): así el saldo final siempre cierra
        # como número. `saldo_cargado` es lo que separa "cargado en 0" de "sin cargar".
        c["saldo_cargado"] = ini is not None
        c["saldo_inicial"] = round(ini if ini is not None else 0.0, 2)
        c["saldo_final"] = round(c["saldo_inicial"] + c["neto"], 2)
        c["saldo_por"] = s["actualizado_por"] if s else None
        c["saldo_at"] = s["actualizado_at"] if s else None
        cuentas.append(c)

    return {"fecha": ddmmyyyy, "fecha_iso": dia.isoformat(), "estado": estado,
            "estado_bancos": ESTADO_EFECTIVO,  # el front lo aclara en la tab BANCOS
            "resumen": resumen, "cuentas": cuentas,
            "puede_editar_saldo": puede_editar_saldo(email),
            "conectados": conectados(), "actualizado_at": datetime.now(UTC).isoformat(),
            "movimientos": movimientos, "n": len(movimientos), "raw": len(movimientos)}


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


def registrar_cuentas(vistas: dict[tuple[str, str], str | None], dia: date) -> None:
    """Da de alta las cuentas vistas en un día (idempotente).

    El UPDATE tiene guarda para que el poll de la vista (cada 20s) sea un no-op
    cuando no hay nada nuevo que anotar.
    """
    if not vistas:
        return
    filas = [{"c": c, "u": u, "id": aid, "d": dia} for (c, u), aid in vistas.items()]
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
