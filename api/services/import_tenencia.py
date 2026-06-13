"""api/services/import_tenencia.py — import masivo de tenencia a Valuaciones.AuM.

Carga manual desde Excel (Manager → IMPORTAR TENENCIA) para CORREGIR el AuM en
fechas puntuales (típicamente fines de mes) cuyo dato automático quedó mal por el
corrimiento de fecha del job. Pisa SOLO las `(fecha, cuenta)` que vienen en el
archivo — idempotente, scopeado, sin scans de toda la colección (REGLA #4).

El Excel ya trae cantidad + precio + VALUACIÓN del sistema contable (la verdad):
se importa TAL CUAL, sin recalcular. El doc queda idéntico al que escribe
`jobs/aum.py` (mismos campos) → Carteras/AUM lo leen sin cambios. La CARTERA la
resuelve la vista uniendo con `Valuaciones.Assets` por `unidad` al leer: por eso
acá solo VALIDAMOS que la unidad exista en Assets y REPORTAMOS las que no (esas
igual se importan, pero van a agrupar como "OTROS" hasta que las cargues en Assets).

Cada doc lleva `origen="import_manual"` + `importado_por/at` → auditable y
distinguible del dato del job.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from core.mongo import get_mongo_client, get_mongo_client_read

_DB, _COL = "Valuaciones", "AuM"
_MAX_ROWS = 50_000  # tope de seguridad por request

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _norm_fecha(v) -> str | None:
    """Acepta YYYY-MM-DD o DD/MM/YYYY → devuelve YYYY-MM-DD; None si inválida."""
    s = str(v or "").strip()[:10]
    if _ISO.match(s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            return None
    m = _DMY.match(s)
    if m:
        d, mo, y = m.groups()
        try:
            datetime.strptime(f"{y}-{mo}-{d}", "%Y-%m-%d")
            return f"{y}-{mo}-{d}"
        except ValueError:
            return None
    return None


def _num(v) -> float | None:
    """Float tolerante: acepta number o string '1.234,56' / '1234.56'."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    # '1.234,56' (es-AR) → '1234.56'
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _assets_index() -> dict[str, dict]:
    """unidad → {tipoTitulo, cartera} desde Valuaciones.Assets (para enriquecer
    tipoTitulo y validar match de cartera)."""
    out: dict[str, dict] = {}
    cur = get_mongo_client_read()[_DB]["Assets"].find(
        {}, {"_id": 0, "unidad": 1, "CLASE_ACTIVO": 1, "CARTERA": 1, "INSTRUMENTO": 1})
    for a in cur:
        u = a.get("unidad")
        if u:
            out[u] = {
                "tipoTitulo": (a.get("INSTRUMENTO") or a.get("CLASE_ACTIVO") or "").strip(),
                "cartera":    (a.get("CARTERA") or "").strip(),
            }
    return out


def importar(rows: list[dict], actor: str, commit: bool = False) -> dict:
    """Valida (y opcionalmente aplica) un import de tenencia.

    rows: [{fecha, id_cuenta, cuenta?, unidad, cantidad, precio, valuacion, moneda?}]
    commit=False → solo previsualiza (no escribe). commit=True → pisa AuM.

    Devuelve un resumen con conteos, cuentas/fechas afectadas, especies sin match
    en Assets y los errores de validación por fila (con su número de fila).
    """
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "error": "archivo vacío o sin filas"}
    if len(rows) > _MAX_ROWS:
        return {"ok": False, "error": f"demasiadas filas ({len(rows)} > {_MAX_ROWS})"}

    assets = _assets_index()
    docs: list[dict] = []
    errores: list[dict] = []
    unmatched: set[str] = set()
    total_val = 0.0

    for i, r in enumerate(rows, 1):
        fecha = _norm_fecha(r.get("fecha"))
        idc = str(r.get("id_cuenta") or "").strip()
        if idc.endswith(".0"):           # Excel a veces manda 805.0
            idc = idc[:-2]
        unidad = str(r.get("unidad") or r.get("especie") or "").strip()
        cant = _num(r.get("cantidad"))
        prec = _num(r.get("precio"))
        val = _num(r.get("valuacion"))

        problemas = []
        if not fecha:
            problemas.append("fecha inválida (usá YYYY-MM-DD o DD/MM/YYYY)")
        if not idc.isdigit():
            problemas.append("id_cuenta inválido (debe ser numérico)")
        if not unidad:
            problemas.append("falta especie/unidad")
        if val is None:
            problemas.append("falta valuación")
        if problemas:
            errores.append({"fila": i, "detalle": "; ".join(problemas),
                            "valores": {"fecha": r.get("fecha"), "id_cuenta": r.get("id_cuenta"),
                                        "unidad": unidad}})
            continue

        a = assets.get(unidad)
        if not a:
            unmatched.add(unidad)
        cuenta = str(r.get("cuenta") or "").strip() or f"[{idc}]"
        moneda = (str(r.get("moneda") or "").strip() or None)

        docs.append({
            "id_cuenta":      idc,
            "unidad":         unidad,
            "tipoTitulo":     (a or {}).get("tipoTitulo", ""),
            "cuenta":         cuenta,
            "cantidad":       cant if cant is not None else 0.0,
            "precio":         prec if prec is not None else 0.0,
            "valuacion":      val,
            "fecha_snapshot": fecha,
            "moneda":         moneda,
            "origen":         "import_manual",
        })
        total_val += val

    cuentas = sorted({d["id_cuenta"] for d in docs})
    fechas = sorted({d["fecha_snapshot"] for d in docs})

    resumen = {
        "ok":              True,
        "commit":          commit,
        "n_filas":         len(rows),
        "n_validas":       len(docs),
        "n_errores":       len(errores),
        "errores":         errores[:200],
        "cuentas":         cuentas,
        "n_cuentas":       len(cuentas),
        "fechas":          fechas,
        "total_valuacion": round(total_val, 2),
        "especies_sin_match_en_assets": sorted(unmatched),
    }

    if not commit:
        return resumen

    if not docs:
        return {**resumen, "aplicado": False, "error": "no hay filas válidas para aplicar"}

    # ── Aplicar: por (fecha, cuenta) → delete + insert (idéntico al job) ──────
    ts = datetime.now(UTC)
    por_grupo: dict[tuple[str, str], list[dict]] = {}
    for d in docs:
        por_grupo.setdefault((d["fecha_snapshot"], d["id_cuenta"]), []).append(d)

    col = get_mongo_client()[_DB][_COL]
    borrados = insertados = 0
    for (fecha, idc), grupo in por_grupo.items():
        borrados += col.delete_many(
            {"id_cuenta": idc, "fecha_snapshot": fecha}).deleted_count
        for d in grupo:
            d["timestamp"] = ts
            d["importado_por"] = actor
            d["importado_en"] = ts
        col.insert_many(grupo, ordered=False)
        insertados += len(grupo)

    return {**resumen, "aplicado": True, "borrados": borrados, "insertados": insertados}
