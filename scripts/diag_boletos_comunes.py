"""diag_boletos_comunes.py — ¿cuántos boletos de CashFlow.Flujo están en
CashFlow.NegocioMovimientos? Objetivo: 100% de Flujo cubierto.

Compara el identificador de boleto de los dos lados:
  Flujo.boleto                → ej. 2026069919  (número)
  NegocioMovimientos.comprobante → ej. "BOL 2026069919"  (string con prefijo)

OJO: por el prefijo "BOL " un match crudo da 0% FALSO. El script normaliza a
SOLO DÍGITOS de ambos lados y, para que no haya espejismos, imprime muestras
crudas + el match crudo al lado del normalizado. Si los dígitos coinciden, el
match normalizado sube; si NO coinciden ni normalizados, es que los dos
endpoints de Aunesa usan numeraciones distintas (otro problema).

Lo que importa es la columna "Solo en Flujo" → esos son los boletos que la
vista vieja tiene y la nueva NO. Eso hay que llevar a 0.

Read-only. NO modifica nada.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_boletos_comunes
    venv/bin/python -m scripts.diag_boletos_comunes --ejemplos 30
"""
from __future__ import annotations

import argparse
import re

from core.mongo import get_mongo_client_read

_DIGITS = re.compile(r"\d+")


def _norm(v) -> str | None:
    """Solo los dígitos. 'BOL 2026069919' → '2026069919'; 2026069919 → '2026069919'."""
    if v is None:
        return None
    grupos = _DIGITS.findall(str(v))
    return "".join(grupos) if grupos else None


def _to_iso(raw) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if "/" in s:
        try:
            d, m, y = s[:10].split("/")
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except (ValueError, IndexError):
            return None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ejemplos", type=int, default=20,
                    help="cuántos boletos solo-en-Flujo listar (default 20)")
    args = ap.parse_args()

    c = get_mongo_client_read()
    flujo = c["CashFlow"]["Flujo"]
    nego = c["CashFlow"]["NegocioMovimientos"]

    # ── Lado FLUJO: boleto → (concertacion_iso, contraparte) ────────────────
    flujo_raw: list = []                      # muestras crudas
    flujo_norm: dict[str, dict] = {}          # norm_key → doc info
    flujo_sin_boleto = 0
    flujo_raw_keys: set[str] = set()          # string crudo, para match crudo
    for d in flujo.find({}, {"_id": 0, "boleto": 1, "concertacion": 1, "contraparte": 1}):
        b = d.get("boleto")
        if len(flujo_raw) < 5:
            flujo_raw.append(b)
        k = _norm(b)
        if k is None:
            flujo_sin_boleto += 1
            continue
        flujo_raw_keys.add(str(b))
        # si hay duplicados de boleto, nos quedamos con el primero (da igual cuál).
        flujo_norm.setdefault(k, {
            "iso": _to_iso(d.get("concertacion")),
            "cp": d.get("contraparte"),
            "raw": b,
        })

    # ── Lado NEGOCIO: comprobante ───────────────────────────────────────────
    nego_raw: list = []
    nego_norm: set[str] = set()
    nego_raw_keys: set[str] = set()
    nego_isos: list[str] = []
    for d in nego.find({}, {"_id": 0, "comprobante": 1, "fecha": 1}):
        cmp_ = d.get("comprobante")
        if len(nego_raw) < 5:
            nego_raw.append(cmp_)
        k = _norm(cmp_)
        if k is not None:
            nego_norm.add(k)
            nego_raw_keys.add(str(cmp_))
        iso = _to_iso(d.get("fecha"))
        if iso:
            nego_isos.append(iso)

    nego_min = min(nego_isos) if nego_isos else None
    nego_max = max(nego_isos) if nego_isos else None

    # ── Match ────────────────────────────────────────────────────────────────
    flujo_keys = set(flujo_norm)
    en_comun = flujo_keys & nego_norm
    solo_flujo = flujo_keys - nego_norm
    match_crudo = flujo_raw_keys & nego_raw_keys
    cobertura = (len(en_comun) / len(flujo_keys) * 100) if flujo_keys else 0.0

    print("=" * 84)
    print("BOLETOS EN COMÚN — CashFlow.Flujo.boleto  vs  NegocioMovimientos.comprobante")
    print("=" * 84)
    print("\nMuestras CRUDAS (para verificar que la normalización es correcta):")
    print(f"  Flujo.boleto         : {flujo_raw}")
    print(f"  Negocio.comprobante  : {nego_raw}")
    print(f"  → normalizado (dígitos): {[_norm(x) for x in flujo_raw]}  /  "
          f"{[_norm(x) for x in nego_raw]}")

    print("\nTotales:")
    print(f"  Flujo: {len(flujo_keys):>7} boletos con número  "
          f"({flujo_sin_boleto} sin boleto numérico, no comparables)")
    print(f"  Negocio: {len(nego_norm):>7} comprobantes  "
          f"(rango fecha: {nego_min} → {nego_max})")

    print("\nMatch:")
    print(f"  Crudo (string exacto)     : {len(match_crudo):>7}   "
          f"← bajo si hay prefijo 'BOL '")
    print(f"  Normalizado (solo dígitos): {len(en_comun):>7}")
    print(f"  Solo en Flujo (FALTAN)    : {len(solo_flujo):>7}   ← llevar a 0")
    print(f"\n  ►► COBERTURA DE FLUJO: {cobertura:.1f}%  "
          f"({len(en_comun)}/{len(flujo_keys)})")

    if len(en_comun) == 0 and len(flujo_keys) and len(nego_norm):
        print("\n⚠ 0 en común NORMALIZADO → los dos endpoints de Aunesa usan numeraciones")
        print("  de boleto DISTINTAS. Boleto (informes) ≠ comprobante (consolidados).")
        print("  Mirá las muestras crudas de arriba: si los números no se parecen,")
        print("  no se pueden linkear por ID — habría que linkear por (cuenta+fecha+importe).")

    # ── Desglose de los faltantes por fecha ─────────────────────────────────
    if solo_flujo:
        antes = dentro = sin_fecha = 0
        ejemplos = []
        for k in solo_flujo:
            info = flujo_norm[k]
            iso = info["iso"]
            if iso is None:
                sin_fecha += 1
            elif nego_min and iso < nego_min:
                antes += 1
            else:
                dentro += 1
                if len(ejemplos) < args.ejemplos:
                    ejemplos.append((info["raw"], iso, info["cp"]))

        print("\nDesglose de los que faltan (solo en Flujo):")
        print(f"  Antes del primer dato de Negocio ({nego_min}) — no capturable: {antes}")
        print(f"  Sin fecha parseable:                                          {sin_fecha}")
        print(f"  DENTRO del rango de Negocio (FALTANTE REAL):                  {dentro}  ← el problema")

        if ejemplos:
            print("\n  Ejemplos de faltantes REALES (boleto · concertacion · contraparte):")
            for raw, iso, cp in sorted(ejemplos, key=lambda e: e[1] or ""):
                print(f"    {raw!s:<14} {iso}  {cp}")

    print("\nCÓMO LEERLO:")
    print("• Si COBERTURA ~100% → Flujo ⊆ Negocio, la migración no pierde boletos.")
    print("• 'FALTANTE REAL' > 0 → boletos que Negocio debería tener y no tiene.")
    print("  Correr negocio_movimientos para esas fechas, o ver por qué no los trae.")


if __name__ == "__main__":
    main()
