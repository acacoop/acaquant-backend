"""api/services/tesoreria.py — Back Office → Tesorería (ingresos/egresos del día).

Fuente: Aunesa `GET cuentas/consultaMovDocsSolicitados` ("Movimientos y Documentos
Solicitados") — movimientos BANCARIOS de dinero (transferencias, transferencia MEP,
e-cheq). La DIRECCIÓN la da el campo `solicitud` (Depósito = ingreso / Extracción =
egreso), NO el signo del `monto` (siempre positivo). Plata efectiva = `estado`
'Procesado' (default). Modelo verificado por discovery 2026-07-03.

Puro (sin FastAPI): lo llama `api/routers/back_office.py`. Se sirve LIVE contra Aunesa
sin persistir — volumen chico (~cientos de mov/día). El histórico de saldos (si se
necesita) se congelará con un job aparte más adelante.
"""
from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from core import aunesa

_ENDPOINT = "cuentas/consultaMovDocsSolicitados"
# La dirección la da `solicitud`. Comparamos SIN acentos y en minúscula porque el string
# de Aunesa puede venir en NFC o NFD (la 'ó'/'ó' se ven iguales pero != por bytes) — comparar
# el literal acentuado directo descartaba TODAS las filas (200 OK con 0 resultados).
_INGRESO = "deposito"   # 'Depósito'
_EGRESO = "extraccion"  # 'Extracción'


def _norm(s: Any) -> str:
    """Minúscula + sin diacríticos, para comparar `solicitud` a prueba de NFC/NFD."""
    d = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in d if not unicodedata.combining(c)).strip().lower()


def _hoy_art() -> datetime:
    """Ahora en ART (UTC-3), sin depender de la tz del server."""
    return datetime.now(UTC) - timedelta(hours=3)


def _fechas(iso: str | None) -> tuple[str, str, str]:
    """(dd/mm/yyyy del día, dd/mm/yyyy del día+1, yyyymmdd del día) desde un ISO YYYY-MM-DD;
    default = hoy ART. El día+1 es para `liquidacionHasta`: Aunesa EXIGE desde < hasta
    (un rango de un solo día con desde==hasta tira 400), así que pedimos [día, día+1] y
    después filtramos las filas al día objetivo."""
    d = datetime.strptime(iso, "%Y-%m-%d").date() if iso else _hoy_art().date()
    return d.strftime("%d/%m/%Y"), (d + timedelta(days=1)).strftime("%d/%m/%Y"), d.strftime("%Y%m%d")


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


def _riel(tipo_doc: Any) -> str:
    """'[TR] Transferencia' → 'Transferencia' (saca el código entre corchetes)."""
    s = (str(tipo_doc or "")).strip()
    if s.startswith("[") and "]" in s:
        return s.split("]", 1)[1].strip() or s
    return s


def ingresos_egresos_dia(*, fecha: str | None = None, estado: str = "Procesado") -> dict:
    """Ingresos/egresos bancarios de un día (default hoy ART) desde Aunesa.

    Devuelve:
      - `resumen`: {unidad: {ingresos, egresos, neto, n}} por moneda (ARS/USD).
      - `movimientos`: filas para la tabla (hora, cuenta, cliente, riel, unidad, tipo,
        monto, estado), ordenadas por hora desc.
    """
    ddmmyyyy, ddmmyyyy_hasta, yyyymmdd = _fechas(fecha)
    params: dict[str, Any] = {"liquidacionDesde": ddmmyyyy, "liquidacionHasta": ddmmyyyy_hasta}
    if estado:
        params["estados"] = estado

    resp = aunesa.get(_ENDPOINT, params)
    if resp.status_code == 204:
        rows: list[dict] = []
    elif resp.status_code == 200:
        body = resp.json()
        rows = body if isinstance(body, list) else []
    else:
        raise RuntimeError(f"Aunesa {_ENDPOINT} [{resp.status_code}]: {resp.text[:300]}")

    resumen: dict[str, dict] = {}
    movimientos: list[dict] = []
    dia_objetivo = 0
    for r in rows:
        # El rango pedido es [día, día+1]; nos quedamos SOLO con las filas del día objetivo.
        if str(r.get("fecha") or "").strip() != ddmmyyyy:
            continue
        dia_objetivo += 1
        sol = _norm(r.get("solicitud"))
        if sol not in (_INGRESO, _EGRESO):
            continue  # defensivo: el discovery confirmó SOLO estos 2 valores
        unidad = (r.get("unidad") or "?").upper()
        monto = _num(r.get("monto"))
        es_ingreso = sol == _INGRESO

        b = resumen.setdefault(unidad, {"ingresos": 0.0, "egresos": 0.0, "neto": 0.0, "n": 0})
        if es_ingreso:
            b["ingresos"] += monto
        else:
            b["egresos"] += monto
        b["neto"] = b["ingresos"] - b["egresos"]
        b["n"] += 1

        per = r.get("persona") or {}
        movimientos.append({
            "id": r.get("id"),
            "hora": _hora(r.get("id"), yyyymmdd),
            "cuenta": r.get("cuenta"),
            "cliente": per.get("nombreCompleto") or "",
            "riel": _riel(r.get("tipoDocSoli")),
            "unidad": unidad,
            "tipo": "ingreso" if es_ingreso else "egreso",
            "monto": round(monto, 2),
            "estado": r.get("estado"),
        })

    for b in resumen.values():
        b["ingresos"], b["egresos"], b["neto"] = (
            round(b["ingresos"], 2), round(b["egresos"], 2), round(b["neto"], 2))
    movimientos.sort(key=lambda m: m.get("hora") or "", reverse=True)

    return {"fecha": ddmmyyyy, "estado": estado, "resumen": resumen,
            "movimientos": movimientos, "n": len(movimientos),
            # diagnóstico: raw = filas del rango [día,día+1]; dia = filas del día objetivo.
            "raw": dia_objetivo}
