"""scripts/seed_curvas_ons.py — promover ONs de BondsMaster a Trading.Curvas.

Hace que las ONs sean instrumentos live de primera clase reusando TODA la
maquinaria existente (REGLA #7, máximo reuso):
  - motor_rofex las suscribe solo (lee tickers de Trading.Curvas).
  - motor_curvas las enriquece (TIR/duration/paridad) con la rama "on"
    (que reusa la matemática de soberanos para USD y de tasa_fija para ARS).
  - renta_fija.listar_curva("on") las sirve en la vista.

FUENTE: Trading.BondsMaster (master editable — el user cura: borra/agrega).
DESTINO: Trading.Curvas, docs con curva="on" (sync idempotente).

Mapeo por moneda (BondsMaster.moneda_flujo):
  - USD → pata canónica = ticker D (USD). Precio ya en USD → TIR directa.
  - ARS → pata canónica = ticker O (pesos). Precio y flujos en pesos.

Flujos: se conservan en el shape NATIVO de BondsMaster
  {fecha, amortizacion, interes, valor_residual}
porque la rama "on" del motor usa monto_flujo() = amortizacion + interes
(montos absolutos por 100 VN), igual que tasa_fija. NO se remapea a % (evita
una transformación con pérdida).

MODOS:
  (default, dry-run)  python -m scripts.seed_curvas_ons
      No escribe NADA. Transforma, corre chequeos de sanidad y CALCULA
      TIR/duration/paridad por ON (precio de referencia: Trading.ONSnapshot
      si existe, sino precio TEST=100) usando la matemática REAL del motor.
      Imprime una tabla para que la mesa valide.

  (escribe)  python -m scripts.seed_curvas_ons --commit
      Upsert de los docs curva="on" en Trading.Curvas + borra los curva="on"
      que ya no estén en BondsMaster (sync limpio). BLOQUEADO en horario de
      rueda (13-20 UTC L-V, REGLA #4) salvo --force.

REGLA #2: el dry-run no asume — mide. La TIR sale de la misma función que el
motor (sin divergencia). REGLA #5: borrar una vez que el sync se estabilice
(o promover a jobs/ moviendo la math a quant/).
"""
from __future__ import annotations

import argparse
from datetime import datetime

from core.mongo import get_mongo_client, get_mongo_client_read

# Reuso de la matemática EXACTA del motor (sin copiar → sin divergencia).
from engines.curvas import (
    cargar_dias_habiles,
    cargar_mep_actual,
    convexity,
    fecha_flujo,
    macaulay_duration,
    monto_flujo,
    precio_soberano_a_usd,
    siguiente_dia_habil,
    xirr,
)

PRECIO_TEST = 100.0  # fallback si no hay precio de referencia (USD o ARS)


def _short_from_full(full: str) -> str:
    """'MERV - XMEV - YM40D - 24hs' → 'YM40D'."""
    if not full or not isinstance(full, str):
        return ""
    parts = [p.strip() for p in full.split(" - ")]
    return parts[2] if len(parts) >= 3 else ""


def _fecha_iso(raw) -> str | None:
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:10]
    return None


def construir_doc_on(bm: dict) -> dict | None:
    """BondsMaster doc → Trading.Curvas doc (curva='on'). None si no se puede."""
    asset = bm.get("asset")
    moneda = (bm.get("moneda_flujo") or "USD").upper()
    tickers = bm.get("tickers") or {}
    # Pata canónica según moneda de pago.
    ticker_full = tickers.get("USD") if moneda == "USD" else tickers.get("ARS")
    if not ticker_full:
        # sin la pata de su moneda, probamos la otra (mejor algo que nada)
        ticker_full = tickers.get("ARS") or tickers.get("USD")
    if not asset or not ticker_full:
        return None

    flujos = []
    for f in bm.get("flujos") or []:
        fd = fecha_flujo(f)
        if not fd:
            continue
        flujos.append({
            "fecha": fd.isoformat(),
            "amortizacion": float(f.get("amortizacion", 0) or 0),
            "interes": float(f.get("interes", 0) or 0),
            "valor_residual": float(f.get("valor_residual", 100) or 100),
        })

    return {
        "ticker": ticker_full,
        "ticker_corto": asset,
        "curva": "on",
        "moneda_flujo": moneda,
        "emisor": bm.get("emisor"),
        "tasa_cupon": bm.get("tasa_cupon"),
        "valor_nominal": 100,
        "fecha_vencimiento": _fecha_iso(bm.get("vencimiento")),
        "flujos": flujos,
    }


def calcular_on(doc: dict, precio: float, dias_habiles, mep) -> dict | None:
    """Réplica EXACTA de lo que será la rama 'on' del motor (usando los
    helpers reales). USD → precio a USD (D as-is / O ÷MEP) y math soberano;
    ARS → precio peso directo. Devuelve {TEA, duration, mod_duration,
    convexity, paridad} o None."""
    if not precio or precio <= 0:
        return None
    fecha_vto_str = doc.get("fecha_vencimiento")
    if not fecha_vto_str:
        return None
    try:
        from datetime import date
        fecha_vto = date.fromisoformat(fecha_vto_str[:10])
    except Exception:
        return None

    hoy = datetime.now().date()
    settlement_str = siguiente_dia_habil(dias_habiles, hoy)
    fecha_settlement = (
        date.fromisoformat(settlement_str) if settlement_str else hoy
    )
    if (fecha_vto - fecha_settlement).days <= 0:
        return None

    moneda = (doc.get("moneda_flujo") or "USD").upper()
    if moneda == "USD":
        precio_calc = precio_soberano_a_usd(precio, doc.get("ticker") or "", mep)
        if precio_calc is None:
            return None
    else:
        precio_calc = precio  # ARS: precio y flujos en pesos

    flujos_raw = doc.get("flujos") or []
    flujos_futuros = [
        (fecha_flujo(f), monto_flujo(f), f)
        for f in flujos_raw
        if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement and monto_flujo(f) > 0
    ]
    if not flujos_futuros:
        return None

    residual_vivo = float(flujos_futuros[0][2].get("valor_residual", 100) or 100)
    res = {}
    if residual_vivo > 0:
        res["paridad"] = round(precio_calc / residual_vivo * 100, 4)

    fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
    cf = [-precio_calc] + [m for _, m, _ in flujos_futuros]
    tea = xirr(fechas_dt, cf)
    # Rango amplio: USD ~6-12%, ARS puede ser alto. <50 corta error numérico.
    if tea is None or not (-0.5 < tea < 50):
        return res or None

    fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
    montos = [m for _, m, _ in flujos_futuros]
    base_dt = datetime.combine(fecha_settlement, datetime.min.time())
    dur = macaulay_duration(fechas_flujos_dt, montos, tea, base_dt)
    conv = convexity(fechas_flujos_dt, montos, tea, base_dt)
    res["TEA"] = round(tea, 6)
    if dur is not None:
        res["duration"] = dur
        if tea > -1:
            res["mod_duration"] = round(dur / (1 + tea), 4)
    if conv is not None:
        res["convexity"] = conv
    return res


def _precios_referencia(read_db) -> dict[str, float]:
    """ticker_full → mid de Trading.ONSnapshot (si la colección existe).
    Stale (abril) pero sirve para validar que la math converge a algo sano."""
    out: dict[str, float] = {}
    try:
        for s in read_db["Trading"]["ONSnapshot"].find(
                {}, {"_id": 0, "ticker": 1, "asset": 1, "px_bid": 1, "px_off": 1}):
            bid, off = s.get("px_bid"), s.get("px_off")
            mids = [p for p in (bid, off) if isinstance(p, (int, float)) and p > 0]
            if not mids:
                continue
            mid = sum(mids) / len(mids)
            if s.get("ticker"):
                out[s["ticker"]] = mid
            if s.get("asset"):
                out.setdefault(s["asset"], mid)
    except Exception:
        pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="escribe a Trading.Curvas")
    ap.add_argument("--force", action="store_true", help="permite --commit en rueda")
    ap.add_argument("--include-ars", action="store_true",
                    help="incluye en el commit las ONs ARS (default: solo USD — "
                         "las ARS necesitan definir convención de precio, fase 2)")
    args = ap.parse_args()

    read_cli = get_mongo_client_read()
    bonds = list(read_cli["Trading"]["BondsMaster"].find({}, {"_id": 0}))
    print(f"BondsMaster: {len(bonds)} ONs en el master\n")

    dias_habiles = cargar_dias_habiles(read_cli)
    mep = cargar_mep_actual(read_cli)
    precios_ref = _precios_referencia(read_cli)
    print(f"MEP actual: {mep} · precios de referencia (ONSnapshot): {len(precios_ref)}\n")

    docs, problemas = [], []
    print(f"{'asset':<8}{'emisor':<20}{'mon':<5}{'pata':<22}{'Σamort':>8}"
          f"{'precio':>10}{'src':>6}{'TEA%':>8}{'dur':>7}{'parid':>8}")
    print("-" * 110)
    for bm in bonds:
        doc = construir_doc_on(bm)
        if not doc:
            problemas.append(f"{bm.get('asset')}: sin asset/ticker")
            continue
        docs.append(doc)

        sum_amort = sum(f["amortizacion"] for f in doc["flujos"])
        flag_amort = "" if 95 <= sum_amort <= 105 else " ⚠"
        # Precio de referencia SOLO por el ticker de la pata canónica (no por
        # asset → el asset es el código O/pesos y metía precio peso-escala en
        # el cálculo USD). Para USD, banda de sanidad [10,300]: ONSnapshot
        # (abril) mezcla precios peso y USD; fuera de banda → TEST.
        precio = precios_ref.get(doc["ticker"])
        if doc["moneda_flujo"] == "USD" and precio is not None and not (10 <= precio <= 300):
            precio = None
        src = "real" if precio else "TEST"
        if not precio:
            precio = PRECIO_TEST

        calc = calcular_on(doc, precio, dias_habiles, mep) or {}
        tea = calc.get("TEA")
        tea_s = f"{tea*100:.2f}" if isinstance(tea, (int, float)) else "—"
        dur_s = f"{calc.get('duration'):.2f}" if calc.get("duration") else "—"
        par_s = f"{calc.get('paridad'):.1f}" if calc.get("paridad") else "—"
        pata = _short_from_full(doc["ticker"])
        print(f"{doc['ticker_corto']:<8}{(doc.get('emisor') or '')[:18]:<20}"
              f"{doc['moneda_flujo']:<5}{pata:<22}{sum_amort:>7.0f}{flag_amort:<1}"
              f"{precio:>10.2f}{src:>6}{tea_s:>8}{dur_s:>7}{par_s:>8}")
        if tea is None:
            problemas.append(f"{doc['ticker_corto']}: TIR no convergió a precio {precio:.1f}")

    # Scope del commit: por default solo USD (las ARS van a fase 2).
    docs_usd = [d for d in docs if d["moneda_flujo"] == "USD"]
    docs_ars = [d for d in docs if d["moneda_flujo"] != "USD"]
    docs_commit = docs if args.include_ars else docs_usd

    print("\n" + "=" * 60)
    print(f"Total transformadas: {len(docs)}  ·  USD: {len(docs_usd)}  ·  ARS: {len(docs_ars)}")
    print(f"A escribir (scope actual): {len(docs_commit)} docs curva='on'")
    if docs_ars and not args.include_ars:
        print(f"⏸  {len(docs_ars)} ARS DIFERIDAS (fase 2): "
              f"{[d['ticker_corto'] for d in docs_ars]}")
    if problemas:
        print(f"⚠️  {len(problemas)} con observaciones:")
        for p in problemas:
            print(f"   - {p}")

    if not args.commit:
        print("\n(DRY-RUN — no se escribió nada. Validá las TEA y corré con "
              "--commit cuando estés conforme. --include-ars para sumar las ARS.)")
        return

    # --- COMMIT ---------------------------------------------------------------
    ahora = datetime.utcnow()
    en_rueda = ahora.weekday() < 5 and 13 <= ahora.hour < 20
    if en_rueda and not args.force:
        print("\n⛔ Horario de rueda (13-20 UTC L-V). Reintentá fuera de rueda "
              "o con --force (REGLA #4).")
        return

    curvas = get_mongo_client()["Trading"]["Curvas"]
    from pymongo import UpdateOne
    ops = [UpdateOne({"ticker": d["ticker"]}, {"$set": d}, upsert=True) for d in docs_commit]
    tickers_ok = {d["ticker"] for d in docs_commit}
    if ops:
        curvas.bulk_write(ops, ordered=False)
    # Limpieza: borrar curva='on' que ya no estén en el scope (sync limpio).
    borrados = curvas.delete_many(
        {"curva": "on", "ticker": {"$nin": list(tickers_ok)}}).deleted_count
    print(f"\n✅ COMMIT: {len(ops)} upserts, {borrados} stale borrados en Trading.Curvas.")


if __name__ == "__main__":
    main()
