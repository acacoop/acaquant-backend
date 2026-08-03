"""scripts/diag_calendario.py — ¿por qué el calendario económico está vacío?

Read-only sobre la DB. Pega a FMP (3-4 requests del free tier de 250/día).

La corrida del 2026-08-03 dio 401/403 en el calendario. Ese error solo NO alcanza
para decidir: hay que separar "la key no sirve" de "la key sirve pero el plan no
incluye el calendario", porque se arreglan distinto (rotar credencial vs pagar /
cambiar de proveedor). Este diag prueba, en orden:

  1. Estado de home.market_calendar (¿hay algo guardado?).
  2. ¿La key existe en el entorno? (se imprime ENMASCARADA, nunca completa).
  3. Sonda a un endpoint que SÍ está en el plan free (perfil de AAPL) → dice si
     la KEY es válida, independientemente del calendario.
  4. Sonda al calendario en las DOS bases: `/stable/economic-calendar` (la
     documentada hoy) y `/api/v3/economic_calendar` (legacy). Muestra el status
     HTTP y el cuerpo — FMP explica ahí si el endpoint es de pago.
  5. Si alguna trae datos: desglose por país e impacto ANTES del filtro del job,
     para ver si el filtro (AR/US/BR + impacto alto) los descartaría.

Uso:
    python -m scripts.diag_calendario
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import requests

from core.postgres import connect


def _mask(k: str | None) -> str:
    if not k:
        return "(vacía / no seteada)"
    return f"seteada · {len(k)} chars · termina en …{k[-4:]}"


def _sonda(url: str, params: dict) -> tuple[int, str, list | dict | None]:
    """→ (status, cuerpo recortado, json parseado o None). No levanta."""
    try:
        r = requests.get(url, params=params, timeout=20)
    except requests.RequestException as e:
        return 0, f"red: {e}", None
    try:
        return r.status_code, r.text[:300], r.json()
    except ValueError:
        return r.status_code, r.text[:300], None


def main() -> int:
    print("=== 1. Estado de home.market_calendar ===")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), min(evt_ts), max(evt_ts) FROM home.market_calendar")
        n, f_min, f_max = cur.fetchone()
        print(f"filas: {n}   rango: {f_min} → {f_max}")
        cur.execute("SELECT count(*) FROM home.market_calendar WHERE evt_ts >= now()")
        print(f"futuros (lo que muestra la vista): {cur.fetchone()[0]}")

    from config import FMP_API_KEY
    print(f"\n=== 2. FMP_API_KEY ===\n{_mask(FMP_API_KEY)}")
    if not FMP_API_KEY:
        print("→ no hay key: setear FMP_API_KEY en .env del Droplet y reintentar.")
        return 1

    hoy = datetime.now(UTC).date()
    desde, hasta = hoy.isoformat(), (hoy + timedelta(days=60)).isoformat()

    print("\n=== 3. ¿La KEY es válida? (endpoint del plan free: perfil AAPL) ===")
    st, body, _ = _sonda("https://financialmodelingprep.com/stable/profile",
                         {"symbol": "AAPL", "apikey": FMP_API_KEY})
    print(f"HTTP {st} — {body[:160]}")
    key_ok = st == 200
    print("→ la KEY es válida." if key_ok else "→ la KEY NO sirve (rotarla en FMP).")

    print("\n=== 4. Sondas al CALENDARIO ===")
    candidatos = [
        ("stable", "https://financialmodelingprep.com/stable/economic-calendar"),
        ("legacy", "https://financialmodelingprep.com/api/v3/economic_calendar"),
    ]
    datos: list[dict] = []
    for nombre, url in candidatos:
        st, body, js = _sonda(url, {"from": desde, "to": hasta, "apikey": FMP_API_KEY})
        print(f"\n[{nombre}] {url}")
        print(f"  HTTP {st} — {body[:240]}")
        if st == 200 and isinstance(js, list) and js:
            print(f"  → OK: {len(js)} eventos")
            if not datos:
                datos = js
        elif st == 403:
            print("  → 403: la key es válida pero este endpoint NO está en el plan.")
        elif st == 401:
            print("  → 401: key inválida para este endpoint.")

    if not datos:
        print("\n=== VEREDICTO ===")
        if key_ok:
            print("La KEY funciona pero el CALENDARIO no está disponible con el plan actual.")
            print("No se arregla con código: hay que subir de plan en FMP o cambiar de")
            print("proveedor de calendario económico.")
        else:
            print("La KEY no sirve. Generar una nueva en FMP y actualizar FMP_API_KEY en .env.")
        return 1

    print("\n=== 5. ¿El filtro del job dejaría pasar algo? ===")
    print("muestra cruda (primeros 3):")
    for ev in datos[:3]:
        print(f"  {ev}")
    print(f"\npaíses (top 10): {Counter(str(e.get('country')) for e in datos).most_common(10)}")
    print(f"valores de `impact`: {Counter(str(e.get('impact')) for e in datos).most_common()}")

    from jobs.economic_calendar import IMPACT_MIN, _country_code, _impact_to_int
    pasan = [e for e in datos
             if _country_code(e.get("country", "")) is not None
             and _impact_to_int(e.get("impact")) >= IMPACT_MIN]
    print(f"\npasan el filtro (AR/US/BR + impacto ≥ {IMPACT_MIN}): {len(pasan)} de {len(datos)}")
    if not pasan:
        print("→ FMP trae datos pero el filtro los descarta TODOS: comparar los valores")
        print("  de arriba con COUNTRIES / IMPACT_MAP en jobs/economic_calendar.py.")
    else:
        print("→ todo OK: correr `python -m jobs.economic_calendar` llena la tabla.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
