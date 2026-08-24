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

from api.cache import cached
from api.services.diagnostico_registry import PIEZAS, VISTAS, Pieza
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
    """Devuelve (timestamp_aware | None, run_status | None) de una pieza. SQL-native
    (decomiso Mongo): jobs desde manager.job_runs, tablas desde Postgres. Las Piezas
    legacy con coll/field Mongo (sin tabla SQL) devuelven sin_datos."""
    if p.run_tipo:
        # Frescura de jobs desde manager.job_runs (SQL-native; Manager.JobRuns dropeada).
        try:
            from api.services import manager_infra_sql
            ts, status = manager_infra_sql.jobrun_ultimo_sql(p.run_tipo)
            return (asegurar_aware(ts, UTC) if ts is not None else None), status
        except Exception:
            return None, None
    if p.tabla:
        # Frescura SQL: max(ts_expr::timestamptz) de la tabla.
        try:
            from core.postgres import get_pool
            where = f" WHERE {p.sql_where}" if p.sql_where else ""
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT max(({p.ts_expr})::timestamptz) FROM {p.tabla}{where}")
                row = cur.fetchone()
            ts = row[0] if row else None
            return _parse_ts(ts, p.ts_kind, p.assume), None
        except Exception:
            return None, None
    return None, None


def _estado(p: Pieza, ts: datetime | None, run_status: str | None,
            ahora_a: datetime) -> dict:
    # `ventana` viaja con la pieza (2026-08-20): sin ella, quien lea el árbol no
    # puede decir DESDE y HASTA qué hora el problema es real, y un motor apagado
    # a las 3 AM se lee igual que uno caído. Pedido del user: *«es fundamental
    # entender desde qué hora hasta qué hora el error es real para cada motor»*.
    # ⚠️ `unidad` y `tabla` viajan en el dict porque **el AV AGENT los necesita
    # para poder decir QUÉ relanzar** (`agente/detectores/sistema.motor_caido`).
    # Sin la unidad, el hallazgo tiene sujeto «?» y su `que_hacer` no puede
    # nombrar nada — que es la diferencia entre un aviso y una instrucción.
    base = {"label": p.label, "tipo": p.tipo, "cadencia": p.cadencia,
            "umbral_s": p.umbral_s, "ventana": p.ventana,
            "unidad": p.unidad, "tabla": p.tabla}
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


@cached(ttl=30)
def arbol() -> dict:
    """Árbol completo: vista → grupos → piezas, con status y resumen.

    Cacheado 30s (anti-estampida): la tab Diagnóstico lo pollea cada 10s y cada
    llamada hace 45 find_one a Atlas (~1.7s). Con cache, el poll pega al cache y
    abrir Manager es instantáneo; la frescura mostrada queda como mucho 30s vieja
    (invisible: el árbol muestra "hace X min"). Sin args → una sola clave."""
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
