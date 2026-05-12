"""Debug: descarga form.idx y muestra el header + primeras N líneas raw.

Sirve para entender el formato exacto de los anchos de columna y ajustar el
parser si SEC cambió algo.

Uso:
    python -m scripts.debug_form_idx
    python -m scripts.debug_form_idx --year 2025 --quarter 4
"""
from __future__ import annotations

import argparse

import requests

from core.sec_edgar import USER_AGENT


def run(year: int, quarter: int, n_lines: int) -> None:
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
    print(f"GET {url}\n")
    r = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
        timeout=60,
    )
    print(f"HTTP {r.status_code}")
    print(f"content-type: {r.headers.get('content-type')}")
    print(f"content-length: {len(r.text):,} bytes\n")
    if r.status_code != 200:
        print("Body (recortado):")
        print(r.text[:600])
        return

    lines = r.text.split("\n")
    print(f"Total líneas: {len(lines):,}\n")
    print("─" * 100)
    print(f"PRIMERAS {n_lines} LÍNEAS RAW (con ruler para ver columnas):")
    print("─" * 100)
    # Ruler de columnas cada 10 chars
    ruler = "".join((f"{i:>10}" if (i % 10 == 0) else "·") for i in range(0, 121, 10))
    print(ruler)
    print("0         1         2         3         4         5         6         7         8         9         10        11        12")
    for line in lines[:n_lines]:
        print(line[:120])

    # Mostremos también una muestra de líneas DESPUÉS del header (~línea 12-15)
    if len(lines) > 50:
        print()
        print("─" * 100)
        print("MUESTRA DE 5 LÍNEAS DATA (después del header):")
        print("─" * 100)
        # Buscamos la línea de dashes
        start = 0
        for i, line in enumerate(lines):
            if line.startswith("---"):
                start = i + 1
                break
        if start == 0:
            start = 12  # fallback típico
        for line in lines[start:start + 5]:
            print(line[:120])
        # Ahora un detalle de la primera línea de data — vemos qué hay en cada col chunk
        if start < len(lines) and lines[start]:
            data_line = lines[start]
            print()
            print("DETALLE de la línea 0 de data:")
            print(f"  len:                {len(data_line)}")
            print(f"  [0:12]:    '{data_line[0:12]}'")
            print(f"  [12:74]:   '{data_line[12:74]}'")
            print(f"  [74:86]:   '{data_line[74:86]}'")
            print(f"  [86:98]:   '{data_line[86:98]}'")
            print(f"  [98:]:     '{data_line[98:120]}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--quarter", type=int, default=1)
    parser.add_argument("--lines", type=int, default=15)
    args = parser.parse_args()
    run(args.year, args.quarter, args.lines)
