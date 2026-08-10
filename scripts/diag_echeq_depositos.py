"""Diag READ-ONLY: por qué los "Depósito de e-cheque" no aparecen en
DEPÓSITOS & EXTRACCIONES (vista NEGOCIO → CASHFLOW).

CONTEXTO DEL CIRCUITO (lo que dice el código, verificado — no la DB):

  La vista lee `operaciones.movimientos` (via /api/operaciones/flujos/resumen →
  api/services/cashflow_sql.py::flujos_resumen). Esa tabla la escribe UN SOLO
  writer: `jobs/cashflow.py`, cron `0 2 * * 2-6` (02:00 UTC = 23:00 ART).

  `jobs/cashflow.py` pega a Aunesa `operaciones/consolidadosGenerales` del día y
  se queda SOLO con las filas cuyo `informacion`, normalizado (sin tildes,
  minúsculas), contiene alguna de: deposito · transferencia · extraccion
  (jobs/cashflow.py::PALABRAS_CLAVE). Después invierte el signo y upsertea por
  `comprobante`.

  MISMA FUENTE, OTRA TABLA: `jobs/negocio_movimientos.py` (cron cada 30' en
  rueda) pega al MISMO endpoint y persiste TODO en
  `operaciones.negocio_movimientos`, categorizando con las MISMAS tres palabras
  (api/services/aunesa_negocio.py::categorizar). O sea: hay una copia FRESCA del
  mismo dato al lado. Si el depósito e-cheq está ahí y no en `movimientos`, el
  problema es el WRITER (job muerto), no el filtro ni la fuente.

  TERCERA FUENTE, DISTINTA: Tesorería (Back Office) NO usa ese endpoint — usa
  `cuentas/consultaMovDocsSolicitados`, que son movimientos BANCARIOS y trae el
  e-cheq explícito en el RIEL (`tipoDocSoli` = '[E CHEQ] E CHEQ') con dirección
  en `solicitud` (Depósito / Extracción). Ahí el e-cheq SÍ se ve. La pregunta es
  si ese mismo depósito tiene contrapartida en el feed del comitente.

QUÉ RESPONDE (5 bloques, en este orden):

  1. ¿Está viva `operaciones.movimientos`? Último día cargado + volumen por mes.
     Si el último dato es de hace meses, el job está muerto y nada más importa.
  2. ¿Qué dice `manager.job_runs` del job `cashflow`? Corrió / falló / con qué error.
  3. ¿Qué hay en la FUENTE (Aunesa consolidadosGenerales) estos días? Cuántas
     filas trae, cuántas captura el filtro actual, y TODOS los `informacion`
     distintos que mencionan "cheq" — marcando cuáles pasarían el filtro y
     cuáles NO.
  4. ¿Qué hay en la tabla FRESCA (`negocio_movimientos`) que viene del mismo
     lado? Depósitos/transferencias/extracciones por mes + los que dicen "cheq".
  5. ¿Qué ve Tesorería esos mismos días? Movimientos con RIEL e-cheq, partidos
     por Depósito/Extracción y por estado, con importes.

  Al final imprime un VEREDICTO con la causa que sostienen los números.

100% read-only: no escribe una sola fila. Pega a Aunesa 2 veces por día pedido
(consolidadosGenerales + consultaMovDocsSolicitados) → con el default de 3 días
son 6 llamadas. No correr con --dias grande en horario de rueda.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_echeq_depositos
    python -m scripts.diag_echeq_depositos --dias 5
    python -m scripts.diag_echeq_depositos --fecha 2026-08-07
    python -m scripts.diag_echeq_depositos --meses 18      # ventana del histograma
"""
from __future__ import annotations

import argparse
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta

from api.services._sql import _q

# Filtro EXACTO del writer (jobs/cashflow.py). Se importa el criterio, no se copia
# a mano, para que el diag no pueda diferir del job.
from jobs.cashflow import PALABRAS_CLAVE, es_movimiento, fetch_dia
from jobs.cashflow import autenticar as _auth_cashflow

_LINEA = "─" * 78


def _norm(s) -> str:
    return (unicodedata.normalize("NFD", str(s or ""))
            .encode("ascii", "ignore").decode("utf-8").lower())


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _ultimos_habiles(hasta: date, n: int) -> list[date]:
    """Los `n` últimos días hábiles hasta `hasta` (incluido). `ultimos_habiles`
    devuelve hoy + n_atras previos → se le pide n-1 para que `--dias 3` sean 3."""
    from core.calendario import ultimos_habiles
    return ultimos_habiles(hasta, max(0, n - 1))


def _titulo(n: int, txt: str) -> None:
    print(f"\n{_LINEA}\n{n}. {txt}\n{_LINEA}")


# ─────────────────────────────────────────────────────────────────────────────
# 1) Estado de operaciones.movimientos (lo que lee la vista)
# ─────────────────────────────────────────────────────────────────────────────
def bloque_tabla(meses: int) -> dict:
    _titulo(1, "TABLA QUE LEE LA VISTA — operaciones.movimientos")

    tot = _q("SELECT count(*) AS n FROM movimientos")[0]["n"]
    print(f"Filas totales: {tot:,}")

    # `fecha` es text dd/mm/yyyy → el regex blinda el to_date contra basura
    # (mismo patrón que cashflow_sql.listar_flujos).
    ok = r"fecha ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$'"
    rows = _q(
        f"SELECT to_char(to_date(fecha,'DD/MM/YYYY'),'YYYY-MM') AS mes, count(*) AS n, "
        f"       count(*) FILTER (WHERE informacion ILIKE '%%cheq%%') AS con_cheq "
        f"  FROM movimientos WHERE {ok} "
        f" GROUP BY 1 ORDER BY 1 DESC LIMIT %(m)s", {"m": meses})
    malas = _q(f"SELECT count(*) AS n FROM movimientos WHERE NOT ({ok})")[0]["n"]
    if malas:
        print(f"⚠ {malas:,} filas con `fecha` malformada (quedan fuera de la vista SIEMPRE)")

    if not rows:
        print("❌ SIN filas con fecha parseable. La tabla no sirve nada.")
        return {"ultimo_mes": None, "tot": tot}

    print(f"\n{'mes':>9}  {'filas':>8}  {'dicen cheq':>10}")
    for r in rows:
        print(f"{r['mes']:>9}  {r['n']:>8,}  {r['con_cheq']:>10,}")

    ult = _q(
        f"SELECT max(to_date(fecha,'DD/MM/YYYY')) AS d FROM movimientos WHERE {ok}"
    )[0]["d"]
    atraso = (_hoy_art() - ult).days if ult else None
    print(f"\nÚltimo día cargado: {ult}   → atraso: {atraso} días corridos")
    if atraso is not None and atraso > 5:
        print("❌ LA TABLA ESTÁ MUERTA. El writer (jobs/cashflow.py) dejó de escribir.")
    return {"ultimo_mes": rows[0]["mes"], "ultimo_dia": ult, "atraso": atraso, "tot": tot}


# ─────────────────────────────────────────────────────────────────────────────
# 2) Qué dice job_runs del cron `cashflow`
# ─────────────────────────────────────────────────────────────────────────────
def bloque_job_runs() -> dict:
    _titulo(2, "EL CRON — manager.job_runs WHERE tipo = 'cashflow'")

    rows = _q(
        "SELECT started_at, finished_at, status, "
        "       data->'stats' AS stats, data->>'error' AS error "
        "  FROM manager.job_runs WHERE tipo = 'cashflow' "
        " ORDER BY started_at DESC LIMIT 15")
    if not rows:
        print("❌ NINGÚN run registrado. O nunca corrió con JobRunLogger, o el cron")
        print("   no está instalado en la crontab REAL del Droplet.")
        print("   (deploy/crontab.txt lo declara: `0 2 * * 2-6 ... jobs.cashflow --today`,")
        print("    pero ese archivo es la fuente DECLARADA — verificar `crontab -l`.)")
        return {"runs": 0}

    print(f"{'started_at (UTC)':>20}  {'status':>8}  detalle")
    for r in rows:
        det = r["error"] or (str(r["stats"]) if r["stats"] else "")
        print(f"{str(r['started_at'])[:19]:>20}  {r['status']!s:>8}  {det[:80]}")

    ultimo = rows[0]["started_at"]
    dias = (datetime.now(UTC) - ultimo).days if ultimo else None
    print(f"\nÚltimo run: {ultimo} ({dias} días atrás)")
    errores = sum(1 for r in rows if r["status"] != "ok")
    if errores:
        print(f"⚠ {errores}/{len(rows)} de los últimos runs NO terminaron ok.")
    return {"runs": len(rows), "ultimo": ultimo, "dias": dias, "errores": errores}


# ─────────────────────────────────────────────────────────────────────────────
# 3) La FUENTE: Aunesa consolidadosGenerales (lo que el job debería capturar)
# ─────────────────────────────────────────────────────────────────────────────
def bloque_fuente(dias: list[date]) -> dict:
    _titulo(3, "LA FUENTE — Aunesa /operaciones/consolidadosGenerales")
    print(f"Filtro actual del writer: informacion contiene {list(PALABRAS_CLAVE)}\n")

    headers = _auth_cashflow()
    capturadas: Counter = Counter()
    perdidas: Counter = Counter()
    total_filas = 0

    for d in dias:
        dstr = d.strftime("%d/%m/%Y")
        try:
            data, headers = fetch_dia(dstr, headers)
        except Exception as e:
            print(f"  {dstr}: ❌ {str(e)[:120]}")
            continue
        data = data or []
        total_filas += len(data)
        match = [r for r in data if es_movimiento(r.get("informacion", ""))]
        cheq = [r for r in data if "cheq" in _norm(r.get("informacion"))]
        print(f"  {dstr}: {len(data):>5} filas | {len(match):>4} pasan el filtro | "
              f"{len(cheq):>3} mencionan 'cheq'")
        for r in cheq:
            info = str(r.get("informacion") or "")
            (capturadas if es_movimiento(info) else perdidas)[info] += 1

    print(f"\nTotal filas leídas de Aunesa: {total_filas:,}")

    if capturadas:
        print("\n✔ `informacion` con 'cheq' que SÍ pasan el filtro actual:")
        for info, n in capturadas.most_common(30):
            print(f"   [{n:>3}] {info[:110]}")
    if perdidas:
        print("\n❌ `informacion` con 'cheq' que el filtro TIRA (esto sería el bug):")
        for info, n in perdidas.most_common(30):
            print(f"   [{n:>3}] {info[:110]}")
    if not capturadas and not perdidas:
        print("\n(Ninguna fila de estos días menciona 'cheq' en `informacion`.")
        print(" El depósito de e-cheque NO viaja por este endpoint, o no hubo en los días pedidos.)")

    return {"total": total_filas, "capturadas": sum(capturadas.values()),
            "perdidas": sum(perdidas.values())}


# ─────────────────────────────────────────────────────────────────────────────
# 4) La copia FRESCA del mismo feed: operaciones.negocio_movimientos
# ─────────────────────────────────────────────────────────────────────────────
def bloque_negocio(meses: int) -> dict:
    _titulo(4, "MISMO FEED, TABLA VIVA — operaciones.negocio_movimientos")
    print("Si acá SÍ están y en `movimientos` no, la fuente y el filtro están bien:")
    print("lo que falla es el writer de `movimientos`.\n")

    desde = (_hoy_art().replace(day=1) - timedelta(days=31 * meses)).isoformat()
    rows = _q(
        "SELECT to_char(fecha,'YYYY-MM') AS mes, categoria, count(*) AS n "
        "  FROM negocio_movimientos "
        " WHERE fecha >= %(d)s AND categoria IN ('deposito','transferencia','extraccion') "
        "   AND anulado_en IS NULL "
        " GROUP BY 1,2 ORDER BY 1 DESC", {"d": desde})
    if not rows:
        print("(sin depósitos/transferencias/extracciones en la ventana)")
    else:
        por_mes: dict[str, dict[str, int]] = defaultdict(dict)
        for r in rows:
            por_mes[r["mes"]][r["categoria"]] = r["n"]
        cats = ("deposito", "transferencia", "extraccion")
        print(f"{'mes':>9}  " + "  ".join(f"{c:>13}" for c in cats))
        for mes in sorted(por_mes, reverse=True)[:meses]:
            print(f"{mes:>9}  " + "  ".join(f"{por_mes[mes].get(c, 0):>13,}" for c in cats))

    cheq = _q(
        "SELECT fecha, categoria, cuenta, moneda, importe, informacion "
        "  FROM negocio_movimientos "
        " WHERE fecha >= %(d)s AND informacion ILIKE '%%cheq%%' AND anulado_en IS NULL "
        " ORDER BY fecha DESC LIMIT 40", {"d": desde})
    print(f"\nBoletos con 'cheq' en `informacion` (últimos {meses} meses): {len(cheq)}"
          + (" (tope 40)" if len(cheq) == 40 else ""))
    for r in cheq[:25]:
        print(f"   {r['fecha']}  {r['categoria']!s:>14}  {r['moneda']!s:>4} "
              f"{float(r['importe'] or 0):>16,.2f}  {str(r['informacion'])[:60]}")

    return {"filas_cheq": len(cheq)}


# ─────────────────────────────────────────────────────────────────────────────
# 5) Tesorería: el e-cheq que SÍ se ve (otro endpoint de Aunesa)
# ─────────────────────────────────────────────────────────────────────────────
def bloque_tesoreria(dias: list[date]) -> dict:
    _titulo(5, "TESORERÍA — Aunesa /cuentas/consultaMovDocsSolicitados (RIEL e-cheq)")
    print("Este es el feed BANCARIO donde el e-cheq aparece explícito. Es OTRO endpoint:")
    print("si el depósito e-cheq vive solo acá, el cashflow del comitente nunca lo vio.\n")

    from api.services.tesoreria import (
        ESTADO_EFECTIVO,
        TODOS_ESTADOS,
        _fechas,
        _num,
        aplanar,
        es_echeq,
        traer_crudas,
    )

    def _quien(m: dict) -> str:
        """Nombre del titular. `aplanar` aplana el objeto `persona` a persona_*, pero
        las claves exactas no están documentadas → se toma el primer persona_* con
        pinta de texto largo y, si no hay, la cuenta operativa."""
        cands = [str(v) for k, v in m.items()
                 if k.startswith("persona_") and isinstance(v, str) and len(v) > 3]
        return (cands[0] if cands else str(m.get("cuentaOperativa") or ""))[:38]

    tot_dep = tot_ext = 0
    muestra_cruda: dict | None = None
    for d in dias:
        try:
            crudas = traer_crudas(dia=d, estado=TODOS_ESTADOS)
        except Exception as e:
            print(f"  {d}: ❌ {str(e)[:120]}")
            continue
        _, _, yyyymmdd = _fechas(d.isoformat())
        movs = [aplanar(r, yyyymmdd) for r in crudas]
        ech = [m for m in movs if es_echeq(m.get("tipoDocSoli"))]
        dep = [m for m in ech if m["_tipo"] == "ingreso"]
        ext = [m for m in ech if m["_tipo"] == "egreso"]
        tot_dep += len(dep)
        tot_ext += len(ext)
        if dep and muestra_cruda is None:
            muestra_cruda = dep[0]
        print(f"  {d}: {len(movs):>4} mov bancarios | {len(ech):>3} e-cheq "
              f"→ {len(dep)} DEPÓSITO / {len(ext)} EXTRACCIÓN")
        for m in dep:
            marca = "" if m.get("estado") == ESTADO_EFECTIVO else f"  ⚠ {m.get('estado')}"
            print(f"       DEP  {m.get('unidad') or ''!s:>4} "
                  f"{_num(m.get('monto')):>15,.2f}  {_quien(m)}{marca}")

    print(f"\nTotal en la ventana: {tot_dep} depósitos e-cheq / {tot_ext} extracciones e-cheq")

    # Discovery: el shape REAL de un depósito e-cheq. Es lo que hace falta para
    # decidir por qué campo cruzarlo con el comitente (CUIT, comprobante, importe).
    if muestra_cruda:
        print("\nShape crudo del 1er depósito e-cheq (campos no vacíos):")
        for k, v in sorted(muestra_cruda.items()):
            if v not in (None, "", {}, []):
                print(f"   {k:<28} {str(v)[:70]}")

    return {"dep": tot_dep, "ext": tot_ext}


# ─────────────────────────────────────────────────────────────────────────────
def veredicto(t: dict, j: dict, f: dict, n: dict, tes: dict) -> None:
    _titulo(6, "VEREDICTO")
    causas = []

    if (t.get("atraso") or 0) > 5:
        causas.append(
            f"WRITER MUERTO — `operaciones.movimientos` tiene su último día el "
            f"{t.get('ultimo_dia')} ({t.get('atraso')} días de atraso). La vista "
            f"DEPÓSITOS & EXTRACCIONES está congelada ahí, no le falta un tipo de "
            f"movimiento: le falta TODO desde esa fecha."
        )
        if j.get("runs") == 0:
            causas.append("El cron `cashflow` no dejó NI UN run en job_runs → no está corriendo.")
        elif (j.get("dias") or 0) > 5:
            causas.append(f"El último run de `cashflow` fue hace {j['dias']} días.")
        elif j.get("errores"):
            causas.append("El cron corre pero termina en error (ver el detalle del bloque 2).")

    if f.get("perdidas"):
        causas.append(
            f"FILTRO CORTO — {f['perdidas']} filas de Aunesa mencionan 'cheq' y NO "
            f"contienen deposito/transferencia/extraccion, así que el writer las tira."
        )
    elif f.get("capturadas"):
        causas.append(
            f"El filtro NO es el problema: las {f['capturadas']} filas con 'cheq' del "
            f"feed del comitente SÍ pasan {list(PALABRAS_CLAVE)}."
        )

    if tes.get("dep") and not f.get("capturadas") and not f.get("perdidas"):
        causas.append(
            f"FUENTES DISTINTAS — hay {tes['dep']} depósitos e-cheq en el feed BANCARIO "
            f"(Tesorería) y CERO rastro en el feed del comitente (consolidadosGenerales). "
            f"El cashflow por cliente no puede verlos desde donde mira hoy."
        )

    if not causas:
        causas.append("Los números no muestran ninguna de las causas esperadas — leer los bloques.")

    for i, c in enumerate(causas, 1):
        print(f"  {i}. {c}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=int, default=3,
                    help="días hábiles hacia atrás a inspeccionar en Aunesa (default 3)")
    ap.add_argument("--fecha", help="YYYY-MM-DD: inspeccionar SOLO ese día")
    ap.add_argument("--meses", type=int, default=14,
                    help="ventana del histograma por mes (default 14)")
    args = ap.parse_args()

    if args.fecha:
        dias = [datetime.strptime(args.fecha, "%Y-%m-%d").date()]
    else:
        dias = _ultimos_habiles(_hoy_art(), args.dias)

    print(f"\nDIAG e-cheq → DEPÓSITOS & EXTRACCIONES   (hoy ART: {_hoy_art()})")
    print(f"Días inspeccionados en Aunesa: {', '.join(str(d) for d in dias)}")

    t = bloque_tabla(args.meses)
    j = bloque_job_runs()
    f = bloque_fuente(dias)
    n = bloque_negocio(args.meses)
    tes = bloque_tesoreria(dias)
    veredicto(t, j, f, n, tes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
