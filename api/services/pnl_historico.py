"""api/services/pnl_historico.py — Cuaderno de PnL diario de carga MANUAL.

Vista TRADING → PNL HISTÓRICO. El usuario tipea el PnL de cada día hábil y el
acumulado se calcula al leer:
  - acumulado_total  : suma corrida desde el 1-jul-2026, nunca se reinicia.
  - acumulado_mensual: se reinicia a 0 el primer día hábil de cada mes.

NO lo alimenta el motor de PnL — es un registro manual e independiente. `cuenta`
es una etiqueta libre ('General' por defecto); cada cuenta es su propio cuaderno
(clave fecha+cuenta en valuaciones.pnl_historico).

Los días hábiles salen de mercado.dias_habiles (calendario AR ya poblado). Se
listan desde INICIO hasta el fin del mes en curso, así el día de hoy siempre
aparece para cargar y se ven los días que faltan del mes.
"""
from __future__ import annotations

from datetime import date, timedelta

from psycopg.rows import dict_row

from core.postgres import get_pool

INICIO = date(2026, 7, 1)
CUENTA_DEFAULT = "General"


def _fin_mes(d: date) -> date:
    """Último día del mes de `d`."""
    primero_prox = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return primero_prox - timedelta(days=1)


def _norm_cuenta(cuenta: str | None) -> str:
    return (cuenta or "").strip() or CUENTA_DEFAULT


def listar(cuenta: str = CUENTA_DEFAULT) -> dict:
    """Días hábiles desde INICIO hasta fin del mes actual, con el monto cargado
    de la cuenta y los dos acumulados. Los días sin monto van en blanco (monto y
    acumulados = None) para que el gráfico corte en el último día cargado."""
    cuenta = _norm_cuenta(cuenta)
    hoy = date.today()
    fin = _fin_mes(hoy)

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT fecha FROM mercado.dias_habiles "
            "WHERE fecha >= %s AND fecha <= %s ORDER BY fecha",
            (INICIO, fin),
        )
        dias = [r["fecha"] for r in cur.fetchall()]

        cur.execute(
            "SELECT fecha, monto FROM valuaciones.pnl_historico WHERE cuenta = %s",
            (cuenta,),
        )
        montos = {r["fecha"]: float(r["monto"]) for r in cur.fetchall()}

        cur.execute("SELECT DISTINCT cuenta FROM valuaciones.pnl_historico ORDER BY cuenta")
        cuentas = [r["cuenta"] for r in cur.fetchall()]

    if CUENTA_DEFAULT not in cuentas:
        cuentas = [CUENTA_DEFAULT, *cuentas]

    filas = []
    acum_total = 0.0
    acum_mes = 0.0
    mes_prev: tuple[int, int] | None = None
    for d in dias:
        mk = (d.year, d.month)
        nuevo_mes = mk != mes_prev
        if nuevo_mes and mes_prev is not None:
            acum_mes = 0.0

        monto = montos.get(d)
        if monto is not None:
            acum_total += monto
            acum_mes += monto

        filas.append({
            "fecha": d.isoformat(),
            "mes": d.strftime("%Y-%m"),
            "monto": monto,
            "acumulado_total": round(acum_total, 4) if monto is not None else None,
            "acumulado_mensual": round(acum_mes, 4) if monto is not None else None,
            "es_hoy": d == hoy,
            "nuevo_mes": nuevo_mes,
        })
        mes_prev = mk

    return {
        "cuenta": cuenta,
        "inicio": INICIO.isoformat(),
        "cuentas": cuentas,
        "dias": filas,
    }


def guardar(fecha: str, monto: float | None = None, cuenta: str = CUENTA_DEFAULT) -> dict:
    """Upsert del PnL de un día. monto None o vacío → borra la fila (celda vacía)."""
    cuenta = _norm_cuenta(cuenta)
    f = date.fromisoformat(fecha)
    vacio = monto is None or monto == ""

    with get_pool().connection() as conn, conn.cursor() as cur:
        if vacio:
            cur.execute(
                "DELETE FROM valuaciones.pnl_historico WHERE fecha = %s AND cuenta = %s",
                (f, cuenta),
            )
        else:
            cur.execute(
                "INSERT INTO valuaciones.pnl_historico (fecha, cuenta, monto, actualizado) "
                "VALUES (%s, %s, %s, now()) "
                "ON CONFLICT (fecha, cuenta) DO UPDATE SET monto = EXCLUDED.monto, actualizado = now()",
                (f, cuenta, float(monto)),
            )
        conn.commit()

    return {"ok": True, "fecha": f.isoformat(), "cuenta": cuenta}
