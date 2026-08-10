"""Diag READ-ONLY paso 3 — dónde se evaporan los DEPÓSITOS.

Lo que ya está MEDIDO (paso 2, no hipótesis):
  · El 06/08 Aunesa mandó 66 comprobantes distintos que pasan el filtro y en
    `operaciones.movimientos` quedaron 57. El 07/08: 60 → 54.
  · Los que faltan son TODOS "Depósito ..." (e-cheque, cheques, común). Ni una
    extracción, ni una transferencia. `solo en movimientos = 0` los tres días.
  · El colapso por comprobante repetido dentro del MISMO día es 1 y 2 → NO
    explica 9 ni 6. La pérdida es otra cosa.
  · `jobs/cashflow.py` y `core/pg_mirror.py` no se tocan desde febrero → no es
    una regresión de código nuestra.

Quedan tres explicaciones posibles y este script las separa:

  H1 — LA FILA ESTÁ, CON OTRA FECHA. El job pide por `concertacionDesde/Hasta`
       pero guarda `r["fecha"]` CRUDO. Si un depósito viene con la fecha de
       liquidación (o Aunesa lo re-informa otro día y el upsert por
       `comprobante` le pisa la fecha), la plata está pero bajo otro día.
  H2 — LA FILA NO ESTÁ: la escritura falla para ellas. Ojo con esto:
       `write_native` es best-effort — atrapa CUALQUIER excepción, la loguea y
       devuelve 0 — y `jobs/cashflow.py` NO mira ese retorno: suma
       `len(con_comp)` igual e imprime éxito. El job puede perder filas (o un
       día entero) y quedar `ok` en job_runs.
  H3 — MIS BUCKETS DE TEXTO MIENTEN. El A1 partió por ILIKE '%e-cheque%' y
       mostró 0 desde abril, pero "otros cheq" tiene 113-152 esos mismos meses.
       Si el texto cambió de forma ("e-cheq" sin la 'ue', otro wording), el
       "0 desde abril" es un artefacto de MI corte, no un hueco real.

BLOQUES
  1. Los comprobantes que faltan, buscados en TODA la tabla (sin filtro de
     fecha, con trim y por número suelto) → mata o confirma H1 de una.
  2. Los TEXTOS distintos con 'cheq' que hay en la tabla, por mes → mata H3.
  3. LA CURVA DEL CORTE, sin depender de texto: por mes, cuántos boletos
     `categoria='deposito'` de `negocio_movimientos` tienen su comprobante en
     `movimientos`. Da el % de cobertura mes a mes y la fecha del quiebre.
  4. El log real del cron (`logs/cashflow.log`): qué dijo el job en sus últimas
     corridas — cuántas filas creyó escribir y si `pg_mirror` gritó un error
     que nadie miró.

100% read-only. 1 llamada a Aunesa por día pedido (default 2).

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_echeq_paso3
    python -m scripts.diag_echeq_paso3 --dias 4 --meses 16
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import UTC, date, datetime, timedelta

from api.services._sql import _q
from jobs.cashflow import autenticar as _auth
from jobs.cashflow import es_movimiento, fetch_dia

_LINEA = "─" * 78
_LOG = "/root/TradingAV/logs/cashflow.log"
_RE_NUM = re.compile(r"(\d{4,})")


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _titulo(n: str, txt: str) -> None:
    print(f"\n{_LINEA}\n{n}. {txt}\n{_LINEA}")


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
def bloque1(dias: list[date]) -> None:
    _titulo("1", "LOS QUE FALTAN — ¿están en la tabla bajo OTRA fecha? (H1 vs H2)")
    print("Se toma el set que Aunesa mandó para el día, se resta lo que hay en la tabla")
    print("PARA ESE DÍA, y a los que faltan se los busca en TODA la tabla sin filtro.\n")

    headers = _auth()
    for d in dias:
        dstr = d.strftime("%d/%m/%Y")
        try:
            data, headers = fetch_dia(dstr, headers)
        except Exception as e:
            print(f"  {dstr}: ❌ {str(e)[:100]}")
            continue

        lineas = [r for r in (data or [])
                  if es_movimiento(r.get("informacion", "")) and r.get("comprobante")]
        por_comp: dict[str, dict] = {}
        for r in lineas:
            por_comp[str(r["comprobante"])] = r

        en_dia = {str(r["comprobante"]) for r in _q(
            "SELECT comprobante FROM movimientos WHERE fecha = %(f)s", {"f": dstr})}
        faltan = sorted(set(por_comp) - en_dia)

        print(f"  {dstr}: Aunesa={len(por_comp)}  en la tabla ese día={len(en_dia)}  "
              f"faltan={len(faltan)}")
        if not faltan:
            continue

        # ¿Existen en CUALQUIER fecha? (H1: la fila está, mal fechada)
        hallados = _q(
            "SELECT comprobante, fecha, informacion, total FROM movimientos "
            " WHERE comprobante = ANY(%(c)s)", {"c": faltan})
        hmap = {str(h["comprobante"]): h for h in hallados}
        print(f"     de esos, EXISTEN en otra fecha: {len(hallados)} / {len(faltan)}")

        # Match tolerante: por trim y por el número suelto del comprobante.
        nums = [m.group(1) for c in faltan if (m := _RE_NUM.search(c))]
        laxo = _q(
            "SELECT comprobante, fecha, informacion FROM movimientos "
            " WHERE trim(comprobante) = ANY(%(t)s) "
            "    OR comprobante ~ ANY(%(n)s)",
            {"t": [c.strip() for c in faltan], "n": [rf"\y{n}\y" for n in nums]}
        ) if nums else []
        extra = [x for x in laxo if str(x["comprobante"]) not in hmap]
        if extra:
            print(f"     ⚠ match LAXO (trim/número): {len(extra)} más → el comprobante")
            print("       se guarda con OTRO formato que el que manda Aunesa:")
            for x in extra[:5]:
                print(f"         tabla={x['comprobante']!r}  fecha={x['fecha']}  "
                      f"{str(x['informacion'])[:40]}")

        print("     detalle de los que faltan:")
        for c in faltan[:10]:
            r = por_comp[c]
            h = hmap.get(c)
            donde = f"→ EN LA TABLA con fecha {h['fecha']}" if h else "→ NO ESTÁ en ninguna fecha"
            print(f"       {c!r:>22}  {_f(r.get('total')) * -1:>18,.2f}  "
                  f"{str(r.get('informacion'))[:34]:<34} {donde}")

    print("\nSi la mayoría dice 'EN LA TABLA con fecha X' → H1: la plata está, mal fechada,")
    print("y la vista la muestra el día equivocado. Si dice 'NO ESTÁ' → H2: se pierde al")
    print("escribir, y hay que mirar el bloque 4 (el log del job).")


# ─────────────────────────────────────────────────────────────────────────────
def bloque2(meses: int) -> None:
    _titulo("2", "LOS TEXTOS REALES con 'cheq' en la tabla, por mes (mata H3)")
    print("El A1 del paso 2 partió por buckets que ELEGÍ yo. Acá está el texto crudo.\n")

    desde = _hoy_art().replace(day=1) - timedelta(days=31 * meses)
    rows = _q(
        "SELECT to_char(to_date(fecha,'DD/MM/YYYY'),'YYYY-MM') AS mes, "
        # ' ID' corta la cola única de "Extracción - ECHEQ ID: XXXX" (si no, cada
        # fila sería un texto distinto y no se puede agrupar).
        "       split_part(informacion, ' ID', 1) AS texto, count(*) AS n "
        "  FROM movimientos "
        r" WHERE fecha ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$' "
        "   AND informacion ILIKE '%%cheq%%' "
        "   AND to_date(fecha,'DD/MM/YYYY') >= %(d)s "
        " GROUP BY 1,2 ORDER BY 1 DESC, 3 DESC", {"d": desde})
    if not rows:
        print("(sin filas con 'cheq' en la ventana)")
        return
    por_mes: dict[str, list] = {}
    for r in rows:
        por_mes.setdefault(r["mes"], []).append(r)
    for mes in sorted(por_mes, reverse=True):
        print(f"\n  {mes}")
        for r in por_mes[mes][:6]:
            print(f"     [{r['n']:>4}]  {str(r['texto'])[:66]}")
        if len(por_mes[mes]) > 6:
            print(f"     … y {len(por_mes[mes]) - 6} texto(s) más")


# ─────────────────────────────────────────────────────────────────────────────
def bloque3(meses: int) -> None:
    _titulo("3", "LA CURVA DEL CORTE — cobertura de DEPÓSITOS, sin depender del texto")
    print("Por mes: boletos `categoria='deposito'` en negocio_movimientos vs cuántos de")
    print("esos comprobantes existen en `movimientos`. Es un anti-join por PK, no un LIKE.\n")

    desde = (_hoy_art().replace(day=1) - timedelta(days=31 * meses)).isoformat()
    rows = _q(
        "SELECT to_char(n.fecha,'YYYY-MM') AS mes, "
        "       count(*) AS boletos, "
        "       count(m.comprobante) AS en_movimientos, "
        "       sum(n.importe) FILTER (WHERE m.comprobante IS NULL) AS plata_perdida "
        "  FROM negocio_movimientos n "
        "  LEFT JOIN movimientos m ON m.comprobante = n.comprobante "
        " WHERE n.fecha >= %(d)s AND n.categoria = 'deposito' AND n.anulado_en IS NULL "
        " GROUP BY 1 ORDER BY 1 DESC", {"d": desde})

    print(f"{'mes':>9}  {'depósitos':>10}  {'en movimientos':>15}  {'cobertura':>10}  "
          f"{'Σ faltante (mezcla mon.)':>26}")
    for r in rows:
        cob = (r["en_movimientos"] / r["boletos"] * 100) if r["boletos"] else 0
        marca = "  ❌" if cob < 90 else ""
        print(f"{r['mes']:>9}  {r['boletos']:>10,}  {r['en_movimientos']:>15,}  "
              f"{cob:>9.0f}%  {_f(r['plata_perdida']):>26,.0f}{marca}")

    print("\nEl mes donde la cobertura se cae es la FECHA DEL QUIEBRE. Con eso se busca qué")
    print("cambió ese mes (del lado de Aunesa, porque del nuestro no se tocó nada).")

    # Mismo corte para el resto de las categorías: ¿es exclusivo de los depósitos?
    otras = _q(
        "SELECT n.categoria, count(*) AS boletos, count(m.comprobante) AS en_mov "
        "  FROM negocio_movimientos n "
        "  LEFT JOIN movimientos m ON m.comprobante = n.comprobante "
        " WHERE n.fecha >= %(d)s AND n.categoria = ANY(%(c)s) AND n.anulado_en IS NULL "
        " GROUP BY 1 ORDER BY 1",
        {"d": desde, "c": ["deposito", "transferencia", "extraccion"]})
    print("\nComparación por categoría (misma ventana) — ¿solo los depósitos?")
    for r in otras:
        cob = (r["en_mov"] / r["boletos"] * 100) if r["boletos"] else 0
        print(f"   {r['categoria']:<15} {r['boletos']:>7,} boletos → {cob:>5.0f}% en movimientos")


# ─────────────────────────────────────────────────────────────────────────────
def bloque4(n: int = 60) -> None:
    _titulo("4", "EL LOG DEL CRON — qué dijo el job realmente")
    print("`write_native` atrapa cualquier error, lo loguea y devuelve 0; el job NO mira")
    print("ese retorno y suma igual → puede reportar éxito habiendo escrito nada.\n")

    if not os.path.exists(_LOG):
        print(f"(no existe {_LOG} — ¿otro path de logs?)")
        return
    with open(_LOG, encoding="utf-8", errors="replace") as fh:
        lineas = fh.readlines()
    print(f"Últimas {n} líneas de {_LOG}:\n")
    for ln in lineas[-n:]:
        print("   " + ln.rstrip()[:120])

    errores = [ln for ln in lineas if "pg_mirror" in ln or "⚠" in ln or "Error" in ln]
    print(f"\nLíneas con pg_mirror / ⚠ / Error en TODO el log: {len(errores)}")
    for ln in errores[-10:]:
        print("   " + ln.rstrip()[:120])


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=int, default=2, help="días hábiles a inspeccionar (def 2)")
    ap.add_argument("--meses", type=int, default=15, help="ventana de los histogramas (def 15)")
    args = ap.parse_args()

    from core.calendario import ultimos_habiles
    # Se saltea HOY: el cron de cashflow corre a las 02:00 UTC del día siguiente,
    # así que el día en curso SIEMPRE está vacío y no prueba nada.
    dias = [d for d in ultimos_habiles(_hoy_art(), args.dias) if d != _hoy_art()][-args.dias:]

    print(f"\nDIAG e-cheq PASO 3   (hoy ART: {_hoy_art()})")
    print(f"Días: {', '.join(str(d) for d in dias)}  (se saltea hoy: el cron aún no corrió)")

    bloque1(dias)
    bloque2(args.meses)
    bloque3(args.meses)
    bloque4()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
