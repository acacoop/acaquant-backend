"""backfill_fci_fee.py — carga masiva del fee de administración de FCI a Valuaciones.Assets.

Lee un Excel/CSV con 2 columnas (Fondo | Honorarios Adm. SG) y rellena `FEE_ADMIN`
en los assets CARTERA=FCI, matcheando por nombre de fondo (== `unidad`).

Convención (confirmada): el honorario viene en PORCENTAJE con coma decimal
("3,8200" = 3,82 %) y se guarda como FRACCIÓN: 0.0382. Así la comisión sale directa
(saldo × FEE_ADMIN). Ver [[project_referidos_comision_fci]].

Seguro (REGLA #4):
  * DRY-RUN por default — NO escribe; muestra match / no-match / FCI sin fee.
  * Idempotente: UPDATE por `unidad` (sin upsert) → re-correrlo no duplica ni crea.
  * Scopeado a CARTERA FCI; valida 0<fee<=1 (descarta valores absurdos por error de parseo).

Uso:
    python -m scripts.backfill_fci_fee --file ruta/al/excel.xlsx            # DRY-RUN
    python -m scripts.backfill_fci_fee --file ruta/al/excel.xlsx --apply    # escribe
    python -m scripts.backfill_fci_fee --file ruta.csv --sheet "Hoja1"
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

import pandas as pd

from core.mongo import get_mongo_client, get_mongo_client_read

_FCI_CARTERAS = ["FCI", "CARTERA FCI"]


def _norm(s) -> str:
    """Normaliza un nombre de fondo para matchear: minúsculas, espacios colapsados."""
    return " ".join(str(s or "").strip().lower().split())


def _parse_pct(raw) -> float | None:
    """'3,8200' / '3.82' / 3.82 → 3.82 (porcentaje). None si no parsea."""
    s = str(raw).strip().replace("%", "").replace(" ", "")
    if not s or s.lower() in ("nan", "none"):
        return None
    s = s.replace(",", ".")          # coma decimal → punto (no hay miles en un fee < 100)
    try:
        return float(s)
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Excel (.xlsx) o CSV con columnas Fondo | Honorarios")
    ap.add_argument("--sheet", default=0, help="hoja del Excel (nombre o índice, default primera)")
    ap.add_argument("--apply", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    # ── 1) Leer el archivo (todo como string para controlar el parseo de la coma) ──
    if args.file.lower().endswith(".csv"):
        df = pd.read_csv(args.file, dtype=str, sep=None, engine="python")
    else:
        df = pd.read_excel(args.file, sheet_name=args.sheet, dtype=str)
    cols = list(df.columns)
    if len(cols) < 2:
        print(f"⚠️ El archivo tiene {len(cols)} columna(s); necesito al menos 2 (Fondo, Honorarios).")
        return
    fondo_col = next((c for c in cols if "fondo" in str(c).lower()), cols[0])
    hon_col = next((c for c in cols if "honor" in str(c).lower()), cols[1])
    print(f"=== backfill FCI fee — archivo {args.file} ===")
    print(f"    columna Fondo='{fondo_col}'  ·  columna Honorario='{hon_col}'  ·  {'APPLY' if args.apply else 'DRY-RUN'}\n")

    # ── 2) Universo FCI en Assets (norm(unidad) → unidad original) ─────────────
    col = (get_mongo_client() if args.apply else get_mongo_client_read())["Valuaciones"]["Assets"]
    fci = {}
    for d in col.find({"CARTERA": {"$in": _FCI_CARTERAS}}, {"_id": 0, "unidad": 1, "FEE_ADMIN": 1}):
        u = d.get("unidad")
        if u:
            fci[_norm(u)] = {"unidad": u, "fee_actual": d.get("FEE_ADMIN")}
    print(f"[Assets] {len(fci)} fondos CARTERA FCI en el maestro\n")

    # ── 3) Matchear filas del Excel ────────────────────────────────────────────
    matched, no_encontrados, invalidos = [], [], []
    vistos: set[str] = set()
    for _, row in df.iterrows():
        fondo = str(row[fondo_col] or "").strip()
        if not fondo or _norm(fondo) in ("", "fondo"):
            continue
        nf = _norm(fondo)
        if nf in vistos:
            continue
        vistos.add(nf)
        pct = _parse_pct(row[hon_col])
        if pct is None:
            invalidos.append((fondo, row[hon_col]))
            continue
        fraccion = round(pct / 100.0, 6)
        if not (0 < fraccion <= 1):
            invalidos.append((fondo, f"{row[hon_col]} → {fraccion} (fuera de 0<fee<=1)"))
            continue
        hit = fci.get(nf)
        if not hit:
            no_encontrados.append((fondo, pct))
            continue
        matched.append({"unidad": hit["unidad"], "pct": pct, "fraccion": fraccion,
                        "fee_actual": hit["fee_actual"]})

    # ── 4) Reporte ─────────────────────────────────────────────────────────────
    print(f"── MATCHEAN ({len(matched)}) — fondo → fee% → fracción (fee actual) ──")
    for m in matched[:60]:
        cambia = "" if m["fee_actual"] == m["fraccion"] else f"  (antes: {m['fee_actual']})"
        print(f"    {m['unidad'][:46]:<46} {m['pct']:>7.4f}%  → {m['fraccion']}{cambia}")
    if len(matched) > 60:
        print(f"    … (+{len(matched) - 60} más)")
    print()

    if no_encontrados:
        print(f"── ⚠️ NO ENCONTRADOS en Assets ({len(no_encontrados)}) — revisá el nombre en el Excel ──")
        for f, p in no_encontrados:
            print(f"    {f[:50]:<50} {p:>7.4f}%")
        print()
    if invalidos:
        print(f"── ⚠️ HONORARIO INVÁLIDO ({len(invalidos)}) ──")
        for f, v in invalidos:
            print(f"    {f[:50]:<50} {v}")
        print()

    en_excel = {_norm(m["unidad"]) for m in matched}
    sin_fee = [v["unidad"] for k, v in fci.items() if k not in en_excel]
    if sin_fee:
        print(f"── FCI en Assets SIN fee en el Excel ({len(sin_fee)}) ──")
        for u in sorted(sin_fee)[:40]:
            print(f"    {u}")
        if len(sin_fee) > 40:
            print(f"    … (+{len(sin_fee) - 40} más)")
        print()

    # ── 5) Aplicar ─────────────────────────────────────────────────────────────
    if not args.apply:
        print(f"DRY-RUN: {len(matched)} fondos se actualizarían. Re-corré con --apply para escribir.")
        return
    now = datetime.now(UTC)
    n = 0
    for m in matched:
        res = col.update_one(
            {"unidad": m["unidad"]},
            {"$set": {"FEE_ADMIN": m["fraccion"], "actualizado_por": "backfill_fci_fee",
                      "actualizado_at": now}},
        )
        n += res.modified_count
    print(f"✅ APPLY: {n} fondos actualizados ({len(matched)} matcheados).")


if __name__ == "__main__":
    main()
