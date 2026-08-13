"""Diag READ-ONLY: el endpoint NUEVO `cuentas/{id}/posiciones` (Resumen de posiciones).

QUÉ QUEREMOS AVERIGUAR
======================
Hoy toda la tenencia sale de `cuentas/{id}/posicionValuada`, que es una posición
**proyectada**: incluye lo que todavía no liquidó. Por eso una cuenta que hoy tiene
0 pesos puede aparecer NEGATIVA en ARS (la caución que vence mañana ya está contada).
Eso obliga al back office a hacer a mano el control de "títulos negativos".

El endpoint `cuentas/{id}/posiciones` (Resumen de cuenta → tipo "Posiciones") separa
`cantidadLiquidada` de `cantidadPendienteLiquidar`. Si eso es cierto contra datos
reales, el control de negativos se vuelve una consulta y no un trabajo manual — y
sería el insumo natural de un job LIVE (mismo patrón que `jobs/tenencia_live.py`:
solo presente, sin histórico).

Antes de escribir una línea de ese job hay que MEDIR (REGLA #2). Ninguna de estas
preguntas está respondida hoy:

  1. CONTRATO   — ¿qué formato de `fecha` acepta? (`posicionValuada` usa DD/MM/YYYY,
                  pero es otro endpoint y la doc no lo dice). ¿Y `incluirParking`?
  2. SEMÁNTICA  — ¿`fecha = hoy` devuelve HOY, o rige la "regla H1" del otro endpoint
                  (`desde = X` → devuelve el hábil ANTERIOR a X)? ¿Acepta futuro?
  3. SIGNO      — `posicionValuada` viene invertido (el parser hace `× -1`). ¿Este
                  también? Se mide, no se supone.
  4. IDENTIDAD  — el nuevo trae `especie` / `simboloLocal`; el nuestro trae `unidad`
                  (`[28902] NOMBRE`). ¿Se puede joinear? ¿Con qué cobertura?
  5. NEGATIVOS  — LA PREGUNTA: ¿qué viene negativo acá y qué viene negativo en
                  `posicionValuada`? Si el ARS negativo por caución desaparece de la
                  liquidada y aparece en la pendiente, la hipótesis del user está
                  confirmada con datos.
  6. PARKING    — qué agrega `incluirParking=True` (¿filas nuevas? ¿solo el bloque
                  `parking` de cada fila?).

QUÉ HACE
========
Sondea la misma cuenta (default 805) contra los DOS endpoints y pone los resultados
uno al lado del otro. Todas las llamadas son secuenciales y contadas: ~11 por cuenta
(el cron diario hace ~1.800), así que no le agrega presión a Aunesa.

SEGURIDAD — NO ESCRIBE NADA
===========================
Cero INSERT/UPDATE/DELETE. Solo GETs de lectura a Aunesa + SELECTs. No toca
`portafolio.tenencia`, ni `tenencia_live`, ni `job_runs`.

Uso:
    python -m scripts.diag_posiciones_resumen
    python -m scripts.diag_posiciones_resumen --cuentas 805,1839
    python -m scripts.diag_posiciones_resumen --especie AL30     # prueba el filtro
    python -m scripts.diag_posiciones_resumen --dump /tmp/pos.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta

from core import aunesa
from core.calendario import es_habil, proximo_habil
from jobs.aum import _SESSION, POSICION_URL, autenticar, obtener_cuentas
from jobs.portafolio_backfill import (
    _PARAMS_BASE,
    _load_assets_map,
    _parse,
    _timeout_for,
    cargar_contrapartes,
)

CUENTAS_DEFAULT = ("805",)
PATH_NUEVO = "cuentas/{}/posiciones"
TOL = 1e-6

# Formatos candidatos para el parámetro `fecha`. La doc solo dice "Fecha del
# resumen" — cuál acepta es exactamente lo que hay que medir.
FORMATOS = (("DD/MM/YYYY", "%d/%m/%Y"), ("YYYY-MM-DD", "%Y-%m-%d"))

_RE_CORCHETE = re.compile(r"^\[(\d+)\]")
_llamadas = 0


# ── fechas ────────────────────────────────────────────────────────────────────
def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _habil_anterior(d: date) -> date:
    d -= timedelta(days=1)
    while not es_habil(d):
        d -= timedelta(days=1)
    return d


def _ultimo_habil(hoy: date) -> date:
    return hoy if es_habil(hoy) else _habil_anterior(hoy)


def _arg(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


# ── llamadas ──────────────────────────────────────────────────────────────────
def _get_nuevo(idc: str, fecha: date, fmt: str, *, parking: str | None = None,
               especie: str | None = None) -> dict:
    """Un GET al endpoint nuevo. Devuelve status, ms, filas crudas y el error tal cual."""
    global _llamadas
    params: dict[str, str] = {"fecha": fecha.strftime(fmt)}
    if parking is not None:
        params["incluirParking"] = parking
    if especie:
        params["especie"] = especie
    etiqueta = (f"fecha={params['fecha']}"
                + (f" parking={parking}" if parking is not None else "")
                + (f" especie={especie}" if especie else ""))
    t0 = time.monotonic()
    try:
        resp = aunesa.get(PATH_NUEVO.format(idc), params, timeout=120, retries=2)
    except Exception as e:
        return {"etiqueta": etiqueta, "params": params, "status": None,
                "error": f"{type(e).__name__}: {e}", "ms": round((time.monotonic() - t0) * 1000),
                "raw": []}
    finally:
        _llamadas += 1
    out = {"etiqueta": etiqueta, "params": params, "status": resp.status_code,
           "ms": round((time.monotonic() - t0) * 1000), "raw": [], "error": None}
    if resp.status_code == 204:
        out["error"] = "204 — sin contenido"
        return out
    if resp.status_code != 200:
        # El CUERPO del error importa: un 400 de Aunesa trae `errors[].detail`, que
        # es lo que dice QUÉ parámetro está mal (formato de fecha, permiso, etc.).
        out["error"] = (resp.text or "")[:400]
        return out
    try:
        data = resp.json()
    except Exception as e:
        out["error"] = f"respuesta no-JSON: {type(e).__name__}: {e} · {(resp.text or '')[:200]}"
        return out
    if isinstance(data, list):
        out["raw"] = data
    elif isinstance(data, dict):
        # Por si viniera envuelto ({"data": [...]}): se reporta, no se adivina.
        out["raw"] = [data]
        out["error"] = f"la respuesta es un OBJETO, no una lista · claves={sorted(data)[:12]}"
    else:
        out["error"] = f"la respuesta no es lista ni objeto (type={type(data).__name__})"
    return out


def _get_valuada(idc: str, denom: str, desde: date, headers: dict) -> dict:
    """El endpoint VIEJO, con los mismos parámetros que usa el job diario."""
    global _llamadas
    params = {**_PARAMS_BASE, "desde": desde.strftime("%d/%m/%Y")}
    t0 = time.monotonic()
    try:
        resp = _SESSION.get(POSICION_URL.format(idc), params=params, headers=headers,
                            timeout=_timeout_for(idc, denom))
    except Exception as e:
        return {"status": None, "error": f"{type(e).__name__}: {e}",
                "ms": round((time.monotonic() - t0) * 1000), "raw": []}
    finally:
        _llamadas += 1
    out = {"status": resp.status_code, "ms": round((time.monotonic() - t0) * 1000),
           "raw": [], "error": None}
    if resp.status_code == 204:
        out["error"] = "204 — cuenta sin posición"
        return out
    if resp.status_code != 200:
        out["error"] = (resp.text or "")[:300]
        return out
    try:
        data = resp.json()
    except Exception as e:
        out["error"] = f"no-JSON: {type(e).__name__}: {e}"
        return out
    out["raw"] = data if isinstance(data, list) else []
    return out


# ── helpers de lectura ────────────────────────────────────────────────────────
def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _breakdown(raw: list, campo: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in raw:
        if isinstance(r, dict):
            k = str(r.get(campo))
            out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _clave(fila: dict) -> str:
    """Identidad de una fila del endpoint nuevo, para comparar entre sondas."""
    return "|".join(str(fila.get(k) or "") for k in
                    ("especie", "subCuenta", "estado", "lugar", "monedaCotizacion"))


# ── 1) contrato: qué parámetros acepta ────────────────────────────────────────
def contrato(idc: str, hoy: date) -> tuple[str | None, dict]:
    """Sondea los formatos de fecha. Devuelve (formato ganador, sondas)."""
    print("\n── 1) CONTRATO — ¿qué formato de `fecha` acepta? ──────────────────────────")
    h0 = _ultimo_habil(hoy)
    sondas = {}
    for nombre, fmt in FORMATOS:
        r = _get_nuevo(idc, h0, fmt)
        sondas[nombre] = r
        st = r["status"] if r["status"] is not None else "EXC"
        det = (r["error"] or "")[:90] if r["error"] else "ok"
        print(f"   {nombre:<12} {r['params']['fecha']:<12} HTTP {st!s:<5} "
              f"{r['ms']:>6}ms  {len(r['raw']):>5} filas   {det}")

    ganador = None
    for nombre, _ in FORMATOS:
        r = sondas[nombre]
        if r["status"] == 200 and r["raw"] and not r["error"]:
            ganador = nombre
            break
    if ganador is None:  # ningún 200 con filas: aceptar un 200/204 limpio igual
        for nombre, _ in FORMATOS:
            if sondas[nombre]["status"] in (200, 204):
                ganador = nombre
                break
    if ganador:
        print(f"\n   → formato usado en el resto del diag: {ganador}")
    else:
        print("\n   ⚠ NINGÚN formato funcionó. Mirá el cuerpo del error de arriba: si dice")
        print("     403/permiso, falta habilitar «[API] Cuenta - Resumen de posiciones» en")
        print("     Hígyrus para nuestro usuario (REGLA #6: eso solo lo puede pedir el user).")
    return ganador, sondas


# ── 2) semántica de `fecha` ───────────────────────────────────────────────────
def semantica(idc: str, hoy: date, fmt: str) -> dict:
    """¿`fecha = hoy` es hoy? ¿acepta futuro? ¿cambia algo entre días?"""
    print("\n── 2) SEMÁNTICA DE `fecha` — ¿qué día devuelve cada valor? ────────────────")
    h0 = _ultimo_habil(hoy)
    fechas = [("HOY", hoy), ("H0 (últ. hábil)", h0), ("H-1", _habil_anterior(h0)),
              ("H+1 (FUTURO)", proximo_habil(h0))]
    vistos: dict[str, dict] = {}
    out: dict[str, dict] = {}
    for etiqueta, d in fechas:
        iso = d.isoformat()
        if iso in vistos:                       # HOY y H0 coinciden si hoy es hábil
            out[etiqueta] = vistos[iso]
            print(f"   {etiqueta:<16} {iso}   (misma fecha que una sonda anterior)")
            continue
        r = _get_nuevo(idc, d, fmt)
        r["fecha"] = iso
        vistos[iso] = r
        out[etiqueta] = r
        st = r["status"] if r["status"] is not None else "EXC"
        liq = sum(_num(x.get("cantidadLiquidada")) for x in r["raw"] if isinstance(x, dict))
        pen = sum(_num(x.get("cantidadPendienteLiquidar")) for x in r["raw"]
                  if isinstance(x, dict))
        det = (r["error"] or "")[:70] if r["error"] else \
            f"Σliquidada={liq:,.2f}  Σpendiente={pen:,.2f}"
        print(f"   {etiqueta:<16} {iso}   HTTP {st!s:<5} {r['ms']:>6}ms "
              f"{len(r['raw']):>5} filas   {det}")

    # ¿Devuelven todas lo mismo? Si sí, `fecha` no está filtrando nada.
    firmas = {e: {_clave(f): (_num(f.get("cantidadLiquidada")),
                              _num(f.get("cantidadPendienteLiquidar")))
                  for f in r["raw"] if isinstance(f, dict)}
              for e, r in out.items() if r["status"] == 200}
    if len(firmas) > 1:
        base_e = next(iter(firmas))
        iguales = [e for e in firmas if e != base_e and firmas[e] == firmas[base_e]]
        distintas = [e for e in firmas if e != base_e and firmas[e] != firmas[base_e]]
        print(f"\n   vs {base_e}: idénticas → {iguales or '(ninguna)'} · "
              f"distintas → {distintas or '(ninguna)'}")
        print("   Cómo leerlo: si TODAS son idénticas, `fecha` no mueve la respuesta (o la")
        print("   cuenta no tuvo movimientos). Si H+1 responde 200 con datos, el endpoint")
        print("   acepta fecha futura — igual que la regla H1 del endpoint viejo.")
    return out


# ── 3) anatomía cruda ─────────────────────────────────────────────────────────
def anatomia(r: dict) -> None:
    print("\n── 3) ANATOMÍA DE LA RESPUESTA ───────────────────────────────────────────")
    raw = [f for f in r["raw"] if isinstance(f, dict)]
    if not raw:
        print("   (sin filas — nada que anatomizar)")
        return
    claves: dict[str, int] = {}
    for f in raw:
        for k in f:
            claves[k] = claves.get(k, 0) + 1
    n = len(raw)
    print(f"   sonda: {r['etiqueta']} · {n} filas")
    print("   CLAVES (cuántas filas la traen / cuántas la traen NO vacía):")
    for k in sorted(claves):
        no_vacias = sum(1 for f in raw if f.get(k) not in (None, "", []))
        print(f"     {k:<26} {claves[k]:>4}/{n}   con valor: {no_vacias:>4}")
    faltantes = sorted(set(claves) ^ {
        "cuenta", "fecha", "tipoTitulo", "tipoTituloAgente", "codigoISIN", "especie",
        "nombreEspecie", "simboloLocal", "lugar", "subCuenta", "estado", "cantidadLiquidada",
        "cantidadPendienteLiquidar", "precio", "precioUnitario", "monedaCotizacion",
        "fechaPrecio", "informacion", "parking"})
    if faltantes:
        print(f"   ⚠ diferencias contra la doc (falta o sobra): {faltantes}")

    for campo in ("estado", "lugar", "subCuenta", "informacion", "tipoTitulo",
                  "monedaCotizacion", "fecha", "fechaPrecio"):
        if campo in claves:
            b = _breakdown(raw, campo)
            print(f"   {campo:<18} {json.dumps(b, ensure_ascii=False)[:150]}")

    con_parking = [f for f in raw if f.get("parking")]
    print(f"\n   filas con bloque `parking` NO vacío: {len(con_parking)}/{n}")
    if con_parking:
        print(f"     ejemplo: {json.dumps(con_parking[0].get('parking'), ensure_ascii=False)[:300]}")
    print(f"\n   FILA DE EJEMPLO:\n     {json.dumps(raw[0], ensure_ascii=False)[:700]}")


# ── 4) la posición, y los NEGATIVOS ───────────────────────────────────────────
def posiciones(r: dict) -> None:
    print("\n── 4) POSICIÓN — liquidada vs pendiente de liquidar ───────────────────────")
    raw = [f for f in r["raw"] if isinstance(f, dict)]
    if not raw:
        print("   (sin filas)")
        return
    print(f"   {'especie':<16} {'símbolo':<12} {'estado':<6} {'subCta':<8} "
          f"{'LIQUIDADA':>16} {'PENDIENTE':>16} {'precio':>12} {'mon':<5}")
    for f in sorted(raw, key=lambda x: str(x.get("especie") or "")):
        print(f"   {str(f.get('especie') or '')[:16]:<16} "
              f"{str(f.get('simboloLocal') or '')[:12]:<12} "
              f"{str(f.get('estado') or '')[:6]:<6} "
              f"{str(f.get('subCuenta') or '')[:8]:<8} "
              f"{_num(f.get('cantidadLiquidada')):>16,.2f} "
              f"{_num(f.get('cantidadPendienteLiquidar')):>16,.2f} "
              f"{_num(f.get('precio')):>12,.4f} "
              f"{str(f.get('monedaCotizacion') or '')[:5]:<5}")

    print("\n   ▸ NEGATIVOS (el caso de uso: control de títulos negativos)")
    neg_liq = [f for f in raw if _num(f.get("cantidadLiquidada")) < -TOL]
    neg_pen = [f for f in raw if _num(f.get("cantidadPendienteLiquidar")) < -TOL]
    print(f"     con LIQUIDADA negativa : {len(neg_liq)}")
    for f in neg_liq[:15]:
        print(f"       {str(f.get('especie'))[:20]:<20} {str(f.get('nombreEspecie'))[:28]:<28} "
              f"{_num(f.get('cantidadLiquidada')):>16,.2f}")
    print(f"     con PENDIENTE negativa : {len(neg_pen)}")
    for f in neg_pen[:15]:
        print(f"       {str(f.get('especie'))[:20]:<20} {str(f.get('nombreEspecie'))[:28]:<28} "
              f"{_num(f.get('cantidadPendienteLiquidar')):>16,.2f}")
    print("\n     Lo que hay que mirar: si una especie está NEGATIVA en pendiente pero en")
    print("     CERO/positiva en liquidada, ese es exactamente el caso que hoy el back")
    print("     office marca a mano (la caución que todavía no venció).")


# ── 5) parking ────────────────────────────────────────────────────────────────
def parking(idc: str, fecha: date, fmt: str) -> None:
    print("\n── 5) `incluirParking` — ¿qué agrega? ─────────────────────────────────────")
    sin = _get_nuevo(idc, fecha, fmt, parking="False")
    con = _get_nuevo(idc, fecha, fmt, parking="True")
    for nombre, r in (("False", sin), ("True", con)):
        st = r["status"] if r["status"] is not None else "EXC"
        print(f"   incluirParking={nombre:<6} HTTP {st!s:<5} {r['ms']:>6}ms "
              f"{len(r['raw']):>5} filas   {(r['error'] or '')[:70]}")
    if sin["status"] != 200 or con["status"] != 200:
        return
    a = {_clave(f) for f in sin["raw"] if isinstance(f, dict)}
    b = {_clave(f) for f in con["raw"] if isinstance(f, dict)}
    nuevas = sorted(b - a)
    print(f"   filas que SOLO aparecen con parking=True: {len(nuevas)}")
    for k in nuevas[:10]:
        print(f"     {k}")
    bloques = sum(1 for f in con["raw"] if isinstance(f, dict) and f.get("parking"))
    print(f"   filas con bloque `parking` poblado (parking=True): {bloques}")
    if not nuevas and not bloques:
        print("   → no cambia nada para esta cuenta/fecha (puede no tener parking).")


# ── 6) contra `posicionValuada` (el endpoint que usamos hoy) ──────────────────
def contra_valuada(idc: str, denom: str, r_nuevo: dict, hoy: date, headers: dict,
                   amap: dict) -> None:
    """Compara el nuevo contra el viejo en sus DOS horizontes (t0 y t1).

    Los horizontes salen de la regla H1, la misma que usa `jobs/tenencia_live.py`:
      t0 (liquidada a HOY)    → desde = hoy + 1 hábil
      t1 (liquidada a MAÑANA) → desde = hoy + 2 hábiles
    Si el nuevo `cantidadLiquidada` se parece a t0 y `+ pendiente` se parece a t1,
    los dos endpoints están contando lo mismo con distinto corte — que es justo la
    hipótesis a confirmar.
    """
    print("\n── 6) CONTRA `posicionValuada` (lo que usamos hoy) ────────────────────────")
    d_t0 = proximo_habil(_ultimo_habil(hoy))
    d_t1 = proximo_habil(d_t0)
    vals = {}
    for h, d in (("t0", d_t0), ("t1", d_t1)):
        v = _get_valuada(idc, denom, d, headers)
        pos = {p["unidad"]: p for p in _parse(v["raw"], idc, denom, "1970-01-01", amap)}
        vals[h] = {"resp": v, "pos": pos, "desde": d}
        st = v["status"] if v["status"] is not None else "EXC"
        print(f"   posicionValuada {h}  desde={d}  HTTP {st!s:<5} {v['ms']:>6}ms "
              f"{len(v['raw']):>5} filas → {len(pos)} posiciones  {(v['error'] or '')[:50]}")

    raw = [f for f in r_nuevo["raw"] if isinstance(f, dict)]
    if not raw or not any(v["pos"] for v in vals.values()):
        print("   (falta un lado de la comparación — no se puede cruzar)")
        return

    # ── identidad: `especie` del nuevo ↔ `[NNN]` de la `unidad` del viejo ──────
    nuevo_por_esp: dict[str, dict] = {}
    for f in raw:
        e = str(f.get("especie") or "").strip()
        if not e:
            continue
        g = nuevo_por_esp.setdefault(e, {"liq": 0.0, "pen": 0.0, "filas": 0,
                                         "nombre": f.get("nombreEspecie"),
                                         "simbolo": f.get("simboloLocal")})
        g["liq"] += _num(f.get("cantidadLiquidada"))
        g["pen"] += _num(f.get("cantidadPendienteLiquidar"))
        g["filas"] += 1

    def _idx(unidad: str) -> str | None:
        m = _RE_CORCHETE.match(unidad or "")
        return m.group(1) if m else None

    viejo_por_esp: dict[str, dict] = {}
    for h, v in vals.items():
        for unidad, p in v["pos"].items():
            k = _idx(unidad) or unidad          # cash ('ARS','USD') no trae corchetes
            g = viejo_por_esp.setdefault(k, {"unidad": unidad, "t0": None, "t1": None,
                                             "ticker": p.get("ticker")})
            g[h] = p["cantidad"]

    matcheadas = sorted(set(nuevo_por_esp) & set(viejo_por_esp))
    solo_nuevo = sorted(set(nuevo_por_esp) - set(viejo_por_esp))
    solo_viejo = sorted(set(viejo_por_esp) - set(nuevo_por_esp))
    print(f"\n   IDENTIDAD (especie ↔ [NNN] de unidad): {len(matcheadas)} cruzan · "
          f"{len(solo_nuevo)} solo en el nuevo · {len(solo_viejo)} solo en el viejo")
    for k in solo_nuevo[:8]:
        g = nuevo_por_esp[k]
        print(f"     solo NUEVO : especie={k:<14} {str(g['nombre'])[:34]:<34} "
              f"liq={g['liq']:,.2f}")
    for k in solo_viejo[:8]:
        print(f"     solo VIEJO : {viejo_por_esp[k]['unidad'][:44]:<44} "
              f"t0={viejo_por_esp[k]['t0']}")

    # ── signo: se MIDE, no se supone ──────────────────────────────────────────
    igual = opuesto = ni = 0
    for k in matcheadas:
        liq, t0 = nuevo_por_esp[k]["liq"], viejo_por_esp[k].get("t0")
        if t0 is None:
            continue
        if abs(liq - t0) <= max(TOL, abs(t0) * 1e-6):
            igual += 1
        elif abs(liq + t0) <= max(TOL, abs(t0) * 1e-6):
            opuesto += 1
        else:
            ni += 1
    print(f"\n   SIGNO/valor de `cantidadLiquidada` vs t0 parseado: {igual} iguales · "
          f"{opuesto} opuestos · {ni} ni una cosa ni la otra")
    print("   (el parser del job hace `cantidad × -1`; si acá da 'opuestos', el nuevo")
    print("    endpoint viene con el signo YA derecho y no hay que invertirlo)")

    print(f"\n   {'especie':<14} {'símbolo/ticker':<16} {'NUEVO liq':>15} {'NUEVO pend':>14} "
          f"{'liq+pend':>15} {'VIEJO t0':>15} {'VIEJO t1':>15}")
    for k in matcheadas:
        g, v = nuevo_por_esp[k], viejo_por_esp[k]
        sym = g["simbolo"] or v.get("ticker") or ""
        t0 = v.get("t0")
        t1 = v.get("t1")
        print(f"   {k[:14]:<14} {str(sym)[:16]:<16} {g['liq']:>15,.2f} {g['pen']:>14,.2f} "
              f"{g['liq'] + g['pen']:>15,.2f} "
              f"{(f'{t0:,.2f}' if t0 is not None else '—'):>15} "
              f"{(f'{t1:,.2f}' if t1 is not None else '—'):>15}")

    # ── el corazón: negativos de un lado y no del otro ────────────────────────
    print("\n   ▸ DÓNDE DIFIEREN LOS NEGATIVOS")
    filas = []
    for k in sorted(set(nuevo_por_esp) | set(viejo_por_esp)):
        liq = nuevo_por_esp.get(k, {}).get("liq")
        t0 = viejo_por_esp.get(k, {}).get("t0")
        t1 = viejo_por_esp.get(k, {}).get("t1")
        neg_nuevo = liq is not None and liq < -TOL
        neg_viejo = (t0 is not None and t0 < -TOL) or (t1 is not None and t1 < -TOL)
        if neg_nuevo != neg_viejo:
            filas.append((k, liq, nuevo_por_esp.get(k, {}).get("pen"), t0, t1))
    if not filas:
        print("     (ninguna especie cambia de signo entre los dos endpoints)")
    for k, liq, pen, t0, t1 in filas[:25]:
        print(f"     especie={k[:16]:<16} NUEVO liq={_fmt(liq):>15} pend={_fmt(pen):>15} "
              f"| VIEJO t0={_fmt(t0):>15} t1={_fmt(t1):>15}")
    print("\n     Esta lista ES el control de títulos negativos: cada fila es un caso que")
    print("     hoy el back office tiene que interpretar a mano.")


def _fmt(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"


# ── 7) contra lo que ya tenemos persistido ────────────────────────────────────
def contra_sql(idc: str, hoy: date) -> None:
    print("\n── 7) CONTRA SQL (lo que las vistas muestran hoy) ─────────────────────────")
    from api.services._sql import _q
    for tabla, sql, params in (
        ("portafolio.tenencia (última fecha)",
         "SELECT fecha, COUNT(*) n, SUM(CASE WHEN cantidad < 0 THEN 1 ELSE 0 END) negativas "
         "FROM portafolio.tenencia WHERE id_cuenta = %(c)s "
         "AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia WHERE id_cuenta = %(c)s) "
         "GROUP BY fecha", {"c": idc}),
        ("portafolio.tenencia_live (hoy)",
         "SELECT horizonte, desde_consultado, COUNT(*) n, "
         "SUM(CASE WHEN cantidad < 0 THEN 1 ELSE 0 END) negativas, MAX(actualizado_at) fresco "
         "FROM portafolio.tenencia_live WHERE id_cuenta = %(c)s AND fecha = %(f)s "
         "GROUP BY horizonte, desde_consultado ORDER BY horizonte",
         {"c": idc, "f": hoy}),
    ):
        try:
            filas = _q(sql, params)
        except Exception as e:
            print(f"   {tabla}: no pude leer ({type(e).__name__}: {e})")
            continue
        if not filas:
            print(f"   {tabla}: sin filas para la cuenta {idc}")
            continue
        for f in filas:
            print(f"   {tabla}: " + " · ".join(f"{k}={v}" for k, v in f.items()))
    print("\n   Si `tenencia_live` tiene negativas y el endpoint nuevo no, ahí está la")
    print("   diferencia que justifica el job nuevo.")


# ── 8) veredicto ──────────────────────────────────────────────────────────────
def veredicto(fmt: str | None, sem: dict, r_nuevo: dict) -> None:
    print("\n── 8) VEREDICTO ───────────────────────────────────────────────────────────")
    if not fmt:
        print("   ✗ El endpoint no respondió con ningún formato de fecha. Sin esto no hay")
        print("     nada que decidir: primero hay que destrabar acceso/permiso.")
        return
    print(f"   ✓ formato de `fecha` aceptado: {fmt}")
    fut = sem.get("H+1 (FUTURO)")
    if fut and fut["status"] == 200 and fut["raw"]:
        print("   ✓ acepta fecha FUTURA con datos → hay que definir qué día representa")
        print("     (comparar sus totales con t0/t1 de la sección 6 antes de usarlo)")
    elif fut:
        print(f"   ✗ fecha futura: HTTP {fut['status']} · {(fut['error'] or '')[:60]}")
    raw = [f for f in r_nuevo["raw"] if isinstance(f, dict)]
    con_pend = sum(1 for f in raw if abs(_num(f.get("cantidadPendienteLiquidar"))) > TOL)
    nota = ("el corte liquidado/pendiente EXISTE y separa" if con_pend else
            "esta cuenta/fecha no tiene nada pendiente (probar otro día u otra cuenta)")
    print(f"   · filas con PENDIENTE ≠ 0: {con_pend}/{len(raw)} → {nota}")
    print("\n   Nada de esto cambió producción. El próximo paso, si el veredicto da bien,")
    print("   es un job LIVE con el mismo diseño que `jobs/tenencia_live.py`: solo el")
    print("   presente, tabla propia, sin histórico y sin tocar `portafolio.tenencia`.")


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    cuentas = tuple(c.strip() for c in
                    (_arg("--cuentas") or ",".join(CUENTAS_DEFAULT)).split(",") if c.strip())
    especie = _arg("--especie")
    dump = _arg("--dump")
    hoy = _hoy_art()

    print(f"\n{'=' * 78}")
    print(f"DIAG — endpoint NUEVO cuentas/{{id}}/posiciones   ·   {hoy.isoformat()} (ART)")
    print(f"{'=' * 78}")
    print("READ-ONLY: no escribe una sola fila. Solo GETs de lectura + SELECTs.")
    print(f"   cuentas   : {', '.join(cuentas)}")
    print(f"   hoy hábil : {es_habil(hoy)}   ·   último hábil: {_ultimo_habil(hoy)}")

    cargar_contrapartes()
    amap = _load_assets_map()
    headers = autenticar()
    df = obtener_cuentas(headers)
    denoms = {str(r["id"]): str(r["denominacion"]) for _, r in df.iterrows()}
    print(f"   assets en el mapa: {len(amap)}   ·   cuentas activas en Aunesa: {len(denoms)}")

    payload: dict = {}
    for idc in cuentas:
        denom = denoms.get(idc, "")
        print(f"\n\n{'█' * 78}\n█  CUENTA {idc}  {denom[:55]}\n{'█' * 78}")
        if not denom:
            print("   ⚠ no figura como activa en el listado de Aunesa — la sondeo igual")

        fmt_nombre, sondas = contrato(idc, hoy)
        if not fmt_nombre:
            veredicto(None, {}, {"raw": []})
            payload[idc] = {"contrato": sondas}
            continue
        fmt = dict(FORMATOS)[fmt_nombre]

        sem = semantica(idc, hoy, fmt)
        # La sonda de referencia para el resto: la del último hábil, que es el día
        # que el resto del sistema tiene cargado.
        ref = sem.get("H0 (últ. hábil)") or sem.get("HOY")
        ref = ref if ref and ref["status"] == 200 else sondas[fmt_nombre]

        anatomia(ref)
        posiciones(ref)
        parking(idc, _ultimo_habil(hoy), fmt)
        if especie:
            r = _get_nuevo(idc, _ultimo_habil(hoy), fmt, especie=especie)
            print(f"\n   filtro especie={especie}: HTTP {r['status']} · {len(r['raw'])} filas "
                  f"{(r['error'] or '')[:60]}")
        contra_valuada(idc, denom, ref, hoy, headers, amap)
        contra_sql(idc, hoy)
        veredicto(fmt_nombre, sem, ref)

        payload[idc] = {"contrato": sondas, "semantica": sem, "referencia": ref}

    print(f"\n   llamadas a Aunesa hechas por este diag: {_llamadas}")
    if dump:
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)
        print(f"   crudo completo guardado en {dump}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
