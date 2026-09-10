"""scripts/diag_fci_cafci.py — qué responde de verdad la API pública de CAFCI. SOLO LECTURA.

Herramienta: fci · Diag: si los 3 endpoints de CAFCI que usa el Excel responden y con qué shape

El Excel «Informe FCI Semanal» de la mesa consume desde Power Query estos tres
endpoints (leídos de sus consultas embebidas). Nadie del sistema los llamó
nunca. Este diag lo hace UNA vez y muestra, sin interpretar:

  1. /fondo?estado=1&include=…&limit=0  → status, latencia, tamaño, cuántos
     fondos y clases vienen, claves de un fondo y de una clase, tipos de renta
     y monedaId distintos.
  2. /fondo/{f}/clase/{c}/rendimiento/{desde}/{hasta}?step=1 → cuántos puntos
     trae en una llamada, primera y última fecha, un elemento crudo.
  3. /fondo/{f}/clase/{c}/ficha → claves de data.info.diaria / semanal y los
     valores del bloque `actual` (patrimonio, vcp, vcpUnitario…).

Default: fondo 518 / clase 1047 = «Argenfunds Ahorro Pesos - Clase B» (ids
tomados de la hoja BaseFCI del Excel). Cambiar con --fondo/--clase.

Uso:
    venv/bin/python -m scripts.diag_fci_cafci
    venv/bin/python -m scripts.diag_fci_cafci --fondo 1343 --clase 3831 --desde 2026-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date

import requests

BASE = "https://api.cafci.org.ar"
INCLUDE = ("entidad;depositaria,entidad;gerente,tipoRenta,tipoRentaMixta,region,"
           "benchmark,horizonte,duration,tipo_fondo,clase_fondo")


def _get(url: str, params: dict | None = None, timeout: int = 90):
    t0 = time.perf_counter()
    try:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": "TradingAV-diag/1.0"})
    except requests.RequestException as e:
        print(f"  ✗ {url}: {type(e).__name__}: {e}")
        return None
    ms = (time.perf_counter() - t0) * 1000
    print(f"  {r.status_code} · {ms:,.0f} ms · {len(r.content):,} bytes · {r.url}")
    if r.status_code != 200:
        print("  cuerpo:", r.text[:400])
        return None
    try:
        return r.json()
    except ValueError:
        print("  respuesta NO JSON:", r.text[:400])
        return None


def _keys(d, n=60) -> list:
    return sorted(d.keys())[:n] if isinstance(d, dict) else [type(d).__name__]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fondo", type=int, default=518)
    ap.add_argument("--clase", type=int, default=1047)
    ap.add_argument("--desde", default="2023-12-29")
    ap.add_argument("--sin-catalogo", action="store_true", help="saltear el catálogo (pesa varios MB)")
    a = ap.parse_args()
    hoy = date.today().isoformat()

    # 1) catálogo
    if not a.sin_catalogo:
        print("\n── 1. CATÁLOGO /fondo")
        cat = _get(f"{BASE}/fondo", {"estado": 1, "include": INCLUDE, "limit": 0,
                                      "order": "clase_fondos.nombre"})
        if isinstance(cat, dict):
            print("  claves top:", _keys(cat))
            fondos = cat.get("data") if isinstance(cat.get("data"), list) else []
            clases = [c for f in fondos for c in (f.get("clase_fondos") or [])]
            print(f"  fondos: {len(fondos)} · clases: {len(clases)}")
            if fondos:
                f0 = fondos[0]
                print("  claves de un fondo:", _keys(f0))
                print("  gerente (anidado):", json.dumps(f0.get("gerente"), ensure_ascii=False)[:300])
                print("  sociedadGerenteId:", f0.get("sociedadGerenteId"), "· diasLiquidacion:",
                      f0.get("diasLiquidacion"), "· codigoCNV:", f0.get("codigoCNV"))
                if clases:
                    print("  claves de una clase:", _keys(clases[0]))
                    print("  clase ejemplo:", json.dumps(clases[0], ensure_ascii=False)[:300])
                print("  tipoRenta:", Counter((f.get("tipoRenta") or {}).get("nombre") for f in fondos))
                print("  monedaId de las clases:", Counter(c.get("monedaId") for c in clases))
                print("  gerentes distintos:", len({(f.get("gerente") or {}).get("nombreCorto") for f in fondos}))
        elif cat is not None:
            print("  shape inesperada:", type(cat).__name__, str(cat)[:300])

    # 2) serie
    print(f"\n── 2. RENDIMIENTO /fondo/{a.fondo}/clase/{a.clase}/rendimiento/{a.desde}/{hoy}?step=1")
    ren = _get(f"{BASE}/fondo/{a.fondo}/clase/{a.clase}/rendimiento/{a.desde}/{hoy}", {"step": 1})
    if isinstance(ren, dict):
        print("  claves top:", _keys(ren))
        data = ren.get("data")
        if isinstance(data, list):
            print(f"  puntos: {len(data)}")
            if data:
                print("  primero:", json.dumps(data[0], ensure_ascii=False)[:400])
                print("  último: ", json.dumps(data[-1], ensure_ascii=False)[:400])
                fechas = [str((d.get("hasta") or {}).get("fecha"))[:10] for d in data if isinstance(d, dict)]
                print(f"  fechas hasta: {min(fechas)} → {max(fechas)} · distintas: {len(set(fechas))}")
        else:
            print("  data no es lista:", type(data).__name__, str(data)[:300])

    # 3) ficha
    print(f"\n── 3. FICHA /fondo/{a.fondo}/clase/{a.clase}/ficha")
    fi = _get(f"{BASE}/fondo/{a.fondo}/clase/{a.clase}/ficha")
    if isinstance(fi, dict):
        print("  claves top:", _keys(fi))
        info = ((fi.get("data") or {}).get("info") or {})
        print("  claves data.info:", _keys(info))
        diaria = info.get("diaria") or {}
        print("  claves diaria:", _keys(diaria))
        print("  diaria.actual:", json.dumps(diaria.get("actual"), ensure_ascii=False)[:500])
        print("  diaria.rendimientos:", json.dumps(diaria.get("rendimientos"), ensure_ascii=False)[:600])
        semanal = info.get("semanal") or {}
        print("  claves semanal:", _keys(semanal), "· fechaDatos:", semanal.get("fechaDatos"))
        carteras = semanal.get("carteras") or []
        print(f"  carteras: {len(carteras)} · primera:", json.dumps(carteras[0], ensure_ascii=False)[:300] if carteras else "—")
    return 0


if __name__ == "__main__":
    sys.exit(main())
