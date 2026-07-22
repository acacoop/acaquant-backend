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


def contenido(estado: str | None = None) -> tuple[str, int]:
    """El markdown del buzón, en memoria. (texto, cantidad de pedidos)."""
    pedidos = _leer(estado)
    return _render(pedidos), len(pedidos)


def regenerar(estado: str | None = None) -> int:
    """Escribe docs/PEDIDOS.md EN DISCO. Para la máquina de desarrollo.

    En el SERVER no se usa: ver `publicar()`. Dejar el archivo suelto en el
    checkout de producción bloquea el `git pull` del deploy ("untracked working
    tree files would be overwritten by merge") — pasó apenas se uso en serio,
    2026-07-22."""
    texto, n = contenido(estado)
    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write(texto)
    return n


_RAMA_PUBLICACION = "main"


def _git(*args: str, index: str | None = None, entrada: bytes | None = None) -> str:
    """git con salida limpia. `index` usa un índice TEMPORAL (GIT_INDEX_FILE):
    lo que se arma ahí no toca el staging real del checkout."""
    env = {**os.environ, "GIT_INDEX_FILE": index} if index else None
    r = subprocess.run(["git", *args], cwd=RAIZ, check=True, capture_output=True,
                       timeout=120, env=env, input=entrada)
    return r.stdout.decode(errors="replace").strip()


def publicar(texto: str | None = None) -> str:
    """Publica el buzón en el repo SIN TOCAR NADA del checkout del server:
    ni el working tree, ni la rama, ni el índice, ni el disco.

    Cierra el último eslabón manual: el buzón vive en la DB del Droplet y
    Claude Code lo lee desde el repo. Sin esto había que acordarse de
    commitear, que es el tipo de paso que hace que un circuito automático deje
    de usarse.

    POR QUÉ NO ES UN `git add` + `commit` + `push` NORMAL
    El checkout del Droplet es PRODUCCIÓN y casi siempre está detrás de `main`
    (el desarrollo se pushea desde otra máquina). Un commit local ahí no puede
    pushearse — rechazado por fast-forward — y la salida fácil sería que el job
    haga `git pull` antes. Eso convertiría un cron en un DEPLOY AUTOMÁTICO:
    código nuevo apareciendo en producción sin que nadie lo decida, y jobs
    posteriores corriendo una versión que nadie revisó ni reinició. No.

    En su lugar se construye el commit "al costado" con plumbing: se toma el
    árbol de `origin/main`, se le cambia UN archivo y se pushea ese commit.

    POR QUÉ TAMPOCO ESCRIBE EN DISCO
    La primera versión sí escribía, y el archivo quedaba SUELTO (untracked) en
    el checkout de producción. Resultado: el `git pull` del deploy empezó a
    abortar con "untracked working tree files would be overwritten by merge" —
    o sea, el buzón bloqueaba los deploys. El contenido va del SQL al objeto de
    git por stdin, sin pasar por el filesystem.

    Nunca levanta: si algo falla lo dice y el buzón sigue intacto en la DB."""
    rel = os.path.relpath(SALIDA, RAIZ).replace(os.sep, "/")
    indice_tmp = os.path.join(RAIZ, ".git", "index-pedidos.tmp")
    try:
        if texto is None:
            texto, _n = contenido()
        _git("fetch", "origin", _RAMA_PUBLICACION)
        base = _git("rev-parse", f"origin/{_RAMA_PUBLICACION}")
        blob = _git("hash-object", "-w", "--stdin", entrada=texto.encode("utf-8"))
        # ¿el archivo ya es idéntico al publicado? entonces no hay nada que hacer
        try:
            if _git("rev-parse", f"{base}:{rel}") == blob:
                return "sin cambios que publicar"
        except subprocess.CalledProcessError:
            pass                      # todavía no existe en el repo — se crea
        _git("read-tree", base, index=indice_tmp)
        _git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}",
             index=indice_tmp)
        tree = _git("write-tree", index=indice_tmp)
        commit = _git("commit-tree", tree, "-p", base,
                      "-m", "docs(pedidos): actualizar buzón")
        _git("push", "origin", f"{commit}:refs/heads/{_RAMA_PUBLICACION}")
        return f"publicado en {_RAMA_PUBLICACION} ({commit[:8]}) — el checkout no se tocó"
    except subprocess.CalledProcessError as e:
        det = (e.stderr or b"").decode(errors="replace").strip().splitlines()
        return ("NO se pudo publicar: " + (det[-1] if det else str(e))
                + " — los pedidos están a salvo en la base; se puede reintentar "
                  "con: python -m scripts.gen_pedidos --publicar")
    except Exception as e:
        return f"NO se pudo publicar ({type(e).__name__}: {e})"
    finally:
        if os.path.exists(indice_tmp):
            os.remove(indice_tmp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--estado", default=None, help=f"filtrar ({'|'.join(_ESTADOS)})")
    ap.add_argument("--marcar", nargs=2, metavar=("ID", "ESTADO"),
                    help="cambiar el estado de un pedido y regenerar")
    ap.add_argument("--nota", default=None, help="nota de triage (con --marcar)")
    ap.add_argument("--publicar", action="store_true",
                    help="pushear al repo SIN escribir en disco (modo server)")
    args = ap.parse_args()

    if args.marcar:
        _marcar(int(args.marcar[0]), args.marcar[1], args.nota)

    # Dos modos EXCLUYENTES a propósito:
    #   --publicar → va al repo, NO toca el disco (el server: escribir ahí un
    #                archivo suelto rompe el `git pull` del deploy).
    #   sin flag   → escribe el archivo (la máquina de desarrollo, para leerlo).
    if args.publicar:
        texto, n = contenido(args.estado)
        print(f"{n} pedidos")
        print(publicar(texto))
    else:
        n = regenerar(args.estado)
        print(f"{n} pedidos → {os.path.relpath(SALIDA, RAIZ)}")
        print("(en el server usá --publicar: no deja el archivo en el checkout)")


if __name__ == "__main__":
    main()
