"""jobs/guardrails.py — invariantes de sanidad de datos post-cierre.

Doc vivo: docs/OBSERVABILIDAD_ROBUSTEZ.md (commit 2). El watchdog vigila que el
motor esté VIVO; esto vigila que el NÚMERO esté BIEN: detecta un dato podrido
(AuM que saltó, precio de cierre absurdo, curva incompleta, nulls donde no van)
ANTES de que un usuario decida con él.

Arquitectura:
- REGISTRY de checks: cada check es una función PURA (recibe datos ya leídos,
  devuelve una lista de resultados {ok, check_id, severidad, mensaje,
  valor_medido, umbral}). Testeable sin SQL.
- El runner lee los datos (queries SCOPEADAS por fecha — REGLA #4, cero full
  scans), corre los checks y junta violaciones.

CALIBRACIÓN OBLIGATORIA (REGLA #2 — lo más importante):
- Modo default `--report`: NO alerta. Imprime cada check con su VALOR REAL
  medido. El user lo corre varios días y con esos números fija los umbrales en
  `config.GUARDRAILS_UMBRALES` (None = sin calibrar = ese check no alerta jamás).
- Con `--alert` (cron prod): alerta por Telegram (METADATA ONLY — jamás datos de
  clientes) con cooldown en `manager.watchdog_alertas` keyeado
  `guardrail:<check_id>` (mismo patrón que el watchdog usa para motores).
  Idempotente: re-correrlo dentro del cooldown no duplica alertas.

Uso:
    python -m jobs.guardrails             # --report implícito (calibración)
    python -m jobs.guardrails --alert     # prod (cron post-cierre)
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from config import GUARDRAILS_COOLDOWN_H, GUARDRAILS_UMBRALES
from core.notify import send_telegram
from core.pg_mirror import write_native
from core.postgres import get_pool
from jobs.watchdog import _last_alert_at

logger = logging.getLogger(__name__)

_MAX_ITEMS_MSG = 8   # tickers que se listan en un mensaje (metadata, no spam)


# ─────────────────────────── checks PUROS (testeables) ───────────────────────


def _res(check_id: str, ok: bool, severidad: str, mensaje: str,
         valor_medido, umbral) -> dict:
    return {"ok": ok, "check_id": check_id, "severidad": severidad,
            "mensaje": mensaje, "valor_medido": valor_medido, "umbral": umbral}


def check_aum_delta(total_hoy: float | None, total_ayer: float | None,
                    umbral_pct: float | None) -> list[dict]:
    """AuM total día-contra-día: |Δ%| > umbral = dato sospechoso (una carga
    rota del writer diario, no un movimiento real). Fuente: portafolio.tenencia
    aum='si' (la valuación ya viene calculada con el divisor por CARTERA — la
    fórmula no inferible vive en el writer, acá NO se recalcula nada)."""
    cid, sev = "aum_delta", "alta"
    if not total_hoy or not total_ayer:
        return [_res(cid, False, sev, "AuM sin datos para comparar (¿corrió el "
                     "writer diario?)", None, umbral_pct)]
    delta_pct = (total_hoy / total_ayer - 1) * 100
    ok = umbral_pct is None or abs(delta_pct) <= umbral_pct
    return [_res(cid, ok, sev,
                 f"AuM total saltó {delta_pct:+.2f}% día-contra-día",
                 round(delta_pct, 2), umbral_pct)]


def check_saltos_precio(precios_hoy: dict[str, float], precios_prev: dict[str, float],
                        umbral_pct: float | None) -> list[dict]:
    """Precio de cierre por bono vs el cierre previo: salto > umbral% = precio
    podrido (o un evento real — por eso el umbral lo calibra el user). Solo
    tickers presentes en ambos cierres con precio > 0."""
    cid, sev = "salto_precio", "media"
    out = []
    for tk, hoy in precios_hoy.items():
        prev = precios_prev.get(tk)
        if not prev or not hoy or prev <= 0 or hoy <= 0:
            continue
        delta_pct = (hoy / prev - 1) * 100
        if umbral_pct is not None and abs(delta_pct) > umbral_pct:
            out.append(_res(cid, False, sev,
                            f"{tk}: cierre saltó {delta_pct:+.1f}%",
                            round(delta_pct, 2), umbral_pct))
    if not out:
        # un solo resultado OK con el máximo medido (para el report de calibración)
        max_abs = max((abs((h / precios_prev[t] - 1) * 100)
                       for t, h in precios_hoy.items()
                       if precios_prev.get(t) and precios_prev[t] > 0 and h > 0),
                      default=0.0)
        out.append(_res(cid, True, sev,
                        f"máximo salto de cierre del día: {max_abs:.2f}%",
                        round(max_abs, 2), umbral_pct))
    return out


def check_completitud_curvas(master_por_curva: dict[str, set[str]],
                             tickers_cierre: set[str],
                             umbral_pct: float | None) -> list[dict]:
    """Cobertura del cierre por curva: % de bonos del master (mercado.curvas)
    que tienen cierre hoy. Bajo el umbral = faltan patas (motor caído a mitad
    de rueda, bono sin precio, etc.)."""
    cid, sev = "cobertura_curva", "media"
    out = []
    for curva, tks in sorted(master_por_curva.items()):
        if not tks:
            continue
        con_cierre = len(tks & tickers_cierre)
        pct = con_cierre / len(tks) * 100
        ok = umbral_pct is None or pct >= umbral_pct
        out.append(_res(cid, ok, sev,
                        f"curva {curva}: {con_cierre}/{len(tks)} bonos con cierre "
                        f"({pct:.0f}%)", round(pct, 1), umbral_pct))
    return out


def check_sanidad_cierre(filas: list[dict]) -> list[dict]:
    """Invariante ABSOLUTO (sin umbral): en el cierre no puede haber precios
    ≤ 0 ni nulls en los campos obligatorios (ticker/precio). Siempre activo."""
    cid, sev = "sanidad_cierre", "alta"
    malos = []
    for f in filas:
        tk = f.get("ticker_corto")
        px = f.get("ultimo_precio")
        if not tk or px is None or px <= 0:
            malos.append(str(tk or "SIN_TICKER"))
    if malos:
        return [_res(cid, False, sev,
                     f"{len(malos)} filas de cierre inválidas (precio ≤0/null o "
                     f"sin ticker): {', '.join(malos[:_MAX_ITEMS_MSG])}",
                     len(malos), 0)]
    return [_res(cid, True, sev, f"cierre sano ({len(filas)} filas)", 0, 0)]


# ─────────────────────────── lecturas SCOPEADAS ──────────────────────────────


def _leer_aum() -> tuple[float | None, float | None]:
    """(total_hoy, total_ayer) = suma de valuación aum='si' de las 2 últimas
    fechas del writer. Scopeado por fecha (2 fechas puntuales, no un scan)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT fecha FROM portafolio.tenencia "
                    "WHERE aum = 'si' ORDER BY fecha DESC LIMIT 2")
        fechas = [r[0] for r in cur.fetchall()]
        if len(fechas) < 2:
            return None, None
        cur.execute(
            "SELECT fecha, sum(valuacion) FROM portafolio.tenencia "
            "WHERE aum = 'si' AND fecha = ANY(%s) GROUP BY fecha ORDER BY fecha DESC",
            (fechas,),
        )
        filas = {r[0]: float(r[1] or 0) for r in cur.fetchall()}
    return filas.get(fechas[0]), filas.get(fechas[1])


def _leer_cierres() -> tuple[list[dict], dict[str, float], dict[str, float]]:
    """(filas_hoy, precios_hoy, precios_prev) de las 2 últimas fechas de
    mercado.snapshots_cierre_hist. Scopeado por fecha."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT fecha FROM mercado.snapshots_cierre_hist "
                    "ORDER BY fecha DESC LIMIT 2")
        fechas = [r[0] for r in cur.fetchall()]
        if not fechas:
            return [], {}, {}
        cur.execute(
            "SELECT fecha, ticker_corto, ultimo_precio "
            "FROM mercado.snapshots_cierre_hist WHERE fecha = ANY(%s)",
            (fechas,),
        )
        hoy_f, prev_f = fechas[0], (fechas[1] if len(fechas) > 1 else None)
        filas_hoy: list[dict] = []
        p_hoy: dict[str, float] = {}
        p_prev: dict[str, float] = {}
        for fecha, tk, px in cur.fetchall():
            if fecha == hoy_f:
                filas_hoy.append({"ticker_corto": tk, "ultimo_precio":
                                  float(px) if px is not None else None})
                if tk and px is not None:
                    p_hoy[tk] = float(px)
            elif fecha == prev_f and tk and px is not None:
                p_prev[tk] = float(px)
    return filas_hoy, p_hoy, p_prev


def _leer_master_por_curva() -> dict[str, set[str]]:
    """Bonos del master agrupados por curva (cacheado en curvas_sql — barato).
    Las ONs se excluyen: su cobertura de precios es intrínsecamente rala (ilíquidas)
    y ensuciaría el check con falsos rojos."""
    from core import curvas_sql

    out: dict[str, set[str]] = {}
    for d in curvas_sql.cargar_todos():
        curva, tk = d.get("curva"), d.get("ticker_corto")
        if curva and tk and not str(curva).startswith("on"):
            out.setdefault(curva, set()).add(tk)
    return out


# ─────────────────────────── runner ──────────────────────────────────────────


def correr_checks() -> list[dict]:
    """Lee los datos y corre el registry completo. Devuelve TODOS los resultados
    (ok y violaciones) — el caller decide reportar o alertar."""
    resultados: list[dict] = []
    u = GUARDRAILS_UMBRALES

    try:
        total_hoy, total_ayer = _leer_aum()
        resultados += check_aum_delta(total_hoy, total_ayer, u.get("aum_delta_pct"))
    except Exception as e:
        logger.warning("guardrails: lectura AuM falló (%s)", e)

    try:
        filas_hoy, p_hoy, p_prev = _leer_cierres()
        resultados += check_sanidad_cierre(filas_hoy)
        resultados += check_saltos_precio(p_hoy, p_prev, u.get("salto_precio_pct"))
        resultados += check_completitud_curvas(
            _leer_master_por_curva(), set(p_hoy), u.get("cobertura_curva_pct"))
    except Exception as e:
        logger.warning("guardrails: lectura de cierres falló (%s)", e)

    return resultados


def _alertar(violaciones: list[dict]) -> int:
    """Telegram con cooldown por check_id (metadata only). Idempotente."""
    ahora = datetime.now(UTC)
    enviados = 0
    por_check: dict[str, list[dict]] = {}
    for v in violaciones:
        por_check.setdefault(v["check_id"], []).append(v)
    for check_id, vs in por_check.items():
        key = f"guardrail:{check_id}"
        prev = _last_alert_at(key)
        if prev and (ahora - prev) < timedelta(hours=GUARDRAILS_COOLDOWN_H):
            continue
        cuerpo = "\n".join(f"• {v['mensaje']}" for v in vs[:_MAX_ITEMS_MSG])
        extra = f"\n(+{len(vs) - _MAX_ITEMS_MSG} más)" if len(vs) > _MAX_ITEMS_MSG else ""
        send_telegram(f"🧪 Guardrail *{check_id}* ({vs[0]['severidad']}):\n"
                      f"{cuerpo}{extra}\n_revisar antes de confiar en el dato_")
        write_native("watchdog_alertas", ["id"],
                     [{"id": key, "last_alert_at": ahora, "n": len(vs)}])
        enviados += 1
    return enviados


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", action="store_true",
                    help="manda alertas Telegram (default: --report, solo imprime)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from core.job_runs import JobRunLogger
    with JobRunLogger("guardrails") as jr:
        resultados = correr_checks()
        violaciones = [r for r in resultados if not r["ok"]]
        sin_calibrar = sorted({r["check_id"] for r in resultados if r["umbral"] is None})

        print(f"{'=' * 70}\nGUARDRAILS — {len(resultados)} resultados · "
              f"{len(violaciones)} violaciones · modo {'ALERT' if args.alert else 'REPORT'}")
        print(f"{'=' * 70}")
        for r in resultados:
            marca = "✅" if r["ok"] else "🔴"
            umbral = "SIN CALIBRAR" if r["umbral"] is None else r["umbral"]
            print(f"{marca} [{r['check_id']:16}] {r['mensaje']}  "
                  f"(medido={r['valor_medido']} · umbral={umbral})")
        if sin_calibrar:
            print(f"\n⚠ Checks SIN calibrar (no alertan): {', '.join(sin_calibrar)} — "
                  "corré este report unos días y fijá los umbrales en "
                  "config.GUARDRAILS_UMBRALES con los valores medidos.")

        enviados = _alertar(violaciones) if args.alert and violaciones else 0
        jr.set_stat("resultados", len(resultados))
        jr.set_stat("violaciones", len(violaciones))
        jr.set_stat("alertas_enviadas", enviados)
    print(f"\n{'✅ sin violaciones' if not violaciones else f'🔴 {len(violaciones)} violaciones'}"
          f"{f' · {enviados} alertas enviadas' if args.alert else ' (REPORT — sin alertas)'}")


if __name__ == "__main__":
    main()
