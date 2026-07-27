"""probe_eikon_cierres.py — SONDA descartable (Paso 0).

Pregunta a Eikon si hay HISTÓRICO de cierres diarios para 2 RICs de muestra de
la watchlist offshore (uno global, uno bonar). NO escribe nada, NO postea nada:
solo imprime si `ek.get_timeseries()` devuelve serie o viene vacío.

Por qué importa: los RICs offshore son páginas de contribuidor MarketAxess
("=1M"). Esas páginas a veces NO tienen serie histórica en Eikon (son quotes
contribuidas en vivo, no instrumentos de exchange con cierre EOD). Si esta
sonda vuelve vacía, el backfill vía get_timeseries NO sirve y hay que cambiar
de estrategia — mejor saberlo en 2 minutos que después de armar todo.

Correr en la NOTEBOOK de oficina (Workspace abierto y logueado):

    python scripts/probe_eikon_cierres.py

Descartable — se borra apenas leemos el resultado (REGLA #5).
"""
import sys
import warnings
from datetime import date, timedelta

warnings.filterwarnings("ignore", category=FutureWarning)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

# ── CONFIG — pegá tu app key (la misma del feed eikon_feed_simple.py) ─────────
EIKON_APP_KEY = "PEGA_TU_APP_KEY_ACA"
# ─────────────────────────────────────────────────────────────────────────────

# 2 muestras: un global y un bonar (RICs tomados de core.eikon_bonos.BONOS_OFF)
MUESTRAS = {
    "040114HS2=1M": "GD30 (global, ley extranjera)",
    "ARARGE3209S=1M": "AL30 (bonar, ley local)",
}

DIAS_ATRAS = 90  # ventana de prueba


def main() -> None:
    try:
        import eikon as ek
    except ImportError:
        print("Falta la librería 'eikon' -> pip install eikon")
        raise SystemExit(1) from None

    if not hasattr(ek, "set_app_key"):
        print("El archivo no puede llamarse eikon.py (Python se importa a sí mismo).")
        raise SystemExit(1)

    if "PEGA_TU_APP_KEY" in EIKON_APP_KEY:
        print("Completá EIKON_APP_KEY arriba con tu app key (la del feed).")
        raise SystemExit(1)

    ek.set_app_key(EIKON_APP_KEY.strip())

    hasta = date.today()
    desde = hasta - timedelta(days=DIAS_ATRAS)
    print(f"Probando get_timeseries CLOSE diario {desde} -> {hasta}\n")

    for ric, etiqueta in MUESTRAS.items():
        print(f"── {ric}  ({etiqueta})")
        try:
            df = ek.get_timeseries(
                ric,
                fields="CLOSE",
                start_date=desde.isoformat(),
                end_date=hasta.isoformat(),
                interval="daily",
            )
        except Exception as e:  # sonda: queremos ver cualquier error
            print(f"   ERROR: {type(e).__name__}: {e}\n")
            continue

        if df is None or len(df) == 0:
            print("   VACÍO — sin serie histórica para este RIC.\n")
            continue

        n = len(df)
        primera = df.index[0]
        ultima = df.index[-1]
        try:
            val_ult = float(df.iloc[-1, 0])
        except Exception:
            val_ult = df.iloc[-1, 0]
        print(f"   OK — {n} filas | {primera.date()} .. {ultima.date()} | "
              f"último cierre={val_ult}\n")

    print("Veredicto: si ambos dieron OK con varias filas, seguimos al Paso 1.\n"
          "Si vinieron VACÍOS o con error, avisá — cambiamos de estrategia.")


if __name__ == "__main__":
    main()
