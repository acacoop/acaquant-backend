"""scripts/diag_opciones_padron.py — por qué motor_options no encuentra opciones.

Replica la 1ra pasada de OptionsEngine._generar_maestra: pide el padrón a pyRofex y
cuenta las opciones GGAL + sus vencimientos FUTUROS. Si no hay futuros, el motor hace
`return` → loop de restart. Confirma si es pre-mercado / padrón vacío vs problema real.

Uso (en el Droplet, idealmente en rueda):  python -m scripts.diag_opciones_padron
"""
from collections import Counter
from datetime import datetime

import pyRofex

from core.rofex_session import inicializar_sesion


def main() -> None:
    if not inicializar_sesion():
        print("❌ inicializar_sesion() devolvió False — la sesión pyRofex no arrancó.")
        return
    print("✅ sesión pyRofex OK")

    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        print(f"❌ get_detailed_instruments status != OK: {res.get('status') if res else res}")
        return
    insts = res["instruments"]
    print(f"padrón total: {len(insts)} instrumentos")

    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ggal = [i for i in insts if i.get("underlying") == "Grupo Financiero Galicia Merval"]
    opciones = [i for i in ggal if i.get("cficode", "").startswith("O")]
    print(f"GGAL underlying: {len(ggal)}  ·  opciones GGAL (cfi O*): {len(opciones)}")

    fut, pas, bad = set(), set(), 0
    for i in opciones:
        v = i.get("maturity_date", i.get("maturityDate", ""))
        if len(v) == 8:
            try:
                (fut if datetime.strptime(v, "%Y%m%d") > hoy else pas).add(v)
            except ValueError:
                bad += 1
        else:
            bad += 1
    print(f"vencimientos FUTUROS (> hoy {hoy:%Y-%m-%d}): {sorted(fut) or 'NINGUNO ← causa del crash'}")
    print(f"vencimientos pasados/hoy: {sorted(pas)}")
    if bad:
        print(f"maturity_date raros/ausentes: {bad}")
    print(f"cficodes: {dict(Counter(i.get('cficode') for i in opciones))}")

    if not fut:
        print("\n→ DIAGNÓSTICO: 0 vencimientos futuros → el motor hace return + restart loop.")
        print("  Pre-mercado: ROFEX aún no listó opciones → debería resolverse al abrir (13:30 UTC).")
        print("  En plena rueda: problema real (OPEX sin nuevo vencimiento / data ROFEX).")
    else:
        print(f"\n→ Hay {len(fut)} vencimiento(s) futuro(s) → el motor DEBERÍA arrancar.")


if __name__ == "__main__":
    main()
