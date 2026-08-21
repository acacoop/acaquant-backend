"""Diag read-only: ¿el `codigo_contable` de cada cuenta apunta a la cuenta correcta?

El mapeo `bancos.cuentas.codigo_contable → codigoCuenta de Aunesa` se carga A MANO
y es lo ÚNICO que decide qué movimientos del mayor son de qué banco. Un mapeo mal
hecho no se ve como un error: se ve como una diferencia de conciliación, porque
compara el mayor de una cuenta contra el extracto de otra.

Este script pone las dos puntas al lado:

  A) MAPEADAS   — por cada `codigo_contable` cargado, CÓMO LA LLAMA AUNESA
                  (`nombreCuenta`) junto a cómo la llamamos nosotros. Si el
                  nombre de Aunesa no nombra el mismo banco, el mapeo está mal
                  y ahí termina el diagnóstico.
  B) SIN MAPEAR — las demás cuentas del día, por peso. Sirve para lo inverso:
                  encontrar el banco que SÍ debería estar y no está.

⚠️ Es el humano el que decide: acá NO se adivina qué cuenta contable «parece» un
banco. Nombres como `CCL BANCO DE VALORES A3 MERCADOS — Posiciones en garantía`
nombran un banco sin ser cuentas bancarias, y una versión anterior de este aviso
en `jobs/mayor_sync` marcó siete cuentas de las que seis eran falsos positivos.
El script muestra los nombres y se calla.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_mayor_mapeo --archivo /tmp/reg2008.json   # sin red
    python -m scripts.diag_mayor_mapeo --fecha 20/08/2026            # pega a Aunesa (~5 min)

NO escribe nada en la base.
"""
from __future__ import annotations

import argparse
import json
from datetime import date

from core import aunesa
from core.postgres import get_job_pool
from jobs.mayor_sync import ENDPOINT, TIMEOUT_S, _num


def _plata(x: float) -> str:
    return f"{x:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _nuestras() -> list[dict]:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT codigo_contable, bank_name, account_label, account_number,
                      account_type, currency
                 FROM bancos.cuentas
                WHERE activa AND codigo_contable IS NOT NULL
                ORDER BY codigo_contable""")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _del_dia(registros: list[dict]) -> dict[str, dict]:
    """`codigoCuenta` → {nombre, movimientos, suma} de todo el día contable."""
    out: dict[str, dict] = {}
    for asiento in registros:
        if not isinstance(asiento, dict):
            continue
        for mov in (asiento.get("movimientos") or []):
            if not isinstance(mov, dict):
                continue
            cod = str(mov.get("codigoCuenta") or "").strip()
            if not cod:
                continue
            e = out.setdefault(cod, {"nombre": "", "movimientos": 0, "suma": 0.0})
            e["nombre"] = e["nombre"] or str(mov.get("nombreCuenta") or "").strip()
            e["movimientos"] += 1
            e["suma"] += _num(mov.get("valuacion")) or 0.0
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--archivo", default=None,
                   help="JSON ya guardado por diag_registros_contables (no toca la API)")
    p.add_argument("--fecha", default=None, help="dd/mm/yyyy (si no hay --archivo)")
    args = p.parse_args()

    if args.archivo:
        with open(args.archivo, encoding="utf-8") as f:
            data = json.load(f)
    else:
        f = args.fecha or date.today().strftime("%d/%m/%Y")
        print(f"pidiendo {f} a Aunesa (tarda varios minutos)…")
        resp = aunesa.get(ENDPOINT, {"fechaDesde": f, "fechaHasta": f, "inclNC": "true"},
                          timeout=TIMEOUT_S, retries=1)
        resp.raise_for_status()
        data = resp.json()

    registros = data.get("registros") if isinstance(data, dict) else data
    dia = _del_dia([r for r in registros or [] if isinstance(r, dict)])
    nuestras = _nuestras()
    mapeados = {c["codigo_contable"] for c in nuestras}

    print("=" * 100)
    print("A) CUENTAS MAPEADAS — comparar las DOS columnas de nombre")
    print("=" * 100)
    print(f"{'código':<14} {'NOSOTROS (bancos.cuentas)':<40} {'AUNESA (nombreCuenta)':<34} {'movs':>5} {'suma':>18}")
    for c in nuestras:
        cod = c["codigo_contable"]
        d = dia.get(cod)
        nuestro = f"{c['bank_name'] or ''} · {c['account_label'] or c['account_number']}"
        nuestro += f" ({c['account_type']} {c['currency']})"
        if d is None:
            print(f"{cod:<14} {nuestro[:39]:<40} {'— sin movimientos ese día —':<34} {'':>5} {'':>18}")
            continue
        print(f"{cod:<14} {nuestro[:39]:<40} {d['nombre'][:33]:<34} "
              f"{d['movimientos']:>5} {_plata(d['suma']):>18}")

    sin = sorted(((cod, d) for cod, d in dia.items() if cod not in mapeados),
                 key=lambda kv: -abs(kv[1]["suma"]))
    print()
    print("=" * 100)
    print(f"B) SIN MAPEAR — {len(sin)} cuentas, por peso. ¿Falta alguna que SÍ sea un banco?")
    print("=" * 100)
    print(f"{'código':<14} {'AUNESA (nombreCuenta)':<58} {'movs':>5} {'suma':>18}")
    for cod, d in sin[:40]:
        print(f"{cod:<14} {d['nombre'][:57]:<58} {d['movimientos']:>5} {_plata(d['suma']):>18}")
    if len(sin) > 40:
        print(f"   … {len(sin) - 40} más")


if __name__ == "__main__":
    main()
