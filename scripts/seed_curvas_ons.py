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
      Llama a api.services.ons.sync_ons_to_curvas() → upsert de TODAS las ONs
      como curva="on_<sector>" + borra las stale (sync limpio). Mismo sync que
      usa el panel Manager. BLOQUEADO en rueda (13-20 UTC L-V) salvo --force.

NOTA: el transform y el sync viven en api/services/ons.py (compartidos con
Manager). Este CLI es respaldo + validación (la tabla dry-run con la math real
del motor). REGLA #2: el dry-run no asume, mide.
"""
from __future__ import annotations

import argparse
from datetime import datetime

from api.services.ons import bondmaster_to_curva_doc, sync_ons_to_curvas
from core.mongo import get_mongo_client_read

# Reuso de la matemática EXACTA del motor (sin copiar → sin divergencia).
from engines.curvas import (
    cargar_a3500_actual,
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

# El transform BondsMaster→Curvas y el sync viven en api/services/ons.py
# (compartidos con el panel Manager → sin divergencia ni conflicto de scope).
# Acá solo VALIDAMOS (dry-run con la math real del motor) y disparamos el sync.
construir_doc_on = bondmaster_to_curva_doc


def calcular_on(doc: dict, precio: float, dias_habiles, mep, tc_a3500=None) -> dict | None:
    """Réplica EXACTA de la rama 'on' del motor. USD → precio a USD (D as-is /
    O ÷MEP); DL (dólar-linked) → precio peso ÷ A3500; ARS → peso directo.
    Devuelve {TEA, duration, mod_duration, convexity, paridad} o None."""
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
    elif moneda == "DL":
        if not tc_a3500 or tc_a3500 <= 0:
            return None
        precio_calc = precio / tc_a3500
    else:
        precio_calc = precio  # ARS peso nativo

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
    ap.add_argument("--commit", action="store_true", help="sincroniza a Trading.Curvas")
    ap.add_argument("--force", action="store_true", help="permite --commit en rueda")
    args = ap.parse_args()

    read_cli = get_mongo_client_read()
    bonds = list(read_cli["Trading"]["BondsMaster"].find({}, {"_id": 0}))
    print(f"BondsMaster: {len(bonds)} ONs en el master\n")

    dias_habiles = cargar_dias_habiles(read_cli)
    mep = cargar_mep_actual(read_cli)
    tc_a3500 = cargar_a3500_actual(read_cli)
    precios_ref = _precios_referencia(read_cli)
    print(f"MEP: {mep} · A3500: {tc_a3500} · precios ref (ONSnapshot): {len(precios_ref)}\n")

    docs, problemas = [], []
    print(f"{'asset':<8}{'emisor':<20}{'mon':<5}{'curva(sector)':<16}{'Σamort':>8}"
          f"{'precio':>10}{'src':>6}{'TEA%':>8}{'dur':>7}{'parid':>8}")
    print("-" * 104)
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

        calc = calcular_on(doc, precio, dias_habiles, mep, tc_a3500) or {}
        tea = calc.get("TEA")
        tea_s = f"{tea*100:.2f}" if isinstance(tea, (int, float)) else "—"
        dur_s = f"{calc.get('duration'):.2f}" if calc.get("duration") else "—"
        par_s = f"{calc.get('paridad'):.1f}" if calc.get("paridad") else "—"
        print(f"{doc['ticker_corto']:<8}{(doc.get('emisor') or '')[:18]:<20}"
              f"{doc['moneda_flujo']:<5}{doc['curva']:<16}{sum_amort:>7.0f}{flag_amort:<1}"
              f"{precio:>10.2f}{src:>6}{tea_s:>8}{dur_s:>7}{par_s:>8}")
        if tea is None:
            problemas.append(f"{doc['ticker_corto']}: TIR no convergió a precio {precio:.1f}")

    print("\n" + "=" * 60)
    print(f"Transformadas OK: {len(docs)} ONs")
    if problemas:
        print(f"⚠️  {len(problemas)} con observaciones:")
        for p in problemas:
            print(f"   - {p}")

    if not args.commit:
        print("\n(DRY-RUN — no se escribió nada. Validá las TEA y corré con "
              "--commit cuando estés conforme.)")
        return

    # --- COMMIT: sincroniza TODAS las ONs (vía el servicio compartido) --------
    ahora = datetime.utcnow()
    en_rueda = ahora.weekday() < 5 and 13 <= ahora.hour < 20
    if en_rueda and not args.force:
        print("\n⛔ Horario de rueda (13-20 UTC L-V). Reintentá fuera de rueda "
              "o con --force (REGLA #4).")
        return

    res = sync_ons_to_curvas()
    print(f"\n✅ COMMIT: {res['sincronizadas']} sincronizadas, "
          f"{res['borradas']} stale borradas en Trading.Curvas.")


if __name__ == "__main__":
    main()
