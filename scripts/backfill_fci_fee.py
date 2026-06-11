"""backfill_fci_fee.py — carga masiva del fee de administración de FCI a Valuaciones.Assets.

Lee un Excel/CSV con 2 columnas (Fondo | Honorarios Adm. SG) y rellena `FEE_ADMIN`
en los assets CARTERA=FCI, matcheando por nombre de fondo (== `unidad`).

Convención (confirmada): el honorario viene en PORCENTAJE con coma decimal
("3,8200" = 3,82 %) y se guarda como FRACCIÓN: 0.0382. Así la comisión sale directa
(saldo × FEE_ADMIN). Ver [[project_referidos_comision_fci]].

Seguro (REGLA #4):
  * DRY-RUN por default — NO escribe; muestra match / TUS FCI sin fee / inválidos.
  * Idempotente: UPDATE por `unidad` (sin upsert) → re-correrlo no duplica ni crea.
  * Scopeado a CARTERA FCI; valida 0<fee<=1 (descarta valores absurdos por error de parseo).
  * `--out archivo.txt` vuelca el reporte a un archivo (para revisar / pushear / compartir).

El CSV suele traer TODO el universo CAFCI (miles) → la mayoría cae en "no encontrados"
(fondos que no operás): es ruido esperado. Lo que importa es "TUS FCI SIN FEE".

Uso:
    python -m scripts.backfill_fci_fee --file scripts/fci_fees.csv                       # DRY-RUN
    python -m scripts.backfill_fci_fee --file scripts/fci_fees.csv --out scripts/rep.txt # + archivo
    python -m scripts.backfill_fci_fee --file scripts/fci_fees.csv --apply               # escribe
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import UTC, datetime

import pandas as pd

from core.mongo import get_mongo_client, get_mongo_client_read

_FCI_CARTERAS = ["FCI", "CARTERA FCI"]


def _norm(s) -> str:
    """Normaliza para matchear: sin acentos, minúsculas, 'fci' suelto fuera, solo
    alfanumérico (unifica 'X Clase B' vs 'X - Clase B', dobles espacios, etc.)."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"\bfci\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _fund_name(unidad: str) -> str:
    """Nombre del fondo dentro del `unidad` de Assets, que viene como
    '[<id>] CAFCI<cod>-<id> - <NOMBRE>'. Saca el prefijo [id] y el bloque CAFCI
    (todo hasta el primer ' - '). El Excel trae solo el <NOMBRE> limpio."""
    s = re.sub(r"^\s*\[\d+\]\s*", "", str(unidad))
    if " - " in s:
        s = s.split(" - ", 1)[1]
    return s


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
    ap.add_argument("--out", help="vuelca el reporte a este archivo (UTF-8)")
    args = ap.parse_args()

    report: list[str] = []

    def emit(s: str = "") -> None:
        report.append(s)
        print(s)

    def flush() -> None:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write("\n".join(report) + "\n")
            print(f"\n→ reporte escrito en {args.out}")

    # ── 1) Leer el archivo (todo string para controlar el parseo de la coma) ──
    if args.file.lower().endswith(".csv"):
        df = pd.read_csv(args.file, dtype=str, sep=None, engine="python")
    else:
        df = pd.read_excel(args.file, sheet_name=args.sheet, dtype=str)
    cols = list(df.columns)
    if len(cols) < 2:
        emit(f"El archivo tiene {len(cols)} columna(s); necesito al menos 2 (Fondo, Honorarios).")
        flush()
        return
    fondo_col = next((c for c in cols if "fondo" in str(c).lower()), cols[0])
    hon_col = next((c for c in cols if "honor" in str(c).lower()), cols[1])
    emit(f"=== backfill FCI fee — archivo {args.file} ===")
    emit(f"    Fondo='{fondo_col}'  Honorario='{hon_col}'  {'APPLY' if args.apply else 'DRY-RUN'}")
    emit("")

    # ── 2) Universo FCI en Assets (norm(unidad) → unidad original) ─────────────
    col = (get_mongo_client() if args.apply else get_mongo_client_read())["Valuaciones"]["Assets"]
    fci = {}
    for d in col.find({"CARTERA": {"$in": _FCI_CARTERAS}}, {"_id": 0, "unidad": 1, "FEE_ADMIN": 1}):
        u = d.get("unidad")
        if u:
            fci[_norm(_fund_name(u))] = {"unidad": u, "fee_actual": d.get("FEE_ADMIN")}
    emit(f"[Assets] {len(fci)} fondos CARTERA FCI en el maestro\n")

    # ── 3) Matchear filas del archivo ──────────────────────────────────────────
    matched, no_encontrados, invalidos = [], [], []
    vistos: set[str] = set()
    for _, row in df.iterrows():
        fondo = str(row[fondo_col] or "").strip()
        nf = _norm(fondo)
        if not nf or nf == "fondo" or nf in vistos:
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

    # ── 4) Reporte (enfocado) ──────────────────────────────────────────────────
    emit(f"── MATCHEAN ({len(matched)}) — fondo → fee% → fracción (fee actual) ──")
    for m in sorted(matched, key=lambda x: x["unidad"]):
        cambia = "" if m["fee_actual"] == m["fraccion"] else f"  (antes: {m['fee_actual']})"
        emit(f"    {m['unidad'][:48]:<48} {m['pct']:>7.4f}%  → {m['fraccion']}{cambia}")
    emit("")

    # TUS FCI que NO recibieron fee — lo accionable (diferencia de nombre).
    en_excel = {_norm(m["unidad"]) for m in matched}
    sin_fee = sorted(v["unidad"] for k, v in fci.items() if k not in en_excel)
    emit(f"── ⚠️ TUS FCI (Assets) SIN FEE ({len(sin_fee)}) — revisá el nombre vs el Excel ──")
    for u in sin_fee:
        emit(f"    {u}")
    emit("")

    if invalidos:
        emit(f"── ⚠️ HONORARIO INVÁLIDO en el Excel ({len(invalidos)}) ──")
        for f, v in invalidos[:30]:
            emit(f"    {f[:50]:<50} {v}")
        emit("")

    # Ruido esperado: fondos del Excel que no están en tu maestro (no los operás).
    emit(f"── (info) fondos del Excel NO en tu maestro: {len(no_encontrados)} (ruido, ignorar) ──")
    for f, p in no_encontrados[:15]:
        emit(f"    {f[:50]:<50} {p:>7.4f}%")
    if len(no_encontrados) > 15:
        emit(f"    … (+{len(no_encontrados) - 15} más)")
    emit("")

    # ── 5) Aplicar ─────────────────────────────────────────────────────────────
    if not args.apply:
        emit(f"DRY-RUN: {len(matched)} fondos se actualizarían. Re-corré con --apply para escribir.")
        flush()
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
    emit(f"✅ APPLY: {n} fondos actualizados ({len(matched)} matcheados).")
    flush()


if __name__ == "__main__":
    main()
