"""Pega a mano un token de 1816 sacado de la sesión WEB.

**Por qué existe** (2026-08-17). El plan da **50 tokens por día**, y cuando ese
cupo se agota el endpoint de auth contesta 429 a todo: el cliente no puede
conseguir uno **y no hay forma de esperar a que se libere** — se recupera al día
siguiente. Pero un token dura 24 h y sirva de donde salga, así que uno obtenido
desde la web deja al sistema entero operativo sin gastar un login.

Es una VÍA DE ESCAPE, no la operación normal: lo normal es que el token se pida
una vez y lo compartan todos los procesos (`manager.tokens_externos`).

Tres cosas que hace y que un INSERT a mano no haría:

1. **Valida el token contra la API antes de guardarlo.** Persistir uno que no
   sirve es peor que no tener ninguno: todo sigue fallando, pero ahora con un
   error que no habla del token.
2. **Lee la expiración REAL del JWT** (el claim `exp`) en vez de suponer 24 h. Si
   el que pegaste vence en dos horas, el sistema tiene que saberlo — si no, va a
   seguir usándolo y fallando con 401 cuando venza.
3. **No suma al contador de logins**: este token no nos costó uno de los 50.

⚠️ El token es una CREDENCIAL. El script no lo imprime entero (solo un prefijo), y
pasarlo como argumento lo deja en el historial del shell: si te importa, usá
`--stdin` y pegalo cuando lo pida.

    python -m scripts.set_token_1816 --token eyJhbGciOi...
    python -m scripts.set_token_1816 --stdin
    python -m scripts.set_token_1816 --token <TOK> --horas 6   # si no trae `exp`
"""

from __future__ import annotations

import argparse
import base64
import json
import time


def _exp_del_jwt(tok: str) -> tuple[float, str]:
    """La expiración que declara el propio token. **No se valida la firma** — no
    es nuestra y no la necesitamos: acá el JWT se lee como lo que es, un sobre con
    la fecha escrita afuera. Si no se puede leer, se devuelve 0 y decide el
    `--horas`."""
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)          # padding base64url
        datos = json.loads(base64.urlsafe_b64decode(payload))
        exp = float(datos.get("exp") or 0)
        if exp > time.time():
            return exp, f"del propio token (claim `exp`): {datos.get('exp')}"
        if exp:
            return 0.0, f"⚠ el token dice que YA VENCIÓ (exp={datos.get('exp')})"
    except Exception as e:
        return 0.0, f"no se pudo leer el `exp` del JWT ({type(e).__name__})"
    return 0.0, "el token no trae `exp`"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--token", default="", help="el JWT copiado de la sesión web")
    ap.add_argument("--stdin", action="store_true",
                    help="leerlo por entrada estándar (no queda en el historial)")
    ap.add_argument("--horas", type=float, default=24.0,
                    help="vigencia a asumir si el token no declara `exp` (default 24)")
    ap.add_argument("--sin-validar", action="store_true",
                    help="guardarlo sin probarlo contra la API (NO recomendado)")
    args = ap.parse_args()

    tok = (args.token or "").strip()
    if args.stdin or not tok:
        import getpass
        tok = getpass.getpass("Pegá el token de 1816 (no se va a mostrar): ").strip()
    if not tok:
        print("No se recibió ningún token.")
        return 1

    exp, nota = _exp_del_jwt(tok)
    if not exp:
        exp = time.time() + args.horas * 3600
        nota += f" → se asume {args.horas:g}h"
    print(f"\ntoken  {tok[:12]}…{tok[-6:]}  ({len(tok)} chars)")
    print(f"vence  {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}   [{nota}]")

    from core import mercado_1816 as m

    if not args.sin_validar:
        # La prueba REAL: una llamada autenticada con ESTE token. Se usa `/curvas`
        # porque es la más barata del proveedor (1 crédito, y de esos sobran).
        print("\nvalidando contra la API…")
        try:
            import requests
            r = requests.get(f"{m._BASE}/v1/mercado/curvas",
                             headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        except Exception as e:
            print(f"  ✖ no se pudo llegar a 1816: {e}")
            return 1
        if r.status_code != 200:
            print(f"  ✖ el token NO sirve — HTTP {r.status_code}: {r.text[:160]}")
            print("    (401/403 = token inválido o vencido · 429 = rate limit, "
                  "esperá unos segundos y probá de nuevo)")
            return 1
        n = len(r.json() or [])
        print(f"  ✔ anda: la API contestó {n} curvas con este token")

    m.guardar_token_manual(tok, exp)
    est = m.estado_token()
    if not est["token_vigente"]:
        print("\n  ✖ se guardó pero la fila no quedó vigente — ¿existe la tabla "
              "`manager.tokens_externos`? Corré `python -m scripts.apply_schema`.")
        return 1
    print(f"\n✔ guardado en manager.tokens_externos. Lo van a usar TODOS los "
          f"procesos\n  (api + jobs) durante {est['expira_en_s'] // 3600}h "
          f"{est['expira_en_s'] % 3600 // 60}m, sin gastar logins.\n"
          "  No hace falta reiniciar nada: el próximo request lo adopta.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
