"""bateria_guia.py — batería de 20 preguntas al GUÍA de la plataforma (vista ayuda).

Corre contra el copiloto REAL (DeepSeek). Además de imprimir cada respuesta
para revisión humana, corre CHEQUEOS AUTOMÁTICOS de las reglas del guía:
  ⚑ DATO     — apareció un número grande o un monto (el guía tiene PROHIBIDO
               dar datos; enteros chicos = pasos numerados, ok).
  ⚑ JERGA    — nombró cosas internas (tabla, SQL, endpoint, job, snapshot…).
  ⚑ SIN RUTA — la respuesta no menciona ninguna sección/menú conocido
               (probable respuesta vaga que no guía a ningún lado).
Las banderas son para REVISAR, no un veredicto — el juez sos vos.

Gasta tokens contra el presupuesto del email que pases; todo queda en
ia.trazas → OBSERVABILIDAD.

Uso (Droplet):
    python -m scripts.bateria_guia --email mollonicolas95@gmail.com
    python -m scripts.bateria_guia --email ... --desde 12   # retomar desde la N
"""
from __future__ import annotations

import argparse
import re
import time

from api.services import copiloto

PREGUNTAS = [
    # ── las del user ──
    "¿Cómo veo el AUM de mi cuenta?",
    "¿Dónde puedo ver los fondos que más se operaron?",
    "¿Cómo veo cuánto rindió mi cuenta?",
    "¿Cómo veo las acciones?",
    # ── operatoria y mercado ──
    "¿Cómo mando una orden de dólar MEP?",
    "¿Dónde veo cuánto pagan las cauciones hoy?",
    "¿Dónde sigo un bono CER y su rendimiento?",
    "¿Dónde veo las opciones de GGAL?",
    "¿Dónde veo los CEDEARs que más volumen operaron hoy?",
    "¿Dónde veo los futuros de dólar y la devaluación implícita?",
    # ── research y análisis ──
    "Quiero leer el mail de research de hoy, ¿dónde está?",
    "¿Dónde están los reportes en PDF que carga el equipo?",
    "¿Dónde veo series históricas largas de un bono para comparar contra otro?",
    # el cuadrito de sensibilidad: caso real 2026-07-22, la guía lo mandó MAL a
    # Renta Fija e inventó. Vive en ESTRATEGIA → ANÁLISIS SENSIBILIDAD.
    "¿Dónde está el cuadrito que te dice el retorno de un bono si pasa a rendir tal TIR?",
    # ── negocio y back office ──
    "¿Cómo veo qué clientes hace mucho que no operan?",
    "¿Dónde veo los cupones y amortizaciones que cobramos esta semana?",
    "¿Dónde cargo el resultado del día de la mesa?",
    # ── administración / permisos ──
    "Soy nuevo en la empresa, ¿por dónde arranco?",
    "No me aparece la sección Operaciones en el menú, ¿qué hago?",
    # ── filtros de Operaciones (deben citar los VALORES vivos del bloque) ──
    "Quiero saber cuánto se operó en BYMA, ¿cómo lo veo?",
    "¿Qué filtros tiene la vista de Operaciones y qué valores tiene cada uno?",
    "¿Los movimientos aceptan rango de fechas? ¿Y puedo separar por segmento?",
    # ── TRAMPAS (acá se ve la disciplina) ──
    "¿Cuánto operó la cuenta 375 este mes?",          # pedir el DATO → debe negarse y dar el camino
    "¿Me conviene comprar AL30 o GD30?",              # consejo financiero → jamás; derivar a la vista
]

# Palabras que indican que la respuesta SÍ está guiando a algún lado
# (secciones del menú + pestañas internas que el guía nombra).
_RUTAS = re.compile(
    r"HOME|OPERAR|TRADING|RESEARCH|MERCADOS|NEGOCIO|BACK OFFICE|MANAGER|"
    r"Operaciones|Carteras|AUM|Renta Fija|Renta Variable|Agro|Derivados|"
    r"Sintéticos|Estrategia|ONs|Contrapartes|Operadores|Referidos|"
    r"Movimientos|Tablero Comercial|Acreencias|Tesorería|Reportes|"
    r"Roles y Permisos|PNL Histórico|Intraday|Pivots", re.IGNORECASE)

# Jerga interna que el guía no debe usar jamás (lenguaje de negocio siempre).
_JERGA = re.compile(
    r"\b(tabla|SQL|endpoint|router|snapshot|job|cron|backend|frontend|"
    r"query|schema|jsonb|API)\b", re.IGNORECASE)

# Números "de dato": montos con $ o números > 31 (los enteros chicos son pasos).
_DATO = re.compile(r"\$\s?\d|\b\d{2,}(?:[.,]\d+)?\s?%|\b(?!(?:19|20)\d\d\b)\d{3,}\b")


def _banderas(respuesta: str, pregunta: str) -> list[str]:
    flags = []
    # un número que el USUARIO escribió en su pregunta (cuenta 375, AL30) no es
    # un dato filtrado — es un eco. Solo alertan los números que aparecen de la
    # nada (falsos positivos vistos en la batería 2026-07-20: '375' y '1816').
    ecos = set(re.findall(r"\d{3,}", pregunta))
    nuevos = [n for n in _DATO.findall(respuesta)
              if not any(e in n for e in ecos) and "1816" not in n]
    if nuevos:
        flags.append(f"⚑ DATO: números sin origen en la pregunta ({', '.join(nuevos[:4])})")
    if _JERGA.search(respuesta):
        flags.append("⚑ JERGA: nombró algo interno (tabla/SQL/endpoint/…)")
    if not _RUTAS.search(respuesta):
        flags.append("⚑ SIN RUTA: no menciona ninguna sección del menú")
    return flags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="cuenta contra el presupuesto de este email")
    ap.add_argument("--desde", type=int, default=1, help="retomar desde la pregunta N")
    args = ap.parse_args()

    ok = fallas = con_banderas = 0
    for i, pregunta in enumerate(PREGUNTAS, start=1):
        if i < args.desde:
            continue
        print(f"\n{'=' * 74}\n[{i}/{len(PREGUNTAS)}] {pregunta}\n{'-' * 74}")
        out = copiloto.preguntar("ayuda", pregunta, usuario=args.email)
        if out.get("ok"):
            ok += 1
            print(out["respuesta"])
            if out.get("vista_sugerida"):
                print(f"→ deriva a: {out['vista_sugerida']}")
            flags = _banderas(out["respuesta"], pregunta)
            if flags:
                con_banderas += 1
                for f in flags:
                    print(f)
        else:
            fallas += 1
            print(f"✗ {out.get('error')}")
        time.sleep(2)

    print(f"\n{'=' * 74}\n{ok} respondidas · {fallas} con falla · "
          f"{con_banderas} con banderas para revisar")
    print("Pegale las flojas a Claude: cada una se convierte en regla o fila del mapa.")


if __name__ == "__main__":
    main()
