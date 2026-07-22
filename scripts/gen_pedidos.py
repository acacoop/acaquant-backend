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
import subprocess
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
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE manager.pedidos SET estado = %s, "
                "notas = COALESCE(%s, notas) WHERE id = %s",
                (estado, nota, pedido_id))
            if cur.rowcount == 0:
                raise SystemExit(f"no existe el pedido #{pedido_id}")
    except Exception as e:
        if "manager.pedidos" in str(e) and "exist" in str(e).lower():
            raise SystemExit(_FALTA_TABLA) from None
        raise
    print(f"#{pedido_id} → {estado}" + (f" · {nota}" if nota else ""))


_FALTA_TABLA = (
    "La tabla manager.pedidos todavía no existe en la base.\n"
    "Aplicá el schema y volvé a correr:\n"
    "    python -m scripts.apply_schema && systemctl restart api.service"
)


def _leer(estado: str | None) -> list[dict]:
    cond, params = "", ()
    if estado:
        cond, params = "WHERE estado = %s", (estado,)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT id, ts, usuario, vista, tipo, titulo, texto, contexto, estado, "
                f"notas, impacto, esfuerzo, spec, duplicado_de, decidido_por "
                f"FROM manager.pedidos {cond} ORDER BY ts DESC", params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    except Exception as e:
        # un traceback de psycopg no le dice a nadie QUÉ hacer
        if "manager.pedidos" in str(e) and "exist" in str(e).lower():
            raise SystemExit(_FALTA_TABLA) from None
        raise


# Orden de la cola de trabajo: lo que más rinde primero. Mismos pesos que
# jobs/pedidos_triage.py — se importan de ahí para que no puedan divergir.
def _peso(p: dict) -> tuple:
    from jobs.pedidos_triage import _PESO_ESFUERZO, _PESO_IMPACTO
    return (_PESO_IMPACTO.get(p.get("impacto"), 1),
            _PESO_ESFUERZO.get(p.get("esfuerzo"), 1), p["id"])


def _cola(pedidos: list[dict]) -> list[str]:
    """La COLA DE TRABAJO: lo aceptado, priorizado, con su especificación.

    Va arriba de todo y es lo único que hace falta leer para ponerse a
    trabajar — el resto del archivo es historial. Lo levanta el comando
    `/pedidos` de Claude Code."""
    aceptados = sorted((p for p in pedidos if p["estado"] == "aceptado"), key=_peso)
    if not aceptados:
        return ["## 🛠 COLA DE TRABAJO", "",
                "_Nada aprobado pendiente._ Los pedidos nuevos los tría",
                "`jobs/pedidos_triage.py` y se aprueban desde Telegram.", ""]
    out = [f"## 🛠 COLA DE TRABAJO ({len(aceptados)})", "",
           "Aprobados y sin hacer, **ordenados por lo que más rinde** (impacto alto /",
           "esfuerzo chico primero). Al terminar uno:",
           "`python -m scripts.gen_pedidos --marcar <id> hecho`.", ""]
    for p in aceptados:
        out.append(f"### #{p['id']} · {p['titulo']}")
        out.append(f"<sub>impacto **{p.get('impacto') or '?'}** · esfuerzo "
                   f"**{p.get('esfuerzo') or '?'}** · pedido desde "
                   f"`{p['vista'] or '—'}` · aprobó {p.get('decidido_por') or '—'}</sub>")
        out.append("")
        if p.get("spec"):
            out.append(f"**Propuesta:** {p['spec'].strip()}")
            out.append("")
        out.append(f"> _Lo que pidieron:_ {(p['texto'] or '').strip()}")
        out.append("")
    return out


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
        "tiene la idea la dice donde le surgió, sin abrir un ticket. Después",
        "`jobs/pedidos_triage.py` los tría con IA (duplicados, impacto,",
        "esfuerzo, propuesta técnica) y avisa por Telegram con botones para",
        "aprobar; `jobs/pedidos_inbox.py` aplica esa decisión.",
        "",
        f"**{len(pedidos)} pedidos** · "
        + " · ".join(f"{e}: {len(por_estado.get(e, []))}" for e in _ESTADOS),
        "",
        *_cola(pedidos),
        "---",
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
                triaje = ""
                if p.get("impacto") or p.get("esfuerzo"):
                    triaje = (f" · impacto {p.get('impacto') or '?'}"
                              f" / esfuerzo {p.get('esfuerzo') or '?'}")
                out.append(f"- **#{p['id']} · {p['titulo']}**  ")
                out.append(f"  <sub>{fecha} · {quien} · desde "
                           f"`{p['vista'] or '—'}`{triaje}</sub>  ")
                out.append(f"  {(p['texto'] or '').strip()}")
                if p.get("spec"):
                    out.append(f"  <sub>💡 {p['spec'].strip()}</sub>")
                if p.get("contexto") and p["contexto"] != p["texto"]:
                    out.append(f"  <sub>contexto: {p['contexto'].strip()}</sub>")
                if p.get("notas"):
                    out.append(f"  <sub>📌 {p['notas'].strip()}</sub>")
                out.append("")
    if not pedidos:
        out += ["_Sin pedidos todavía._", ""]
    return "\n".join(out)


def regenerar(estado: str | None = None) -> int:
    """Reescribe docs/PEDIDOS.md desde la tabla. Devuelve cuántos pedidos hay.
    Es función (no solo CLI) para que los jobs la llamen sin lanzar un proceso."""
    pedidos = _leer(estado)
    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write(_render(pedidos))
    return len(pedidos)


def publicar() -> str:
    """Commitea y pushea SOLO docs/PEDIDOS.md. Devuelve un mensaje de estado.

    Existe para cerrar el último eslabón manual: el archivo se genera en el
    Droplet y Claude Code lo lee desde el repo. Sin esto había que acordarse de
    commitear a mano, que es exactamente el tipo de paso que hace que un
    circuito automático deje de usarse.

    Acotado a UN path a propósito: nunca commitea otra cosa que pueda haber
    quedada tocada en el checkout del server. Si falla (sin credencial de push,
    checkout raro), NO revienta: lo dice y el archivo queda igual en disco."""
    rel = os.path.relpath(SALIDA, RAIZ)
    try:
        subprocess.run(["git", "add", "--", rel], cwd=RAIZ, check=True,
                       capture_output=True, timeout=30)
        # ¿cambió algo? sin esto, un commit vacío falla y ensucia el log
        if subprocess.run(["git", "diff", "--cached", "--quiet", "--", rel],
                          cwd=RAIZ, timeout=30).returncode == 0:
            return "sin cambios que publicar"
        subprocess.run(["git", "commit", "-m", "docs(pedidos): actualizar buzón", "--", rel],
                       cwd=RAIZ, check=True, capture_output=True, timeout=60)
        subprocess.run(["git", "push"], cwd=RAIZ, check=True,
                       capture_output=True, timeout=120)
        return "publicado (commit + push de docs/PEDIDOS.md)"
    except subprocess.CalledProcessError as e:
        det = (e.stderr or b"").decode(errors="replace").strip().splitlines()
        return ("NO se pudo publicar: " + (det[-1] if det else str(e))
                + " — el archivo está actualizado en el server; "
                  "commitealo a mano si querés que Claude lo vea")
    except Exception as e:
        return f"NO se pudo publicar ({type(e).__name__}: {e})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--estado", default=None, help=f"filtrar ({'|'.join(_ESTADOS)})")
    ap.add_argument("--marcar", nargs=2, metavar=("ID", "ESTADO"),
                    help="cambiar el estado de un pedido y regenerar")
    ap.add_argument("--nota", default=None, help="nota de triage (con --marcar)")
    ap.add_argument("--publicar", action="store_true",
                    help="además commitear y pushear docs/PEDIDOS.md")
    args = ap.parse_args()

    if args.marcar:
        _marcar(int(args.marcar[0]), args.marcar[1], args.nota)

    n = regenerar(args.estado)
    print(f"{n} pedidos → {os.path.relpath(SALIDA, RAIZ)}")
    if args.publicar:
        print(publicar())
    else:
        print("commiteá el archivo (o usá --publicar) para que Claude lo lea.")


if __name__ == "__main__":
    main()
