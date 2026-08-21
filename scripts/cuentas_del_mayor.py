"""Catálogo de cuentas del mayor: los valores ÚNICOS `codigoCuenta` + `nombreCuenta`.

Existe porque el endpoint **no tiene un listado de plan de cuentas**: una cuenta
solo aparece si tuvo movimientos ese día. Pidiendo un solo día quedan afuera las
que no se movieron, y son justo las que uno no sabe cómo mapear.

La solución es barrer VARIOS DÍAS y quedarse solo con las cuentas distintas. Este
script hace eso: una request por día, y de cada respuesta descarta los ~23.600
movimientos y se queda únicamente con el par (código, nombre).

⚠️ **Va día por día, NO con un rango largo en una sola request.** Un día ya pesa
9,6 MB y tarda entre 75 s y 306 s; un rango de dos semanas en una sola llamada
podría no volver nunca y dejarte sin nada después de esperar. Yendo de a un día,
si el quinto falla igual conservás los cuatro anteriores.

⚠️ **Cada día es una request de ~90-300 s.** Cinco días pueden ser 25 minutos.
Empezá con 2 o 3 y medí antes de pedir un mes.

Uso (en el Droplet):
    python -m scripts.cuentas_del_mayor                              # solo ayer
    python -m scripts.cuentas_del_mayor --desde 11/08/2026 --hasta 19/08/2026
    python -m scripts.cuentas_del_mayor --archivo /tmp/registros_1908.json  # sin red
    python -m scripts.cuentas_del_mayor --desde 18/08/2026 --bancos --csv /tmp/ctas.csv

Read-only: no escribe en la base. Si puede leer `bancos.cuentas`, marca cuáles ya
tienen `codigo_contable` cargado, para que se vea de un vistazo qué falta.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, timedelta

import requests

ENDPOINT = "contabilidad/registrosContables"

# Para el filtro opcional `--bancos`. Es un filtro de lo que se MUESTRA, no un
# criterio de mapeo: el mapeo lo decide una persona mirando la lista.
#
# ⚠️ El PREFIJO del código NO sirve para esto: `101010100004 Asignación a
# inversiones (Regularizadora)` cae en el mismo rango que los bancos y no lo es.
PISTAS_BANCO = ("banco", "bco", "bank", "cvu")


def _dias(desde: date, hasta: date):
    d = desde
    while d <= hasta:
        yield d
        d += timedelta(days=1)


def _parse(f: str) -> date:
    return datetime.strptime(f, "%d/%m/%Y").date()


def _acumular(registros: list, cuentas: dict, dia: str) -> int:
    """Suma al catálogo las cuentas de esta respuesta. Devuelve cuántos
    movimientos miró (para el progreso)."""
    n = 0
    for asiento in registros:
        if not isinstance(asiento, dict):
            continue
        for mov in (asiento.get("movimientos") or []):
            if not isinstance(mov, dict):
                continue
            n += 1
            codigo = str(mov.get("codigoCuenta") or "").strip()
            if not codigo:
                continue
            c = cuentas.setdefault(codigo, {
                "codigo": codigo, "nombres": {}, "movs": 0,
                "monedas": set(), "dias": set()})
            nombre = str(mov.get("nombreCuenta") or "").strip()
            if nombre:
                # Se cuentan las variantes: si un código aparece con dos nombres
                # distintos, eso hay que verlo y no elegir uno en silencio.
                c["nombres"][nombre] = c["nombres"].get(nombre, 0) + 1
            c["movs"] += 1
            c["dias"].add(dia)
            if (u := mov.get("codigoUnidad")):
                c["monedas"].add(str(u).strip().upper())
    return n


def _ya_mapeadas() -> dict[str, str]:
    """`codigo_contable` → descripción de la cuenta bancaria. Si la base no está
    a mano, devuelve vacío: el catálogo sirve igual."""
    try:
        from api.services._sql import _q

        filas = _q("""SELECT codigo_contable, bank_name, account_number, currency
                        FROM bancos.cuentas WHERE codigo_contable IS NOT NULL""")
    except Exception:
        return {}
    return {f["codigo_contable"]: f"{f['bank_name']} {f['account_number']} {f['currency']}"
            for f in filas}


def main() -> None:
    ayer = date.today() - timedelta(days=1)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--archivo", default=None, help="JSON ya guardado (no toca la API)")
    p.add_argument("--desde", default=None, help="dd/mm/yyyy (default: ayer)")
    p.add_argument("--hasta", default=None, help="dd/mm/yyyy (default: igual a --desde)")
    p.add_argument("--sin-nc", action="store_true", help="inclNC=false (default: true)")
    p.add_argument("--bancos", action="store_true",
                   help="mostrar solo las que parecen cuentas bancarias")
    p.add_argument("--csv", default=None, help="ruta donde guardar el listado")
    args = p.parse_args()

    cuentas: dict[str, dict] = {}
    fallidos: list[str] = []

    if args.archivo:
        print(f"Leyendo {args.archivo}…")
        with open(args.archivo, encoding="utf-8") as f:
            data = json.load(f)
        registros = data.get("registros") if isinstance(data, dict) else data
        _acumular(registros or [], cuentas, "(archivo)")
    else:
        from core import aunesa

        desde = _parse(args.desde) if args.desde else ayer
        hasta = _parse(args.hasta) if args.hasta else desde
        dias = list(_dias(desde, hasta))
        print(f"{len(dias)} día(s) · una request por día, ~90-300 s cada una "
              f"→ hasta ~{len(dias) * 5:.0f} min\n")
        for i, d in enumerate(dias, 1):
            f = d.strftime("%d/%m/%Y")
            print(f"  [{i}/{len(dias)}] {f} … ", end="", flush=True)
            try:
                resp = aunesa.get(ENDPOINT,
                                  {"fechaDesde": f, "fechaHasta": f,
                                   "inclNC": "false" if args.sin_nc else "true"},
                                  timeout=300)
            except requests.exceptions.RequestException as e:
                print(f"✗ {type(e).__name__}")
                fallidos.append(f)
                continue
            if resp.status_code == 204:
                print("sin movimientos")
                continue
            if resp.status_code != 200:
                print(f"✗ HTTP {resp.status_code}")
                fallidos.append(f)
                continue
            try:
                data = resp.json()
            except ValueError:
                print("✗ no es JSON")
                fallidos.append(f)
                continue
            registros = data.get("registros") if isinstance(data, dict) else data
            n = _acumular(registros or [], cuentas, f)
            print(f"{n} movs · {len(cuentas)} cuentas distintas acumuladas")

    if not cuentas:
        print("\nNo se juntó ninguna cuenta.")
        return

    mapeadas = _ya_mapeadas()
    filas = sorted(cuentas.values(), key=lambda c: c["codigo"])
    if args.bancos:
        filas = [c for c in filas
                 if any(t in max(c["nombres"], key=c["nombres"].get).casefold()
                        for t in PISTAS_BANCO)]

    print("\n" + "=" * 108)
    print(f"{len(filas)} cuentas{' bancarias' if args.bancos else ''} distintas"
          f"{f' (de {len(cuentas)} en total)' if args.bancos else ''}")
    if mapeadas:
        ya = sum(1 for c in filas if c["codigo"] in mapeadas)
        print(f"{ya} ya tienen codigo_contable cargado · {len(filas) - ya} sin mapear")
    print("=" * 108)
    print(f"\n{'CÓDIGO':<14} {'MOVS':>7} {'DÍAS':>5} {'MONEDA':<8} {'NOMBRE EN EL MAYOR':<46} MAPEO")
    print("-" * 108)
    for c in filas:
        nombre = max(c["nombres"], key=c["nombres"].get)
        monedas = "/".join(sorted(c["monedas"])) or "?"
        marca = f"✔ {mapeadas[c['codigo']]}" if c["codigo"] in mapeadas else ""
        print(f"{c['codigo']:<14} {c['movs']:>7} {len(c['dias']):>5} {monedas:<8} "
              f"{nombre[:46]:<46} {marca}")
        if len(c["nombres"]) > 1:
            otros = ", ".join(n for n in c["nombres"] if n != nombre)
            print(f"{'':<14} {'':>7} {'':>5} {'':<8} ⚠️  también aparece como: {otros[:70]}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["codigo_cuenta", "nombre_cuenta", "movimientos", "dias",
                        "monedas", "codigo_contable_cargado"])
            for c in filas:
                w.writerow([c["codigo"], max(c["nombres"], key=c["nombres"].get),
                            c["movs"], len(c["dias"]), "/".join(sorted(c["monedas"])),
                            mapeadas.get(c["codigo"], "")])
        print(f"\n💾 {args.csv}")

    if fallidos:
        # Un día que no volvió puede ser justo el que tenía la cuenta que falta.
        print(f"\n⚠️  {len(fallidos)} día(s) sin respuesta: {', '.join(fallidos)}")
        print("   El catálogo puede estar incompleto — reintentá esos días.")


if __name__ == "__main__":
    main()
