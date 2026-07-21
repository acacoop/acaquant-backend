"""diag_pii_matcher — calibración del matcher de la aduana (core/pii_gateway).

READ-ONLY. Mide contra el catálogo REAL de clientes lo que Claude no puede
ver (REGLA #2), para que el user calibre:
  ASISTENTE_FUZZY_UMBRAL   (default 0.90)
  ASISTENTE_STOPLIST_EXTRA (palabras genéricas a excluir del match por token)

Qué imprime:
  1. Tamaño del catálogo (cuentas, nombres, tokens de match, documentos).
  2. Los 30 tokens más REPETIDOS entre clientes distintos → candidatos a
     stoplist (un token que aparece en 50 denominaciones es un genérico
     societario, no un apellido útil).
  3. Tokens ambiguos (mismo apellido en 2+ clientes) — detectan igual, pero
     no resuelven a cuenta.
  4. Sensibilidad del fuzzy: para una muestra de tokens del catálogo, cuántos
     OTROS tokens del catálogo matchearían a umbral 0.85 / 0.90 / 0.95 —
     si a 0.90 hay muchos cruces, subir el umbral.
  5. (opcional) --frase "texto": muestra qué tacharía la aduana en esa frase,
     con el umbral vigente. Probar con nombres reales y frases de mercado.

Correr en el Droplet:
  python -m scripts.diag_pii_matcher
  python -m scripts.diag_pii_matcher --frase "como viene la cuenta de Juan Perez"

Con el resultado: setear las env en el .env del Droplet y listo (la aduana
las lee en runtime). Este diag se BORRA cuando el tema cierre (REGLA #5).
"""
from __future__ import annotations

import argparse
import difflib
import random
from collections import Counter

from core import pii_gateway


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frase", help="probar la tokenización sobre una frase")
    ap.add_argument("--muestra", type=int, default=300,
                    help="tokens muestreados para la sensibilidad del fuzzy")
    args = ap.parse_args()

    cat = pii_gateway._catalogo()
    if cat is None:
        print("ERROR: no pude leer el catálogo (¿DB accesible?)")
        return

    print("── 1. catálogo ──")
    print(f"ids de cuenta: {len(cat['ids'])} · nombres completos: {len(cat['nombres'])} · "
          f"tokens de match: {len(cat['tokens'])} · documentos: {len(cat['documentos'])}")

    print("\n── 2. tokens más repetidos entre clientes (candidatos a stoplist) ──")
    conteo: Counter = Counter()
    for nombre in cat["nombres"]:
        for tok in set(nombre.split()):
            if tok in cat["tokens"]:
                conteo[tok] += 1
    for tok, n in conteo.most_common(30):
        marca = "  ← candidato stoplist" if n >= 10 else ""
        print(f"  {tok:<20} en {n} clientes{marca}")

    print("\n── 3. tokens ambiguos (apellido compartido, no resuelven a cuenta) ──")
    ambiguos = [t for t, cid in cat["tokens"].items() if cid == ""]
    print(f"  {len(ambiguos)} de {len(cat['tokens'])} "
          f"({100 * len(ambiguos) / max(1, len(cat['tokens'])):.1f}%)")

    print("\n── 4. sensibilidad del fuzzy (cruces token vs token del catálogo) ──")
    claves = list(cat["tokens"].keys())
    muestra = random.sample(claves, min(args.muestra, len(claves)))
    for umbral in (0.85, 0.90, 0.95):
        cruces = 0
        for tok in muestra:
            otros = [c for c in claves if c != tok]
            if difflib.get_close_matches(tok, otros, n=1, cutoff=umbral):
                cruces += 1
        print(f"  umbral {umbral}: {cruces}/{len(muestra)} tokens matchean OTRO token "
              f"({100 * cruces / max(1, len(muestra)):.0f}% de cruce)")
    print("  (mucho cruce a un umbral = a ese nivel el fuzzy confunde apellidos entre sí;"
          "\n   elegir el umbral más BAJO con cruce tolerable — más bajo tacha más typos)")

    if args.frase:
        print("\n── 5. prueba sobre la frase ──")
        limpio, mapping = pii_gateway.tokenize(args.frase)
        print(f"  original : {args.frase}")
        print(f"  tachado  : {limpio}")
        print(f"  fichas   : {mapping['fichas']}")


if __name__ == "__main__":
    main()
