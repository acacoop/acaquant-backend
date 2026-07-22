"""smoke_asistente — la primera pregunta real al ASISTENTE DE NEGOCIO (P7).

Reemplaza el curl (REGLA #0: nada de headers/quotes copy-paste en la consola
del Droplet): llama al service EN PROCESO, con la DB y la credencial reales
del .env. Gasta tokens de UNA pregunta.

Correr en el Droplet:
    python -m scripts.smoke_asistente --ruteo          # a dónde va cada tarea (0 tokens)
    python -m scripts.smoke_asistente --tools          # ¿cada tool trae datos? (0 tokens)
    python -m scripts.smoke_asistente
    python -m scripts.smoke_asistente --pregunta "¿Cuál es el AuM total hoy?"
    python -m scripts.smoke_asistente --chat-id <id>   # continuar la conversación

Imprime la respuesta + el chat_id (para el turno siguiente) + la traza_id
(para mirarla en OBSERVABILIDAD → IA). Si algo se niega (aduana sin catálogo,
presupuesto, credencial), lo dice claro.
"""
from __future__ import annotations

import argparse
import os


def _ruteo() -> None:
    """Qué proveedor/modelo usa cada tarea y si su credencial está puesta.
    Cero tokens — es la verificación de que el ruteo de privacidad quedó bien."""
    from core import ai, llm

    print(f"{'tarea':<24} {'proveedor':<10} {'modelo':<22} {'no entrena':<11} credencial")
    print("-" * 84)
    for tarea in sorted(ai._TAREAS):
        cfg = ai._config(tarea)
        prov = ai._proveedor(cfg)
        print(f"{tarea:<24} {prov:<10} {ai._modelo(cfg):<22} "
              f"{('sí' if llm.no_entrena(prov) else 'NO'):<11} "
              f"{'OK' if llm.configurado(prov) else 'FALTA'}")
    print("\nLa tarea del asistente de negocio debe ir a un proveedor con "
          "'no entrena = sí'.\nSi su credencial dice FALTA, el asistente se "
          "apaga (NO cae al otro proveedor).")


# Argumentos de sonda por tool. Los valores son genéricos a propósito: no
# buscamos el número correcto, buscamos que la tool DEVUELVA ALGO. Las tools
# sin sonda (o que necesitan una ficha de una conversación real) se declaran
# acá igual, con el motivo — así el reporte nunca las omite en silencio.
#
# `"_rango": True` inyecta un período PASADO (últimos 30 días). Va solo donde
# corresponde: metérselo a una tool que mira hacia ADELANTE (cobros futuros)
# hace que la sonda mienta — el primer sondeo la mostró leyendo el pasado y
# pareciendo vacía (2026-07-22). Sin la marca, la tool usa SUS defaults, que
# es además lo que va a pasar en el chat real.
_SONDAS: dict[str, dict | None] = {
    "resumen_mesa":            {},
    "aum_composicion":         {},
    "cobros_futuros":          {"dias": 30},
    "flujo_de_fondos":         {"_rango": True},
    "pulso_mesa":              {},
    "jobs_fallidos":           {"dias": 7},
    "costo_ia":                {"dias": 7},
    "controles_calidad_datos": {},
    "serie_historica":         {"que": "macro", "clave": "cer", "_rango": True},
    "aum_variacion":           {},
    # una sonda por LENTE: son cuatro readers distintos detrás de una tool
    "tablero_comercial":       {"que": "operadores", "_rango": True},
    "tablero_comercial#objetivos":    {"que": "objetivos"},   # su default = mes en curso
    "tablero_comercial#cartera":      {"que": "cartera"},
    "tablero_comercial#sin_operador": {"que": "sin_operador"},
    "volumen_operado":         {"por": "mercado", "_rango": True},
    "aranceles_consolidado":   {"por": "mercado", "_rango": True},
    # necesitan una FICHA que solo existe dentro de un chat con la aduana
    "quien_es":           None,
    "rendimiento_cuenta": None,
    "posiciones_cuenta":  None,
    "aum_historico":      None,
}

# Lo que delata a una tool muerta: contesta, pero contesta que no hay nada.
# (Los tres bugs de shape encontrados el 2026-07-22 se veían exactamente así.)
_SEÑALES_VACIO = ("sin datos", "no hay", "sin operaciones", "sin menciones",
                  "todo limpio", "sin snapshots", "sin movimientos")


def _sondear_tools() -> None:
    """Ejecuta CADA tool del asistente contra la DB real (read-only, 0 tokens)
    y muestra qué devuelve. Existe porque los tests mockean el resultado de los
    services: si la tool lee una clave que el service no emite, el unit test
    pasa igual y la tool queda MUDA en producción — el modelo lo tapa
    improvisando y nadie se entera. Esto lo hace visible en una corrida."""
    from datetime import UTC, datetime, timedelta

    from api.services import asistente_tools as at

    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    rango = {"desde": (hoy - timedelta(days=30)).isoformat(), "hasta": hoy.isoformat()}
    declaradas = at.herramientas_declaradas()
    sin_sonda = sorted(declaradas - {k.split("#")[0] for k in _SONDAS})
    vacias, rotas = [], []

    print(f"sondeando {len(_SONDAS)} sondas sobre {len(declaradas)} tools "
          "(read-only, sin LLM)\n")
    for clave in sorted(_SONDAS):
        # "tool#variante" permite sondear varias lentes de una misma tool
        nombre = clave.split("#")[0]
        sonda = _SONDAS[clave]
        if sonda is None:
            print(f"— {clave:<30} SALTEADA (necesita una ficha de un chat real)")
            continue
        args = dict(sonda)
        if args.pop("_rango", False):
            args = {**rango, **args}
        try:
            out = at.ejecutar(nombre, args, mapping={"fichas": {}},
                              usuario=os.getenv("EVAL_EMAIL", "smoke@acaquant.local"))
        except Exception as e:                      # no debería: ejecutar() atrapa
            rotas.append(clave)
            print(f"✗ {clave:<30} EXCEPCIÓN {type(e).__name__}: {e}")
            continue
        una_linea = " ".join(out.split())[:104]
        bajo = out.lower()
        if "falló" in bajo or "desconocida" in bajo:
            rotas.append(clave)
            marca = "✗"
        elif "permiso" in bajo:
            marca = "🔒"                              # gateado: no es una falla
        elif any(s in bajo for s in _SEÑALES_VACIO):
            vacias.append(clave)
            marca = "?"
        else:
            marca = "✓"
        print(f"{marca} {clave:<30} {una_linea}")

    print("\n" + "-" * 78)
    if sin_sonda:
        print(f"SIN SONDA (agregar a _SONDAS): {', '.join(sin_sonda)}")
    if rotas:
        print(f"ROTAS: {', '.join(rotas)}  ← la tool falla o no existe el handler")
    if vacias:
        print(f"VACÍAS: {', '.join(vacias)}\n"
              "  Puede ser legítimo (no hay anomalías, no hubo movimientos) o puede\n"
              "  ser que lea una clave que el service no devuelve. Verificar contra\n"
              "  la vista equivalente de la web antes de darlo por bueno.")
    if not (rotas or vacias or sin_sonda):
        print("todas las tools devolvieron datos.")
    print("\nEl 🔒 es una tool gateada por Control Comercial: el usuario del smoke "
          "no tiene\nel permiso. Para probarlas de verdad, correr con "
          "EVAL_EMAIL=<un email con el flag>.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ruteo", action="store_true",
                    help="mostrar a qué proveedor va cada tarea (no gasta tokens)")
    ap.add_argument("--tools", action="store_true",
                    help="ejecutar cada tool contra la DB real (no gasta tokens)")
    ap.add_argument("--pregunta", default="¿Cuál es el AuM total administrado hoy?")
    ap.add_argument("--chat-id", default=None)
    ap.add_argument("--email", default=os.getenv("EVAL_EMAIL", "smoke@acaquant.local"))
    args = ap.parse_args()

    if args.ruteo:
        _ruteo()
        return
    if args.tools:
        _sondear_tools()
        return

    from api.services import asistente

    r = asistente.responder(mensaje=args.pregunta, email=args.email, chat_id=args.chat_id)
    if not r.get("ok"):
        print(f"NO respondió — motivo: {r.get('motivo')} · {r.get('mensaje')}")
        raise SystemExit(1)
    print(f"chat_id : {r['chat_id']}")
    print(f"traza_id: {r.get('traza_id')}")
    print(f"\n{r['respuesta']}")


if __name__ == "__main__":
    main()
