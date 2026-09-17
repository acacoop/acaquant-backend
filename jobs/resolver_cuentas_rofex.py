"""Resuelve OFFLINE el nº de cuenta que ROFEX ACEPTA para cada comitente activo
y lo persiste en `clientes.comitentes.rofex_account/rofex_valida/rofex_resuelto_at`.

Por qué existe (incidente 2026-09-17, dos rounds): `engines/motor_ordenes.py`
sincroniza "Órdenes del día" (OPERAR) sumando cuentas a la ÚNICA sesión WS de
pyRofex que ya tiene abierta la cuenta master en vivo. Para eso necesitaba
saber qué nº de cuenta acepta ROFEX para cada `id_cuenta` de
`clientes.comitentes` (`clientes.cuentas.id_cuenta` perdió los ceros a la
izquierda de forma inconsistente — no hay regla de formato, hay que MEDIR
contra el broker). El motor lo hacía en caliente, una cuenta a la vez, con
`get_account_report`: cuando el broker no reconocía una, cerraba el WS
ENTERO de la master — no solo esa consulta. Pasó dos veces, en rueda.

Este job hace exactamente esa medición, pero OFFLINE: fuera de rueda, con
`motor_ordenes.service` PARADO (no puede haber dos sesiones pyRofex del
mismo usuario vivas a la vez — ver `core/rofex_orders_session.py`). El motor,
con el WS ya arriba, después solo LEE lo que este job dejó cacheado; nunca
vuelve a preguntarle nada al broker en caliente.

Cadencia: cron 1×/día, bien después de que paró `motor_ordenes.service`
(20:05 UTC) y del último `sync_comitentes` del día (21:00 UTC) — así agarra
altas nuevas del mismo día. Ver `deploy/crontab.txt`.

Por defecto solo resuelve cuentas SIN resolución previa (`rofex_resuelto_at
IS NULL`) — las ya resueltas (válidas o no) no se vuelven a probar, para no
pagar un round-trip al broker por cuenta todos los días de nuevo. Flags:

    python -m jobs.resolver_cuentas_rofex             # solo las nuevas
    python -m jobs.resolver_cuentas_rofex --retry-invalid  # + las marcadas inválidas
    python -m jobs.resolver_cuentas_rofex --full       # todas, ignora el cache
    python -m jobs.resolver_cuentas_rofex --dry-run    # prueba, no escribe

⚠️ NO correr con `motor_ordenes.service` activo (`systemctl is-active
motor_ordenes` → si da `active`, este job aborta solo, salvo `--force`).
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import time
from datetime import UTC, datetime

from core.job_runs import JobRunLogger
from core.logs import configurar
from core.postgres import get_pool
from core.rofex_orders_session import _formas_cuenta, ensure_session_envio

configurar()
logger = logging.getLogger("resolver_cuentas_rofex")

# Pausa entre cuentas — mismo espíritu que `SYNC_PACE_S` de motor_ordenes:
# no ráfaguear al broker con centenares de REST seguidos.
PACE_S = 0.35


def _motor_ordenes_activo() -> bool:
    """Best-effort: True si `motor_ordenes.service` está `active` en systemd.
    Si `systemctl` no está disponible (ej. corriendo local, fuera del
    Droplet), asume que no hay riesgo y devuelve False."""
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "motor_ordenes.service"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() == "active"
    except Exception:
        return False


def _universo(retry_invalid: bool, full: bool) -> list[tuple[str, str | None]]:
    """(id_cuenta, forma ya cacheada o None) de comitentes activos a evaluar.

    Mismo filtro que `engines/motor_ordenes.py::_cuentas_a_sincronizar` y
    `jobs/aum.py::obtener_cuentas` (tipo Comitente/Propia + estado Activa) —
    el universo de cuentas operables tiene que ser el MISMO en todos lados."""
    where = ["tipo IN ('Comitente', 'Propia')", "estado = 'Activa'"]
    if not full:
        if retry_invalid:
            where.append("(rofex_resuelto_at IS NULL OR rofex_valida IS NOT TRUE)")
        else:
            where.append("rofex_resuelto_at IS NULL")
    sql = f"SELECT id_cuenta FROM clientes.comitentes WHERE {' AND '.join(where)} ORDER BY id_cuenta"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [(str(r[0]), None) for r in cur.fetchall()]


def _resolver_una(id_cuenta: str) -> tuple[str | None, bool]:
    """Prueba las formas candidatas contra ROFEX. Devuelve (forma_ok, valida).

    No usa `resolver_cuenta_rofex` (ese cachea EN MEMORIA para la sesión del
    motor, no persiste) — prueba directo con `get_account_report`, mismo
    criterio de éxito."""
    import pyRofex

    for forma in _formas_cuenta(id_cuenta):
        try:
            resp = pyRofex.get_account_report(account=forma)
        except Exception as e:
            logger.debug("cuenta %s forma %r: excepción %s", id_cuenta, forma, e)
            continue
        if (isinstance(resp, dict) and resp.get("status") == "OK"
                and (resp.get("accountData") or {}).get("detailedAccountReports")):
            return forma, True
    return None, False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--retry-invalid", action="store_true",
                     help="además de las nuevas, reintenta las marcadas inválidas")
    ap.add_argument("--full", action="store_true",
                     help="ignora el cache: re-resuelve TODO el universo activo")
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo reporta")
    ap.add_argument("--force", action="store_true",
                     help="corre igual aunque motor_ordenes.service esté activo (NO recomendado)")
    args = ap.parse_args()

    if not args.force and _motor_ordenes_activo():
        print(
            "✗ motor_ordenes.service está ACTIVO — este job abre su PROPIA sesión "
            "pyRofex y, si el motor tiene el WS de la master arriba, probar cuentas "
            "acá puede tirarlo abajo (es el mismo incidente que este job existe para "
            "evitar). Corré esto fuera de rueda, con el motor parado, o pasá --force "
            "si sabés lo que hacés.",
            flush=True,
        )
        return 1

    with JobRunLogger("resolver_cuentas_rofex") as jr:
        universo = _universo(args.retry_invalid, args.full)
        jr.set_stat("universo", len(universo))
        if not universo:
            jr.log("nada para resolver (todo ya tiene rofex_resuelto_at)")
            return 0

        try:
            ensure_session_envio()
        except Exception as e:
            jr.error(f"no pude abrir sesión pyRofex REST: {e}")
            return 1

        ok = malas = 0
        for id_cuenta, _ in universo:
            forma, valida = _resolver_una(id_cuenta)
            if valida:
                ok += 1
                jr.log(f"{id_cuenta} → {forma!r} (OK)")
            else:
                malas += 1
                logger.warning("cuenta %s sin forma ROFEX válida — queda marcada inválida", id_cuenta)
            if not args.dry_run:
                with get_pool().connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE clientes.comitentes SET rofex_account = %s, "
                        "rofex_valida = %s, rofex_resuelto_at = %s WHERE id_cuenta = %s",
                        (forma, valida, datetime.now(UTC), id_cuenta),
                    )
                    conn.commit()
            time.sleep(PACE_S)

        jr.set_stat("ok", ok)
        jr.set_stat("invalidas", malas)
        jr.log(f"{ok} cuenta(s) confirmada(s), {malas} sin forma ROFEX válida "
               f"({'dry-run, nada escrito' if args.dry_run else 'persistido en clientes.comitentes'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
