"""Diag READ-ONLY paso 2 — dos preguntas que dejó abiertas `diag_echeq_depositos`.

El paso 1 mostró que la premisa era otra: `operaciones.movimientos` NO está muerta
(último día 2026-08-07, el cron viene ok) y los "Depósito de e-cheque" SÍ pasan el
filtro del writer. Entonces quedan dos preguntas, y este script las mide.

PREGUNTA A — ¿por qué no se ven en DEPÓSITOS & EXTRACCIONES si están en la fuente?
  Sospecha concreta (hipótesis, esto la confirma o la mata): `operaciones.movimientos`
  tiene PK = `comprobante` SOLO, mientras su hermana `operaciones.negocio_movimientos`
  usa `(fecha, comprobante)`. Y `jobs/cashflow.py` NO consolida por boleto: upsertea
  UNA FILA POR LÍNEA CRUDA de Aunesa, con `comprobante` de clave. Si un boleto trae
  varias líneas, la última PISA a las anteriores y la plata de las otras desaparece.
  Se mide comparando, por día: líneas que pasan el filtro vs comprobantes DISTINTOS
  entre ellas vs filas que efectivamente quedaron en la tabla.
  De paso: diff por comprobante contra `negocio_movimientos` (mismo feed, PK completa)
  → qué boletos tiene una y no la otra, y cuánta plata hay en la diferencia.

PREGUNTA B — el puente hacia TESORERÍA.
  El paso 1 midió algo fuerte: en el feed BANCARIO (`consultaMovDocsSolicitados`) hubo
  0 depósitos e-cheq en 3 días, contra 20 extracciones. Por eso la tab CHEQUES →
  RECIBIDOS es 100% carga manual. Pero los mismos días, el feed del COMITENTE
  (`consolidadosGenerales`) trajo 11 "Depósito de e-cheque" por montos grandes. O sea:
  Aunesa SÍ manda esa plata, por otra puerta. Este bloque saca el shape completo de
  esos depósitos y los cruza contra lo que el back office ya cargó a mano en
  `operaciones.tesoreria_cheques` (lado='recibido') — para saber si un espejo
  automático (gemelo de `sincronizar_echeq_emitidos`, que ya existe para los EMITIDOS)
  es viable y cuánto trabajo manual se ahorra.

100% read-only. 1 llamada a Aunesa por día pedido (solo consolidadosGenerales).

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_echeq_paso2
    python -m scripts.diag_echeq_paso2 --dias 5
    python -m scripts.diag_echeq_paso2 --meses 14
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from api.services._sql import _q
from jobs.cashflow import autenticar as _auth
from jobs.cashflow import es_movimiento, fetch_dia

_LINEA = "─" * 78
_FECHA_OK = r"fecha ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$'"


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _titulo(n: str, txt: str) -> None:
    print(f"\n{_LINEA}\n{n}. {txt}\n{_LINEA}")


def _m(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
def bloque_a1(meses: int) -> None:
    """¿Los depósitos de e-cheque están en la tabla que lee la vista?"""
    _titulo("A1", "¿ESTÁN? — operaciones.movimientos, desglose del 'cheq' por tipo")
    print("La columna 'dicen cheq' del paso 1 mezclaba todo. Acá se separa qué es cada cosa.\n")

    rows = _q(
        "SELECT to_char(to_date(fecha,'DD/MM/YYYY'),'YYYY-MM') AS mes, "
        "  count(*) FILTER (WHERE informacion ILIKE '%%e-cheque%%')          AS dep_n, "
        "  sum(total) FILTER (WHERE informacion ILIKE '%%e-cheque%%')        AS dep_ars, "
        "  count(*) FILTER (WHERE informacion ILIKE '%%ECHEQ ID%%')          AS ext_n, "
        "  count(*) FILTER (WHERE informacion ILIKE '%%Recepci%%ECHEQ%%')    AS rec_n, "
        "  count(*) FILTER (WHERE informacion ILIKE '%%cheq%%' "
        "                     AND informacion NOT ILIKE '%%e-cheque%%' "
        "                     AND informacion NOT ILIKE '%%ECHEQ ID%%' "
        "                     AND informacion NOT ILIKE '%%Recepci%%ECHEQ%%') AS otros_n "
        f"  FROM movimientos WHERE {_FECHA_OK} "
        " GROUP BY 1 ORDER BY 1 DESC LIMIT %(m)s", {"m": meses})

    print(f"{'mes':>9}  {'DEPÓSITO e-cheque':>18}  {'suma (mezcla mon.)':>20}  "
          f"{'EXTRACC ECHEQ':>14}  {'Recepción':>10}  {'otros cheq':>11}")
    for r in rows:
        print(f"{r['mes']:>9}  {r['dep_n']:>18,}  {_m(r['dep_ars']):>20,.0f}  "
              f"{r['ext_n']:>14,}  {r['rec_n']:>10,}  {r['otros_n']:>11,}")

    tot_dep = sum(r["dep_n"] for r in rows)
    if tot_dep == 0:
        print("\n❌ CERO 'Depósito de e-cheque' en la tabla que lee la vista, aunque el")
        print("   filtro del writer los acepta → se pierden DESPUÉS del filtro (ver A2).")
    else:
        print(f"\n✔ Hay {tot_dep:,} 'Depósito de e-cheque' en la tabla. Si no se ven en la")
        print("  vista, el problema NO es la ingesta: es la lectura/el filtro de la pantalla.")


# ─────────────────────────────────────────────────────────────────────────────
def bloque_a2(dias: list[date]) -> None:
    """Colapso por PK: líneas de Aunesa vs comprobantes distintos vs filas guardadas."""
    _titulo("A2", "¿CUÁNTO PIERDE EL PK? — líneas de Aunesa vs filas en la tabla")
    print("`movimientos` tiene PK = comprobante SOLO y el writer NO consolida por boleto:")
    print("upsertea una fila por LÍNEA cruda. Dos líneas del mismo comprobante = la 2ª pisa")
    print("a la 1ª. Si `líneas` > `comprobantes`, hay boletos multilínea y hay plata perdida.\n")

    headers = _auth()
    print(f"{'día':>12}  {'líneas ok':>10}  {'compr. dist':>12}  {'colapso':>8}  "
          f"{'filas tabla':>12}  {'Σ líneas':>18}  {'Σ tabla':>18}")
    for d in dias:
        dstr = d.strftime("%d/%m/%Y")
        try:
            data, headers = fetch_dia(dstr, headers)
        except Exception as e:
            print(f"{dstr:>12}  ❌ {str(e)[:60]}")
            continue
        lineas = [r for r in (data or []) if es_movimiento(r.get("informacion", ""))]
        lineas = [r for r in lineas if r.get("comprobante")]
        compr = {r["comprobante"] for r in lineas}
        # El writer invierte el signo antes de guardar → se compara con el mismo signo.
        suma_lineas = -sum(_m(r.get("total")) for r in lineas)

        fila = _q(
            "SELECT count(*) AS n, sum(total) AS s FROM movimientos "
            " WHERE fecha = %(f)s", {"f": dstr})[0]
        colapso = len(lineas) - len(compr)
        marca = "  ⚠" if colapso else ""
        print(f"{dstr:>12}  {len(lineas):>10,}  {len(compr):>12,}  {colapso:>8,}{marca}  "
              f"{fila['n']:>12,}  {suma_lineas:>18,.0f}  {_m(fila['s']):>18,.0f}")

    print("\n`colapso` > 0  → ESE número de líneas se sobreescribieron entre sí (plata perdida).")
    print("`filas tabla` < `compr. dist` → además hay comprobantes que ni llegaron, o que")
    print("otro DÍA pisó (el PK no incluye la fecha: un comprobante repetido migra de día).")

    # ¿Se repite un comprobante en fechas distintas? Eso rompe el histórico entero.
    dup = _q(
        "SELECT comprobante, count(DISTINCT fecha) AS dias FROM movimientos "
        " GROUP BY comprobante HAVING count(DISTINCT fecha) > 1 LIMIT 5")
    print(f"\nComprobantes que aparecen con MÁS DE UNA fecha en la tabla: {len(dup)}"
          " (imposible: el PK los colapsa — si sale >0 es que el schema real difiere)")


# ─────────────────────────────────────────────────────────────────────────────
def bloque_a3(dias: list[date]) -> None:
    """Diff por comprobante contra negocio_movimientos (mismo feed, PK completa)."""
    _titulo("A3", "DIFF — operaciones.movimientos vs operaciones.negocio_movimientos")
    print("Mismo origen, misma categorización. Lo que está en una y no en la otra es pérdida.\n")

    cats = ("deposito", "transferencia", "extraccion")
    for d in dias:
        dstr = d.strftime("%d/%m/%Y")
        mov = _q("SELECT comprobante, total, informacion FROM movimientos "
                 " WHERE fecha = %(f)s", {"f": dstr})
        neg = _q("SELECT comprobante, importe, informacion FROM negocio_movimientos "
                 " WHERE fecha = %(f)s AND categoria = ANY(%(c)s) AND anulado_en IS NULL",
                 {"f": d.isoformat(), "c": list(cats)})
        sm = {r["comprobante"] for r in mov}
        sn = {r["comprobante"] for r in neg}
        solo_neg = sn - sm
        solo_mov = sm - sn
        plata_neg = sum(_m(r["importe"]) for r in neg if r["comprobante"] in solo_neg)
        print(f"  {d}:  movimientos={len(sm):>4}  negocio={len(sn):>4}  "
              f"| solo en negocio={len(solo_neg):>4} (Σ {plata_neg:>18,.0f})  "
              f"| solo en movimientos={len(solo_mov):>3}")
        muestras = [r for r in neg if r["comprobante"] in solo_neg][:6]
        for r in muestras:
            print(f"        falta: {r['comprobante']:>16}  {_m(r['importe']):>18,.2f}  "
                  f"{str(r['informacion'])[:45]}")


# ─────────────────────────────────────────────────────────────────────────────
def bloque_b(dias: list[date]) -> None:
    """El puente a Tesorería: shape de los depósitos e-cheq + cruce con la carga manual."""
    _titulo("B", "TESORERÍA — los DEPÓSITOS e-cheq que Aunesa manda por la otra puerta")
    print("El feed bancario dio 0 depósitos e-cheq. El del comitente los tiene. Acá está")
    print("todo lo que trae cada uno, y cuánto de eso el back office ya tipeó a mano.\n")

    headers = _auth()
    depositos: list[dict] = []
    for d in dias:
        dstr = d.strftime("%d/%m/%Y")
        try:
            data, headers = fetch_dia(dstr, headers)
        except Exception as e:
            print(f"  {dstr}: ❌ {str(e)[:80]}")
            continue
        for r in (data or []):
            if "e-cheque" in str(r.get("informacion") or "").lower():
                depositos.append({"_dia": d, **r})

    print(f"Depósitos de e-cheque en la ventana: {len(depositos)}\n")
    if not depositos:
        print("(ninguno en estos días — reintentar con --dias más grande)")
        return

    print(f"{'día':>12}  {'unidad':>6}  {'importe':>20}  cuenta")
    por_dia: dict[date, float] = defaultdict(float)
    for r in depositos:
        # Aunesa manda el depósito con signo de broker (negativo); el writer lo invierte.
        imp = -_m(r.get("total"))
        por_dia[r["_dia"]] += imp
        print(f"{r['_dia']!s:>12}  {r.get('unidad') or ''!s:>6}  {imp:>20,.2f}  "
              f"{str(r.get('cuenta') or '')[:44]}")
    print("\nTotal por día:")
    for d, s in sorted(por_dia.items()):
        print(f"   {d}  {s:>22,.2f}")

    print("\nShape crudo del 1er depósito (campos no vacíos) — para saber qué se puede")
    print("espejar y qué falta (¿trae banco? ¿CUIT? ¿id de e-cheq?):")
    for k, v in sorted(depositos[0].items()):
        if k != "_dia" and v not in (None, "", {}, []):
            print(f"   {k:<26} {str(v)[:74]}")

    # Cruce contra lo que el back office cargó a mano en la tab CHEQUES → RECIBIDOS.
    d0, d1 = min(x["_dia"] for x in depositos), max(x["_dia"] for x in depositos)
    manual = _q(
        "SELECT id, banco, unidad, importe, estado, comitente_denominacion, "
        "       creado_at::date AS dia, origen "
        "  FROM tesoreria_cheques "
        " WHERE lado = 'recibido' AND tipo = 'echeq' "
        "   AND creado_at::date BETWEEN %(a)s AND %(b)s "
        " ORDER BY creado_at", {"a": d0, "b": d1})
    print(f"\nCheques RECIBIDOS e-cheq cargados A MANO en esos días: {len(manual)}")
    for r in manual:
        print(f"   {r['dia']}  {str(r['banco'])[:22]:<22} {r['unidad']!s:>4} "
              f"{_m(r['importe']):>18,.2f}  {r['estado']:<10} {r['origen']}")

    n_api = len(depositos)
    print(f"\n→ Aunesa informó {n_api} depósito(s) e-cheq; el equipo cargó {len(manual)} a mano.")
    if n_api and not manual:
        print("  El back office NO los está cargando: esa plata no entra a la fila")
        print("  'Ingresos e-cheqs' de BANCOS → el saldo final del banco queda corto.")
    elif n_api and manual:
        print("  Los dos lados existen → un espejo automático reemplaza el tipeo, pero")
        print("  necesita dedup contra lo manual o duplica el ingreso en el saldo.")


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=int, default=3, help="días hábiles a inspeccionar (def 3)")
    ap.add_argument("--meses", type=int, default=14, help="ventana del histograma (def 14)")
    args = ap.parse_args()

    from core.calendario import ultimos_habiles
    dias = ultimos_habiles(_hoy_art(), max(0, args.dias - 1))

    print(f"\nDIAG e-cheq PASO 2   (hoy ART: {_hoy_art()})")
    print(f"Días: {', '.join(str(d) for d in dias)}")

    bloque_a1(args.meses)
    bloque_a2(dias)
    bloque_a3(dias)
    bloque_b(dias)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
