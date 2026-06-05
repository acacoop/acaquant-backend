"""Motor del Diagnóstico — arma el árbol vista→piezas con status, desde el registro.

Lee la frescura de cada pieza (motor/api por su colección de salida; job por
`Manager.JobRuns`), calcula el estado y agrupa en árbol por vista → subgrupo.

Todas las fechas pasan por `core.tz` (ZoneInfo) → sin el desfasaje de los
`datetime.utcnow() - timedelta(hours=3)` que tenía el status viejo.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import datetime, time

from api.services.diagnostico_registry import PIEZAS, VISTAS, Pieza
from core.mongo import get_mongo_client_read
from core.tz import AR_TZ, UTC, ahora_ar, asegurar_aware, segundos_desde

_APERTURA = {"rueda": time(10, 0), "rueda_agro": time(10, 30)}
_CIERRE = time(17, 5)


def _en_ventana(ahora: datetime, ventana: str) -> bool:
    """¿Estamos en la ventana en la que la pieza DEBERÍA estar produciendo?

    rueda / rueda_agro → L-V dentro del horario de mercado. always / diario →
    siempre (no se suprime el cálculo de frescura por horario).
    """
    if ventana not in _APERTURA:
        return True
    return ahora.weekday() < 5 and _APERTURA[ventana] <= ahora.time() <= _CIERRE


def _fmt_delta(s: float) -> str:
    s = int(s)
    if s < 0:     return "—"
    if s < 60:    return f"{s}s"
    if s < 3600:  return f"{s // 60}m {s % 60}s"
    if s < 86400: return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def _parse_ts(val, ts_kind: str, assume: str) -> datetime | None:
    """Normaliza el valor crudo a datetime aware. `assume` ∈ {UTC, AR}."""
    if val is None:
        return None
    tz = AR_TZ if assume == "AR" else UTC
    try:
        if isinstance(val, datetime):
            return asegurar_aware(val, tz)
        s = str(val)[:10]
        if ts_kind == "ddmmyyyy":
            d = datetime.strptime(s, "%d/%m/%Y").date()
        else:  # iso / datetime como string
            d = _date.fromisoformat(s)
        # fecha sin hora → medianoche en AR (las fechas de jobs son ART).
        return datetime(d.year, d.month, d.day, tzinfo=AR_TZ)
    except Exception:
        return None


def _leer_frescura(p: Pieza) -> tuple[datetime | None, str | None]:
    """Devuelve (timestamp_aware | None, run_status | None) de una pieza."""
    cli = get_mongo_client_read()
    if p.run_tipo:
        doc = cli["Manager"]["JobRuns"].find_one(
            {"tipo": p.run_tipo}, {"finished_at": 1, "status": 1, "_id": 0},
            sort=[("finished_at", -1)],
        )
        if not doc:
            return None, None
        return _parse_ts(doc.get("finished_at"), "datetime", "UTC"), doc.get("status")
    if p.coll and p.field:
        filtro = {p.field: {"$exists": True, "$nin": [None, ""]}}
        if p.filtro:
            filtro = {**filtro, **p.filtro}
        doc = cli[p.db][p.coll].find_one(filtro, {p.field: 1, "_id": 0},
                                         sort=[(p.field, -1)])
        if not doc:
            return None, None
        return _parse_ts(doc.get(p.field), p.ts_kind, p.assume), None
    return None, None


def _estado(p: Pieza, ts: datetime | None, run_status: str | None,
            ahora_a: datetime) -> dict:
    base = {"label": p.label, "tipo": p.tipo, "cadencia": p.cadencia,
            "umbral_s": p.umbral_s}
    if run_status == "error":
        return {**base, "estado": "error", "ultima": _fmt_ultima(ts),
                "hace": _hace(ts), "run_status": run_status}
    if ts is None:
        return {**base, "estado": "sin_datos", "ultima": None, "hace": "—",
                "run_status": run_status}
    delta = segundos_desde(ts)
    if not _en_ventana(ahora_a, p.ventana):
        estado = "fuera_rueda"
    elif delta > p.umbral_s * 3:
        estado = "critico"
    elif delta > p.umbral_s:
        estado = "lento"
    else:
        estado = "ok"
    return {**base, "estado": estado, "ultima": _fmt_ultima(ts),
            "hace": _fmt_delta(delta), "run_status": run_status}


def _fmt_ultima(ts: datetime | None) -> str | None:
    return ts.astimezone(AR_TZ).strftime("%Y-%m-%d %H:%M:%S") if ts else None


def _hace(ts: datetime | None) -> str:
    return _fmt_delta(segundos_desde(ts)) if ts else "—"


_ALERTA = {"critico", "error", "sin_datos", "lento"}


def arbol() -> dict:
    """Árbol completo: vista → grupos → piezas, con status y resumen."""
    ahora_a = ahora_ar()

    with ThreadPoolExecutor(max_workers=12) as ex:
        frescuras = list(ex.map(_leer_frescura, PIEZAS))

    evaluadas = [
        (p, _estado(p, ts, rs, ahora_a))
        for p, (ts, rs) in zip(PIEZAS, frescuras, strict=True)
    ]

    vistas_out = []
    for v in VISTAS:
        piezas_v = [(p, e) for p, e in evaluadas if p.vista == v]
        if not piezas_v:
            continue
        # agrupar por subgrupo preservando orden de aparición
        grupos: dict[str | None, list[dict]] = {}
        for p, e in piezas_v:
            grupos.setdefault(p.grupo, []).append(e)
        ok = sum(1 for _, e in piezas_v if e["estado"] == "ok")
        # "fuera_rueda" no cuenta como alerta (es esperado fuera de horario)
        alertas = sum(1 for _, e in piezas_v if e["estado"] in _ALERTA)
        vistas_out.append({
            "vista":   v,
            "resumen": {"ok": ok, "total": len(piezas_v), "alertas": alertas},
            "grupos":  [{"grupo": g, "piezas": ps} for g, ps in grupos.items()],
        })

    return {
        "ahora_ar": ahora_a.strftime("%Y-%m-%d %H:%M:%S"),
        "en_rueda": _en_ventana(ahora_a, "rueda"),
        "vistas":   vistas_out,
    }
