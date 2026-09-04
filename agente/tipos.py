"""`agente/tipos.py` — el vocabulario. No importa NADA del proyecto.

Es una tabla de datos: la pueden leer el catálogo, los detectores y la vista sin
abrir un ciclo de imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── LOS ESTADOS DE UN HALLAZGO ─────────────────────────────────────────────
#
# CINCO, y cada uno se atiende distinto. Un estado de más es una rama de más en
# cada pantalla, para siempre. Se sacó `visto` del modelo viejo: "alguien lo
# miró y no hizo nada" no se atiende distinto de `nuevo`.
NUEVO = "nuevo"          # apareció y nadie lo tocó
EN_CURSO = "en_curso"    # se aplicó el arreglo y falta que el detector confirme
RESUELTO = "resuelto"    # ya no está
IGNORADO = "ignorado"    # una persona dijo «no me interesa» — reversible
REINCIDIO = "reincidio"  # estaba resuelto POR ACCIÓN y volvió

ESTADOS = (NUEVO, EN_CURSO, RESUELTO, IGNORADO, REINCIDIO)
# ⚠️ `reincidio` ES abierto (2026-09-01, §0.cy). Sin él acá, un hallazgo que
# volvió no lo cerraba nadie (`_cerrar_ausentes` mira ABIERTOS), no lo actualizaba
# nadie (`_ver` también) —así que la próxima vez que se lo viera nacía OTRA fila
# en `reincidencias`— y no aparecía en ENCONTRÓ. M31G6 quedó así desde el 28/08:
# «abierto» para siempre y visible en ninguna pantalla.
ABIERTOS = (NUEVO, EN_CURSO, REINCIDIO)

# ── CÓMO SE CERRÓ. Son DOS y NO significan lo mismo ────────────────────────
#
# ⚠️ Esta distinción es la que sostiene a `reincidencias`. Sin ella, un bono que
# no operó esa noche se auto-resuelve, vuelve mañana, y la tabla que debería
# estar vacía se llena de ruido hasta que nadie la mira.
POR_ACCION = "accion"      # se apretó el arreglo Y DESPUÉS el detector no lo vio
POR_AUSENCIA = "ausencia"  # el detector no lo vio, y nada más
# ⚠️ **CADUCIDAD ≠ AUSENCIA.** «No lo vi» es una observación; «el sujeto dejó de
# existir» es un HECHO VERIFICADO, con fuente y fecha. La diferencia importa por
# dos motivos, y ninguno es cosmético:
#
#   · Un hallazgo caducado **no puede reincidir**: «volver» no significa nada
#     sobre un bono que venció. Sin este tercer valor, el cierre caía en ACCIÓN
#     (si alguien había apretado el arreglo antes) y el sujeto muerto quedaba
#     habilitado a generar una reincidencia — que es literalmente cómo nació la
#     primera fila de esa tabla (M31G6, 28/08).
#   · Y le da un LUGAR a la respuesta «esto ya no aplica». Antes la única salida
#     era que una persona apretara «no me interesa», que es falso (nadie está
#     desinteresado) y además pierde el motivo para siempre.
#
# Es un CIERRE y no un sexto estado a propósito: el hallazgo queda `resuelto`
# —que es verdad, ya no está—, ninguna pantalla suma una rama, y hereda solo la
# regla que importa (no reincide). Ver `docs/AGENT.md` §8, invariante 4.
POR_CADUCIDAD = "caducidad"
CIERRES = (POR_ACCION, POR_AUSENCIA, POR_CADUCIDAD)

# ── RESULTADO DE UNA CORRIDA ───────────────────────────────────────────────
#
# ⚠️ `SIN_DATOS` NO es `OK` con la lista vacía: es «no pude mirar». Solo `OK`
# habilita cerrar por ausencia. Ver `registro.guardar`.
OK, SIN_DATOS, ERROR = "ok", "sin_datos", "error"

SEVERIDADES = ("alta", "media", "baja")
DOMINIOS = ("MERCADO", "SISTEMA", "DATOS", "SEGURIDAD")
# CUÁNDO tiene sentido mirar. El horario vive en `agente/reloj.py` y acá solo
# se lo nombra: `cierre` es «una vez, con el día cerrado», y es lo que hace que
# nada le siga pidiendo al mercado después de las 17.
VENTANAS = ("rueda", "cierre", "habil", "siempre")
TIPOS = ("detector", "consulta", "accion")

# DE QUÉ COSA HABLA EL SUJETO de una habilidad. Es lo que hace que la caducidad
# sea general y no un parche por detector: el motor no sabe qué es un bono, sabe
# preguntarle a `agente/vigencia.py` por el TIPO declarado acá.
#
# Vacío ("") es una declaración explícita y válida: «mi sujeto no es una cosa
# que pueda dejar de existir» (una vista, un campo, una prueba). No es un olvido
# — sin declararlo, la habilidad no caduca nada, que es el default seguro.
#
# ⚠️ Solo entran los tipos que tienen **partida de defunción verificable**. Sumar
# uno acá sin una fuente que afirme «esto ya no existe» haría que el agente
# cerrara hallazgos por no encontrar el sujeto, y NO ENCONTRARLO NO ES PRUEBA DE
# NADA — es el invariante 1 disfrazado de feature nueva.
SUJETOS = ("bono",)


class SinDatos(Exception):
    """«No pude mirar». La levanta un detector que no pudo leer su fuente.

    Es distinta de devolver `[]`: un detector que devuelve vacío está afirmando
    que no hay nada, y con eso el agente CIERRA los problemas que no vinieron.
    Levantar esto dice «no sé» — y entonces no se cierra nada.
    """


@dataclass(frozen=True)
class Hallazgo:
    """Lo que una habilidad vio, en un momento.

    ⚠️ `que_hacer` es obligatorio y la base lo exige con un CHECK. **Si no se
    puede decir qué hacer, la regla está mal pensada** — una fila que solo dice
    «esto está mal» le pasa el problema entero al que la lee.
    """

    sujeto: str
    regla: str
    severidad: str
    problema: str
    que_hacer: str
    nombre: str = ""
    # ⚠️ **EL ERROR CRUDO, TAL CUAL.** User (2026-08-25): *«debería verse el
    # código del error real; con eso alcanza para darme cuenta de quién es el
    # error. El `que_hacer` está de más — es mejor mostrar la evidencia»*.
    #
    # Y tenía razón: «se arregla del otro lado, verificar si es de ellos o si se
    # nos venció una credencial» es la MISMA frase para AUNESA, 1816, BCRA e
    # Interbanking, escrita a mano en el detector. No sale de ningún dato. Un
    # texto que no cambia con el caso no informa: entrena a saltearlo.
    #
    # El error de verdad ya se calculaba y quedaba enterrado en `evidencia`, que
    # la pantalla ni lee. Acá es un campo propio, y por eso se muestra.
    detalle: str = ""
    evidencia: dict = field(default_factory=dict)

    def __post_init__(self):
        if not str(self.sujeto).strip():
            raise ValueError("un hallazgo sin sujeto no se le puede adjudicar a nada")
        if not str(self.regla).strip():
            raise ValueError(f"«{self.sujeto}» sin regla: la identidad del "
                             "problema es habilidad+sujeto+regla")
        if not str(self.que_hacer).strip():
            raise ValueError(f"«{self.sujeto}/{self.regla}» sin `que_hacer`")
        if self.severidad not in SEVERIDADES:
            raise ValueError(f"severidad «{self.severidad}» no existe")


@dataclass(frozen=True)
class Habilidad:
    """UNA cosa que el agente sabe hacer, con todo lo que hay que saber de ella.

    ⚠️ **`arreglo` se declara POR REGLA, no por habilidad.** Una habilidad puede
    tener reglas de las dos clases: en `precio_moneda`, `pata_equivocada` se
    arregla con un botón y `cotiza_en_pesos` es contexto. Colgar el arreglo de la
    habilidad obligaría a elegir mal para una de las dos.

    De ahí sale la CLASE, que nadie escribe:
        con arreglo → `trabajo` → AHORA (hoy) + ENCONTRÓ (hasta arreglarse)
        sin arreglo → `aviso`   → AHORA y nada más
    """

    nombre: str
    tipo: str
    dominio: str
    que_mira: str
    cada_segundos: int
    correr: object                       # callable(umbrales) -> list[Hallazgo]
    ventana: str = "siempre"
    usa_ia: bool = False
    umbrales: dict = field(default_factory=dict)
    arreglos: dict = field(default_factory=dict)   # regla -> id del arreglo
    # DE QUÉ TIPO es su sujeto (`SUJETOS`), o "" si no es una cosa que pueda
    # dejar de existir. Es lo único que hay que agregar para que una habilidad
    # sepa caducar — sin tocar el motor, ni el registro, ni una lista aparte.
    sujeto_es: str = ""

    def __post_init__(self):
        for campo, validos in (("tipo", TIPOS), ("dominio", DOMINIOS),
                               ("ventana", VENTANAS)):
            if getattr(self, campo) not in validos:
                raise ValueError(f"«{self.nombre}»: {campo}="
                                 f"{getattr(self, campo)!r} no es uno de {validos}")
        if not str(self.que_mira).strip():
            raise ValueError(f"«{self.nombre}» no dice qué mira: la tab de "
                             "habilidades lo mostraría vacío")
        if self.cada_segundos < 30:
            raise ValueError(f"«{self.nombre}»: {self.cada_segundos}s es un "
                             "ritmo que ningún detector necesita")
        # Un tipo de sujeto inventado no puede fallar en silencio: `vigencia` no
        # lo encontraría en su registro, no verificaría nada, y la habilidad
        # nunca caducaría — sin un error, sin un log, sin nada que mirar.
        if self.sujeto_es and self.sujeto_es not in SUJETOS:
            raise ValueError(f"«{self.nombre}»: sujeto_es={self.sujeto_es!r} no "
                             f"es uno de {SUJETOS} — y un tipo que `vigencia` no "
                             "conoce hace que la habilidad no caduque nada, "
                             "callada")

    def arreglo_de(self, regla: str) -> str:
        return self.arreglos.get(regla, "")
