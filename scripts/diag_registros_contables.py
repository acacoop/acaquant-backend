"""Diag read-only: ¿qué devuelve `GET contabilidad/registrosContables` de Aunesa?

Endpoint NUEVO para nosotros (documentación del custodio, 2026-08-20): asientos
contables entre dos fechas, cada uno con sus movimientos. Todavía no lo consume
nada de la app — esto es la primera medición, para ver la forma REAL de la
respuesta antes de escribir cualquier integración.

Va por `core.aunesa` (el cliente único: login cacheado, re-auth ante 401, retry
de timeout y anotación en `core/proveedores`). NO se copia el login acá.

Lo que imprime, de menos a más:
  1. PEDIDO    — endpoint y parámetros exactos que se mandan.
  2. RESPUESTA — status, tiempo, y las claves de primer nivel que llegaron
                 (para contrastar contra lo que promete la doc).
  3. RESUMEN   — asientos, movimientos, y el ranking de cuentas por cantidad.
  4. CUENTA    — el detalle de la cuenta buscada (por defecto la de Patagonia).
  5. CRUDO     — un asiento entero tal cual vino: los tipos y los nombres de
                 campo de la doc son "string" para todo y hay que verificarlos.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_registros_contables
    python -m scripts.diag_registros_contables --sin-nc
    python -m scripts.diag_registros_contables --desde 01/08/2026 --hasta 19/08/2026
    python -m scripts.diag_registros_contables --cuenta "Patagonia" --max 50
    python -m scripts.diag_registros_contables --todas          # sin filtro de cuenta
    python -m scripts.diag_registros_contables --guardar /tmp/registros.json

NO escribe nada en la base ni imprime credenciales.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date, timedelta

import requests

from core import aunesa

ENDPOINT = "contabilidad/registrosContables"

# La cuenta que motivó la prueba. Se matchea por SUBCADENA sobre `codigoCuenta` y
# `nombreCuenta`: no sabemos todavía cómo viene escrita (con corchetes, con el
# número de CC pegado, o partida en dos campos), así que pedir el string entero
# sería asumir el formato que justamente venimos a medir.
CUENTA_DEFAULT = "101010100002"


def _fecha_ar(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _cuerpo(texto: str, n: int = 400) -> str:
    t = " ".join((texto or "").split())
    return (t[:n] + "…") if len(t) > n else (t or "(vacío)")


def _num(v) -> float | None:
    """La doc declara TODOS los campos como "string", incluidos los importes.
    Se aceptan los dos formatos posibles (1234.56 y 1.234,56); si no se puede
    leer, devuelve None y el resumen lo cuenta aparte en vez de inventar un 0."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _plata(x: float) -> str:
    return f"{x:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _movimientos(registros: list[dict]) -> list[tuple[dict, dict]]:
    """Aplana a (asiento, movimiento). Tolera `movimientos` ausente o null: la
    doc lo muestra siempre presente, pero eso es el ejemplo, no el contrato."""
    out = []
    for asiento in registros:
        if not isinstance(asiento, dict):
            continue
        for mov in (asiento.get("movimientos") or []):
            if isinstance(mov, dict):
                out.append((asiento, mov))
    return out


def _matchea(mov: dict, aguja: str) -> bool:
    aguja = aguja.casefold()
    return any(aguja in str(mov.get(campo) or "").casefold()
               for campo in ("codigoCuenta", "nombreCuenta", "cuentaID", "codigoExp"))


def paso_pedido(params: dict) -> None:
    print("\n1) PEDIDO")
    print(f"   GET {aunesa.BASE_URL}/{ENDPOINT}")
    for k, v in params.items():
        print(f"      {k} = {v}")


def paso_respuesta(params: dict) -> dict | list | None:
    print("\n2) RESPUESTA")
    t0 = time.monotonic()
    try:
        resp = aunesa.get(ENDPOINT, params, timeout=120)
    except aunesa.AunesaCaido as e:
        print(f"   ❌ Aunesa caído: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"   ❌ {type(e).__name__}: {e}")
        return None
    seg = time.monotonic() - t0
    print(f"   HTTP {resp.status_code} en {seg:.1f} s  ({len(resp.content)} bytes)")
    if resp.status_code == 204:
        print("   ✔ 204 = el rango no tiene registros (no es un error)")
        return None
    if resp.status_code != 200:
        # 400 y 401 traen `errors[].title/detail` según la doc: se muestra crudo
        # porque es lo único que dice si falta un permiso de Hígyrus.
        print(f"   ❌ cuerpo: {_cuerpo(resp.text, 800)}")
        return None
    try:
        data = resp.json()
    except ValueError:
        print(f"   ❌ 200 pero la respuesta no es JSON: {_cuerpo(resp.text, 300)}")
        return None
    if isinstance(data, dict):
        print(f"   claves de primer nivel: {sorted(data.keys())}")
    else:
        print(f"   la raíz NO es un objeto, es {type(data).__name__}")
    return data


def paso_resumen(registros: list[dict], pares: list[tuple[dict, dict]]) -> None:
    print("\n3) RESUMEN")
    print(f"   asientos: {len(registros)}   movimientos: {len(pares)}")
    if not pares:
        return

    campos = Counter()
    for _, mov in pares:
        campos.update(mov.keys())
    print(f"   campos vistos en los movimientos: {sorted(campos)}")

    por_cuenta: dict[tuple[str, str], list[float | None]] = {}
    for _, mov in pares:
        clave = (str(mov.get("codigoCuenta") or "?"), str(mov.get("nombreCuenta") or ""))
        por_cuenta.setdefault(clave, []).append(_num(mov.get("valuacion")))

    print(f"   cuentas distintas: {len(por_cuenta)}   top 15 por cantidad de movimientos:")
    top = sorted(por_cuenta.items(), key=lambda kv: -len(kv[1]))[:15]
    for (codigo, nombre), vals in top:
        leidos = [v for v in vals if v is not None]
        ilegibles = len(vals) - len(leidos)
        extra = f"  ({ilegibles} valuaciones ilegibles)" if ilegibles else ""
        print(f"      {len(vals):>5} mov  Σ {_plata(sum(leidos)):>18}  "
              f"{codigo} {nombre[:45]}{extra}")


def paso_cuenta(pares: list[tuple[dict, dict]], aguja: str, maximo: int) -> None:
    print(f"\n4) CUENTA BUSCADA — subcadena {aguja!r}")
    encontrados = [(a, m) for a, m in pares if _matchea(m, aguja)]
    if not encontrados:
        print("   (ninguna) — el rango no la tiene o viene escrita distinto.")
        print("   Mirar el ranking del paso 3 y reintentar con --cuenta <otro texto>.")
        return
    total = sum(v for _, m in encontrados if (v := _num(m.get("valuacion"))) is not None)
    print(f"   {len(encontrados)} movimientos   Σ valuación {_plata(total)}")
    for asiento, mov in encontrados[:maximo]:
        print(f"      {str(asiento.get('fechaAlta') or '')[:10]:>10} "
              f"as.{str(asiento.get('numero') or '?'):>10}  "
              f"{_plata(_num(mov.get('valuacion')) or 0.0):>16}  "
              f"cant={mov.get('cantidad')} factor={mov.get('factor')}  "
              f"{str(mov.get('comprobante') or '')[:18]:<18} "
              f"{str(mov.get('referencia') or asiento.get('referencia') or '')[:50]}")
    if len(encontrados) > maximo:
        print(f"      … {len(encontrados) - maximo} más (subir --max para verlos)")


def paso_crudo(registros: list[dict]) -> None:
    print("\n5) UN ASIENTO CRUDO (para verificar nombres y tipos reales)")
    if not registros:
        print("   (no hay)")
        return
    # El primero que tenga movimientos: un asiento vacío no muestra nada útil.
    muestra = next((r for r in registros if r.get("movimientos")), registros[0])
    print(json.dumps(muestra, ensure_ascii=False, indent=2)[:4000])


def main() -> None:
    ayer = date.today() - timedelta(days=1)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", default=None, help="dd/mm/yyyy (default: ayer)")
    p.add_argument("--hasta", default=None, help="dd/mm/yyyy (default: igual a --desde)")
    p.add_argument("--sin-nc", action="store_true", help="inclNC=false (default: true)")
    p.add_argument("--cuenta", default=CUENTA_DEFAULT,
                   help=f"subcadena a buscar en las cuentas (default: {CUENTA_DEFAULT})")
    p.add_argument("--todas", action="store_true", help="no filtrar por cuenta")
    p.add_argument("--max", type=int, default=25, help="movimientos a listar en el paso 4")
    p.add_argument("--guardar", default=None, help="ruta donde volcar el JSON completo")
    args = p.parse_args()

    desde = args.desde or _fecha_ar(ayer)
    params = {
        "fechaDesde": desde,
        "fechaHasta": args.hasta or desde,
        # La doc lo declara requerido y en minúsculas ("true/false"), no booleano JSON.
        "inclNC": "false" if args.sin_nc else "true",
    }

    print("=" * 78)
    print("DIAG · Aunesa · registros contables")
    print("=" * 78)
    paso_pedido(params)
    data = paso_respuesta(params)
    if data is None:
        return

    registros = data.get("registros") if isinstance(data, dict) else data
    if not isinstance(registros, list):
        print(f"\n⚠️  `registros` no es una lista (es {type(registros).__name__}). "
              "La respuesta entera va abajo.")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
        return

    pares = _movimientos(registros)
    paso_resumen(registros, pares)
    if not args.todas:
        paso_cuenta(pares, args.cuenta, args.max)
    paso_crudo(registros)

    if args.guardar:
        with open(args.guardar, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n💾 JSON completo en {args.guardar}")


if __name__ == "__main__":
    main()
