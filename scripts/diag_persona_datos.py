"""diag_persona_datos.py — read-only de Aunesa GET /api/personas/datosPersona.

Objetivo: ver el response COMPLETO de una persona (KYC detallado: patrimonio,
procedencia/medio de fondeo, declaraciones PEP/UIF/FATCA, accionistas, etc.)
para confirmar si figura el "límite de fondeo" y dónde (probable:
`informacionPatrimonial[].medioFondeo` / `patrimonioYBalance`).

El endpoint pide `tipoId` + `id` de la PERSONA, no de la cuenta. Por eso, dado
`--cuenta 805`, primero pega a `cuentas/listadoCuentas` para esa cuenta y
extrae los candidatos (tipoId, id) — del bracket del `titular` ("[DNI 93698623]")
y de cualquier sub-objeto con claves tipoId/id (titular, personasRelacionadas,
autoridades, etc.). Después llama a `datosPersona` por cada candidato y dumpea
todo. También se puede pasar la persona directo con `--tipo-id` / `--id`.

NO escribe nada (solo auth + GET).

Uso:
    python -m scripts.diag_persona_datos --cuenta 805
    python -m scripts.diag_persona_datos --tipo-id DNI --id 93698623
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from core import aunesa

# Consola Windows (cp1252) revienta con acentos/flechas en el JSON; forzamos UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_BRACKET = re.compile(r"\[\s*([A-Za-z]+)\s+([0-9.\-]+)\s*\]")  # "[DNI 93698623]"


def _candidatos_persona(cuenta_doc: dict) -> list[tuple[str, str]]:
    """Extrae pares (tipoId, id) de persona de un doc de cuenta, sin asumir paths.

    1) Recorre recursivamente el doc juntando todo dict que tenga 'tipoId' + 'id'.
    2) Fallback: parsea el bracket del campo `titular` ("[DNI 93698623] ...").
    """
    vistos: list[tuple[str, str]] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            tid, pid = node.get("tipoId"), node.get("id")
            if tid and pid:
                par = (str(tid), str(pid))
                if par not in vistos:
                    vistos.append(par)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(cuenta_doc)

    titular = cuenta_doc.get("titular") or ""
    m = _BRACKET.search(titular)
    if m:
        par = (m.group(1), m.group(2).replace(".", ""))
        if par not in vistos:
            vistos.append(par)

    return vistos


def _resolver_desde_cuenta(cuenta: str) -> list[tuple[str, str]]:
    r = aunesa.get("cuentas/listadoCuentas", {"idCuenta": [cuenta]}, timeout=120)
    print(f"GET listadoCuentas idCuenta={cuenta} → HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:2000])
        return []
    data = r.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or [data]
    if not data:
        print("Sin cuenta.")
        return []
    doc = data[0]
    print("\n" + "=" * 72)
    print(f"listadoCuentas — DOC COMPLETO de la cuenta {cuenta} (todos los campos):")
    print("-" * 72)
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    cands = _candidatos_persona(doc)
    print(f"\n  candidatos (tipoId, id): {cands}\n")
    return cands


def _dump_persona(tipo_id: str, pid: str) -> None:
    print("=" * 72)
    print(f"datosPersona  tipoId={tipo_id}  id={pid}")
    r = aunesa.get("personas/datosPersona", {"tipoId": tipo_id, "id": pid}, timeout=120)
    print(f"  → HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:2000])
        return
    body = r.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuenta", help="idCuenta a resolver (ej. 805)")
    ap.add_argument("--tipo-id", dest="tipo_id", help="tipoId de la persona (directo)")
    ap.add_argument("--id", dest="pid", help="id de la persona (directo)")
    args = ap.parse_args()

    if args.tipo_id and args.pid:
        _dump_persona(args.tipo_id, args.pid)
        return

    if not args.cuenta:
        ap.error("pasá --cuenta NNN, o --tipo-id X --id Y")

    cands = _resolver_desde_cuenta(args.cuenta)
    if not cands:
        print("No se pudo extraer (tipoId, id) de la cuenta. Pasalos con --tipo-id / --id.")
        return
    for tipo_id, pid in cands:
        _dump_persona(tipo_id, pid)


if __name__ == "__main__":
    main()
