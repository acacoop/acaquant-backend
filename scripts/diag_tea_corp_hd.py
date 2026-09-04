"""`scripts/diag_tea_corp_hd.py` — TNA/TEA nuestras vs las de 1816, para los
CORPORATIVOS HARD DOLAR de la vista RENTA FIJA.

Read-only. No escribe una sola fila.

Salida: una fila por ticker, cuatro columnas. Nada más.

    TICKER    TNA MIA   TNA 1816   TEA MIA   TEA 1816

Uso:
    python -m scripts.diag_tea_corp_hd          # la tabla
    python -m scripts.diag_tea_corp_hd --dry    # universo y costo, sin pegarle a 1816
    python -m scripts.diag_tea_corp_hd --fecha 2026-09-03

Las cuatro decisiones que hacen que la comparación sea comparación, y no dos
números al lado:

1. **El universo sale de la VISTA** (`curvas_vista.get_curvas_vista()`), pill
   `hard_dolar` + `emisor_tipo='corporativo'`. Si este script definiera por su
   cuenta qué es un corporativo hard dólar, auditaría un universo que no es el
   que la mesa mira.

2. **`moneda='mep'` al pedirle a 1816, y NO el default.** El spec de 1816 dice
   que para instrumentos pagaderos en otra moneda las cotizaciones se dividen
   por **CCL**, y nuestro motor divide por **MEP**
   (`engines/curvas.py::precio_soberano_a_usd`, que es la que usa la rama `on`
   para `moneda_flujo='USD'`). Pedir el default y comparar tasas es comparar dos
   tipos de cambio distintos — eso fueron los 202 bps de GD46, no la fórmula.

3. **La TNA MIA se DERIVA de la TEA con la convención de la casa** (TEM×12,
   `quant.tasas.tna_desde_tea`), que es exactamente lo que la pantalla muestra en
   esta pill: el snapshot no publica TNA, y el backend solo la manda calculada
   para `tasa_fija` (§ paso 23). Cualquier otra fórmula acá mostraría un número
   que no es el que se está mirando.

4. **La TEA MIA es la del MOTOR.** Si la fila de la vista trae la tasa de 1816
   (el fallback de `agente/tasa_1816.py`, que entra solo cuando el motor no
   calculó), la celda MIA queda **vacía**: comparar 1816 contra 1816 daría cero
   de diferencia y parecería que está todo bien.

Costo en créditos: **tickers × 2** (`tea` y `tna`). `--dry` lo dice sin gastar.
"""
from __future__ import annotations

import argparse
import sys

from quant.tasas import tna_desde_tea

# Cuántos tickers entran por llamada. **La API TRUNCA en 50 y no avisa**
# (`core/mercado_1816.indicadores`: `list(tickers)[:50]`), así que sin trocear,
# del 51 en adelante las celdas de 1816 saldrían vacías y se leerían como «1816
# no lo tiene».
LOTE = 50

def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def universo() -> list[dict]:
    """Los corporativos de la pill HARD DOLAR, tal como los sirve la vista.

    Una fila por bono: en esta pill no hay duales, pero el dedupe por ticker va
    igual — un bono repetido pediría dos veces el mismo crédito.
    """
    from api.services.curvas_vista import get_curvas_vista
    v = get_curvas_vista() or {}
    vistos, out = set(), []
    for b in (v.get("bonos") or []):
        if b.get("pill") != "hard_dolar" or b.get("emisor_tipo") != "corporativo":
            continue
        tk = (b.get("ticker_corto") or "").strip().upper()
        if not tk or tk in vistos:
            continue
        vistos.add(tk)
        out.append(b)
    return sorted(out, key=lambda b: b["ticker_corto"].upper())


def pedir_1816(tickers: list[str], fecha: str | None) -> dict[str, dict]:
    """`nuestro ticker → {tea, tna}` de 1816, en lotes de 50.

    La rueda se resuelve UNA vez (con el primer lote que traiga datos) y se le
    pasa fija a los demás: si cada lote retrocediera por su cuenta, media tabla
    podría quedar de una rueda y media de otra sin que se note.
    """
    from core import mercado_1816
    if not mercado_1816.disponible():
        print("MERCADO_1816_API_KEY no está configurada.", file=sys.stderr)
        return {}

    g = mercado_1816.grafias_de(tickers, "fija")
    pedidos = sorted(g)
    out: dict[str, dict] = {}
    for i in range(0, len(pedidos), LOTE):
        lote = pedidos[i:i + LOTE]
        try:
            resp = mercado_1816.indicadores_vigentes(
                lote, ["tea", "tna"], fecha=fecha, moneda="mep") or {}
        except Exception as e:
            print(f"1816 no contestó el lote {i // LOTE + 1}: {e}", file=sys.stderr)
            continue
        fecha = fecha or resp.get("fechaOperacion")
        for grafia, v in (resp.get("instrumentos") or {}).items():
            nuestro = g.get(grafia)
            if not nuestro or not v:
                continue
            # Gana la grafía que trajo TEA (el alias pide la misma pata dos veces).
            if nuestro not in out or (out[nuestro].get("tea") is None
                                      and v.get("tea") is not None):
                out[nuestro] = {"tea": _f(v.get("tea")), "tna": _f(v.get("tna"))}
    return out


def _pct(v: float | None) -> str:
    return "--" if v is None else f"{v * 100:.2f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fecha", help="rueda a pedirle a 1816 (YYYY-MM-DD)")
    ap.add_argument("--dry", action="store_true",
                    help="universo y costo en créditos, sin pegarle a 1816")
    args = ap.parse_args()

    bonos = universo()
    if not bonos:
        print("No hay corporativos en la pill HARD DOLAR.", file=sys.stderr)
        return 1

    if args.dry:
        print(f"{len(bonos)} tickers × 2 campos = {len(bonos) * 2} créditos")
        for b in bonos:
            print(f"  {b['ticker_corto']}")
        return 0

    d1816 = pedir_1816([b["ticker_corto"] for b in bonos], args.fecha)

    print(f"{'TICKER':<10}{'TNA MIA':>10}{'TNA 1816':>11}"
          f"{'TEA MIA':>10}{'TEA 1816':>11}")
    for b in bonos:
        tk = b["ticker_corto"]
        # La TEA del MOTOR: si la fila viene con la de 1816 (fallback del agente,
        # que entra solo cuando el motor no calculó), acá va vacío.
        mia = None if b.get("tea_fuente") == "1816" else _f((b.get("metrics") or {}).get("TEA"))
        suyo = d1816.get(tk) or {}
        print(f"{tk:<10}{_pct(tna_desde_tea(mia)):>10}{_pct(suyo.get('tna')):>11}"
              f"{_pct(mia):>10}{_pct(suyo.get('tea')):>11}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
