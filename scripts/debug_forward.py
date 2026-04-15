"""
debug_forward.py — Muestra paso a paso cómo se calcula la forward TX26 vs TZX26.

Uso:
    python debug_forward.py
"""

from core.mongo import get_mongo_client


def main():
    client = get_mongo_client()
    db = client["Trading"]

    # ── 1. Leer instrumentos de Curvas ────────────────────────────────────────
    curvas = {d["ticker"]: d for d in db["Curvas"].find({})}
    print("=== INSTRUMENTOS EN Trading.Curvas ===")
    for _, inst in curvas.items():
        print(f"  {inst.get('ticker_corto'):10} | curva={inst.get('curva'):10} | vto={inst.get('fecha_vencimiento','?')[:10]}")

    # ── 2. Buscar TX26 y TZX26 ────────────────────────────────────────────────
    tx26  = next((v for v in curvas.values() if v.get("ticker_corto") == "TX26"),  None)
    tzx26 = next((v for v in curvas.values() if v.get("ticker_corto") == "TZX26"), None)

    if not tx26:
        print("\n❌ TX26 no encontrado en Trading.Curvas")
        return
    if not tzx26:
        print("\n❌ TZX26 no encontrado en Trading.Curvas")
        return

    print("\n=== TX26 ===")
    print(f"  ticker:    {tx26['ticker']}")
    print(f"  curva:     {tx26.get('curva')}")
    print(f"  vto:       {tx26.get('fecha_vencimiento','?')[:10]}")

    print("\n=== TZX26 ===")
    print(f"  ticker:    {tzx26['ticker']}")
    print(f"  curva:     {tzx26.get('curva')}")
    print(f"  vto:       {tzx26.get('fecha_vencimiento','?')[:10]}")

    # ── 3. Última TEA de cada uno desde TimeSales ─────────────────────────────
    def ultima_tea(ticker):
        doc = db["TimeSales"].find_one(
            {"ticker": ticker, "TEA": {"$exists": True}, "duration": {"$exists": True}},
            sort=[("timestamp", -1)]
        )
        if not doc:
            return None, None, None
        return doc.get("TEA"), doc.get("duration"), doc.get("timestamp")

    tea_tx26,  dur_tx26,  ts_tx26  = ultima_tea(tx26["ticker"])
    tea_tzx26, dur_tzx26, ts_tzx26 = ultima_tea(tzx26["ticker"])

    print("\n=== TEA y Duration desde TimeSales ===")
    print(f"  TX26:  TEA={tea_tx26}  | duration={dur_tx26}  | timestamp={ts_tx26}")
    print(f"  TZX26: TEA={tea_tzx26} | duration={dur_tzx26} | timestamp={ts_tzx26}")

    if tea_tx26 is None or tea_tzx26 is None:
        print("\n❌ Falta TEA en alguno de los dos. No se puede calcular la forward.")
        return

    t_tx26  = dur_tx26
    t_tzx26 = dur_tzx26

    print("\n=== Plazos (usando duration, NO días al vencimiento) ===")
    print(f"  TX26:  duration={t_tx26:.6f} años")
    print(f"  TZX26: duration={t_tzx26:.6f} años")

    # ── 5. Identificar cuál es el corto y cuál el largo ───────────────────────
    if t_tx26 < t_tzx26:
        t_a, tea_a, nombre_a = t_tx26,  tea_tx26,  "TX26"
        t_b, tea_b, nombre_b = t_tzx26, tea_tzx26, "TZX26"
    else:
        t_a, tea_a, nombre_a = t_tzx26, tea_tzx26, "TZX26"
        t_b, tea_b, nombre_b = t_tx26,  tea_tx26,  "TX26"

    print("\n=== Orden (A=corto, B=largo) ===")
    print(f"  A (corto): {nombre_a} | TEA={tea_a:.6f} | t={t_a:.6f}")
    print(f"  B (largo): {nombre_b} | TEA={tea_b:.6f} | t={t_b:.6f}")

    # ── 6. Fórmula paso a paso ────────────────────────────────────────────────
    dt = t_b - t_a
    num = (1 + tea_b) ** t_b
    den = (1 + tea_a) ** t_a
    cociente = num / den
    forward = cociente ** (1 / dt) - 1

    print(f"\n=== Cálculo forward ({nombre_a} → {nombre_b}) ===")
    print(f"  dt          = t_b - t_a          = {dt:.6f}")
    print(f"  (1+TEA_b)^t_b = (1+{tea_b:.6f})^{t_b:.6f} = {num:.8f}")
    print(f"  (1+TEA_a)^t_a = (1+{tea_a:.6f})^{t_a:.6f} = {den:.8f}")
    print(f"  cociente    = num/den             = {cociente:.8f}")
    print(f"  forward     = cociente^(1/dt) - 1 = {forward:.6f}  ({forward*100:.4f}%)")

    # ── 7. Comparar con lo guardado en ForwardsLive ───────────────────────────
    print("\n=== Valor en ForwardsLive ===")
    for curva_nombre in set(v.get("curva") for v in [tx26, tzx26]):
        doc_live = db["ForwardsLive"].find_one({"curva": curva_nombre})
        if not doc_live:
            print(f"  Sin doc en ForwardsLive para curva={curva_nombre}")
            continue
        matrix = doc_live.get("matrix", {})
        val_ab = matrix.get(nombre_b, {}).get(nombre_a)
        val_ba = matrix.get(nombre_a, {}).get(nombre_b)
        print(f"  curva={curva_nombre}")
        print(f"  matrix[{nombre_b}][{nombre_a}] = {val_ab}")
        print(f"  matrix[{nombre_a}][{nombre_b}] = {val_ba}")
        print(f"  updated_at = {doc_live.get('updated_at')}")

    client.close()


if __name__ == "__main__":
    main()
