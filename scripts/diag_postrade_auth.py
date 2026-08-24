"""scripts/diag_postrade_auth.py — ¿podemos conectarnos a Postrade y sacar token?

READ-ONLY. No escribe en la base ni en Postrade: pide tokens y hace un GET de
consulta. No toca ningún método que registre, modifique ni cancele nada.

ETAPA 1 de la integración con la API Postrade de A3 Mercados / Argentina
Clearing (`anywhereportfolio.com.ar`). Contesta tres preguntas, en orden, y
cada una es un reclamo distinto si falla:

  1. ¿Las credenciales sirven?  → pide el token por BODY y por QUERY (el manual
     dice que las dos formas son válidas; si una anda y la otra no, lo sabemos
     acá y no dentro de un job).
  2. ¿Cómo se manda el token?   → el manual dice "incluir el header
     Authorization el token obtenido" y lo ilustra con una captura, así que si
     va crudo o con `Bearer ` NO está escrito en ninguna parte. Se MIDE:
     prueba las dos contra un endpoint real (REGLA #2).
  3. ¿El token sirve de verdad? → un GET a ClosingProcesses, que no depende de
     que tengamos cuentas ni posiciones cargadas.

Al final imprime exactamente qué dejar en el `.env`.

Uso (desde la raíz del repo):
    python -m scripts.diag_postrade_auth

Requiere en el .env:
    POSTRADE_USUARIO=...
    POSTRADE_PASSWORD=...
    POSTRADE_BASE_URL=...   (opcional — default producción)
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import requests

import config
from core import postrade

# La consola de Windows abre en cp1252 y revienta con las flechas y los ticks.
# El diag tiene que poder correrse desde la PC del usuario, no solo del Droplet.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "=" * 78


def _tapado(v: str | None) -> str:
    """Muestra lo justo para reconocer el valor sin exponerlo.

    Con valores cortos no se muestra NADA del contenido: una contraseña de 8
    caracteres con las puntas a la vista queda prácticamente revelada.
    """
    v = (v or "").strip()
    if not v:
        return "(VACÍO)"
    if len(v) <= 12:
        return f"(oculto, {len(v)} chars)"
    return f"{v[:4]}…{v[-2:]} ({len(v)} chars)"


def _cuerpo(r: requests.Response, limite: int = 300) -> str:
    try:
        return json.dumps(r.json(), ensure_ascii=False)[:limite]
    except ValueError:
        return (r.text or "")[:limite].replace("\n", " ")


def _ultimo_habil() -> str:
    """AAAAMMDD del último día hábil (sin feriados: solo evita sábado y domingo).

    Alcanza para una prueba de vida — si cae feriado la API contesta igual, con
    la lista vacía, y eso también responde la pregunta que este diag hace.
    """
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


# --------------------------------------------------------------------------- #
def paso_credenciales() -> tuple[str, str]:
    print(SEP)
    print("1. CREDENCIALES DEL .env")
    print(SEP)
    usuario = config.POSTRADE_USUARIO or ""
    password = config.POSTRADE_PASSWORD or ""
    print(f"  POSTRADE_USUARIO  : {_tapado(usuario)}")
    print(f"  POSTRADE_PASSWORD : {_tapado(password)}")
    print(f"  POSTRADE_BASE_URL : {config.POSTRADE_BASE_URL}")
    entorno = "PRODUCCIÓN" if "demoapi" not in config.POSTRADE_BASE_URL else "TESTING/DEMO"
    print(f"  → entorno         : {entorno}")
    if not usuario or not password:
        print("\n  ✗ Faltan credenciales en el .env. No se puede seguir.")
        raise SystemExit(1)
    return usuario, password


def paso_token(usuario: str, password: str) -> tuple[str | None, str | None]:
    """Pide el token por las dos vías. Devuelve (token, estilo_que_funcionó)."""
    print()
    print(SEP)
    print("2. TOKEN — las dos formas que admite el manual")
    print(SEP)
    url = f"{config.POSTRADE_BASE_URL.rstrip('/')}{postrade.TOKEN_PATH}"
    print(f"  POST {url}\n")

    credenciales = {"nombreUsuario": usuario, "password": password}
    ganador: tuple[str, str] | None = None

    def _pedir(estilo: str) -> requests.Response:
        if estilo == "body":
            return requests.post(
                url,
                json=credenciales,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=60,
            )
        return requests.post(
            url, params=credenciales, headers={"Accept": "application/json"}, timeout=60
        )

    for estilo in ("body", "query"):
        # Si el body ya trajo token, no se prueba la querystring: medido, ese
        # camino se cuelga (dos ReadTimeout de 60s) y dejaría al diag dos
        # minutos parado para confirmar algo que ya no cambia ninguna decisión
        # — el default del cliente es body. Se prueba solo si el body falló.
        if ganador is not None:
            print(f"  – {'credenciales en la QUERYSTRING':<34} → no se prueba (el body ya anduvo)")
            break
        etiqueta = (
            "credenciales en el BODY (JSON)" if estilo == "body"
            else "credenciales en la QUERYSTRING"
        )
        r = None
        for intento in (1, 2):
            try:
                r = _pedir(estilo)
                break
            except requests.RequestException as e:
                # Medido: el camino por querystring a veces se cuelga. Un
                # timeout NO prueba que la forma no sirva, así que se reintenta
                # antes de sacar conclusiones.
                if intento == 1:
                    print(f"  … {etiqueta:<34} → {type(e).__name__}, reintento")
                    continue
                print(f"  ✗ {etiqueta:<34} → red: {type(e).__name__}: {e}")
        if r is None:
            continue

        marca = "✓" if r.status_code == 200 else "✗"
        print(f"  {marca} {etiqueta:<34} → HTTP {r.status_code}")

        tok = None
        if r.status_code == 200:
            try:
                tok = postrade.desempaquetar(r.json(), contexto="token")
            except (ValueError, postrade.PostradeError) as e:
                print(f"      ⚠️ HTTP 200 pero el sobre trae un ERROR → {e}")
        if isinstance(tok, str) and tok.strip():
            print(f"      token: {_tapado(tok)}")
            if ganador is None:
                ganador = (tok.strip(), estilo)
        else:
            print(f"      cuerpo: {_cuerpo(r)}")

    if ganador is None:
        print("\n  ✗ Ninguna de las dos formas devolvió token.")
        print("    OJO: esta API rechaza las credenciales con HTTP 200 y el error")
        print("    ADENTRO del sobre (Status='Unauthorized', Code='401'). Si viste")
        print("    eso arriba, es rechazo de credenciales — no un problema de red.")
        print("    Se le reclama a Argentina Clearing: atencionalcliente@matbarofex.com.ar")
        return None, None

    tok, estilo = ganador
    print(f"\n  → sirve: {estilo}. El token declara 24hs de vida (no viene en la respuesta).")
    return tok, estilo


def paso_header(tok: str) -> str | None:
    """Mide si el token va crudo o con `Bearer `. Devuelve el prefijo que anda.

    Prueba contra VARIOS métodos, no contra uno: si el único que probáramos
    resultara no estar habilitado para nuestro usuario, el diag concluiría "el
    token no sirve" cuando el token está perfecto. Alcanza con que UNO conteste
    para saber cómo viaja el header.
    """
    print()
    print(SEP)
    print("3. CÓMO VIAJA EL TOKEN — Token / crudo / Bearer (medido, no supuesto)")
    print(SEP)
    fecha = _ultimo_habil()
    # Referencial primero: no depende de fechas ni de que tengamos posiciones.
    candidatos = (
        ("PreTrade/CurrencyList", {}),
        ("PosTrade/ClosingProcesses", {"EntryDate": fecha}),
        ("PreTrade/PartyDetails", {}),
    )

    for prefijo, etiqueta in (
        ("Token ", "Authorization: Token <token>"),
        ("", "Authorization: <token>"),
        ("Bearer ", "Authorization: Bearer <token>"),
    ):
        for path, params in candidatos:
            try:
                r = requests.get(
                    f"{config.POSTRADE_BASE_URL.rstrip('/')}/{path}",
                    params=params or None,
                    headers={"Authorization": f"{prefijo}{tok}", "Accept": "application/json"},
                    timeout=60,
                )
            except requests.RequestException as e:
                print(f"  ✗ {etiqueta:<30} {path:<28} → red: {type(e).__name__}")
                continue

            detalle = f"HTTP {r.status_code}"
            ok = False
            if r.status_code == 200:
                try:
                    postrade.desempaquetar(r.json(), contexto=path)
                    ok = True
                    detalle = "OK"
                except postrade.PostradeAuthError:
                    detalle = "el sobre dice 401 (header mal)"
                except (ValueError, postrade.PostradeError) as e:
                    # No es OK, pero TAMPOCO es un rechazo de auth: el header
                    # llegó bien y el método contestó otra cosa (falta un
                    # parámetro, no está habilitado). Eso ya prueba el header.
                    detalle = f"responde, pero: {str(e)[:90]}"
            print(f"  {'✓' if ok else '·'} {etiqueta:<30} {path:<28} → {detalle}")
            if ok:
                print(f"\n  → sirve: Authorization: {prefijo!r} + token")
                return prefijo

    print("\n  ✗ El token no fue aceptado de ninguna de las tres formas.")
    print("    OJO al mensaje de arriba, porque distingue DOS cosas distintas:")
    print("      · 'Invalid Authorization header.'  → el FORMATO del header está")
    print("        mal (le falta el prefijo). NO es un problema de permisos.")
    print("      · 'Invalid Authorization'          → las credenciales no entran.")
    print("    Si no es ninguna de las dos, ahí sí puede faltar el PERMISO sobre")
    print("    los métodos, que es otro reclamo y otro interlocutor.")
    return None


def paso_cliente(prefijo: str) -> None:
    """La prueba que importa: el cliente real de `core/postrade.py`, de punta a punta.

    No repite la llamada a mano: usa `postrade.ping()`. Si el diag probara por
    su cuenta, podría pasar en verde con un cliente roto — que es exactamente
    lo que un diagnóstico no puede permitirse.
    """
    print()
    print(SEP)
    print("4. EL CLIENTE REAL (core/postrade.py) DE PUNTA A PUNTA")
    print(SEP)
    config.POSTRADE_TOKEN_PREFIJO = prefijo
    postrade.reset_token()
    try:
        valor = postrade.ping()
    except postrade.PostradeError as e:
        print(f"  ✗ {e}")
        return
    muestra = json.dumps(valor, ensure_ascii=False)[:400]
    print(f"  ✓ ping() [CurrencyList] contestó: {muestra}")


def main() -> None:
    usuario, password = paso_credenciales()
    tok, estilo = paso_token(usuario, password)
    if not tok:
        raise SystemExit(2)
    prefijo = paso_header(tok)
    if prefijo is None:
        raise SystemExit(3)
    paso_cliente(prefijo)

    print()
    print(SEP)
    print("RESULTADO — qué dejar en el .env")
    print(SEP)
    print(f"  POSTRADE_BASE_URL={config.POSTRADE_BASE_URL}")
    print("  POSTRADE_USUARIO=<el usuario>")
    print("  POSTRADE_PASSWORD=<la contraseña>")
    if estilo != "body":
        print(f"  POSTRADE_AUTH_STYLE={estilo}      # el default 'body' NO funcionó")
    if prefijo:
        print(f"  POSTRADE_TOKEN_PREFIJO={prefijo!r}   # el token NO va crudo")
    if estilo == "body" and not prefijo:
        print("  (nada más: los defaults de config.py son los que funcionan)")


if __name__ == "__main__":
    main()
