"""Diag READ-ONLY: SIMULAR la posición de los próximos días desde lo que ya tenemos.

LA PREGUNTA
-----------
Ya sabemos que Aunesa nos puede dar T0 y T+1 pidiendo `desde` = fecha futura
(`scripts/diag_posicion_t0.py`). Pero eso cuesta llamadas nuevas al custodio.

La alternativa es RECONSTRUIRLA con lo que ya está en nuestra base:

    última foto de `portafolio.tenencia`  +  boletos de `operaciones.negocio_movimientos`
    aplicados según su fecha de LIQUIDACIÓN  =  ¿la posición de los días siguientes?

Este script hace esa simulación y la devuelve en el mismo formato que la posición
(unidad → cantidad), una por cada día siguiente. SOLO CANTIDADES: el precio no
entra, no es lo que está en discusión.

CÓMO SE VALIDA
--------------
Con `--comparar` pide a Aunesa la posición REAL de esas mismas fechas (el mismo
GET read-only del otro diag) y muestra la diferencia unidad por unidad. O sea: la
simulación se contrasta contra la respuesta del custodio, que es la verdad.

  * Si cierra al VN → la posición se puede derivar sin pegarle a Aunesa por cuenta.
  * Si no cierra → el residuo dice EXACTAMENTE qué movimiento falta, y ahí se
    decide si se arregla la derivación o si hay que ir a buscar el dato al custodio.

LAS DOS REGLAS QUE ESTE SCRIPT ASUME (y que el resultado pone a prueba)
----------------------------------------------------------------------
1) FECHA DE LIQUIDACIÓN. `negocio_movimientos` NO la trae: guarda `fecha`
   (concertación) y `plazo` como texto ("24hs", "CI", "1 días"). Se deriva con
   `_liquidacion()`. Cada movimiento se imprime con su fecha derivada al lado
   para poder auditarla a ojo — si la regla está mal, se ve acá antes que en el total.

2) SIGNO Y APLICACIÓN. `cantidad` viene con signo cliente (compra +, venta −),
   igual que `portafolio.tenencia` → se suma directo a la unidad del título.
   `importe` (también signo cliente) se suma a la unidad de la MONEDA (ARS/USD),
   que es como el cash vive en la tenencia.

LO QUE YA SE SABE QUE PUEDE FALTAR (mirar acá primero si no cierra)
------------------------------------------------------------------
`api/services/aunesa_negocio.EXCLUIR_SUBSTRINGS` descarta en la INGESTA todo lo
que contenga `otc`, `usdl` o `integracion de garantias`. Eso nunca llegó a
`negocio_movimientos` → ninguna simulación lo puede recuperar. El script avisa
cuando el residuo huele a eso.

READ-ONLY: no escribe nada. Sin `--comparar` no toca Aunesa siquiera (es 100% SQL).

ESTADO DE LA VALIDACIÓN (2026-08-11, cuenta 805, 11 y 12/08)
------------------------------------------------------------
Cierra EXACTO en las 8 unidades y en las dos fechas, efectivo incluido. El único
error que hubo fue un supuesto mío sobre cuándo liquida la apertura de una caución
(ver `_liquidacion`). Falta ampliarlo a muchas cuentas — para eso está `--top`.

Uso:
    python -m scripts.diag_simular_posicion                     # cuenta 805, solo SQL
    python -m scripts.diag_simular_posicion --comparar          # + posición real de Aunesa
    python -m scripts.diag_simular_posicion --cuenta 1346 --dias 3
    python -m scripts.diag_simular_posicion --cuentas 805,1346,1839 --comparar
    python -m scripts.diag_simular_posicion --top 25 --comparar # las 25 más movidas, resumen
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta

from api.services._sql import _q
from core.calendario import es_habil, proximo_habil

CUENTA_DEFAULT = "805"
DIAS_DEFAULT = 2          # cuántos días hábiles simular hacia adelante
TOL = 1e-6


def _arg(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


# ── regla de liquidación ──────────────────────────────────────────────────────
def _liquidacion(concertacion: date, plazo: str | None,
                 categoria: str | None = None) -> tuple[date, str]:
    """(fecha de liquidación, regla aplicada) a partir de concertación + `plazo`.

    `plazo` es TEXTO libre de Aunesa, parseado por `aunesa_negocio.parse_informacion`:
    "24hs" (contado 24 horas), "CI"/"Inm"/"Contado Inmediato" (mismo día), "N días"
    (el TÉRMINO de una caución). Cualquier otra cosa cae en el default y se marca
    como tal para que se vea en el listado — no se adivina en silencio.

    ⚠ CAUCIONES: la APERTURA y el CIERRE de la misma caución comparten el MISMO
    texto de plazo ("31 días") pero liquidan en momentos opuestos — la apertura
    mueve la plata al principio, el cierre recién al vencimiento. Sin mirar la
    `categoria` (`caucion_*_ap` / `caucion_*_ci`) una apertura se mandaría 31 días
    al futuro y la simulación quedaría corta justo en la línea de efectivo.

    La APERTURA liquida a 24hs, como cualquier boleto — MEDIDO el 2026-08-11 en la
    cuenta 805: la caución tomadora de ARS 3.143.854 concertada el 10/08 NO estaba
    en la foto del 10/08 y SÍ estaba en la posición real del 11/08. La primera
    versión de este script asumía "mismo día", la daba por incluida en la foto y
    nunca la aplicaba: el ARS quedaba corto por 3.143.854,00 exactos — el monto
    entero de la caución — en TODAS las fechas simuladas. Los títulos cerraban
    perfecto y solo el efectivo fallaba, siempre por el mismo número.
    """
    cat = (categoria or "").strip().lower()
    p = (plazo or "").strip().lower()
    if cat.startswith("caucion"):
        if cat.endswith("_ap"):
            return proximo_habil(concertacion), "caución APERTURA → próximo hábil (24hs)"
        if cat.endswith("_ci"):
            num = "".join(c for c in p if c.isdigit())
            if num:
                d = concertacion + timedelta(days=int(num))
                while not es_habil(d):
                    d += timedelta(days=1)
                return d, f"caución CIERRE → concert. + {num} días corridos (SUPUESTO)"
            return proximo_habil(concertacion), "caución CIERRE sin plazo → próx. hábil (SUPUESTO)"
    if not p:
        return concertacion, "sin plazo → mismo día (SUPUESTO)"
    if "24" in p:
        return proximo_habil(concertacion), "24hs → próximo hábil"
    if p.startswith(("ci", "inm", "contado inm")):
        return concertacion, "CI → mismo día"
    if "d" in p:                       # "1 días", "31 días"
        num = "".join(c for c in p if c.isdigit())
        if num:
            d = concertacion + timedelta(days=int(num))
            while not es_habil(d):
                d += timedelta(days=1)
            return d, f"{num} días corridos → {d.isoformat()} (SUPUESTO: corridos, no hábiles)"
    return proximo_habil(concertacion), f"plazo {plazo!r} no reconocido → próximo hábil (SUPUESTO)"


# ── 1) base ───────────────────────────────────────────────────────────────────
def base_tenencia(cuenta: str) -> tuple[date | None, dict[str, float]]:
    filas = _q("SELECT fecha, unidad, cantidad FROM portafolio.tenencia "
               "WHERE id_cuenta = %(c)s "
               "AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia WHERE id_cuenta = %(c)s) "
               "ORDER BY unidad", {"c": cuenta})
    if not filas:
        return None, {}
    return filas[0]["fecha"], {f["unidad"]: float(f["cantidad"] or 0) for f in filas}


# ── 2) movimientos ────────────────────────────────────────────────────────────
def movimientos(cuenta: str, desde: date) -> list[dict]:
    """Boletos de la cuenta con concertación >= la fecha de la foto.

    `anulado_en` (reconciliación de jobs/negocio_movimientos) excluye los boletos
    que Aunesa dejó de devolver: aplicar un boleto anulado sería inventar posición.
    """
    return _q(
        "SELECT fecha, comprobante, categoria, op, ticker, cantidad, precio, importe, "
        "       moneda, plazo, estado, informacion "
        "FROM operaciones.negocio_movimientos "
        "WHERE id_cuenta = %(c)s AND fecha >= %(d)s AND anulado_en IS NULL "
        "ORDER BY fecha, comprobante", {"c": cuenta, "d": desde})


def _ticker_a_unidad() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in _q("SELECT unidad, ticker FROM portafolio.assets WHERE ticker IS NOT NULL"):
        t = (r["ticker"] or "").strip().upper()
        if t:
            out.setdefault(t, []).append(r["unidad"])
    return out


def listar_movimientos(movs: list[dict], base_fecha: date, t2u: dict) -> list[dict]:
    """Imprime cada boleto con su fecha de liquidación derivada y a qué unidad
    impacta. Devuelve los movimientos enriquecidos (los que ya liquidaron antes
    de la foto quedan marcados y NO se aplican: ya están adentro de la base)."""
    print("\n── 2) MOVIMIENTOS (con la fecha de liquidación DERIVADA) ──────────────────")
    print(f"   {'concert.':<11} {'liq.':<11} {'categoría':<18} {'ticker':<8} "
          f"{'cantidad':>14} {'importe':>16} {'mon':<5} {'plazo':<12} regla")
    out = []
    for m in movs:
        liq, regla = _liquidacion(m["fecha"], m["plazo"], m["categoria"])
        unidades = t2u.get((m["ticker"] or "").strip().upper(), [])
        unidad = unidades[0] if len(unidades) == 1 else None
        aplica = liq > base_fecha
        e = {**m, "liq": liq, "regla": regla, "unidad": unidad, "aplica": aplica,
             "amb": len(unidades) > 1}
        out.append(e)
        marca = "" if aplica else "   ← YA en la foto, no se aplica"
        if m["ticker"] and not unidad:
            marca += ("   ⚠ ticker AMBIGUO en assets" if unidades
                      else "   ⚠ ticker SIN unidad en assets")
        print(f"   {m['fecha']!s:<11} {liq!s:<11} {str(m['categoria'] or '')[:18]:<18} "
              f"{str(m['ticker'] or '—')[:8]:<8} {float(m['cantidad'] or 0):>14,.2f} "
              f"{float(m['importe'] or 0):>16,.2f} {str(m['moneda'] or '')[:5]:<5} "
              f"{str(m['plazo'] or '—')[:12]:<12} {regla}{marca}")
        print(f"        └ {str(m['informacion'] or '')[:110]}")
    if not movs:
        print("   (ninguno)")
    return out


# ── 3) simulación ─────────────────────────────────────────────────────────────
def simular(base: dict[str, float], movs: list[dict], fechas: list[date]) -> dict:
    """Aplica los movimientos por fecha de liquidación, acumulando.

    Devuelve {fecha: {unidad: cantidad}} — el mismo shape que la posición.
    """
    por_fecha: dict[date, list[dict]] = defaultdict(list)
    for m in movs:
        if m["aplica"]:
            por_fecha[m["liq"]].append(m)

    estado = dict(base)
    salida = {}
    for f in fechas:
        for m in por_fecha.get(f, []):
            cant = float(m["cantidad"] or 0)
            imp = float(m["importe"] or 0)
            # Pata TÍTULO: solo si el boleto tiene una unidad resuelta y cantidad.
            if m["unidad"] and cant:
                estado[m["unidad"]] = estado.get(m["unidad"], 0.0) + cant
            # Pata DINERO: el cash vive en la tenencia como unidad = la moneda.
            if imp and m["moneda"]:
                estado[m["moneda"]] = estado.get(m["moneda"], 0.0) + imp
        # Las unidades que quedan en 0 desaparecen de la posición (igual que el job,
        # que descarta `cantidad == 0`).
        salida[f] = {u: c for u, c in estado.items() if abs(c) > TOL}
        estado = dict(salida[f])
    return salida


def imprimir_posicion(titulo: str, pos: dict[str, float]) -> None:
    print(f"\n   ▸ {titulo}   ({len(pos)} unidades)")
    for u in sorted(pos):
        print(f"       {u[:52]:<52} {pos[u]:>18,.2f}")


# ── 4) comparación contra Aunesa (opcional) ───────────────────────────────────
def _aunesa_ctx():
    """Auth + catálogos, UNA vez para toda la corrida (no por cuenta)."""
    from jobs.aum import autenticar, obtener_cuentas
    from jobs.portafolio_backfill import _load_assets_map, cargar_contrapartes

    cargar_contrapartes()
    headers = autenticar()
    df = obtener_cuentas(headers)
    return {"headers": headers, "amap": _load_assets_map(),
            "denoms": {str(r["id"]): str(r["denominacion"]) for _, r in df.iterrows()}}


def comparar(cuenta: str, simulado: dict, ctx: dict, detalle: bool = True) -> list[dict]:
    """Pide a Aunesa la posición REAL de cada fecha simulada y la diffea.

    Regla H1: la posición AL día D se pide con `desde` = D + 1 hábil. Es el mismo
    GET de lectura que ya usa el job diario — no escribe nada.

    Devuelve una fila por fecha con el veredicto, para que el modo `--resumen`
    pueda juntar muchas cuentas sin imprimir el detalle de cada una.
    """
    from jobs.portafolio_backfill import _parse
    from scripts.diag_posicion_t0 import _sondear

    denom = ctx["denoms"].get(cuenta, "")
    veredictos = []
    if detalle:
        print("\n── 4) SIMULADO vs REAL (Aunesa) ───────────────────────────────────────────")

    for f in sorted(simulado):
        r = _sondear(cuenta, denom, proximo_habil(f), ctx["headers"])
        if r["status"] != 200:
            veredictos.append({"fecha": f, "error": f"HTTP {r['status']}", "difs": None})
            if detalle:
                print(f"\n   ▸ {f}: no pude traer la real (HTTP {r['status']} "
                      f"{(r['error'] or '')[:60]})")
            continue
        real = {x["unidad"]: x["cantidad"]
                for x in _parse(r["raw"], cuenta, denom, "1970-01-01", ctx["amap"])}
        sim = simulado[f]
        unidades = sorted(set(real) | set(sim))
        difs = [(u, sim.get(u), real.get(u)) for u in unidades
                if abs((sim.get(u) or 0) - (real.get(u) or 0)) > TOL]
        veredictos.append({"fecha": f, "error": None, "difs": difs, "n_unidades": len(unidades)})
        if not detalle:
            continue
        estado = "✓ CIERRA EXACTO" if not difs else f"⚠ {len(difs)} unidades no cierran"
        print(f"\n   ▸ {f}  (desde={proximo_habil(f)})   {estado}")
        print(f"       {'unidad':<52} {'SIMULADO':>16} {'REAL':>16} {'Δ':>16}")
        for u in unidades:
            s, rr = sim.get(u), real.get(u)
            d = (s or 0) - (rr or 0)
            marca = "  ←" if abs(d) > TOL else ""
            print(f"       {u[:52]:<52} "
                  f"{('—' if s is None else f'{s:,.2f}'):>16} "
                  f"{('—' if rr is None else f'{rr:,.2f}'):>16} {d:>16,.2f}{marca}")
    if detalle:
        print("\n   Si algo no cierra, mirá primero: (a) la fecha de liquidación derivada")
        print("   en la sección 2, (b) los movimientos que la ingesta EXCLUYE por diseño")
        print("   (otc / usdl / integracion de garantias), (c) aranceles dentro de `importe`.")
    return veredictos


# ── selección de cuentas ──────────────────────────────────────────────────────
def _cuentas_con_mas_boletos(n: int) -> list[str]:
    """Las N cuentas con más boletos desde la última foto global.

    Son las que más chance tienen de romper la simulación: si cierra en las que
    más se movieron, cierra. Validar sobre cuentas quietas no prueba nada.
    """
    filas = _q(
        "SELECT id_cuenta, COUNT(*) AS n FROM operaciones.negocio_movimientos "
        "WHERE fecha >= (SELECT MAX(fecha) FROM portafolio.tenencia) "
        "  AND anulado_en IS NULL AND id_cuenta IS NOT NULL "
        "GROUP BY id_cuenta ORDER BY n DESC LIMIT %(n)s", {"n": int(n)})
    return [f["id_cuenta"] for f in filas]


def _procesar(cuenta: str, dias: int, t2u: dict, detalle: bool) -> tuple[dict, list, date | None]:
    """Base + movimientos + simulación de una cuenta. Imprime si `detalle`."""
    base_fecha, base = base_tenencia(cuenta)
    if not base_fecha:
        if detalle:
            print(f"   ✗ la cuenta {cuenta} no tiene filas en portafolio.tenencia")
        return {}, [], None

    if detalle:
        print("── 1) BASE: última foto en portafolio.tenencia ────────────────────────────")
        imprimir_posicion(f"posición al {base_fecha}", base)

    movs_raw = movimientos(cuenta, base_fecha)
    if detalle:
        movs = listar_movimientos(movs_raw, base_fecha, t2u)
    else:
        movs = []
        for m in movs_raw:
            liq, regla = _liquidacion(m["fecha"], m["plazo"], m["categoria"])
            us = t2u.get((m["ticker"] or "").strip().upper(), [])
            movs.append({**m, "liq": liq, "regla": regla,
                         "unidad": us[0] if len(us) == 1 else None,
                         "aplica": liq > base_fecha})

    fechas, d = [], base_fecha
    for _ in range(dias):
        d = proximo_habil(d)
        fechas.append(d)
    simulado = simular(base, movs, fechas)

    if detalle:
        print("\n── 3) POSICIÓN SIMULADA (mismo formato que la posición real) ──────────────")
        for f in fechas:
            aplicados = sum(1 for m in movs if m["aplica"] and m["liq"] == f)
            imprimir_posicion(
                f"posición simulada al {f}  ·  {aplicados} movimiento(s) aplicados",
                simulado[f])
    return simulado, movs, base_fecha


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    dias = int(_arg("--dias", DIAS_DEFAULT))
    top = _arg("--top")
    lista = _arg("--cuentas") or _arg("--cuenta")
    resumen = "--resumen" in sys.argv or bool(top)

    if top:
        cuentas = _cuentas_con_mas_boletos(int(top))
    else:
        cuentas = [c.strip() for c in (lista or CUENTA_DEFAULT).split(",") if c.strip()]

    print(f"\n{'=' * 78}")
    print(f"SIMULACIÓN de posición · {len(cuentas)} cuenta(s) · "
          f"{dias} día(s) hábiles hacia adelante")
    print(f"{'=' * 78}")
    print("READ-ONLY · solo CANTIDADES (el precio no entra en esta prueba)")
    if top:
        print(f"   cuentas: las {top} con MÁS boletos desde la última foto "
              f"(las que más chance tienen de romper)")
    if resumen and "--comparar" not in sys.argv:
        print("   ⚠ el modo resumen sin --comparar no valida nada: agregá --comparar")
    print()

    t2u = _ticker_a_unidad()
    ctx = _aunesa_ctx() if "--comparar" in sys.argv else None
    filas = []

    for cuenta in cuentas:
        detalle = not resumen
        if detalle:
            print(f"\n{'─' * 78}\nCUENTA {cuenta}\n{'─' * 78}")
        simulado, movs, base_fecha = _procesar(cuenta, dias, t2u, detalle)
        if not base_fecha:
            filas.append({"cuenta": cuenta, "vered": None, "movs": 0})
            continue
        vered = comparar(cuenta, simulado, ctx, detalle=detalle) if ctx else None
        filas.append({"cuenta": cuenta, "vered": vered,
                      "movs": sum(1 for m in movs if m["aplica"])})

    if ctx:
        print(f"\n{'=' * 78}\nVEREDICTO POR CUENTA\n{'=' * 78}")
        print(f"   {'cuenta':<10} {'movs':>5}  {'fecha':<12} {'unid.':>6}  resultado")
        ok = roto = 0
        for f in filas:
            if f["vered"] is None:
                print(f"   {f['cuenta']:<10} {'—':>5}  (sin foto en portafolio.tenencia)")
                continue
            for v in f["vered"]:
                if v["error"]:
                    print(f"   {f['cuenta']:<10} {f['movs']:>5}  {v['fecha']!s:<12} "
                          f"{'—':>6}  no pude comparar ({v['error']})")
                    continue
                if v["difs"]:
                    roto += 1
                    peor = max(v["difs"], key=lambda d: abs((d[1] or 0) - (d[2] or 0)))
                    print(f"   {f['cuenta']:<10} {f['movs']:>5}  {v['fecha']!s:<12} "
                          f"{v['n_unidades']:>6}  ⚠ {len(v['difs'])} no cierran · "
                          f"peor: {peor[0][:28]} Δ={(peor[1] or 0) - (peor[2] or 0):,.2f}")
                else:
                    ok += 1
                    print(f"   {f['cuenta']:<10} {f['movs']:>5}  {v['fecha']!s:<12} "
                          f"{v['n_unidades']:>6}  ✓ cierra exacto")
        total = ok + roto
        pct = f"{100 * ok / total:.0f}%" if total else "—"
        print(f"\n   TOTAL: {ok}/{total} comparaciones cierran exacto ({pct})")
        print("   Cierra = la posición se puede DERIVAR sin pegarle a Aunesa por cuenta.")
    else:
        print("\n   (corré con --comparar para contrastarla contra la posición real de Aunesa)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
