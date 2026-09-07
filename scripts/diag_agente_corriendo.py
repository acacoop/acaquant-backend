"""`scripts/diag_agente_corriendo.py` — **¿QUÉ CÓDIGO ESTÁ CORRIENDO EL AGENTE?**

Read-only. **No escribe una sola fila.** Doc: `docs/AGENT.md` §0.ea.

## La pregunta

«Deployé y sigue igual» tiene CUATRO causas posibles y desde afuera se ven
idénticas:

    1. el REPO del Droplet quedó viejo          → `git pull` no trajo nada
    2. el DAEMON quedó viejo                     → nadie reinició `agente.service`
    3. el daemon es nuevo pero NO CORRIÓ todavía → `on_faltante` es de ventana
                                                   `rueda` y corre cada 2 h
    4. el código nuevo no hace lo que creíamos   → el bug es mío

Adivinar cuál es la causa #1 de perder una tarde. Este diag las separa con
datos, y cada bloque contesta UNA.

## Cómo distingue el código viejo del nuevo, sin mirar el commit

El detector `on_faltante` cambió de forma: antes emitía **una fila por ON**
(regla `no_esta_en_curvas`), ahora las que la mesa NO tiene van juntas en **una
fila de familia** (regla `no_estan_en_curvas`, sujeto «ONs HARD DÓLAR»).

Esas dos reglas son la huella digital. Si en la base hay hallazgos ABIERTOS con
la regla vieja **escritos después del deploy**, el que los escribió corre código
viejo — sin importar lo que diga `git log`.

    python -m scripts.diag_agente_corriendo
"""
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime

from core.postgres import get_pool


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _hace(ts) -> str:
    if ts is None:
        return "nunca"
    seg = (datetime.now(UTC) - ts).total_seconds()
    if seg < 90:
        return f"hace {int(seg)} s"
    if seg < 5400:
        return f"hace {int(seg // 60)} min"
    return f"hace {seg / 3600:.1f} h"


def _systemctl(*args: str) -> str:
    try:
        r = subprocess.run(["systemctl", *args], capture_output=True,
                           text=True, timeout=10)
        return (r.stdout or r.stderr).strip()
    except Exception as e:                                    # pragma: no cover
        return f"(no pude preguntarle a systemd: {e})"


def main() -> int:
    # ── 1 ──────────────────────────────────────────────────────────────────
    _titulo("1. EL SERVICIO — ¿existe, está vivo, y desde cuándo?")
    print("  Si `ActiveEnterTimestamp` es ANTERIOR al deploy, el daemon nunca se\n"
          "  reinició y sigue con el código de esa fecha (§0.ea).\n")
    print("  is-active   :", _systemctl("is-active", "agente.service") or "—")
    print("  is-enabled  :", _systemctl("is-enabled", "agente.service") or "—")
    for k in ("ActiveEnterTimestamp", "ExecMainStartTimestamp", "NRestarts"):
        print(f"  {k:<12}:", _systemctl("show", "agente.service", "-p", k) or "—")

    # ── 2 ──────────────────────────────────────────────────────────────────
    _titulo("2. EL LATIDO — ¿el daemon está escribiendo?")
    lat = _filas("SELECT at, proximo_en_s FROM agente.latido WHERE id = 1")
    if not lat:
        print("  ⚠️ no hay latido: el daemon nunca escribió. O no corre, o no llega\n"
              "     a la base.")
    else:
        at, prox = lat[0]
        print(f"  último latido: {at} ({_hace(at)}) · vuelve en {prox or '?'} s")

    # ── 3 ──────────────────────────────────────────────────────────────────
    _titulo("3. `on_faltante` — cuándo miró por última vez y con qué resultado")
    h = _filas("SELECT ultima_corrida_at, ultimo_resultado, ultimo_error, "
               "       ventana, cada_segundos, activa, corridas_hoy "
               "  FROM agente.habilidades WHERE nombre = 'on_faltante'")
    if not h:
        print("  ⚠️ la habilidad no está en la tabla: el catálogo no se sembró.")
    else:
        at, res, err, ventana, cada, activa, hoy = h[0]
        print(f"  última corrida : {at} ({_hace(at)})")
        print(f"  resultado      : {res or '—'}"
              + (f" · {err[:120]}" if err else ""))
        print(f"  ventana        : {ventana} · cada {cada // 3600} h · "
              f"activa={activa} · corridas hoy={hoy}")
        if ventana == "rueda":
            print("\n  ⚠️ VENTANA `rueda`: fuera de 13-20 UTC de lunes a viernes NO corre,\n"
                  "     ni siquiera con el daemon recién reiniciado. Para forzarla ahora:\n"
                  "     el botón «correr» al lado de `on_faltante` en la tab HABILIDADES\n"
                  "     (usa `correr_una`, que ignora la ventana a propósito).")

    # ── 4 ──────────────────────────────────────────────────────────────────
    _titulo("4. LA HUELLA DIGITAL — ¿qué código escribió lo que se ve?")
    print("  `no_esta_en_curvas`  = una fila POR ON  → código VIEJO\n"
          "  `no_estan_en_curvas` = UNA fila de familia → código NUEVO\n")
    por_regla = _filas("""
        SELECT regla, estado, count(*), max(visto_ultima_vez)
          FROM agente.hallazgos
         WHERE habilidad = 'on_faltante'
         GROUP BY regla, estado
         ORDER BY regla, estado
    """)
    if not por_regla:
        print("  no hay ningún hallazgo de `on_faltante`.")
    else:
        print(f"  {'REGLA':<22} {'ESTADO':<12} {'N':>5}  ÚLTIMA VEZ QUE SE VIO")
        print("  " + "─" * 74)
        for regla, estado, n, ultimo in por_regla:
            print(f"  {regla:<22} {estado:<12} {n:>5}  {ultimo} ({_hace(ultimo)})")

    # ── 5 ──────────────────────────────────────────────────────────────────
    _titulo("5. EL CÓDIGO DE ESTE CHECKOUT — qué produciría si corriera")
    print("  Esto corre el detector EN ESTE PROCESO (el del checkout), sin guardar\n"
          "  nada. Separa «el repo está viejo» de «el proceso está viejo»:\n"
          "  si acá sale UNA fila de familia y en la base hay 200 individuales,\n"
          "  el repo está bien y el que corre viejo es el daemon.\n")
    try:
        from agente import fuentes, tipos
        from agente.detectores import mercado
        print(f"  FAMILIA_ON en el módulo: {getattr(mercado, 'FAMILIA_ON', '—')}")
        fuentes.refrescar()
        try:
            hs = mercado.on_faltante({})
        except tipos.SinDatos as e:
            print(f"  ⚠️ SinDatos: {e}")
            hs = []
        cuenta: dict[str, int] = {}
        for x in hs:
            cuenta[x.regla] = cuenta.get(x.regla, 0) + 1
        print(f"  hallazgos que produce AHORA: {len(hs)}")
        for regla, n in sorted(cuenta.items()):
            print(f"    · {regla}: {n}")
        fam = [x for x in hs if x.regla == "no_estan_en_curvas"]
        if fam:
            print(f"    → la fila de familia trae "
                  f"{fam[0].evidencia.get('cantidad')} ON(s) adentro")
    except Exception as e:
        print(f"  ⚠️ no pude correr el detector: {type(e).__name__}: {e}")

    _titulo("CÓMO LEERLO")
    print("  · Bloque 1 con un arranque VIEJO  → el deploy no reinició el daemon.")
    print("  · Bloque 3 sin corrida reciente   → no le tocó (ventana o ritmo).")
    print("  · Bloque 4 con la regla VIEJA y fecha de hoy → el daemon corre viejo.")
    print("  · Bloque 5 con la fila de familia → el REPO está bien; el problema")
    print("    es qué proceso está corriendo, no el código.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
