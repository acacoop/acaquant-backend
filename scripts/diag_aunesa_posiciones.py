"""Diag READ-ONLY: ¿qué contesta Aunesa, endpoint por endpoint?

Nace de una contradicción real: el viernes 07/08 las vistas de Aunesa funcionaban
todo el día, pero el backfill de tenencias recibió HTTP 500 en las 1868 cuentas. Y
la corrida quedó en `ok` porque el job nunca reportó nada.

Ese detalle descarta la explicación fácil ("Aunesa estaba caído"): el job hace
`autenticar()` ANTES del loop, así que si el login hubiera fallado habría reventado
ahí. Llegó a pedir 1868 posiciones ⇒ el login andaba y el que devolvió 500 fue el
endpoint de POSICIONES.

Este diag separa las dos cosas, con los MISMOS parámetros que usa el job:

  1) LOGIN            → ¿la credencial entra?
  2) posicionValuada  → el endpoint que falló, probado con distintos `desde`
                        (hoy / último hábil / uno viejo que ya cargó bien), para
                        ver si el 500 depende de la FECHA pedida o es del endpoint.

Muestra el CUERPO de la respuesta: ahí suele estar el motivo real.

Uso:
    python -m scripts.diag_aunesa_posiciones            # cuenta 10 (la primera)
    python -m scripts.diag_aunesa_posiciones 1234       # otra cuenta
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import requests

import config
from core.calendario import es_habil

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
POSICION_URL = "https://aca.aunesa.com/Irmo/api/cuentas/{}/posicionValuada"
# Los mismos que manda jobs/portafolio_backfill.py::_fetch_parse.
PARAMS_BASE = {"hasta": "", "tipoCuenta": "Comitentes y propias",
               "nivel": "Especie x cuenta", "ocultarCerradas": "true"}


def _ultimo_habil(desde_dia: date) -> date:
    d = desde_dia
    while not es_habil(d):
        d -= timedelta(days=1)
    return d


def login() -> dict | None:
    print("\n── 1) LOGIN ──────────────────────────────────────────────────")
    try:
        r = requests.post(AUTH_URL, json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        }, headers={"Content-Type": "application/json"}, timeout=15)
    except Exception as e:
        print(f"   ✗ ni siquiera respondió: {type(e).__name__}: {e}")
        return None
    print(f"   HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"   cuerpo: {(r.text or '')[:400]}")
        print("   → el login está caído: es Aunesa entero, no un endpoint puntual.")
        return None
    tok = r.json().get("token")
    print(f"   ✓ token OK ({'sí' if tok else 'NO vino token'})")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def posiciones(headers: dict, cuenta: str) -> None:
    print(f"\n── 2) posicionValuada · cuenta {cuenta} ──────────────────────")
    hoy = date.today()
    # El job pide `desde` = día hábil SIGUIENTE al que quiere snapshotear. Se prueban
    # varias fechas para ver si el 500 depende de cuál se pide.
    casos = [
        ("HOY", hoy),
        ("último hábil", _ultimo_habil(hoy)),
        ("07/08 (el que falló)", date(2026, 8, 7)),
        ("06/08 (uno que cargó bien)", date(2026, 8, 6)),
        ("05/08 (uno que cargó bien)", date(2026, 8, 5)),
    ]
    for etiqueta, d in casos:
        desde = d.strftime("%d/%m/%Y")
        params = {"desde": desde, **PARAMS_BASE}
        try:
            r = requests.get(POSICION_URL.format(cuenta), params=params,
                             headers=headers, timeout=60)
        except Exception as e:
            print(f"   {etiqueta:<28} desde={desde}  ✗ {type(e).__name__}: {e}")
            continue
        extra = ""
        if r.status_code == 200:
            try:
                data = r.json()
                n = len(data) if isinstance(data, list) else len(data or {})
                extra = f" · {n} items"
            except Exception:
                extra = " · (no es JSON)"
        elif r.status_code == 204:
            extra = " · sin posición (no es error)"
        else:
            extra = f" · cuerpo: {(r.text or '')[:200]}"
        marca = "✓" if r.status_code in (200, 204) else "⚠"
        print(f"   {etiqueta:<28} desde={desde}  {marca} HTTP {r.status_code}{extra}")


def main() -> int:
    cuenta = sys.argv[1] if len(sys.argv) > 1 else "10"
    print(f"\n{'=' * 70}\nDIAG AUNESA — login vs. posicionValuada\n{'=' * 70}")
    h = login()
    if not h:
        print("\nSin login no se puede probar el resto. Si esto da 500, Aunesa está")
        print("caído entero y hay que esperar (o reclamarles con este output).\n")
        return 0
    posiciones(h, cuenta)
    print("\nCómo leerlo:")
    print("  · login OK + posiciones 500 en TODAS las fechas → el endpoint está roto.")
    print("  · login OK + 500 SOLO en algunas fechas → depende del día que se pide")
    print("    (el job pide el día hábil SIGUIENTE al que snapshotea).")
    print("  · todo 200 → Aunesa ya se recuperó: se puede recuperar el 06/08.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
