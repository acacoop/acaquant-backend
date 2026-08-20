"""Diag read-only: ¿Aunesa está caído o el problema es NUESTRO?

Nace del incidente que se ve en la vista como **"AUNESA CAÍDO"** en Back Office →
Tesorería, con el mensaje `HTTPError: 500 Server Error for url:
https://aca.aunesa.com/Irmo/api/login`. Ese 500 lo devuelve el servidor del
CUSTODIO, no nuestra API — pero desde la pantalla las dos cosas se ven igual, y
sin medirlo no se puede afirmar de quién es. Esto lo mide.

Cuatro pasos, de menos a más:
  1. CREDENCIALES — ¿están las tres `AUNESA_*` en el `.env`? (no imprime la clave).
  2. ALCANCE      — ¿el host contesta algo? Un POST con body vacío, SIN credenciales:
                    un 4xx significa "vivo y rechazando" (que es lo sano); un 5xx o un
                    timeout, que su servidor está roto de entrada.
  3. LOGIN        — N intentos reales con las credenciales, mostrando status, tiempo y
                    las primeras líneas de la respuesta. Es lo que decide el veredicto.
  4. PUNTA A PUNTA— si hubo token, pide los movimientos del día (el MISMO endpoint que
                    usa la vista) y cuenta las filas. Un login OK con esto en error es
                    otra cosa distinta y hay que verlo aparte.

NO escribe nada, NO imprime la contraseña ni el token, y usa `LOGIN_TIMEOUT_S`
por intento con una pausa entre medio: es una API de terceros y machacarle el
login es la mejor forma de que nos bloqueen la cuenta.

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_aunesa                    # 3 intentos + prueba punta a punta
    python -m scripts.diag_aunesa --intentos 5       # si sospechás que es intermitente
    python -m scripts.diag_aunesa --sin-punta-a-punta
    python -m scripts.diag_aunesa --fecha 2026-08-19
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime

import requests

import config
from api.services.tesoreria import _ENDPOINT, TODOS_ESTADOS, _fechas, _hoy_art
from core import aunesa

# Headers que dicen algo cuando el error es de infraestructura (quién contestó
# realmente: su app, un balanceador, un WAF).
_HEADERS_UTILES = ("server", "content-type", "cf-ray", "x-powered-by", "via", "retry-after")


def _tapado(v: str | None, *, ver: int = 0) -> str:
    """Valor de config para imprimir. `ver` = cuántos caracteres del principio se
    muestran; el resto es largo. La contraseña va SIEMPRE con ver=0."""
    if not v:
        return "(VACÍA)"
    cabeza = v[:ver] if ver else ""
    return f"{cabeza}{'•' * min(len(v) - len(cabeza), 12)} ({len(v)} chars)"


def _cuerpo(texto: str, n: int = 400) -> str:
    t = " ".join((texto or "").split())
    return (t[:n] + "…") if len(t) > n else (t or "(vacío)")


def _mostrar_headers(resp: requests.Response) -> None:
    for h in _HEADERS_UTILES:
        if (v := resp.headers.get(h)):
            print(f"      {h}: {v}")


# ⚠️ **`AUNESA_CLIENT_ID` VA VACÍO Y SIEMPRE FUE ASÍ** (user, 2026-08-20). Este
# diag lo listaba como FALTANTE y con eso cantaba «es NUESTRO» — señalando como
# causa la configuración normal del sistema. Solo `username` y `password` son
# obligatorias; que un campo se llame `clientId` no significa que el proveedor lo
# pida, y meses de logins exitosos con el campo vacío son la medición que manda.
OBLIGATORIAS = ("AUNESA_USERNAME", "AUNESA_PASSWORD")


def paso_credenciales() -> bool:
    print("\n1) CREDENCIALES en el .env")
    faltan = []
    for nombre, valor, ver in (("AUNESA_CLIENT_ID", config.AUNESA_CLIENT_ID, 4),
                               ("AUNESA_USERNAME", config.AUNESA_USERNAME, 4),
                               ("AUNESA_PASSWORD", config.AUNESA_PASSWORD, 0)):
        opcional = nombre not in OBLIGATORIAS
        marca = "  (opcional — va vacía siempre)" if opcional and not valor else ""
        print(f"   {nombre:18} {_tapado(valor, ver=ver)}{marca}")
        if not valor and not opcional:
            faltan.append(nombre)
    if faltan:
        print(f"   ❌ FALTAN: {', '.join(faltan)}")
        return False
    print("   ✔ están cargadas (que sean las CORRECTAS lo dice el paso 3)")
    return True


def paso_alcance() -> int | None:
    """POST sin credenciales: separa "el host está vivo" de "su servidor está roto".

    Devuelve el status (o None si ni contesta) **porque el veredicto lo necesita**:
    ver abajo por qué esta evidencia le gana a cualquier revisión de config.
    """
    print("\n2) ¿EL HOST CONTESTA? (POST con body vacío, sin credenciales)")
    t0 = time.monotonic()
    try:
        resp = requests.post(aunesa.AUTH_URL, json={},
                             headers={"Content-Type": "application/json"},
                             timeout=aunesa.LOGIN_TIMEOUT_S)
    except requests.exceptions.RequestException as e:
        print(f"   ❌ ni siquiera contesta: {type(e).__name__}: {e}")
        print("      (DNS, red del Droplet o el servicio entero abajo)")
        return None
    ms = (time.monotonic() - t0) * 1000
    print(f"   HTTP {resp.status_code} en {ms:.0f} ms")
    _mostrar_headers(resp)
    print(f"      cuerpo: {_cuerpo(resp.text, 200)}")
    if resp.status_code < 500:
        print("   ✔ el servicio está VIVO y rechaza un login vacío, que es lo correcto.")
    else:
        print("   ❌ 5xx con el body VACÍO: se rompe antes de mirar las credenciales →")
        print("      es su servidor. Nada que tocar de este lado.")
    return resp.status_code


def paso_barrido() -> None:
    """LAS CINCO APIS que el sistema usa de verdad, y el análisis del conjunto.

    Pedido del user (2026-08-20): *«que intente conectarse a todas las APIs que
    usamos, a ver si todas dan el mismo error, ya que esto impacta en muchos
    lados, y darme un análisis general»*. Una sola prueba no contesta eso, y la
    diferencia importa: con las cinco caídas no hay nada que hacer de este lado;
    con una sola, el resto de los datos sigue entrando.
    """
    from core.proveedores import barrer_aunesa

    print("\n2b) LAS 5 APIS QUE USAMOS")
    r = barrer_aunesa()
    for f in r.get("endpoints") or []:
        marca = "✔" if f["ok"] else "❌"
        estado = f["status"] if f["status"] is not None else "sin respuesta"
        print(f"   {marca} {estado!s:>13}  {f['ms']:>5} ms  {f['path']}")
        print(f"                        ({f['para_que']})")
        if f["detalle"]:
            print(f"                        {f['detalle'][:120]}")
    if not r.get("endpoints"):
        print("   (no se probaron: ver el análisis)")
    print(f"\n   ANÁLISIS: {r.get('analisis', '')}")


def paso_login(intentos: int, pausa: float) -> tuple[str | None, list[str]]:
    """N logins REALES. Devuelve (token o None, resumen por intento)."""
    print(f"\n3) LOGIN REAL — {intentos} intento(s) contra {aunesa.AUTH_URL}")
    token, resultados = None, []
    for i in range(1, intentos + 1):
        t0 = time.monotonic()
        try:
            resp = requests.post(
                aunesa.AUTH_URL,
                json={"clientId": config.AUNESA_CLIENT_ID,
                      "username": config.AUNESA_USERNAME,
                      "password": config.AUNESA_PASSWORD},
                headers={"Content-Type": "application/json"},
                timeout=aunesa.LOGIN_TIMEOUT_S,
            )
        except requests.exceptions.RequestException as e:
            ms = (time.monotonic() - t0) * 1000
            print(f"   intento {i}/{intentos}: ✗ {type(e).__name__} tras {ms:.0f} ms")
            resultados.append(f"red:{type(e).__name__}")
            if i < intentos:
                time.sleep(pausa)
            continue
        ms = (time.monotonic() - t0) * 1000
        tok = None
        if resp.status_code < 400:
            try:
                tok = (resp.json() or {}).get("token")
            except ValueError:
                tok = None
        marca = "✔" if tok else "✗"
        print(f"   intento {i}/{intentos}: {marca} HTTP {resp.status_code} en {ms:.0f} ms"
              + ("  → token OK" if tok else ""))
        if not tok:
            _mostrar_headers(resp)
            print(f"      cuerpo: {_cuerpo(resp.text)}")
        resultados.append(f"{resp.status_code}{'+token' if tok else ''}")
        token = token or tok
        if i < intentos:
            time.sleep(pausa)
    return token, resultados


def paso_punta_a_punta(token: str, dia: date) -> None:
    """El MISMO endpoint que la vista, con el token recién sacado."""
    print(f"\n4) PUNTA A PUNTA — movimientos del {dia.isoformat()} ({_ENDPOINT})")
    desde, hasta, _ = _fechas(dia.isoformat())
    t0 = time.monotonic()
    try:
        resp = requests.get(
            f"{aunesa.BASE_URL}/{_ENDPOINT}",
            params={"liquidacionDesde": desde, "liquidacionHasta": hasta,
                    "estados": TODOS_ESTADOS},
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            timeout=90,
        )
    except requests.exceptions.RequestException as e:
        print(f"   ❌ {type(e).__name__}: {e}")
        return
    seg = time.monotonic() - t0
    print(f"   HTTP {resp.status_code} en {seg:.1f} s")
    if resp.status_code == 204:
        print("   ✔ 204 = el día no tiene movimientos (no es un error)")
        return
    if resp.status_code != 200:
        print(f"      cuerpo: {_cuerpo(resp.text)}")
        print("   ❌ el LOGIN anda pero el endpoint de la vista no → es otra cosa,")
        print("      no la caída del login. Mirar el cuerpo de arriba.")
        return
    try:
        filas = resp.json()
    except ValueError:
        print(f"   ❌ 200 pero la respuesta no es JSON: {_cuerpo(resp.text, 200)}")
        return
    n = len(filas) if isinstance(filas, list) else 0
    del_dia = sum(1 for r in filas if str(r.get("fecha") or "").strip() == desde) \
        if isinstance(filas, list) else 0
    print(f"   ✔ {n} filas ({del_dia} del día pedido — la vista descarta el resto)")


def veredicto(cred_ok: bool, token: str | None, resultados: list[str],
              status_sin_credenciales: int | None = None) -> None:
    print("\n" + "=" * 70)
    # ⚠️⚠️ **LA EVIDENCIA LE GANA AL CHECKLIST, y esto ya se equivocó una vez.**
    #
    # En la corrida del 2026-08-20 el paso 2 dijo *«5xx con el body VACÍO: es su
    # servidor, nada que tocar de este lado»* y tres líneas después el veredicto
    # dijo *«es NUESTRO»*. **El mismo informe afirmando las dos cosas.** Y no es
    # un detalle de redacción: mandó a buscar el problema al lugar equivocado,
    # señalando como causa una configuración que siempre fue así.
    #
    # La regla es de lógica, no de estilo: si el host devuelve 5xx a una request
    # SIN credenciales, se rompió ANTES de leerlas. Ninguna revisión de nuestro
    # `.env` puede explicar eso, así que la evidencia manda sobre el checklist.
    # Un chequeo de config solo puede ser la causa si el host contesta 4xx.
    if status_sin_credenciales and status_sin_credenciales >= 500:
        print("VEREDICTO: es de ELLOS — su servidor devuelve 5xx a una request")
        print("  SIN credenciales, o sea que se rompe ANTES de mirarlas.")
        if not cred_ok:
            print("  (Falta algo en el .env, y hay que arreglarlo igual — pero NO")
            print("   es la causa de esto: el 5xx pasa sin mandar credencial alguna.)")
    elif not cred_ok:
        print("VEREDICTO: es NUESTRO — faltan credenciales en el .env del Droplet.")
    elif token and all("+token" in r for r in resultados):
        print("VEREDICTO: Aunesa responde BIEN en todos los intentos.")
        print("  Si la vista sigue diciendo AUNESA CAÍDO, el proceso de la API todavía")
        print("  tiene abierto el cortacircuito (dura 45s) o guarda el error de antes:")
        print("  esperá un refresh, y si persiste reiniciá api.service.")
    elif token:
        print("VEREDICTO: INTERMITENTE — algunos intentos entran y otros no.")
        print(f"  Intentos: {', '.join(resultados)}")
        print("  Los reintentos de `core/aunesa.py` están hechos justo para esto: la")
        print("  vista se recupera sola en cuanto uno de los intentos pasa.")
    elif any(r.startswith(("400", "401", "403")) for r in resultados):
        print("VEREDICTO: es NUESTRO — Aunesa contesta y RECHAZA las credenciales.")
        print(f"  Intentos: {', '.join(resultados)}")
        print("  Credencial vencida o cambiada: hay que pedirle al custodio la nueva")
        print("  y actualizar el .env. NO reintentar en loop (bloquean la cuenta).")
    else:
        print("VEREDICTO: es de ELLOS — el login del custodio está caído.")
        print(f"  Intentos: {', '.join(resultados)}")
        print("  No hay nada que arreglar de este lado. La Tesorería sigue mostrando")
        print("  TODO lo cargado a mano (saldos, cheques, mercados, banco a banco,")
        print("  registros, VEPs); lo único que falta son los movimientos del día, y")
        print("  vuelven solos cuando Aunesa se recupera. Avisarle al custodio.")
    print("=" * 70)


def main() -> None:
    ap = argparse.ArgumentParser(description="¿Aunesa está caído o el problema es nuestro?")
    ap.add_argument("--intentos", type=int, default=3, help="logins reales a probar (default 3)")
    ap.add_argument("--pausa", type=float, default=2.0, help="segundos entre intentos")
    ap.add_argument("--fecha", help="ISO YYYY-MM-DD para la prueba punta a punta (default hoy)")
    ap.add_argument("--sin-punta-a-punta", action="store_true",
                    help="no pedir los movimientos del día")
    args = ap.parse_args()

    print("=" * 70)
    print(f"DIAG AUNESA — {datetime.now().isoformat(timespec='seconds')}")
    print(f"BASE_URL: {aunesa.BASE_URL}")
    print("=" * 70)

    cred_ok = paso_credenciales()
    status_sc = paso_alcance()
    paso_barrido()
    token, resultados = (None, []) if not cred_ok else paso_login(args.intentos, args.pausa)
    if token and not args.sin_punta_a_punta:
        dia = (datetime.strptime(args.fecha, "%Y-%m-%d").date() if args.fecha
               else _hoy_art().date())
        paso_punta_a_punta(token, dia)
    veredicto(cred_ok, token, resultados, status_sc)


if __name__ == "__main__":
    main()
