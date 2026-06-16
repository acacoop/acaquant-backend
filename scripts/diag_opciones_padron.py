"""diag_opciones_padron.py — qué tiene REALMENTE el padrón pyRofex sobre GGAL/opciones.

El motor dice "No se encontraron opciones de GGAL" con cficodes vacío. Esto distingue
las causas: ¿cambió el LABEL del underlying? ¿desaparecieron las opciones GGAL? ¿no hay
opciones de NADA en el padrón? Muestra los underlyings tipo Galicia, los símbolos GFG*
y todas las opciones (cfi O*) por subyacente.

Uso (en el Droplet):  python -m scripts.diag_opciones_padron
"""
from collections import Counter

import pyRofex

from core.rofex_session import inicializar_sesion

TARGET = "Grupo Financiero Galicia Merval"  # lo que filtra el motor, exacto


def _sym(i: dict) -> str:
    return (i.get("instrumentId") or {}).get("symbol") or ""


def main() -> None:
    if not inicializar_sesion():
        print("❌ inicializar_sesion() devolvió False — sesión pyRofex no arrancó.")
        return
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        print(f"❌ get_detailed_instruments status != OK: {res.get('status') if res else res}")
        return
    insts = res["instruments"]
    print(f"padrón total: {len(insts)} instrumentos\n")

    # 1) ¿Existe el underlying EXACTO que filtra el motor?
    n_target = sum(1 for i in insts if i.get("underlying") == TARGET)
    print(f"1) underlying == {TARGET!r}: {n_target} instrumentos")

    # 2) Underlyings que mencionan galicia/ggal (detecta label cambiado)
    print("\n2) underlyings que matchean 'galicia'/'ggal':")
    ul: Counter = Counter()
    for i in insts:
        u = i.get("underlying") or ""
        if "galicia" in u.lower() or "ggal" in u.lower():
            ul[u] += 1
    if ul:
        for u, n in ul.most_common():
            print(f"   {n:>5}  {u!r}")
    else:
        print("   NINGUNO ← ni 'Galicia' ni 'GGAL' aparecen como underlying")

    # 3) Símbolos GFG* (prefijo típico de opciones GGAL: GFGC=call, GFGV=put)
    gfg = [i for i in insts if "GFG" in _sym(i)]
    print(f"\n3) símbolos que contienen 'GFG': {len(gfg)}")
    for i in gfg[:12]:
        print(f"   {_sym(i):<22} cfi={i.get('cficode')!r:<10} "
              f"under={i.get('underlying')!r} vto={i.get('maturity_date', i.get('maturityDate'))}")

    # 4) ¿Hay opciones (cfi O*) de CUALQUIER subyacente? (¿falta el segmento entero?)
    opts = [i for i in insts if (i.get("cficode") or "").startswith("O")]
    print(f"\n4) opciones (cfi O*) en TODO el padrón: {len(opts)}")
    if opts:
        und = Counter((i.get("underlying") or "?") for i in opts)
        print("   por underlying (top 15):")
        for u, n in und.most_common(15):
            print(f"     {n:>5}  {u!r}")
    else:
        print("   ❌ CERO opciones de cualquier cosa → el segmento de opciones NO viene "
              "en get_detailed_instruments (problema de ROFEX/segmento, no del motor).")


if __name__ == "__main__":
    main()
