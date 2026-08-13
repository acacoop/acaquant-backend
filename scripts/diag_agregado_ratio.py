"""scripts/diag_agregado_ratio.py — ¿cuánto comprimiría el agregado comercial?

LA PREGUNTA QUE DECIDE EL PROYECTO
==================================
El agregado del Tablero Comercial (`docs/AGREGADO_COMERCIAL.md`) reemplaza
"sumar todos los boletos" por "leer una fila por (día, cuenta)". Cuánto mejora
NO es una opinión: es el ratio de compresión.

    ratio alto (5×+)  → las queries pasan de sumar cientos de miles de filas a
                        decenas de miles. El proyecto vale.
    ratio bajo (~1×)  → cada cuenta opera una vez por día: el agregado tendría
                        casi las mismas filas que el original y la mejora sería
                        marginal. EL PROYECTO NO VALE — y son días ahorrados.

Sin este número, prometer una mejora es adivinar (REGLA #2).

Mide las dos tablas del tablero:
  · `negocio_movimientos` → grano (fecha, id_cuenta, moneda)  [volumen]
  · `portafolio.tenencia` → grano (fecha, id_cuenta)          [AuM]

En tenencia la compresión es por ACTIVO (una cuenta con 10 posiciones colapsa
10 filas en 1), así que suele comprimir más que la de boletos.

Read-only. Tarda unos segundos: recorre las tablas enteras para contar
combinaciones distintas (es un COUNT DISTINCT, no hay forma barata).

Uso (en el Droplet):
    python -m scripts.diag_agregado_ratio
"""
from __future__ import annotations

import time

from core.postgres import get_pool

# (etiqueta, tabla, columnas del grano propuesto, columna que se suma)
_CASOS = (
    ("VOLUMEN  negocio_movimientos", "operaciones.negocio_movimientos",
     ("fecha", "id_cuenta", "moneda"), "anulado_en IS NULL"),
    ("AuM      portafolio.tenencia", "portafolio.tenencia",
     ("fecha", "id_cuenta"), "aum = 'si'"),
)


def main() -> int:
    pool = get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        conn.autocommit = True
        for etiqueta, tabla, grano, filtro in _CASOS:
            cols = ", ".join(grano)
            print(f"\n{etiqueta}")
            print(f"   grano propuesto: ({cols})   filtro: {filtro}")
            t0 = time.perf_counter()
            try:
                cur.execute(
                    f"SELECT count(*) AS filas, count(DISTINCT ({cols})) AS grupos "
                    f"FROM {tabla} WHERE {filtro}")
                filas, grupos = cur.fetchone()
            except Exception as e:
                print(f"   no se pudo medir: {str(e).splitlines()[0][:80]}")
                continue
            seg = time.perf_counter() - t0
            if not grupos:
                print("   sin filas que cumplan el filtro.")
                continue
            ratio = filas / grupos
            print(f"   filas hoy      : {filas:>10,}")
            print(f"   filas agregado : {grupos:>10,}")
            print(f"   RATIO          : {ratio:>10.1f}×   (medido en {seg:.1f}s)")
            if ratio >= 4:
                print("   → VALE. El agregado divide el trabajo por ~"
                      f"{ratio:.0f} y encima deja de crecer con los boletos.")
            elif ratio >= 2:
                print("   → GANANCIA MODERADA. Mejora, pero lo fuerte va a ser")
                print("     la escalabilidad más que la velocidad de hoy.")
            else:
                print("   → NO VALE por velocidad: el agregado tendría casi las")
                print("     mismas filas. Solo se justificaría por escalabilidad,")
                print("     y hay que decidirlo a sabiendas.")
    print("""
CÓMO USARLO
  El ratio es el divisor del trabajo. Una query que hoy suma 270.000 filas, con
  ratio 5× pasa a sumar 54.000: eso es lo que se puede esperar, ni más ni menos.
  Si los dos casos dan bajo, el proyecto de `docs/AGREGADO_COMERCIAL.md` NO se
  hace — y son días ahorrados con un dato, no con una corazonada.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
