"""scripts/diag_convertir_flujos.py — convierte los flujos EN MEMORIA y remide la TEA. READ-ONLY.

Cierra el hilo que abrieron `diag_motor_ejes` → `diag_flujos_shape` →
`diag_tea_dos_ramas`. Ese último midió que **14 bonos pierden la TEA** al cambiar
de rama, y en las DOS direcciones (`on`→`soberanos` y `soberanos`→`on`). Que
pierdan en los dos sentidos es la prueba de que el problema NO es la matemática:
las dos ramas hacen hard dólar. **El problema es el FORMATO de los flujos.**

Cada bono tiene los flujos guardados en el formato de la rama para la que se
cargó, y la otra rama busca claves que no existen. Eso es un problema de DATOS, y
los datos se convierten. Mirando cómo cada rama SUMA un flujo (con VN = 100):

    monto_flujo(f)                = amortizacion     + interes                 ← rama `on`
    monto_flujo_soberano(f, 100)  = amortizacion_pct + cupon_sobre_residual    ← rama `soberanos`
    monto_flujo_cer(f, 100)       = amortizacion_pct + cupon_sobre_residual × residual_previo_pct/100

O sea que entre `on` y `soberanos` la conversión es un **RENOMBRE PURO**. Contra
`cer` no: ahí el cupón SÍ se multiplica por el residual vivo, así que hay que
dividirlo. Esa asimetría es justo el tipo de detalle que no se puede suponer —
por eso este script no propone la conversión, la EJECUTA y remide.

**Qué hace:** por cada bono que cambiaría de rama, convierte sus flujos al
formato que espera la rama nueva (en memoria, sin tocar la base), vuelve a llamar
a `calcular_campos` y compara la TEA contra la de hoy.

  · **IDÉNTICA** → la conversión es correcta Y la migración es gratis. Es el
    resultado que se busca: el bono conserva su TEA exacta.
  · **DIFIERE**  → la conversión corre pero da otro número. El Δ en bps dice
    cuánto; hay que mirar ese bono a mano antes de convertirlo de verdad.
  · **SIGUE SIN TEA** → convertir no alcanza: a ese bono le falta otra cosa.

Si sale IDÉNTICA para los BOPREAL y los hard dólar, este mismo código de
conversión se convierte en el backfill (scopeado y idempotente, REGLA #4) y el
motor se puede migrar sin perder una sola tasa.

READ-ONLY. No escribe, no borra, no toca los motores.

Uso:
    python -m scripts.diag_convertir_flujos
"""
from __future__ import annotations

from datetime import datetime

from core import curvas_sql
from core.postgres import get_pool

_SEP = "=" * 104

_CURVA_DE_RAMA = {
    "soberanos": "soberanos", "cer": "cer", "tasa_fija": "tasa_fija",
    "dolar_linked": "dolar_linked", "on": "on", "solo_duration": "tamar",
}


def _rama_por_ejes(d: dict) -> str:
    emisor, moneda, ajuste = d.get("emisor_tipo"), d.get("moneda_eje"), d.get("ajuste")
    if not (emisor and moneda and ajuste):
        return "sin_ejes"
    if emisor == "corporativo":
        return "on"
    if ajuste == "cer":
        return "cer"
    if ajuste == "dolar_linked":
        return "dolar_linked"
    if ajuste == "fija":
        return "soberanos" if moneda in ("USD", "EUR") else "tasa_fija"
    return "solo_duration"


def _rama_hoy(curva: str | None) -> str:
    c = (curva or "").strip()
    if c in ("tasa_fija", "cer", "soberanos", "dolar_linked"):
        return c
    if c == "on" or c.startswith("on_"):
        return "on"
    return "solo_duration"


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _residuales(flujos: list[dict]) -> list[float]:
    """`residual_previo_pct` de cada flujo: el nominal VIVO justo antes de ese pago.

    Arranca en 100 y va bajando con cada amortización. Se DERIVA acumulando en vez
    de leerse, porque el formato absoluto no lo trae — y la rama CER lo necesita
    para el cupón, la de soberanos para la paridad.
    """
    out, vivo = [], 100.0
    for f in flujos:
        out.append(vivo)
        vivo -= _f(f.get("amortizacion", f.get("amortizacion_pct", 0)))
    return out


# Las conversiones se derivan INVIRTIENDO la fórmula con que cada rama SUMA un
# flujo (engines/curvas.py:95-128). No se copian de ningún lado: se despejan.
#
#   monto_flujo(f)                = amortizacion + interes
#   monto_flujo_soberano(f, vn)   = amortizacion_pct/100·vn + cupon_sobre_residual/100·vn
#   monto_flujo_cer(f, vn)        = amortizacion_pct/100·vn + cupon_sobre_residual·residual/100·vn
#
# ⚠️ La asimetría que casi se me pasa: la rama SOBERANOS divide el cupón por 100 y
# la rama CER **no** — ahí `cupon_sobre_residual` es una TASA que se multiplica por
# el residual vivo. Con la fórmula equivocada un cupón de 2 daba 200. Lo cazó el
# chequeo unitario contra las funciones reales del motor, no la lectura del código.
def _a_porcentual(flujos: list[dict], vn: float, *, para_cer: bool) -> list[dict]:
    """ABSOLUTO → PORCENTUAL (lo que esperan las ramas `soberanos` y `cer`)."""
    res = _residuales(flujos)
    out = []
    for f, residual in zip(flujos, res, strict=False):
        amort, interes = _f(f.get("amortizacion")), _f(f.get("interes"))
        if para_cer:
            # despejar de: c · residual/100 · vn = interes
            cupon = (interes * 100 / (residual * vn)) if (residual > 0 and vn > 0) else 0.0
        else:
            # despejar de: c/100 · vn = interes
            cupon = (interes * 100 / vn) if vn > 0 else 0.0
        nuevo = {k: v for k, v in f.items() if k not in ("amortizacion", "interes")}
        nuevo["amortizacion_pct"] = (amort * 100 / vn) if vn > 0 else 0.0
        nuevo["cupon_sobre_residual"] = cupon
        nuevo.setdefault("residual_previo_pct", round(residual, 6))
        out.append(nuevo)
    return out


def _a_absoluto(flujos: list[dict], vn: float) -> list[dict]:
    """PORCENTUAL (formato soberanos) → ABSOLUTO (lo que espera la rama `on`)."""
    out = []
    for f in flujos:
        nuevo = {k: v for k, v in f.items()
                 if k not in ("amortizacion_pct", "cupon_sobre_residual")}
        nuevo["amortizacion"] = _f(f.get("amortizacion_pct")) / 100 * vn
        nuevo["interes"] = _f(f.get("cupon_sobre_residual")) / 100 * vn
        out.append(nuevo)
    return out


def _convertir(flujos: list[dict], rama_destino: str,
               vn: float = 100.0) -> tuple[list[dict], str]:
    """Los flujos en el formato que espera `rama_destino`, + qué se hizo."""
    if not flujos:
        return flujos, "sin flujos"
    claves: set[str] = set()
    for f in flujos:
        claves |= set(f.keys())
    es_abs = bool(claves & {"amortizacion", "interes"})
    es_pct = bool(claves & {"amortizacion_pct", "cupon_sobre_residual"})

    if rama_destino in ("soberanos", "cer"):
        if es_pct:
            return flujos, "ya estaba"
        if es_abs:
            return _a_porcentual(flujos, vn, para_cer=rama_destino == "cer"), \
                ("abs→pct (tasa/residual)" if rama_destino == "cer" else "abs→pct")
    if rama_destino in ("on", "tasa_fija"):
        if es_abs:
            return flujos, "ya estaba"
        if es_pct:
            return _a_absoluto(flujos, vn), "pct→abs"
    return flujos, "sin conversión"


def _pct(x) -> str:
    return f"{float(x) * 100:.2f}%" if x is not None else "--"


def _autochequeo() -> list[str]:
    """La conversión tiene que dar el MISMO monto que la fórmula original, en las
    tres direcciones. Se verifica contra las funciones REALES del motor, no contra
    números escritos a mano — si alguien cambia `monto_flujo_cer`, esto lo canta.

    Existe porque la primera versión de la conversión a CER estaba MAL (un cupón
    de 2 daba 200: la rama CER no divide por 100 y la de soberanos sí) y el error
    no se veía leyendo el código.
    """
    from engines.curvas import monto_flujo, monto_flujo_cer, monto_flujo_soberano

    abs_ = [{"fecha": "2027-01-01", "amortizacion": 50.0, "interes": 2.0},
            {"fecha": "2028-01-01", "amortizacion": 50.0, "interes": 1.0}]
    pct_ = [{"fecha": "2027-01-01", "amortizacion_pct": 50.0, "cupon_sobre_residual": 2.0},
            {"fecha": "2028-01-01", "amortizacion_pct": 50.0, "cupon_sobre_residual": 1.0}]
    fallos = []
    for destino, orig, fn_o, fn_d in (
        ("soberanos", abs_, monto_flujo, monto_flujo_soberano),
        ("cer", abs_, monto_flujo, monto_flujo_cer),
        ("on", pct_, monto_flujo_soberano, monto_flujo),
    ):
        conv, _ = _convertir(orig, destino, 100.0)
        for a, b in zip(orig, conv, strict=False):
            va = fn_o(a, 100) if fn_o is not monto_flujo else fn_o(a)
            vb = fn_d(b, 100) if fn_d is not monto_flujo else fn_d(b)
            if abs(va - vb) > 1e-9:
                fallos.append(f"{destino}: {va} ≠ {vb}")
    return fallos


def main() -> None:
    print(_SEP)
    print("CONVERTIR LOS FLUJOS y remedir — ¿se salva la TEA?")
    print(_SEP)

    fallos = _autochequeo()
    if fallos:
        print("\n  ❌ AUTOCHEQUEO FALLIDO — la conversión NO reproduce el monto original:")
        for f in fallos:
            print(f"     {f}")
        print("  No mirar los números de abajo: la conversión está mal.\n")
    else:
        print("\n  ✅ autochequeo: la conversión reproduce el monto EXACTO en las 3 direcciones")

    from engines.curvas import (
        calcular_campos,
        cargar_a3500_actual,
        cargar_cer,
        cargar_dias_habiles,
        cargar_mep_actual,
    )

    docs = curvas_sql.cargar_todos()
    cer_dict, dias_habiles = cargar_cer(), cargar_dias_habiles()
    mep, a3500 = cargar_mep_actual(), cargar_a3500_actual()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, last_price, updated_at FROM mercado.market_snapshot "
                    "WHERE last_price IS NOT NULL AND last_price > 0")
        snap = {r[0]: {"price": float(r[1]), "ts": r[2]} for r in cur.fetchall()}
    print(f"\n  MEP {mep} · A3500 {a3500} · {len(snap)} con precio\n")

    def _corre(fake: dict, instr: dict) -> dict:
        try:
            return calcular_campos(fake, instr, cer_dict, dias_habiles, mep, a3500) or {}
        except Exception as e:
            return {"_error": type(e).__name__ + ": " + str(e)[:50]}

    filas = []
    for d in docs:
        # ⚠️ blob invertido: `ticker` = símbolo de mercado, `ticker_corto` = código.
        simbolo, codigo = d.get("ticker"), d.get("ticker_corto")
        rama_h, rama_n = _rama_hoy(d.get("curva")), _rama_por_ejes(d)
        if rama_h == rama_n or rama_n in ("sin_ejes", "solo_duration"):
            continue                       # esos no se arreglan convirtiendo flujos
        m = snap.get(simbolo or "")
        if not m:
            continue
        fake = {"ticker": simbolo, "price": m["price"],
                "ts": m["ts"], "timestamp": m["ts"] or datetime.utcnow()}

        tea_hoy = _corre(fake, d).get("TEA")
        curva_nueva = _CURVA_DE_RAMA.get(rama_n, d.get("curva"))
        # SIN convertir (lo que midió el diag anterior)
        tea_sin = _corre(fake, {**d, "curva": curva_nueva}).get("TEA")
        # CON los flujos convertidos al formato de la rama nueva
        flujos_conv, como = _convertir(d.get("flujos") or [], rama_n,
                                       _f(d.get("valor_nominal"), 100.0) or 100.0)
        r_conv = _corre(fake, {**d, "curva": curva_nueva, "flujos": flujos_conv})
        tea_con = r_conv.get("TEA")

        if tea_hoy is None and tea_con is None:
            estado, delta = "sin TEA (ni antes ni después)", None
        elif tea_hoy is not None and tea_con is None:
            estado, delta = "SIGUE SIN TEA", None
        elif tea_hoy is None and tea_con is not None:
            estado, delta = "GANA TEA", None
        elif abs(float(tea_hoy) - float(tea_con)) < 1e-9:
            estado, delta = "IDÉNTICA", 0.0
        else:
            estado = "DIFIERE"
            delta = (float(tea_con) - float(tea_hoy)) * 10000

        filas.append({
            "codigo": codigo, "hoy": rama_h, "nueva": rama_n, "como": como,
            "tea_hoy": tea_hoy, "tea_sin": tea_sin, "tea_con": tea_con,
            "estado": estado, "delta": delta, "emisor_tipo": d.get("emisor_tipo"),
            "err": r_conv.get("_error"),
        })

    print(_SEP)
    print("  RESUMEN")
    print(_SEP)
    for e in ("IDÉNTICA", "DIFIERE", "GANA TEA", "SIGUE SIN TEA",
              "sin TEA (ni antes ni después)"):
        n = [f for f in filas if f["estado"] == e]
        if n:
            print(f"  {e:<32}{len(n):>3}   {', '.join(str(x['codigo']) for x in n)}")

    print(f"\n{_SEP}\n  DETALLE — SOBERANO y BCRA primero\n{_SEP}")
    print(f"  {'TICKER':<9}{'HOY→NUEVA':<22}{'CONVERSIÓN':<22}"
          f"{'TEA HOY':>9}{'SIN CONV':>10}{'CON CONV':>10}{'Δ bps':>8}   ESTADO")
    print("  " + "-" * 102)

    def _orden(f: dict) -> tuple:
        pri = 0 if f["emisor_tipo"] in ("soberano", "bcra") else 1
        rank = {"IDÉNTICA": 0, "GANA TEA": 1, "DIFIERE": 2, "SIGUE SIN TEA": 3}
        return (pri, rank.get(f["estado"], 9), str(f["codigo"]))

    pri_ant = None
    for f in sorted(filas, key=_orden):
        pri = "SOBERANO / BCRA" if f["emisor_tipo"] in ("soberano", "bcra") else "el resto"
        if pri != pri_ant:
            print(f"\n  ▸ {pri}")
            pri_ant = pri
        dl = f"{f['delta']:+.0f}" if f["delta"] is not None else "--"
        marca = "  " if f["estado"] in ("IDÉNTICA", "GANA TEA") else "⚠ "
        print(f"  {marca}{str(f['codigo'])[:8]:<9}{f['hoy'] + '→' + f['nueva']:<22}"
              f"{f['como']:<22}{_pct(f['tea_hoy']):>9}{_pct(f['tea_sin']):>10}"
              f"{_pct(f['tea_con']):>10}{dl:>8}   {f['estado']}")
        if f["err"]:
            print(f"      error: {f['err']}")

    ok = [f for f in filas if f["estado"] in ("IDÉNTICA", "GANA TEA")]
    print(f"\n{_SEP}\n  QUÉ SIGNIFICA\n{_SEP}")
    print(f"  {len(ok)} de {len(filas)} bonos conservan (o ganan) su TEA si se convierten")
    print("  los flujos ANTES de migrar el motor.")
    print("\n  Si los BOPREAL están en IDÉNTICA, esta misma conversión se vuelve un")
    print("  backfill scopeado e idempotente y el motor se migra sin perder una tasa.")
    print("  Los DIFIERE hay que mirarlos de a uno: la conversión corre pero el número")
    print("  cambia, y ahí no alcanza con medir — hay que decidir cuál es el correcto.")
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
