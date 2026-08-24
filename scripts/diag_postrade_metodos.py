"""scripts/diag_postrade_metodos.py — ¿QUÉ métodos de Postrade nos habilitaron?

READ-ONLY, y no por promesa: recorre **solo** los métodos marcados como LECTURA
en `core/postrade_catalogo.py`. Los de escritura (`NewOrderSingle`,
`CancelOrder`, `AccountStatus`, `ChangePassword`…) ni se rozan — no están en la
lista que este script itera, así que no hay forma de que uno se cuele por un
typo.

ETAPA 2 de la integración. La etapa 1 respondió «¿podemos entrar?». Esta
responde la que viene: **de los ~40 métodos de lectura que documenta el manual,
cuáles contesta de verdad NUESTRO usuario**. Es la diferencia entre lo que la
API ofrece y lo que nos dieron, y no se puede inferir de ningún papel (REGLA #2)
— hay que preguntárselo a la API.

Sin esto, cualquier decisión sobre qué construir arriba sería una apuesta.

Para cada método informa una de cuatro cosas, y cada una lleva a un lugar
distinto:

  ✓ CON DATOS      → sirve, y ya trae información. Se puede construir encima.
  ○ VACÍO          → habilitado, pero sin datos. Puede ser real (no operamos
                     eso) o faltar un parámetro: NO es lo mismo que un permiso
                     denegado y por eso se muestran separados.
  ⊘ NO HABILITADO  → el token vale pero el método no. Reclamo al proveedor.
  ✗ ERROR          → otra cosa (falta un parámetro obligatorio, red, formato).

Uso (desde la raíz del repo):
    python -m scripts.diag_postrade_metodos
    python -m scripts.diag_postrade_metodos --json    # salida para procesar
"""
from __future__ import annotations

import argparse
import json
import sys

from core import postrade
from core.postrade_catalogo import ESCRITURAS, LECTURAS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "=" * 78


def _tamano(valor) -> str:
    """Cuánto trajo, en una palabra."""
    if valor is None:
        return "null"
    if isinstance(valor, list):
        return f"{len(valor)} items"
    if isinstance(valor, dict):
        return f"dict({len(valor)} claves)"
    return f"{type(valor).__name__}"


def _vacio(valor) -> bool:
    return valor is None or (isinstance(valor, (list, dict, str)) and len(valor) == 0)


def probar_uno(m) -> dict:
    """Llama a UN método de lectura y clasifica el resultado."""
    if m.obligatorios and not m.prueba:
        return {
            "metodo": m.nombre, "estado": "sin_probar",
            "detalle": f"pide {', '.join(m.obligatorios)} y no hay valor de prueba declarado",
        }
    try:
        valor = postrade.leer(m.nombre, dict(m.prueba) if m.prueba else None)
    except postrade.PostradeNoHabilitado as e:
        return {"metodo": m.nombre, "estado": "no_habilitado", "detalle": str(e)[:200]}
    except postrade.PostradeAuthError as e:
        return {"metodo": m.nombre, "estado": "auth", "detalle": str(e)[:200]}
    except (postrade.PostradeError, ValueError) as e:
        return {"metodo": m.nombre, "estado": "error", "detalle": str(e)[:200]}

    return {
        "metodo": m.nombre,
        "estado": "vacio" if _vacio(valor) else "con_datos",
        "detalle": _tamano(valor),
        "muestra": json.dumps(valor, ensure_ascii=False)[:220] if not _vacio(valor) else "",
    }


MARCA = {
    "con_datos": "✓", "vacio": "○", "no_habilitado": "⊘",
    "error": "✗", "auth": "✗", "sin_probar": "–",
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Releva qué métodos de Postrade responden.")
    ap.add_argument("--json", action="store_true", help="salida JSON en vez de tabla")
    args = ap.parse_args()

    if not args.json:
        print(SEP)
        print("RELEVAMIENTO DE MÉTODOS — API Postrade (A3 Mercados / ACyRSA)")
        print(SEP)
        print(f"  Base URL : {postrade._base()}")
        print(f"  Lecturas a probar : {len(LECTURAS)}")
        print(f"  Escrituras EXCLUIDAS de esta prueba : {len(ESCRITURAS)}")
        print("  (los métodos con efecto real no se llaman ni para diagnosticar)\n")

    # Un solo token para todo el relevamiento: se pide una vez y se reusa.
    try:
        postrade.token()
    except postrade.PostradeError as e:
        print(f"✗ No se pudo obtener token: {e}")
        raise SystemExit(2) from None

    resultados = [probar_uno(m) for m in LECTURAS]

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
        return

    for r in resultados:
        print(f"  {MARCA.get(r['estado'], '?')} {r['metodo']:<34} {r['detalle'][:120]}")
        if r.get("muestra"):
            print(f"      {r['muestra']}")

    print()
    print(SEP)
    print("RESUMEN")
    print(SEP)
    for estado, etiqueta in (
        ("con_datos", "✓ CON DATOS      — sirven y ya traen información"),
        ("vacio", "○ VACÍO          — habilitados, sin datos (o falta un filtro)"),
        ("no_habilitado", "⊘ NO HABILITADO  — reclamo al proveedor"),
        ("error", "✗ ERROR          — revisar parámetros"),
        ("auth", "✗ AUTH           — problema de credenciales"),
        ("sin_probar", "– SIN PROBAR     — piden parámetro obligatorio, no se inventa"),
    ):
        nombres = [r["metodo"] for r in resultados if r["estado"] == estado]
        if nombres:
            print(f"\n  {etiqueta}  ({len(nombres)})")
            print(f"    {', '.join(nombres)}")

    print("\n  Los ✓ son el material con el que se decide la etapa 3 "
          "(qué se persiste y para qué pregunta de negocio).")


if __name__ == "__main__":
    main()
