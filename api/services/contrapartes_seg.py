"""api/services/contrapartes_seg.py — segmentación + conciliador de `clientes.contrapartes` (SQL).

Backend de la vista MANAGER → CONTRAPARTES (módulo `manager_contrapartes`). **Fuente única
SQL** (Mongo CashFlow.Contrapartes deprecado). Servicio puro (sin FastAPI).

Tabla `clientes.contrapartes` (clave = `id_cuenta`):
  {id_cuenta, denominacion (de Aunesa), contraparte (editable), segmento (editable),
   origen, actualizado_por/at}. `denominacion` se prefiere de la fila; si falta, del JOIN
   a `cuentas`.

⚠️ Efectos colaterales (INTENCIONALES) al AGREGAR una contraparte — el front avisa:
  - la cuenta SALE del AuM (jobs/_aum_filters.py reglas 3 y 4: match por id o por nombre).
  - su nivel_3 pasa a "PJ GRANDE" (api/services/segmentacion.py::clasificar_nivel_3).

Conciliador: lista cuentas que están en Aunesa (`cuentas/listadoCuentas`) pero NO en
contrapartes, y cuya `denominacion` contiene el nombre de alguna contraparte conocida.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from api.services._sql import _q
from api.services.segmentacion import _TIPOS_PH  # tipo_cliente de persona física
from core import aunesa
from core.postgres import get_pool

# Valores de `contraparte` que no son nombres reales (mismo criterio que _aum_filters).
_PLACEHOLDERS: frozenset[str] = frozenset({"", "NO APLICA", "N/A", "NONE", "NULL", "-"})
_MIN_KEYWORD_LEN = 3
# denominacion preferida: la de la fila; si NULL, la del JOIN a cuentas.
_DEN = "COALESCE(c.denominacion, u.denominacion)"


def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _s(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def inferir_segmento(denominacion: str | None, contraparte: str | None) -> str | None:
    """Sugerencia de segmento (FCI→Fondos, ALYC, BANCO→Bancos)."""
    den = (denominacion or "").upper()
    cp = (contraparte or "").upper()
    if "FCI" in den:
        return "Fondos"
    if "ALYC" in cp:
        return "ALYC"
    if "BANCO" in den:
        return "Bancos"
    return None


def _norm_keyword(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if not s or s in _PLACEHOLDERS or len(s) < _MIN_KEYWORD_LEN:
        return None
    return s


def listar_contrapartes(*, segmento: str | None = None, contraparte: str | None = None,
                        q: str | None = None) -> dict:
    """Lista las contrapartes (panel izquierdo). Filtros por segmento/contraparte exactos
    y `q` (cada token en denominacion o cuenta, AND sin orden)."""
    where = ["c.id_cuenta IS NOT NULL"]
    p: dict = {}
    if segmento:
        where.append("c.segmento = %(seg)s")
        p["seg"] = segmento
    if contraparte:
        where.append("c.contraparte = %(cp)s")
        p["cp"] = contraparte
    for i, tok in enumerate((q or "").split()):
        where.append(f"({_DEN} ILIKE %(t{i})s OR c.id_cuenta ILIKE %(t{i})s)")
        p[f"t{i}"] = f"%{tok}%"
    rows = _q(
        f"SELECT c.id_cuenta AS cuenta, {_DEN} AS denominacion, c.contraparte, c.segmento "
        f"FROM contrapartes c LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
        f"WHERE {' AND '.join(where)} ORDER BY denominacion NULLS LAST LIMIT 5000", p)
    return {"contrapartes": [
        {"cuenta": _s(r["cuenta"]), "denominacion": _s(r["denominacion"]),
         "contraparte": _s(r["contraparte"]), "segmento": _s(r["segmento"])}
        for r in rows], "n": len(rows)}


def segmentos_distinct() -> dict:
    """Valores distintos de segmento + contraparte para los datalist (autocomplete)."""
    def _clean(rows: list[dict], col: str) -> list[str]:
        return sorted({
            r[col].strip() for r in rows
            if isinstance(r[col], str) and r[col].strip()
            and r[col].strip().upper() not in _PLACEHOLDERS})
    segs = _q("SELECT DISTINCT segmento FROM contrapartes WHERE segmento IS NOT NULL")
    cps = _q("SELECT DISTINCT contraparte FROM contrapartes WHERE contraparte IS NOT NULL")
    return {"segmentos": _clean(segs, "segmento"), "contrapartes": _clean(cps, "contraparte")}


def update_contraparte(*, cuenta: str, contraparte: str | None = None,
                       segmento: str | None = None, actor: str | None = None) -> dict:
    """Edita contraparte/segmento de una cuenta existente. El router traduce
    updated=False/reason a 400/404."""
    cuenta = _s(cuenta) or ""
    if not cuenta:
        return {"updated": False, "reason": "cuenta_vacia"}
    sets: dict[str, Any] = {}
    if contraparte is not None:
        sets["contraparte"] = _s(contraparte)
    if segmento is not None:
        sets["segmento"] = _s(segmento)
    if not sets:
        return {"updated": False, "reason": "sin_campos"}
    sets["actualizado_por"] = actor
    sets["actualizado_at"] = datetime.now(UTC)
    cols = ", ".join(f"{k} = %({k})s" for k in sets)
    n = _exec(f"UPDATE contrapartes SET {cols} WHERE id_cuenta = %(idc)s",
              {**sets, "idc": cuenta})
    if n == 0:
        return {"updated": False, "reason": "not_found"}
    row = _q(f"SELECT c.id_cuenta AS cuenta, {_DEN} AS denominacion, c.contraparte, c.segmento "
             f"FROM contrapartes c LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
             f"WHERE c.id_cuenta = %(idc)s", {"idc": cuenta})
    d = row[0] if row else {}
    return {"updated": True, "cuenta": str(d.get("cuenta")), "denominacion": d.get("denominacion"),
            "contraparte": d.get("contraparte"), "segmento": d.get("segmento")}


def add_contraparte(*, cuenta: str, denominacion: str | None, contraparte: str | None,
                    segmento: str | None, actor: str | None = None) -> dict:
    """Alta de 1 click desde el conciliador. Idempotente (upsert por id_cuenta)."""
    cuenta = _s(cuenta) or ""
    if not cuenta:
        return {"added": False, "reason": "cuenta_vacia"}
    p = {"idc": cuenta, "den": _s(denominacion), "cp": _s(contraparte),
         "seg": _s(segmento), "por": actor, "at": datetime.now(UTC)}
    _exec(
        "INSERT INTO contrapartes (id_cuenta, denominacion, contraparte, segmento, origen, "
        "actualizado_por, actualizado_at) "
        "VALUES (%(idc)s, %(den)s, %(cp)s, %(seg)s, 'reconciler', %(por)s, %(at)s) "
        "ON CONFLICT (id_cuenta) DO UPDATE SET denominacion = EXCLUDED.denominacion, "
        "contraparte = EXCLUDED.contraparte, segmento = EXCLUDED.segmento, origen = 'reconciler', "
        "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at", p)
    return {"added": True, "cuenta": cuenta, "denominacion": p["den"],
            "contraparte": p["cp"], "segmento": p["seg"]}


def reconciliar(*, limit: int = 500) -> dict:
    """Conciliador (panel derecho): cuentas de Aunesa que NO están en contrapartes y cuya
    `denominacion` matchea el nombre de una contraparte conocida. Pega Aunesa LIVE."""
    existentes = {str(r["id_cuenta"]) for r in
                  _q("SELECT id_cuenta FROM contrapartes WHERE id_cuenta IS NOT NULL "
                     "AND id_cuenta <> ''")}
    keywords: dict[str, str] = {}
    for r in _q("SELECT DISTINCT contraparte FROM contrapartes WHERE contraparte IS NOT NULL"):
        k = _norm_keyword(r["contraparte"])
        if k:
            keywords[k] = r["contraparte"].strip()
    kw_sorted = sorted(keywords, key=lambda x: (-len(x), x))
    kw_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in kw_sorted) + r")\b") if kw_sorted else None

    resp = aunesa.get("cuentas/listadoCuentas")
    if resp.status_code != 200:
        raise RuntimeError(f"Aunesa cuentas/listadoCuentas devolvió status {resp.status_code}")
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or []

    candidatos: list[dict] = []
    for c in data:
        if not isinstance(c, dict) or (c.get("estado") or "") != "Activa":
            continue
        tipo_cli = (c.get("disposicionesGenerales") or {}).get("tipoCliente")
        if tipo_cli in _TIPOS_PH:
            continue
        cid = c.get("id")
        if cid is None:
            continue
        cid = str(cid)
        if cid in existentes:
            continue
        den = c.get("denominacion") or ""
        m = kw_re.search(den.upper()) if kw_re else None
        if not m:
            continue
        match = m.group(1)
        candidatos.append({
            "cuenta": cid, "denominacion": den,
            "contraparte_sugerida": keywords[match],
            "segmento_sugerido": inferir_segmento(den, keywords[match]),
            "keyword": keywords[match], "tipo_cliente": tipo_cli,
        })
        if len(candidatos) >= limit:
            break
    candidatos.sort(key=lambda x: x["denominacion"] or "")
    return {"candidatos": candidatos, "n": len(candidatos)}
