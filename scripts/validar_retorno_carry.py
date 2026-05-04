"""validar_retorno_carry.py — confirma que las dos vistas llegan a hoy.

Llama a los services directo (sin HTTP). Si esto anda, después de
restart api.service la vista en el frontend tiene que andar igual.

Uso:
    python -m scripts.validar_retorno_carry
"""
from datetime import date, timedelta

from api.services.carry_trade import serie_carry_trade
from api.services.descomposicion_retorno import descomposicion_realizada
from api.services.renta_fija import get_historico_curva


def _mark(ok: bool) -> str:
    return "[OK]" if ok else "[!! ]"


def main() -> None:
    hoy = date.today()
    hoy_s = hoy.isoformat()
    desde = (hoy - timedelta(days=20)).isoformat()

    print()
    print("=" * 64)
    print(f" Validación Retorno Total + Carry Trade   ({hoy_s})")
    print("=" * 64)

    # ── 1. Carry Trade tasa_fija ────────────────────────────────
    print("\n[1] Carry Trade — tasa_fija")
    r = serie_carry_trade(curva="tasa_fija", desde=desde)
    if r.get("error"):
        print(f"    [!! ] error: {r['error']}")
    else:
        ff = r.get("fecha_final")
        print(
            f"    {_mark(ff == hoy_s)} fecha_base: {r.get('fecha_base')}  ·  "
            f"fecha_final: {ff}  ·  serie: {len(r.get('serie', []))} días"
        )

    # ── 2. Carry Trade cer ──────────────────────────────────────
    print("\n[2] Carry Trade — cer")
    r = serie_carry_trade(curva="cer", desde=desde)
    if r.get("error"):
        print(f"    [!! ] error: {r['error']}")
    else:
        ff = r.get("fecha_final")
        print(
            f"    {_mark(ff == hoy_s)} fecha_base: {r.get('fecha_base')}  ·  "
            f"fecha_final: {ff}  ·  serie: {len(r.get('serie', []))} días"
        )

    # ── 3. Retorno Total ex-post tasa_fija ──────────────────────
    print("\n[3] Retorno Total ex-post — tasa_fija (desde-hasta hoy)")
    r = descomposicion_realizada(desde=desde, hasta=hoy_s, curva="tasa_fija")
    if r.get("error"):
        print(f"    [!! ] error: {r['error']}")
    else:
        n = len(r.get("bonos", []))
        print(f"    {_mark(n > 0)} días: {r.get('dias')}  ·  bonos: {n}")

    # ── 4. Retorno Total ex-post cer ────────────────────────────
    print("\n[4] Retorno Total ex-post — cer (desde-hasta hoy)")
    r = descomposicion_realizada(desde=desde, hasta=hoy_s, curva="cer")
    if r.get("error"):
        print(f"    [!! ] error: {r['error']}")
    else:
        n = len(r.get("bonos", []))
        cer_acc = r.get("cer_accrual_periodo")
        print(
            f"    {_mark(n > 0)} días: {r.get('dias')}  ·  bonos: {n}  ·  "
            f"cer_accrual: {cer_acc}"
        )

    # ── 5. /historico-curva — fechas que ve el selector del frontend ────
    print("\n[5] /historico-curva — última fecha que ve el selector del front")
    for curva in ("tasa_fija", "cer"):
        rows = get_historico_curva(curva=curva)
        fechas = sorted({r.get("fecha") for r in rows if r.get("fecha")})
        ult = fechas[-1] if fechas else None
        print(f"    {_mark(ult == hoy_s)} {curva:<14}  última: {ult}  ·  {len(fechas)} días en total")

    print()
    print("=" * 64)
    print(" Lectura:")
    print(f"   - [1][2] fecha_final = {hoy_s}  → live fallback en carry-trade")
    print( "   - [3][4] bonos > 0           → live fallback en descomposicion")
    print(f"   - [5] última = {hoy_s}        → selector del frontend incluye hoy")
    print( "   Si todo da [OK] y el frontend igual muestra fecha vieja,")
    print( "   recién ahí es cache de browser (Ctrl+Shift+R).")
    print("=" * 64)
    print()


if __name__ == "__main__":
    main()
