"""Diag read-only: universo de CUENTAS OPERATIVAS (bancos) de Tesorería.

Recorre los últimos N días contra Aunesa (`cuentas/consultaMovDocsSolicitados`, el
mismo endpoint que la vista) y arma el inventario real: qué cuentas operativas
existen, con qué id/moneda, cuántos movimientos y en cuántos días aparecieron.
Sirve para saber de antemano qué cards van a salir en la vista y para cuáles hay
que cargar saldo inicial — sin esperar a que un banco aparezca por primera vez.

Si la DB es accesible, además cruza contra `operaciones.tesoreria_saldos` y marca
qué cuentas nunca tuvieron saldo cargado.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_tesoreria_cuentas                  # últimos 15 días, Procesado
    python -m scripts.diag_tesoreria_cuentas --dias 60
    python -m scripts.diag_tesoreria_cuentas --hasta 2026-07-31 --dias 30
    python -m scripts.diag_tesoreria_cuentas --estado ""      # sin filtro de estado
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from api.services.tesoreria import (
    _EGRESO,
    _ENDPOINT,
    _INGRESO,
    _cuenta_operativa,
    _fechas,
    _hoy_art,
    _norm,
    _num,
)
from core import aunesa

PAUSA_S = 0.4  # no martillar Aunesa: es una API de terceros, un request por día


def _filas_dia(dia: date, estado: str) -> list[dict]:
    ddmmyyyy, hasta, _ = _fechas(dia.isoformat())
    params: dict = {"liquidacionDesde": ddmmyyyy, "liquidacionHasta": hasta}
    if estado:
        params["estados"] = estado
    resp = aunesa.get(_ENDPOINT, params)
    if resp.status_code == 204:
        return []
    if resp.status_code != 200:
        print(f"  ! {dia} → HTTP {resp.status_code}: {resp.text[:200]}")
        return []
    body = resp.json()
    rows = body if isinstance(body, list) else []
    return [r for r in rows if str(r.get("fecha") or "").strip() == ddmmyyyy]


def _saldos_cargados() -> set[tuple[str, str]] | None:
    """{(cuenta_operativa, unidad)} con al menos un saldo inicial cargado. None si no hay DB."""
    try:
        from api.services._sql import _q
        rows = _q("SELECT DISTINCT cuenta_operativa, unidad FROM operaciones.tesoreria_saldos")
        return {(r["cuenta_operativa"], r["unidad"]) for r in rows}
    except Exception as e:
        print(f"(sin cruce contra tesoreria_saldos: {type(e).__name__})")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dias", type=int, default=15, help="cuántos días hacia atrás (default 15)")
    ap.add_argument("--hasta", help="YYYY-MM-DD del último día a mirar (default: hoy ART)")
    ap.add_argument("--estado", default="Procesado", help="'' = sin filtro de estado")
    a = ap.parse_args()

    fin = date.fromisoformat(a.hasta) if a.hasta else _hoy_art().date()
    dias = max(a.dias, 1)
    print(f"Aunesa {_ENDPOINT} · {dias} días hasta {fin} · estado={a.estado or 'TODOS'}\n")

    # clave = (denominación, unidad) — el id de Aunesa ya lleva la moneda adentro.
    acc: dict[tuple[str, str], dict] = {}
    ids_por_den: dict[str, set[str]] = {}
    dias_con_datos = 0

    for i in range(dias):
        d = fin - timedelta(days=i)
        filas = _filas_dia(d, a.estado)
        if filas:
            dias_con_datos += 1
        print(f"· {d} → {len(filas)} mov.")
        for r in filas:
            co = r.get("cuentaOperativa")
            den = _cuenta_operativa(co) or "SIN CUENTA OPERATIVA"
            uni = (r.get("unidad") or "?").upper()
            if isinstance(co, dict) and co.get("id"):
                ids_por_den.setdefault(den, set()).add(str(co["id"]))
            k = (den, uni)
            e = acc.setdefault(k, {"n": 0, "ing": 0.0, "egr": 0.0, "dias": set()})
            e["n"] += 1
            e["dias"].add(d)
            sol = _norm(r.get("solicitud"))
            if sol == _INGRESO:
                e["ing"] += _num(r.get("monto"))
            elif sol == _EGRESO:
                e["egr"] += _num(r.get("monto"))
        time.sleep(PAUSA_S)

    if not acc:
        print("\nSin movimientos en el rango. Probá --dias 60 o --estado ''.")
        return

    cargados = _saldos_cargados()
    print(f"\n=== CUENTAS OPERATIVAS ({len(acc)}) · {dias_con_datos}/{dias} días con datos ===")
    print(f"{'':<3}{'DENOMINACIÓN':<38}{'UNI':<5}{'DÍAS':>5}{'MOV':>6}{'INGRESOS':>18}{'EGRESOS':>18}")
    for (den, uni), e in sorted(acc.items(), key=lambda kv: -kv[1]["n"]):
        falta = "" if cargados is None else ("   " if (den, uni) in cargados else " ! ")
        print(f"{falta:<3}{den[:37]:<38}{uni:<5}{len(e['dias']):>5}{e['n']:>6}"
              f"{e['ing']:>18,.2f}{e['egr']:>18,.2f}")
    if cargados is not None:
        print("\n'!' = nunca tuvo saldo inicial cargado en operaciones.tesoreria_saldos.")

    multi = {d: ids for d, ids in ids_por_den.items() if len(ids) > 1}
    print("\n=== IDs de Aunesa por denominación ===")
    for den in sorted(ids_por_den):
        print(f"  {den:<40} {sorted(ids_por_den[den])}")
    if multi:
        print(f"\n>>> OJO: {len(multi)} denominación(es) con más de un id "
              f"(la card agrupa por denominación+moneda): {sorted(multi)}")


if __name__ == "__main__":
    main()
