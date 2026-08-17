"""¿Funciona una consulta a 1816 con el token pegado a mano, SIN loguearse?

El plan da **50 tokens por día** y ese cupo se agotó, así que el endpoint de auth
contesta 429 a todo. Pero un token dura 24 h y sirve venga de donde venga: con uno
sacado de la sesión web, pegado en `manager.tokens_externos`, el sistema entero
tiene que poder operar **sin pedir ni un login más**.

Este script lo PRUEBA, y la prueba tiene dos mitades — sin la segunda no probaría
nada, porque una consulta que anda podría haber andado gracias a un login nuevo:

  1. **la consulta trae datos** (se piden las curvas, la llamada más barata);
  2. **el contador de logins NO se movió** y el token de la base es EL MISMO de
     antes → no hubo autenticación de por medio.

    python -m scripts.probar_1816_sin_login
    python -m scripts.probar_1816_sin_login --ticker AL30   # además, un indicador
"""

from __future__ import annotations

import argparse
import time


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", default="", help="probar además un dato de este bono")
    args = ap.parse_args()

    from core import mercado_1816 as m

    print("\n1816 — PRUEBA SIN LOGIN")
    print("=" * 64)

    fila = m._fila_token()
    if not fila.get("token"):
        print("  ✖ No hay token en manager.tokens_externos. Pegá uno primero.")
        return 1

    tok = fila["token"]
    exp = fila.get("exp") or 0
    print(f"  token en la base   {tok[:12]}…{tok[-6:]}  ({len(tok)} chars)")
    if not exp:
        print("  ✖ el token no declara `exp` y la columna `expira_at` está vacía:")
        print("    el cliente no puede saber si sigue vigente → no lo va a usar.")
        print("    Cargá `expira_at` a mano, o pegalo con "
              "`python -m scripts.set_token_1816`.")
        return 1
    restante = exp - time.time()
    origen = "del propio JWT" if fila.get("exp_del_jwt") else "de la columna expira_at"
    print(f"  vence              {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}"
          f"  ({origen})")
    if restante <= 0:
        print(f"  ✖ YA VENCIÓ hace {int(-restante) // 60} min — hay que pegar otro.")
        return 1
    print(f"  le quedan          {int(restante) // 3600}h {int(restante) % 3600 // 60}m")

    antes = m.estado_token()["logins_hoy"]

    print("\n  consultando /curvas (1 crédito, sin tocar el endpoint de auth)…")
    try:
        curvas = m.curvas() or []
    except Exception as e:
        print(f"  ✖ falló: {e}")
        print("\n    Si el error habla de AUTH, el token de la base no se está "
              "usando.\n    Si habla de rate limit, esperá unos segundos: es el "
              "límite de 1 petición/segundo.")
        return 1

    print(f"  ✔ contestó: {len(curvas)} curvas")
    if curvas:
        nombres = [c.get("name") or c.get("nombre") or "?" for c in curvas[:4]]
        print(f"    ({' · '.join(nombres)}…)")

    if args.ticker:
        # `indicadores_vigentes` (el precio lo pone 1816) y no `indicadores_de`,
        # que es el de input MANUAL y exige que le pasemos un precio de referencia.
        print(f"\n  consultando indicadores de {args.ticker}…")
        try:
            r = m.indicadores_vigentes([args.ticker], ["tea", "paridad"]) or {}
            v = (r.get("instrumentos") or {}).get(args.ticker.upper()) or {}
            print(f"  ✔ {args.ticker}: tea={v.get('tea')}  paridad={v.get('paridad')}"
                  f"  (rueda {r.get('fechaOperacion')})"
                  if v else f"  ⚠ 1816 no devolvió datos para {args.ticker}")
        except Exception as e:
            print(f"  ✖ falló: {e}")

    # ── LA SEGUNDA MITAD: probar que NO hubo login ──────────────────────────
    despues = m.estado_token()["logins_hoy"]
    mismo = (m._fila_token().get("token") == tok)
    print("\n" + "=" * 64)
    print(f"  logins registrados   antes {antes}  ·  después {despues}")
    print(f"  el token es el mismo {'sí' if mismo else 'NO — se pidió uno nuevo'}")
    if despues == antes and mismo:
        print("\n  ✔✔ CONFIRMADO: la consulta salió con el token pegado a mano y "
              "NO se\n      gastó ningún login. Todo el sistema puede operar así "
              "hasta que venza.\n")
        return 0
    print("\n  ⚠ hubo una autenticación de por medio: el token de la base no "
          "alcanzó.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
