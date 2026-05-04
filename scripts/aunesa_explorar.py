"""aunesa_explorar.py — exploratorio del endpoint /operaciones/consolidadosGenerales.

Pega al endpoint con un día específico, muestra qué tipos de movimientos
trae, cuál es el shape de cada tipo, y cuánto se está descartando con el
filtro actual de jobs/cashflow.py (palabras clave deposito/transferencia/
extraccion).

NO escribe nada a Mongo. Solo análisis.

Uso:
    python -m scripts.aunesa_explorar              # día de hoy ART
    python -m scripts.aunesa_explorar --fecha 2026-05-02

Output:
    1. Total de movimientos del día.
    2. Distribución por tipo de `informacion` (top + count).
    3. Sample doc completo de cada tipo distinto.
    4. Análisis del filtro actual: cuántos capturás vs cuántos descartás.
    5. Top tipos descartados (potencial sin uso).
"""
from __future__ import annotations

import argparse
import json
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

import requests

import config

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
OPS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/consolidadosGenerales"

# Mismo filtro que jobs/cashflow.py — para reportar qué descartamos.
PALABRAS_CLAVE_ACTUALES = ["deposito", "transferencia", "extraccion"]


def _autenticar() -> dict:
    resp = requests.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _normalizar(s: str) -> str:
    return (
        unicodedata.normalize("NFD", str(s))
        .encode("ascii", "ignore")
        .decode("utf-8")
        .lower()
    )


def _captura_actual(informacion: str) -> bool:
    norm = _normalizar(informacion)
    return any(p in norm for p in PALABRAS_CLAVE_ACTUALES)


def _truncar(v, max_len: int = 60) -> str:
    s = str(v) if v is not None else "None"
    return s if len(s) <= max_len else s[:max_len] + "…"


def _print_doc_compacto(doc: dict, indent: str = "    ") -> None:
    """Imprime un dict en una vista compacta legible (1 key por línea)."""
    for k in sorted(doc.keys()):
        v = doc[k]
        print(f"{indent}{k:25s} = {_truncar(v, 80)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fecha", help="YYYY-MM-DD; default: hoy ART.")
    args = parser.parse_args()

    if args.fecha:
        try:
            d = datetime.strptime(args.fecha, "%Y-%m-%d").date()
        except ValueError:
            print(f"[!! ] --fecha mal formada: {args.fecha} (esperado YYYY-MM-DD)")
            return 1
    else:
        d = (datetime.now(UTC) - timedelta(hours=3)).date()

    dia_str = d.strftime("%d/%m/%Y")

    print()
    print("=" * 70)
    print(f" Exploratorio Aunesa /operaciones/consolidadosGenerales · {d.isoformat()} (ART)")
    print("=" * 70)

    print("\n[1/5] Autenticando…")
    headers = _autenticar()
    print("    [OK] token recibido.")

    print(f"\n[2/5] GET ?tiposCuenta=Comitente&concertacionDesde={dia_str}&concertacionHasta={dia_str}")
    print("    (puede tardar 1-2 min con días de mucha actividad)")
    params = {
        "tiposCuenta": "Comitente",
        "concertacionDesde": dia_str,
        "concertacionHasta": dia_str,
    }
    resp = None
    last_err: Exception | None = None
    for intento in range(1, 4):
        try:
            resp = requests.get(OPS_URL, params=params, headers=headers, timeout=180)
            break
        except requests.exceptions.Timeout as e:
            last_err = e
            print(f"    [WARN] timeout en intento {intento}/3, reintentando…")
            continue
        except Exception as e:
            last_err = e
            break
    if resp is None:
        print(f"    [!! ] No se pudo conectar tras 3 intentos: {last_err}")
        return 1
    if resp.status_code != 200:
        print(f"    [!! ] HTTP {resp.status_code}: {resp.text[:300]}")
        return 1
    data = resp.json()
    if not isinstance(data, list):
        print(f"    [!! ] Shape inesperado: {type(data).__name__}")
        print(f"    raw: {str(data)[:300]}")
        return 1

    total = len(data)
    print(f"    [OK] {total} movimientos del día.")

    if total == 0:
        print("\n    Día vacío. Probá con otra fecha.")
        return 0

    # ─── Análisis 1: distribución por `informacion` ───
    print("\n[3/5] Distribución por tipo de `informacion`:")
    counter = Counter(r.get("informacion") or "(sin informacion)" for r in data)
    for tipo, n in counter.most_common():
        capturado = "✓" if _captura_actual(tipo) else "✗"
        print(f"    [{capturado}] {n:5d}  {tipo}")

    # ─── Análisis 2: sample por tipo ───
    print("\n[4/5] Sample doc completo por cada tipo (1 doc por tipo):")
    samples_por_tipo: dict[str, dict] = {}
    for r in data:
        t = r.get("informacion") or "(sin informacion)"
        if t not in samples_por_tipo:
            samples_por_tipo[t] = r

    for tipo in sorted(samples_por_tipo.keys()):
        capturado = "✓ capturado" if _captura_actual(tipo) else "✗ DESCARTADO"
        print(f"\n  ── {tipo}  [{capturado}]")
        _print_doc_compacto(samples_por_tipo[tipo])

    # ─── Análisis 3: filtro actual vs total ───
    print("\n[5/5] Análisis del filtro actual (jobs/cashflow.py):")
    capturados = sum(1 for r in data if _captura_actual(r.get("informacion") or ""))
    descartados = total - capturados
    pct_cap = (capturados / total) * 100
    pct_desc = (descartados / total) * 100
    print(f"    palabras clave actuales : {PALABRAS_CLAVE_ACTUALES}")
    print(f"    capturados              : {capturados:5d} / {total} ({pct_cap:.1f}%)")
    print(f"    descartados             : {descartados:5d} / {total} ({pct_desc:.1f}%)")

    if descartados > 0:
        # Top descartados (potencial sin uso).
        descart_counter: Counter[str] = Counter()
        for r in data:
            t = r.get("informacion") or "(sin informacion)"
            if not _captura_actual(t):
                descart_counter[t] += 1
        print("\n    TOP tipos descartados (potencial sin uso):")
        for tipo, n in descart_counter.most_common(10):
            print(f"      {n:5d}  {tipo}")

    # ─── Análisis 4: campos disponibles globalmente ───
    print("\n  Campos (keys) presentes en TODOS los movimientos:")
    all_keys: set[str] = set()
    keys_por_tipo: dict[str, set[str]] = defaultdict(set)
    for r in data:
        all_keys.update(r.keys())
        keys_por_tipo[r.get("informacion") or "(sin)"].update(r.keys())
    print(f"    Universo de keys        : {sorted(all_keys)}")

    # ─── Dump raw a archivo opcional para inspección manual ───
    out_path = f"exports/aunesa_explorar_{d.isoformat()}.json"
    try:
        from pathlib import Path
        Path("exports").mkdir(exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n    Raw response guardado en: {out_path}")
    except Exception as e:
        print(f"\n    [WARN] no pude guardar raw: {e}")

    print()
    print("=" * 70)
    print(" Fin del exploratorio.")
    print("=" * 70)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
