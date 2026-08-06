"""Diag read-only: qué campos devuelve HOY la API de Aunesa que alimenta Back Office → Tesorería.

Pega LIVE `cuentas/consultaMovDocsSolicitados` (mismo endpoint/params que
`api/services/tesoreria.py`) y muestra el inventario REAL de claves: cobertura por
campo, un valor de ejemplo y qué campos son NUEVOS respecto de los 19 que hoy
renderiza el front. Sirve para confirmar si el update de la API ya salió.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_tesoreria_campos                     # hoy ART, estado Procesado
    python -m scripts.diag_tesoreria_campos --fecha 2026-08-05
    python -m scripts.diag_tesoreria_campos --dias 5            # busca hasta 5 días atrás si no hay filas
    python -m scripts.diag_tesoreria_campos --estado ""         # todos los estados
    python -m scripts.diag_tesoreria_campos --raw 2             # + JSON crudo de 2 filas
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from api.services.tesoreria import _ENDPOINT, _fechas, _hoy_art
from core import aunesa

# Los 19 campos que el front ya muestra (service tesoreria: crudos + persona_* + _hora/_tipo).
CONOCIDOS = {
    "id", "idExterno", "fecha", "solicitud", "tipo", "tipoDocSoli", "cuenta", "unidad",
    "monto", "estado", "banco", "cbucvu", "cuentaOperativa",
    "persona_nombreCompleto", "persona_documento", "persona_cuit",
    "persona_tipoDocumento", "persona_tipoPersona",
}


def _pedir(fecha_iso: str, estado: str) -> tuple[list[dict], str]:
    """Filas del día `fecha_iso` (ISO) con el MISMO criterio que el service."""
    ddmmyyyy, ddmmyyyy_hasta, _ = _fechas(fecha_iso)
    params: dict = {"liquidacionDesde": ddmmyyyy, "liquidacionHasta": ddmmyyyy_hasta}
    if estado:
        params["estados"] = estado
    resp = aunesa.get(_ENDPOINT, params)
    if resp.status_code == 204:
        return [], ddmmyyyy
    if resp.status_code != 200:
        raise SystemExit(f"Aunesa {_ENDPOINT} [{resp.status_code}]: {resp.text[:400]}")
    body = resp.json()
    rows = body if isinstance(body, list) else []
    return [r for r in rows if str(r.get("fecha") or "").strip() == ddmmyyyy], ddmmyyyy


def _aplanar(r: dict) -> dict:
    """Mismo aplanado que el service: persona.* → persona_*."""
    out = {k: v for k, v in r.items() if k != "persona"}
    for pk, pv in (r.get("persona") or {}).items():
        out[f"persona_{pk}"] = pv
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy ART)")
    ap.add_argument("--estado", default="Procesado", help="'' = todos")
    ap.add_argument("--dias", type=int, default=1,
                    help="si el día no tiene filas, retrocede hasta N días buscando datos")
    ap.add_argument("--raw", type=int, default=0, help="imprime N filas crudas completas (JSON)")
    a = ap.parse_args()

    base = a.fecha or _hoy_art().date().isoformat()
    d0 = date.fromisoformat(base)

    rows: list[dict] = []
    usado = ""
    for i in range(max(a.dias, 1)):
        iso = (d0 - timedelta(days=i)).isoformat()
        rows, usado = _pedir(iso, a.estado)
        print(f"· {iso} ({usado}) estado={a.estado or 'TODOS'} → {len(rows)} filas")
        if rows:
            break
    if not rows:
        print("\nSin filas: no puedo listar campos. Probá con --dias 7 o --estado ''.")
        return

    planas = [_aplanar(r) for r in rows]
    claves: dict[str, int] = {}
    ejemplo: dict[str, object] = {}
    for p in planas:
        for k, v in p.items():
            claves[k] = claves.get(k, 0) + (1 if v not in (None, "") else 0)
            if k not in ejemplo and v not in (None, ""):
                ejemplo[k] = v

    nuevos = [k for k in claves if k not in CONOCIDOS]
    faltantes = [k for k in CONOCIDOS if k not in claves]

    print(f"\n=== CAMPOS DEVUELTOS ({len(claves)}) sobre {len(planas)} filas ===")
    print(f"{'CAMPO':<28} {'CON DATO':>9}  EJEMPLO")
    for k in sorted(claves, key=lambda x: (x not in nuevos, x)):
        marca = "NUEVO " if k in nuevos else "      "
        val = str(ejemplo.get(k, ""))[:60]
        print(f"{marca}{k:<22} {claves[k]:>4}/{len(planas):<4} {val}")

    print(f"\n>>> CAMPOS NUEVOS (no estaban en el front): {nuevos or 'ninguno'}")
    if faltantes:
        print(f">>> CAMPOS QUE YA NO VIENEN: {faltantes}")

    for r in rows[: max(a.raw, 0)]:
        print("\n--- fila cruda ---")
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
