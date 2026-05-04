"""validar_retorno_carry.py — confirma que las dos vistas llegan a hoy.

Llama a los services directo (sin HTTP). Si esto anda, después de
restart api.service la vista en el frontend tiene que andar igual.

Uso:
    python -m scripts.validar_retorno_carry
"""
from datetime import date, timedelta

from api.services.carry_trade import serie_carry_trade
from api.services.descomposicion_retorno import descomposicion_realizada


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

    print()
    print("=" * 64)
    print(" Lectura:")
    print(f"   - [1][2] fecha_final = {hoy_s}  → live fallback funcionando")
    print( "   - [3][4] bonos > 0    → snapshot de hoy disponible")
    print( "   Si todo da [OK], los cambios funcionan. Si el frontend igual")
    print( "   muestra fecha vieja, es cache de Vercel (acaquant-web).")
    print("=" * 64)
    print()


if __name__ == "__main__":
    main()
