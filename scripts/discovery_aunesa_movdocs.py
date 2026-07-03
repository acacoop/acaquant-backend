"""scripts/discovery_aunesa_movdocs.py — DISCOVERY del endpoint Aunesa
`GET cuentas/consultaMovDocsSolicitados` (Movimientos y Documentos Solicitados).

Objetivo (REGLA #2: medir antes de modelar): pegarle al endpoint con distintas
combinaciones de filtros, GUARDAR las responses crudas y RESUMIR los shapes +
distribuciones (estados, tipoDocSoli, banco, unidad, signo del `monto`), para
después decidir cómo modelar la futura vista TESORERÍA (ingresos/egresos del día)
dentro de Back Office.

Contexto: esta API trae movimientos BANCARIOS (cbuCVU, banco, monto) — distinto de
los ingresos/egresos de mercado que ya tenemos en un job. El discovery confirma qué
campo marca ingreso vs egreso y qué estados/tipos existen (no lo asumimos).

── PII ──────────────────────────────────────────────────────────────────────────
La response trae datos personales (documento, cuit, nombreCompleto, cbuCVU, banco).
  • El dump CRUDO se guarda en scripts/out_movdocs/*.json  → GITIGNORED (no al repo).
  • A la CONSOLA salen solo resúmenes con la PII ENMASCARADA → seguro para pegar en
    el chat.

── Uso (en el Droplet: git pull && python -m scripts.discovery_aunesa_movdocs) ────
  python -m scripts.discovery_aunesa_movdocs                 # HOY + ventana 7d
  python -m scripts.discovery_aunesa_movdocs --dias 30       # ventana más ancha
  python -m scripts.discovery_aunesa_movdocs --desde 01/07/2026 --hasta 03/07/2026
  python -m scripts.discovery_aunesa_movdocs --cuenta 805    # + prueba scopeada a 1 cuenta
  python -m scripts.discovery_aunesa_movdocs --sin-filtro    # + prueba sin ningún filtro

Requiere las env vars AUNESA_* (ya configuradas en el Droplet; las usa core.aunesa).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from core import aunesa

ENDPOINT = "cuentas/consultaMovDocsSolicitados"
OUT_DIR = Path(__file__).resolve().parent / "out_movdocs"

# Estados posibles según la doc (los mandamos todos para no perder ninguno).
ESTADOS_TODOS = ("Procesado;Rechazado;Anulado;Pendiente de autorizar;"
                 "Pendiente;Demorado;Incompleto")


# ── helpers ───────────────────────────────────────────────────────────────────────
def _guardar(nombre: str, obj: Any) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{nombre}.json").write_text(
        json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _shape(obj: Any, prof: int = 0, max_prof: int = 4) -> str:
    """Descripción compacta del shape de un JSON (claves + tipos + muestra escalar)."""
    ind = "  " * prof
    if isinstance(obj, dict):
        if prof >= max_prof:
            return "{…}"
        lineas = [f"{ind}  {k}: {_shape(v, prof + 1, max_prof)}" for k, v in list(obj.items())[:40]]
        return "{\n" + "\n".join(lineas) + f"\n{ind}}}"
    if isinstance(obj, list):
        return "[] (vacío)" if not obj else f"[{len(obj)}× " + _shape(obj[0], prof, max_prof) + "]"
    if isinstance(obj, bool):
        return f"bool ({obj})"
    if isinstance(obj, (int, float)):
        return f"num ({obj})"
    if isinstance(obj, str):
        return f"str ({obj[:24]!r})" if obj else "str ('')"
    if obj is None:
        return "null"
    return type(obj).__name__


def _mask(s: Any) -> str:
    """Enmascara un valor PII para la consola: deja primeros/últimos 2 chars."""
    s = "" if s is None else str(s)
    if len(s) <= 4:
        return "•" * len(s)
    return f"{s[:2]}{'•' * (len(s) - 4)}{s[-2:]}"


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _fmt(n: float) -> str:
    return f"{n:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _dist(rows: list[dict], key: str) -> None:
    """Imprime la distribución de un campo: conteo + suma de `monto` por valor."""
    agg: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        agg[str(r.get(key))].append(_num(r.get("monto")))
    print(f"    · por {key}:")
    for val, montos in sorted(agg.items(), key=lambda kv: -len(kv[1])):
        print(f"        {val!r:32} n={len(montos):5}  Σmonto={_fmt(sum(montos))}")


def _resumen(rows: list[dict]) -> None:
    """Resumen agregado + muestra enmascarada para modelar ingresos/egresos."""
    if not rows:
        print("    (sin filas)")
        return
    montos = [_num(r.get("monto")) for r in rows]
    neg = sum(1 for m in montos if m < 0)
    print(f"    filas={len(rows)}  Σmonto={_fmt(sum(montos))}  "
          f"min={_fmt(min(montos))}  max={_fmt(max(montos))}  negativos={neg}")
    print(f"    campos presentes: {sorted({k for r in rows for k in r})}")
    # `solicitud` es el campo que separa INGRESO (Depósito) de EGRESO (Extracción) —
    # va primero. Los demás son cortes secundarios (riel / moneda / estado / banco).
    for k in ("solicitud", "estado", "tipoDocSoli", "unidad", "banco"):
        if any(k in r for r in rows):
            _dist(rows, k)
    # Corte cruzado solicitud × unidad (la base de "ingresos/egresos del día por moneda").
    print("    · solicitud × unidad (Σmonto):")
    cruz: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        cruz[(str(r.get("solicitud")), str(r.get("unidad")))].append(_num(r.get("monto")))
    for (sol, uni), montos in sorted(cruz.items(), key=lambda kv: -sum(kv[1])):
        print(f"        {sol!r:24} {uni!r:6}  n={len(montos):5}  Σmonto={_fmt(sum(montos))}")
    print("    muestra (PII enmascarada):")
    for r in rows[:3]:
        per = r.get("persona") or {}
        print(f"        id={r.get('id')} sol={r.get('solicitud')} tipo={r.get('tipoDocSoli')!r} "
              f"estado={r.get('estado')!r} cuenta={r.get('cuenta')} unidad={r.get('unidad')!r} "
              f"monto={r.get('monto')} banco={_mask(r.get('banco'))} cbu={_mask(r.get('cbuCVU'))} "
              f"nombre={_mask(per.get('nombreCompleto'))} doc={_mask(per.get('documento'))}")


def _probe(nombre: str, params: dict[str, Any]) -> None:
    """Una llamada al endpoint: guarda crudo, imprime status/shape/resumen."""
    print(f"\n[{nombre}] GET {ENDPOINT}  params={params}")
    try:
        resp = aunesa.get(ENDPOINT, params)
    except Exception as e:  # discovery: queremos ver cualquier fallo, sea red o parseo
        print(f"  ✗ error de red/cliente: {e}")
        return
    print(f"  status = {resp.status_code}")
    if resp.status_code == 204:
        print("  → 204 sin contenido (no hay movimientos para ese filtro)")
        return
    try:
        body = resp.json()
    except ValueError:
        print(f"  ✗ response no-JSON:\n{resp.text[:500]}")
        return
    _guardar(nombre, body)
    if resp.status_code != 200:
        print(f"  ✗ error [{resp.status_code}]: {json.dumps(body, ensure_ascii=False)[:500]}")
        return
    print(f"  shape → {_shape(body)}")
    rows = body if isinstance(body, list) else body.get("data") if isinstance(body, dict) else None
    if isinstance(rows, list):
        _resumen(rows)
    else:
        print("  (la response no es una lista de filas — revisá el shape de arriba)")


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


# ── main ─────────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="Discovery Aunesa consultaMovDocsSolicitados.")
    p.add_argument("--desde", help="liquidacionDesde dd/mm/yyyy (default: hoy - --dias)")
    p.add_argument("--hasta", help="liquidacionHasta dd/mm/yyyy (default: hoy)")
    p.add_argument("--dias", type=int, default=7, help="ancho de la ventana si no pasás --desde")
    p.add_argument("--estados", default=ESTADOS_TODOS, help="estados separados por ';'")
    p.add_argument("--cuenta", help="probar además scopeado a una cuenta comitente")
    p.add_argument("--sin-filtro", action="store_true", help="probar además sin ningún parámetro")
    args = p.parse_args()

    hoy = datetime.now().date()
    hasta = args.hasta or _ddmmyyyy(hoy)
    desde = args.desde or _ddmmyyyy(hoy - timedelta(days=args.dias))

    print(f"BASE = {aunesa.BASE_URL}   endpoint = {ENDPOINT}")
    print(f"HOY (ART server) = {_ddmmyyyy(hoy)}   ventana = {desde} → {hasta}   estados = {args.estados!r}")

    # 1) Sólo HOY — el caso de uso central de Tesorería (ingresos/egresos del día).
    _probe("hoy", {"liquidacionDesde": _ddmmyyyy(hoy), "liquidacionHasta": _ddmmyyyy(hoy),
                   "estados": args.estados})

    # 2) Ventana (por si hoy viene vacío / feriado): ver el shape con datos reales.
    _probe("ventana", {"liquidacionDesde": desde, "liquidacionHasta": hasta, "estados": args.estados})

    # 3) Scopeado a una cuenta (opcional).
    if args.cuenta:
        _probe("cuenta", {"cuenta": args.cuenta, "liquidacionDesde": desde,
                          "liquidacionHasta": hasta, "estados": args.estados})

    # 4) Sin filtro (opcional; puede ser pesado o devolver default del backend).
    if args.sin_filtro:
        _probe("sin_filtro", {})

    print(f"\n✓ Discovery listo. Responses CRUDAS (con PII) en: {OUT_DIR}  (gitignored)")
    print("  Pegá en el chat SOLO los resúmenes de arriba (ya vienen enmascarados).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
