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

PROBARLO SIN MOLESTAR A NADIE
==============================

    python -m jobs.saldos_a_operadores --a vos@acavalores.com.ar

Manda TODO a una sola persona, con `[PRUEBA]` en el asunto. Sin esto, probar un
aviso masivo significa escribirle a la mesa entera para ver si el modal se ve
bien.

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


def _armar(filas: list[dict]) -> tuple[str, list[dict]]:
    """(asunto, filas de la tabla) para un operador.

    El asunto se lee de un vistazo; la tabla es lo que se completa. Los
    descubiertos van PRIMERO: es lo que hay que atender hoy, y ordenar por cuenta
    los mezclaría con los sobrantes.
    """
    negativos = [f for f in filas if f["saldo"] < 0]
    cuentas = len({f["id_cuenta"] for f in filas})
    asunto = (f"{len(negativos)} cuenta(s) tuyas EN DESCUBIERTO"
              if negativos else
              f"{cuentas} cuenta(s) tuyas con saldo para revisar")
    orden = sorted(filas, key=lambda f: (f["saldo"] >= 0, -abs(f["saldo"])))
    tabla = [{
        # La CLAVE identifica la fila entre corridas: si el job vuelve a correr,
        # las que el operador ya tildó NO se pierden.
        "clave": f"{f['id_cuenta']}:{f['moneda']}",
        "etiqueta": f["cuenta"] or f["id_cuenta"],
        "datos": {"cuenta": f["cuenta"] or f["id_cuenta"],
                  "id_cuenta": f["id_cuenta"], "moneda": f["moneda"],
                  "saldo": round(f["saldo"], 2),
                  "signo": "negativo" if f["saldo"] < 0 else "positivo"},
    } for f in orden]
    return asunto, tabla


def main() -> int:
    # ── MODO PRUEBA ─────────────────────────────────────────────────────────
    # `--a vos@…` manda TODO a una sola persona en vez de a cada operador. Es
    # para poder ver el modal con datos reales sin escribirle a nadie más — y
    # sin eso, probar un aviso masivo significa molestar a la mesa.
    import sys
    prueba = ""
    if "--a" in sys.argv:
        i = sys.argv.index("--a")
        prueba = sys.argv[i + 1].strip().lower() if i + 1 < len(sys.argv) else ""
        if "@" not in prueba:
            print("--a necesita un email")
            return 1

    with JobRunLogger("saldos_a_operadores") as jr:
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
        if prueba:
            # Todo junto a una sola persona, y se DICE en el asunto para que
            # nadie confunda una prueba con el aviso real.
            por_operador = {prueba: filas}
            print(f"MODO PRUEBA — todo va a {prueba}, nadie más recibe nada\n")

        # El tema lleva LA FECHA: el mensaje de hoy es uno nuevo aunque el de
        # ayer siga abierto. Sin eso, un operador que no cierra el suyo dejaría
        # de recibir — y el que más los acumula es justo el que más los necesita.
        from datetime import date
        tema = f"{TEMA}:{date.today().isoformat()}"

        enviados = fallaron = 0
        for email, suyas in por_operador.items():
            asunto, tabla = _armar(suyas)
            # **Va como TABLA y con `interrumpe`**: el user lo pidió explícito —
            # «tiene que ser como el modal de briefing, aparece en la pantalla y
            # te hace hacer algo para continuar, no que aparezca en el cuerpo del
            # agente como si nada».
            #
            # Y VENCE al cierre de la jornada: un aviso de saldos vale HOY;
            # mañana el mercado abre con otros números y pedir acción sobre la
            # foto de ayer es peor que no avisar.
            r = msg.enviar_tabla(
                para=email, tema=f"{tema}:prueba" if prueba else tema,
                asunto=f"[PRUEBA] {asunto}" if prueba else asunto, filas=tabla,
                detalle="Marcá cada una a medida que la resolvés. Vale por hoy.",
                donde="SALDOS DE CUENTAS", por="jobs.saldos_a_operadores",
                interrumpe=True)
            if r.get("ok"):
                enviados += 1
            else:
                fallaron += 1
                print(f"   ✖ {email}: {r.get('error')}")

        jr.set_stat("enviados", enviados)
        jr.set_stat("operadores", len(por_operador))
        jr.set_stat("sin_operador", huerfanas)
        if fallaron:
            jr.set_stat("fallaron", fallaron)
        print(f"✔ {enviados} operador(es) avisado(s) sobre "
              f"{len({f['id_cuenta'] for f in filas})} cuenta(s)")
        if huerfanas:
            print(f"\n⚠ {huerfanas} cuenta(s) con saldo NO tienen operador "
                  f"asignado: a ésas no le llegan a nadie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
