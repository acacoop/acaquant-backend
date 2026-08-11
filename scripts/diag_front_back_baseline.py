"""diag_front_back_baseline.py — ¿portar lógica del front al back MEJORA la latencia?

Read-only. Antes de mover una línea (REGLA #2), este diag mide sobre la data REAL
del Droplet las tres cosas que deciden si un candidato vale la pena:

  1) **ms del servicio** — frío (1ª llamada) y tibio (2ª). Si el 2º tiempo se
     desploma, el service está `@cached` y el número tibio NO es CPU: es cache.
  2) **bytes que viajan HOY** — JSON crudo y **gzip**, que es lo que realmente
     cruza la red (Vercel/nginx comprimen). El gzip es el número que importa.
  3) **bytes que viajarían DESPUÉS** — el payload agregado, simulado acá con la
     MISMA regla que hoy corre en el browser (portada literal desde el .tsx que
     se indica en cada candidato). No es una estimación de forma: son los
     números reales sobre las filas reales.
  4) **KB/hora por usuario** — bytes × frecuencia de poll. Un payload de 200 KB
     que se pollea cada 2s son 350 MB/hora/usuario; el mismo payload pedido una
     vez al abrir la vista no es un problema de latencia.

Lo que este diag **NO** mide: el tiempo de CPU del browser. Eso se mide del otro
lado (`src/lib/perf.ts` en acaquant-frontend, flag `acaquant:perf` en
localStorage) — son dos mitades del mismo número y hay que mirar las dos.

Uso (Droplet, desde la raíz del repo):
    python -m scripts.diag_front_back_baseline              # todos los candidatos
    python -m scripts.diag_front_back_baseline --solo pulso # uno solo
    python -m scripts.diag_front_back_baseline --json out.json   # para diffear después

El `--json` es el punto: se corre ANTES de tocar nada (baseline) y DESPUÉS de
portar, y se comparan los dos archivos. Sin ese par de archivos, "mejoró la
latencia" es una opinión.

Read-only de verdad: solo llama services de lectura. No escribe, no borra.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

# ── helpers de medición ───────────────────────────────────────────────────────


def _json_bytes(obj: Any) -> int:
    """Bytes del payload serializado tal como sale por HTTP (default=str: el
    encoder de FastAPI resuelve date/Decimal; acá lo replicamos para no
    subestimar el tamaño)."""
    return len(json.dumps(obj, default=str, separators=(",", ":")).encode())


def _gzip_bytes(obj: Any) -> int:
    """Bytes DESPUÉS de comprimir — lo que de verdad viaja por la red."""
    raw = json.dumps(obj, default=str, separators=(",", ":")).encode()
    return len(gzip.compress(raw, compresslevel=6))


def _kb(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n / 1024:,.1f}"


def _medir(fn: Callable[[], Any],
           invalidar: tuple[str, ...] = ()) -> tuple[float, float, float | None, Any]:
    """(ms_frío, ms_tibio, ms_recurrente, payload).

    Las tres corridas miden cosas distintas y confundirlas lleva a la
    conclusión equivocada:

    - **frío**: primera llamada del proceso. Incluye warm-up que NO se repite
      (imports, pool de conexiones, los `@cached` internos de TTL largo). Es el
      peor caso absoluto, no el costo habitual.
    - **tibio**: segunda llamada inmediata. Si el service está `@cached` esto
      es un cache HIT y da ~0 — no dice nada del costo real.
    - **recurrente**: se INVALIDA el cache del service y se vuelve a llamar,
      con el proceso ya caliente. **Este es el número que importa**: lo que
      cuesta un cache miss en régimen, que es lo que paga el usuario cada vez
      que expira el TTL. Sin esto, un endpoint con TTL de 2s y 1s de cómputo
      parece gratis ("tibio 0.0 ms") cuando en realidad está al límite.
    """
    t0 = time.perf_counter()
    out = fn()
    frio = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    fn()
    tibio = (time.perf_counter() - t0) * 1000

    recurrente: float | None = None
    if invalidar:
        try:
            from api.cache import invalidate
            invalidate(*invalidar)
            t0 = time.perf_counter()
            fn()
            recurrente = (time.perf_counter() - t0) * 1000
        except Exception as e:
            logging.getLogger(__name__).warning("no se pudo medir recurrente: %s", e)
    return frio, tibio, recurrente, out


def _n(obj: Any) -> int | None:
    """Cardinalidad para dimensionar el payload (filas, o claves si es dict)."""
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        return len(obj)
    return None


# ── simulaciones del "DESPUÉS" — portadas LITERAL del front ───────────────────
# Cada una lleva el archivo .tsx del que se copió la regla. Si el front cambia y
# esto no, el número de "después" miente: es la misma trampa que la duplicación
# que estamos midiendo, así que van juntas y con la referencia al lado.


def _wavg(items: list[tuple[float | None, float]]) -> float | None:
    """metricas-panel.tsx::wavg — ponderado por volumen; si no hay volumen
    (w=0 en todos) cae a promedio simple para no dejar la fila vacía."""
    sw = swp = sp = 0.0
    n = 0
    for pct, w in items:
        if pct is None:
            continue
        n += 1
        sp += pct
        if w > 0:
            sw += w
            swp += pct * w
    if sw > 0:
        return swp / sw
    return sp / n if n else None


def _sim_pulso(rows: list[dict]) -> dict:
    """metricas-panel.tsx::PulsoRubrosPanel — agregado por RUBRO + total mercado.
    Es EXACTAMENTE lo que hoy calcula el browser en cada poll de 2s."""
    por_rubro: dict[str, list[dict]] = {}
    for r in rows:
        rubro = (r.get("rubro") or "").strip() or "—"
        por_rubro.setdefault(rubro, []).append(r)

    def _v(rs: list[dict], campo: str) -> list[tuple[float | None, float]]:
        return [(r.get(campo), r.get("adr_dollar_vol") or 0) for r in rs]

    rubros = []
    for rubro, rs in por_rubro.items():
        up = sum(1 for r in rs if r.get("adr_vs_1d_pct") is not None
                 and r["adr_vs_1d_pct"] > 0)
        down = sum(1 for r in rs if r.get("adr_vs_1d_pct") is not None
                   and r["adr_vs_1d_pct"] < 0)
        rubros.append({
            "rubro": rubro,
            "vol": sum(r.get("adr_dollar_vol") or 0 for r in rs),
            "p1d": _wavg(_v(rs, "adr_vs_1d_pct")),
            "wtd": _wavg(_v(rs, "adr_ret_wtd_pct")),
            "r15": _wavg(_v(rs, "adr_ret_15r_pct")),
            "mtd": _wavg(_v(rs, "adr_ret_mtd_pct")),
            "ytd": _wavg(_v(rs, "adr_ret_ytd_pct")),
            "up": up, "down": down,
        })
    total = {
        "vol": sum(r.get("adr_dollar_vol") or 0 for r in rows),
        "p1d": _wavg([(r.get("adr_vs_1d_pct"), r.get("adr_dollar_vol") or 0) for r in rows]),
        "up": sum(1 for r in rows if r.get("adr_vs_1d_pct") is not None
                  and r["adr_vs_1d_pct"] > 0),
        "down": sum(1 for r in rows if r.get("adr_vs_1d_pct") is not None
                    and r["adr_vs_1d_pct"] < 0),
    }
    return {"rubros": rubros, "total": total}


def _sim_movers(rows: list[dict]) -> list[dict]:
    """trading-movers-scanner.tsx::esMover — |intradía| o |1D| >= 4%.
    OJO: usa los campos del CEDEAR en ARS (intraday_pct / vs_1d_pct), NO los
    adr_* del pulso. Son dos umbrales sobre dos series distintas."""
    umbral = 4.0
    return [r for r in rows
            if abs(r.get("intraday_pct") or 0) >= umbral
            or abs(r.get("vs_1d_pct") or 0) >= umbral]


def _verificar_scanner(rows: list[dict]) -> str:
    """Cobertura de los campos que usan las simulaciones.

    Una simulación que lee un campo que ya no existe no explota: devuelve
    todo None y reporta un payload chiquito y un ahorro fantástico. Esta
    línea es el antídoto — si la cobertura da 0, el número de 'después' NO
    vale y hay que revisar los nombres contra `scanner_sql.get_cedears_scanner`.
    """
    if not rows:
        return "sin filas — el scanner no devolvió nada (¿fuera de rueda?)"
    campos = ("rubro", "adr_dollar_vol", "adr_vs_1d_pct", "intraday_pct", "vs_1d_pct")
    partes = []
    for c in campos:
        n = sum(1 for r in rows if r.get(c) is not None)
        marca = " ⚠" if n == 0 else ""
        partes.append(f"{c}={n}/{len(rows)}{marca}")
    return "cobertura de campos → " + "  ".join(partes)


# ── candidatos ────────────────────────────────────────────────────────────────
# `llamar` devuelve el payload que HOY manda el backend.
# `simular` (opcional) devuelve el payload que mandaría DESPUÉS de portar.
# `poll_s` = cada cuánto lo pide el front (None = una sola vez al abrir la vista).
# `desglose` (opcional) = bytes por clave, para ver QUÉ parte del payload pesa.


def _c_scanner() -> Any:
    from api.services import scanner_sql
    return scanner_sql.get_cedears_scanner()


def _c_retorno_total() -> Any:
    from api.services.renta_fija import get_retorno_total_data
    return {c: get_retorno_total_data(curva=c)
            for c in ("tasa_fija", "cer", "soberanos")}


def _c_hist_forwards() -> Any:
    from api.services.mercado_hist_sql import get_historico_forwards
    return get_historico_forwards()


def _c_comercial() -> Any:
    from api.services.comercial_sql import analisis_comercial
    return analisis_comercial(operador="__todos__")


CANDIDATOS: list[dict] = [
    {
        "id": "pulso",
        "titulo": "PULSO por rubro (scanner CEDEARs)",
        "front": "metricas-panel.tsx",
        "llamar": _c_scanner,
        "simular": _sim_pulso,
        "verificar": _verificar_scanner,
        "poll_s": 2,
        "invalidar": ("get_cedears_scanner",),
        "ttl_s": 2,   # @cached(ttl=2) en scanner_sql.py — igual que el poll
        "nota": "El universo YA se baja para la TABLA del scanner. Un endpoint "
                "/pulso AGREGA un request, no lo reemplaza: el ahorro de red es "
                "0 salvo que la tabla también se recorte. El valor de portarlo "
                "es que el copiloto y la vista dejen de tener dos _wavg.",
    },
    {
        "id": "movers",
        "titulo": "MOVERS ±4% (radar de Trading)",
        "front": "trading-movers-scanner.tsx",
        "llamar": _c_scanner,
        "simular": _sim_movers,
        "verificar": _verificar_scanner,
        "poll_s": 2,
        "invalidar": ("get_cedears_scanner",),
        "ttl_s": 2,
        "nota": "Acá el universo se baja SOLO para filtrar unas pocas filas: "
                "en /trading no hay tabla que consuma el resto. Este sí es un "
                "ahorro de red real y se mide en el 'después'.",
    },
    {
        "id": "retorno_total",
        "titulo": "RETORNO TOTAL (3 fetches en paralelo al abrir)",
        "front": "research-retorno-total.tsx",
        "llamar": _c_retorno_total,
        "simular": None,
        "poll_s": None,
        "invalidar": ("get_historico_curva",),
        "ttl_s": 60,  # @cached(ttl=60) sobre get_historico_curva
        "desglose": True,
        "nota": "Una sola vez al montar, no se pollea → el peso pega en el "
                "TIEMPO DE APERTURA, no en el sostenido. El desglose por clave "
                "dice cuánto es rows vs mep/oficial/flujos (las series de dólar "
                "van completas y el front las usa como lookup).",
    },
    {
        "id": "hist_forwards",
        "titulo": "Histórico de forwards (matriz de pares)",
        "front": "research-forwards.tsx",
        "llamar": _c_hist_forwards,
        "simular": None,
        "poll_s": None,
        "invalidar": ("get_historico_forwards",),
        "ttl_s": 300,
        "nota": "Sin filtro trae TODO el histórico. El front ya puede pedir "
                "?curva=&desde= — comparar este número contra el filtrado dice "
                "si el problema es el endpoint o cómo lo llama la vista.",
    },
    {
        "id": "comercial",
        "titulo": "Análisis comercial (dump crudo de clientes)",
        "front": "comercial-*-view.tsx",
        "llamar": _c_comercial,
        "simular": None,
        "poll_s": None,
        "nota": "Los KPIs (estados, cupos, pivotes) se arman en el cliente "
                "sobre este dump. El dump igual hace falta para la tabla, así "
                "que portar los KPIs no baja bytes: baja CPU del browser.",
    },
]


# ── reporte ───────────────────────────────────────────────────────────────────


def _desglose_bytes(payload: Any) -> list[tuple[str, int, int]]:
    """(clave, bytes, gzip) del primer nivel — para ver qué parte pesa."""
    out: list[tuple[str, int, int]] = []
    if not isinstance(payload, dict):
        return out
    for k, v in payload.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out.append((f"{k}.{k2}", _json_bytes(v2), _gzip_bytes(v2)))
        else:
            out.append((k, _json_bytes(v), _gzip_bytes(v)))
    return sorted(out, key=lambda t: -t[1])


def _correr(cand: dict) -> dict:
    print("═" * 78)
    print(f"{cand['id'].upper()} — {cand['titulo']}")
    print(f"   front: {cand['front']}")
    print("═" * 78)
    res: dict = {"id": cand["id"], "titulo": cand["titulo"], "front": cand["front"]}
    try:
        frio, tibio, recurrente, payload = _medir(
            cand["llamar"], cand.get("invalidar", ()))
    except Exception as e:  # un candidato caído no puede tumbar el diag entero
        print(f"   ⚠ NO SE PUDO MEDIR: {type(e).__name__}: {e}\n")
        res["error"] = f"{type(e).__name__}: {e}"
        return res

    b_ahora, g_ahora = _json_bytes(payload), _gzip_bytes(payload)
    res |= {"ms_frio": round(frio, 1), "ms_tibio": round(tibio, 1),
            "ms_recurrente": round(recurrente, 1) if recurrente is not None else None,
            "ttl_s": cand.get("ttl_s"),
            "n": _n(payload), "bytes": b_ahora, "gzip": g_ahora}

    print(f"   servicio   : {frio:8.1f} ms frío  |  {tibio:8.1f} ms tibio"
          f"{'   ← @cached (el tibio es cache, no CPU)' if tibio < frio / 10 else ''}")
    if recurrente is not None:
        ttl = cand.get("ttl_s")
        print(f"   RECURRENTE : {recurrente:8.1f} ms  ← lo que cuesta un cache miss "
              f"con el proceso caliente" + (f" (TTL {ttl}s)" if ttl else ""))
        # Un cómputo que tarda más que su propio TTL nunca llega a servirse
        # cacheado: cada poll paga el precio completo.
        if ttl and recurrente > ttl * 1000 * 0.5:
            print(f"   {'⚠ ATENCIÓN':<12}: el cómputo ({recurrente:.0f} ms) es "
                  f"comparable al TTL ({ttl * 1000} ms) — el cache casi no ayuda")
    print(f"   payload HOY: {_kb(b_ahora):>10} KB  |  {_kb(g_ahora):>8} KB gzip"
          f"  |  n={_n(payload)}")

    if cand.get("poll_s"):
        por_hora = g_ahora * (3600 / cand["poll_s"])
        res["kb_hora_usuario"] = round(por_hora / 1024, 1)
        print(f"   poll {cand['poll_s']}s     : {por_hora / 1024 / 1024:8.1f} MB/hora "
              f"por usuario con la vista abierta (gzip)")
    else:
        print("   poll        : una sola vez al abrir la vista (no se pollea)")

    if cand.get("verificar"):
        try:
            linea = cand["verificar"](payload)
            res["verificacion"] = linea
            print(f"   {linea}")
        except Exception as e:
            print(f"   ⚠ verificación falló: {type(e).__name__}: {e}")

    if cand.get("simular"):
        try:
            after = cand["simular"](payload)
            b_desp, g_desp = _json_bytes(after), _gzip_bytes(after)
            ahorro = 100 * (1 - g_desp / g_ahora) if g_ahora else 0
            res |= {"bytes_despues": b_desp, "gzip_despues": g_desp,
                    "n_despues": _n(after), "ahorro_gzip_pct": round(ahorro, 1)}
            print(f"   payload DESP:{_kb(b_desp):>10} KB  |  {_kb(g_desp):>8} KB gzip"
                  f"  |  n={_n(after)}")
            print(f"   AHORRO      : {ahorro:8.1f} % del gzip")
            if cand.get("poll_s"):
                d = (g_ahora - g_desp) * (3600 / cand["poll_s"])
                print(f"                 {d / 1024 / 1024:8.1f} MB/hora/usuario menos")
        except Exception as e:
            print(f"   ⚠ simulación falló: {type(e).__name__}: {e}")
            res["error_simulacion"] = f"{type(e).__name__}: {e}"

    if cand.get("desglose"):
        print("   desglose del payload (qué parte pesa):")
        for k, b, g in _desglose_bytes(payload)[:12]:
            print(f"      {k:<28} {_kb(b):>10} KB  |  {_kb(g):>8} KB gzip")
        res["desglose"] = [{"clave": k, "bytes": b, "gzip": g}
                           for k, b, g in _desglose_bytes(payload)]

    print(f"\n   → {cand['nota']}\n")
    res["nota"] = cand["nota"]
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--solo", help="correr un solo candidato por id")
    ap.add_argument("--json", dest="salida", help="guardar el resultado (baseline)")
    args = ap.parse_args()

    cands = CANDIDATOS
    if args.solo:
        cands = [c for c in CANDIDATOS if c["id"] == args.solo]
        if not cands:
            ids = ", ".join(c["id"] for c in CANDIDATOS)
            print(f"id desconocido: {args.solo}. Disponibles: {ids}")
            return 2

    print("\nBASELINE front→back — cuánto cuesta HOY lo que calcula el browser")
    print("Los ms son del SERVICIO (no incluyen red ni render). El gzip es lo que viaja.\n")

    out = [_correr(c) for c in cands]

    print("═" * 78)
    print("RESUMEN (gzip = lo que viaja)")
    print("═" * 78)
    print(f"{'candidato':<16}{'ms recurr':>10}{'KB hoy':>10}{'KB desp':>10}{'ahorro':>9}"
          f"{'MB/h/usr':>10}")
    for r in out:
        if r.get("error"):
            print(f"{r['id']:<16}{'ERROR':>10}")
            continue
        mbh = r.get("kb_hora_usuario")
        ahorro = f"{r['ahorro_gzip_pct']:.0f}%" if "ahorro_gzip_pct" in r else "—"
        mb_hora = f"{mbh / 1024:.0f}" if mbh else "—"
        # El recurrente manda; si no se pudo medir cae al frío, marcado con *.
        ms = (f"{r['ms_recurrente']:.0f}" if r.get("ms_recurrente") is not None
              else f"{r['ms_frio']:.0f}*")
        print(f"{r['id']:<16}{ms:>10}{_kb(r['gzip']):>10}"
              f"{_kb(r.get('gzip_despues')):>10}{ahorro:>9}{mb_hora:>10}")
    print("* sin medición recurrente: es el frío (incluye warm-up que no se repite)")

    print("\nFalta la otra mitad: el CPU del browser. Medirla en el front con "
          "localStorage.setItem('acaquant:perf','1') y recargar la vista.")

    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nBaseline guardado en {args.salida} — re-correr y diffear "
              "DESPUÉS de portar para tener el antes/después real.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
