"""Diagnóstico encadenado de los 2 bugs CER:

  1. BCRA job no actualiza CER → ¿qué está devolviendo la API hoy?
  2. Bonos CER con CER de liquidación ya publicado no migran a tasa fija
     → ¿qué dice _bonos_cer_fijados() y por qué deja afuera a algunos?

Salida (en este orden):
  A. Pega directo a la API BCRA v4.0 con los mismos params que el job
     (today-3 → today+21) y muestra el rango devuelto.
  B. Lee Trading.CER y muestra max(fecha) + últimas 5 entradas. Comparar
     con (A) para confirmar si el job está al día o le faltan publicaciones
     nuevas.
  C. Lee Trading.DiasHabiles y muestra max(fecha). Si está stale el cálculo
     de fecha_cer_liquidacion devuelve None y no se fijan bonos.
  D. Para CADA bono CER en Trading.Curvas: ticker, vto, fecha_liq (vto-10
     hábiles), si fecha_liq <= max_cer_publicado → fijado, sino → variable.
     Resalta los que están a < 30 días de vto (candidatos a fijación
     inminente).

Uso (en el Droplet):
    python -m scripts.diag_cer_fijados
"""
from __future__ import annotations

from datetime import date, timedelta

import requests

from core.mongo import get_mongo_client_read

ID_CER_BCRA = 30
BCRA_URL = f"https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias/{ID_CER_BCRA}"


def section_a_bcra_api() -> tuple[str | None, str | None]:
    print("=== A. ¿Qué devuelve la API BCRA HOY? (idéntico al job --today) ===")
    desde = (date.today() - timedelta(days=3)).isoformat()
    hasta = (date.today() + timedelta(days=21)).isoformat()
    print(f"   desde={desde}  hasta={hasta}")
    try:
        r = requests.get(BCRA_URL, params={"desde": desde, "hasta": hasta}, timeout=15)
        print(f"   HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"   body (recortado): {r.text[:400]}")
            return None, None
        j = r.json()
        results = j.get("results") or []
        if not results:
            print("   results = [] → API responde OK pero sin contenido")
            return None, None
        detalle = results[0].get("detalle") or []
        print(f"   detalle: {len(detalle)} filas")
        if not detalle:
            print("   detalle vacío — BCRA no tiene CER en el rango pedido")
            return None, None
        primera = detalle[0].get("fecha")
        ultima = detalle[-1].get("fecha")
        # Asume orden cronológico; si no, calcular min/max.
        fechas = [d.get("fecha") for d in detalle if d.get("fecha")]
        if fechas:
            primera = min(fechas)
            ultima = max(fechas)
        print(f"   rango devuelto: {primera} → {ultima}")
        print("   últimas 5 publicadas:")
        for d in sorted(detalle, key=lambda x: x.get("fecha") or "")[-5:]:
            print(f"     {d.get('fecha')} = {d.get('valor')}")
        return primera, ultima
    except Exception as e:
        print(f"   error: {e}")
        return None, None


def section_b_mongo_cer() -> str | None:
    print("\n=== B. Trading.CER en Mongo ===")
    db = get_mongo_client_read()
    col = db["Trading"]["CER"]
    n = col.count_documents({})
    print(f"   total docs: {n}")
    if n == 0:
        print("   colección vacía — el job nunca corrió o se vació")
        return None
    docs = list(col.find({}, {"_id": 0}).sort("fecha", -1).limit(5))
    max_fecha = docs[0]["fecha"] if docs else None
    print(f"   max(fecha) en DB: {max_fecha}")
    print("   últimas 5 entradas en DB:")
    for d in docs:
        print(f"     {d.get('fecha')} = {d.get('valor')}")
    return max_fecha


def section_c_dias_habiles() -> str | None:
    print("\n=== C. Trading.DiasHabiles ===")
    db = get_mongo_client_read()
    col = db["Trading"]["DiasHabiles"]
    n = col.count_documents({})
    print(f"   total docs: {n}")
    if n == 0:
        print("   ¡VACÍO! fecha_cer_liquidacion() devuelve None siempre → 0 bonos fijados")
        return None
    last = col.find_one({}, {"_id": 0}, sort=[("fecha", -1)])
    print(f"   max(fecha) DiasHabiles: {last.get('fecha') if last else 'n/a'}")
    return last.get("fecha") if last else None


def section_d_fijados(max_cer: str | None) -> None:
    print("\n=== D. Estado de fijación bono por bono ===")
    if not max_cer:
        print("   sin max_cer_publicado → no puedo calcular fijación")
        return

    try:
        from engines.curvas import fecha_cer_liquidacion
    except Exception as e:
        print(f"   no pude importar fecha_cer_liquidacion: {e}")
        return

    db = get_mongo_client_read()
    dias_habiles = sorted(
        d["fecha"] for d in db["Trading"]["DiasHabiles"].find({}, {"fecha": 1, "_id": 0})
    )
    if not dias_habiles:
        print("   sin DiasHabiles → nada que fijar")
        return

    print(f"   max_cer_publicado en DB: {max_cer}")
    print(
        f"   {'TICKER':<14} {'VTO':<12} {'FECHA_LIQ_CER':<14} "
        f"{'ESTADO':<10} DÍAS_AL_VTO"
    )
    hoy_iso = date.today().isoformat()

    bonos = []
    for inst in db["Trading"]["Curvas"].find(
        {"curva": "cer"},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "fecha_vencimiento": 1},
    ):
        vto = str(inst.get("fecha_vencimiento") or "")[:10]
        if not vto:
            continue
        fecha_liq = fecha_cer_liquidacion(dias_habiles, vto, n=10)
        fijado = bool(fecha_liq and fecha_liq <= max_cer)
        try:
            dias_al_vto = (
                date.fromisoformat(vto) - date.today()
            ).days
        except Exception:
            dias_al_vto = None
        bonos.append({
            "ticker": inst.get("ticker_corto") or inst["ticker"],
            "vto": vto,
            "fecha_liq": fecha_liq or "—",
            "fijado": fijado,
            "dias_al_vto": dias_al_vto,
        })

    # Ordeno por vto ascendente (los más cercanos primero).
    bonos.sort(key=lambda b: b["vto"])
    fijados_count = sum(1 for b in bonos if b["fijado"])
    print(f"   total bonos CER: {len(bonos)}  |  fijados: {fijados_count}  "
          f"|  variables: {len(bonos) - fijados_count}\n")

    # Imprimo todos los que tienen <= 60 días al vto (zona de fijación inminente).
    print("   --- Bonos a <= 60 días del vto (zona de fijación inminente) ---")
    for b in bonos:
        if b["dias_al_vto"] is None or b["dias_al_vto"] > 60:
            continue
        marker = (
            "  ⚠ DEBERIA FIJARSE" if (b["fecha_liq"] != "—" and b["fecha_liq"] <= hoy_iso and not b["fijado"])
            else ""
        )
        print(
            f"   {b['ticker']:<14} {b['vto']:<12} {b['fecha_liq']:<14} "
            f"{'FIJADO' if b['fijado'] else 'VARIABLE':<10} "
            f"{b['dias_al_vto']:>3} días{marker}"
        )

    # Listo también los ya fijados (para confirmar que el camino sí funciona en algún caso).
    print("\n   --- Ya fijados ---")
    if not fijados_count:
        print("   (ninguno)")
    for b in bonos:
        if not b["fijado"]:
            continue
        print(
            f"   {b['ticker']:<14} {b['vto']:<12} fecha_liq={b['fecha_liq']} "
            f"({b['dias_al_vto']} días al vto)"
        )


if __name__ == "__main__":
    _bcra_min, bcra_max = section_a_bcra_api()
    mongo_max = section_b_mongo_cer()
    section_c_dias_habiles()
    section_d_fijados(mongo_max)

    print("\n=== Resumen ===")
    if bcra_max and mongo_max and bcra_max > mongo_max:
        print(f"   ⚠ BCRA publicó hasta {bcra_max} pero la DB solo tiene hasta {mongo_max}")
        print("     El job NO está insertando los nuevos forward. Corré:")
        print("     python -m jobs.bcra --today")
    elif bcra_max and mongo_max and bcra_max == mongo_max:
        print(f"   ✓ DB al día con BCRA ({mongo_max})")
    elif not bcra_max:
        print("   ⚠ La API BCRA no devolvió datos en el rango pedido — investigar.")
