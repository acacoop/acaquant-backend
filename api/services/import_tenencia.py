"""api/services/import_tenencia.py — parsers de celdas del import manual de tenencia.

Módulo de HELPERS puros, sin acceso a base. Lo consume `import_tenencia_sql.py`
(el import vivo: Manager → AUNESA → IMPORTAR, escribe SQL `portafolio.tenencia`
vía /import-aum-sql y /import-precios-sql).

Acá vive solo la normalización de lo que manda el Excel, que es la parte
delicada: fechas en 3 formatos distintos (ISO, DD/MM/YYYY y el serial numérico
que Excel emite cuando la celda tiene formato Fecha) y números en formato es-AR
('1.234,56'). Ver `_norm_fecha` / `_num`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

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
