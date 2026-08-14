"""scripts/diag_interbanking_auth.py — por qué el token de Interbanking da 401.

READ-ONLY. No escribe en la base ni en Interbanking: solo pide tokens.

Prueba en matriz todas las combinaciones plausibles de (endpoint × forma de
mandar las credenciales × con/sin scope) y muestra de cada una el status, el
header `WWW-Authenticate` y el cuerpo. Ese header es la pieza que Postman no
mostraba y que suele traer el motivo real cuando el cuerpo viene vacío.

Antes de la matriz pregunta al servidor su propio documento de descubrimiento
OIDC, que es la fuente de verdad: ahí salió que el `tokenUrl` de los YAML de
Interbanking (`/cas/oidc/accessToken`) no es el endpoint real
(`/cas/oidc/oidcAccessToken`).

Si alguna combinación devuelve 200, además prueba el token contra el gateway y
te dice qué poner en el .env. Si NINGUNA funciona, hace un último sondeo al
gateway con el `client_id` solo — sirve para distinguir "credenciales mal" de
"aplicación no habilitada", que es lo que habría que reclamarle al proveedor.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_interbanking_auth

Requiere en el .env:
    INTERBANKING_CLIENT_ID=...
    INTERBANKING_CLIENT_SECRET=...
    INTERBANKING_CUSTOMER_ID=...      (opcional para este diag)
"""
from __future__ import annotations

import base64
import json
import time

import requests

import config

DISCOVERY = "https://auth.interbanking.com.ar/cas/oidc/.well-known/openid-configuration"
GATEWAY = "https://api-gw.interbanking.com.ar/api/prod/v1"

# Candidatos de endpoint. El de discovery se antepone en runtime.
URLS_BASE = [
    "https://auth.interbanking.com.ar/cas/oidc/oidcAccessToken",
    "https://auth.interbanking.com.ar/cas/oidc/accessToken",
    "https://auth.interbanking.com.ar/cas/oidc/token",
]

SEP = "=" * 78


def _tapado(v: str | None) -> str:
    """Muestra lo justo para reconocer el valor sin exponerlo."""
    v = (v or "").strip()
    if not v:
        return "(VACÍO)"
    if len(v) <= 8:
        return f"{v[:2]}…{v[-1:]} ({len(v)} chars)"
    return f"{v[:4]}…{v[-2:]} ({len(v)} chars)"


def revisar_credenciales() -> tuple[str, str]:
    print(SEP)
    print("1. CREDENCIALES DEL .env")
    print(SEP)
    cid = config.INTERBANKING_CLIENT_ID or ""
    sec = config.INTERBANKING_CLIENT_SECRET or ""
    cust = config.INTERBANKING_CUSTOMER_ID or ""

    print(f"  INTERBANKING_CLIENT_ID     : {_tapado(cid)}")
    print(f"  INTERBANKING_CLIENT_SECRET : {_tapado(sec)}")
    print(f"  INTERBANKING_CUSTOMER_ID   : {cust or '(VACÍO)'}")

    problemas = []
    if not cid.strip():
        problemas.append("falta INTERBANKING_CLIENT_ID")
    if not sec.strip():
        problemas.append("falta INTERBANKING_CLIENT_SECRET")
    for nombre, val in (("CLIENT_ID", cid), ("CLIENT_SECRET", sec)):
        if val and val != val.strip():
            problemas.append(f"{nombre} tiene espacios al principio o al final")
        if val and any(c in val for c in " \t\n\r"):
            problemas.append(f"{nombre} tiene espacios o saltos de línea ADENTRO")
        if val and ('"' in val or "'" in val):
            problemas.append(f"{nombre} tiene comillas adentro (el .env no las necesita)")

    if problemas:
        print("\n  ⚠️  PROBLEMAS:")
        for p in problemas:
            print(f"     - {p}")
    else:
        print("\n  ✓ Sin problemas de formato.")

    if not cid.strip() or not sec.strip():
        raise SystemExit("\nSin credenciales no hay nada que probar. Cargalas en el .env.")
    return cid.strip(), sec.strip()


def leer_discovery() -> list[str]:
    """Devuelve la lista de URLs a probar, con la declarada por el servidor primero."""
    print()
    print(SEP)
    print("2. QUÉ DICE EL SERVIDOR DE SÍ MISMO (discovery OIDC, sin credenciales)")
    print(SEP)
    urls = list(URLS_BASE)
    try:
        r = requests.get(DISCOVERY, headers={"Accept": "application/json"}, timeout=20)
        print(f"  HTTP {r.status_code} — {DISCOVERY}")
        if r.status_code != 200:
            print(f"  cuerpo: {r.text[:300]}")
            return urls
        j = r.json()
    except Exception as e:  # diag: cualquier fallo se reporta, no corta
        print(f"  ✗ No se pudo leer el discovery: {type(e).__name__}: {e}")
        return urls

    te = j.get("token_endpoint")
    grants = j.get("grant_types_supported") or []
    metodos = j.get("token_endpoint_auth_methods_supported") or []
    scopes = j.get("scopes_supported") or []

    print(f"  issuer            : {j.get('issuer')}")
    print(f"  token_endpoint    : {te}")
    print(f"  grants            : {grants}")
    print(f"  auth methods      : {metodos}")
    print(f"  ¿client_credentials habilitado? : {'SÍ' if 'client_credentials' in grants else 'NO'}")
    scope_ok = config.INTERBANKING_SCOPE in scopes if scopes else None
    print(f"  ¿scope '{config.INTERBANKING_SCOPE}' existe? : "
          f"{'SÍ' if scope_ok else ('NO' if scope_ok is False else '?')}")

    if te and te not in urls:
        urls.insert(0, te)
    elif te:
        urls.remove(te)
        urls.insert(0, te)
    return urls


def _intentar(url: str, cid: str, secret: str, estilo: str, con_scope: bool) -> requests.Response | None:
    data = {"grant_type": "client_credentials"}
    if con_scope and config.INTERBANKING_SCOPE:
        data["scope"] = config.INTERBANKING_SCOPE
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    params = None

    if estilo == "basic":
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{cid}:{secret}".encode()
        ).decode()
    elif estilo == "post":
        data["client_id"] = cid
        data["client_secret"] = secret
    elif estilo == "query":
        params = dict(data)
        params["client_id"] = cid
        params["client_secret"] = secret
        data = {}
    elif estilo == "basic+header":
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{cid}:{secret}".encode()
        ).decode()
        headers["client_id"] = cid

    try:
        return requests.post(url, data=data, params=params, headers=headers, timeout=20)
    except requests.RequestException as e:
        print(f"      ✗ red: {type(e).__name__}: {e}")
        return None


def matriz(urls: list[str], cid: str, secret: str) -> tuple[str, str, bool, str] | None:
    """Prueba todas las combinaciones. Devuelve la primera que dé 200."""
    print()
    print(SEP)
    print("3. MATRIZ DE INTENTOS DE TOKEN")
    print(SEP)

    estilos = ["basic", "post", "query", "basic+header"]
    ganador = None

    for url in urls:
        print(f"\n  ── {url}")
        for estilo in estilos:
            for con_scope in (True, False):
                etiqueta = f"{estilo:<13} scope={'sí' if con_scope else 'no '}"
                r = _intentar(url, cid, secret, estilo, con_scope)
                time.sleep(0.3)
                if r is None:
                    continue

                wa = r.headers.get("WWW-Authenticate", "")
                cuerpo = (r.text or "").replace("\n", " ")[:160]
                marca = "✓✓✓" if r.status_code == 200 else "   "
                print(f"     {marca} {etiqueta} → HTTP {r.status_code}")
                if wa:
                    print(f"           WWW-Authenticate: {wa}")
                if r.status_code != 200:
                    print(f"           {cuerpo}")

                if r.status_code == 200 and ganador is None:
                    try:
                        tok = r.json().get("access_token")
                    except ValueError:
                        tok = None
                    if tok:
                        ganador = (url, estilo, con_scope, tok)
                        print("           >>> FUNCIONA. Sigue probando el resto para dejar el cuadro completo.")
    return ganador


def probar_gateway(tok: str, cid: str) -> None:
    print()
    print(SEP)
    print("4. EL TOKEN CONTRA EL GATEWAY (GET /accounts)")
    print(SEP)
    cust = (config.INTERBANKING_CUSTOMER_ID or "").strip()
    if not cust:
        print("  ⚠️  Sin INTERBANKING_CUSTOMER_ID no se puede llamar a /accounts.")
        print("      Cargá el código de abonado en el .env y volvé a correr.")
        return

    r = requests.get(
        f"{GATEWAY}/accounts",
        params={"customer-id": cust, "account-type": "CC", "limit": 100, "page": 0},
        headers={"client_id": cid, "Authorization": f"Bearer {tok}", "Accept": "application/json"},
        timeout=30,
    )
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  {r.text[:500]}")
        print("\n  El token sirve pero el gateway rechaza. Los sospechosos, en orden:")
        print("   - customer-id equivocado (código de abonado de OTRA empresa o inventado)")
        print("   - la aplicación no está suscripta al producto/plan de esta API")
        return

    j = r.json()
    lista = j.get("accounts") or []
    print(f"  ✓ {len(lista)} cuentas | total_rows={(j.get('general_data') or {}).get('total_rows')}")
    for c in lista[:40]:
        print(f"     banco={c.get('bank_number')} ({c.get('bank_name')}) "
              f"| {c.get('account_type')} {c.get('currency')} "
              f"| nro={c.get('account_number')} | cbu={c.get('account_cbu')} "
              f"| {c.get('account_label')}")


def sondear_gateway_sin_token(cid: str) -> None:
    """Sin token válido: ver si el gateway al menos reconoce el client_id.

    Distingue 'credenciales mal' de 'aplicación no habilitada': son reclamos
    distintos al proveedor.
    """
    print()
    print(SEP)
    print("4. SONDEO DEL GATEWAY SIN TOKEN (para saber qué reclamar)")
    print(SEP)
    cust = (config.INTERBANKING_CUSTOMER_ID or "").strip() or "A00000A"
    for desc, headers in (
        ("solo header client_id", {"client_id": cid}),
        ("sin ningún header de auth", {}),
    ):
        try:
            r = requests.get(
                f"{GATEWAY}/accounts",
                params={"customer-id": cust, "account-type": "CC"},
                headers={**headers, "Accept": "application/json"},
                timeout=30,
            )
            print(f"  {desc:<28} → HTTP {r.status_code}: {(r.text or '')[:200]}")
        except requests.RequestException as e:
            print(f"  {desc:<28} → red: {e}")
    print("\n  Leer así:")
    print("   - Si los dos dan el MISMO error, el gateway ni mira el client_id →")
    print("     el problema está antes: la app no está habilitada/suscripta.")
    print("   - Si difieren, el client_id SÍ se reconoce y lo que falla es el token.")


def main() -> None:
    print("\nDIAGNÓSTICO DE AUTENTICACIÓN — INTERBANKING")
    cid, secret = revisar_credenciales()
    urls = leer_discovery()
    ganador = matriz(urls, cid, secret)

    print()
    print(SEP)
    print("VEREDICTO")
    print(SEP)

    if not ganador:
        print("  ✗ NINGUNA combinación devolvió un token.")
        sondear_gateway_sin_token(cid)
        print("\n  Con estos datos, el reclamo a consultasapi@interbanking.com.ar es:")
        print("   1. Confirmar la URL real del endpoint de token para nuestra aplicación.")
        print("   2. Confirmar que la aplicación está SUSCRIPTA y APROBADA en el plan.")
        print(f"   3. Confirmar que el scope '{config.INTERBANKING_SCOPE}' está habilitado para ella.")
        print("   4. Confirmar que el client_secret vigente es el que tenemos"
              " (si se regeneró, el viejo dejó de servir).")
        print("\n  Adjuntar: la matriz de arriba. Muestra que probamos todos los endpoints"
              "\n  y las cuatro formas de mandar credenciales, y que el error no cambia.")
        return

    url, estilo, con_scope, tok = ganador
    print("  ✓ HAY TOKEN.")
    print(f"     endpoint : {url}")
    print(f"     estilo   : {estilo}")
    print(f"     scope    : {'se manda' if con_scope else 'NO se manda'}")
    print("\n  Poné esto en el .env del Droplet para que el cliente use la combinación buena:")
    print(f"     INTERBANKING_TOKEN_URL={url}")
    print(f"     INTERBANKING_AUTH_STYLE={'post' if estilo == 'post' else 'basic'}")
    if not con_scope:
        print("     INTERBANKING_SCOPE=")
    if estilo in ("query", "basic+header"):
        print(f"\n  ⚠️  Ojo: funcionó con el estilo '{estilo}', que core/interbanking.py NO")
        print("      implementa (solo basic y post). Avisar para agregarlo.")

    probar_gateway(tok, cid)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # diag: el traceback completo no aporta acá
        print(f"\n✗ {type(e).__name__}: {e}")
        print(json.dumps({"hint": "revisá el .env y la conectividad de red"}, ensure_ascii=False))
