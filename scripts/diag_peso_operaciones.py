"""scripts/diag_peso_operaciones.py — ¿de qué está hecho el peso de NEGOCIO → OPERACIONES?

POR QUÉ ESTE DIAG
=================
Pregunta del user (2026-08-19): *"¿no serviría aplicar PAGINADO en NEGOCIO →
OPERACIONES? son miles de registros históricos y la vista se pone cada vez más
pesada"*.

Antes de paginar hay que saber QUÉ es lo pesado, porque la vista **no manda
registros**: los siete tabs mandan AGREGADOS (`GROUP BY` server-side). Paginar
una lista de boletos no arreglaría nada si lo que viaja son sumas por cuenta.
Las tres cosas que SÍ pueden crecer sin techo, y que este script mide por
separado, son:

  1. **Cantidad de GRUPOS** — `por_cuenta` / `por_instrumento` / `por_dim` NO
     tienen `LIMIT`: devuelven una fila por cada cuenta y cada título del rango.
     Con rango amplio (YTD/histórico) eso es lo que engorda el JSON, y el front
     las pinta con un `.map()` plano (sin virtualizar) → tantas filas de DOM
     como grupos.
  2. **Series históricas sin cota** — `/ops/serie` no filtra por fecha NUNCA
     (trae desde el primer día); la de aranceles trae ~18 meses salvo el botón
     ALL.
  3. **Catálogos completos** — `/ops/fechas` (una fila por día operado, para
     siempre), `/ops/cuentas-list` (el padrón entero) y el payload de CASHFLOW
     (2 años de día × cuenta × unidad, que el browser filtra en cliente).

Lo que el script imprime, por endpoint y por rango realista (ÚLTIMA / MES / YTD):
**ms de la llamada, filas de cada array, y KB del JSON que viajaría**. Con eso
se decide con números si hace falta paginado, top-N, o nada.

CÓMO LEERLO
===========
  · KB del JSON = lo que baja el browser. Referencia: >250 KB ya se siente en
    una vista que además pinta cada fila.
  · filas = filas de DOM que el front va a montar (no hay virtualización).
  · Si un endpoint pesa parecido en ÚLTIMA y en YTD, su problema NO es el rango.
  · La columna Δ YTD/ÚLTIMA dice cuánto multiplica el rango: ahí se ve si el
    crecimiento es por historia (paginado/top-N ayuda) o es constante (no ayuda).

Read-only: no escribe una sola fila. Uso (en el Droplet):
    python -m scripts.diag_peso_operaciones            # telemetría + rangos ÚLTIMA/MES/YTD
    python -m scripts.diag_peso_operaciones --all      # + el rango HISTÓRICO COMPLETO (pesado)
    python -m scripts.diag_peso_operaciones --sin-telemetria
"""
from __future__ import annotations

import json
import sys
import time

from api.services import cashflow_sql as _cf
from api.services import financiamiento as _fin
from api.services import operaciones_sql as _ops
from api.services._sql import _q

# Umbrales de lectura (no son reglas, son referencias para el ojo).
KB_OJO = 250.0        # a partir de acá el payload se nota
FILAS_OJO = 1_500     # a partir de acá el .map() plano pesa en el DOM


def _kb(obj) -> float:
    """KB del JSON tal como viajaría por la red (sin espacios, como FastAPI)."""
    return len(json.dumps(obj, default=str, separators=(",", ":")).encode()) / 1024


def _perfil(payload: dict) -> tuple[dict[str, int], float]:
    """(filas por array de primer nivel, KB totales) de una respuesta."""
    arrays = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
    return arrays, _kb(payload)


def _medir(label: str, fn, **kw) -> dict | None:
    t0 = time.perf_counter()
    try:
        payload = fn(**kw)
    except Exception as e:
        print(f"  {label:<34} ERROR: {str(e).splitlines()[0][:70]}")
        return None
    ms = (time.perf_counter() - t0) * 1000
    arrays, kb = _perfil(payload if isinstance(payload, dict) else {"filas": payload})
    total_filas = sum(arrays.values())
    marca = ""
    if kb >= KB_OJO:
        marca += " ⚠KB"
    if total_filas >= FILAS_OJO:
        marca += " ⚠FILAS"
    detalle = "  ".join(f"{k}={v}" for k, v in sorted(arrays.items(), key=lambda x: -x[1])[:5])
    print(f"  {label:<34} {ms:7.0f}ms {kb:8.1f}KB  {total_filas:6d} filas{marca}")
    if detalle:
        print(f"  {'':<34} {detalle}")
    return {"label": label, "ms": ms, "kb": kb, "filas": total_filas, "arrays": arrays}


# ── 1. Telemetría real: ¿qué endpoints de la vista se usan y cuánto cuestan? ──
def telemetria(dias: int = 7) -> None:
    print(f"\n{'='*78}\n1. TELEMETRÍA REAL — /api/operaciones/* últimos {dias} días")
    print("   (manager.latencia_endpoints; ordenado por TIEMPO TOTAL consumido)")
    print(f"{'='*78}")
    try:
        rows = _q(
            "SELECT endpoint, SUM(n) AS n, SUM(total_ms) AS tot, MAX(max_ms) AS mx, "
            "SUM(lentas) AS lentas FROM manager.latencia_endpoints "
            "WHERE hora >= now() - make_interval(days => %(d)s) "
            "AND endpoint LIKE '/api/operaciones/%%' "
            "GROUP BY endpoint ORDER BY SUM(total_ms) DESC LIMIT 20",
            {"d": dias},
        )
    except Exception as e:
        print(f"   sin telemetría ({str(e).splitlines()[0][:60]})")
        return
    if not rows:
        print("   sin datos en la ventana")
        return
    print(f"   {'endpoint':<48} {'n':>7} {'avg':>7} {'total':>9} {'max':>7} {'>1s':>5}")
    for r in rows:
        n = int(r["n"] or 0) or 1
        tot_s = float(r["tot"] or 0) / 1000
        print(f"   {r['endpoint'][:48]:<48} {n:>7} {float(r['tot'] or 0)/n:>6.0f}ms "
              f"{tot_s:>8.0f}s {int(r['mx'] or 0):>6}ms {int(r['lentas'] or 0):>5}")


# ── 2. Rangos con los que la vista trabaja de verdad ─────────────────────────
def _rangos(incluir_all: bool) -> list[tuple[str, str, str]]:
    """[(etiqueta, desde, hasta)] anclados en la ÚLTIMA fecha con operaciones —
    los mismos que arman los botones ÚLTIMA / MES y el seed YTD del front."""
    fechas = [f["fecha"] for f in _ops.ops_fechas()["fechas"]]
    if not fechas:
        return []
    ultima, primera = fechas[0], fechas[-1]
    out = [
        ("ULTIMA (1 día)", ultima, ultima),
        ("MES (mes en curso)", ultima[:7] + "-01", ultima),
        ("YTD (default de 4 tabs)", f"{ultima[:4]}-01-01", ultima),
    ]
    if incluir_all:
        out.append(("HISTÓRICO COMPLETO", primera, ultima))
    return out


def catalogos() -> None:
    print(f"\n{'='*78}\n2. CATÁLOGOS — crecen con la historia/padrón, se piden SIEMPRE al abrir")
    print(f"{'='*78}")
    _medir("/ops/fechas", _ops.ops_fechas)
    _medir("/ops/cuentas-list", _ops.ops_cuentas_list)
    _medir("/ops/diferencias-fechas USDL", _ops.ops_diferencias_fechas, moneda="USDL")
    _medir("/ops/segmentos", _ops.ops_segmentos)
    _medir("/ops/niveles3", _ops.ops_niveles3)
    _medir("/ops/carteras", _ops.ops_carteras)


def series_sin_cota() -> None:
    print(f"\n{'='*78}\n3. SERIES SIN COTA DE FECHA — /ops/serie trae desde el PRIMER día, siempre")
    print(f"{'='*78}")
    _medir("/ops/serie ARS (sin filtros→agregado)", _ops.ops_serie, moneda="ARS")
    _medir("/ops/serie ARS + 1 filtro (en vivo)", _ops.ops_serie, moneda="ARS", mercado="BYMA")
    _medir("/ops/serie USD_DOL", _ops.ops_serie, moneda="USD_DOL")


def tabs(rangos: list[tuple[str, str, str]]) -> dict[str, list[dict]]:
    hist: dict[str, list[dict]] = {}
    for etiqueta, desde, hasta in rangos:
        print(f"\n{'='*78}\n4. TABS con rango {etiqueta}  [{desde} → {hasta}]")
        print(f"{'='*78}")
        for nombre, fn, kw in (
            ("OPERACIONES /ops/resumen", _ops.ops_resumen,
             {"moneda": "ARS", "desde": desde, "hasta": hasta}),
            ("ARANCELES /ops/aranceles", _ops.ops_aranceles,
             {"moneda": "ARS", "desde": desde, "hasta": hasta, "agg": "MENSUAL"}),
            ("AGRO /ops/agro", _ops.ops_agro,
             {"desde": desde, "hasta": hasta, "agg": "DIARIO"}),
            ("DÓLAR FUTURO /ops/dolar-futuro", _ops.ops_dolar_futuro,
             {"desde": desde, "hasta": hasta, "agg": "DIARIO"}),
            ("DIFERENCIAS /ops/diferencias-diarias", _ops.ops_diferencias_diarias,
             {"desde": desde, "hasta": hasta, "moneda": "USDL"}),
        ):
            r = _medir(nombre, fn, **kw)
            if r:
                hist.setdefault(nombre, []).append({**r, "rango": etiqueta})
    return hist


def independientes() -> None:
    print(f"\n{'='*78}\n5. TABS QUE NO USAN EL SELECTOR DE RANGO (traen su propio universo)")
    print(f"{'='*78}")
    from datetime import date
    hoy = date.today()
    desde = date(hoy.year - 2, hoy.month, 1).isoformat()
    # ANTES y AHORA de la tab DEPÓSITOS, uno al lado del otro. `/flujos/resumen`
    # es lo que la vista bajaba hasta el 2026-08-19 (el grano, que el browser
    # filtraba); `/flujos/serie` es lo que baja ahora. Se mide el viejo aunque ya
    # no se use: es la única forma de ver el tamaño del problema que se arregló.
    _medir("DEPÓSITOS antes /flujos/resumen", _cf.flujos_resumen,
           desde=desde, hasta=hoy.isoformat())
    # Se miden los DOS casos porque son muy distintos y el promedio mentiría: la
    # lista del selector (1.021 cuentas, 44 KB) viaja SOLO al abrir la tab y al
    # cambiar el tipo de filtro. Cambiar rango, granularidad o cuenta —que es lo
    # que el usuario hace todo el tiempo— no la manda.
    _medir("DEPÓSITOS abrir la tab (con selector)", _cf.flujos_serie,
           ventana_desde=desde, ventana_hasta=hoy.isoformat(), agg="DIARIO")
    _medir("DEPÓSITOS click típico DIARIO", _cf.flujos_serie,
           ventana_desde=desde, ventana_hasta=hoy.isoformat(), agg="DIARIO",
           con_opciones=False)
    _medir("DEPÓSITOS click típico MENSUAL", _cf.flujos_serie,
           ventana_desde=desde, ventana_hasta=hoy.isoformat(), agg="MENSUAL",
           con_opciones=False)
    _medir("FINANCIAMIENTO /financiamiento", _fin.libro)


def veredicto(hist: dict[str, list[dict]]) -> None:
    print(f"\n{'='*78}\n6. CUÁNTO MULTIPLICA EL RANGO (Δ = último rango medido / ÚLTIMA)")
    print("   Δ alto  → el peso es HISTORIA: top-N / paginado / virtualizar sirve.")
    print("   Δ ~1    → el peso es fijo: paginar no cambia nada, mirar otra cosa.")
    print(f"{'='*78}")
    for nombre, mediciones in hist.items():
        if len(mediciones) < 2:
            continue
        a, b = mediciones[0], mediciones[-1]
        d_kb = (b["kb"] / a["kb"]) if a["kb"] else 0
        d_fil = (b["filas"] / a["filas"]) if a["filas"] else 0
        print(f"  {nombre:<40} KB ×{d_kb:5.1f}   filas ×{d_fil:5.1f}   "
              f"({a['kb']:.0f}→{b['kb']:.0f} KB, {a['filas']}→{b['filas']} filas)")


def main() -> int:
    incluir_all = "--all" in sys.argv
    if "--sin-telemetria" not in sys.argv:
        telemetria()
    catalogos()
    series_sin_cota()
    rangos = _rangos(incluir_all)
    if not rangos:
        print("\nNo hay fechas en operaciones.operaciones — nada que medir.")
        return 1
    hist = tabs(rangos)
    independientes()
    veredicto(hist)
    print("\nListo. Nada de esto escribió en la base.")
    if not incluir_all:
        print("Para medir también el rango HISTÓRICO COMPLETO: --all (es el caso pesado).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
