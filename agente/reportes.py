"""`agente/reportes.py` — lo que un job REPORTA sin escribir, declarado.

Varios jobs encuentran cosas que no corrigen a propósito —una moneda que 1816
dice distinta, un conflicto entre dos fuentes, una curva que el cierre salteó—
y hasta el 2026-09-02 lo dejaban en su log y en un contador de
`manager.job_runs`. Un contador dice que hay algo; no dice qué. Y un log no lo
abre nadie. Ver `docs/AGENT.md` §0.dd, y §0.di para los contadores de NEGOCIO
(boletos, bancos, cámara) que se sumaron después de medir cuáles morían en el log.

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
            "Si alguna de esas patas importa, pedirle la cobertura a 1816: hasta "
            "entonces quedan sin TEA ni margen en la vista."),
    Reporte("snapshot_cierre", "curvas_salteadas",
            "curva(s) que el cierre diario SALTEÓ (sin universo o sin snapshot)",
            "El cierre de hoy no tiene esas curvas: mirar el log del job y, si el "
            "motor de curvas no escribió, rehacerlo desde `cierre_chain`.",
            severidad="alta"),
    # ⚠️ **NO HAY FILA PARA `cleanup_curvas · borrados`, Y NO ES UN OLVIDO**
    # (§0.eu). Su `que_hacer` decía «Nada que hacer: es lo esperado» — o sea,
    # declaraba por escrito que no era un problema y aun así ocupaba un renglón
    # de AHORA todos los días. El agente avisa de lo NUESTRO cuando algo está
    # mal; un bono que el cleanup borra porque vence es el sistema funcionando.
    #
    # **No se pierde el dato**: el job guarda igual el número y la lista en
    # `manager.job_runs` (`borrados` / `borrados_lista`), que es donde vivía
    # antes de existir esta tabla. Lo que se saca es el AVISO, no el registro.
    Reporte("validar_instrumentos", "simbolos_borrados",
            "símbolo(s) borrados de `mercado.especies` por no existir en Primary",
            "Si alguno debería existir, la foto de Primary estaba vieja ese día: "
            "`foto_primary` lo vigila. Si no, es basura que ya no vuelve."),
    Reporte("validar_instrumentos", "tickers_no_vigentes",
            "ticker(s) cuyos assets están TODOS dados de baja",
            "Repasar la lista: si alguno sigue vivo, destildar VIGENTE en "
            "Manager → TÍTULOS · ASSETS (sella `manual`). El resto amortizaron.",
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
                    "emisor_financiamiento", "derivados_otc", "ticker", "especies",
                    "herencia")],
    Reporte("saldos_a_operadores", "sin_operador",
            "cuenta(s) con saldo que no tienen operador asignado: nadie recibió su aviso",
            "Asignarles operador en Manager → CLIENTES (SIN OPERADOR). Hasta entonces "
            "esos saldos no le llegan a nadie.", con_lista=False),
    Reporte("sync_comitentes", "saltadas_sin_id",
            "comitente(s) que Aunesa mandó sin id y no entraron",
            "Es un dato roto en origen: revisar en Aunesa los comitentes sin código.",
            con_lista=False),

    # ── Los contadores que morían en el log (§0.di) ────────────────────────
    # Boletos: la anulación que el tope dejó a medias. Es el hallazgo más caro
    # que se perdía: MOVIMIENTOS muestra vivos boletos que Aunesa ya anuló.
    Reporte("operaciones_informes", "anulacion_incompleta",
            "tope(s) que frenaron la anulación de boletos que Aunesa dejó de devolver "
            "(respuesta parcial probable): esos boletos siguen VIVOS en la base",
            "Volver a correr `jobs.operaciones_informes` fuera de hora pico; si el tope "
            "vuelve a saltar, Aunesa está devolviendo parcial de verdad y hay que mirar "
            "cuenta por cuenta en Manager → OPERACIONES antes de anular.",
            severidad="alta"),
    Reporte("operaciones_informes", "cuentas_fallidas",
            "cuenta(s) que la API de informes de Aunesa no contestó (timeout/error): "
            "sus boletos de hoy NO entraron",
            "Se completan solos en la próxima corrida horaria. Si la misma cuenta falla "
            "varias veces seguidas, es de Aunesa: reclamar con el número de cuenta."),
    Reporte("negocio_movimientos", "anulacion_abortada",
            "día(s) donde el tope frenó la anulación de boletos del consolidado: "
            "quedaron vivos boletos que Aunesa ya no devuelve",
            "Volver a correr `jobs.negocio_movimientos --fecha <día>`; si vuelve a "
            "abortar, revisar ese día en /operaciones/negocio contra Aunesa a mano.",
            severidad="alta"),
    # Bancos: lo que interbanking_sync y mayor_sync ya miden y nadie leía.
    Reporte("interbanking_sync", "dias_incoherentes",
            "día(s) de banco donde lo guardado NO coincide con el total que declara "
            "el extracto: el consolidado de ese día está incompleto",
            "Volver a correr `jobs.interbanking_sync --dias 2` para esa ventana. Si "
            "persiste, el extracto de Interbanking trae movimientos con el mismo hash "
            "y hay que mirarlo en BACK OFFICE → Interbanking → ese día.",
            severidad="alta"),
    Reporte("interbanking_sync", "cuentas_error",
            "cuenta(s) bancaria(s) que Interbanking no contestó: sin extracto de hoy",
            "Se reintenta en la próxima corrida (cada 2 h). Si la misma cuenta falla "
            "todo el día, es del banco o del abonado: reclamar a Interbanking."),
    Reporte("mayor_sync", "no_aplicado",
            "corrida(s) donde el MAYOR contable no se aplicó: el día tenía movimientos y "
            "Aunesa devolvió 0, así que se conservó el de ayer",
            "Si Aunesa anuló todo de verdad, correr `jobs.mayor_sync --forzar`. Si no, "
            "esperar la próxima corrida: la conciliación banco↔mayor de hoy está usando "
            "un mayor viejo hasta entonces.",
            severidad="alta"),
    Reporte("mayor_sync", "cuentas_sin_mapear",
            "cuenta(s) contable(s) del mayor que no están mapeadas a un banco: sus "
            "movimientos quedan AFUERA de la conciliación",
            "Cargar el `codigo_contable` de cada una en BACK OFFICE → Interbanking → "
            "cuentas. No se deduce del nombre: hay que saber qué banco es."),
    Reporte("mayor_sync", "duplicados",
            "movimientoID repetido(s) en la respuesta de Aunesa: se guardó uno y se "
            "descartó el resto",
            "Es un dato de origen: si el saldo del mayor no cuadra ese día, el "
            "duplicado es el primer sospechoso. Reclamar a Aunesa con los ids."),
    # Cámara: la fila que el UPSERT elige y la que pierde.
    Reporte("ap5_portfolio", "claves_divergentes",
            "clave(s) de posición de futuros con filas DISTINTAS en el PositionReport: "
            "se guardó una y se perdió la otra",
            "Comparar las filas divergentes contra la cámara en /operaciones → "
            "POSICIONES Y DIFERENCIAS y corregir el ACTIVO INTEGRADO a mano si la "
            "posición guardada no es la real.",
            severidad="alta"),
    # Lo que queda sin resolver y solo tiene número (el job no guarda lista).
    # ⚠️ `aranceles.sin_match` NO está a propósito: medido el 2026-09-02, son
    # 2.530 de 3.187 informes (80%) todos los días — es la base, no una
    # anomalía, y un aviso por hora con ese número enseña a ignorar AHORA.
    Reporte("ops_tasa_mav", "sin_texto",
            "boleto(s) MAV cuyo movimiento no trae `informacion`: no hay de dónde "
            "sacar la tasa",
            "Falta el dato en origen: cargar la tasa a mano en Manager → OPERACIONES "
            "o reclamar a Aunesa el campo.",
            severidad="baja", con_lista=False),
    # El amarillo permanente de precios_acciones_daily (§0.dk): `partial` todos
    # los días desde el 17/08 por UN ticker que falla siempre. Con la lista, el
    # color se vuelve un aviso concreto.
    Reporte("precios_acciones_daily", "errores",
            "ticker(s) cuyas velas Yahoo no devolvió: el scanner los muestra con el "
            "precio de ayer",
            "Si es siempre el mismo ticker, Yahoo cambió su símbolo o lo deslistó: "
            "corregir el RIC/ticker en Manager → RENTA VARIABLE o sacarlo del universo. "
            "Mientras tanto el job queda `partial` todos los días.",
            severidad="baja"),
    Reporte("ops_tasa_mav", "ambiguos",
            "boleto(s) MAV con VARIAS tasas distintas en su información: se dejan sin "
            "tasa a propósito",
            "Elegir la tasa correcta a mano en Manager → OPERACIONES; el job no "
            "adivina entre dos.",
            con_lista=False),
)


# ── LO QUE CADA JOB DE INGESTA TRAE, y de qué forma (§0.dk) ────────────────
#
# `trajo_poco` compara el contador de la última corrida contra lo que el job
# venía trayendo. Hay dos formas de traer, y no se miden igual — salió de
# medir 12 corridas de cada uno (scripts/diag_ingesta, 2026-09-02):
#
#   diario     una corrida por día (o pocas) y el número es estable: se
#              compara contra la MEDIANA de sus últimas corridas ok.
#   acumulado  corre varias veces por día y el número CRECE durante el día
#              (ventana de dos días, Contabilidad cargando, boletos entrando):
#              se compara contra la corrida ANTERIOR del mismo día. La primera
#              del día no opina: un lunes trae menos que un viernes a la tarde
#              y eso es legítimo.
@dataclass(frozen=True)
class Volumen:
    job: str
    stat: str
    modo: str           # diario | acumulado
    que: str            # qué es ese número, en criollo


VOLUMENES: tuple[Volumen, ...] = (
    Volumen("aum", "cuentas_ok", "diario", "cuentas con tenencia que Aunesa contestó"),
    Volumen("sync_comitentes", "recibidas", "diario", "comitentes que Aunesa devolvió"),
    Volumen("ap5_portfolio", "filas_crudas", "diario", "posiciones de futuros de la cámara"),
    Volumen("snapshot_cierre", "docs", "diario", "bonos con cierre"),
    Volumen("mercado_1816_discovery", "catalogo", "diario", "instrumentos del catálogo de 1816"),
    Volumen("operaciones_informes", "filas", "acumulado", "boletos de los informes de Aunesa"),
    Volumen("negocio_movimientos", "boletos", "acumulado", "boletos del consolidado de Aunesa"),
    Volumen("interbanking_sync", "movimientos", "acumulado", "movimientos de los extractos"),
    Volumen("mayor_sync", "movimientos_banco", "acumulado", "movimientos del mayor contable"),
    Volumen("aranceles", "informes_obtenidos", "acumulado", "informes de aranceles de Aunesa"),
)

