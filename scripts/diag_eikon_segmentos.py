"""diag_eikon_segmentos.py — DISCOVERY de INGRESOS POR SEGMENTO en Eikon.

Read-only. Corre en la NOTEBOOK (Workspace abierto y logueado), igual que el
feed — el Droplet no tiene Eikon. No escribe nada en la base ni le pega a la
API: solo pregunta y anota qué contesta Reuters.

Qué contesta este diag (hoy NO lo sabemos — REGLA #2, nada de asumir):
  1. ¿Existen los campos de segmentos de NEGOCIO (TR.BGS.Bus*) para nuestros
     papeles, y con qué nombre vuelve cada segmento?
  2. ¿Y los GEOGRÁFICOS (TR.BGS.Geo*)? (ventas por región)
  3. ¿Hay HISTORIA (5 años / 8 trimestres) o solo el último año fiscal?
  4. ¿La suma de los segmentos cierra contra TR.Revenue? (si no cierra, hay
     eliminaciones/"otros" y el gráfico necesita una fila de ajuste.)

Uso (en la notebook, misma carpeta donde tenés feed.py):

    py diag_eikon_segmentos.py

Pegá tu EIKON_APP_KEY abajo (la misma del feed) o pasala por línea de comandos:

    py diag_eikon_segmentos.py TU_APP_KEY

Deja un archivo `salida_segmentos.txt` al lado del script con TODO lo que
devolvió Eikon — ese archivo es el que hay que mandar de vuelta.
"""
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def _salir(motivo: str) -> None:
    print(motivo)
    try:
        input("\n[ENTER] para cerrar...")
    except Exception:
        pass
    raise SystemExit(1)


try:
    import eikon as ek
    import pandas as pd
except ImportError as e:
    _salir(f"Falta la librería {e.name!r} -> en la consola de este Python:  "
           f"pip install eikon pandas")

if not hasattr(ek, "set_app_key"):
    _salir("El archivo NO puede llamarse eikon.py (Python lo importa a sí mismo "
           "en vez de la librería). Renombralo y listo.")

# ════════════════════════════════════════════════════════════════════════════
EIKON_APP_KEY = "PEGA_TU_APP_KEY_ACA"     # la misma del feed
RICS = ["AAPL.O", "NVDA.O", "KO.N"]       # 3 perfiles distintos a propósito:
# AAPL publica segmentos de producto Y geográficos · NVDA tiene 2 segmentos muy
# marcados (Data Center / Gaming) · KO es consumo con corte regional fuerte.
# ════════════════════════════════════════════════════════════════════════════

SALIDA = Path(__file__).with_name("salida_segmentos.txt")
_lineas: list[str] = []


def log(txt: str = "") -> None:
    """Imprime en consola Y acumula para el archivo de salida."""
    print(txt)
    _lineas.append(txt)


def probar(titulo: str, campos: list[str], parametros: dict | None = None) -> None:
    """Una prueba aislada: pide `campos` para los RICs y anota TODO lo que
    vuelve (columnas, filas, errores). Un campo inexistente NO corta el diag —
    justamente saber que no existe es parte del resultado."""
    log("\n" + "═" * 78)
    log(f"► {titulo}")
    log(f"  campos     : {campos}")
    log(f"  parameters : {parametros or '—'}")
    try:
        df, err = ek.get_data(RICS, campos, parameters=parametros, field_name=True)
    except Exception as e:
        log(f"  ❌ EXCEPCIÓN: {type(e).__name__}: {e}")
        return
    if err:
        vistos = set()
        for e in err:
            msg = str(e.get("message", ""))[:150]
            if msg not in vistos:
                vistos.add(msg)
                log(f"  ⚠️ aviso Eikon: {msg}")
    if df is None or df.empty:
        log("  ⛔ SIN DATOS (el campo no aplica a estos RICs o no lo tenemos licenciado).")
        return
    log(f"  ✅ filas={len(df)}  columnas={list(df.columns)}")
    log("  ── contenido completo ──")
    with pd.option_context("display.max_rows", 200, "display.max_columns", 40,
                           "display.width", 200):
        log(df.to_string(index=False))


def main() -> None:
    key = sys.argv[1].strip() if len(sys.argv) > 1 else EIKON_APP_KEY
    if "PEGA_TU" in key:
        _salir("Te falta pegar EIKON_APP_KEY arriba (o pasarla: "
               "py diag_eikon_segmentos.py TU_APP_KEY).")
    ek.set_app_key(key)

    log(f"DISCOVERY DE SEGMENTOS — {datetime.now():%Y-%m-%d %H:%M:%S}")
    log(f"RICs: {RICS}")

    # ── 1) SEGMENTOS DE NEGOCIO ────────────────────────────────────────────
    # Nombre del campo según la doc de LSEG: familia TR.BGS.* (Business and
    # Geographic Segments). Se prueban las dos grafías que aparecen en los
    # foros (BusTotalRevenue / BusinessTotalRevenue) porque NO está confirmado
    # cuál acepta esta versión de la lib.
    probar("NEGOCIO — nombre del segmento + ingresos (último año fiscal)",
           ["TR.BGS.BusTotalRevenue.segmentName", "TR.BGS.BusTotalRevenue"])
    probar("NEGOCIO — grafía alternativa 'BusinessTotalRevenue'",
           ["TR.BGS.BusinessTotalRevenue.segmentName", "TR.BGS.BusinessTotalRevenue"])
    probar("NEGOCIO — con código de segmento y orden de detalle",
           ["TR.BGS.BusTotalRevenue.segmentName", "TR.BGS.BusTotalRevenue.segmentCode",
            "TR.BGS.BusTotalRevenue.segmentDetailsOrder", "TR.BGS.BusTotalRevenue"])
    probar("NEGOCIO — ingresos EXTERNOS (sin ventas entre segmentos)",
           ["TR.BGS.BusExternalRevenue.segmentName", "TR.BGS.BusExternalRevenue"])
    probar("NEGOCIO — rentabilidad y activos por segmento",
           ["TR.BGS.BusTotalRevenue.segmentName", "TR.BGS.BusOperatingIncome",
            "TR.BGS.BusTotalAssets", "TR.BGS.BusCapitalExpenditures"])

    # ── 2) SEGMENTOS GEOGRÁFICOS ───────────────────────────────────────────
    probar("GEOGRÁFICO — región + ingresos",
           ["TR.BGS.GeoTotalRevenue.segmentName", "TR.BGS.GeoTotalRevenue"])

    # ── 3) ¿HAY HISTORIA? ──────────────────────────────────────────────────
    # Mismo patrón que las series de la ficha (SDate/EDate). Si vuelve una sola
    # fecha por segmento, NO hay historia y el gráfico es una foto.
    probar("NEGOCIO — 5 AÑOS (SDate=0, EDate=-4, en millones USD)",
           ["TR.BGS.BusTotalRevenue.date", "TR.BGS.BusTotalRevenue.segmentName",
            "TR.BGS.BusTotalRevenue"],
           {"SDate": "0", "EDate": "-4", "Scale": "6", "Curn": "USD"})
    probar("NEGOCIO — 8 TRIMESTRES (Period=FQ0, Frq=FQ)",
           ["TR.BGS.BusTotalRevenue.date", "TR.BGS.BusTotalRevenue.segmentName",
            "TR.BGS.BusTotalRevenue"],
           {"SDate": "0", "EDate": "-7", "Period": "FQ0", "Frq": "FQ",
            "Scale": "6", "Curn": "USD"})

    # ── 4) ¿CIERRA CONTRA EL TOTAL? ────────────────────────────────────────
    # Si Σ segmentos ≠ TR.Revenue hay eliminaciones / "corporate & other" y el
    # gráfico necesita una fila de ajuste para no mentir.
    probar("CONTROL — ingresos totales del año fiscal (para cruzar contra la suma)",
           ["TR.Revenue"], {"Period": "FY0", "Scale": "6", "Curn": "USD"})

    log("\n" + "═" * 78)
    log("FIN. Mandá el archivo salida_segmentos.txt tal cual.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\ncortado a mano.")
    except Exception as e:
        log(f"\n❌ {type(e).__name__}: {e}")
    try:
        SALIDA.write_text("\n".join(_lineas), encoding="utf-8")
        print(f"\n📄 salida escrita en: {SALIDA}")
    except Exception as e:
        print(f"\n⚠️ no se pudo escribir {SALIDA}: {e}")
    try:
        input("\n[ENTER] para cerrar…")
    except Exception:
        pass
