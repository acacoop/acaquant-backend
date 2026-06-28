import argparse
from datetime import date, timedelta

import requests

from core.pg_mirror import write_native

VARIABLES_BCRA = {
    "CER":    30,
    "TAMAR":  44,
    "DOLAR":   5,
    "BADLAR":  7,
}


def fetch_y_guardar(nombre, id_variable, desde, hasta):
    url = f"https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias/{id_variable}"
    params = {"desde": desde, "hasta": hasta}

    try:
        res = requests.get(url, params=params, timeout=30)
        res.raise_for_status()

        resultados = res.json().get('results', [])
        if not resultados:
            print(f"[{nombre}] BCRA devolvió vacío.")
            return

        historial = resultados[0].get('detalle', [])
        if not historial:
            print(f"[{nombre}] Sin datos para el rango {desde} → {hasta}.")
            return

        # SQL-ONLY (macro.series_macro): ya NO se escribe Trading.<serie> en Mongo.
        pg_rows = []
        for d in historial:
            fecha = d.get('fecha')
            valor = d.get('valor')
            try:
                pg_rows.append({"serie": nombre, "fecha": date.fromisoformat(str(fecha)[:10]),
                                "valor": valor})
            except ValueError:
                pass

        n = write_native("macro.series_macro", ["serie", "fecha"], pg_rows)
        print(f"[{nombre}] {n} upserts a macro.series_macro (de {len(historial)} registros).")
        if pg_rows and not n:
            print(f"[{nombre}] ⚠️ write_native devolvió 0 — revisar Postgres.")

    except requests.exceptions.HTTPError as err:
        print(f"[{nombre}] Error HTTP: {err.response.status_code}")
    except Exception as e:
        print(f"[{nombre}] Error: {e}")


def run(today=False):
    # El BCRA publica el CER con ~10 días hábiles de anticipación (forward).
    # Pedimos hasta hoy+21 días corridos para capturar esos valores — las
    # otras series (TAMAR/DOLAR/BADLAR) no publican forward, simplemente
    # devuelven vacío para fechas futuras, no rompe.
    hasta = (date.today() + timedelta(days=21)).isoformat()
    if today:
        desde = (date.today() - timedelta(days=3)).isoformat()
    else:
        desde = "2023-01-01"

    for nombre, id_variable in VARIABLES_BCRA.items():
        fetch_y_guardar(nombre, id_variable, desde, hasta)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", action="store_true", help="Fetch solo el dia de hoy para todas las variables")
    args = parser.parse_args()
    from core.job_runs import JobRunLogger
    with JobRunLogger("bcra"):
        run(today=args.today)
