"""smoke_copiloto.py — ¿por qué no aparece el botón del copiloto?

El botón "✦ CONSULTALE A LA IA" (MERCADO → RENTA VARIABLE) se OCULTA solo si
el backend no responde bien al probe GET /api/ia/copiloto/vistas. Este smoke
le pega a ese endpoint EN EL PROCESO VIVO del Droplet y dice exactamente qué
capa está fallando:

  404            → el api.service corriendo es VIEJO (falta restart tras el pull)
  401            → problema de API_KEY (no debería pasar desde el propio Droplet)
  403            → el email no tiene el módulo `ia` tildado (Manager → ROLES)
  200 sin la vista → mismo caso 403 pero para el módulo renta-variable
  200 con renta_variable → backend PERFECTO: el problema es el browser/Vercel
                     (hard refresh Ctrl+Shift+R, o esperar el deploy del front)

Además, `--contexto` arma el contexto de TODAS las vistas contra la DB real
(0 tokens) y muestra qué bloques trae cada una — la forma de detectar un
bloque que quedó MUDO porque el service que lo alimenta cambió de shape.

Uso (Droplet):
    python -m scripts.smoke_copiloto --contexto        # ¿qué ve la IA en cada vista?
    python -m scripts.smoke_copiloto --email tu@email.com
    python -m scripts.smoke_copiloto --email tu@email.com --pregunta "¿qué sube hoy?"
"""
from __future__ import annotations

import argparse
import json
import os

import requests

from config import API_KEY


def _contexto() -> None:
    """Arma el contexto de CADA vista contra la DB real (0 tokens) y muestra
    cuántas filas y qué bloques trajo.

    Por qué existe: el copiloto no se rompe con una excepción, se rompe en
    SILENCIO. Un bloque `extras` que lee una clave que el service ya no emite
    devuelve [] y desaparece del prompt; el modelo contesta igual, con menos
    datos, y nadie lo nota. Los unit tests no lo ven porque mockean el service.
    Acá se ve de una: bloque que falta = bloque que hay que ir a mirar."""
    from api.services.copiloto.registro import VISTAS

    _MAX = 400   # mismo tope que usa el motor para la tabla
    for vista, cfg in sorted(VISTAS.items()):
        fetch = cfg.get("fetch")
        if not fetch:
            print(f"— {vista:<16} sin fetch (handler propio: {cfg.get('titulo')})")
            continue
        try:
            filas = fetch({}) or []
        except Exception as e:
            print(f"✗ {vista:<16} FETCH ROTO — {type(e).__name__}: {e}")
            continue
        if not filas:
            if cfg.get("depende_de_params"):
                print(f"— {vista:<16} 0 filas SIN PARÁMETROS (su contexto sale de lo "
                      "que el usuario tiene en pantalla) — esperado")
            else:
                print(f"✗ {vista:<16} 0 filas → el copiloto responde "
                      "'datos_no_disponibles'")
            continue
        bloques: list[str] = []
        extras = cfg.get("extras")
        if extras:
            try:
                bloques = [b for b in (extras(filas[:_MAX], "", [], {}) or []) if b]
            except Exception as e:
                print(f"? {vista:<16} {len(filas):>4} filas · EXTRAS ROTOS — "
                      f"{type(e).__name__}: {e}")
                continue
        # el título de cada bloque es su primera línea entre corchetes
        titulos = [b.split("\n", 1)[0][:38] for b in bloques]
        marca = "✓" if (filas and (bloques or not extras)) else "?"
        print(f"{marca} {vista:<16} {len(filas):>4} filas · {len(bloques)} bloques"
              + (f" · {', '.join(titulos[:4])}" if titulos else ""))
    print("\n" + "-" * 78)
    print("Los bloques se arman con la vista en su estado por DEFECTO (sin filtros).\n"
          "Un ✗ o un conteo de bloques más bajo del esperado es la señal de que un\n"
          "service cambió de shape y el bloque quedó mudo.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=(os.getenv("MANAGER_EMAILS", "").split(",")[0] or None),
                    help="email con el que probar (default: primero de MANAGER_EMAILS)")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="URL base del api local")
    ap.add_argument("--pregunta", help="si se pasa, hace una pregunta end-to-end (gasta tokens)")
    ap.add_argument("--contexto", action="store_true",
                    help="armar el contexto de cada vista contra la DB (no gasta tokens)")
    args = ap.parse_args()
    if args.contexto:
        _contexto()
        return
    if not args.email:
        raise SystemExit("falta --email (o MANAGER_EMAILS en el .env)")

    headers = {"x-acaquant-user-email": args.email}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    r = requests.get(f"{args.base}/api/ia/copiloto/vistas", headers=headers, timeout=10)
    print(f"GET /api/ia/copiloto/vistas → HTTP {r.status_code}")
    print(f"body: {r.text[:400]}\n")

    if r.status_code == 404:
        print("VEREDICTO: el api.service que corre NO tiene el endpoint → "
              "systemctl restart api.service (el pull estaba, faltó el restart).")
        return
    if r.status_code == 403:
        print(f"VEREDICTO: {args.email} no tiene el módulo `ia` → tildarlo en "
              "Manager → ROLES Y PERMISOS (o esperar el TTL de 60s del cache).")
        return
    if r.status_code != 200:
        print("VEREDICTO: fallo de auth/base — revisar API_KEY o el puerto (--base).")
        return

    vistas = [v.get("vista") for v in (r.json().get("vistas") or [])]
    if "renta_variable" not in vistas:
        print(f"VEREDICTO: responde pero sin renta_variable (vistas={vistas}) → "
              f"a {args.email} le falta el módulo renta-variable, o el backend "
              "quedó en una versión intermedia (pull + restart).")
        return

    print("VEREDICTO: backend PERFECTO (renta_variable habilitada). Si el botón no se ve, "
          "es el front: hard refresh (Ctrl+Shift+R) en trading.acaquant.com/renta-variable "
          "y verificar que el deploy de Vercel esté 'Ready'.")

    if args.pregunta:
        r2 = requests.post(
            f"{args.base}/api/ia/copiloto",
            headers={**headers, "Content-Type": "application/json"},
            json={"vista": "renta_variable", "pregunta": args.pregunta, "historial": []},
            timeout=90,
        )
        print(f"\nPOST /api/ia/copiloto → HTTP {r2.status_code}")
        print(json.dumps(r2.json(), ensure_ascii=False, indent=2)[:1200])


if __name__ == "__main__":
    main()
