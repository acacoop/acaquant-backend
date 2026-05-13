"""Diag de por qué un bono CER no se está marcando como `cer_fijado`.

Reproduce paso a paso la cadena que usa el endpoint `/api/titulos/flujos`:

  1. Última fecha en `Trading.CER` (lo pisa `jobs.bcra --today` 22 UTC L-V).
  2. Doc del bono en `Trading.Curvas` — `ticker`, `ticker_corto`, `curva`,
     `fecha_vencimiento`.
  3. `fecha_cer_liquidacion(vto, n=10)` calculada — fecha del CER que el
     BCRA tiene que publicar para que el bono quede fijado.
  4. Comparación `fecha_liq <= max_cer_publicado` con TIPOS dumpeados (esta
     es la trampa silenciosa: si DiasHabiles.fecha es str y CER.fecha es
     datetime, la comparación rompe y `_bonos_cer_fijados()` cae al `except
     Exception` devolviendo set vacío sin que nadie se entere).
  5. Mapping LARGO → CORTO via Trading.Curvas.ticker_corto.
  6. Resultado final: ¿está en el set `_bonos_cer_fijados_set_corto()`?
  7. Doc del bono en `Valuaciones.ValuacionesAPI` (la fuente de
     `/api/titulos/flujos`) y qué `curva_efectiva` resolvería.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_cer_fijado X15Y6
    python -m scripts.diag_cer_fijado            # default X15Y6

Pasarle el ticker CORTO, no el largo de Rofex.
"""
from __future__ import annotations

import sys
import traceback

from api.deps import get_db_titulos, get_db_trading
from api.services.renta_fija import _bonos_cer_fijados
from api.routers.titulos import _bonos_cer_fijados_set_corto


def _type_repr(v) -> str:
    return f"{type(v).__name__}({v!r})"


def run(ticker_corto: str) -> None:
    print("=" * 100)
    print(f"DIAG CER fijado — ticker_corto={ticker_corto}")
    print("=" * 100)

    db_trading = get_db_trading()
    db_titulos = get_db_titulos()

    # ── 1. Última fecha en Trading.CER ──────────────────────────────
    print("\n[1] Última fecha publicada en Trading.CER")
    cer_max = db_trading["CER"].find_one({}, sort=[("fecha", -1)], projection={"fecha": 1, "valor": 1})
    if not cer_max:
        print("    ✗ Colección Trading.CER vacía. Correr `python -m jobs.bcra --today`.")
        return
    max_fecha = cer_max["fecha"]
    print(f"    max_cer_publicado = {_type_repr(max_fecha)}")
    if "valor" in cer_max:
        print(f"    valor             = {cer_max['valor']}")

    # ── 2. Doc del bono en Trading.Curvas ──────────────────────────
    print(f"\n[2] Bono en Trading.Curvas (ticker_corto={ticker_corto})")
    curva_doc = db_trading["Curvas"].find_one(
        {"ticker_corto": ticker_corto},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1, "fecha_vencimiento": 1, "tipo": 1},
    )
    if not curva_doc:
        print(f"    ✗ No hay doc con ticker_corto='{ticker_corto}' en Trading.Curvas.")
        # Buscar variantes por si está en mayúsculas/minúsculas distintas o ticker LARGO.
        alt = list(db_trading["Curvas"].find(
            {"$or": [
                {"ticker_corto": {"$regex": f"^{ticker_corto}$", "$options": "i"}},
                {"ticker": {"$regex": ticker_corto, "$options": "i"}},
            ]},
            {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1},
        ).limit(5))
        if alt:
            print(f"    ¿Buscabas alguno de estos? {alt}")
        return
    print(f"    {curva_doc}")
    ticker_largo = curva_doc["ticker"]
    vto = str(curva_doc.get("fecha_vencimiento") or "")[:10]
    if curva_doc.get("curva") != "cer":
        print(f"    ⚠ curva='{curva_doc.get('curva')}' — el bono ya NO está en la curva CER. "
              f"Si lo que ves en producción todavía dice CER, posible cache de Vercel/CDN.")

    # ── 3. fecha_cer_liquidacion ───────────────────────────────────
    print("\n[3] fecha_cer_liquidacion(vto, n=10)")
    dias_habiles = sorted(
        d["fecha"] for d in db_trading["DiasHabiles"].find({}, {"fecha": 1, "_id": 0})
    )
    print(f"    DiasHabiles cargados: {len(dias_habiles)} fechas")
    if dias_habiles:
        print(f"    primer elemento:  {_type_repr(dias_habiles[0])}")
        print(f"    último elemento:  {_type_repr(dias_habiles[-1])}")

    from engines.curvas import fecha_cer_liquidacion
    fecha_liq = fecha_cer_liquidacion(dias_habiles, vto, n=10)
    print(f"    vto              = {_type_repr(vto)}")
    print(f"    fecha_liq        = {_type_repr(fecha_liq)}")

    # ── 4. Comparación fecha_liq <= max_cer_publicado ─────────────
    print("\n[4] Comparación fecha_liq <= max_cer_publicado")
    print(f"    fecha_liq tipo         = {type(fecha_liq).__name__}")
    print(f"    max_cer_publicado tipo = {type(max_fecha).__name__}")
    if fecha_liq is None:
        print("    fecha_liq es None — no entra al set. Causa: vto sin suficientes hábiles atrás.")
    else:
        try:
            cmp_result = fecha_liq <= max_fecha
            print(f"    fecha_liq <= max_cer_publicado → {cmp_result}")
            if not cmp_result:
                print(f"    Falta publicar CER hasta {fecha_liq}. Última publicada: {max_fecha}.")
        except TypeError as e:
            print(f"    ✗ TypeError al comparar — ESTO ES LA CAUSA DEL FALLO SILENCIOSO.")
            print(f"      {e}")
            print(f"      _bonos_cer_fijados() cae al except Exception y devuelve set() vacío.")

    # ── 5. Set fijados (vía función real, con cache) ──────────────
    print("\n[5] Resultado de _bonos_cer_fijados() (cached, TTL 30s)")
    try:
        fijados_largos = _bonos_cer_fijados()
        print(f"    {len(fijados_largos)} bonos fijados (ticker LARGO)")
        print(f"    ¿{ticker_largo} adentro? {'SÍ' if ticker_largo in fijados_largos else 'NO'}")
        if fijados_largos and len(fijados_largos) <= 20:
            for t in sorted(fijados_largos):
                print(f"      · {t}")
    except Exception:
        print("    ✗ _bonos_cer_fijados() levantó:")
        traceback.print_exc()

    # ── 6. Mapping LARGO → CORTO ──────────────────────────────────
    print("\n[6] _bonos_cer_fijados_set_corto() — mapping vía Trading.Curvas.ticker_corto")
    try:
        fijados_cortos = _bonos_cer_fijados_set_corto()
        print(f"    {len(fijados_cortos)} bonos fijados (ticker CORTO)")
        in_set = ticker_corto in fijados_cortos
        print(f"    ¿{ticker_corto} adentro? {'SÍ' if in_set else 'NO'}")
        if not in_set and fijados_cortos and len(fijados_cortos) <= 30:
            print(f"    fijados (cortos): {sorted(fijados_cortos)}")
    except Exception:
        print("    ✗ _bonos_cer_fijados_set_corto() levantó:")
        traceback.print_exc()

    # ── 7. ValuacionesAPI doc + curva_efectiva esperada ───────────
    print(f"\n[7] Doc en Valuaciones.ValuacionesAPI para ticker={ticker_corto}")
    val_docs = list(db_titulos["ValuacionesAPI"].find(
        {"ticker": ticker_corto}, {"_id": 0},
    ))
    if not val_docs:
        print(f"    ✗ Sin docs con ticker='{ticker_corto}' en ValuacionesAPI. "
              f"Sync corre vía `jobs/sync_api_copies.py`.")
    else:
        print(f"    {len(val_docs)} doc(s)")
        for d in val_docs[:3]:
            curva_orig = d.get("curva")
            print(f"      · curva={curva_orig} fecha_vto={d.get('fecha_vencimiento')} "
                  f"moneda_flujo={d.get('moneda_flujo')}")
        # Simular lo que hace listar_flujos_titulos:
        in_set = ticker_corto in (fijados_cortos if 'fijados_cortos' in locals() else set())
        for d in val_docs:
            curva_orig = d.get("curva")
            is_fijado = (curva_orig == "cer") and in_set
            curva_ef = "tasa_fija" if is_fijado else curva_orig
            print(f"    → cer_fijado={is_fijado}  curva_efectiva={curva_ef}")
            break  # con el primero alcanza

    print("\n" + "=" * 100)
    print("VEREDICTO")
    print("=" * 100)
    print("Mirá los pasos [4]–[6]:")
    print("  · Si [4] dice TypeError → mismatch de tipos (str vs datetime) entre DiasHabiles y CER.")
    print("  · Si [4] dice False → falta publicar CER hasta fecha_liq (esperar job BCRA / día).")
    print("  · Si [5] dice SÍ pero [6] dice NO → mapping ticker_corto roto en Trading.Curvas.")
    print("  · Si [6] dice SÍ pero el frontend sigue mostrando CER → cache de Vercel/CDN o ")
    print("    `/api/titulos/flujos` cacheado 60s. Esperar 1 min y refrescar con cache-busting.")


if __name__ == "__main__":
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "X15Y6"
    run(ticker)
