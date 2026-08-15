"""scripts/diag_1816_cashflow.py — censo del universo de 1816 + prueba del endpoint
NUEVO de cashflow, con validación cruzada contra NUESTRO master (`mercado.curvas`
+ `portafolio.assets`). Doc madre: docs/VISTA_RESEARCH.md.

Responde TRES preguntas, en este orden:

  1. **¿Cuántos tickers hay?** Recorre `/v1/mercado/curvas` y pide
     `/v1/mercado/instrumentos?curvaId=N` por cada curva → total real, desglose
     por curva, y cuántos son VENCIDOS (`soloPerforming=false`) si se pide.
  2. **¿Cuántos de esos son MÍOS?** Cruza contra `mercado.curvas` (ticker_corto,
     normalizado sacando la especie D/C) y `portafolio.assets` (ticker). Dice qué
     tengo yo que 1816 NO tiene, y el tamaño de lo que 1816 tiene y yo no.
  3. **¿El cashflow sirve?** Llama `/v1/mercado/cashflow/{ticker}` sobre una
     muestra y compara cupón a cupón contra los `flujos` de mi master: cantidad
     de cupones, rango de fechas, fechas que faltan de un lado o del otro, y el
     primer cupón común impreso lado a lado (para ver ESCALA — 1816 puede venir
     por VN 100 o por VN 1; NO se asume, se muestra).

Es READ-ONLY: no escribe una sola fila en la base ni en el watch.

⚠ CRÉDITOS (el recurso escaso, §4.2 del doc):
  · curvas + instrumentos = 1 crédito por llamada (≈35 el censo, ≈70 con --vencidos)
  · cashflow = **1 crédito por CUPÓN devuelto** → ~20 por bono soberano típico
El script mide el balance ANTES y DESPUÉS y te dice el costo REAL de cada bloque
(así el número del doc deja de ser una estimación y pasa a ser un hecho medido).

Uso:
    python -m scripts.diag_1816_cashflow                   # censo + cruce + cashflow de 5
    python -m scripts.diag_1816_cashflow --censo           # SOLO el universo (sin cashflow)
    python -m scripts.diag_1816_cashflow --vencidos        # el censo incluye los vencidos
    python -m scripts.diag_1816_cashflow --ticker AL30     # probar cashflow de uno puntual
    python -m scripts.diag_1816_cashflow --muestra 12      # agrandar la muestra
    python -m scripts.diag_1816_cashflow --json /tmp/1816.json    # guarda el censo crudo
    python -m scripts.diag_1816_cashflow --desde /tmp/1816.json   # reusa censo (0 créditos)
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date

from core import mercado_1816

# MISMO regex que jobs/mercado_1816_discovery (si divergen, el cruce de acá y el
# del watch dejarían de coincidir): exige letras + dígitos antes de la especie,
# así un ticker que termina en C/D sin ser especie (p.ej. una ON) no se mutila.
_RE_ESPECIE = re.compile(r"^([A-Z]+\d+)[DC]$")   # AL30D/GD30C → AL30/GD30


# ── helpers ──────────────────────────────────────────────────────────────────


def _norm(t: str | None) -> str:
    """A la forma de 1816: mayúsculas y sin la especie (D/C) final. Mismo criterio
    que jobs/mercado_1816_discovery (los tickers de la casa traen la especie)."""
    t = (t or "").strip().upper()
    m = _RE_ESPECIE.match(t)
    return m.group(1) if m else t


def _fecha(v) -> str:
    """Cualquier cosa con pinta de fecha → 'YYYY-MM-DD' ('' si no se puede)."""
    if isinstance(v, str):
        return v.strip()[:10]
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return ""


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _saldo() -> dict | None:
    """Balance de créditos (cuesta 0). None si falla — nunca corta la corrida."""
    try:
        return mercado_1816.balance()
    except Exception as e:
        print(f"  (no se pudo leer el balance: {e})")
        return None


def _usados(b: dict | None) -> int | None:
    if not isinstance(b, dict):
        return None
    d = b.get("daily") or {}
    return d.get("used") if isinstance(d.get("used"), int) else None


def _delta(antes: dict | None, despues: dict | None) -> str:
    a, d = _usados(antes), _usados(despues)
    return f"{d - a} créditos (medido)" if a is not None and d is not None else "sin medir"


# ── 1) censo del universo ────────────────────────────────────────────────────


def _censo(vencidos: bool) -> dict:
    """{curvas: [...], instrumentos: {ticker: inst}, por_curva: {...}} recorriendo
    TODAS las curvas del catálogo. Una curva que falla no aborta el censo."""
    curvas = mercado_1816.curvas() or []
    print(f"Curvas en el catálogo de 1816: {len(curvas)}")

    universo: dict[str, dict] = {}
    filas: list[dict] = []
    for c in curvas:
        cid = c.get("id") or c.get("curvaId")
        nombre = c.get("name") or c.get("nombre") or c.get("descripcion") or f"curva {cid}"
        if cid is None:
            continue
        fila = {"id": cid, "nombre": nombre, "vigentes": 0, "total": 0, "error": ""}
        try:
            vig = mercado_1816.instrumentos(curva_id=int(cid)) or []
        except Exception as e:
            fila["error"] = str(e)[:80]
            filas.append(fila)
            continue
        fila["vigentes"] = len(vig)
        for inst in vig:
            tk = (inst.get("ticker") or "").strip().upper()
            if tk:
                inst["_curva_id"], inst["_curva"] = cid, nombre
                universo.setdefault(tk, inst)
        if vencidos:
            try:
                todos = mercado_1816.instrumentos(curva_id=int(cid),
                                                  solo_performing=False) or []
                fila["total"] = len(todos)
            except Exception as e:
                fila["error"] = str(e)[:80]
        filas.append(fila)

    return {"curvas": filas, "instrumentos": universo}


def _imprimir_censo(censo: dict, vencidos: bool) -> None:
    filas = censo["curvas"]
    print(f"\n{'ID':>4}  {'CURVA':<38} {'VIGENTES':>9}" + ("  {:>9}".format("TOTAL")
                                                           if vencidos else ""))
    print("─" * (55 + (11 if vencidos else 0)))
    for f in sorted(filas, key=lambda x: -x["vigentes"]):
        extra = f"  {f['total']:>9}" if vencidos else ""
        marca = f"   ⚠ {f['error']}" if f["error"] else ""
        print(f"{f['id']:>4}  {f['nombre'][:38]:<38} {f['vigentes']:>9}{extra}{marca}")

    suma_vig = sum(f["vigentes"] for f in filas)
    dedup = len(censo["instrumentos"])
    print("─" * (55 + (11 if vencidos else 0)))
    print(f"{'':>4}  {'TOTAL (suma por curva)':<38} {suma_vig:>9}"
          + (f"  {sum(f['total'] for f in filas):>9}" if vencidos else ""))
    print(f"\n➡ TICKERS ÚNICOS VIGENTES en 1816: {dedup}  "
          f"(la suma por curva da {suma_vig}: la diferencia son tickers publicados "
          f"en más de una curva, p.ej. las patas de los duales)")
    if vencidos:
        print("   El TOTAL incluye vencidos (soloPerforming=false) — no se deduplica "
              "porque solo se cuenta, no se baja.")


# ── 2) cruce con lo mío (Manager) ────────────────────────────────────────────


def _mis_tickers() -> tuple[dict[str, list[str]], str]:
    """{ticker_normalizado: [de dónde salió, …]} juntando el master de renta fija
    (`mercado.curvas`) y el catálogo de títulos (`portafolio.assets`)."""
    mios: dict[str, list[str]] = {}
    nota = ""
    try:
        from core import curvas_sql
        for d in curvas_sql.cargar_todos():
            tc = (d.get("ticker_corto") or "").strip().upper()
            if tc:
                mios.setdefault(_norm(tc), []).append(f"curvas:{d.get('curva', '')}")
    except Exception as e:
        nota += f" · mercado.curvas no leído ({str(e)[:60]})"
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM portafolio.assets "
                        "WHERE ticker IS NOT NULL AND ticker <> ''")
            for (tk,) in cur.fetchall():
                mios.setdefault(_norm(tk), []).append("assets")
    except Exception as e:
        nota += f" · portafolio.assets no leído ({str(e)[:60]})"
    return mios, nota


def _cruce(censo: dict) -> list[str]:
    """Imprime el cruce y devuelve los tickers que están en los DOS lados
    (candidatos naturales para probar el cashflow)."""
    mios, nota = _mis_tickers()
    if not mios:
        print(f"\n⚠ No se pudo leer nada de la base{nota} — se saltea el cruce.")
        return []

    univ = censo["instrumentos"]
    en_ambos = sorted(t for t in mios if t in univ)
    solo_mios = sorted(t for t in mios if t not in univ)
    solo_1816 = len(univ) - len(en_ambos)

    print(f"\n── CRUCE con lo que tengo en Manager ──{nota}")
    print(f"  Tickers míos (mercado.curvas + portafolio.assets, normalizados): {len(mios)}")
    print(f"  ✔ EN LOS DOS (1816 los tiene): {len(en_ambos)}")
    print(f"  ✘ MÍOS que 1816 NO tiene:      {len(solo_mios)}")
    print(f"  + DE 1816 que yo no tengo:     {solo_1816}")
    if en_ambos:
        print("\n  En los dos:\n    " + ", ".join(en_ambos[:60])
              + (f" … (+{len(en_ambos) - 60})" if len(en_ambos) > 60 else ""))
    if solo_mios:
        print("\n  Míos sin match (ONs, FCI, acciones, cauciones y tickers con otro "
              "código en 1816):\n    " + ", ".join(solo_mios[:60])
              + (f" … (+{len(solo_mios) - 60})" if len(solo_mios) > 60 else ""))
    return en_ambos


# ── 3) prueba del endpoint de cashflow ───────────────────────────────────────


def _mis_flujos(ticker: str) -> dict | None:
    """Doc de MI master para ese ticker normalizado (o None)."""
    try:
        from core import curvas_sql
        for d in curvas_sql.cargar_todos():
            if _norm(d.get("ticker_corto")) == ticker:
                return d
    except Exception:
        return None
    return None


def _probar_cashflow(ticker: str) -> dict:
    """Llama el endpoint y contrasta con mis flujos. Devuelve un resumen dict."""
    print(f"\n── CASHFLOW {ticker} " + "─" * (58 - len(ticker)))
    try:
        data = mercado_1816.cashflow(ticker)
    except Exception as e:
        print(f"  ✘ FALLÓ: {e}")
        return {"ticker": ticker, "ok": False, "error": str(e)[:200]}

    cupones = data.get("cashflow") or []
    print(f"  ✔ HTTP 200 · fechaOperacion={data.get('fechaOperacion')} "
          f"plazo={data.get('plazo')} · cupones={len(cupones)}")
    if not cupones:
        print("  ⚠ Vino SIN cupones (¿instrumento vencido, o sin cashflow publicado?)")
        return {"ticker": ticker, "ok": True, "cupones": 0}

    claves = sorted({k for c in cupones if isinstance(c, dict) for k in c})
    print(f"  Campos que devuelve cada cupón: {', '.join(claves)}")
    print("  PRIMER cupón (crudo): " + json.dumps(cupones[0], ensure_ascii=False)[:200])
    print("  ÚLTIMO cupón (crudo): " + json.dumps(cupones[-1], ensure_ascii=False)[:200])

    suma_amort = sum(_num(c.get("flujoAmortizacion")) or 0 for c in cupones)
    suma_int = sum(_num(c.get("flujoInteres")) or 0 for c in cupones)
    suma_tot = sum(_num(c.get("flujoTotal")) or 0 for c in cupones)
    print(f"  Σ amortización={suma_amort:,.4f} · Σ interés={suma_int:,.4f} · "
          f"Σ total={suma_tot:,.4f}")
    print("    (la Σ amortización es la que revela la ESCALA: ~100 = por VN 100, "
          "~1 = por VN 1, y si es menor puede venir ya amortizado en parte)")

    # contraste contra mi master
    doc = _mis_flujos(ticker)
    if not doc:
        print("  (no tengo este ticker en mercado.curvas → sin contraste)")
        return {"ticker": ticker, "ok": True, "cupones": len(cupones)}

    hoy = date.today().isoformat()
    f1816 = {_fecha(c.get("fechaPagoTeorica") or c.get("fechaPagoEfectiva")): c
             for c in cupones if isinstance(c, dict)}
    f1816.pop("", None)
    mios_todos = {_fecha(f.get("fecha")): f for f in (doc.get("flujos") or [])}
    mios_todos.pop("", None)
    fut_1816 = {f: c for f, c in f1816.items() if f >= hoy}
    fut_mios = {f: c for f, c in mios_todos.items() if f >= hoy}

    print(f"  Mi master ({doc.get('curva')}): {len(mios_todos)} cupones "
          f"({len(fut_mios)} futuros) · 1816: {len(f1816)} ({len(fut_1816)} futuros)")
    faltan_en_1816 = sorted(set(fut_mios) - set(fut_1816))
    faltan_en_mios = sorted(set(fut_1816) - set(fut_mios))
    if faltan_en_1816:
        print(f"  ⚠ fechas futuras que tengo yo y 1816 no: {', '.join(faltan_en_1816[:8])}")
    if faltan_en_mios:
        print(f"  ⚠ fechas futuras que trae 1816 y yo no: {', '.join(faltan_en_mios[:8])}")
    comunes = sorted(set(fut_1816) & set(fut_mios))
    if not comunes:
        print("  ⚠ SIN fechas futuras en común — comparar a mano antes de confiar.")
        return {"ticker": ticker, "ok": True, "cupones": len(cupones), "comunes": 0}

    f = comunes[0]
    mio, suyo = fut_mios[f], fut_1816[f]
    mi_amort = _num(mio.get("amortizacion_pct", mio.get("amortizacion")))
    mi_int = _num(mio.get("cupon_sobre_residual", mio.get("interes")))
    su_amort = _num(suyo.get("flujoAmortizacion"))
    su_int = _num(suyo.get("flujoInteres"))
    print(f"  Fechas futuras en común: {len(comunes)}. Primer cupón común ({f}):")
    print(f"      MÍO : amortización={mi_amort} · interés={mi_int}")
    print(f"      1816: amortización={su_amort} · interés={su_int}")
    if mi_int and su_int:
        print(f"      ratio interés 1816/mío = {su_int / mi_int:.4f}  "
              "(≈1 → misma escala; ≈100 o ≈0.01 → escala distinta)")
    return {"ticker": ticker, "ok": True, "cupones": len(cupones),
            "comunes": len(comunes), "suma_amort": suma_amort}


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="Censo de 1816 + prueba del cashflow")
    ap.add_argument("--censo", action="store_true", help="solo el universo, sin cashflow")
    ap.add_argument("--vencidos", action="store_true",
                    help="el censo también cuenta los vencidos (duplica el costo)")
    ap.add_argument("--ticker", help="probar cashflow SOLO de estos (coma-separados)")
    ap.add_argument("--muestra", type=int, default=5,
                    help="cuántos tickers del cruce probar (default 5)")
    ap.add_argument("--json", dest="salida", help="guarda el censo crudo en este archivo")
    ap.add_argument("--desde", help="reusa un censo guardado con --json (0 créditos)")
    args = ap.parse_args()

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env — no se puede consultar la API.")
        return

    print("=" * 72)
    print("1816 — CENSO DEL UNIVERSO + PRUEBA DEL ENDPOINT CASHFLOW")
    print("=" * 72)

    b0 = _saldo()
    if b0:
        d, m = b0.get("daily") or {}, b0.get("monthly") or {}
        print(f"Créditos ANTES · día {d.get('used')}/{d.get('limit')} · "
              f"mes {m.get('used')}/{m.get('limit')}\n")

    # 1) censo
    if args.desde:
        with open(args.desde, encoding="utf-8") as fh:
            censo = json.load(fh)
        print(f"Censo leído de {args.desde} (0 créditos).")
    else:
        censo = _censo(args.vencidos)
    _imprimir_censo(censo, args.vencidos)

    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as fh:
            json.dump(censo, fh, ensure_ascii=False, indent=1)
        print(f"\n(censo crudo guardado en {args.salida} — reusalo con --desde)")

    b1 = _saldo()
    print(f"\nCosto del censo: {_delta(b0, b1)}")

    # 2) cruce
    en_ambos = _cruce(censo)

    if args.censo:
        print("\n(--censo: no se probó el cashflow)")
        return

    # 3) cashflow
    if args.ticker:
        muestra = [_norm(t) for t in args.ticker.split(",") if t.strip()]
    else:
        muestra = en_ambos[:max(0, args.muestra)]
        if not muestra:                       # sin base o sin cruce → el del manual
            muestra = ["AL30"]
    print(f"\n{'=' * 72}\nPROBANDO CASHFLOW sobre {len(muestra)} ticker(s): "
          f"{', '.join(muestra)}\n(costo: 1 crédito por cupón devuelto)\n{'=' * 72}")

    resultados = [_probar_cashflow(t) for t in muestra]

    b2 = _saldo()
    ok = [r for r in resultados if r.get("ok")]
    con_cupones = [r for r in ok if r.get("cupones")]
    print(f"\n{'=' * 72}\nRESUMEN")
    print(f"  Universo 1816 (tickers únicos vigentes): {len(censo['instrumentos'])}")
    print(f"  En los dos lados (1816 ∩ Manager):       {len(en_ambos)}")
    print(f"  Cashflow probado: {len(ok)}/{len(muestra)} OK · "
          f"{len(con_cupones)} con cupones · "
          f"{sum(r.get('cupones', 0) for r in con_cupones)} cupones en total")
    for r in resultados:
        if not r.get("ok"):
            print(f"    ✘ {r['ticker']}: {r.get('error')}")
    print(f"  Costo del bloque cashflow: {_delta(b1, b2)}")
    if b2:
        d = b2.get("daily") or {}
        print(f"  Créditos DESPUÉS · día {d.get('used')}/{d.get('limit')}")
    print("=" * 72)


if __name__ == "__main__":
    main()
