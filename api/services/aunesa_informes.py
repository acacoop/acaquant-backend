"""api/services/aunesa_informes.py — aranceles por boleto desde Aunesa /operaciones/informes.

Es el ÚNICO dato que `CashFlow.NegocioMovimientos` no tiene: `consolidadosGenerales`
(la fuente del negocio) no trae el arancel; `/operaciones/informes` sí. Este
service pide informes por cuenta y devuelve {boleto: {moneda: arancel}}, para
enriquecer los boletos por `boleto == comprobante`.

Detalles aprendidos del response real (diag con cuentas 805 / 1346):
  - informes es multi-fila por boleto (una por ejecución); el arancel se repite
    IDÉNTICO en cada fila → se toma una vez por boleto, NO se suman las filas.
  - El `Monto` viene con formato inconsistente: `aranceles` con punto decimal
    ("1102.80") pero otros campos con coma argentina ("1338,07") → parser robusto.
  - `aranceles` casi siempre en ARS (aun en operaciones USD).
"""
from __future__ import annotations

from core import aunesa

INFORMES_PATH = "operaciones/informes"


def parse_monto(x: object) -> float:
    """Parsea un Monto de Aunesa tolerando los dos formatos que devuelve:
    con coma → argentino (puntos = miles, coma = decimal); sin coma → el punto
    es decimal (float directo). String vacío / inválido → 0.0."""
    s = str(x or "").strip()
    if not s:
        return 0.0
    try:
        if "," in s:
            return float(s.replace(".", "").replace(",", "."))
        return float(s)
    except ValueError:
        return 0.0


def _aranceles_de_item(item: dict) -> dict[str, float]:
    """{moneda: arancel} del array `aranceles` de un boleto (ignora filas vacías)."""
    out: dict[str, float] = {}
    for row in item.get("aranceles") or []:
        if not isinstance(row, dict):
            continue
        mon = (row.get("Moneda") or "").strip()
        val = parse_monto(row.get("Monto"))
        if mon and val:
            out[mon] = round(out.get(mon, 0.0) + val, 2)
    return out


def aranceles_por_boleto(
    cuenta: str, fecha_desde: str, fecha_hasta: str,
) -> dict[str, dict[str, float]]:
    """Mapa {boleto: {moneda: arancel}} para una cuenta.

    `fecha_desde`/`fecha_hasta` en dd/mm/yyyy = Fecha LIQUIDACIÓN (obligatorias
    al consultar por cuenta, según el doc del endpoint). El arancel se toma una
    vez por boleto (las filas de un mismo boleto traen el mismo arancel).
    """
    resp = aunesa.get(INFORMES_PATH, {
        "cuenta": str(cuenta),
        "fechaDesde": fecha_desde,
        "fechaHasta": fecha_hasta,
    })
    if resp.status_code == 204:
        return {}
    resp.raise_for_status()
    body = (resp.text or "").strip()
    data = resp.json() if body else []
    if not isinstance(data, list):
        return {}

    out: dict[str, dict[str, float]] = {}
    for it in data:
        if not isinstance(it, dict):
            continue
        bol = it.get("boleto")
        if not bol:
            continue
        ar = _aranceles_de_item(it)
        if ar:
            out[bol] = ar   # idéntico por fila → el último gana (mismo valor)
    return out
