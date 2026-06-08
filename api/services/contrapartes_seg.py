"""api/services/contrapartes_seg.py — segmentación + conciliador de CashFlow.Contrapartes.

Backend de la vista MANAGER → CONTRAPARTES (módulo `manager_contrapartes`). Mongo es la
FUENTE DE VERDAD; el espejo SQL (tabla `contrapartes`) se refresca en el próximo
`sync_postgres` (cron cada 20 min). Servicio puro (sin FastAPI).

Doc de Contrapartes (clave = `cuenta`, el id):
  {cuenta, denominacion (de Aunesa, read-only), contraparte (editable), segmento (editable)}

⚠️ Efectos colaterales (INTENCIONALES) al AGREGAR una contraparte — el front avisa:
  - la cuenta SALE del AuM (jobs/_aum_filters.py reglas 3 y 4: match por id o por nombre).
  - su nivel_3 pasa a "PJ GRANDE" (api/services/segmentacion.py::clasificar_nivel_3).

Conciliador: lista cuentas que están en Aunesa (`cuentas/listadoCuentas`) pero NO en
Contrapartes, y cuya `denominacion` contiene el nombre de alguna contraparte conocida
(keywords auto-derivadas de los `contraparte` distintos). Sirve para detectar cuentas
nuevas sin segmentar.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from api.services.segmentacion import _TIPOS_PH  # tipo_cliente de persona física (Persona/Empleado)
from core import aunesa
from core.mongo import get_mongo_client, get_mongo_client_read

# Valores de `contraparte` que no son nombres reales (mismo criterio que _aum_filters).
_PLACEHOLDERS: frozenset[str] = frozenset({"", "NO APLICA", "N/A", "NONE", "NULL", "-"})
# Mínimo de caracteres de una keyword para usarse en el match (evita falsos positivos).
_MIN_KEYWORD_LEN = 3
_PROJ = {"_id": 0, "cuenta": 1, "denominacion": 1, "contraparte": 1, "segmento": 1}


def _col_ro():
    return get_mongo_client_read()["CashFlow"]["Contrapartes"]


def _col_rw():
    return get_mongo_client()["CashFlow"]["Contrapartes"]


def _s(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _cuenta_match(cuenta: str) -> dict:
    """Match por `cuenta` tolerando que esté guardada como string o int."""
    if cuenta.isdigit():
        return {"cuenta": {"$in": [cuenta, int(cuenta)]}}
    return {"cuenta": cuenta}


def inferir_segmento(denominacion: str | None, contraparte: str | None) -> str | None:
    """Sugerencia de segmento. Réplica de jobs/segmento_contrapartes.inferir_segmento
    (para no depender de un script one-shot) — mantener en sync si cambian las reglas."""
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
    """Nombre de contraparte → keyword normalizada (upper, sin placeholders, len>=3).
    Mismo criterio que jobs/_aum_filters.load_contrapartes_names → detección y exclusión
    del AuM quedan alineadas."""
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if not s or s in _PLACEHOLDERS or len(s) < _MIN_KEYWORD_LEN:
        return None
    return s


def listar_contrapartes(*, segmento: str | None = None, contraparte: str | None = None,
                        q: str | None = None) -> dict:
    """Lista las contrapartes (panel izquierdo). Filtros opcionales por segmento /
    contraparte exactos y `q` (substring sobre denominacion o cuenta)."""
    filtro: dict[str, Any] = {"cuenta": {"$nin": [None, ""]}}
    if segmento:
        filtro["segmento"] = segmento
    if contraparte:
        filtro["contraparte"] = contraparte
    if q:
        rgx = {"$regex": re.escape(q), "$options": "i"}
        filtro["$or"] = [{"denominacion": rgx}, {"cuenta": rgx}]
    rows = [
        {"cuenta": str(d.get("cuenta")), "denominacion": d.get("denominacion"),
         "contraparte": d.get("contraparte"), "segmento": d.get("segmento")}
        for d in _col_ro().find(filtro, _PROJ).sort("denominacion", 1).limit(5000)
    ]
    return {"contrapartes": rows, "n": len(rows)}


def segmentos_distinct() -> dict:
    """Valores distintos de segmento + contraparte para los datalist (autocomplete)."""
    col = _col_ro()

    def _clean(vals: list) -> list[str]:
        return sorted({
            str(v).strip() for v in vals
            if isinstance(v, str) and v.strip() and str(v).strip().upper() not in _PLACEHOLDERS
        })

    return {"segmentos": _clean(col.distinct("segmento")),
            "contrapartes": _clean(col.distinct("contraparte"))}


def update_contraparte(*, cuenta: str, contraparte: str | None = None,
                       segmento: str | None = None, actor: str | None = None) -> dict:
    """Edita contraparte/segmento de una cuenta existente. Devuelve {updated, ...}.
    El router traduce updated=False/reason a 400/404."""
    cuenta = _s(cuenta) or ""
    if not cuenta:
        return {"updated": False, "reason": "cuenta_vacia"}
    set_fields: dict[str, Any] = {}
    if contraparte is not None:
        set_fields["contraparte"] = _s(contraparte)
    if segmento is not None:
        set_fields["segmento"] = _s(segmento)
    if not set_fields:
        return {"updated": False, "reason": "sin_campos"}
    set_fields["actualizado_por"] = actor
    set_fields["actualizado_at"] = datetime.now(UTC)
    res = _col_rw().update_one(_cuenta_match(cuenta), {"$set": set_fields})
    if res.matched_count == 0:
        return {"updated": False, "reason": "not_found"}
    doc = _col_ro().find_one(_cuenta_match(cuenta), _PROJ) or {}
    return {"updated": True, "cuenta": str(doc.get("cuenta")), "denominacion": doc.get("denominacion"),
            "contraparte": doc.get("contraparte"), "segmento": doc.get("segmento")}


def add_contraparte(*, cuenta: str, denominacion: str | None, contraparte: str | None,
                    segmento: str | None, actor: str | None = None) -> dict:
    """Alta de 1 click desde el conciliador. Idempotente (upsert por cuenta)."""
    cuenta = _s(cuenta) or ""
    if not cuenta:
        return {"added": False, "reason": "cuenta_vacia"}
    doc = {
        "cuenta": cuenta, "denominacion": _s(denominacion),
        "contraparte": _s(contraparte), "segmento": _s(segmento),
        "actualizado_por": actor, "actualizado_at": datetime.now(UTC), "origen": "reconciler",
    }
    _col_rw().update_one(_cuenta_match(cuenta), {"$set": doc}, upsert=True)
    return {"added": True, "cuenta": cuenta, "denominacion": doc["denominacion"],
            "contraparte": doc["contraparte"], "segmento": doc["segmento"]}


def reconciliar(*, limit: int = 500) -> dict:
    """Conciliador (panel derecho). Devuelve cuentas de Aunesa que NO están en
    Contrapartes y cuya `denominacion` matchea el nombre de una contraparte conocida.

    Pega Aunesa LIVE (cuentas/listadoCuentas) — on-demand, NO cachear ni pollear.
    """
    col = _col_ro()
    existentes = {
        str(d["cuenta"])
        for d in col.find({"cuenta": {"$nin": [None, ""]}}, {"_id": 0, "cuenta": 1})
        if d.get("cuenta") is not None
    }
    # keyword (upper) → nombre original, para sugerir la contraparte.
    keywords: dict[str, str] = {}
    for r in col.distinct("contraparte"):
        k = _norm_keyword(r)
        if k:
            keywords[k] = r.strip()
    kw_sorted = sorted(keywords, key=len, reverse=True)  # matchea el nombre más largo primero

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
        # Una contraparte NUNCA es persona física → se excluyen los tipo_cliente PH
        # (Persona / Empleado). Mismo criterio que la clasificación patrimonial
        # (segmentacion._TIPOS_PH). Acelera el conciliador y deja solo PJ/institucionales.
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
        den_up = den.upper()
        match = next((k for k in kw_sorted if k in den_up), None)
        if not match:
            continue
        candidatos.append({
            "cuenta": cid,
            "denominacion": den,
            "contraparte_sugerida": keywords[match],
            "segmento_sugerido": inferir_segmento(den, keywords[match]),
            "keyword": keywords[match],
            "tipo_cliente": tipo_cli,
        })
        if len(candidatos) >= limit:
            break
    candidatos.sort(key=lambda x: x["denominacion"] or "")
    return {"candidatos": candidatos, "n": len(candidatos)}
