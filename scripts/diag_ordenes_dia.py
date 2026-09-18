"""Diagnóstico read-only de OPERAR → "Órdenes del día".

Mide la cadena completa sin abrir pyRofex ni pegarle al broker:

1. si el motor está activo y qué dice `.env` sobre `ORDENES_DIA_SYNC`;
2. si el schema nuevo existe (`clientes.comitentes.rofex_*`);
3. si el resolver offline pobló el cache de cuentas ROFEX;
4. si `operaciones.ordenes_dia` tiene filas para HOY / día hábil anterior;
5. qué dejó logueado `motor_ordenes.service` desde el último arranque;
6. última corrida de `jobs.resolver_cuentas_rofex` en `manager.job_runs`.

Uso:
    python -m scripts.diag_ordenes_dia
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from core.postgres import connect

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")


def _print_titulo(txt: str) -> None:
    print("\n" + "=" * 78)
    print(txt)
    print("=" * 78)


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 999, f"{type(e).__name__}: {e}"


def _scalar(sql: str, params: tuple = ()):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def _rows(sql: str, params: tuple = ()) -> list[tuple]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _dict_row(sql: str, params: tuple = ()) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return None
        return {desc.name: val for desc, val in zip(cur.description, row, strict=False)}


def _print_tabla(rows: list[tuple], headers: tuple[str, ...]) -> None:
    if not rows:
        print("(sin filas)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(str(v)))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)))


def _diag_entorno() -> None:
    _print_titulo("1) Motor y env")
    env = dotenv_values(ENV_PATH) if os.path.exists(ENV_PATH) else {}
    sync = (env.get("ORDENES_DIA_SYNC") or "").strip()
    print(f".env: ORDENES_DIA_SYNC={sync!r} ({'PRENDIDO' if sync == '1' else 'APAGADO'})")
    code, out = _run(["systemctl", "is-active", "motor_ordenes.service"])
    print(f"systemctl is-active motor_ordenes.service → rc={code}, salida={out!r}")
    code, out = _run(["systemctl", "show", "motor_ordenes.service", "-p", "ActiveState", "-p", "MainPID"])
    print(out or f"(systemctl show no disponible, rc={code})")


def _diag_schema_y_cache() -> None:
    _print_titulo("2) Schema y cache ROFEX")
    cols = _rows(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='clientes'
          AND table_name='comitentes'
          AND column_name IN ('rofex_account', 'rofex_valida', 'rofex_resuelto_at')
        ORDER BY column_name
        """
    )
    print("columnas rofex_* en clientes.comitentes:", ", ".join(c[0] for c in cols) or "NINGUNA")
    stats = _dict_row(
        """
        SELECT
            count(*) FILTER (WHERE tipo IN ('Comitente','Propia') AND estado='Activa') AS activas,
            count(*) FILTER (WHERE tipo IN ('Comitente','Propia') AND estado='Activa'
                              AND rofex_valida IS TRUE AND rofex_account IS NOT NULL) AS validas,
            count(*) FILTER (WHERE tipo IN ('Comitente','Propia') AND estado='Activa'
                              AND rofex_valida IS FALSE) AS invalidas,
            count(*) FILTER (WHERE tipo IN ('Comitente','Propia') AND estado='Activa'
                              AND rofex_resuelto_at IS NULL) AS sin_resolver,
            max(rofex_resuelto_at) AS ultima_resolucion
        FROM clientes.comitentes
        """
    )
    print(stats)
    muestras = _rows(
        """
        SELECT id_cuenta, rofex_account, rofex_valida, rofex_resuelto_at
        FROM clientes.comitentes
        WHERE tipo IN ('Comitente','Propia') AND estado='Activa'
        ORDER BY rofex_valida DESC NULLS LAST, rofex_resuelto_at DESC NULLS LAST, id_cuenta
        LIMIT 12
        """
    )
    _print_tabla(muestras, ("id_cuenta", "rofex_account", "valida", "resuelto_at"))


def _diag_ordenes() -> None:
    _print_titulo("3) Tabla operaciones.ordenes_dia")
    hoy_ar = datetime.now(TZ_AR).date()
    ayer_cal = hoy_ar - timedelta(days=1)
    total = _scalar("SELECT count(*) FROM operaciones.ordenes_dia")
    print(f"hoy Argentina = {hoy_ar}; ayer calendario = {ayer_cal}; total tabla = {total}")
    por_fecha = _rows(
        """
        SELECT fecha, count(*) AS reports, count(*) FILTER (WHERE last_qty > 0) AS ejecuciones,
               count(DISTINCT account) AS cuentas, max(updated_at) AS ultimo_update
        FROM operaciones.ordenes_dia
        GROUP BY fecha
        ORDER BY fecha DESC
        LIMIT 10
        """
    )
    _print_tabla(por_fecha, ("fecha", "reports", "last_qty>0", "cuentas", "ultimo_update"))
    por_cuenta_hoy = _rows(
        """
        SELECT account, count(*) AS reports, count(*) FILTER (WHERE last_qty > 0) AS ejecuciones,
               max(transact_time) AS ultimo_transact, max(updated_at) AS ultimo_update
        FROM operaciones.ordenes_dia
        WHERE fecha = %s
        GROUP BY account
        ORDER BY reports DESC, account
        LIMIT 20
        """,
        (hoy_ar,),
    )
    print("\nTop cuentas HOY:")
    _print_tabla(por_cuenta_hoy, ("account", "reports", "last_qty>0", "ultimo_transact", "ultimo_update"))


def _diag_ws_vivo() -> None:
    """¿El WS está trayendo algo HOY? Separa "el motor no captura" de "todavía
    no operó nadie": `ordenes_live`/`ordenes_audit` los escribe el MISMO
    handler que alimenta `ordenes_dia`, pero solo para NUESTRAS órdenes."""
    _print_titulo("3b) ¿Llega algo por el WS hoy?")
    hoy_ar = datetime.now(TZ_AR).date()
    live = _rows(
        """
        SELECT account, count(*) AS ordenes, max(updated_at) AS ultimo
        FROM operaciones.ordenes_live
        WHERE updated_at::date = %s
        GROUP BY account ORDER BY ultimo DESC LIMIT 10
        """,
        (hoy_ar,),
    )
    print("ordenes_live de hoy (nuestras órdenes por el API):")
    _print_tabla(live, ("account", "ordenes", "ultimo"))
    audit = _rows(
        """
        SELECT kind, count(*) AS eventos, max(ts) AS ultimo
        FROM operaciones.ordenes_audit
        WHERE ts::date = %s
        GROUP BY kind ORDER BY ultimo DESC LIMIT 10
        """,
        (hoy_ar,),
    )
    print("\nordenes_audit de hoy (execution reports auditados):")
    _print_tabla(audit, ("kind", "eventos", "ultimo"))


def _diag_job_runs() -> None:
    _print_titulo("4) Último resolver_cuentas_rofex")
    row = _dict_row(
        """
        SELECT started_at, finished_at, status,
               data->'stats' AS stats,
               data->'errors' AS errors,
               data->'log' AS log
        FROM manager.job_runs
        WHERE tipo='resolver_cuentas_rofex'
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    print(row or "(sin runs registrados)")


def _diag_logs() -> None:
    _print_titulo("5) Logs recientes motor_ordenes")
    code, out = _run(["journalctl", "-u", "motor_ordenes", "-n", "220", "--no-pager"])
    if code not in (0, 1):
        print(f"journalctl no disponible o falló rc={code}: {out}")
        return
    claves = ("Órdenes del día", "ordenes_dia", "Motor de órdenes ARRIBA", "goodbye", "ERROR websocket")
    lineas = [ln for ln in out.splitlines() if any(k in ln for k in claves)]
    if not lineas:
        print("(sin líneas relevantes en los últimos 220 logs)")
        return
    for ln in lineas[-80:]:
        print(ln)


def _veredicto() -> None:
    _print_titulo("6) Veredicto automático")
    env = dotenv_values(ENV_PATH) if os.path.exists(ENV_PATH) else {}
    sync = (env.get("ORDENES_DIA_SYNC") or "").strip()
    hoy_ar = datetime.now(TZ_AR).date()
    validas = _scalar(
        """
        SELECT count(*)
        FROM clientes.comitentes
        WHERE tipo IN ('Comitente','Propia') AND estado='Activa'
          AND rofex_valida IS TRUE AND rofex_account IS NOT NULL
        """
    )
    hoy = _scalar("SELECT count(*) FROM operaciones.ordenes_dia WHERE fecha = %s", (hoy_ar,))
    activo = _run(["systemctl", "is-active", "motor_ordenes.service"])[1].strip()
    if sync != "1":
        print("PROBLEMA MÁS PROBABLE: ORDENES_DIA_SYNC no está en '1' en .env; el motor arranca pero no sincroniza la ALyC.")
    elif activo != "active":
        print("PROBLEMA MÁS PROBABLE: motor_ordenes.service no está activo.")
    elif not validas:
        print("PROBLEMA MÁS PROBABLE: el cache ROFEX está vacío; el motor no tiene cuentas confirmadas para suscribir.")
    elif not hoy:
        print("PROBLEMA MÁS PROBABLE: el motor está activo y el cache existe, pero hoy no hizo backfill/upsert. Mirar logs: si no aparece 'Órdenes del día: sincronizando', el service no cargó el env/restart correcto; si aparece con errores, el problema está en backfill/suscripción.")
    else:
        print(f"OK DB: hay {hoy} orderReport(s) de hoy. Si el front se ve vacío, el problema probablemente está en el filtro/scope/frontend, no en el motor.")


def main() -> int:
    _diag_entorno()
    _diag_schema_y_cache()
    _diag_ordenes()
    _diag_ws_vivo()
    _diag_job_runs()
    _diag_logs()
    _veredicto()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
