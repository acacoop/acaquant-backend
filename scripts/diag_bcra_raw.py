"""Diagnóstico raw del job BCRA — para descartar que la API esté devolviendo
data stale/proyectada y nuestra DB esté guardando algo distinto de lo que
BCRA publica oficialmente.

Muestra:
  A. URL EXACTA + status + headers + body raw (sin parsear) de la API BCRA
     con el rango del job (today-3 → today+21). Si BCRA cambió el endpoint
     o nos redirige a cache stale, lo vemos acá.
  B. Misma query pero SOLO hacia atrás (today-30 → today) para comparar
     con (A) y ver si los valores cambian (si la respuesta forward dates
     es real o una proyección).
  C. Para cada fecha en la respuesta de (A): si está en nuestra DB
     muestra el valor en DB, sino dice "NO EN DB".

Uso (en el Droplet):
    python -m scripts.diag_bcra_raw

Después de ver el output, comparar manualmente contra lo que dice
bcra.gob.ar para el CER del día.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import requests

from core.mongo import get_mongo_client_read

ID_CER = 30
BASE = "https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias"


def _hit(label: str, desde: str, hasta: str) -> dict | None:
    url = f"{BASE}/{ID_CER}"
    params = {"desde": desde, "hasta": hasta}
    print(f"\n=== {label} ===")
    print(f"   GET {url}")
    print(f"   params: {params}")
    try:
        r = requests.get(url, params=params, timeout=15)
        print(f"   HTTP {r.status_code}")
        print(f"   final url (post-redirects): {r.url}")
        print(f"   server: {r.headers.get('server') or '—'}")
        print(f"   date header: {r.headers.get('date') or '—'}")
        print(f"   cache-control: {r.headers.get('cache-control') or '—'}")
        print(f"   age header: {r.headers.get('age') or '—'}")
        if r.status_code != 200:
            print(f"   body (recortado): {r.text[:500]}")
            return None
        j = r.json()
        # Imprimo el resumen pero también el JSON completo recortado.
        results = j.get("results") or []
        print(f"   metadata: {j.get('metadata') or '—'}")
        if not results:
            print("   results = [] → API responde 200 OK pero sin contenido")
            print(f"   raw body: {json.dumps(j, ensure_ascii=False)[:600]}")
            return j
        detalle = results[0].get("detalle") or []
        print(f"   detalle: {len(detalle)} filas")
        fechas = sorted(d.get("fecha") for d in detalle if d.get("fecha"))
        if fechas:
            print(f"   rango: {fechas[0]} → {fechas[-1]}")
        print("   filas completas:")
        for d in sorted(detalle, key=lambda x: x.get("fecha") or ""):
            # Imprimo cada fila tal cual la devuelve BCRA (todos los keys).
            print(f"     {json.dumps(d, ensure_ascii=False)}")
        return j
    except Exception as e:
        print(f"   error: {e}")
        return None


def _compare_vs_db(resp: dict | None) -> None:
    print("\n=== C. Comparación API ↔ Mongo (Trading.CER) por fecha ===")
    if not resp:
        print("   sin respuesta de la API, salto")
        return
    results = resp.get("results") or []
    if not results:
        return
    detalle = results[0].get("detalle") or []
    if not detalle:
        return

    db = get_mongo_client_read()
    col = db["Trading"]["CER"]
    fechas = [d.get("fecha") for d in detalle if d.get("fecha")]
    docs = {
        d["fecha"]: d.get("valor")
        for d in col.find({"fecha": {"$in": fechas}}, {"_id": 0, "fecha": 1, "valor": 1})
    }

    print(f"   {'FECHA':<12} {'API':<22} {'DB':<22} ¿MATCH?")
    for d in sorted(detalle, key=lambda x: x.get("fecha") or ""):
        f = d.get("fecha")
        v_api = d.get("valor")
        v_db = docs.get(f, "NO EN DB")
        match = "✓" if (v_db != "NO EN DB" and v_db == v_api) else "✗"
        print(f"   {f:<12} {v_api!s:<22} {v_db!s:<22} {match}")


def _peek_db() -> None:
    print("\n=== D. Sample de Trading.CER (5 más recientes) ===")
    db = get_mongo_client_read()
    docs = list(
        db["Trading"]["CER"].find({}, {"_id": 0}).sort("fecha", -1).limit(5)
    )
    for d in docs:
        print(f"   {d}")


if __name__ == "__main__":
    today = date.today()
    desde_fwd = (today - timedelta(days=3)).isoformat()
    hasta_fwd = (today + timedelta(days=21)).isoformat()
    desde_back = (today - timedelta(days=30)).isoformat()
    hasta_back = today.isoformat()

    print("Para verificar manualmente: comparar las filas devueltas con")
    print("https://www.bcra.gob.ar/PublicacionesEstadisticas/Principales_variables.asp")
    print("(o el endpoint oficial 'CER' del BCRA).\n")

    resp_fwd = _hit(f"A. Con forward (rango del job): {desde_fwd} → {hasta_fwd}",
                    desde_fwd, hasta_fwd)
    _hit(f"B. Solo histórico hacia atrás: {desde_back} → {hasta_back}",
         desde_back, hasta_back)
    _compare_vs_db(resp_fwd)
    _peek_db()
