"""sync_api_copies.py — re-sincroniza colecciones API derivadas.

Las colecciones `*API.*API` son copias derivadas de colecciones fuente
(CashFlow.*, Valuaciones.*, Trading.*). Se construyen con drop+insert vía
`scripts.api_migrate` para mantener un contrato limpio para la API.

Este job permite re-sincronizarlas desde cron, inmediatamente después de
que se actualice la colección fuente correspondiente. Cada flag invoca la
migración respectiva:

    python -m jobs.sync_api_copies --assets        # Valuaciones.Assets     → TitulosAPI.AssetsAPI
    python -m jobs.sync_api_copies --titulos       # Trading.Curvas+Bonds   → TitulosAPI.ValuacionesAPI
    python -m jobs.sync_api_copies --all           # todas (modo backfill)

Nota: --flujo/--movimientos (OperacionesAPI) y --aum (PortfolioAPI) fueron
ELIMINADOS — la API lee directo de CashFlow.* y Valuaciones.AuM.
"""
import argparse
import time
import traceback

from scripts.api_migrate import (
    migrate_assets,
    migrate_flujos_titulos,
)

TASKS = {
    "titulos":     ("Trading.Curvas+Bonds → TitulosAPI.ValuacionesAPI", migrate_flujos_titulos),
    # `assets` rebuildea TitulosAPI.AssetsAPI desde Valuaciones.Assets (UPPERCASE
    # → lowercase via drop+insert). Encadenar al cron de aum mantiene la copia
    # API en sync con la fuente de verdad cuando el usuario edita CARTERA/EMISOR
    # en Valuaciones.Assets.
    "assets":      ("Valuaciones.Assets → TitulosAPI.AssetsAPI",        migrate_assets),
}


def run(names: list[str]) -> int:
    """Ejecuta las migraciones indicadas. Devuelve código de salida (0 si todo OK)."""
    errores = 0
    for name in names:
        label, fn = TASKS[name]
        print(f"\n▶ sync_api_copies [{name}] — {label}")
        t0 = time.time()
        try:
            fn()
            print(f"✔ {name} OK en {time.time() - t0:.1f}s")
        except Exception:
            errores += 1
            print(f"✘ {name} FALLÓ en {time.time() - t0:.1f}s")
            traceback.print_exc()
    return 0 if errores == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Re-sync de colecciones API.")
    for name in TASKS:
        ap.add_argument(f"--{name}", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="Correr todas las migraciones (full backfill).")
    ap.add_argument("--all-dashboard", action="store_true",
                    help="Las 5 que consume el dashboard acaquant-web.")
    args = ap.parse_args()

    if args.all or args.all_dashboard:
        names = list(TASKS.keys())
    else:
        names = [n for n in TASKS if getattr(args, n)]

    if not names:
        ap.error("Nada que sincronizar. Usá --<nombre> o --all.")

    code = run(names)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
