"""scripts/diag_rofex_cuenta.py — QUÉ número de cuenta reconoce ROFEX (read-only).

CORRE EN EL DROPLET (tiene la sesión de ROFEX de envío). Para cada cuenta candidata,
prueba VARIAS formas contra `pyRofex.get_account_report` y dice cuál devuelve datos.

Contexto del incidente: el fix del 2026-07-03 asumió que ROFEX quiere la cuenta con
zfill(3) ('15' → '015'). Pero eso NUNCA se verificó contra ROFEX — sólo se midió la DB.
El user reporta que '015' NO trae saldo → hay que ver qué forma reconoce REALMENTE el
broker (raw '15', '015', '0015', sin ceros, …). Este diag lo responde.

Es 100% de lectura: get_account_report sólo consulta saldos, no envía órdenes ni escribe.

Uso:
    python -m scripts.diag_rofex_cuenta                # todas las cuentas de <3 dígitos
    python -m scripts.diag_rofex_cuenta 15 9 7         # cuentas puntuales
"""
from __future__ import annotations

import sys

import pyRofex

from core.postgres import get_pool
from core.rofex_orders_session import cuenta_default, ensure_session_envio


def _probe(form: str) -> str:
    """Consulta get_account_report para `form` y resume si ROFEX devolvió datos."""
    try:
        resp = pyRofex.get_account_report(account=form)
    except Exception as e:
        return f"EXCEPCIÓN: {e}"
    if not isinstance(resp, dict) or not resp:
        return "sin respuesta"
    st = resp.get("status")
    ad = resp.get("accountData") or {}
    detalle = ad.get("detailedAccountReports") or {}
    # ¿trae saldos reales? detailedAccountReports con al menos una rueda.
    has = bool(detalle)
    extra = ""
    if isinstance(detalle, dict) and detalle:
        extra = f"  ruedas={list(detalle)[:6]}"
    return f"status={st!r:6} saldos={'SÍ ✅' if has else 'vacío ❌'}{extra}"


def _formas(base: str) -> list[str]:
    """Variantes a probar, sin repetir: raw, zfill(3), zfill(4), sin ceros."""
    cand = [base, base.zfill(3), base.zfill(4), base.lstrip("0") or base]
    out: list[str] = []
    for f in cand:
        if f not in out:
            out.append(f)
    return out


def main() -> int:
    try:
        default = ensure_session_envio()
    except Exception as e:
        print(f"⛔ No se pudo iniciar la sesión de ROFEX de envío: {e}")
        return 1
    print(f"Sesión ROFEX OK. Cuenta default del .env: {default!r} "
          f"(cuenta_default normalizada: {cuenta_default()!r})\n")

    args = sys.argv[1:]
    if args:
        cuentas = args
    else:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id_cuenta FROM clientes.cuentas "
                        "WHERE id_cuenta ~ '^[0-9]+$' AND length(id_cuenta) < 3 "
                        "ORDER BY id_cuenta::int LIMIT 15")
            cuentas = [r[0] for r in cur.fetchall()]

    if not cuentas:
        print("No hay cuentas de <3 dígitos para probar (o pasá tickers por argumento).")
        return 0

    print(f"Probando {len(cuentas)} cuenta(s) contra ROFEX get_account_report:\n")
    for c in cuentas:
        print(f"── cuenta base {c!r} ──")
        for f in _formas(str(c)):
            print(f"    {f!r:8} → {_probe(f)}")
        print()

    print("Lectura: la forma con 'saldos=SÍ ✅' es la que ROFEX reconoce. Si NINGUNA trae "
          "saldos, el nº de ROFEX no se deriva del id_cuenta (mapeo distinto) → hay que "
          "conseguir el nº real de comitente en ROFEX.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
