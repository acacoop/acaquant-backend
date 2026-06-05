"""Diag READ-ONLY: formato del identificador fiscal en Aunesa (para el matcher CUIT).

Feature "Control Automático" (Clientes): el Excel trae el CUIT en la columna
'Nº ident.fis.1' y hay que cruzarlo contra NUESTRAS cuentas. El CUIT NO está en
Clientes.Comitentes; vive en el campo `titular` de Aunesa listadoCuentas con
formato '[TIPO nro] NOMBRE' (ej. '[DNI 93698623] ...'). Este diag mide qué TIPO
viene (DNI/CUIT/CUIL) y la longitud del número, para decidir cómo normalizar el
match (un CUIT de persona física contiene el DNI de 8 dígitos en el medio).

Uso:
    python -m scripts.diag_titular_cuit
"""
from __future__ import annotations

import re
from collections import Counter

from core import aunesa

_BRACKET = re.compile(r"^\[\s*([A-Za-z./]+)\s+([0-9.\-]+)\s*\]")


def main() -> int:
    resp = aunesa.get("cuentas/listadoCuentas", params={"tipoCuenta": "Comitente"})
    data = resp.json()
    if isinstance(data, list):
        cuentas = data
    elif isinstance(data, dict):
        cuentas = data.get("cuentas") or data.get("data") or []
    else:
        cuentas = []
    print(f"cuentas Comitente: {len(cuentas)}")

    tipos: Counter = Counter()
    largos: Counter = Counter()
    sin_bracket = 0
    ejemplos: list[str] = []
    for c in cuentas:
        titular = str(c.get("titular") or "").strip()
        m = _BRACKET.match(titular)
        if not m:
            sin_bracket += 1
            if len(ejemplos) < 6:
                ejemplos.append(f"(sin bracket) {titular[:50]!r}")
            continue
        tipo = m.group(1).upper()
        nro = re.sub(r"\D", "", m.group(2))
        tipos[tipo] += 1
        largos[(tipo, len(nro))] += 1
        if len(ejemplos) < 14:
            ejemplos.append(f"[{tipo}] {nro} (len {len(nro)})")

    print("\n-- tipos de identificador --")
    for t, n in tipos.most_common():
        print(f"  {t}: {n}")
    print(f"  sin bracket parseable: {sin_bracket}")

    print("\n-- (tipo, longitud del número) --")
    for (t, ln), n in sorted(largos.items()):
        print(f"  {t} len={ln}: {n}")

    print("\n-- ejemplos --")
    for e in ejemplos:
        print(f"  {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
