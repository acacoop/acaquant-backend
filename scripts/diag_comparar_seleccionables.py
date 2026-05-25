"""diag_comparar_seleccionables.py — Verificar el fix de listar_bonos_seleccionables.

listar_bonos_seleccionables (selector de Comparar Inversión) tarda ~974ms y es
87% CPU: llama listar_curva() por curva, que enriquece cada bono con precio/TEA/
duration/tc_breakeven desde MarketSnapshot — todo lo cual el selector DESCARTA
(solo usa ticker/vto/tipo/moneda/cer_fijado). El fix: leer Curvas directo + reusar
_bonos_cer_fijados(), sin el enrich.

Este diag corre AMBAS versiones (la actual de prod y la liviana propuesta),
compara el output campo por campo y mide el speedup. Si dice IDÉNTICO, el fix
es seguro de aplicar. Si hay diferencias, las lista para corregir ANTES de tocar
prod (no se aplica nada a ciegas).

    python -m scripts.diag_comparar_seleccionables
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from api.db import get_db_trading
from api.services.comparar_inversion import (
    _CURVAS_SOPORTADAS,
    _moneda_de,
    listar_bonos_seleccionables,  # versión ACTUAL (prod, pesada)
)
from api.services.renta_fija import _bonos_cer_fijados


# ── Versión liviana propuesta (idéntico output, sin enrich de MarketSnapshot) ──
def _meses_al_vto(vto_raw, ahora: datetime) -> float | None:
    if not vto_raw:
        return None
    try:
        if isinstance(vto_raw, datetime):
            vto = vto_raw if vto_raw.tzinfo else vto_raw.replace(tzinfo=UTC)
        else:
            vto = datetime.fromisoformat(str(vto_raw)[:10]).replace(tzinfo=UTC)
    except Exception:
        return None
    return round((vto - ahora).days / 30.44, 1)


def _docs_curva(db, curva: str, fijados: set[str]) -> list[dict]:
    proj = {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
            "fecha_vencimiento": 1, "curva": 1}
    if curva == "cer":
        filtro: dict = {"curva": "cer"}
        if fijados:
            filtro["ticker"] = {"$nin": list(fijados)}
    elif curva == "tasa_fija":
        filtro = ({"$or": [{"curva": "tasa_fija"},
                           {"curva": "cer", "ticker": {"$in": list(fijados)}}]}
                  if fijados else {"curva": "tasa_fija"})
    else:
        filtro = {"curva": curva}
    return list(db["Curvas"].find(filtro, proj))


def listar_bonos_seleccionables_light() -> list[dict]:
    db = get_db_trading()
    fijados = _bonos_cer_fijados()
    ahora = datetime.now(UTC)
    out: list[dict] = []
    for curva in _CURVAS_SOPORTADAS:
        for d in _docs_curva(db, curva, fijados):
            vto = d.get("fecha_vencimiento")
            meses = _meses_al_vto(vto, ahora)
            if meses is None:  # sin vto o no parseable → listar_curva también lo excluye
                continue
            ticker_corto = d.get("ticker_corto") or d.get("ticker")
            out.append({
                "id": f"curvas:{ticker_corto}",
                "ticker": d.get("ticker"),
                "ticker_corto": ticker_corto,
                "label": ticker_corto,
                "curva": curva,
                "tipo": d.get("tipo"),
                "moneda": _moneda_de(curva),
                "vencimiento": str(vto)[:10] if vto else None,
                "meses_al_vto": meses,
                "cer_fijado": (curva == "tasa_fija" and d.get("curva") == "cer"),
            })
    out.sort(key=lambda x: (x["moneda"], x.get("vencimiento") or "9999"))
    return out


# Campos que el frontend del selector consume → los que deben coincidir.
_KEYS = ("id", "ticker", "ticker_corto", "label", "curva", "tipo",
         "moneda", "vencimiento", "meses_al_vto", "cer_fijado")


def _norm(rows: list[dict]) -> dict[str, dict]:
    return {r["id"]: {k: r.get(k) for k in _KEYS} for r in rows}


def main() -> None:
    t0 = time.perf_counter()
    old = listar_bonos_seleccionables()
    t_old = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    new = listar_bonos_seleccionables_light()
    t_new = (time.perf_counter() - t1) * 1000

    mo, mn = _norm(old), _norm(new)
    solo_old = set(mo) - set(mn)
    solo_new = set(mn) - set(mo)
    comunes = set(mo) & set(mn)
    difs = []
    for i in sorted(comunes):
        for k in _KEYS:
            if mo[i][k] != mn[i][k]:
                difs.append((i, k, mo[i][k], mn[i][k]))

    print("=" * 70)
    print(f"ACTUAL (prod):  {len(old):>4} bonos   {t_old:8.1f} ms")
    print(f"LIVIANA:        {len(new):>4} bonos   {t_new:8.1f} ms")
    if t_new > 0:
        print(f"speedup:        {t_old / t_new:.1f}x más rápido")
    print("=" * 70)

    if not solo_old and not solo_new and not difs:
        print("✅ IDÉNTICO — mismos bonos, mismos campos. El fix es seguro de aplicar.")
        return

    print("⚠️  DIFERENCIAS (NO aplicar hasta resolver):")
    if solo_old:
        print(f"  solo en ACTUAL ({len(solo_old)}): {sorted(solo_old)[:10]}")
    if solo_new:
        print(f"  solo en LIVIANA ({len(solo_new)}): {sorted(solo_new)[:10]}")
    for i, k, vo, vn in difs[:20]:
        print(f"  {i}.{k}: actual={vo!r}  liviana={vn!r}")
    if len(difs) > 20:
        print(f"  … y {len(difs) - 20} diferencias más")


if __name__ == "__main__":
    main()
