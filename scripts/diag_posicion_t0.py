"""Diag READ-ONLY: ¿la posición valuada de Aunesa puede darnos T0 en vez de T-1?

CONTEXTO — qué hacemos hoy y por qué está atrasado
--------------------------------------------------
`jobs/portafolio_backfill.py --diario` (cron 11:00 UTC L-V) es el ÚNICO writer de
`portafolio.tenencia`, y de ahí cuelga TODO el negocio: /aum, /valuaciones, el PnL,
la Tenencia Valorizada, el "AuM gestionado" del Tablero Comercial.

Ese job aplica la llamada **regla H1**: `desde = X` → Aunesa devuelve la posición del
día hábil **ANTERIOR** a X. Por eso el job pide `desde = D + 1 hábil` y guarda `fecha = D`.
Consecuencia: en modo `--diario` pide `desde = hoy` y persiste el CIERRE DE AYER.
Todo el sistema mira T-1. Seguro, pero un día tarde.

LA PREGUNTA QUE NADIE MIDIÓ (docs/OPORTUNIDADES.md [D2])
--------------------------------------------------------
Si `desde = X` devuelve X-1, entonces para tener HOY (T0) habría que mandar
`desde = próximo día hábil` — una **fecha futura**. Nadie probó nunca si Aunesa la
acepta, y de esa respuesta depende la forma del proyecto [D1] entero:

  * Si la ACEPTA y trae datos nuevos → la tenencia T0 es un cambio de UN parámetro,
    sin estimar nada.
  * Si la rechaza (o devuelve lo mismo que el cierre) → hay que ESTIMAR T0 como
    "snapshot T-1 + boletos de hoy", que es otro proyecto y otro riesgo.

QUÉ HACE ESTE SCRIPT
--------------------
Sondea la MISMA cuenta con 4 fechas `desde` distintas y pone los resultados uno al
lado del otro, contra lo que ya está persistido en SQL y contra los boletos del día.

  H-1  = hábil anterior al último hábil   → control: debería dar T-2
  H0   = último día hábil (lo que pide el cron hoy) → debería dar T-1 (baseline)
  H+1  = próximo día hábil (FECHA FUTURA) → candidato a T0   ← LA PREGUNTA
  H+2  = el hábil siguiente               → candidato a T+1 (liquidación)

Y valida las tres cosas que hacen creíble la respuesta:
  1) que H0 reproduzca EXACTO lo que el job dejó en `portafolio.tenencia` (si no
     coincide, la sonda mide otra cosa y el resto del análisis no vale nada),
  2) qué trae la respuesta CRUDA (claves, campos de fecha, valores de `informacion`)
     — hoy el parser tira todo lo que no sea `Acumulado` sin que nadie haya mirado
     qué más viene,
  3) si H+1 difiere de H0, que el DELTA se explique con los boletos de hoy
     (`operaciones.negocio_movimientos`). Un delta que no cierra con los boletos no
     es "más fresco", es otra cosa.

SEGURIDAD — este script NO escribe NADA
---------------------------------------
Cero INSERT/UPDATE/DELETE: solo GETs a Aunesa (el mismo endpoint de lectura que ya
usa el job) y SELECTs. No toca `portafolio.tenencia`, ni `backfill_log`, ni
`job_runs`. No corre en paralelo ni en masa: 3 cuentas × 4 sondas = 12 llamadas
secuenciales (el cron hace ~1800 por día).

Uso:
    python -m scripts.diag_posicion_t0
    python -m scripts.diag_posicion_t0 --cuentas 1839,805,1346
    python -m scripts.diag_posicion_t0 --con-hasta        # sondea además `hasta` = `desde`
    python -m scripts.diag_posicion_t0 --dump /tmp/t0.json  # guarda el crudo para analizar
"""
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, date, datetime, timedelta

from api.services._sql import _q
from core.calendario import es_habil, proximo_habil
from jobs.aum import _SESSION, POSICION_URL, autenticar, obtener_cuentas
from jobs.portafolio_backfill import (
    _PARAMS_BASE,
    _load_assets_map,
    _parse,
    _timeout_for,
    cargar_contrapartes,
)

CUENTAS_DEFAULT = ("1839", "805", "1346")
TOL = 1e-6            # tolerancia de comparación de cantidades (float)


# ── helpers de fecha ──────────────────────────────────────────────────────────
def _habil_anterior(d: date) -> date:
    d -= timedelta(days=1)
    while not es_habil(d):
        d -= timedelta(days=1)
    return d


def _ultimo_habil(hoy: date) -> date:
    return hoy if es_habil(hoy) else _habil_anterior(hoy)


def _sondas(hoy: date) -> list[tuple[str, date, str]]:
    """(etiqueta, fecha `desde`, qué esperamos según la regla H1)."""
    h0 = _ultimo_habil(hoy)
    hm1 = _habil_anterior(h0)
    # OJO: `proximo_habil` ya devuelve el hábil ESTRICTAMENTE posterior — no hay que
    # sumarle un día antes (hacerlo se saltea un hábil entero, y la sonda pediría T+2
    # creyendo pedir T+1). Es el mismo helper que usa el job.
    hp1 = proximo_habil(h0)
    hp2 = proximo_habil(hp1)
    return [
        ("H-1", hm1, f"control · esperado = cierre de {_habil_anterior(hm1)}"),
        ("H0", h0, f"lo que pide el cron · esperado = cierre de {hm1}  ← BASELINE"),
        ("H+1", hp1, f"FECHA FUTURA · si anda, sería la posición de {h0} (T0)"),
        ("H+2", hp2, f"FECHA FUTURA +1 · sería la posición de {hp1} (T+1)"),
    ]


def _arg(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


# ── sonda (una llamada read-only) ─────────────────────────────────────────────
def _sondear(idc: str, denom: str, desde: date, headers: dict,
             con_hasta: bool = False) -> dict:
    """Un GET a `posicionValuada`. Devuelve status, timing y el JSON crudo."""
    params = {**_PARAMS_BASE, "desde": desde.strftime("%d/%m/%Y")}
    if con_hasta:
        params["hasta"] = desde.strftime("%d/%m/%Y")
    t0 = time.monotonic()
    try:
        resp = _SESSION.get(POSICION_URL.format(idc), params=params, headers=headers,
                            timeout=_timeout_for(idc, denom))
    except Exception as e:
        return {"status": None, "error": f"{type(e).__name__}: {e}",
                "ms": round((time.monotonic() - t0) * 1000), "raw": []}
    ms = round((time.monotonic() - t0) * 1000)
    out = {"status": resp.status_code, "ms": ms, "raw": [], "error": None}
    if resp.status_code == 204:
        out["error"] = "204 — cuenta sin posición"
        return out
    if resp.status_code != 200:
        out["error"] = (resp.text or "")[:300]
        return out
    try:
        data = resp.json()
    except Exception as e:
        out["error"] = f"respuesta no-JSON: {type(e).__name__}: {e} · {resp.text[:200]}"
        return out
    out["raw"] = data if isinstance(data, list) else []
    if not isinstance(data, list):
        out["error"] = f"la respuesta NO es una lista (type={type(data).__name__})"
    return out


def _posiciones(raw: list, idc: str, denom: str, amap: dict) -> dict[str, dict]:
    """Aplica el MISMO parser del job → {unidad: registro}. Mismas reglas de
    valuación, signo y agrupación que lo que se persistiría."""
    regs = _parse(raw, idc, denom, "1970-01-01", amap)
    return {r["unidad"]: r for r in regs}


# ── 1) matriz de sondeo ───────────────────────────────────────────────────────
def _informacion_breakdown(raw: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in raw:
        if isinstance(r, dict):
            k = str(r.get("informacion"))
            out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def matriz(resultados: dict) -> None:
    print("\n── 1) MATRIZ DE SONDEO (¿qué contesta Aunesa a cada `desde`?) ─────────────")
    print(f"   {'cuenta':<8} {'sonda':<5} {'desde':<11} {'HTTP':<6} {'ms':>6} "
          f"{'filas':>6} {'posic.':>7}  detalle")
    for idc, por_sonda in resultados.items():
        for etiqueta, r in por_sonda.items():
            st = r["status"] if r["status"] is not None else "EXC"
            marca = "  ⚠" if (r["error"] and r["status"] != 204) else ""
            det = (r["error"] or "")[:60] if r["error"] else \
                  f"informacion={_informacion_breakdown(r['raw'])}"
            print(f"   {idc:<8} {etiqueta:<5} {r['desde']:<11} {st!s:<6} {r['ms']:>6} "
                  f"{len(r['raw']):>6} {len(r['pos']):>7}{marca}  {det}")
    print("\n   Cómo leerlo: si H+1 devuelve HTTP != 200 o 0 posiciones, Aunesa NO acepta")
    print("   fecha futura → la tenencia T0 hay que ESTIMARLA (snapshot + boletos).")


# ── 2) anatomía de la respuesta cruda ─────────────────────────────────────────
def anatomia(resultados: dict) -> None:
    print("\n── 2) ANATOMÍA DE LA RESPUESTA CRUDA ──────────────────────────────────────")
    muestra = None
    for por_sonda in resultados.values():
        for r in por_sonda.values():
            if r["raw"]:
                muestra = r
                break
        if muestra:
            break
    if not muestra:
        print("   (ninguna sonda trajo filas — nada que anatomizar)")
        return
    raw = muestra["raw"]
    claves: dict[str, int] = {}
    for r in raw:
        if isinstance(r, dict):
            for k in r:
                claves[k] = claves.get(k, 0) + 1
    print(f"   muestra: cuenta {muestra['id_cuenta']} · sonda {muestra['sonda']} "
          f"· {len(raw)} filas")
    print(f"   CLAVES presentes: {sorted(claves)}")

    # El parser del job se queda SOLO con `informacion == 'Acumulado'`. Qué más viene:
    print(f"\n   valores de `informacion`: {_informacion_breakdown(raw)}")
    print("   (el job DESCARTA todo lo que no sea 'Acumulado' — si acá aparecen")
    print("    movimientos del día, ese es el otro camino posible hacia T0)")

    # Campos con pinta de fecha: son los que dirían A QUÉ FECHA corresponde la posición.
    fechas = {}
    for r in raw[:200]:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if any(t in k.lower() for t in ("fecha", "date", "liquid", "concert", "vto",
                                            "vencim")):
                fechas.setdefault(k, set()).add(str(v)[:19])
    if fechas:
        print("\n   CAMPOS DE FECHA encontrados (valores distintos, máx 6):")
        for k, vs in sorted(fechas.items()):
            print(f"     {k:<22} {sorted(vs)[:6]}")
        print("   → si alguno trae la fecha de la posición, deja de hacer falta inferirla.")
    else:
        print("\n   ⚠ NINGÚN campo de fecha en la respuesta: la fecha de la posición es")
        print("     una INFERENCIA nuestra (la regla H1), no un dato que Aunesa afirme.")

    ej = next((r for r in raw if isinstance(r, dict) and r.get("informacion") == "Acumulado"),
              raw[0])
    print(f"\n   FILA DE EJEMPLO (Acumulado):\n     {json.dumps(ej, ensure_ascii=False)[:600]}")


# ── 3) comparación entre sondas ───────────────────────────────────────────────
def comparar(resultados: dict) -> None:
    print("\n── 3) MISMA CUENTA, CADA SONDA: ¿cambia la posición? ──────────────────────")
    for idc, por_sonda in resultados.items():
        print(f"\n   ▸ cuenta {idc}")
        etiquetas = list(por_sonda.keys())
        unidades = sorted({u for r in por_sonda.values() for u in r["pos"]})
        if not unidades:
            print("     (ninguna sonda devolvió posiciones)")
            continue
        base = por_sonda.get("H0", {}).get("pos", {})
        # Una sonda que FALLÓ mostraría "—" en cada unidad, que se lee igual que
        # "la posición no existe". Se marca con ✗ en el encabezado para no confundir
        # "Aunesa dice que no hay" con "no pudimos preguntar".
        rotas = {e for e, r in por_sonda.items() if r["status"] != 200}
        titulos = {e: (f"{e}✗" if e in rotas else e) for e in etiquetas}
        if rotas:
            print(f"     ✗ = la sonda no respondió 200 ({', '.join(sorted(rotas))}) — "
                  f"sus columnas NO son 'sin posición'")

        print(f"     {'unidad':<26} " + " ".join(f"{titulos[e]:>16}" for e in etiquetas)
              + "   Δ vs H0")
        for u in unidades:
            celdas, deltas = [], []
            for e in etiquetas:
                p = por_sonda[e]["pos"].get(u)
                celdas.append(f"{p['cantidad']:>16,.2f}" if p
                              else f"{('✗' if e in rotas else '—'):>16}")
                if e != "H0" and e not in rotas:
                    q = (p or {}).get("cantidad", 0.0) - (base.get(u) or {}).get("cantidad", 0.0)
                    if abs(q) > TOL:
                        deltas.append(f"{e}{q:+,.2f}")
            marca = "  ← CAMBIA" if deltas else ""
            print(f"     {u[:26]:<26} " + " ".join(celdas) + f"   {' '.join(deltas)}{marca}")

        # Totales por moneda: el número que mira el negocio.
        print(f"\n     {'TOTAL valuación':<26} "
              + " ".join(f"{titulos[e]:>16}" for e in etiquetas))
        monedas = sorted({(p.get("moneda") or "?") for r in por_sonda.values()
                          for p in r["pos"].values()})
        for m in monedas:
            fila = []
            for e in etiquetas:
                if e in rotas:
                    fila.append(f"{'✗':>16}")
                    continue
                tot = sum(p["valuacion"] or 0 for p in por_sonda[e]["pos"].values()
                          if (p.get("moneda") or "?") == m)
                fila.append(f"{tot:>16,.2f}")
            print(f"     {('  ' + m)[:26]:<26} " + " ".join(fila))


# ── 4) contraste con lo persistido ────────────────────────────────────────────
def contra_sql(resultados: dict) -> None:
    """La sonda H0 debería reproducir EXACTO la última fecha guardada por el job.

    Es el control de que estamos midiendo lo mismo que el job persiste: si H0 no
    coincide con SQL, cualquier conclusión sobre H+1 es aire.
    """
    print("\n── 4) SONDA H0 vs lo que el job YA persistió en portafolio.tenencia ───────")
    for idc, por_sonda in resultados.items():
        h0 = por_sonda.get("H0")
        if not h0:
            continue
        try:
            filas = _q("SELECT fecha, unidad, cantidad, valuacion FROM portafolio.tenencia "
                       "WHERE id_cuenta = %(c)s "
                       "AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia "
                       "             WHERE id_cuenta = %(c)s)",
                       {"c": idc})
        except Exception as e:
            print(f"   cuenta {idc}: no pude leer SQL ({type(e).__name__}: {e})")
            continue
        if not filas:
            print(f"   cuenta {idc}: SIN filas en portafolio.tenencia (¿cuenta nueva o vacía?)")
            continue
        fecha_sql = filas[0]["fecha"]
        sql_map = {f["unidad"]: float(f["cantidad"] or 0) for f in filas}
        pos = {u: p["cantidad"] for u, p in h0["pos"].items()}
        solo_sql = sorted(set(sql_map) - set(pos))
        solo_api = sorted(set(pos) - set(sql_map))
        distintas = [(u, sql_map[u], pos[u]) for u in set(sql_map) & set(pos)
                     if abs(sql_map[u] - pos[u]) > TOL]
        ok = not solo_sql and not solo_api and not distintas
        print(f"\n   ▸ cuenta {idc} · SQL fecha={fecha_sql} ({len(sql_map)} unidades) "
              f"vs H0 desde={h0['desde']} ({len(pos)} unidades)  "
              f"{'✓ COINCIDE' if ok else '⚠ DIFIERE'}")
        for u in solo_sql[:5]:
            print(f"       solo en SQL : {u} ({sql_map[u]:,.2f})")
        for u in solo_api[:5]:
            print(f"       solo en API : {u} ({pos[u]:,.2f})")
        for u, a, b in distintas[:5]:
            print(f"       distinta    : {u}  SQL={a:,.2f}  API={b:,.2f}")
    print("\n   Si acá dice COINCIDE, la sonda mide lo mismo que el job → el resto vale.")


# ── 5) validación causal: ¿el delta son los boletos de hoy? ───────────────────
def boletos(resultados: dict, amap: dict, hoy: date) -> None:
    print("\n── 5) BOLETOS DEL DÍA (¿explican el delta H+1 − H0?) ──────────────────────")
    ticker2unidad: dict[str, list[str]] = {}
    for unidad, meta in amap.items():
        t = (meta.get("ticker") or "").strip().upper()
        if t:
            ticker2unidad.setdefault(t, []).append(unidad)

    desde = _habil_anterior(_ultimo_habil(hoy))
    for idc in resultados:
        try:
            filas = _q("SELECT fecha, ticker, op, categoria, cantidad, precio, importe, "
                       "moneda, estado FROM operaciones.negocio_movimientos "
                       "WHERE id_cuenta = %(c)s AND fecha >= %(d)s "
                       "ORDER BY fecha DESC, ticker", {"c": idc, "d": desde})
        except Exception as e:
            print(f"   cuenta {idc}: no pude leer negocio_movimientos "
                  f"({type(e).__name__}: {e})")
            continue
        print(f"\n   ▸ cuenta {idc} · boletos desde {desde}: {len(filas)}")
        if not filas:
            print("     (sin boletos — si igual hay delta entre sondas, NO son operaciones)")
            continue
        for f in filas[:15]:
            t = (f["ticker"] or "").strip().upper()
            us = ticker2unidad.get(t, [])
            uu = us[0] if len(us) == 1 else (f"{len(us)} matches" if us else "sin match")
            print(f"     {f['fecha']}  {t:<12} {str(f['op'] or '')[:10]:<10} "
                  f"cant={float(f['cantidad'] or 0):>14,.2f}  "
                  f"{f['moneda'] or ''!s:<4} {str(f['estado'] or '')[:12]:<12} → unidad: {uu}")
        if len(filas) > 15:
            print(f"     … y {len(filas) - 15} más")
    print("\n   Regla de oro: un delta entre sondas que NO se explica con estos boletos")
    print("   NO es 'dato más fresco' — es otra cosa (y hay que entender qué antes de usarlo).")


# ── 6) veredicto ──────────────────────────────────────────────────────────────
def veredicto(resultados: dict) -> None:
    print("\n── 6) VEREDICTO ───────────────────────────────────────────────────────────")
    for idc, por_sonda in resultados.items():
        h0, hp1 = por_sonda.get("H0"), por_sonda.get("H+1")
        if not h0 or not hp1:
            continue
        if hp1["status"] != 200 or not hp1["pos"]:
            print(f"   cuenta {idc}: ✗ H+1 (fecha futura) NO sirve "
                  f"[HTTP {hp1['status']} · {len(hp1['pos'])} posiciones] "
                  f"{(hp1['error'] or '')[:60]}")
            continue
        iguales = (set(h0["pos"]) == set(hp1["pos"]) and all(
            abs(h0["pos"][u]["cantidad"] - hp1["pos"][u]["cantidad"]) <= TOL
            for u in h0["pos"]))
        if iguales:
            print(f"   cuenta {idc}: ~ H+1 responde OK pero devuelve EXACTAMENTE lo mismo "
                  f"que H0 → Aunesa topea en el último cierre, no hay T0 por parámetro.")
        else:
            dif = len({u for u in set(h0['pos']) | set(hp1['pos'])
                       if abs((h0['pos'].get(u) or {}).get('cantidad', 0)
                              - (hp1['pos'].get(u) or {}).get('cantidad', 0)) > TOL})
            print(f"   cuenta {idc}: ✓ H+1 devuelve datos DISTINTOS ({dif} unidades) → "
                  f"candidato real a T0. Validar el delta contra los boletos (sección 5).")
    print("\n   NADA de esto cambia producción: el job sigue en T-1 hasta que se decida.")


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    cuentas = tuple(c.strip() for c in
                    (_arg("--cuentas") or ",".join(CUENTAS_DEFAULT)).split(",") if c.strip())
    con_hasta = "--con-hasta" in sys.argv
    dump = _arg("--dump")
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()   # ART

    print(f"\n{'=' * 78}")
    print(f"DIAG — posición valuada: T-1 (hoy) vs T0 / T+1   ·   {hoy.isoformat()} (ART)")
    print(f"{'=' * 78}")
    print("READ-ONLY: no escribe una sola fila. Solo GETs a Aunesa + SELECTs.\n")
    print(f"   cuentas sondeadas : {', '.join(cuentas)}")
    print(f"   hoy es hábil      : {es_habil(hoy)}")
    for etiqueta, d, nota in _sondas(hoy):
        print(f"   {etiqueta:<5} desde={d.isoformat()}  → {nota}")
    if con_hasta:
        print("   (+ variante con `hasta` = `desde`, para ver si acota la ventana)")

    cargar_contrapartes()
    amap = _load_assets_map()
    headers = autenticar()
    df = obtener_cuentas(headers)
    denoms = {str(r["id"]): str(r["denominacion"]) for _, r in df.iterrows()}
    print(f"\n   assets en el mapa : {len(amap)}   ·   cuentas activas en Aunesa: {len(denoms)}")

    resultados: dict[str, dict] = {}
    for idc in cuentas:
        denom = denoms.get(idc, "")
        if not denom:
            print(f"   ⚠ cuenta {idc} NO figura como activa en el listado de Aunesa "
                  f"— la sondeo igual")
        resultados[idc] = {}
        for etiqueta, d, _ in _sondas(hoy):
            r = _sondear(idc, denom, d, headers, con_hasta=False)
            r.update({"id_cuenta": idc, "sonda": etiqueta, "desde": d.isoformat(),
                      "pos": _posiciones(r["raw"], idc, denom, amap)})
            resultados[idc][etiqueta] = r
            if con_hasta:
                r2 = _sondear(idc, denom, d, headers, con_hasta=True)
                r2.update({"id_cuenta": idc, "sonda": f"{etiqueta}h", "desde": d.isoformat(),
                           "pos": _posiciones(r2["raw"], idc, denom, amap)})
                resultados[idc][f"{etiqueta}h"] = r2

    matriz(resultados)
    anatomia(resultados)
    comparar(resultados)
    contra_sql(resultados)
    boletos(resultados, amap, hoy)
    veredicto(resultados)

    if dump:
        payload = {idc: {e: {k: v for k, v in r.items() if k != "pos"}
                         for e, r in por.items()} for idc, por in resultados.items()}
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)
        print(f"\n   crudo completo guardado en {dump}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
