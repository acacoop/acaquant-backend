"""scripts/diag_tea_dos_ramas.py — CALCULA la TEA con las DOS ramas y compara. READ-ONLY.

Idea del user, y es mejor que razonar sobre el código: en vez de deducir si la
rama nueva "sabría leer" los flujos de un bono, **se corre la cuenta las dos veces
sobre el MISMO bono con el MISMO precio y se comparan los números.**

Lo que hace, bono por bono:

    campos_hoy   = calcular_campos(precio, bono tal cual está)          ← rama por `curva`
    campos_nuevo = calcular_campos(precio, bono con la curva que dicen los EJES)

Es la MISMA función del motor (`engines/curvas.py::calcular_campos`), no una
reimplementación — así el resultado no puede diferir de lo que la mesa vería en
pantalla. El único cambio entre las dos llamadas es el campo `curva` del doc, que
es justamente lo que decide la rama.

Por qué esto reemplaza al veredicto de `diag_flujos_shape`: ese decía "MIGRAR
flujos" leyendo qué claves espera cada rama. Es una HIPÓTESIS. Acá se ve el
número: si la TEA da igual, la hipótesis era pesimista y el cambio es gratis; si
da distinta o desaparece, está medido cuánto y en cuál.

Cinco desenlaces posibles por bono:

  · **IDÉNTICA**   — la TEA no se mueve. Migrar ese bono es gratis.
  · **DIFIERE**    — las dos calculan pero dan distinto. Sale el delta en bps.
  · **PIERDE TEA** — hoy calcula, con la rama nueva no. Es la regresión a evitar.
  · **GANA TEA**   — hoy no calcula y con la nueva sí. Suele ser una corrección.
  · **NINGUNA**    — no calcula ni antes ni después (sin precio, sin flujos).

El precio sale de `mercado.market_snapshot` (`last_price` + `updated_at`), armado
igual que el `fake_doc` del loop real del motor (engines/curvas.py:830). Un bono
sin precio no se puede evaluar y sale aparte — no es un error, es que no operó.

READ-ONLY. No escribe, no borra, no toca los motores. Se puede correr con mercado
abierto: solo lee el snapshot.

Uso:
    python -m scripts.diag_tea_dos_ramas
    python -m scripts.diag_tea_dos_ramas --todos     # incluye los que no cambian de rama
"""
from __future__ import annotations

import sys
from datetime import datetime

from core import curvas_sql
from core.postgres import get_pool

_SEP = "=" * 104

# La rama de cálculo → el valor de `curva` que la activa en el motor.
# `solo_duration` usa 'tamar' porque cae en el `else` final, igual que hoy.
_CURVA_DE_RAMA = {
    "soberanos": "soberanos", "cer": "cer", "tasa_fija": "tasa_fija",
    "dolar_linked": "dolar_linked", "on": "on", "solo_duration": "tamar",
}


def _rama_por_ejes(d: dict) -> str:
    """Misma propuesta que `diag_motor_ejes` — corporativo se pregunta PRIMERO."""
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


def _pct(x) -> str:
    return f"{float(x) * 100:.2f}%" if x is not None else "--"


def main() -> None:
    todos = "--todos" in sys.argv
    print(_SEP)
    print("TEA con las DOS ramas — ¿da lo mismo o queda distinto?")
    print(_SEP)

    from engines.curvas import (
        calcular_campos,
        cargar_a3500_actual,
        cargar_cer,
        cargar_dias_habiles,
        cargar_mep_actual,
    )

    docs = curvas_sql.cargar_todos()          # blob + columnas de ejes mergeadas
    cer_dict = cargar_cer()
    dias_habiles = cargar_dias_habiles()
    mep = cargar_mep_actual()
    a3500 = cargar_a3500_actual()
    print(f"\n  insumos: CER {len(cer_dict)} fechas · {len(dias_habiles)} días hábiles"
          f" · MEP {mep} · A3500 {a3500}")
    if not mep:
        print("  ⚠️  sin MEP: los hard dólar en pesos no van a poder calcular (en las DOS ramas)")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, last_price, updated_at FROM mercado.market_snapshot "
                    "WHERE last_price IS NOT NULL AND last_price > 0")
        snap = {r[0]: {"price": float(r[1]), "ts": r[2]} for r in cur.fetchall()}
    print(f"  {len(snap)} instrumentos con precio en el snapshot\n")

    def _corre(fake: dict, instr: dict) -> dict | None:
        """Una corrida del motor. Si revienta, el error viaja en el resultado en
        vez de cortar el diag: un bono con el dato roto no puede tapar a los 220
        que sí se pueden medir."""
        try:
            return calcular_campos(fake, instr, cer_dict, dias_habiles, mep, a3500)
        except Exception as e:
            return {"_error": type(e).__name__ + ": " + str(e)[:60]}

    resultados, sin_precio = [], []
    for d in docs:
        # ⚠️ TRAMPA DEL BLOB: acá `ticker` es el SÍMBOLO de mercado (lo que indexa
        # el snapshot) y `ticker_corto` es el código del bono. Están invertidos
        # respecto de las columnas de la tabla — ya se cobró tres bugs.
        simbolo, codigo = d.get("ticker"), d.get("ticker_corto")
        rama_h, rama_n = _rama_hoy(d.get("curva")), _rama_por_ejes(d)
        if not todos and rama_h == rama_n:
            continue
        m = snap.get(simbolo or "")
        if not m:
            sin_precio.append((codigo, rama_h, rama_n, d.get("emisor_tipo")))
            continue

        fake = {"ticker": simbolo, "price": m["price"],
                "ts": m["ts"], "timestamp": m["ts"] or datetime.utcnow()}

        campos_h = _corre(fake, d)
        # LO ÚNICO que cambia entre las dos corridas es `curva` — que es el campo
        # por el que el motor elige la rama. Todo lo demás (flujos, cer_emision,
        # moneda_flujo, vencimiento) es idéntico.
        campos_n = _corre(fake, {**d, "curva": _CURVA_DE_RAMA.get(rama_n, d.get("curva"))})

        tea_h = (campos_h or {}).get("TEA")
        tea_n = (campos_n or {}).get("TEA")
        if tea_h is None and tea_n is None:
            estado, delta = "NINGUNA", None
        elif tea_h is not None and tea_n is None:
            estado, delta = "PIERDE TEA", None
        elif tea_h is None and tea_n is not None:
            estado, delta = "GANA TEA", None
        elif abs(float(tea_h) - float(tea_n)) < 1e-9:
            estado, delta = "IDÉNTICA", 0.0
        else:
            estado = "DIFIERE"
            delta = (float(tea_n) - float(tea_h)) * 10000     # bps

        resultados.append({
            "codigo": codigo, "hoy": rama_h, "nueva": rama_n, "estado": estado,
            "tea_h": tea_h, "tea_n": tea_n, "delta": delta, "px": m["price"],
            "emisor_tipo": d.get("emisor_tipo"),
            "err": (campos_n or {}).get("_error") or (campos_h or {}).get("_error"),
        })

    # ── Resumen ──────────────────────────────────────────────────────────────
    print(_SEP)
    print("  RESUMEN")
    print(_SEP)
    orden = ["IDÉNTICA", "DIFIERE", "PIERDE TEA", "GANA TEA", "NINGUNA"]
    for e in orden:
        n = [r for r in resultados if r["estado"] == e]
        if n:
            print(f"  {e:<13}{len(n):>4}   {', '.join(str(r['codigo']) for r in n)}")
    if sin_precio:
        print(f"  {'SIN PRECIO':<13}{len(sin_precio):>4}   "
              f"{', '.join(str(t) for t, _, _, _ in sin_precio)}")
        print("               (no operaron — no se pueden evaluar, no es un error)")

    # ── Detalle, con SOBERANO/BCRA arriba (prioridad de la mesa) ─────────────
    print(f"\n{_SEP}\n  DETALLE — prioridad: SOBERANO y BCRA primero\n{_SEP}")
    print(f"  {'TICKER':<9}{'HOY':<14}{'→ NUEVA':<16}{'TEA HOY':>10}{'TEA NUEVA':>11}"
          f"{'Δ bps':>10}   ESTADO")
    print("  " + "-" * 100)

    def _orden(r: dict) -> tuple:
        pri = 0 if r["emisor_tipo"] in ("soberano", "bcra") else 1
        rank = {"DIFIERE": 0, "PIERDE TEA": 1, "GANA TEA": 2, "IDÉNTICA": 3, "NINGUNA": 4}
        return (pri, rank.get(r["estado"], 9), str(r["codigo"]))

    pri_ant = None
    for r in sorted(resultados, key=_orden):
        pri = "SOBERANO / BCRA" if r["emisor_tipo"] in ("soberano", "bcra") else "el resto"
        if pri != pri_ant:
            print(f"\n  ▸ {pri}")
            pri_ant = pri
        dl = f"{r['delta']:+.0f}" if r["delta"] is not None else "--"
        marca = "  " if r["estado"] in ("IDÉNTICA", "NINGUNA") else "⚠ "
        print(f"  {marca}{str(r['codigo'])[:8]:<9}{r['hoy']:<14}{'→ ' + r['nueva']:<16}"
              f"{_pct(r['tea_h']):>10}{_pct(r['tea_n']):>11}{dl:>10}   {r['estado']}")
        if r["err"]:
            print(f"      error: {r['err']}")

    print(f"\n{_SEP}")
    print("  CÓMO LEERLO")
    print(_SEP)
    print("  IDÉNTICA   → migrar ese bono al modelo de ejes es GRATIS, medido.")
    print("  DIFIERE    → las dos ramas calculan pero no coinciden. El Δ dice cuánto;")
    print("               hay que decidir CUÁL de las dos es la correcta (no siempre")
    print("               es la nueva: puede ser que los flujos estén en el formato")
    print("               que espera la rama vieja y haya que convertirlos).")
    print("  PIERDE TEA → la rama nueva no puede calcular. NO migrar ese bono hasta")
    print("               arreglar su dato (clasificarlo, o convertir sus flujos).")
    print("  GANA TEA   → hoy está en blanco y la rama nueva sí calcula: es la")
    print("               corrección que estábamos buscando.")
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
