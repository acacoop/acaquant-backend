"""READ-ONLY. ¿Qué scripts de `scripts/` ya cumplieron y quedaron?

Herramienta: diag RECURRENTE (no se borra — REGLA #5).

EL PUNTO
========

La REGLA #5 dice que un `diag_*`/`fix_*`/`backfill_*` one-shot **se borra**
cuando el tema cierra. Nadie lo hace: cerrar el tema y acordarse de volver a
borrar el archivo son dos actos distintos, y el segundo no tiene quien lo pida.
Medido el 2026-08-28: **143 scripts, 87 de ellos `diag_*`**.

No es sólo espacio. Cada script viejo es una respuesta a una pregunta que ya no
se hace, y la próxima vez que alguien busque cómo diagnosticar algo va a
encontrar tres versiones y ninguna va a decirle cuál sigue siendo cierta.

CÓMO SE DECIDE — y por qué NO alcanza con «no lo llama nadie»
=============================================================

Un script se ejecuta a mano: **que nadie lo importe es lo NORMAL**, no la señal.
Por eso acá se cruzan tres cosas y ninguna manda sola:

1. **¿Lo nombra algo que no sea `scripts/`?**  Un cron, un workflow de CI, una
   skill de `.claude/`, un doc, un test, `CLAUDE.md`. Eso lo hace VIVO: hay algo
   que cuenta con él.
2. **¿Su nombre es de los que se quedan?**  `gen_*` (generadores con `--check`
   en CI), `healthcheck_*`, `smoke_*`, `perf_*`, `audit_*`, `security_*`,
   `apply_*`, `sembrar_*`. Son herramientas, no one-shots.
3. **¿Se lo tocó DESPUÉS del import?**  Un `diag_*` editado en un commit real
   es de un tema ABIERTO. Ojo con este eje, que casi me hace mentir:

   ⚠️⚠️ **LA FECHA DEL ÚLTIMO COMMIT NO ES LA EDAD DEL ARCHIVO.** La historia de
   este repo **arranca el 2026-08-22 con un commit de 1667 archivos** (el import
   al mirror de GitHub). Medido: **119 de los 143 scripts tienen ESA fecha**, que
   no dice nada sobre ellos — un script de hace dos años y uno de esa semana se
   ven idénticos. La primera versión de este diag usaba «tocado hace menos de
   21 días» y clasificó 57 scripts como «tema abierto» leyendo la fecha del
   import. Por eso acá NO se usa la fecha: se compara contra los commits RAÍZ.
   Lo que quedó pegado al import es «edad desconocida», y se dice así.

⚠️ **Esto NO borra nada y no puede decidir solo.** El eje que de verdad importa
—«¿el tema que este script contestaba, cerró?»— no está escrito en ningún lado:
lo sabe el usuario. Lo que hace este diag es poner la lista corta adelante para
que decidir cueste un minuto y no una tarde.

Uso:
    python -m scripts.diag_scripts_muertos           # el informe
    python -m scripts.diag_scripts_muertos --rm      # imprime el `git rm` sugerido
"""
from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# Prefijos que se quedan aunque nadie los nombre: son herramientas recurrentes.
SE_QUEDAN = ("gen_", "healthcheck_", "smoke_", "perf_", "audit_", "security_",
             "apply_", "sembrar_", "export_", "run_")



def _nombran(mod: str) -> list[str]:
    """Archivos FUERA de `scripts/` que mencionan este script. Vacío = nadie.

    ⚠️ Se busca el nombre PELADO (`diag_ticker`) y no sólo `scripts.diag_ticker`,
    aunque eso traiga algún falso positivo. **Los dos errores no cuestan igual**:
    de más deja un script vivo un tiempo más; de menos borra algo que un cron
    llamaba. Con `scripts[./]` solo, `pedir_pata` —cuyo trabajo se mudó adentro
    de `agente/arreglos.py`— habría salido igual, pero cualquier doc que lo
    nombre en prosa se perdía.
    """
    pat = rf"\b{mod}\b"
    try:
        r = subprocess.run(
            ["git", "grep", "-l", "-E", pat, "--", ".", ":(exclude)scripts/"],
            cwd=RAIZ, capture_output=True, text=True, timeout=30)
    except Exception:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _raices() -> set[str]:
    """Los commits RAÍZ: el import masivo. Lo que no se tocó desde ahí no tiene
    edad conocida — ver la advertencia del docstring de arriba."""
    try:
        r = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"],
                           cwd=RAIZ, capture_output=True, text=True, timeout=20)
        return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    except Exception:
        return set()


def _ultimo_commit(rel: str, raices: set[str]) -> tuple[str, bool]:
    """`(fecha, se_tocó_después_del_import)`. La fecha es informativa: si el
    último commit ES una raíz, no dice nada sobre este archivo."""
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%H %cs", "--", rel],
                           cwd=RAIZ, capture_output=True, text=True, timeout=20)
        partes = r.stdout.strip().split()
        if len(partes) != 2:
            # Sin commit todavía: es de HOY, no un resto. Lo contrario sería
            # proponerse borrar el script que uno acaba de escribir.
            return "sin commit", True
        sha, fecha = partes
        return fecha, sha not in raices
    except Exception:
        return "?", False


def _titulo(p: pathlib.Path) -> str:
    """La primera línea del docstring: qué contestaba este script."""
    try:
        doc = ast.get_docstring(ast.parse(p.read_text(encoding="utf-8"))) or ""
    except Exception:
        return ""
    return doc.strip().splitlines()[0][:64] if doc.strip() else ""


def main() -> int:
    quiere_rm = "--rm" in sys.argv
    archivos = sorted(p for p in (RAIZ / "scripts").glob("*.py")
                      if p.stem != "__init__")

    raices = _raices()
    vivos, tibios, muertos = [], [], []
    for p in archivos:
        mod = p.stem
        rel = f"scripts/{p.name}"
        refs = _nombran(mod)
        fecha, tocado = _ultimo_commit(rel, raices)
        item = {"mod": mod, "rel": rel, "kb": p.stat().st_size / 1024,
                "refs": refs, "fecha": fecha, "tocado": tocado, "que": _titulo(p)}
        if refs or mod.startswith(SE_QUEDAN):
            vivos.append(item)
        elif tocado:
            tibios.append(item)
        else:
            muertos.append(item)

    # ⚠️⚠️ **LA VIDA SE HEREDA.** El eje 1 mira fuera de `scripts/`, porque que
    # un script no sea importado es lo NORMAL. Pero eso deja un agujero: un
    # script al que llama un script VIVO, está vivo.
    #
    # Lo pagué en la primera corrida (2026-08-28): `diag_mayor_estado` lo llama
    # el crontab, y en pantalla le dice al operador «corré `diag_mayor_mapeo`».
    # `diag_mayor_mapeo` no lo nombra nadie más, así que cayó en el montón de
    # borrar — y borrarlo dejaba a una herramienta del cron mandando a correr
    # algo que ya no existe. No falla nada: falla el día que alguien lea el
    # cartel y no encuentre el archivo.
    #
    # Se propaga hasta que no se mueve nadie más: si A vive y nombra a B, y B
    # nombra a C, los tres viven.
    if muertos:
        cambio = True
        while cambio:
            cambio = False
            texto_vivo = "\n".join(
                (RAIZ / i["rel"]).read_text(encoding="utf-8", errors="ignore")
                for i in vivos + tibios)
            for i in list(muertos):
                if re.search(rf"\b{re.escape(i['mod'])}\b", texto_vivo):
                    i["refs"] = ["← lo nombra otro script vivo"]
                    vivos.append(i)
                    muertos.remove(i)
                    cambio = True

    total_kb = sum(p.stat().st_size for p in archivos) / 1024
    print(f"\n{'=' * 78}\n SCRIPTS QUE YA CUMPLIERON — {len(archivos)} archivos, "
          f"{total_kb:,.0f} kB\n{'=' * 78}")

    print(f"\n  ① SE QUEDAN ({len(vivos)})")
    print("  Los nombra un cron / CI / una skill / un doc, o su prefijo es de"
          " herramienta.")
    for i in sorted(vivos, key=lambda x: x["mod"]):
        por = i["refs"][0] if i["refs"] else f"prefijo {i['mod'].split('_')[0]}_"
        print(f"      {i['mod']:44} ← {por}")

    print(f"\n  ② TIBIOS ({len(tibios)}) — tocados DESPUÉS del import")
    print("  Nadie los nombra, pero se los editó en un commit real: tema abierto.")
    for i in sorted(tibios, key=lambda x: x["fecha"], reverse=True):
        print(f"      {i['mod']:44} {i['fecha']}  {i['que']}")

    print(f"\n  ③ CANDIDATOS A BORRAR ({len(muertos)})")
    print("  Nadie los nombra y NO se los tocó desde el import: nada en el repo\n"
          "  cuenta con ellos. ⚠ El eje que decide —¿el tema cerró?— no está en el\n"
          "  código: lo sabés vos. Y su edad real es DESCONOCIDA (ver el docstring).")
    print(f"\n      {'SCRIPT':44} {'kB':>6}  QUÉ CONTESTABA")
    for i in sorted(muertos, key=lambda x: x["mod"]):
        print(f"      {i['mod']:44} {i['kb']:>6,.0f}  {i['que']}")
    print(f"\n      → juntos pesan {sum(i['kb'] for i in muertos):,.0f} kB"
          f" de los {total_kb:,.0f} kB\n")

    if quiere_rm and muertos:
        print("  El comando, para revisar y pegar:\n")
        print("      git rm " + " \\\n             ".join(
            sorted(i["rel"] for i in muertos)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
