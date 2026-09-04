"""¿Qué NO explican los boletos en la CONTABILIDAD de cuentas propias?

Read-only. La tenencia (`portafolio.tenencia`, foto diaria liquidada) MANDA;
los boletos (`operaciones.operaciones`) tienen que explicarla. Cuando no la
explican, hoy la fila sale con ⚠ y el número igual suma. Este diag mide, para
las cuentas del proceso (o una) y un mes, TRES cosas:

  1. PAREJAS FCI (provisional / final). Para cada pareja y cada suelto: cuánto
     se movió la tenencia del fondo EL DÍA del provisional y EL DÍA del final.
     Contesta con datos «¿cuál de los dos boletos mueve la posición?», por
     punta (suscripción / rescate) — hoy es hipótesis.
  2. KARDEX DIARIO por título: Δ nominales de la foto contra los boletos de
     ese día. Un día con Δ que ningún boleto explica es un MOVIMIENTO
     ADMINISTRATIVO (transferencia de títulos, amortización, canje…) valuado al
     precio implícito de la foto. Lista esos días con lo que Aunesa cargó ese
     día aunque no cuente (boletos `ignorados`, tipo_operacion crudo).
  3. INVENTARIO de `tipo_operacion` × `operacion` en las cuentas propias del
     período: qué tipos existen y cuántos caen sin punta (los que la
     contabilidad ignora).

    python -m scripts.diag_conciliacion_contabilidad 2026-08
    python -m scripts.diag_conciliacion_contabilidad 2026-08 --cuenta 100
    python -m scripts.diag_conciliacion_contabilidad 2026-08 --out diag_conta_2026-08.txt
    python -m scripts.diag_conciliacion_contabilidad 2026-08 --out diag_conta_2026-08.json

`--out` escribe TODO al archivo (la consola no entra en una pantalla): `.json`
deja el mismo contenido como líneas en una lista, para pegarlo o leerlo desde
otra sesión sin que se corte.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta

from api.services._sql import _f, _q
from api.services.contabilidad_sql import (
    _VENTANA_PAREJA,
    _direccion,
    _fase_fci,
    _mes_anterior,
    emparejar_provisional_final,
)
from core.calendario import restar_habiles, ultimo_habil_del_mes

TOL = 1e-6


def _linea(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def _n(x: float | None) -> str:
    return f"{x:,.2f}" if x is not None else "—"


def _cuentas(solo: str | None) -> list[str]:
    if solo:
        return [solo]
    return [r["id_cuenta"] for r in _q(
        "SELECT id_cuenta FROM operaciones.contabilidad_cuentas ORDER BY id_cuenta")]


def _serie_tenencia(id_cuenta: str, desde: date, hasta: date) -> dict[str, dict[date, dict]]:
    """unidad → {fecha → {cantidad, valuacion}} (sin cash)."""
    out: dict[str, dict[date, dict]] = defaultdict(dict)
    for r in _q("SELECT fecha, unidad, cantidad, valuacion FROM portafolio.tenencia "
                "WHERE id_cuenta = %(c)s AND fecha BETWEEN %(d)s AND %(h)s "
                "AND cartera IS DISTINCT FROM 'MONEDAS'",
                {"c": id_cuenta, "d": desde, "h": hasta}):
        out[r["unidad"] or ""][r["fecha"]] = {"cantidad": _f(r["cantidad"]) or 0.0,
                                             "valuacion": _f(r["valuacion"]) or 0.0}
    return out


def _ops(id_cuenta: str, desde: date, hasta: date) -> list[dict]:
    return _q(
        "SELECT to_char(concertacion,'YYYY-MM-DD') AS fecha, instrumento, operacion, "
        "tipo_operacion, condiciones, cantidad, bruto, moneda, boleto, etapa, es_cierre "
        "FROM operaciones.operaciones WHERE id_cuenta = %(c)s "
        "AND concertacion BETWEEN %(d)s AND %(h)s AND anulado_en IS NULL "
        "ORDER BY concertacion, boleto", {"c": id_cuenta, "d": desde, "h": hasta})


def _delta_dia(serie: dict[date, dict], f: date,
               dias_foto: list[date] | None = None) -> tuple[float | None, float | None]:
    """(Δ cantidad del día f vs el último día CON foto antes de f, precio
    implícito). `dias_foto` = los días en que la CUENTA tiene foto: un título
    que desaparece de la foto no deja fila con 0, deja de tener fila — sin
    esto la baja entera era invisible."""
    dias = dias_foto if dias_foto is not None else sorted(serie)
    if f not in dias:
        return None, None
    previos = [d for d in dias if d < f]
    if not previos:
        return None, None
    ant = serie.get(max(previos), {}).get("cantidad", 0.0)
    hoy = serie.get(f, {"cantidad": 0.0, "valuacion": 0.0})
    px = hoy["valuacion"] / hoy["cantidad"] if hoy["cantidad"] else None
    if px is None and previos and serie.get(max(previos), {}).get("cantidad"):
        prev = serie[max(previos)]
        px = prev["valuacion"] / prev["cantidad"]  # se fue todo: valúa al último precio
    return hoy["cantidad"] - ant, px


def bloque_parejas(id_cuenta: str, ops: list[dict], ten: dict) -> None:
    fci = [r for r in ops if _fase_fci(r.get("tipo_operacion"))]
    if not fci:
        return
    dias_foto = sorted({d for serie in ten.values() for d in serie})
    _linea(f"1 · PAREJAS FCI  ·  cuenta {id_cuenta}  ·  {len(fci)} boletos provisional/final")
    print("   Δ = cuánto cambió la tenencia del fondo ESE día (foto liquidada).")
    print(f"   {'punta':<7} {'provisional':<12} {'Δ ten. prov':>16} {'final':<12} "
          f"{'Δ ten. final':>16} {'cantidad':>16}  instrumento")
    for r in emparejar_provisional_final(fci):
        serie = ten.get(r.get("instrumento") or "", {})
        t = r.get("tipo_operacion") or ""
        punta = _direccion(r.get("operacion"), t) or "?"
        if "→" in t:  # pareja: «X (provisional dd/mm → final dd/mm)»
            f_prov = r["fecha"]
            dd, mm = t.split("final ")[1].rstrip(")").split("/")
            f_fin = f"{f_prov[:4]}-{mm}-{dd}"
        elif _fase_fci(t) == "provisional":
            f_prov, f_fin = r["fecha"], None
        else:
            f_prov, f_fin = None, r["fecha"]
        d_prov, _ = (_delta_dia(serie, date.fromisoformat(f_prov), dias_foto)
                     if f_prov else (None, None))
        d_fin, _ = (_delta_dia(serie, date.fromisoformat(f_fin), dias_foto)
                    if f_fin else (None, None))
        print(f"   {punta:<7} {f_prov or 'SUELTO':<12} {_n(d_prov):>16} {f_fin or 'SUELTO':<12} "
              f"{_n(d_fin):>16} {_n(_f(r.get('cantidad'))):>16}  {r.get('instrumento')}")


def bloque_kardex(id_cuenta: str, mes: str, ops: list[dict], ten: dict,
                  desde: date, hasta: date) -> Counter:
    """Días con Δ de nominales que ningún boleto explica. Devuelve el conteo
    de tipos crudos vistos esos días (para el resumen final)."""
    anio, m = int(mes[:4]), int(mes[5:7])
    ini_mes, fin_mes = date(anio, m, 1), ultimo_habil_del_mes(anio, m)
    por_dia_unidad: dict[tuple, list[dict]] = defaultdict(list)
    for r in emparejar_provisional_final(ops):
        por_dia_unidad[(date.fromisoformat(r["fecha"]), r.get("instrumento") or "")].append(r)
    tipos_sueltos: Counter = Counter()
    _linea(f"2 · KARDEX DIARIO  ·  cuenta {id_cuenta}  ·  {mes}  ·  Δ tenencia sin boleto")
    hubo = False
    dias_foto = sorted({d for serie in ten.values() for d in serie})
    for unidad, serie in sorted(ten.items()):
        for f in [d for d in dias_foto if ini_mes <= d <= fin_mes]:
            delta, px = _delta_dia(serie, f, dias_foto)
            if delta is None or abs(delta) < TOL:
                continue
            # Boletos que LIQUIDAN ese día: contado inmediato concertado el
            # mismo día + 24hs concertado el hábil anterior.
            explican = 0.0
            candidatos = ([b for b in por_dia_unidad.get((f, unidad), [])
                           if "24" not in (b.get("condiciones") or "")] +
                          [b for b in por_dia_unidad.get((restar_habiles(f, 1), unidad), [])
                           if "24" in (b.get("condiciones") or "")])
            for b in candidatos:
                d = _direccion(b.get("operacion"), b.get("tipo_operacion"))
                q = abs(_f(b.get("cantidad")) or 0.0)
                explican += q if d == "compra" else -q if d == "venta" else 0.0
            resto = delta - explican
            if abs(resto) < TOL:
                continue
            hubo = True
            print(f"\n   {f}  {unidad[:48]:<48}  Δ={_n(delta):>16}  "
                  f"boletos={_n(explican):>14}  SIN EXPLICAR={_n(resto):>16}  "
                  f"px impl={_n(px)}  ≈ ${_n(resto * px) if px else '—'}")
            raw = [b for b in ops if b["fecha"] == f.isoformat()
                   and (b.get("instrumento") or "") == unidad]
            for b in raw:
                d = _direccion(b.get("operacion"), b.get("tipo_operacion")) or "SIN PUNTA"
                tipos_sueltos[(b.get("tipo_operacion"), d)] += 1
                print(f"        boleto {b.get('boleto')}  {b.get('tipo_operacion')!s:<45} "
                      f"punta={d:<10} cant={_n(_f(b.get('cantidad')))} etapa={b.get('etapa')}")
            if not raw:
                print("        (Aunesa no cargó NINGÚN boleto de este título ese día)")
    if not hubo:
        print("   todo explicado: cada Δ de nominales tiene su boleto.")
    return tipos_sueltos


def bloque_inventario(cuentas: list[str], desde: date, hasta: date) -> None:
    _linea(f"3 · INVENTARIO tipo_operacion × operacion  ·  {len(cuentas)} cuentas  ·  "
           f"{desde} → {hasta}")
    filas = _q(
        "SELECT tipo_operacion, operacion, count(*) AS n, "
        "sum(CASE WHEN anulado_en IS NOT NULL THEN 1 ELSE 0 END) AS anulados "
        "FROM operaciones.operaciones WHERE id_cuenta = ANY(%(cs)s) "
        "AND concertacion BETWEEN %(d)s AND %(h)s "
        "GROUP BY 1, 2 ORDER BY n DESC", {"cs": cuentas, "d": desde, "h": hasta})
    print(f"   {'n':>6} {'anul':>5}  {'punta':<10} {'operacion':<14} tipo_operacion")
    for r in filas:
        punta = _direccion(r["operacion"], r["tipo_operacion"]) or "SIN PUNTA"
        print(f"   {r['n']:>6} {r['anulados']:>5}  {punta:<10} {str(r['operacion'])[:14]:<14} "
              f"{r['tipo_operacion']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mes", help="YYYY-MM")
    ap.add_argument("--cuenta", default=None)
    ap.add_argument("--out", default=None, help="archivo .txt o .json con la salida completa")
    a = ap.parse_args()
    if a.out:
        import contextlib
        import io
        import json
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _correr(a)
        texto = buf.getvalue()
        with open(a.out, "w", encoding="utf-8") as fh:
            if a.out.lower().endswith(".json"):
                json.dump({"mes": a.mes, "cuenta": a.cuenta, "lineas": texto.splitlines()},
                          fh, ensure_ascii=False, indent=1)
            else:
                fh.write(texto)
        print(f"escrito {a.out} ({len(texto.splitlines())} líneas)")
        return
    _correr(a)


def _correr(a) -> None:
    anio, m = int(a.mes[:4]), int(a.mes[5:7])
    a0, m0 = _mes_anterior(anio, m)
    desde = ultimo_habil_del_mes(a0, m0) - timedelta(days=_VENTANA_PAREJA)
    hasta = ultimo_habil_del_mes(anio, m) + timedelta(days=_VENTANA_PAREJA)
    cuentas = _cuentas(a.cuenta)
    print(f"cuentas: {cuentas}\nventana: {desde} → {hasta}")
    total_sueltos: Counter = Counter()
    for c in cuentas:
        ops = _ops(c, desde, hasta)
        ten = _serie_tenencia(c, desde, hasta)
        if not ops and not ten:
            print(f"\n[{c}] sin boletos ni tenencia en la ventana")
            continue
        bloque_parejas(c, ops, ten)
        total_sueltos += bloque_kardex(c, a.mes, ops, ten, desde, hasta)
    bloque_inventario(cuentas, desde, hasta)
    if total_sueltos:
        _linea("RESUMEN · tipos de boleto vistos en días con Δ SIN EXPLICAR")
        for (tipo, punta), n in total_sueltos.most_common():
            print(f"   {n:>5}  punta={punta:<10} {tipo}")


if __name__ == "__main__":
    main()
