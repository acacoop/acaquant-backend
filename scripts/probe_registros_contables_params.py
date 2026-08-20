"""¿`contabilidad/registrosContables` acepta un filtro por cuenta NO documentado?

La doc declara tres parámetros (`fechaDesde`, `fechaHasta`, `inclNC`) y ninguno
filtra por cuenta: hoy bajamos el día entero (1.840 asientos / 23.405 movimientos
/ 9,6 MB / 93 s) para quedarnos con los 5 movimientos de un banco. Si existiera un
filtro sin documentar, la integración cambia de categoría — de job nocturno a algo
que se puede pedir en vivo.

**Que no esté documentado no significa que no exista**, y la única forma de saberlo
es preguntárselo al servidor.

CÓMO SE INTERPRETA (esto es lo que hace que la prueba sirva):
Casi todas las APIs **ignoran en silencio** los parámetros que no conocen. Si mandás
`cuenta=X` y te devuelve el día completo, eso NO prueba que el filtro no exista: solo
que ese nombre no es. Por eso se manda también un **CONTROL** con un nombre
deliberadamente absurdo:

  · Si el CONTROL devuelve lo mismo que la BASE → la API ignora lo que no entiende.
    Entonces un candidato que devuelva MENOS filas está filtrando de verdad, y uno
    que devuelva lo mismo simplemente no es el nombre correcto.
  · Si el CONTROL da 400 → la API valida los parámetros. Entonces un candidato que
    devuelva 200 ya es señal fuerte de que ese nombre existe.

Sin el control, los dos escenarios se ven igual y cualquier conclusión sería inventada.

⚠️ **CUESTA TIEMPO Y LE PEGA AL CUSTODIO**: cada intento es una request completa de
~90 s. Con la lista por defecto (base + control + 5 candidatos) son ~10 minutos y 7
requests. Correrlo una vez, no en un loop.

Uso (en el Droplet):
    python -m scripts.probe_registros_contables_params
    python -m scripts.probe_registros_contables_params --cuenta 101010100002
    python -m scripts.probe_registros_contables_params --candidatos cuenta,codigoCuenta
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

import requests

from core import aunesa

ENDPOINT = "contabilidad/registrosContables"

# Nombres plausibles. `cuenta` va primero porque es el que YA usa Aunesa en
# `operaciones/informes` — si tienen una convención, es esa.
CANDIDATOS = ("cuenta", "codigoCuenta", "cuentaID", "cuentas", "codigoCuentaContable")

# Nombre que con certeza no existe. Es la vara para saber si la API valida o ignora.
CONTROL = "zzParametroQueNoExiste"


def _medir(params: dict) -> dict:
    t0 = time.monotonic()
    try:
        resp = aunesa.get(ENDPOINT, params, timeout=240)
    except requests.exceptions.RequestException as e:
        return {"error": f"{type(e).__name__}: {e}", "seg": time.monotonic() - t0}
    seg = time.monotonic() - t0
    out = {"status": resp.status_code, "bytes": len(resp.content), "seg": seg}
    if resp.status_code != 200:
        out["cuerpo"] = " ".join((resp.text or "")[:200].split())
        return out
    try:
        data = resp.json()
    except ValueError:
        out["error"] = "200 pero no es JSON"
        return out
    registros = data.get("registros") if isinstance(data, dict) else data
    registros = [r for r in registros or [] if isinstance(r, dict)]
    cuentas = {str(m.get("codigoCuenta") or "")
               for r in registros for m in (r.get("movimientos") or [])
               if isinstance(m, dict)}
    out["asientos"] = len(registros)
    out["movimientos"] = sum(len(r.get("movimientos") or []) for r in registros)
    out["cuentas"] = len(cuentas)
    return out


def _linea(etiqueta: str, r: dict) -> str:
    if "error" in r:
        return f"   {etiqueta:<24} ✗ {r['error']} ({r['seg']:.0f}s)"
    if r.get("status") != 200:
        return f"   {etiqueta:<24} HTTP {r['status']} ({r['seg']:.0f}s)  {r.get('cuerpo','')[:80]}"
    return (f"   {etiqueta:<24} HTTP 200  {r['asientos']:>5} asientos · "
            f"{r['movimientos']:>6} movs · {r['cuentas']:>4} cuentas · "
            f"{r['bytes'] / 1e6:>5.1f} MB · {r['seg']:.0f}s")


def main() -> None:
    ayer = date.today() - timedelta(days=1)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fecha", default=None, help="dd/mm/yyyy (default: ayer)")
    p.add_argument("--cuenta", default="101010100002", help="valor a probar como filtro")
    p.add_argument("--candidatos", default=None, help="lista separada por comas")
    args = p.parse_args()

    fecha = args.fecha or ayer.strftime("%d/%m/%Y")
    base_params = {"fechaDesde": fecha, "fechaHasta": fecha, "inclNC": "true"}
    candidatos = ([c.strip() for c in args.candidatos.split(",") if c.strip()]
                  if args.candidatos else list(CANDIDATOS))

    print("=" * 78)
    print(f"SONDEO · {ENDPOINT} · filtro por cuenta no documentado")
    print(f"fecha {fecha} · valor de prueba {args.cuenta!r}")
    print(f"{len(candidatos) + 2} requests de ~90 s ≈ {(len(candidatos) + 2) * 1.5:.0f} min")
    print("=" * 78 + "\n")

    base = _medir(base_params)
    print(_linea("BASE (sin filtro)", base))
    if base.get("status") != 200:
        print("\n❌ la request base falló: sin una referencia no se puede comparar nada.")
        return

    control = _medir({**base_params, CONTROL: args.cuenta})
    print(_linea(f"CONTROL ({CONTROL})", control))
    valida = control.get("status") != 200
    print(f"\n   → la API {'VALIDA los parámetros' if valida else 'IGNORA lo que no entiende'}"
          f" (según el control)\n")

    hallazgos = []
    for nombre in candidatos:
        r = _medir({**base_params, nombre: args.cuenta})
        print(_linea(nombre, r))
        if r.get("status") == 200 and r.get("asientos", 0) < base["asientos"]:
            hallazgos.append((nombre, r))

    print("\n" + "=" * 78)
    if hallazgos:
        print("✔ FILTRA. Parámetros que devolvieron MENOS que la base:")
        for nombre, r in hallazgos:
            print(f"   {nombre}: {r['asientos']} asientos vs {base['asientos']} "
                  f"({r['cuentas']} cuentas vs {base['cuentas']}) · "
                  f"{r['bytes'] / 1e6:.2f} MB vs {base['bytes'] / 1e6:.2f} MB")
        print("\n   Verificar que lo que volvió sea la cuenta pedida y NO un recorte")
        print("   casual (paginado, límite): mirar que `cuentas` sea 1.")
    elif valida:
        print("✗ Ningún candidato pasó la validación de la API.")
        print("  Como el CONTROL también fue rechazado, la API valida los nombres:")
        print("  estos no existen. Puede existir otro nombre — pedirle la lista al custodio.")
    else:
        print("✗ Todos devolvieron lo mismo que la base.")
        print("  El CONTROL también, así que la API ignora lo que no entiende:")
        print("  esto NO prueba que el filtro no exista, solo que no se llama así.")
        print("  Conclusión operativa: seguir bajando el día entero y filtrar de este lado,")
        print("  y preguntarle al custodio si hay un parámetro de cuenta.")


if __name__ == "__main__":
    main()
