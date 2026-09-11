"""`scripts/diag_tabla_quieta.py` — **¿EL JOB NO CORRIÓ, O CORRIÓ Y NO ESCRIBIÓ?**

Read-only. Doc: `docs/AGENT.md` §0.ew.

EL PROBLEMA QUE RESUELVE
========================

`tabla_quieta` mira UNA cosa —hace cuánto que la tabla no escribe— y de ahí
concluye sobre el JOB. Pero «no escribió» tiene tres causas que se atienden de
formas OPUESTAS y en la tarjeta se ven idénticas:

    el job NO CORRIÓ            → el problema es el scheduler
    corrió y NO TRAJO NADA      → el proveedor no publicó: NO hay nada roto
    corrió, trajo, y NO ESCRIBIÓ→ la escritura está fallando: ahí sí hay bug

La tercera es la única que justifica el aviso, y la segunda es la que más se
repite: `research.bcra_series` y `research.fred_observations` sólo tienen un
sello de ALTA (`ingestado_en`), y sus jobs son incrementales por watermark — si
el BCRA no publicó un día nuevo, mandan CERO filas y el sello no se mueve. El
job está perfecto y la tabla figura «atrasada» todas las mañanas.

El dato que separa las tres YA EXISTE y el detector no lo mira: `manager.job_runs`
(lo escribe `JobRunLogger` en cada corrida, con su `status` y sus `stats`).

Y dos preguntas más que una persona hace delante de la tarjeta y el detector
tampoco: **¿el día sin dato era hábil?** (`mercado.dias_habiles` contra los
días que sí tienen dato — un feriado entre semana no lo descuenta nadie) y
**¿las otras veces que cantó, qué día y a qué hora fue?** (`agente.hallazgos`:
si el «crónico 4× en 30 d» cae siempre un lunes o después de un feriado, es el
calendario, no el job).

    python -m scripts.diag_tabla_quieta                 # las que el agente tiene abiertas
    python -m scripts.diag_tabla_quieta --tabla bancos.sync_log
    python -m scripts.diag_tabla_quieta --corridas 12   # cuántas corridas mostrar
"""
from __future__ import annotations

import argparse
import sys

from core.postgres import get_pool

CORRIDAS = 8


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _abiertas() -> list[str]:
    """Las tablas sobre las que `tabla_quieta` tiene un hallazgo ABIERTO. El
    sujeto de esa habilidad ES el `schema.tabla`, así que no hay que parsear
    nada."""
    from agente import tipos
    return [f[0] for f in _filas(
        "SELECT sujeto FROM agente.hallazgos "
        " WHERE habilidad = 'tabla_quieta' AND estado = ANY(%s) "
        " ORDER BY detectado_at", (list(tipos.ABIERTOS),))]


def _ultimo_dato(tabla: str, col: str):
    """`max(col)` de esa tabla, ahora. `None` si no se pudo mirar — que no es
    «está vacía»."""
    try:
        # `tabla` y `col` salen del perfil de la propia base, nunca del usuario.
        return _filas(f'SELECT max("{col}") FROM {tabla}')[0][0]
    except Exception as e:
        print(f"    ⚠ no pude leer {tabla}.{col}: {str(e).splitlines()[0][:120]}")
        return None


def _perfil(tabla: str) -> dict | None:
    from agente import tablas
    schema, _, corta = tabla.partition(".")
    for p in tablas.perfiles():
        if p["schema"] == schema and p["tabla"] == corta:
            return p
    return None


def _sello(col: str) -> str:
    """De qué CLASE es la columna con la que se mide la frescura. Es la mitad
    que decide si «la tabla no escribió» dice algo del job."""
    from agente import tablas
    if col in tablas._SELLO_ESCRITURA:
        return "ESCRITURA — se mueve cada vez que el job toca la fila"
    if col in tablas._SELLO_ALTA:
        return ("ALTA — ⚠️ sólo se mueve cuando entra dato NUEVO. Un job "
                "incremental que no trae nada la deja quieta SIN estar rota")
    if col in tablas._FECHA_NEGOCIO:
        return "NEGOCIO — de qué día son los datos, no cuándo se escribieron"
    return "sin clasificar (ver agente/tablas.py)"


def _corridas(job: str, n: int) -> list[tuple]:
    """Las últimas corridas del job, de `manager.job_runs`. `tipo` es el nombre
    con el que `JobRunLogger` las anota, que puede no ser el módulo."""
    tipo = job.split(".")[-1]
    return _filas(
        "SELECT started_at, finished_at, data->>'status', data->'stats' "
        "  FROM manager.job_runs WHERE tipo = %s "
        " ORDER BY started_at DESC LIMIT %s", (tipo, n))


def _calendario(tabla: str, col: str, dias: int = 10) -> None:
    """Día por día: ¿fue hábil según `mercado.dias_habiles`? ¿tiene dato en
    `col`? La fila que dice «hábil SIN dato» es la única que acusa al job."""
    from datetime import UTC, datetime, timedelta
    hoy = datetime.now(UTC).date()
    desde = hoy - timedelta(days=dias)
    habiles = {r[0] for r in _filas(
        "SELECT fecha FROM mercado.dias_habiles WHERE fecha BETWEEN %s AND %s",
        (desde, hoy))}
    try:
        con_dato = {r[0] for r in _filas(
            f'SELECT DISTINCT "{col}"::date FROM {tabla} WHERE "{col}" >= %s', (desde,))}
    except Exception as e:
        print(f"    ⚠ no pude listar los días con dato: {str(e).splitlines()[0][:120]}")
        return
    print(f"\n  CALENDARIO (últimos {dias} días · hábil según mercado.dias_habiles):")
    if not habiles:
        print("    ⚠ mercado.dias_habiles no tiene filas en la ventana: no sé qué día fue hábil")
    for i in range(dias, -1, -1):
        d = desde + timedelta(days=dias - i)
        es_habil = ("sí" if d in habiles else "no") if habiles else "?"
        marca = "   ← hábil SIN dato" if d in habiles and d not in con_dato else ""
        print(f"    {d:%a %d/%m}  hábil={es_habil:<2}  dato={'sí' if d in con_dato else 'NO'}{marca}")


def _historial(tabla: str, dias: int = 30) -> None:
    """Cada vez que `tabla_quieta` cantó esta tabla: cuándo apareció (día de la
    semana incluido), cuánto duró, cómo se cerró y si alguien lo leyó."""
    from core.tz import hora_ar
    filas = _filas(
        "SELECT id, detectado_at, veces, estado, cerrado_at, cerrado_como, leido_por "
        "  FROM agente.hallazgos "
        " WHERE habilidad = 'tabla_quieta' AND sujeto = %s "
        "   AND detectado_at >= now() - make_interval(days => %s) "
        " ORDER BY detectado_at DESC", (tabla, dias))
    print(f"\n  HISTORIAL ({len(filas)} vez/veces en {dias} días · hora ART):")
    for hid, det, veces, estado, cer, como, quien in filas:
        duro = f"duró {(cer - det).total_seconds() / 3600:.1f} h" if cer else "ABIERTO"
        print(f"    #{hid} {hora_ar(det)} ({det:%a}) · ×{veces} · {estado:<9} · {duro} · "
              f"cierre={como or '—'} · leído={'por ' + quien if quien else 'no'}")


def _una(tabla: str, n_corridas: int) -> None:
    from agente import tablas
    from core import escribe

    _titulo(tabla)
    p = _perfil(tabla)
    col = (p or {}).get("col_fecha") or ""
    quienes = escribe.quien_escribe(tabla)
    job = escribe.que_relanzar(tabla)

    print(f"  la escriben     : {', '.join(quienes) or '— NADIE DETECTADO —'}")
    print(f"  que_relanzar    : {job or '— vacío: no hay uno de reloj —'}")
    print(f"  la dispara      : {escribe.la_dispara(tabla)}"
          + (f"  ({escribe.por_ocasion(tabla)})" if escribe.por_ocasion(tabla) else ""))
    if not p:
        print("  ⚠ sin perfil en `manager.tabla_perfil`: `tabla_quieta` no la mira")
        return
    print(f"  columna         : {col}")
    print(f"  clase de sello  : {_sello(col)}")
    print(f"  cadencia medida : {p.get('cadencia')} "
          f"(p50 {p.get('intervalo_p50_s')}s, {p.get('filas')} filas)")

    declarado = tablas.declarados().get(tabla)
    if declarado:
        print(f"  ritmo DECLARADO : cada {declarado['hueco_s']}s como mucho "
              f"(cron de {declarado['job']}, solo_habiles={declarado['solo_habiles']})")
    else:
        print("  ritmo DECLARADO : — no hay: el veredicto sale del MEDIDO —")

    ult = _ultimo_dato(tabla, col) if col else None
    if ult is not None:
        p = {**p, "ultimo_dato": ult}
    f = tablas.frescura(p, declarado=declarado)
    print(f"  último dato     : {ult}")
    print(f"  VEREDICTO       : {f['estado'].upper()} — {f['motivo']}")
    if f.get("atraso_s") is not None and f.get("tope_s"):
        print(f"  atraso / tope   : {f['atraso_s'] / 3600:.2f} h / {f['tope_s'] / 3600:.2f} h"
              + (" · fecha de NEGOCIO: es una cota" if f.get("fecha_de_negocio") else ""))
    if col:
        _calendario(tabla, col)
    _historial(tabla)

    # ── LA MITAD QUE EL DETECTOR NO MIRA ────────────────────────────────────
    if not job:
        print("\n  (sin job de reloj: no hay corridas que mirar)")
        return
    print(f"\n  ÚLTIMAS {n_corridas} CORRIDAS DE `{job}` (manager.job_runs):")
    corridas = _corridas(job, n_corridas)
    if not corridas:
        print("    ⚠ NINGUNA. O el job no usa JobRunLogger, o se anota con otro "
              "`tipo`, o de verdad no corre. Sin esto no se puede decir si la "
              "tabla está quieta porque el job no corrió.")
        return
    print(f"    {'ARRANCÓ':20} {'TERMINÓ':20} {'STATUS':9} STATS")
    for arranco, termino, status, stats in corridas:
        s = ", ".join(f"{k}={v}" for k, v in sorted((stats or {}).items())
                      if not isinstance(v, (list, dict)))[:110]
        print(f"    {str(arranco)[:19]:20} {str(termino)[:19]:20} "
              f"{(status or '?'):9} {s}")
    print("\n    → si hay corridas `ok` DESPUÉS del último dato, el job corrió y "
          "no escribió:\n      o el proveedor no trajo nada (y no hay nada roto), "
          "o la escritura falla.\n      Los `stats` de arriba dicen cuál de las dos.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tabla", default="", help="una sola (`schema.tabla`)")
    ap.add_argument("--corridas", type=int, default=CORRIDAS)
    a = ap.parse_args()

    tablas_ = [a.tabla] if a.tabla else _abiertas()
    if not tablas_:
        print("`tabla_quieta` no tiene ningún hallazgo abierto. Nada que mirar.")
        return 0
    print(f"{len(tablas_)} tabla(s): {', '.join(tablas_)}")
    for t in tablas_:
        try:
            _una(t, a.corridas)
        except Exception as e:
            print(f"\n  ⚠ {t}: {type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
