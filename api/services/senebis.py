"""api/services/senebis.py — SENEBIS (vista BACK OFFICE → SENEBIS).

Órdenes que los TRADERS cargan para que el BACK OFFICE las procese en el
sistema externo. Flujo mínimo a propósito:

    trader crea la orden ('pendiente') → aparece en la vista → el back office
    la carga afuera y la marca 'completada'. Dos estados, nada más.

Derivados / defaults (una sola fuente de verdad, calculados acá):
    monto        = vn × px / 100          (px viene cada 100 VN)
    concertacion = HOY (ART) si no viene
    cp           = '255' si no viene
    plazo ↔ liquidacion se infieren entre sí (core.calendario):
        CI → liquidacion = concertacion; 24 → liquidacion = próximo hábil.
        Con liquidacion y sin plazo: mismo día → CI, si no → 24.

Presencia: el front hace heartbeat (o simplemente pollea la lista, que ya
marca presencia) → `operaciones.senebis_presencia` guarda el último visto_at
por email; conectado = visto en los últimos PRESENCIA_TTL_S segundos. Así el
equipo ve quién está en la vista y no se pisan al completar órdenes.

Contraparte del senebi (la clave del Excel):
    'interno'  → contra un cliente de la ALyC: el trader carga la cuenta (cc)
                 por NÚMERO o por DENOMINACIÓN (se resuelve contra
                 clientes.cuentas y se snapshotea cc + cc_denominacion).
    'externo'  → contra un AGENTE de afuera (COCOS, ALLARIA…): el trader elige
                 el NOMBRE del catálogo `operaciones.senebis_agentes`; el
                 NÚMERO (el que espera el sistema destino) se snapshotea en
                 agente_numero al guardar.

Export: `export_xlsx()` arma el .xlsx EXACTO que el back office carga en el
otro sistema: ID · OPERACION · INSTRUMENTO · PLAZO · PRECIO · CANTIDAD ·
CONTRAPARTE · COMITENTE · CARTERA PROPIA · MERCADO. Reglas (2026-08-05):
    externo               → COMITENTE vacío, CONTRAPARTE = número del agente.
    interno GARANTIZADO   → COMITENTE = cp (la 255), CONTRAPARTE vacío.
    interno NO GARANT./s-d→ COMITENTE = cc, CONTRAPARTE vacío.
El ID es el de la tabla: secuencia GLOBAL que arranca donde la fijemos
(scripts/senebis_set_id.py) y nunca se resetea. Los campos numéricos salen
como número cuando el valor lo es (el sistema destino los espera así).
openpyxl con import lazy para no tumbar la API si falta la lib (REGLA #1).

Permisos: módulo `back-office` (gate en api/main.py) para TODO — traders y
back office lo tienen en la matriz. Trazabilidad: cada cambio inserta un
evento en `operaciones.senebis_audit` con before/after. Servicio puro
(sin FastAPI).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from api.services._sql import _f, _q
from core.calendario import proximo_habil
from core.postgres import get_pool

ESTADOS = ("pendiente", "completada")
PLAZOS = ("CI", "24")
TIPOS_CONTRAPARTE = ("interno", "externo")
PRESENCIA_TTL_S = 90  # visto hace ≤90s = conectado (el front pollea cada ~30s)
_TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")

# Campos editables de una orden (el resto es derivado o metadata de auditoría).
_CAMPOS_OP = (
    "operacion", "concertacion", "liquidacion", "plazo", "especie",
    "vn", "px", "monto", "cp", "cc", "contraparte", "nro_contraparte",
    "mercado", "cargan_ellos", "tipo", "tipo_contraparte", "agente",
)

# Headers del Excel que el back office carga en el otro sistema (imagen de
# referencia 2026-08-05): el ID va primero y es la secuencia global de la tabla.
_HEADERS_XLSX = ("ID", "OPERACION", "INSTRUMENTO", "PLAZO", "PRECIO", "CANTIDAD",
                 "CONTRAPARTE", "COMITENTE", "CARTERA PROPIA", "MERCADO")


def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Evento de auditoría — nunca rompe la operación principal."""
    try:
        _exec(
            "INSERT INTO operaciones.senebis_audit (ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": actor or None, "action": action,
             "target": target, "data": Jsonb(_jsonable(data or {}))},
        )
    except Exception:  # el audit no bloquea la escritura real
        import logging
        logging.getLogger(__name__).exception("senebis: audit insert falló")


def _jsonable(v: Any) -> Any:
    """dict/list con date/datetime/Decimal → tipos serializables."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    return v


# ─────────────────────────────────────────────────────────────
# Presencia (quién tiene la vista abierta)
# ─────────────────────────────────────────────────────────────

def marcar_presencia(email: str) -> None:
    e = (email or "").lower().strip()
    if not e:
        return
    _exec(
        "INSERT INTO operaciones.senebis_presencia (email, visto_at) "
        "VALUES (%(e)s, %(at)s) "
        "ON CONFLICT (email) DO UPDATE SET visto_at = EXCLUDED.visto_at",
        {"e": e, "at": datetime.now(UTC)},
    )


def conectados() -> list[dict]:
    """Quiénes vieron la vista en los últimos PRESENCIA_TTL_S segundos."""
    rows = _q(
        "SELECT email, visto_at FROM operaciones.senebis_presencia "
        "WHERE visto_at >= now() - make_interval(secs => %(ttl)s) ORDER BY email",
        {"ttl": PRESENCIA_TTL_S},
    )
    return [{"email": r["email"], "visto_at": r["visto_at"].isoformat()} for r in rows]


# ─────────────────────────────────────────────────────────────
# Agentes externos (catálogo nombre → número del sistema destino)
# ─────────────────────────────────────────────────────────────

def listar_agentes() -> list[dict]:
    return [{"nombre": r["nombre"], "numero": r["numero"]} for r in _q(
        "SELECT nombre, numero FROM operaciones.senebis_agentes ORDER BY nombre")]


def _numero_agente(nombre: str) -> str | None:
    rows = _q("SELECT numero FROM operaciones.senebis_agentes WHERE nombre = %(n)s",
              {"n": nombre})
    return rows[0]["numero"] if rows else None


def upsert_agente(nombre: str, numero: str, actor: str) -> dict:
    n = (nombre or "").strip().upper()
    num = str(numero or "").strip()
    if not n:
        raise ValueError("falta 'nombre'")
    if not num:
        raise ValueError("falta 'numero' (el número de agente del sistema destino)")
    at = datetime.now(UTC)
    por = (actor or "").lower() or None
    _exec(
        "INSERT INTO operaciones.senebis_agentes "
        "(nombre, numero, creado_por, creado_at, actualizado_por, actualizado_at) "
        "VALUES (%(n)s, %(num)s, %(por)s, %(at)s, %(por)s, %(at)s) "
        "ON CONFLICT (nombre) DO UPDATE SET numero = EXCLUDED.numero, "
        "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at",
        {"n": n, "num": num, "por": por, "at": at},
    )
    _audit(actor, "upsert_agente", n, {"numero": num})
    return {"nombre": n, "numero": num}


def quitar_agente(nombre: str, actor: str) -> dict:
    n = (nombre or "").strip().upper()
    if not n:
        raise ValueError("falta 'nombre'")
    borrado = _exec("DELETE FROM operaciones.senebis_agentes WHERE nombre = %(n)s", {"n": n})
    _audit(actor, "remove_agente", n, {})
    return {"borrado": borrado}


def opciones(email: str = "") -> dict:
    """Opciones del form de carga: catálogo de agentes (desplegable del
    externo) + valores válidos. Marca presencia del caller."""
    if email:
        marcar_presencia(email)
    return {
        "agentes": listar_agentes(),
        "tipos_contraparte": list(TIPOS_CONTRAPARTE),
        "plazos": list(PLAZOS),
        "conectados": conectados(),
    }


# ─────────────────────────────────────────────────────────────
# Comitentes (clientes de la ALyC) — búsqueda y resolución
# ─────────────────────────────────────────────────────────────

def _denominacion_limpia(d: str | None) -> str | None:
    """clientes.cuentas.denominacion viene '[805] NOMBRE' o 'NOMBRE' a secas."""
    s = (d or "").strip()
    if s.startswith("[") and "]" in s:
        s = s.split("]", 1)[1].strip()
    return s or None


def buscar_comitentes(q: str = "", limit: int = 20) -> list[dict]:
    """Autocomplete del form (interno): matchea por NÚMERO de cuenta o por
    DENOMINACIÓN, para que el trader cargue con lo que sepa de la cuenta."""
    term = (q or "").strip()
    if not term:
        return []
    rows = _q(
        "SELECT id_cuenta, denominacion FROM clientes.cuentas "
        "WHERE id_cuenta ILIKE %(pref)s OR denominacion ILIKE %(sub)s "
        "ORDER BY id_cuenta LIMIT %(lim)s",
        {"pref": f"{term}%", "sub": f"%{term}%", "lim": int(limit)},
    )
    return [{"id_cuenta": r["id_cuenta"],
             "denominacion": _denominacion_limpia(r["denominacion"])} for r in rows]


def _buscar_cuenta_exacta(term: str) -> dict | None:
    """Cuenta cuyo id_cuenta O denominación matchea EXACTO (case-insensitive).
    Devuelve {id_cuenta, denominacion} o None si no hay match único."""
    rows = _q(
        "SELECT id_cuenta, denominacion FROM clientes.cuentas "
        "WHERE id_cuenta = %(t)s "
        "   OR upper(denominacion) = upper(%(t)s) "
        "   OR upper(denominacion) LIKE upper(%(brack)s) LIMIT 2",
        {"t": term, "brack": f"[%] {term}"},
    )
    if len(rows) != 1:
        return None  # 0 = no existe; 2+ = ambiguo → se guarda lo tipeado tal cual
    return {"id_cuenta": rows[0]["id_cuenta"],
            "denominacion": _denominacion_limpia(rows[0]["denominacion"])}


def _resolver_interno(cc: str | None) -> tuple[str | None, str | None]:
    """(cc, cc_denominacion) para un senebi interno: si lo tipeado matchea una
    cuenta (por número o denominación) se normaliza al NÚMERO + snapshot de la
    denominación; si no matchea queda lo tipeado tal cual (texto libre)."""
    term = (str(cc or "")).strip()
    if not term:
        return None, None
    hit = _buscar_cuenta_exacta(term)
    if hit:
        return hit["id_cuenta"], hit["denominacion"]
    return term, None


# ─────────────────────────────────────────────────────────────
# Órdenes (CRUD + estado)
# ─────────────────────────────────────────────────────────────

def _fila_op(r: dict) -> dict:
    return {
        "id": r["id"],
        "operacion": r["operacion"],
        "concertacion": r["concertacion"].isoformat() if r["concertacion"] else None,
        "liquidacion": r["liquidacion"].isoformat() if r["liquidacion"] else None,
        "plazo": r["plazo"],
        "especie": r["especie"],
        "vn": _f(r["vn"]), "px": _f(r["px"]), "monto": _f(r["monto"]),
        "cp": r["cp"], "cc": r["cc"], "cc_denominacion": r["cc_denominacion"],
        "contraparte": r["contraparte"], "nro_contraparte": r["nro_contraparte"],
        "mercado": r["mercado"],
        "cargan_ellos": r["cargan_ellos"], "tipo": r["tipo"],
        "tipo_contraparte": r["tipo_contraparte"],
        "agente": r["agente"], "agente_numero": r["agente_numero"],
        "estado": r["estado"],
        "completada_por": r["completada_por"],
        "completada_at": r["completada_at"].isoformat() if r["completada_at"] else None,
        "creado_por": r["creado_por"],
        "creado_at": r["creado_at"].isoformat() if r["creado_at"] else None,
        "actualizado_por": r["actualizado_por"],
    }


def listar_ops(desde: str | None = None, hasta: str | None = None,
               estado: str | None = None, especie: str | None = None,
               email: str = "") -> dict:
    """Órdenes del período (default: todas), más nuevas primero, + presencia.

    Cada lectura marca presencia del caller — pollear la lista ES el heartbeat:
    el front no necesita un ping aparte mientras la vista esté abierta."""
    if email:
        marcar_presencia(email)
    conds, params = [], {}
    if desde:
        conds.append("concertacion >= %(desde)s")
        params["desde"] = desde
    if hasta:
        conds.append("concertacion <= %(hasta)s")
        params["hasta"] = hasta
    if estado:
        if estado not in ESTADOS:
            raise ValueError(f"estado {estado!r} inválido: {' | '.join(ESTADOS)}")
        conds.append("estado = %(estado)s")
        params["estado"] = estado
    if especie:
        conds.append("especie ILIKE %(especie)s")
        params["especie"] = f"%{especie.strip()}%"
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = _q(
        f"SELECT * FROM operaciones.senebis {where} ORDER BY concertacion DESC, id DESC",
        params,
    )
    pendientes = sum(1 for r in rows if r["estado"] == "pendiente")
    return {
        "total": len(rows),
        "pendientes": pendientes,
        "ordenes": [_fila_op(r) for r in rows],
        "conectados": conectados(),
    }


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _derivar_monto(p: dict) -> float | None:
    """monto = vn × px / 100 (px viene cada 100 VN). Si viene `monto` explícito
    en el payload, se respeta (override manual)."""
    manual = _num(p.get("monto"))
    if manual is not None:
        return manual
    vn, px = _num(p.get("vn")), _num(p.get("px"))
    if vn is not None and px is not None:
        return vn * px / 100
    return None


def _norm_plazo(v: Any) -> str | None:
    """'ci'/'CI' → 'CI'; '24'/'24hs'/'24HS' → '24'. Otro valor → ValueError."""
    s = str(v or "").strip().upper().replace("HS", "").replace(" ", "")
    if not s:
        return None
    if s == "CI":
        return "CI"
    if s == "24":
        return "24"
    raise ValueError(f"plazo {v!r} inválido: CI | 24")


def _completar_fechas(p: dict) -> dict:
    """Defaults + inferencia plazo ↔ liquidacion. Devuelve
    {concertacion, liquidacion, plazo} listos para persistir.

    - concertacion: default HOY (ART).
    - plazo sin liquidacion: CI → liq = concertacion; 24 → próximo hábil.
    - liquidacion sin plazo: mismo día → CI; posterior → 24.
    - ninguno: CI liquidando hoy (el caso más común de la mesa; editable).
    """
    conc = p.get("concertacion") or datetime.now(_TZ_AR).date().isoformat()
    conc_d = date.fromisoformat(str(conc))
    plazo = _norm_plazo(p.get("plazo"))
    liq = p.get("liquidacion") or None
    if liq:
        liq_d = date.fromisoformat(str(liq))
        if liq_d < conc_d:
            raise ValueError("'liquidacion' no puede ser anterior a 'concertacion'")
        if plazo is None:
            plazo = "CI" if liq_d == conc_d else "24"
    else:
        if plazo is None:
            plazo = "CI"
        liq_d = conc_d if plazo == "CI" else proximo_habil(conc_d)
    return {"concertacion": conc_d.isoformat(), "liquidacion": liq_d.isoformat(),
            "plazo": plazo}


def _validar(p: dict) -> None:
    op = (p.get("operacion") or "").strip().upper()
    if op not in ("COMPRA", "VENTA"):
        raise ValueError("'operacion' tiene que ser COMPRA o VENTA")
    if not (p.get("especie") or "").strip():
        raise ValueError("falta 'especie'")
    tc = (p.get("tipo_contraparte") or "interno").strip().lower()
    if tc not in TIPOS_CONTRAPARTE:
        raise ValueError(
            f"tipo_contraparte {tc!r} inválido: {' | '.join(TIPOS_CONTRAPARTE)}")
    if tc == "externo" and not (p.get("agente") or "").strip():
        raise ValueError("senebi externo: falta 'agente' (elegirlo del catálogo)")


def _row_de_payload(p: dict, actor: str) -> dict:
    fechas = _completar_fechas(p)
    tc = (p.get("tipo_contraparte") or "interno").strip().lower()
    if tc == "externo":
        agente = (p.get("agente") or "").strip().upper()
        numero = _numero_agente(agente)
        if numero is None:
            raise ValueError(
                f"agente {agente!r} no está en el catálogo — el back office lo "
                "agrega con su número desde la vista SENEBIS")
        cc, cc_den = None, None
    else:
        agente, numero = None, None
        cc, cc_den = _resolver_interno(p.get("cc"))
    return {
        "operacion": (p["operacion"] or "").strip().upper(),
        **fechas,
        "especie": (p.get("especie") or "").strip().upper(),
        "vn": _num(p.get("vn")), "px": _num(p.get("px")),
        "monto": _derivar_monto(p),
        "cp": (str(p.get("cp") or "").strip()) or "255",   # default cartera propia 255
        "cc": cc, "cc_denominacion": cc_den,
        "contraparte": (p.get("contraparte") or "").strip() or None,
        "nro_contraparte": (str(p.get("nro_contraparte") or "").strip()) or None,
        "mercado": (p.get("mercado") or "").strip().upper() or None,
        "cargan_ellos": (p.get("cargan_ellos") or "").strip() or None,
        "tipo": (p.get("tipo") or "").strip() or None,
        "tipo_contraparte": tc, "agente": agente, "agente_numero": numero,
        "por": (actor or "").lower() or None, "at": datetime.now(UTC),
    }


def crear_op(payload: dict, actor: str) -> dict:
    p = {k: payload.get(k) for k in _CAMPOS_OP}
    _validar(p)
    row = _row_de_payload(p, actor)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operaciones.senebis "
            "(operacion, concertacion, liquidacion, plazo, especie, vn, px, monto, "
            " cp, cc, cc_denominacion, contraparte, nro_contraparte, mercado, "
            " cargan_ellos, tipo, tipo_contraparte, agente, agente_numero, "
            " estado, creado_por, creado_at, actualizado_por, actualizado_at) "
            "VALUES (%(operacion)s, %(concertacion)s, %(liquidacion)s, %(plazo)s, "
            " %(especie)s, %(vn)s, %(px)s, %(monto)s, %(cp)s, %(cc)s, "
            " %(cc_denominacion)s, %(contraparte)s, %(nro_contraparte)s, %(mercado)s, "
            " %(cargan_ellos)s, %(tipo)s, %(tipo_contraparte)s, %(agente)s, "
            " %(agente_numero)s, 'pendiente', %(por)s, %(at)s, %(por)s, %(at)s) "
            "RETURNING id",
            row,
        )
        op_id = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "create_op", str(op_id), {"after": row})
    return _get_op(op_id)


def _get_op(op_id: int) -> dict:
    rows = _q("SELECT * FROM operaciones.senebis WHERE id = %(id)s", {"id": op_id})
    if not rows:
        raise ValueError(f"orden {op_id} no existe")
    return _fila_op(rows[0])


def editar_op(op_id: int, payload: dict, actor: str) -> dict:
    before = _get_op(op_id)
    # Merge: lo que no viene en el payload se mantiene; el monto se recalcula
    # salvo que venga un override manual REAL (distinto del monto ya guardado —
    # si el front solo echoa la fila, se re-deriva con los vn/px nuevos).
    p = {k: (payload[k] if k in payload else before.get(k)) for k in _CAMPOS_OP}
    if _num(p.get("monto")) == before.get("monto"):
        p["monto"] = None
    _validar(p)
    row = {"id": op_id, **_row_de_payload(p, actor)}
    _exec(
        "UPDATE operaciones.senebis SET operacion=%(operacion)s, "
        "concertacion=%(concertacion)s, liquidacion=%(liquidacion)s, "
        "plazo=%(plazo)s, especie=%(especie)s, vn=%(vn)s, px=%(px)s, "
        "monto=%(monto)s, cp=%(cp)s, cc=%(cc)s, cc_denominacion=%(cc_denominacion)s, "
        "contraparte=%(contraparte)s, "
        "nro_contraparte=%(nro_contraparte)s, mercado=%(mercado)s, "
        "cargan_ellos=%(cargan_ellos)s, tipo=%(tipo)s, "
        "tipo_contraparte=%(tipo_contraparte)s, agente=%(agente)s, "
        "agente_numero=%(agente_numero)s, "
        "actualizado_por=%(por)s, actualizado_at=%(at)s WHERE id=%(id)s",
        row,
    )
    after = _get_op(op_id)
    _audit(actor, "update_op", str(op_id), {"before": before, "after": after})
    return after


def borrar_op(op_id: int, actor: str) -> dict:
    before = _get_op(op_id)
    n = _exec("DELETE FROM operaciones.senebis WHERE id = %(id)s", {"id": op_id})
    _audit(actor, "delete_op", str(op_id), {"before": before})
    return {"borrado": n}


def set_estado(op_id: int, estado: str, actor: str) -> dict:
    """Marca 'completada' (con quién y cuándo) o vuelve a 'pendiente'."""
    if estado not in ESTADOS:
        raise ValueError(f"estado {estado!r} inválido: {' | '.join(ESTADOS)}")
    before = _get_op(op_id)
    if before["estado"] == estado:
        return before  # idempotente: dos clicks simultáneos no duplican audit
    por = (actor or "").lower() or None
    at = datetime.now(UTC)
    _exec(
        "UPDATE operaciones.senebis SET estado=%(estado)s, "
        "completada_por=%(comp_por)s, completada_at=%(comp_at)s, "
        "actualizado_por=%(por)s, actualizado_at=%(at)s WHERE id=%(id)s",
        {"id": op_id, "estado": estado,
         "comp_por": por if estado == "completada" else None,
         "comp_at": at if estado == "completada" else None,
         "por": por, "at": at},
    )
    after = _get_op(op_id)
    _audit(actor, "set_estado", str(op_id), {"before": before, "after": after})
    return after


# ─────────────────────────────────────────────────────────────
# Export Excel (el archivo que se carga en el sistema destino)
# ─────────────────────────────────────────────────────────────

def _numero_si_se_puede(v: Any) -> Any:
    """El sistema destino espera número en CONTRAPARTE/COMITENTE/CARTERA PROPIA;
    si el valor es texto (cuenta sin resolver) queda texto y el back office lo
    corrige a mano."""
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return v


def _fila_export(o: dict) -> list:
    """Una orden → los 10 valores del Excel destino. Acá viven las reglas de
    contraparte (2026-08-05):
        externo             → COMITENTE vacío, CONTRAPARTE = número del agente.
        interno GARANTIZADO → COMITENTE = cp (cartera propia), CONTRAPARTE vacío.
        interno resto       → COMITENTE = cc, CONTRAPARTE vacío."""
    if o["tipo_contraparte"] == "externo":
        contraparte = _numero_si_se_puede(o["agente_numero"])
        comitente = None
    else:
        contraparte = None
        fuente = o["cp"] if (o["mercado"] or "") == "GARANTIZADO" else o["cc"]
        comitente = _numero_si_se_puede(fuente)
    return [o["id"], o["operacion"], o["especie"], o["plazo"], o["px"], o["vn"],
            contraparte, comitente, _numero_si_se_puede(o["cp"]), o["mercado"]]


def excel_preview(desde: str | None = None, hasta: str | None = None,
                  estado: str | None = None, email: str = "") -> dict:
    """Espejo EN VIVO del Excel destino para la tab EXCEL QUANTEX: mismas filas
    y mismas reglas que export_xlsx (una sola fuente de verdad: _fila_export),
    en JSON. `estado` por fila para pintar pendiente/completada en el front."""
    data = listar_ops(desde=desde, hasta=hasta, estado=estado, email=email)
    ordenes = sorted(data["ordenes"], key=lambda o: o["id"])
    return {
        "headers": list(_HEADERS_XLSX),
        "filas": [{"id": o["id"], "estado": o["estado"], "valores": _fila_export(o)}
                  for o in ordenes],
        "conectados": data["conectados"],
    }


def export_xlsx(desde: str | None = None, hasta: str | None = None,
                estado: str | None = None) -> tuple[bytes, str]:
    """Devuelve (bytes del .xlsx, nombre de archivo) con las órdenes filtradas,
    columnas EXACTAS del sistema destino (ID primero). Las órdenes van más
    viejas primero (por ID asc = orden de carga)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError as e:  # lazy: sin openpyxl la API sigue viva (REGLA #1)
        raise RuntimeError(
            "openpyxl no está instalado — correr `pip install -r requirements.txt` "
            "en el venv del Droplet") from e

    data = listar_ops(desde=desde, hasta=hasta, estado=estado)
    ordenes = sorted(data["ordenes"], key=lambda o: o["id"])

    wb = Workbook()
    ws = wb.active
    ws.title = "SENEBIS"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    for col, titulo in enumerate(_HEADERS_XLSX, start=1):
        cell = ws.cell(row=1, column=col, value=titulo)
        cell.font = header_font
        cell.fill = header_fill
    ws.freeze_panes = "A2"

    for i, o in enumerate(ordenes, start=2):
        for col, valor in enumerate(_fila_export(o), start=1):
            ws.cell(row=i, column=col, value=valor)

    # Anchos razonables para abrir y leer sin acomodar nada.
    anchos = (8, 12, 14, 8, 14, 16, 16, 14, 15, 18)
    for col, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = ancho

    buf = BytesIO()
    wb.save(buf)
    hoy = datetime.now(_TZ_AR).strftime("%Y%m%d")
    sufijo = f"_{estado}" if estado else ""
    return buf.getvalue(), f"senebis_{hoy}{sufijo}.xlsx"
