"""Correr UNA herramienta del asistente sin el modelo, para separar «el dato
está mal» de «el modelo lo usó mal». Read-only, cero tokens.

    python -m scripts.diag_herramienta                      # lista y peso de cada ficha
    python -m scripts.diag_herramienta tenencia_actual --cuenta 805 --horizonte t0
    python -m scripts.diag_herramienta curva --curva cer --limit 5
"""
from __future__ import annotations

import inspect
import json
import sys
import time


def _fichas() -> None:
    from asistente import herramientas as H

    print(f"{len(H.TODAS)} herramienta(s). Cada ficha viaja entera en cada "
          "llamada del agente que la tiene:\n")
    total = 0
    for f in (H.ficha(h) for h in H.TODAS):
        fn = f["function"]
        n = len(json.dumps(f, ensure_ascii=False))
        total += n
        params = fn["parameters"]
        req = set(params.get("required") or [])
        args = ", ".join(f"{k}{'' if k in req else ' (opcional)'}"
                         for k in params["properties"])
        print(f"  {fn['name']:20} {n:5} chars   ({args})")
    print(f"\n  {'TOTAL por llamada':20} {total:5} chars ≈ {total // 4} tokens")


def _castear(fn, crudos: dict[str, str]) -> dict:
    """Los `--k v` de la línea de comandos, con el tipo que pide la firma."""
    params = inspect.signature(fn).parameters
    fuera = {}
    for k, v in crudos.items():
        if k not in params:
            print(f"⚠️  {fn.__name__} no toma un argumento `{k}` — lo ignoro")
            continue
        anot = params[k].annotation
        fuera[k] = int(v) if anot in (int, "int") else v
    return fuera


def main() -> int:
    from asistente import herramientas as H

    args = sys.argv[1:]
    if not args:
        _fichas()
        return 0

    nombre = args[0]
    fn = H.POR_NOMBRE.get(nombre)
    if fn is None:
        print(f"no existe `{nombre}`. Hay: {', '.join(sorted(H.POR_NOMBRE))}")
        return 1

    crudos: dict[str, str] = {}
    resto = args[1:]
    for i in range(0, len(resto) - 1, 2):
        if resto[i].startswith("--"):
            crudos[resto[i][2:]] = resto[i + 1]

    kwargs = _castear(fn, crudos)
    print(f"→ {nombre}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})\n")

    t0 = time.monotonic()
    r = fn(**kwargs)
    ms = int((time.monotonic() - t0) * 1000)

    texto = json.dumps(r, ensure_ascii=False, default=str)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    print(f"\n── {ms} ms · {len(texto)} chars ≈ {len(texto) // 4} tokens de ENTRADA, "
          "que se pagan en esta vuelta y en todas las que sigan de esa pregunta.")
    if isinstance(r, dict) and r.get("error"):
        print("⚠️  la herramienta devolvió un ERROR — eso es lo que va a leer el modelo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
