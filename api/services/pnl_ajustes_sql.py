"""api/services/pnl_ajustes_sql.py — AJUSTES MANUALES del motor de PnL.

Eventos corporativos que NO generan boleto en Aunesa (splits de CEDEARs, canjes
de especie, posiciones pre-data) rompen el cost-basis de `pnl.py`: la cantidad
reconstruida de boletos queda desfasada de la tenencia real y el PnL no
realizado se hunde de mentira. Este módulo persiste esos ajustes en
`operaciones.pnl_ajustes` (tabla PROPIA — nunca filas en negocio_movimientos,
que la reconciliación horaria del job de ingesta anularía en silencio) y los
inyecta al stream cronológico de boletos como pseudo-boletos vía
`merge_ajustes_en_boletos` (lo llama `pnl_sql._deps_sql`).

Semántica (la matemática vive en pnl.py, ramas `ajuste_split`/`ajuste_cantidad`):
    split    → factor multiplica la cantidad viva. El costo total NO cambia
               (un split no crea ni destruye plata — baja el promedio por unidad).
    cantidad → delta con signo. >0 suma cantidad con costo opcional;
               <0 resta liberando costo proporcional SIN generar realizado.

Alcance: `id_cuenta=NULL` = GLOBAL → aplica a toda cuenta que tenga boletos del
ticker (un split se carga UNA vez y cubre a todos los tenedores). Con id_cuenta
aplica solo ahí, incluso si la cuenta no tiene boletos del ticker (caso
pre-data: el ajuste ESTABLECE la posición y su costo).

Permisos: escritura SOLO admin (default-deny, revalidado acá aunque el router
ya lo gatee — defensa en profundidad). Todo cambio queda en
`operaciones.pnl_ajustes_audit` con before/after. Servicio puro (sin FastAPI).
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import Any

from psycopg.types.json import Jsonb

from api.services._sql import _f, _q
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_TIPOS = ("split", "cantidad")
_MONEDAS = ("ARS", "USD", "USDC")
_RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Campos editables de un ajuste (el resto es derivado o metadata de auditoría).
_CAMPOS = ("tipo", "ticker", "id_cuenta", "fecha", "factor", "cantidad",
           "costo", "moneda", "nota", "activo")


def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


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


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Evento de auditoría — nunca rompe la operación principal."""
    try:
        _exec(
            "INSERT INTO operaciones.pnl_ajustes_audit (ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": actor or None, "action": action,
             "target": str(target), "data": Jsonb(_jsonable(data or {}))},
        )
    except Exception:
        logger.exception("pnl_ajustes: audit insert falló")


# ─────────────────────────────────────────────────────────────
# Permisos de escritura — SOLO admin (default-deny)
# ─────────────────────────────────────────────────────────────

def puede_escribir(email: str) -> bool:
    """True si el usuario puede escribir ajustes de PnL. Un ajuste mal cargado
    distorsiona el PnL de TODAS las cuentas de un ticker → solo admin. Si algún
    día hace falta delegar, el patrón es una allowlist `pnl_ajustes_escritores`
    como la de Mesa de Dinero — este es el único punto a tocar."""
    email_norm = (email or "").lower().strip()
    if not email_norm:
        return False
    from core.roles import get_user_role
    return get_user_role(email_norm) == "admin"


# ─────────────────────────────────────────────────────────────
# Lectura (vista + merge del motor)
# ─────────────────────────────────────────────────────────────

def listar(email: str = "") -> dict:
    """Todos los ajustes (activos e inactivos) para el ABM + flag de escritura.
    El front esconde la edición sin `puede_escribir`, pero el enforcement real
    es server-side en cada write."""
    rows = _q(
        "SELECT id, tipo, ticker, id_cuenta, fecha, factor, cantidad, costo, "
        "moneda, nota, activo, creado_por, creado_at, actualizado_por, actualizado_at "
        "FROM operaciones.pnl_ajustes ORDER BY fecha DESC, id DESC")
    tk = _tickers_conocidos()
    ajustes = []
    for r in rows:
        ajustes.append({
            "id": r["id"], "tipo": r["tipo"], "ticker": r["ticker"],
            "id_cuenta": r["id_cuenta"], "fecha": _iso(r["fecha"]),
            "factor": _f(r["factor"]), "cantidad": _f(r["cantidad"]),
            "costo": _f(r["costo"]), "moneda": r["moneda"], "nota": r["nota"],
            "activo": bool(r["activo"]),
            # Aviso de typo: el ticker no matchea el catálogo de assets. No
            # bloquea (los FCI matchean por CAFCI y puede haber tickers viejos),
            # pero un ajuste con ticker desconocido probablemente no pegue en nada.
            "ticker_conocido": r["ticker"] in tk,
            "creado_por": r["creado_por"], "creado_at": _iso(r["creado_at"]),
            "actualizado_por": r["actualizado_por"],
            "actualizado_at": _iso(r["actualizado_at"]),
        })
    return {"ajustes": ajustes, "puede_escribir": puede_escribir(email)}


def _tickers_conocidos() -> set[str]:
    """Match_keys válidos del catálogo: TICKER + CAFCI de portafolio.assets
    (misma fuente que _build_unidad_maps_sql). Tabla chica — 1 query."""
    out: set[str] = set()
    try:
        for r in _q("SELECT ticker, cafci FROM portafolio.assets"):
            for k in (r.get("ticker"), r.get("cafci")):
                k = (k or "").strip()
                if k:
                    out.add(k)
    except Exception:
        logger.exception("pnl_ajustes: no pude leer portafolio.assets")
    return out


def _iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else (str(d)[:10] if d else None)


def ajustes_activos(only_cuenta: str | None = None) -> list[dict]:
    """Ajustes ACTIVOS normalizados como pseudo-boletos para el motor.

    `only_cuenta` scopea igual que `_deps_sql`: trae los globales (id_cuenta
    NULL) + los de esa cuenta. La tabla es chica por diseño (un evento
    corporativo por fila) — 1 query siempre."""
    sql = ("SELECT id, tipo, ticker, id_cuenta, fecha, factor, cantidad, costo, "
           "moneda, nota FROM operaciones.pnl_ajustes WHERE activo")
    params: dict = {}
    if only_cuenta is not None:
        sql += " AND (id_cuenta IS NULL OR id_cuenta = %(idc)s)"
        params["idc"] = str(only_cuenta)
    sql += " ORDER BY fecha, id"
    out = []
    for r in _q(sql, params):
        factor = _f(r["factor"])
        nota = (r.get("nota") or "").strip()
        if r["tipo"] == "split":
            op = f"SPLIT ×{factor:g}" if factor else "SPLIT"
            categoria = "ajuste_split"
        else:
            op = "AJUSTE CANTIDAD"
            categoria = "ajuste_cantidad"
        if nota:
            op = f"{op} — {nota}"
        out.append({
            # Shape de boleto que espera _pnl_por_cuenta_core (+ extras).
            "fecha": _iso(r["fecha"]), "categoria": categoria, "op": op,
            "ticker": (r["ticker"] or "").strip(),
            "cantidad": _f(r["cantidad"]), "precio": None,
            # `importe` = costo del ajuste (solo tipo cantidad). El motor lo
            # pesifica con el MEP de `fecha` si la moneda no es ARS (mep=None
            # → fallback get_mep_for_date, mismo riel que un boleto sin mep).
            "importe": _f(r["costo"]), "moneda": r["moneda"] or "ARS", "mep": None,
            "comprobante": f"AJUSTE-{r['id']}", "factor": factor,
            "id_cuenta": str(r["id_cuenta"]) if r["id_cuenta"] is not None else None,
            "_orden": 0,   # ordena ANTES que los boletos del mismo día
        })
    return out


def merge_ajustes_en_boletos(
    boletos_by_id_cuenta: dict[str, list], ajustes: list[dict],
) -> dict[str, list]:
    """Inyecta los ajustes al stream cronológico de boletos de cada cuenta.

    Función PURA (testeable sin DB). Reglas:
      - Ajuste GLOBAL (id_cuenta None): entra SOLO en cuentas que ya tienen
        boletos de ese ticker. En las demás no hay cost-basis que corregir
        (qty_efectiva sale del AuM, que ya viene post-evento) y meterlo
        igual crearía state fantasma con n_movimientos>0 y qty 0.
      - Ajuste POR CUENTA: entra siempre en esa cuenta (aunque no tenga
        boletos del ticker — es la forma de ESTABLECER una posición pre-data).
      - Orden: (fecha, _orden, comprobante) — los ajustes llevan _orden=0 y los
        boletos 1, así el evento aplica ANTES de los boletos de su mismo día
        (un split es efectivo a la apertura: lo operado ese día ya es post-split).
    Muta y devuelve el mismo dict (los lists se reemplazan por copias ordenadas
    solo en las cuentas afectadas)."""
    if not ajustes:
        return boletos_by_id_cuenta

    tickers_por_cuenta: dict[str, set[str]] = {
        cid: {(b.get("ticker") or "").strip() for b in bs}
        for cid, bs in boletos_by_id_cuenta.items()
    }

    tocadas: set[str] = set()
    for aj in ajustes:
        ticker = (aj.get("ticker") or "").strip()
        if not ticker:
            continue
        idc = aj.get("id_cuenta")
        if idc is not None:
            destinos = [str(idc)]
        else:
            destinos = [cid for cid, tks in tickers_por_cuenta.items() if ticker in tks]
        for cid in destinos:
            boletos_by_id_cuenta.setdefault(cid, []).append(dict(aj))
            tocadas.add(cid)

    for cid in tocadas:
        boletos_by_id_cuenta[cid] = sorted(
            boletos_by_id_cuenta[cid],
            key=lambda b: (b.get("fecha") or "", b.get("_orden", 1),
                           b.get("comprobante") or ""),
        )
    return boletos_by_id_cuenta


# ─────────────────────────────────────────────────────────────
# Detección de desfases (candidatos a ajuste)
# ─────────────────────────────────────────────────────────────

def candidatos_desfase() -> dict:
    """(cuenta, ticker) donde los boletos NO reconcilian con la tenencia —
    los candidatos naturales a un ajuste. Lee `valuaciones.pnl_totales_cache`
    (ya trae completeness/qty_aum/qty_calc por posición, lo escribe el cron
    cada 30' en rueda) → costo ~0, sin recalcular PnL. El LATERAL extrae solo
    los campos chicos del jsonb (los boletos anidados no viajan).

    La joya: si el ratio qty_aum/qty_calc es ~constante entre todas las cuentas
    de un ticker (ej. 10.0), eso ES un split y el ratio ES el factor sugerido."""
    try:
        rows = _q(
            "SELECT c.id_cuenta, c.cuenta, r->>'ticker' AS ticker, "
            "       r->>'display_name' AS display_name, "
            "       (r->>'qty_aum')::float8 AS qty_aum, "
            "       (r->>'qty_calc')::float8 AS qty_calc "
            "FROM valuaciones.pnl_totales_cache c, "
            "     LATERAL jsonb_array_elements(c.rows) r "
            "WHERE r->>'completeness' = 'parcial'")
    except Exception:
        logger.exception("pnl_ajustes: no pude leer pnl_totales_cache")
        return {"candidatos": [], "por_ticker": []}

    candidatos = []
    por_ticker: dict[str, list[float]] = {}
    for r in rows:
        qa, qc = float(r.get("qty_aum") or 0), float(r.get("qty_calc") or 0)
        ratio = (qa / qc) if (qa != 0 and qc != 0) else None
        candidatos.append({
            "id_cuenta": r["id_cuenta"], "cuenta": r["cuenta"] or "",
            "ticker": r["ticker"] or "", "display_name": r["display_name"] or "",
            "qty_aum": qa, "qty_calc": qc,
            "ratio": round(ratio, 6) if ratio is not None else None,
        })
        if ratio is not None and ratio > 0 and r["ticker"]:
            por_ticker.setdefault(r["ticker"], []).append(ratio)

    resumen = []
    for tk, ratios in por_ticker.items():
        ratios.sort()
        mediana = ratios[len(ratios) // 2]
        # Consistente = todas las cuentas desfasadas en (casi) el mismo ratio →
        # huele a evento corporativo global, no a boletos sueltos faltantes.
        consistente = all(abs(x - mediana) <= 0.02 * mediana for x in ratios)
        resumen.append({
            "ticker": tk, "n_cuentas": len(ratios),
            "ratio_mediana": round(mediana, 6),
            "consistente": consistente,
            # Sugerencia de factor solo si el ratio es consistente y "redondo"
            # (un split real es 10:1, 2:1, 1:10... no 3.7182:1).
            "factor_sugerido": _factor_redondo(mediana) if consistente else None,
        })
    resumen.sort(key=lambda x: (-int(x["consistente"]), -x["n_cuentas"]))
    candidatos.sort(key=lambda x: (x["ticker"], x["cuenta"]))
    return {"candidatos": candidatos, "por_ticker": resumen}


def _factor_redondo(ratio: float) -> float | None:
    """Si el ratio es ≈ un entero N (split N:1) o ≈ 1/N (reverse 1:N), devuelve
    ese valor exacto como factor sugerido; si no, None (que decida el humano)."""
    if ratio <= 0:
        return None
    for n in range(2, 101):
        if abs(ratio - n) <= 0.02 * n:
            return float(n)
        if abs(ratio - 1.0 / n) <= 0.02 / n:
            return round(1.0 / n, 6)
    return None


# ─────────────────────────────────────────────────────────────
# Escritura (CRUD con audit)
# ─────────────────────────────────────────────────────────────

def _validar(p: dict) -> dict:
    """Normaliza y valida el payload. Levanta ValueError con mensaje claro."""
    tipo = (p.get("tipo") or "").strip().lower()
    if tipo not in _TIPOS:
        raise ValueError(f"tipo inválido: debe ser {' o '.join(_TIPOS)}")
    ticker = (p.get("ticker") or "").strip()
    if not ticker:
        raise ValueError("ticker requerido")
    fecha = (p.get("fecha") or "").strip()
    if not _RE_FECHA.match(fecha):
        raise ValueError("fecha inválida (YYYY-MM-DD)")
    id_cuenta = (str(p.get("id_cuenta")) if p.get("id_cuenta") not in (None, "") else None)
    if id_cuenta is not None:
        id_cuenta = id_cuenta.strip() or None

    factor = cantidad = costo = None
    if tipo == "split":
        try:
            factor = float(p.get("factor"))
        except (TypeError, ValueError):
            raise ValueError("factor requerido para un split") from None
        if factor <= 0:
            raise ValueError("factor debe ser > 0 (10 = split 10:1, 0.1 = reverse 1:10)")
        if factor == 1:
            raise ValueError("factor 1 no ajusta nada")
    else:
        try:
            cantidad = float(p.get("cantidad"))
        except (TypeError, ValueError):
            raise ValueError("cantidad requerida para un ajuste de cantidad") from None
        if cantidad == 0:
            raise ValueError("cantidad no puede ser 0")
        if p.get("costo") not in (None, ""):
            try:
                costo = abs(float(p.get("costo")))
            except (TypeError, ValueError):
                raise ValueError("costo inválido") from None
            if cantidad < 0 and costo:
                raise ValueError(
                    "un ajuste con cantidad negativa libera costo proporcional — no lleva costo")

    moneda = (p.get("moneda") or "ARS").strip().upper()
    if moneda not in _MONEDAS:
        raise ValueError(f"moneda inválida: debe ser {', '.join(_MONEDAS)}")
    activo = bool(p.get("activo", True))
    nota = (p.get("nota") or "").strip() or None

    return {"tipo": tipo, "ticker": ticker, "id_cuenta": id_cuenta, "fecha": fecha,
            "factor": factor, "cantidad": cantidad, "costo": costo,
            "moneda": moneda, "nota": nota, "activo": activo}


def _get(ajuste_id: int) -> dict | None:
    rows = _q("SELECT id, tipo, ticker, id_cuenta, fecha, factor, cantidad, costo, "
              "moneda, nota, activo FROM operaciones.pnl_ajustes WHERE id = %(id)s",
              {"id": ajuste_id})
    return rows[0] if rows else None


def crear(payload: dict, *, actor: str) -> dict:
    if not puede_escribir(actor):
        raise PermissionError("sin permiso de escritura en ajustes de PnL")
    v = _validar(payload)
    row = _q(
        "INSERT INTO operaciones.pnl_ajustes "
        "(tipo, ticker, id_cuenta, fecha, factor, cantidad, costo, moneda, nota, "
        " activo, creado_por, creado_at) "
        "VALUES (%(tipo)s, %(ticker)s, %(id_cuenta)s, %(fecha)s, %(factor)s, "
        "%(cantidad)s, %(costo)s, %(moneda)s, %(nota)s, %(activo)s, %(actor)s, now()) "
        "RETURNING id",
        {**v, "actor": actor},
    )[0]
    _audit(actor, "create", row["id"], {"after": v})
    return {"ok": True, "id": row["id"]}


def editar(ajuste_id: int, payload: dict, *, actor: str) -> dict:
    if not puede_escribir(actor):
        raise PermissionError("sin permiso de escritura en ajustes de PnL")
    before = _get(ajuste_id)
    if before is None:
        raise ValueError(f"ajuste {ajuste_id} no existe")
    # Merge parcial: lo que no viene en el payload conserva lo persistido.
    merged = {k: payload[k] if k in payload else before[k] for k in _CAMPOS}
    merged["fecha"] = _iso(merged["fecha"]) or ""
    v = _validar(merged)
    _exec(
        "UPDATE operaciones.pnl_ajustes SET tipo=%(tipo)s, ticker=%(ticker)s, "
        "id_cuenta=%(id_cuenta)s, fecha=%(fecha)s, factor=%(factor)s, "
        "cantidad=%(cantidad)s, costo=%(costo)s, moneda=%(moneda)s, nota=%(nota)s, "
        "activo=%(activo)s, actualizado_por=%(actor)s, actualizado_at=now() "
        "WHERE id=%(id)s",
        {**v, "actor": actor, "id": ajuste_id},
    )
    _audit(actor, "update", ajuste_id, {"before": before, "after": v})
    return {"ok": True, "id": ajuste_id}


def borrar(ajuste_id: int, *, actor: str) -> dict:
    if not puede_escribir(actor):
        raise PermissionError("sin permiso de escritura en ajustes de PnL")
    before = _get(ajuste_id)
    if before is None:
        raise ValueError(f"ajuste {ajuste_id} no existe")
    _exec("DELETE FROM operaciones.pnl_ajustes WHERE id = %(id)s", {"id": ajuste_id})
    _audit(actor, "delete", ajuste_id, {"before": before})
    return {"ok": True, "id": ajuste_id}
