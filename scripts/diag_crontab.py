"""¿EL CRON DEL REPO ES EL QUE CORRE? — y cuándo corrió cada job.

    python -m scripts.diag_crontab
    python -m scripts.diag_crontab saldos_a_operadores    # uno solo, con su historial

Contesta dos preguntas que hasta hoy no se podían contestar sin entrar a mirar a
mano, y que son distintas:

  1. **¿está instalado?** — `deploy/crontab.txt` es la fuente de verdad para todo
     el sistema, pero `deploy.sh` NO lo instala: hace `git pull`, `apply_schema`
     y reinicia la API. Un cron nuevo puede vivir en el repo y no correr nunca.
  2. **¿corrió?** — `manager.job_runs` guarda cada corrida. Estar instalado y
     haber corrido tampoco son lo mismo (puede fallar el `run_job.sh`, el lock,
     el venv).

Solo LEE. No instala nada.
"""
from __future__ import annotations

import sys


def _runs(label: str, n: int = 10) -> list[tuple]:
    """⚠️ Las columnas son `tipo`/`status`/`started_at` + un `data` jsonb — NO hay
    `job`, `duration_ms` ni `error` sueltos (verificado contra `sql/schema.sql`;
    la primera versión de este diag se las inventó)."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT started_at, status, finished_at, data "
            "  FROM manager.job_runs WHERE tipo = %s "
            " ORDER BY started_at DESC LIMIT %s", (label, n))
        return cur.fetchall()


def _falla(data) -> str:
    """El primer error de la corrida, si lo hubo. Vive adentro del `data`."""
    if not isinstance(data, dict):
        return ""
    errs = data.get("errors") or data.get("error") or []
    if isinstance(errs, str):
        return errs
    return str(errs[0]) if errs else ""


def main() -> int:
    from api.services import av_agent_crontab as cron

    uno = sys.argv[1] if len(sys.argv) > 1 else ""

    print("═" * 74)
    print("  EL CRONTAB DEL REPO vs EL DE LA MÁQUINA")
    print("═" * 74)
    r = cron.comparar()
    if not r["ok"]:
        # **No se afirma nada**: no poder leer no es «está vacío».
        print(f"\n  ⚠ {r['motivo']} — NO puedo decir si están instalados.")
        print(f"    (en el repo hay {r['en_repo']} líneas de cron)")
    else:
        print(f"\n  en el repo:    {r['en_repo']}")
        print(f"  en la máquina: {r['en_maquina']}")
        if not r["sin_instalar"] and not r["sin_declarar"]:
            print("\n  ✔ coinciden: todo lo del repo está instalado")
        for x in r["sin_instalar"]:
            print(f"\n  ✖ NO INSTALADO (no corre): {cron._que_job(x)}")
            print(f"      {x[:110]}")
        for x in r["sin_declarar"]:
            print(f"\n  ⚠ corre y el repo no lo declara: {cron._que_job(x)}")
            print(f"      {x[:110]}")
        if r["sin_instalar"]:
            print("\n  → se instalan con:  crontab /root/TradingAV/deploy/crontab.txt")

    labels = [cron._que_job(x) for x in sorted(cron.del_repo())]
    labels = sorted({x for x in labels if x and "." not in x and "/" not in x})
    if uno:
        labels = [x for x in labels if uno in x] or [uno]

    print("\n" + "═" * 74)
    print("  ¿CORRIÓ? (manager.job_runs)")
    print("═" * 74)
    for lab in labels:
        try:
            filas = _runs(lab, 5 if uno else 1)
        except Exception as e:
            print(f"  {lab:<28} no pude leer job_runs ({type(e).__name__})")
            continue
        if not filas:
            print(f"  {lab:<28} ✖ NUNCA corrió")
            continue
        ts, st, fin, data = filas[0]
        marca = "✔" if st == "ok" else "✖"
        seg = f" · {(fin - ts).total_seconds():.0f}s" if fin and ts else ""
        err = _falla(data)
        print(f"  {lab:<28} {marca} {ts:%d/%m %H:%M} {st}{seg}"
              + (f" · {err[:60]}" if err else ""))
        for ts, st, _fin, data in filas[1:]:
            e = _falla(data)
            print(f"  {'':<28}   {ts:%d/%m %H:%M} {st}"
                  + (f" · {e[:50]}" if e else ""))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
