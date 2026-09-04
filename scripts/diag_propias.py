"""Diag read-only: ¿qué devuelve `consolidadosGenerales` con tiposCuenta=**Propia**?

El paso previo, obligatorio, a crear `operaciones.movimientos_propias`. Hoy
`jobs/negocio_movimientos` pide SIEMPRE `tiposCuenta="Comitente"` — la cartera
PROPIA de la casa (ej. `[1839] ACA VALORES TRADING`, la que más se mueve por
cauciones) nunca se ingiere. La idea es replicar el pipeline con el otro tipo,
pero antes hay que contestar cinco preguntas que NO se pueden suponer (REGLA #2):

  1. **¿"Propia" es siquiera un valor válido?** `jobs/tenencia_live.py` lo usa y
     su propio comentario admite que «el valor exacto para propias NO está
     confirmado contra la API». Se prueban cinco grafías.
  2. **⚠️ ¿El parámetro hace algo?** Se manda un valor BASURA a propósito. Si la
     API lo ignora y devuelve lo mismo que `Comitente`, entonces «las propias»
     serían los comitentes de siempre con otro nombre — y la tabla nueva sería
     una copia. **Sin este control, todo lo demás no significa nada.**
  3. **¿Se solapan con lo que YA está en la base?** Se cruzan los comprobantes
     contra `operaciones.negocio_movimientos` de esos mismos días. Si ya están,
     no hace falta tabla nueva: hace falta sacar un filtro.
  4. **¿El parseo actual les sirve?** Cuántas filas `informacion` NO matchean
     ninguno de los 5 patrones de `parse_informacion` (serían `categoria='otro'`,
     invisibles para todos los consumidores).
  5. **¿Qué mataría cada filtro de la ingesta?** Las propias operan cauciones y
     OTC — y `EXCLUIR_SUBSTRINGS` tira todo lo que diga "otc" en `informacion` o
     en `cuenta`. Puede que el filtro correcto para propias NO sea el mismo.

NO escribe nada: ni a Aunesa (solo GET) ni a Postgres (solo SELECT).

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_propias                     # sonda + 5 días hábiles hasta hoy
    python -m scripts.diag_propias --dias 10
    python -m scripts.diag_propias --hasta 2026-08-29
    python -m scripts.diag_propias --tipo Propia       # saltea la sonda, usa este valor
    python -m scripts.diag_propias --json /tmp/propias.json   # vuelca 30 filas crudas
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from api.services._negocio_informacion_filter import (
    EXCLUIR_INFORMACION_CONTAINS,
)
from api.services._negocio_informacion_filter import (
    es_excluido as _es_info_excluida,
)
from api.services.aunesa_negocio import (
    EXCLUIR_SUBSTRINGS,
    _enriquecer,
    _normalizar,
    agrupar_boletos,
    parse_informacion,
)
from core import aunesa
from core.calendario import ultimos_habiles
from core.postgres import get_pool

ENDPOINT = "operaciones/consolidadosGenerales"

# Grafías candidatas para la cartera propia. `Comitente` va primero como BASELINE
# (sabemos que funciona: es lo que pide el job todos los días) y el ÚLTIMO es el
# CONTROL: un valor que no existe. Si el control devuelve filas, la API ignora el
# parámetro y ninguna de las otras respuestas prueba nada.
BASELINE = "Comitente"
CANDIDATAS = ("Propia", "Propias", "Propietaria", "Cartera Propia")
CONTROL_BASURA = "ZZZNoExisteEsteTipo"

_RE_ID_CUENTA = re.compile(r"^\[(\d+)\]")


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _pedir(tipo: str, d: date, timeout: int = 180) -> tuple[int, list[dict], str]:
    """(status, filas, nota). Nunca levanta: un fallo se reporta como nota."""
    dia = d.strftime("%d/%m/%Y")
    try:
        resp = aunesa.get(ENDPOINT, {"tiposCuenta": tipo, "concertacionDesde": dia,
                                     "concertacionHasta": dia}, timeout=timeout)
    except Exception as e:
        return 0, [], f"{type(e).__name__}: {e}"
    if resp.status_code != 200:
        return resp.status_code, [], (resp.text or "")[:160].replace("\n", " ")
    cuerpo = (resp.text or "").strip()
    if not cuerpo:
        return 200, [], "body vacío (día no hábil o sin movimientos)"
    try:
        data = resp.json()
    except ValueError as e:
        return 200, [], f"no-JSON ({len(cuerpo)} bytes): {e}"
    if not isinstance(data, list):
        return 200, [], f"shape inesperada: {type(data).__name__}"
    return 200, [r for r in data if isinstance(r, dict)], ""


def _comps(filas: list[dict]) -> set[str]:
    return {str(f.get("comprobante")) for f in filas if f.get("comprobante")}


# ── PASO 1: ¿el parámetro discrimina, y con qué grafía? ──────────────────────
def sondar(d: date) -> tuple[str | None, dict[str, set[str]]]:
    print(f"\n{'='*74}\nPASO 1 — ¿`tiposCuenta` hace algo, y cómo se escribe la propia?"
          f"\n         fecha de sonda: {d} · {2 + len(CANDIDATAS)} llamadas\n{'='*74}")
    sets: dict[str, set[str]] = {}
    for tipo in (BASELINE, *CANDIDATAS, CONTROL_BASURA):
        st, filas, nota = _pedir(tipo, d, timeout=120)
        sets[tipo] = _comps(filas)
        etiqueta = ("BASELINE" if tipo == BASELINE
                    else "CONTROL " if tipo == CONTROL_BASURA else "        ")
        detalle = f"HTTP {st:<3}  {len(filas):>6} filas  {len(sets[tipo]):>5} comprobantes"
        print(f"  {etiqueta} {tipo:<20} {detalle}  {nota}")
        time.sleep(1.5)

    base, control = sets[BASELINE], sets[CONTROL_BASURA]
    print()
    if not base:
        print("  ⚠️  El BASELINE vino vacío → la sonda no puede concluir nada. "
              "Probá otra fecha con --hasta (un día hábil con movimiento).")
        return None, sets
    if control and control == base:
        print("  🚨 EL PARÁMETRO SE IGNORA: un valor inexistente devolvió EXACTAMENTE lo "
              "mismo\n     que `Comitente`. Todo lo que devuelvan las grafías de abajo son "
              "los MISMOS\n     movimientos de siempre. NO crear la tabla: primero hay que "
              "encontrar cómo\n     filtra de verdad la API (¿otro parámetro? ¿por cuenta?).")
        return None, sets
    if control:
        print(f"  ⚠️  El control devolvió {len(control)} comprobantes (≠ baseline). El "
              "parámetro filtra\n     algo, pero no rechaza lo desconocido — leé las "
              "grafías con cuidado.")
    else:
        print("  ✅ El control (valor inexistente) devolvió 0 filas → el parámetro "
              "DISCRIMINA de verdad.")

    ganadora = None
    for tipo in CANDIDATAS:
        s = sets[tipo]
        if not s:
            continue
        solapa = len(s & base)
        print(f"  · {tipo:<16} {len(s):>5} comprobantes · {solapa:>5} también están en "
              f"`Comitente` ({solapa*100//max(len(s),1)}%)")
        if ganadora is None:
            ganadora = tipo
    if ganadora is None:
        print("  ❌ Ninguna grafía devolvió filas. O ese día no operó la propia, o la API "
              "usa\n     otro nombre. Probá otra fecha antes de descartar.")
    else:
        print(f"\n  → grafía elegida para el barrido: **{ganadora}**")
    return ganadora, sets


# ── PASO 2: barrido de N días ────────────────────────────────────────────────
def barrer(tipo: str, dias: list[date]) -> list[dict]:
    print(f"\n{'='*74}\nPASO 2 — barrido con tiposCuenta={tipo} · {len(dias)} días hábiles"
          f"\n{'='*74}")
    print(f"  {'fecha':<12}{'raw':>6}{'excl':>6}{'quedan':>8}{'boletos':>9}"
          f"{'cuentas':>9}{'s/comp':>8}{'s/parse':>9}   nota")
    out: list[dict] = []
    for i, d in enumerate(dias):
        st, filas, nota = _pedir(tipo, d)
        movs = [_enriquecer(f) for f in filas]
        # Las dos capas de exclusión de la ingesta, medidas por separado.
        n_info = sum(1 for f in filas if _es_info_excluida(f.get("informacion")))
        n_subs = sum(1 for f in filas if _pega_substring(f))
        excl = sum(1 for f in filas if _es_info_excluida(f.get("informacion")) or _pega_substring(f))
        boletos = agrupar_boletos([m for m, f in zip(movs, filas, strict=True)
                                   if not (_es_info_excluida(f.get("informacion"))
                                           or _pega_substring(f))])
        sin_comp = sum(1 for b in boletos if not b.get("comprobante"))
        sin_parse = sum(1 for f in filas if not parse_informacion(f.get("informacion") or ""))
        ctas = {_RE_ID_CUENTA.match(f.get("cuenta") or "").group(1)
                for f in filas if _RE_ID_CUENTA.match(f.get("cuenta") or "")}
        print(f"  {d.isoformat():<12}{len(filas):>6}{excl:>6}{len(filas)-excl:>8}"
              f"{len(boletos):>9}{len(ctas):>9}{sin_comp:>8}{sin_parse:>9}   "
              f"{nota if st == 200 else f'HTTP {st} {nota}'}")
        out.append({"fecha": d, "status": st, "filas": filas, "movs": movs,
                    "boletos": boletos, "n_info": n_info, "n_subs": n_subs})
        if i < len(dias) - 1:
            time.sleep(2)
    return out


def _pega_substring(f: dict) -> bool:
    """La capa 3 de la ingesta (`otc`/`usdl`/`integracion de garantias` en
    `informacion` O en `cuenta`), con su excepción de Diferencias diarias."""
    info = _normalizar(f.get("informacion") or "")
    if info.startswith("diferencias diarias"):
        return False
    cta = _normalizar(f.get("cuenta") or "")
    return any(s in info or s in cta for s in EXCLUIR_SUBSTRINGS)


# ── PASO 3: ¿ya están en la base? ────────────────────────────────────────────
def cruzar_con_sql(res: list[dict]) -> None:
    print(f"\n{'='*74}\nPASO 3 — ¿esos comprobantes YA están en operaciones.negocio_movimientos?"
          f"\n{'='*74}")
    fechas = [r["fecha"] for r in res]
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT fecha, comprobante, anulado_en IS NOT NULL FROM "
                        "negocio_movimientos WHERE fecha = ANY(%s)", (fechas,))
            en_base: dict[date, dict[str, bool]] = {}
            for f, c, anul in cur.fetchall():
                en_base.setdefault(f, {})[str(c)] = bool(anul)
    except Exception as e:
        print(f"  ⚠️  no pude leer Postgres ({type(e).__name__}: {e}) — sin este cruce "
              "NO se puede\n     decidir si la tabla nueva aporta algo. Corré el diag de "
              "nuevo con la DB arriba.")
        return
    tot_prop = tot_ya = tot_anul = 0
    for r in res:
        comps = _comps(r["filas"])
        base = en_base.get(r["fecha"], {})
        ya = comps & set(base)
        anul = {c for c in ya if base[c]}
        tot_prop += len(comps); tot_ya += len(ya); tot_anul += len(anul)
        print(f"  {r['fecha']}  propias {len(comps):>5}  ya en base {len(ya):>5} "
              f"(de esos, anulados {len(anul):>4})  NUEVOS {len(comps)-len(ya):>5}")
    print()
    if tot_prop == 0:
        print("  (nada que cruzar)")
    elif tot_ya == 0:
        print("  ✅ CERO solape: son movimientos que hoy NO existen en la base. La tabla "
              "nueva\n     aporta información que no tenemos.")
    elif tot_ya == tot_prop:
        print("  🚨 SOLAPE TOTAL: todos estos comprobantes YA están ingeridos. No hace "
              "falta\n     tabla nueva — lo que hay que revisar es por qué se ven como "
              "'propias'.")
    else:
        print(f"  ⚠️  SOLAPE PARCIAL: {tot_ya}/{tot_prop} ({tot_ya*100//tot_prop}%) ya "
              "están.\n     Ojo: una tabla nueva DUPLICARÍA esa parte. Hay que decidir "
              "quién es el dueño\n     de cada comprobante ANTES de escribir (REGLA #9: "
              "dos copias necesitan árbitro).")
    if tot_anul:
        print(f"  ⚠️  {tot_anul} de los que están en base figuran ANULADOS. Si Aunesa los "
              "devuelve\n     por `Propia` y no por `Comitente`, la reconciliación horaria "
              "los está matando.")


# ── PASO 4: qué hay adentro ──────────────────────────────────────────────────
def perfilar(res: list[dict], top: int = 15) -> None:
    print(f"\n{'='*74}\nPASO 4 — qué hay adentro (las {sum(len(r['filas']) for r in res)} "
          f"filas de los {len(res)} días)\n{'='*74}")
    filas = [f for r in res for f in r["filas"]]
    movs = [m for r in res for m in r["movs"]]
    if not filas:
        print("  (sin filas)")
        return

    ctas: Counter[str] = Counter()
    for f in filas:
        ctas[str(f.get("cuenta") or "(sin cuenta)")] += 1
    print(f"\n  CUENTAS ({len(ctas)}):")
    for c, n in ctas.most_common(top):
        print(f"    {n:>6}  {c[:64]}")

    print("\n  CATEGORÍAS (con el categorizador actual):")
    for c, n in Counter(m.get("_categoria") or "otro" for m in movs).most_common():
        print(f"    {n:>6}  {c}")

    print(f"\n  TEXTOS `informacion` más frecuentes (top {top}) · ✗ = no matchea "
          "ningún patrón:")
    for t, n in Counter(str(f.get("informacion") or "(vacío)")
                        for f in filas).most_common(top):
        marca = " " if parse_informacion(t) else "✗"
        print(f"    {n:>6} {marca} {t[:76]}")

    print("\n  UNIDADES / monedas:")
    for u, n in Counter(str(f.get("unidad") or "(null)")[:28] for f in filas).most_common(top):
        print(f"    {n:>6}  {u}")

    for campo in ("estado", "lugar", "uso"):
        vals = Counter(str(f.get(campo) or "(null)") for f in filas)
        print(f"\n  {campo.upper()}: " + " · ".join(f"{v}={n}" for v, n in vals.most_common(8)))

    claves: Counter[str] = Counter()
    for f in filas:
        claves.update(f.keys())
    print(f"\n  CLAVES del JSON ({len(claves)}), y en cuántas filas aparecen:")
    print("    " + " · ".join(f"{k}({n})" for k, n in claves.most_common()))


# ── PASO 5: qué mataría cada filtro ──────────────────────────────────────────
def medir_filtros(res: list[dict]) -> None:
    print(f"\n{'='*74}\nPASO 5 — cuánto tiraría cada filtro de la ingesta actual"
          f"\n{'='*74}")
    filas = [f for r in res for f in r["filas"]]
    if not filas:
        print("  (sin filas)")
        return
    tot = len(filas)
    print(f"\n  Capa 2 — `informacion` contiene (case-sensitive):  "
          f"{sum(r['n_info'] for r in res)} de {tot}")
    for s in EXCLUIR_INFORMACION_CONTAINS:
        n = sum(1 for f in filas if s in (f.get("informacion") or ""))
        if n:
            print(f"    {n:>6}  {s!r}")

    print(f"\n  Capa 3 — substring normalizado en `informacion` O `cuenta`:  "
          f"{sum(r['n_subs'] for r in res)} de {tot}")
    for s in EXCLUIR_SUBSTRINGS:
        en_info = sum(1 for f in filas if s in _normalizar(f.get("informacion") or ""))
        en_cta = sum(1 for f in filas if s in _normalizar(f.get("cuenta") or ""))
        if en_info or en_cta:
            print(f"    {en_info:>6} en informacion · {en_cta:>6} en cuenta   {s!r}")
    dif = sum(1 for f in filas
              if _normalizar(f.get("informacion") or "").startswith("diferencias diarias"))
    print(f"    (excepción 'Diferencias diarias' que se SALVAN del filtro: {dif})")

    boletos = [b for r in res for b in r["boletos"]]
    sin_comp = sum(1 for b in boletos if not b.get("comprobante"))
    print(f"\n  Capa 6 — boletos consolidados SIN comprobante (no persistibles): "
          f"{sin_comp} de {len(boletos)}")
    print("\n  ⚠️  Si el grueso de las propias muere en la capa 3 por 'otc', el filtro de "
          "la\n     ingesta de comitentes NO sirve tal cual para esta tabla — es una "
          "decisión\n     de negocio, no un detalle de implementación.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dias", type=int, default=5, help="cuántos días hábiles (default 5)")
    p.add_argument("--hasta", help="YYYY-MM-DD; último día del barrido (default hoy ART)")
    p.add_argument("--tipo", help="saltea la sonda y usa este valor de tiposCuenta")
    p.add_argument("--json", dest="volcado", help="ruta donde volcar 30 filas crudas")
    p.add_argument("--top", type=int, default=15, help="cuántos valores por ranking")
    args = p.parse_args()

    try:
        hasta = (datetime.strptime(args.hasta, "%Y-%m-%d").date() if args.hasta
                 else _hoy_art())
    except ValueError:
        print(f"--hasta mal formada: {args.hasta}")
        return 1
    dias = ultimos_habiles(hasta, max(args.dias - 1, 0))

    print(f"DIAG PROPIAS · endpoint {ENDPOINT}")
    print(f"ventana: {dias[0]} .. {dias[-1]}  ({len(dias)} días hábiles, calendario AR)")

    tipo = args.tipo
    if tipo:
        print(f"\n(sonda salteada por --tipo: se usa {tipo!r})")
    else:
        tipo, _ = sondar(dias[-1])
        if not tipo:
            print("\n→ Sin grafía válida, el barrido no tiene sentido. Nada más que medir.")
            return 2

    res = barrer(tipo, dias)
    cruzar_con_sql(res)
    perfilar(res, top=args.top)
    medir_filtros(res)

    if args.volcado:
        muestra: list[dict[str, Any]] = [f for r in res for f in r["filas"]][:30]
        with open(args.volcado, "w", encoding="utf-8") as fh:
            json.dump(muestra, fh, ensure_ascii=False, indent=2, default=str)
        print(f"\n→ {len(muestra)} filas crudas volcadas en {args.volcado}")

    print(f"\n{'='*74}\nLISTO. Pegá esta salida en el chat y decidimos el modelo de "
          f"`operaciones.movimientos_propias`\ncon datos: si vale una tabla aparte, qué "
          f"filtros lleva y quién manda sobre los\ncomprobantes que se solapen.\n{'='*74}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
