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

import hashlib
import sys

from jobs.aum import autenticar, consultar_posicion

CUENTA = "255"
_DEFAULT = ["29/05/2026", "01/06/2026"]
_VAL_KEYS = ("valuacion", "valuacionPesos", "valuado", "valor", "valuacionMoneda")


def _consultar(headers, fecha, cuenta):
    data, reauth = consultar_posicion(cuenta, headers, desde=fecha)
    if reauth:  # 401 → re-auth y reintento
        headers = autenticar()
        data, _ = consultar_posicion(cuenta, headers, desde=fecha)
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
    argv = sys.argv[1:]
    cuenta = CUENTA
    out = None
    fechas: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--cuenta" and i + 1 < len(argv):
            cuenta = argv[i + 1]; i += 2; continue
        if a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]; i += 2; continue
        fechas.append(a); i += 1
    if not fechas:
        fechas = _DEFAULT

    buf: list[str] = []

    def emit(s=""):
        print(s)
        buf.append(s)

    headers = autenticar()
    resumenes: list[tuple] = []
    for fecha in fechas:
        emit(f"\n{'=' * 64}")
        emit(f"CUENTA {cuenta}  ·  desde={fecha}  ·  nivel='Especie x cuenta'")
        emit("=" * 64)
        data, headers = _consultar(headers, fecha, cuenta)
        if data is None:
            emit("  (sin respuesta / error HTTP de Aunesa)")
            continue
        rows = _rows(data)
        # MISMO filtro que el job (jobs/aum.py::procesar): solo 'Acumulado'.
        acum = [r for r in rows if r.get("informacion") == "Acumulado"]
        emit(f"  registros totales: {len(rows)}   |   Acumulado (lo que usa el job): {len(acum)}")
        if not acum:
            emit("  (sin filas Acumulado) — keys de la 1ra fila cruda: "
                 + (str(list(rows[0].keys())) if rows else "—"))
            continue

        emit(f"  keys de cada fila: {list(acum[0].keys())}")
        # Campos que parecen fecha → AHÍ se ve si Aunesa corre el día.
        date_keys = [k for k in acum[0] if "fecha" in k.lower() or "date" in k.lower()]
        emit(f"  >>> CAMPOS DE FECHA detectados: {date_keys or '(ninguno)'}")
        emit(f"  {'UNIDAD':<42} {'CANTIDAD':>16} {'PRECIO':>14} "
             + " ".join(f"{k:>12}" for k in date_keys))
        total_c = 0.0
        for r in acum:
            try:
                cant = float(r.get("cantidad") or 0)
            except (TypeError, ValueError):
                cant = 0.0
            total_c += cant
            emit(f"  {str(r.get('unidad'))[:42]:<42} {cant:>16,.2f} "
                 f"{r.get('precio')!s:>14} "
                 + " ".join(f"{r.get(k)!s:>12}" for k in date_keys))
        emit(f"  SUMA cantidades: {total_c:,.2f}")
        fp = hashlib.md5(
            "|".join(f"{r.get('unidad')}:{round(float(r.get('cantidad') or 0), 2)}"
                     for r in sorted(acum, key=lambda x: str(x.get('unidad')))).encode()
        ).hexdigest()[:8]
        resumenes.append((fecha, len(acum), total_c, fp))

    emit("\n" + "=" * 64)
    emit("COMPARACIÓN — ¿qué fechas devuelven lo MISMO? (ahí está el corrimiento)")
    emit("=" * 64)
    emit(f"  {'FECHA (desde)':<16} {'#ESPECIES':>10} {'SUMA CANT':>18} {'HUELLA':>10}")
    for f, n, t, fp in resumenes:
        emit(f"  {f:<16} {n:>10} {t:>18,.2f} {fp:>10}")
    emit("\n  Misma HUELLA = misma posición devuelta → ahí está el corrimiento de fecha.")
    emit("  Decime cuál FECHA (desde) coincide con tu contable del 29/05 y clavamos el offset.")

    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(buf) + "\n")
        print(f"\n(volcado a {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
