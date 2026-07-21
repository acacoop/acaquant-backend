"""gen_pedidos — exporta el BUZÓN DE PEDIDOS a docs/PEDIDOS.md (versionado).

La gente le pide mejoras al copiloto mientras trabaja y quedan en
`manager.pedidos` (ver api/services/copiloto/pedidos.py). Este script los baja
a un markdown del repo: así se revisan en git (con historial), se priorizan a
mano y Claude los lee como cualquier otro doc.

Correr en el Droplet y commitear el resultado:
    python -m scripts.gen_pedidos
    python -m scripts.gen_pedidos --estado nuevo     # solo los sin triar
    python -m scripts.gen_pedidos --marcar 12 aceptado --nota "va en el sprint"
    python -m scripts.gen_pedidos --marcar 13 descartado

El archivo se REGENERA entero en cada corrida (la fuente es la tabla, no el
markdown): para cambiar el estado de un pedido usá --marcar, no edites el .md
esperando que persista.
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

from core.postgres import get_pool

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALIDA = os.path.join(RAIZ, "docs", "PEDIDOS.md")

_ESTADOS = ("nuevo", "aceptado", "descartado", "hecho")
_TITULOS_TIPO = {
    "mejora": "Mejoras pedidas",
    "falta_dato": "Datos que no se encuentran",
    "bug": "Reportes de algo que anda mal",
    "otro": "Otros",
}


def _marcar(pedido_id: int, estado: str, nota: str | None) -> None:
    if estado not in _ESTADOS:
        raise SystemExit(f"estado inválido: {estado!r} (válidos: {', '.join(_ESTADOS)})")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE manager.pedidos SET estado = %s, "
            "notas = COALESCE(%s, notas) WHERE id = %s",
            (estado, nota, pedido_id))
        if cur.rowcount == 0:
            raise SystemExit(f"no existe el pedido #{pedido_id}")
    print(f"#{pedido_id} → {estado}" + (f" · {nota}" if nota else ""))


def _leer(estado: str | None) -> list[dict]:
    cond, params = "", ()
    if estado:
        cond, params = "WHERE estado = %s", (estado,)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, ts, usuario, vista, tipo, titulo, texto, contexto, estado, notas "
            f"FROM manager.pedidos {cond} ORDER BY ts DESC", params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _render(pedidos: list[dict]) -> str:
    por_estado: dict[str, list[dict]] = defaultdict(list)
    for p in pedidos:
        por_estado[p["estado"] or "nuevo"].append(p)

    out = [
        "# PEDIDOS — buzón de la mesa",
        "",
        "> **AUTO-GENERADO** por `python -m scripts.gen_pedidos` desde",
        "> `manager.pedidos`. NO editar a mano: se regenera entero. Para triar",
        "> un pedido: `python -m scripts.gen_pedidos --marcar <id> aceptado`",
        "> (estados: nuevo · aceptado · descartado · hecho; `--nota \"…\"` agrega",
        "> el porqué).",
        "",
        "Los carga la gente hablándole al copiloto mientras trabaja (tool",
        "`registrar_pedido`, ver `api/services/copiloto/pedidos.py`): el que",
        "tiene la idea la dice donde le surgió, sin abrir un ticket.",
        "",
        f"**{len(pedidos)} pedidos** · "
        + " · ".join(f"{e}: {len(por_estado.get(e, []))}" for e in _ESTADOS),
        "",
    ]

    for estado in _ESTADOS:
        grupo = por_estado.get(estado) or []
        if not grupo:
            continue
        out += [f"## {estado.upper()} ({len(grupo)})", ""]
        por_tipo: dict[str, list[dict]] = defaultdict(list)
        for p in grupo:
            por_tipo[p["tipo"] or "otro"].append(p)
        for tipo, titulo in _TITULOS_TIPO.items():
            items = por_tipo.get(tipo) or []
            if not items:
                continue
            out += [f"### {titulo}", ""]
            for p in items:
                fecha = str(p["ts"])[:16]
                quien = (p["usuario"] or "—").split("@")[0]
                out.append(f"- **#{p['id']} · {p['titulo']}**  ")
                out.append(f"  <sub>{fecha} · {quien} · desde `{p['vista'] or '—'}`</sub>  ")
                out.append(f"  {(p['texto'] or '').strip()}")
                if p.get("contexto") and p["contexto"] != p["texto"]:
                    out.append(f"  <sub>contexto: {p['contexto'].strip()}</sub>")
                if p.get("notas"):
                    out.append(f"  <sub>📌 {p['notas'].strip()}</sub>")
                out.append("")
    if not pedidos:
        out += ["_Sin pedidos todavía._", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--estado", default=None, help=f"filtrar ({'|'.join(_ESTADOS)})")
    ap.add_argument("--marcar", nargs=2, metavar=("ID", "ESTADO"),
                    help="cambiar el estado de un pedido y regenerar")
    ap.add_argument("--nota", default=None, help="nota de triage (con --marcar)")
    args = ap.parse_args()

    if args.marcar:
        _marcar(int(args.marcar[0]), args.marcar[1], args.nota)

    pedidos = _leer(args.estado)
    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write(_render(pedidos))
    print(f"{len(pedidos)} pedidos → {os.path.relpath(SALIDA, RAIZ)}")
    print("commiteá el archivo para que quede versionado y Claude lo lea.")


if __name__ == "__main__":
    main()
