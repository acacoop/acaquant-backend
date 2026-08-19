"""jobs/saldos_a_operadores.py — a cada operador, los saldos de SUS comitentes.

Doc madre: **`docs/AV_AGENT.md`** §0.ab.

Pedido del user (2026-08-19): *«que a las 16:45 todos los días hábiles envíe
mensajes vía la app a todos los users que tengan saldos positivos o negativos por
moneda en saldos de cuentas comitentes… que sepa los saldos de las comitentes y a
qué operador pertenece, que sepa qué es un operador y quiénes son»*.

Es el PRIMER consumidor de `av_agent_mensajes`, y por eso importa: prueba que la
capacidad sirve para algo que no se escribió pensando en ella. Cero IA — todo
sale de la base, que es de lo que el user avisó: *«tampoco termina de ser IA
esto»*.

CÓMO SE ARMA
============

    portafolio.control_saldos   el saldo LIQUIDADO de hoy por cuenta y moneda
              ↓ id_cuenta
    clientes.comitentes         qué operador atiende esa cuenta
              ↓ operador_email
    clientes.operadores         quién es y cuál es su mail

TRES DECISIONES QUE LO HACEN ÚTIL Y NO RUIDO
=============================================

1. **UN mensaje por operador**, no uno por cuenta. Un operador con 40 comitentes
   no necesita 40 pings: necesita su lista. Es la misma regla que ya rige
   `avisar.responsable` («200 pings no son 200 avisos, son un aviso ignorado»).

2. **Solo lo que no es cero.** Un saldo en cero es el estado normal y mandarlo
   convierte el mensaje en un reporte que nadie abre. Lo que se avisa es lo que
   pide una decisión: descubiertos y sobrantes.

3. **Las cuentas SIN operador no desaparecen.** No le llegan a nadie por
   definición, así que van al log y al stat del job. Un envío que solo cuenta lo
   que mandó esconde justo lo que quedó sin dueño.

⚠️ **El signo YA viene corregido** en `control_saldos` (Aunesa manda las tenencias
al revés y el daemon lo arregla): negativo = DESCUBIERTO real, no un falso
negativo por caución sin vencer. No re-invertir.
"""
from __future__ import annotations

import logging

from core.job_runs import JobRunLogger
from core.postgres import get_pool

logger = logging.getLogger(__name__)

TEMA = "saldos_comitentes"
# Por debajo de esto no se avisa: es polvo contable, no una decisión.
MINIMO = 1.0


def _saldos_de_hoy() -> list[dict]:
    """Los saldos != 0 de hoy, con su operador ya resuelto. UNA query.

    Las cuentas OCULTAS quedan afuera —el equipo las apagó a propósito— y las
    contrapartes también: no son clientes de un operador.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT lower(c.operador_email), s.id_cuenta, s.cuenta, s.ticker,
                   s.cantidad
            FROM portafolio.control_saldos s
            JOIN clientes.comitentes c ON c.id_cuenta = s.id_cuenta
            LEFT JOIN portafolio.control_saldos_ocultas o
                   ON o.id_cuenta = s.id_cuenta
            WHERE s.fecha = current_date
              AND abs(coalesce(s.cantidad, 0)) >= %s
              AND o.id_cuenta IS NULL
              AND c.operador_email IS NOT NULL
            ORDER BY lower(c.operador_email), s.id_cuenta, s.ticker
        """, (MINIMO,))
        return [{"operador": r[0], "id_cuenta": r[1], "cuenta": r[2],
                 "moneda": r[3], "saldo": float(r[4] or 0)}
                for r in cur.fetchall()]


def _sin_operador() -> int:
    """Cuántas cuentas con saldo quedan sin dueño. **No se esconden.**"""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT count(DISTINCT s.id_cuenta)
            FROM portafolio.control_saldos s
            LEFT JOIN clientes.comitentes c ON c.id_cuenta = s.id_cuenta
            WHERE s.fecha = current_date
              AND abs(coalesce(s.cantidad, 0)) >= %s
              AND coalesce(c.operador_email, '') = ''
        """, (MINIMO,))
        return int((cur.fetchone() or [0])[0] or 0)


def _cuerpo(filas: list[dict]) -> tuple[str, str]:
    """(asunto, detalle) para un operador. El asunto tiene que decidirse de un
    vistazo desde la campanita; el detalle es la lista."""
    negativos = [f for f in filas if f["saldo"] < 0]
    cuentas = len({f["id_cuenta"] for f in filas})
    asunto = (f"{len(negativos)} cuenta(s) TUYAS en descubierto"
              if negativos else
              f"{cuentas} cuenta(s) tuyas con saldo por acomodar")
    # Los descubiertos primero: es lo que hay que atender hoy.
    orden = sorted(filas, key=lambda f: (f["saldo"] >= 0, f["id_cuenta"],
                                         f["moneda"]))
    lineas = [f"{'⚠ ' if f['saldo'] < 0 else ''}{f['cuenta'] or f['id_cuenta']} · "
              f"{f['moneda']} {f['saldo']:,.2f}" for f in orden[:40]]
    if len(orden) > 40:
        lineas.append(f"… y {len(orden) - 40} más (ver SALDOS)")
    return asunto, "\n".join(lineas)


def main() -> int:
    with JobRunLogger("jobs.saldos_a_operadores", tipo="mensajes") as jr:
        from api.services import av_agent_mensajes as msg

        filas = _saldos_de_hoy()
        huerfanas = _sin_operador()
        if not filas:
            print("no hay saldos distintos de cero hoy — no se manda nada")
            jr.set_stat("enviados", 0)
            jr.set_stat("sin_operador", huerfanas)
            return 0

        por_operador: dict[str, list[dict]] = {}
        for f in filas:
            por_operador.setdefault(f["operador"], []).append(f)

        # El tema lleva LA FECHA: el mensaje de hoy es uno nuevo aunque el de
        # ayer siga abierto. Sin eso, un operador que no cierra el suyo dejaría
        # de recibir — y el que más los acumula es justo el que más los necesita.
        from datetime import date
        tema = f"{TEMA}:{date.today().isoformat()}"
        mensajes = [{"para": email, "tema": tema, "donde": "SALDOS DE CUENTAS",
                     **dict(zip(("asunto", "detalle"), _cuerpo(suyas), strict=True))}
                    for email, suyas in por_operador.items()]

        r = msg.enviar_muchos(mensajes, por="jobs.saldos_a_operadores")
        if not r.get("ok"):
            print(f"✖ {r.get('error')}")
            jr.set_stat("error", r.get("error"))
            return 1

        jr.set_stat("enviados", r["enviados"])
        jr.set_stat("ya_estaban", r["ya_estaban"])
        jr.set_stat("operadores", len(por_operador))
        jr.set_stat("sin_operador", huerfanas)
        print(f"✔ {r['enviados']} operador(es) avisado(s) sobre "
              f"{len({f['id_cuenta'] for f in filas})} cuenta(s)"
              + (f" · {r['ya_estaban']} ya lo tenían" if r["ya_estaban"] else ""))
        if r["fallaron"]:
            # **Los que NO llegaron se cantan.** Un envío que solo dice cuántos
            # mandó esconde justo los que hay que mirar.
            print(f"\n✖ {len(r['fallaron'])} no se pudieron mandar:")
            for f in r["fallaron"]:
                print(f"   · {f['para']}: {f['error']}")
            jr.set_stat("fallaron", len(r["fallaron"]))
        if huerfanas:
            print(f"\n⚠ {huerfanas} cuenta(s) con saldo NO tienen operador "
                  f"asignado: a ésas no le llegan a nadie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
