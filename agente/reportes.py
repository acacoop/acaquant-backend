"""`agente/reportes.py` — lo que un job REPORTA sin escribir, declarado.

Varios jobs encuentran cosas que no corrigen a propósito —una moneda que 1816
dice distinta, un conflicto entre dos fuentes, una curva que el cierre salteó—
y hasta el 2026-09-02 lo dejaban en su log y en un contador de
`manager.job_runs`. Un contador dice que hay algo; no dice qué. Y un log no lo
abre nadie. Ver `docs/AGENT.md` §0.dd.

Acá se declara, UNA fila por stat, qué significa que ese número sea mayor que
cero y qué hay que hacer. El job persiste la LISTA al lado del número
(`<stat>_lista`) y la habilidad `job_reporto` convierte cada stat en un aviso
con la lista adentro. Sumar un reporte es una fila; el job solo tiene que
guardar la lista.

Es la REGLA #10 aplicada a los jobs: cada uno inventaba su forma de quejarse.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reporte:
    job: str            # `tipo` en manager.job_runs (lo que JobRunLogger recibe)
    stat: str           # el contador que el job guarda con set_stat
    que: str            # qué significa que sea > 0, en plural y en criollo
    que_hacer: str
    severidad: str = "media"
    regla: str = ""     # default: el stat
    # False = el job solo tiene el número (no hay lista que mostrar): se dice.
    con_lista: bool = True

    @property
    def lista(self) -> str:
        return f"{self.stat}_lista"

    @property
    def nombre_regla(self) -> str:
        return self.regla or self.stat


REPORTES: tuple[Reporte, ...] = (
    Reporte("ficha_1816", "moneda_divergente",
            "bono(s) donde 1816 dice OTRA moneda de denominación que el master",
            "Revisar cada uno en Manager → TÍTULOS: la moneda decide la valuación, "
            "así que el job NO la corrige. Si 1816 tiene razón, corregir el eje "
            "del bono; si no, es un dato de 1816 y se ignora.",
            severidad="alta"),
    Reporte("tamar_1816", "sin_dato",
            "pata(s) TAMAR/dual a las que 1816 no les publica tasa",
            "Nada que apretar: 1816 no cubre esos tickers. Quedan sin TEA ni "
            "margen en la vista; si es uno que importa, pedirle la cobertura a 1816."),
    Reporte("snapshot_cierre", "curvas_salteadas",
            "curva(s) que el cierre diario SALTEÓ (sin universo o sin snapshot)",
            "El cierre de hoy no tiene esas curvas: mirar el log del job y, si el "
            "motor de curvas no escribió, rehacerlo desde `cierre_chain`.",
            severidad="alta"),
    Reporte("cleanup_curvas", "borrados",
            "bono(s) que el cleanup borró del master por estar por vencer",
            "Nada que hacer: es lo esperado. Es para saber QUÉ se fue, que hasta "
            "hoy no quedaba en ningún lado.",
            severidad="baja"),
    Reporte("validar_instrumentos", "simbolos_borrados",
            "símbolo(s) borrados de `mercado.especies` por no existir en Primary",
            "Si alguno debería existir, la foto de Primary estaba vieja ese día: "
            "`foto_primary` lo vigila. Si no, es basura que ya no vuelve."),
    Reporte("validar_instrumentos", "tickers_no_vigentes",
            "ticker(s) cuyos assets están TODOS dados de baja",
            "Nada que apretar: son papeles que amortizaron. Si uno sigue vivo, "
            "destildar VIGENTE en Manager → TÍTULOS · ASSETS (sella `manual`).",
            severidad="baja"),
    Reporte("ops_tasa_mav", "formato_desconocido",
            "boleto(s) cuya `informacion` no matchea '@<tasa>%'",
            "Es un formato nuevo de Aunesa: hay que extender el parseo en "
            "`jobs/ops_tasa_mav.py` con las muestras de la lista."),
    Reporte("assets_autofill", "herencia_divergencias",
            "instrumento(s) con dos unidades que NO se ponen de acuerdo en un campo",
            "El único caso que pide mano humana: abrir las dos unidades en Manager → "
            "TÍTULOS · ASSETS y dejar el valor correcto en las dos.",
            severidad="alta"),
    *[Reporte("assets_autofill", f"{regla}_conflictos",
              f"conflicto(s) de la regla «{regla}»: el catálogo ya tiene un valor "
              "distinto al que la regla deduce, o dos reglas proponen distinto",
              "El job no pisa nada (nunca lo hace). Cada conflicto es un asset a "
              "revisar a mano en Manager → TÍTULOS · ASSETS; el detalle dice qué "
              "dice cada lado.")
      for regla in ("financiamiento", "financiamiento_clase", "fci", "emisor_derivados",
                    "derivados_otc", "ticker", "especies", "herencia")],
    Reporte("saldos_a_operadores", "sin_operador",
            "cuenta(s) con saldo que no tienen operador asignado: nadie recibió su aviso",
            "Asignarles operador en Manager → CLIENTES (SIN OPERADOR). Hasta entonces "
            "esos saldos no le llegan a nadie.", con_lista=False),
    Reporte("sync_comitentes", "saltadas_sin_id",
            "comitente(s) que Aunesa mandó sin id y no entraron",
            "Es un dato roto en origen: revisar en Aunesa los comitentes sin código.",
            con_lista=False),
)
