"""aca_congelar_automaticos — pasa a CARGA MANUAL los valores que la planilla de
ACA venía calculando sola. ONE-SHOT.

POR QUÉ EXISTE. Hasta el 2026-08-19 el histórico de ACA tenía series con
`fuente = macro_var:<SERIE>`: el rendimiento mensual NO se guardaba, se calculaba
en cada lectura desde `macro.series_macro` (en prod, A3500 ← DOLAR). Al dar de
baja esa automatización (decisión del user: "nada de ACA tiene que ser
automático") esos números dejan de aparecer — no se borra nada, porque nunca
estuvieron en la base, pero la columna queda vacía y hay que re-tipear meses.

Este script los calcula UNA última vez con la fórmula vieja y los PERSISTE como
carga manual, que es lo que pasan a ser. Después de correrlo, el script se borra
(REGLA #5).

Es SEGURO:
  · READ-ONLY salvo el INSERT en `aca.historico`, acotado a las series que
    declaran una fuente automática y a los períodos que ya existen.
  · NUNCA pisa una celda cargada a mano: si (periodo, serie) ya tiene `mensual`,
    la saltea y lo dice.
  · Idempotente — correrlo dos veces no cambia nada la segunda.
  · Por default es un DRY-RUN: imprime qué escribiría y no toca la base.

Uso (Droplet, raíz):
    python -m scripts.aca_congelar_automaticos            # dry-run, solo muestra
    python -m scripts.aca_congelar_automaticos --aplicar  # escribe
"""
from __future__ import annotations

import argparse

from api.services._sql import _q
from core.postgres import get_pool

ACTOR = "script:aca_congelar_automaticos"


def _cierres_por_mes(serie_macro: str, periodos: list[str]) -> dict[str, float]:
    """Último valor de cada mes — la misma query que usaba `_macro_mensual`."""
    rows = _q(
        "SELECT DISTINCT ON (to_char(fecha, 'YYYY-MM')) "
        "       to_char(fecha, 'YYYY-MM') AS periodo, valor "
        "FROM macro.series_macro "
        "WHERE serie = %(s)s AND valor IS NOT NULL "
        "  AND to_char(fecha, 'YYYY-MM') = ANY(%(p)s) "
        "ORDER BY to_char(fecha, 'YYYY-MM'), fecha DESC",
        {"s": serie_macro, "p": periodos},
    )
    return {r["periodo"]: float(r["valor"]) for r in rows if r["valor"] is not None}


def _variacion(cierres: dict[str, float], periodos: list[str]) -> dict[str, float]:
    """mes / mes anterior − 1. Es un COCIENTE: no depende de la unidad."""
    out: dict[str, float] = {}
    for i, p in enumerate(sorted(periodos)):
        if i == 0:
            continue
        prev, act = cierres.get(sorted(periodos)[i - 1]), cierres.get(p)
        if prev and act is not None:
            out[p] = act / prev - 1.0
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe (sin esto es dry-run)")
    args = ap.parse_args()

    series = _q("SELECT codigo, nombre, fuente, escala FROM aca.series "
                "WHERE fuente <> 'manual'")
    if not series:
        print("No hay ninguna serie con fuente automática — nada que congelar.")
        return

    periodos = sorted({r["periodo"] for r in _q("SELECT periodo FROM aca.historico")} |
                      {r["periodo"] for r in _q("SELECT periodo FROM aca.periodos")})
    if not periodos:
        print("No hay períodos cargados — nada que congelar.")
        return
    print(f"{len(periodos)} períodos: {periodos[0]} → {periodos[-1]}\n")

    ya = {(r["serie"], r["periodo"])
          for r in _q("SELECT serie, periodo FROM aca.historico WHERE mensual IS NOT NULL")}

    total_escribe = total_saltea = 0
    for s in series:
        fuente = s["fuente"]
        modo, _, serie_macro = fuente.partition(":")
        cierres = _cierres_por_mes(serie_macro, periodos)
        if modo == "macro_pct":
            div = float(s["escala"] or 100) or 1.0
            auto = {p: v / div for p, v in cierres.items()}
        else:
            auto = _variacion(cierres, periodos)

        print(f"── {s['codigo']} ({s['nombre']}) · fuente={fuente} · "
              f"{len(auto)} meses calculados")
        for p in sorted(auto):
            if (s["codigo"], p) in ya:
                print(f"     {p}  SALTEADO (ya está cargado a mano)")
                total_saltea += 1
                continue
            print(f"     {p}  {auto[p] * 100:8.4f} %")
            total_escribe += 1
            if args.aplicar:
                with get_pool().connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO aca.historico (periodo, serie, mensual, "
                        "                           actualizado_por, actualizado_at) "
                        "VALUES (%(p)s, %(s)s, %(m)s, %(a)s, now()) "
                        "ON CONFLICT (periodo, serie) DO UPDATE "
                        "  SET mensual = EXCLUDED.mensual, "
                        "      actualizado_por = EXCLUDED.actualizado_por, "
                        "      actualizado_at = now() "
                        "WHERE aca.historico.mensual IS NULL",
                        {"p": p, "s": s["codigo"], "m": auto[p], "a": ACTOR},
                    )
        print()

    print(f"TOTAL: {total_escribe} celdas a escribir, {total_saltea} salteadas.")
    if not args.aplicar:
        print("DRY-RUN — no se escribió nada. Repetí con --aplicar.")


if __name__ == "__main__":
    main()
