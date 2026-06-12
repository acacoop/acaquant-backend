"""scripts/diag_aunesa_255.py — consulta Aunesa CRUDA (cuenta 255) para 2 fechas.

Replica EXACTAMENTE la consulta del job de AuM (jobs/aum.py::consultar_posicion,
nivel='Especie x cuenta') pero scopeada a la cuenta 255 y a las fechas que pases.
Dumpea la respuesta cruda de Aunesa para comparar 29/05 vs 01/06 (o las que des).

Read-only contra Aunesa (GET posicionValuada). Corre en el Droplet (usa las
credenciales AUNESA_* de la config).

Uso:
    python -m scripts.diag_aunesa_255                       # 29/05/2026 y 01/06/2026
    python -m scripts.diag_aunesa_255 29/05/2026 01/06/2026
    python -m scripts.diag_aunesa_255 29/05/2026 --out scripts/aunesa255.txt
"""
from __future__ import annotations

import json
import sys

from jobs.aum import autenticar, consultar_posicion

CUENTA = "255"
_DEFAULT = ["29/05/2026", "01/06/2026"]
_VAL_KEYS = ("valuacion", "valuacionPesos", "valuado", "valor", "valuacionMoneda")


def _consultar(headers, fecha):
    data, reauth = consultar_posicion(CUENTA, headers, desde=fecha)
    if reauth:  # 401 → re-auth y reintento
        headers = autenticar()
        data, _ = consultar_posicion(CUENTA, headers, desde=fecha)
    return data, headers


def _rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("posiciones", "data", "items", "result", "registros"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    fechas = args or _DEFAULT

    buf: list[str] = []

    def emit(s=""):
        print(s)
        buf.append(s)

    headers = autenticar()
    for fecha in fechas:
        emit(f"\n{'=' * 64}")
        emit(f"CUENTA {CUENTA}  ·  desde={fecha}  ·  nivel='Especie x cuenta'")
        emit("=" * 64)
        data, headers = _consultar(headers, fecha)
        if data is None:
            emit("  (sin respuesta / error HTTP de Aunesa)")
            continue
        rows = _rows(data)
        emit(f"  registros: {len(rows)}")
        if rows:
            emit(f"  keys: {list(rows[0].keys())}")
            total = 0.0
            for r in rows:
                emit("   " + json.dumps(r, ensure_ascii=False, default=str))
                for k in _VAL_KEYS:
                    v = r.get(k)
                    if isinstance(v, (int, float)):
                        total += v
                        break
            emit(f"  SUMA (best-effort sobre {_VAL_KEYS}): {total:,.2f}")
        else:
            emit(json.dumps(data, ensure_ascii=False, indent=2, default=str)[:4000])

    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(buf) + "\n")
        print(f"\n(volcado a {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
