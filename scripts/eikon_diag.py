"""eikon_diag.py — diagnóstico de la conexión Eikon en la PC de oficina (one-shot).

Aísla el error 'invalid error value specified' de get_data: corre 6 pruebas de
la más chica a la más grande y muestra el resultado CRUDO de cada una. Correr
con Workspace/Eikon ABIERTO y logueado; pegar la APP_KEY abajo.

    python eikon_diag.py        (doble click también sirve: pausa al final)

Pegar la salida completa en el chat. Se borra cuando cierre la prueba (REGLA #5).
"""
from __future__ import annotations

import json
import time
import traceback

try:
    import eikon as ek
except ImportError:
    print("Falta la librería eikon →  pip install eikon")
    input("[ENTER] para cerrar…")
    raise SystemExit(1) from None

# ── pegá tu APP_KEY acá (solo en tu copia local, no commitear) ───────────────
EIKON_APP_KEY = "PEGA_TU_APP_KEY_ACA"
# ─────────────────────────────────────────────────────────────────────────────

RIC_UNO = "AAPL.O"
RICS_DOS = ["AAPL.O", "MSFT.O"]
FIELDS_MIN = ["CF_LAST"]
FIELDS_MESA = ["CF_LAST", "CF_BID", "CF_ASK", "PCTCHNG"]


def prueba(n: int, titulo: str):
    print(f"\n{'═' * 60}\nPRUEBA {n} — {titulo}\n{'═' * 60}")


def falla(e: Exception) -> None:
    print(f"✗ FALLÓ — {type(e).__name__}: {e}")
    traceback.print_exc(limit=2)


def main() -> None:
    print(f"eikon lib versión: {getattr(ek, '__version__', '?')}")

    prueba(1, "sesión (set_app_key + puerto del proxy local de Workspace)")
    try:
        ek.set_app_key(EIKON_APP_KEY)
        try:
            print(f"✓ app key seteada · puerto proxy: {ek.get_port_number()}")
        except Exception:
            print("✓ app key seteada (get_port_number no disponible en esta versión)")
    except Exception as e:
        falla(e)
        print("Sin sesión no sigue nada — ¿Workspace está abierto y logueado?")
        return

    prueba(2, f"get_data MÍNIMO: 1 RIC ({RIC_UNO}), 1 campo ({FIELDS_MIN[0]})")
    try:
        df, err = ek.get_data(RIC_UNO, FIELDS_MIN)
        print(f"✓ df:\n{df}\n  err: {err}")
    except Exception as e:
        falla(e)

    prueba(3, f"get_data campos de la mesa: {RICS_DOS} × {FIELDS_MESA}")
    try:
        df, err = ek.get_data(RICS_DOS, FIELDS_MESA)
        print(f"✓ df:\n{df}\n  err: {err}")
    except Exception as e:
        falla(e)

    prueba(4, "get_data raw_output=True (respuesta JSON cruda del backend)")
    try:
        raw = ek.get_data(RIC_UNO, FIELDS_MESA, raw_output=True)
        print(f"✓ raw (primeros 1200 chars):\n{json.dumps(raw, default=str)[:1200]}")
    except Exception as e:
        falla(e)

    prueba(5, "symbology ticker→RIC (AAPL, MSFT, KO) — cruda")
    try:
        df = ek.get_symbology(["AAPL", "MSFT", "KO"], from_symbol_type="ticker",
                              to_symbol_type="RIC", best_match=True)
        print(f"✓ df:\n{df}")
    except Exception as e:
        falla(e)

    prueba(6, "StreamingPrices (suscripción REAL, sin polling) — 5 segundos")
    try:
        sp = ek.StreamingPrices(instruments=RICS_DOS, fields=FIELDS_MESA)
        sp.open()
        time.sleep(5)
        snap = sp.get_snapshot()
        sp.close()
        print(f"✓ snapshot del stream:\n{snap}")
        print("→ Si esta prueba anda, el feed puede pasarse a suscripción real "
              "(sin límites de requests).")
    except Exception as e:
        falla(e)

    print(f"\n{'═' * 60}\nFIN — pegá TODA esta salida en el chat.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        falla(e)
    try:
        input("\n[ENTER] para cerrar…")
    except Exception:
        pass
