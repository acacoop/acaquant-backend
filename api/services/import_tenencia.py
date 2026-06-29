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
from datetime import datetime, timedelta

from core.postgres import get_pool

_DB, _COL = "Valuaciones", "AuM"
_MAX_ROWS = 50_000  # tope de seguridad por request

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# DD/MM/YYYY tolerante: día/mes de 1 o 2 dígitos, separador / - o .
_DMY = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$")
# Sólo dígitos (con o sin '.0') → posible serial de Excel.
_SERIAL = re.compile(r"^\d{4,6}(?:\.0+)?$")
_EXCEL_EPOCH = datetime(1899, 12, 30)  # día 0 de Excel (incluye el bug del 1900)


def _serial_excel(n: float) -> str | None:
    """Serial de Excel (celda con formato Fecha) → YYYY-MM-DD. Acotado al rango
    2000-2100 (~36526..73050) para no confundir un precio/cantidad con una fecha."""
    if not (20000 <= n <= 80000):
        return None
    try:
        return (_EXCEL_EPOCH + timedelta(days=round(n))).strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def _norm_fecha(v) -> str | None:
    """Devuelve YYYY-MM-DD; None si inválida. Acepta:
      - 'YYYY-MM-DD' (con o sin hora detrás)
      - 'D/M/YYYY' / 'D-M-YYYY' / 'D.M.YYYY' (día y mes de 1 o 2 dígitos)
      - serial de Excel: número (46142) o texto ('46142'/'46142.0') — el caso
        típico cuando la columna Fecha quedó con formato fecha y no como texto
      - datetime nativo (por si el parser lo entrega ya tipado)."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float)):
        return _serial_excel(float(v))
    s = str(v).strip()
    if not s:
        return None
    if _SERIAL.match(s):
        return _serial_excel(float(s))
    head = s[:10]
    if _ISO.match(head):
        try:
            datetime.strptime(head, "%Y-%m-%d")
            return head
        except ValueError:
            return None
    m = _DMY.match(s)
    if m:
        d, mo, y = m.groups()
        try:
            return datetime.strptime(f"{y}-{int(mo):02d}-{int(d):02d}", "%Y-%m-%d").strftime(
                "%Y-%m-%d"
            )
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
    """unidad → {tipoTitulo, cartera} desde SQL portafolio.assets (para enriquecer
    tipoTitulo y validar match de cartera)."""
    out: dict[str, dict] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT unidad, instrumento, clase_activo, cartera FROM portafolio.assets")
        for unidad, instrumento, clase, cartera in cur.fetchall():
            if unidad:
                out[unidad] = {
                    "tipoTitulo": (instrumento or clase or "").strip(),
                    "cartera":    (cartera or "").strip(),
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

    # DEPRECADO (decomiso 2026-06-29): este commit escribía Valuaciones.AuM (Mongo, DROPEADA).
    # El import vivo es SQL → /import-aum-sql y /import-precios-sql (import_tenencia_sql.py →
    # portafolio.tenencia). La PREVIEW (commit=False, arriba) sigue sirviendo; el commit Mongo
    # se bloquea para no recrear AuM. Este módulo se conserva solo por _norm_fecha/_num (los
    # reusa import_tenencia_sql).
    raise RuntimeError(
        "import_tenencia.importar(commit=True) DEPRECADO (escribía Mongo Valuaciones.AuM) — "
        "usar /import-aum-sql (SQL portafolio.tenencia).")
