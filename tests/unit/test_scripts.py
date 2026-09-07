"""`scripts/` no puede volver a crecer en silencio.

## El problema, dicho sin vueltas

**Para un script que corre una PERSONA, el repo no puede probar que se usa.** Que
nadie lo importe es lo NORMAL, no la señal — y por eso durante meses la única
defensa fue que alguien se acordara de borrar. Nadie se acuerda: cerrar un tema y
volver al archivo son dos actos distintos, y el segundo no tiene quien lo pida.
Medido el 2026-08-28: **143 scripts, 87 de ellos `diag_*`**.

Hubo un `scripts/diag_scripts_muertos.py` que estimaba esto con heurísticas y él
mismo admitía que «no puede decidir solo». Se borró: un diagnóstico que hay que
acordarse de correr tiene el mismo problema que el que quería resolver.

## La regla, que sí se puede chequear

Un script vive en `scripts/` si cumple **al menos una**:

  1. **Algo AUTOMÁTICO lo corre** — el crontab, la CI, un hook o una skill de
     `.claude/`, o un test lo importa.
  2. **El CÓDIGO manda a correrlo** — un mensaje de error o un docstring que le
     dice al operador «correr `python -m scripts.x`». Si el script no está, el
     que lee ese mensaje queda sin salida.
  3. **ESCRIBE** — altas, cargas, siembras, migraciones, exports, DDL. Eso no es
     un diagnóstico: es la superficie operativa del sistema.
  4. **Está declarado abajo con un motivo en una línea.**

Lo que no cumple ninguna es un `diag_*` read-only que nadie corre. Se borra: git
lo tiene, y un diag se reescribe en diez minutos **con el contexto de hoy**, que
es mejor que uno de hace tres meses con el contexto de entonces.

## Por qué la allowlist es una lista escrita a mano y no otra heurística

Las tres primeras condiciones las mide la máquina. La cuarta es el lugar donde
una persona dice «este me sirve **por esto**», y el costo de escribir el motivo
es exactamente el filtro: si no se puede escribir en una línea por qué existe,
no existe. La lista completa entra en una pantalla y se revisa de un vistazo —
que es lo que ninguna carpeta de 143 archivos permite.
"""
from __future__ import annotations

import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "scripts"

# ── (4) LOS DECLARADOS. Read-only, nadie automático los corre, y se quedan
#        igual. El motivo es obligatorio: es el filtro.
HERRAMIENTAS: dict[str, str] = {
    # feeds y superficies que no tienen otro productor
    "eikon_feed_simple":     "EL productor del feed Eikon: corre en la PC de la oficina con Workspace "
                             "abierto y alimenta /api/ingest/eikon/*. Sin esto la tab REUTERS no tiene datos.",
    "ext_ver":               "inspector de la API externa (/ext): qué ve un accionista con SU token.",
    # contratos y auditoría (dan un veredicto, no una opinión)
    "eval_investigador":     "¿acierta el INVESTIGADOR? Compara lo que propuso contra el arreglo que "
                             "cerró el hallazgo de verdad. Determinista a propósito (invariante #12: "
                             "el agente no se autoevalúa). Sin este número no se puede decidir si "
                             "darle más autonomía.",
    "audit_superficie_http": "inventario de la superficie HTTP por categoría — la contracara de audit_rbac.",
    "check_proxies_next":    "cruza los routers del backend contra los proxies de Next: un handler que falta "
                             "deja el panel vacío EN SILENCIO. Tiene --strict; debería estar en CI.",
    "diag_entorno":          "instalado vs. pineado en requirements.txt. Un drift acá no falla: cambia el "
                             "comportamiento y nadie lo ve.",
    "agente_umbral":         "cambia un umbral del agente sin deploy (`agente.habilidades.umbrales` "
                             "pisa al código). Ajustar cuán sensible es un detector no es programar, "
                             "y hasta que existió esto la única forma era SQL a mano — así que "
                             "«editable en caliente» era una frase. Rechaza una clave que el código "
                             "no declara: escribirla no haría nada y nadie se enteraría.",
    # ⚠️ TEMPORAL — vive mientras dure el trabajo de `sin_emisor`, y se BORRA en
    # el commit que lo cierre (REGLA #5). Se declara acá y no se deja colgando
    # porque el motivo se puede escribir; que se pueda escribir CUÁNDO muere es
    # parte del motivo.
    "diag_emisor":           "mide de dónde puede salir cada `emisor` que falta ANTES de "
                             "automatizarlo: cuánto ya está en 1816 y no llegó (plomería), cuánto "
                             "sale de una regla, y cuánto necesita de verdad un modelo. Sin estos "
                             "números, meter un LLM sería pagarle para tapar un join roto. Se borra "
                             "cuando la habilidad esté hecha.",
    # ⚠️ TEMPORAL — se BORRA cuando el tema de las ONs cierre.
    "diag_agente_corriendo": "separa las cuatro causas de «deployé y sigue igual»: repo viejo, "
                             "daemon viejo, todavía no le tocó, o el código no hace lo que "
                             "creíamos. Compara la huella de las reglas (`no_esta_en_curvas` = "
                             "una fila por ON = código viejo) contra lo que produce el checkout.",
    "diag_redaccion":        "la ÚNICA forma de juzgar si el texto que el modelo le escribe a los "
                             "avisos (AGENT.md §0.di) sirve o es berreta: pone el piso determinista "
                             "y el texto del modelo uno debajo del otro, y agrupa lo que la "
                             "validación rechazó por MOTIVO. Un test congela que el mecanismo no "
                             "pueda hacer daño; si el texto informa o no lo dice una persona "
                             "leyéndolo, y para eso hay que poder verlo.",
    "diag_caducidad":        "el ANTES de la caducidad del agente (AGENT.md §6.8): a qué hallazgos "
                             "abiertos les pegaría, con qué fundamento y de qué fuente, sin escribir "
                             "una fila. Un cierre automático que no se puede mirar antes es fe, no "
                             "ingeniería (REGLA #2). Se borra cuando el tema cierre (REGLA #5).",
    "diag_tea_corp_hd":      "las TNA/TEA de los CORPORATIVOS HARD DOLAR, nuestras contra las de "
                             "1816, una fila por ticker. Hoy NINGUNA habilidad del agente compara el "
                             "VALOR de una tasa contra nadie (la que lo hacía, `tasa_sospechosa`, se "
                             "borró por ruidosa): las dos que tocan `tea` preguntan `is not None`. "
                             "Hasta que exista esa habilidad, esta es la única forma de mirarlo, y "
                             "pide con `moneda=mep` porque el default de 1816 divide por CCL y "
                             "nuestro motor por MEP. Se borra cuando el tema cierre (REGLA #5).",
    "diag_contabilidad_cierre": "contesta «¿por qué el informe de CONTABILIDAD no ve una tenencia que SÍ "
                             "está en la tabla?». Mide las tres puertas por las que una fila se cae "
                             "(la fecha del cierre, el filtro de cartera, el cruce unidad→key) en vez "
                             "de que haya que adivinar cuál fue.",
    # monitoreo de integraciones vivas
    "healthcheck_sql":       "ejecuta el reader REAL de cada dominio contra Postgres. Es el smoke de después "
                             "de un deploy grande o un cambio de schema.",
    "diag_interbanking":     "smoke de las 5 APIs de Interbanking con datos reales. Distinto de «respondió», "
                             "que es lo único que mira el detector `proveedor_caido`.",
    "diag_ingesta":          "cuánto trajo cada job de ingesta, corrida por corrida, con la mediana: el "
                             "dato que decide `ingesta_encogida` antes de escribirla (REGLA #2).",
    "diag_postrade_auth":    "el agente vigila que Postrade RESPONDA (`proveedor_caido`); esto es lo único "
                             "que contesta «¿entramos con ESTA credencial?» con datos reales.",
    "diag_postrade_metodos": "qué métodos de Postrade nos habilitaron. Cambia cuando ACyRSA toca permisos, "
                             "y el reclamo se hace con esta salida.",
    # performance (REGLA #5 los llama recurrentes)
    "diag_mayor_mapeo":      "lo nombra `diag_mayor_estado` (que SÍ corre por cron) en su salida al "
                             "operador: «a otra cuenta: python -m scripts.diag_mayor_mapeo lo separa».",
    "diag_costo_real":       "dónde se va el tiempo, cruzando latencia de endpoints con el costo dentro de "
                             "Postgres. La mitad de BASE no la cubre ningún detector.",
    "profile_motor":         "perfila un motor always-on en vivo con py-spy SIN reiniciarlo — reiniciar en "
                             "rueda corta el feed de precios de la mesa.",
    # build
    "lock_deps":             "repinea requirements.txt fotografiando el venv que YA corre en prod "
                             "(no resuelve contra PyPI, que pinearía a la última). Es lo que evita que un "
                             "`pip install` traiga una versión nueva y tumbe el boot en el restart.",
    "sellar_cierres":        "siembra el histórico de cierres de Interbanking (one-shot re-corrible).",
    # ⏳ TEMPORAL — se borra (script + este renglón) en el mismo commit que aplique
    #    la decisión, según REGLA #5. Está declarado y no escondido justamente
    #    para que su borrado sea una tarea visible y no un olvido.
    "diag_tna":              "contesta «¿la TNA de renta fija está viva o es la misma de siempre?» "
                             "con la serie de cierre: cuántas ruedas el bono no movió su TNA, qué día "
                             "saltó y hace cuánto que el motor no la recalcula. La pantalla no puede "
                             "distinguir una tasa de hace tres semanas de una de hace tres segundos "
                             "(2026-09-02, abierto).",
    "diag_tasa_fija":        "audita la pill TASA FIJA: recalcula cada bono con la función del "
                             "motor y lo cruza contra 1816 por ticker, SEPARANDO las tres causas "
                             "posibles de una diferencia (el número guardado quedó viejo · el precio "
                             "de arranque es otro · la convención de TNA no es la misma). Sin esa "
                             "separación no se puede decidir si hay que tocar el motor "
                             "(2026-09-02, abierto).",
    "diag_congelamiento":    "«la app se congela y con F5 anda» separado en las TRES causas que se "
                             "ven iguales desde la silla del que la usa y se arreglan en lugares "
                             "distintos: la API se puso lenta · le PIDEN más (un poll nuevo no sale "
                             "lento: hace que salga lento todo lo demás) · el NAVEGADOR se clavó. "
                             "Elegir entre las tres sin esto es una corazonada (2026-09-04, abierto).",
    "diag_duales_pata_fija": "por qué TTD26/TTS26 salen en -- en TASA FIJA teniendo TAMAR completo: sigue "
                             "la cadena hasta el punto exacto donde se corta (2026-08-31, abierto).",
    "diag_1816_grafias":     "con qué grafía publica 1816 la pata fija que `tamar_1816` pide con dos "
                             "grafías y no obtiene; primero el catálogo (gratis), después la API (cuesta).",
    "diag_saldo_cierre":     "los DOS saldos que informa Interbanking (extracto vs. API de Saldos) puestos "
                             "uno al lado del otro, con la verificación aritmética del extracto. Es lo único "
                             "que distingue «el banco se contradice» de «estoy comparando el cierre contra el "
                             "saldo operativo», que se ven igual en pantalla: un badge ≠.",
    "diag_desglose_texto":   "«¿mi grafía del DESGLOSE agarró algo?» — una que no agarra deja la columna "
                             "en cero y el total dando bien, así que la pantalla no la distingue de «hoy no "
                             "hubo ese impuesto». Muestra el catálogo con `repr` (espacios y acentos rotos), "
                             "dónde vive un texto de verdad y qué gastos caen en RESTO.",
    "diag_monitor_tape":     "el número que decide la ventana de la tab MONITOR: cuántos ticks hay "
                             "por rueda en los dos tapes y cuánto tardan las DOS queries agregadas "
                             "que la tab va a pedir. Elegir 1 rueda o 5 sin medir esto es adivinar "
                             "(2026-09-04, abierto).",
    "diag_ctas_ops":         "mide cuánto cambia CTAS OPS del INFORME si el número pasa a salir de "
                             "operaciones.operaciones (para que las cuentas OTC cuenten como operativas). Sin este número la decisión se toma a ciegas.",
}

_RE_ESCRIBE = re.compile(
    r"\bINSERT\s+INTO\b|\bUPDATE\s+\w|\bDELETE\s+FROM\b|\bCREATE\s+(?:TABLE|SCHEMA|INDEX)\b|"
    r"\bDROP\s+(?:TABLE|SCHEMA)\b|\bALTER\s+TABLE\b|\bTRUNCATE\b|\bVACUUM\b|"
    r"write_native|append_native|write_hist|replace_native|merge_jsonb_native|"
    r"\.to_excel\(|\.save\(|open\([^)]*[\"']w", re.I)
_RE_MANDA = re.compile(r"correr|refrescarlo|sembrar|siembra|chequeo previo|python -m|lo separa", re.I)


def _nombres() -> set[str]:
    return {p.stem for p in SCRIPTS.glob("*.py") if p.stem != "__init__"}


def _corridos_por_algo_automatico() -> set[str]:
    """crontab · CI · deploy.sh · hooks y skills de .claude · tests."""
    out: set[str] = set()
    fuentes = [RAIZ / f for f in ("deploy/crontab.txt", ".github/workflows/ci.yml", "deploy/deploy.sh")]
    fuentes += [p for p in (RAIZ / ".claude").rglob("*") if p.is_file()]
    # ⚠️ **ESTE ARCHIVO SE EXCLUYE, y no es un detalle.** La allowlist de abajo
    # nombra scripts (`scripts.diag_mayor_mapeo` adentro de un motivo), así que
    # sin esta línea el test se leía a sí mismo como evidencia: cada entrada
    # declarada quedaba además "corrida por algo automático", y la condición ①
    # pasaba a ser cierta por el solo hecho de estar en la lista. Un chequeo que
    # se cita a sí mismo no chequea nada.
    fuentes += [p for p in (RAIZ / "tests").rglob("*.py")
                if "__pycache__" not in p.parts and p.name != "test_scripts.py"]
    for p in fuentes:
        if not p.exists():
            continue
        out |= set(re.findall(r"scripts[./](\w+)", p.read_text(encoding="utf-8", errors="ignore")))
    return out


def _mandados_por_el_codigo() -> set[str]:
    """Un mensaje de error o un docstring que le dice al operador que lo corra."""
    out: set[str] = set()
    for d in ("api", "core", "jobs", "engines", "agente"):
        for p in (RAIZ / d).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            for linea in p.read_text(encoding="utf-8", errors="ignore").split("\n"):
                if _RE_MANDA.search(linea):
                    out |= set(re.findall(r"scripts[./](\w+)", linea))
    return out


def _escriben() -> set[str]:
    return {p.stem for p in SCRIPTS.glob("*.py")
            if p.stem != "__init__" and _RE_ESCRIBE.search(p.read_text(encoding="utf-8", errors="ignore"))}


def test_todo_script_justifica_su_lugar():
    """La regla. Un `diag_*` nuevo que nadie corre falla acá, no dentro de un año."""
    justificados = (_corridos_por_algo_automatico() | _mandados_por_el_codigo()
                    | _escriben() | set(HERRAMIENTAS))
    sobran = sorted(_nombres() - justificados)
    assert not sobran, (
        "estos scripts no cumplen NINGUNA de las cuatro condiciones:\n  "
        + "\n  ".join(sobran)
        + "\n\nO algo los corre, o el código los nombra, o escriben, o se declaran en "
          "HERRAMIENTAS de este archivo con un motivo de una línea. Si no se puede "
          "escribir el motivo, no hay motivo: borralos (git los conserva).")


def test_la_allowlist_no_tiene_fantasmas():
    """Una lista que nombra archivos que no existen es la misma basura que
    denuncia — y encima hace creer que hay una herramienta que no está."""
    fantasmas = sorted(set(HERRAMIENTAS) - _nombres())
    assert not fantasmas, (
        f"HERRAMIENTAS declara scripts que ya no existen: {fantasmas}. "
        "Al borrar un script, sacá también su renglón.")


def test_todo_declarado_explica_por_que():
    """El motivo es el filtro, así que tiene que ser un motivo y no un rótulo."""
    flojos = [n for n, m in HERRAMIENTAS.items() if len(m.strip()) < 40]
    assert not flojos, (
        f"estos declaran un motivo demasiado corto para ser uno: {flojos}")


def test_el_barrido_mira_algo():
    """Los tres de arriba pasan en verde con la carpeta vacía o la ruta cambiada:
    el mismo «no pude» disfrazado de «está todo bien»."""
    assert len(_nombres()) > 20, f"solo {len(_nombres())} scripts: ¿cambió la ruta?"
    assert _corridos_por_algo_automatico(), "cero scripts corridos por cron/CI: ¿cambió el formato?"
    assert _escriben(), "cero scripts que escriben: ¿cambió el detector?"
