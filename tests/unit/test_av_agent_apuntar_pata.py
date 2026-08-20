"""LA PATA EQUIVOCADA POR FIN TIENE ARREGLO.

El user, viendo los BOPREALes en la pantalla por enésima vez (2026-08-20):

    *«estos siguen apareciendo, es algo de no creer. Necesito de una vez por
    todas que esto se solucione.»*

Y tenía razón por una causa estructural: **`pata_equivocada` no tenía ninguna
acción que lo arreglara.** La única puerta era `mercado.pata_dolar`, que PIDE la
pata en dólares pero no toca `mercado.curvas.instrumento`. El master seguía
apuntando a la pata en pesos, el detector lo volvía a ver, y el hallazgo
reaparecía todas las ruedas para siempre.

> Un hallazgo sin arreglo posible no es un aviso: es una pared.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from api.services.av_agent_hacer import ACCIONES, Propuesta, Seguible

_ACT = "MERV - XMEV - BPOA7 - 24hs"
_OK = "MERV - XMEV - BPA7D - 24hs"


def _a():
    return ACCIONES["mercado.apuntar_pata"]


def _caso(**kw):
    base = {"key": "BPOA7", "ticker": "BPOA7", "simbolo": _ACT,
            "sugerido": _OK, "curva": "bopreales"}
    return {**base, **kw}


# ── que exista y esté enchufada ──────────────────────────────────────────────

def test_la_accion_existe_y_resuelve_su_control():
    from api.services.av_agent_hacer import POR_CONTROL
    assert POR_CONTROL["patas_equivocadas"] == "mercado.apuntar_pata"


def test_el_control_existe_y_esta_declarado():
    """Sin el control, la acción no tiene de dónde sacar casos y el botón nunca
    aparece — que es exactamente el estado anterior."""
    from api.services.av_agent_salud import CONTROLES as EXPLICADOS
    from jobs.controles_datos import CONTROLES
    ids = {c.id for c in CONTROLES}
    assert "patas_equivocadas" in ids
    assert "patas_equivocadas" in EXPLICADOS, "un control sin «qué rompe» no se lee"


# ── proponer: la guarda que evita cambiar un problema por otro ───────────────

def test_sin_pata_SUGERIDA_no_se_propone_nada():
    """**REGLA #9(A)**: la pata no se adivina por sufijo. `BPOA7 → BPA7D` se come
    una letra del medio, así que si el control no la trae, no hay propuesta."""
    assert _a().proponer([_caso(sugerido="")]) == []


def test_si_el_master_YA_apunta_a_la_default_no_hay_nada_que_hacer():
    assert _a().proponer([_caso(sugerido=_ACT)]) == []


def test_la_propuesta_nombra_LAS_DOS_patas_en_corto():
    p = _a().proponer([_caso()])[0]
    assert p.sujeto == "BPOA7" and p.propuesto == _OK and p.antes == _ACT
    assert "BPOA7" in p.porque and "BPA7D" in p.porque
    # Y dice lo que hace que valga la pena: se ve sin reiniciar.
    assert "sin reiniciar" in p.porque


# ── aplicar: las DOS copias del símbolo, y la suscripción ────────────────────

class _Cur:
    """Cursor de mentira que graba lo que se le pide."""

    def __init__(self, existe=True, rowcount=1):
        self.sql: list[str] = []
        self._existe, self.rowcount = existe, rowcount

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self._ultimo = (sql, params)

    def fetchone(self):
        return (1,) if self._existe else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, cur):
        self._cur = cur
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _aplicar(cur, subscribe=None):
    from api.services import av_agent_hacer as h

    class _Pool:
        def connection(self):
            return _Conn(cur)

    llamadas: list[str] = []

    class _Adhoc:
        @staticmethod
        def subscribe(sim):
            llamadas.append(sim)
            return subscribe if subscribe is not None else {"ok": True}

    class _Curvas:
        invalidado = False

        @classmethod
        def invalidar(cls):
            cls.invalidado = True

    with patch("core.postgres.get_pool", lambda: _Pool()), \
         patch.dict("sys.modules", {}), \
         patch("core.adhoc_subscriptions.subscribe", _Adhoc.subscribe), \
         patch("core.curvas_sql.invalidar", _Curvas.invalidar):
        h.AccionApuntarPata().aplicar(
            Propuesta(sujeto="BPOA7", campo="mercado.curvas.instrumento",
                      propuesto=_OK, antes=_ACT, porque="", fuente="regla"))
    return llamadas, _Curvas


def test_se_escriben_LAS_DOS_COPIAS_del_simbolo_en_un_solo_UPDATE():
    """La columna y la clave del blob. `curvas_sql` hace ganar a la columna al
    leer, así que con una alcanzaría — pero dejar el blob diciendo otra cosa es
    recrear la divergencia que costó cuatro días (REGLA #9 B). Se arregla el
    duplicado, no se confía en el árbitro."""
    cur = _Cur()
    _aplicar(cur)
    upd = [x for x in cur.sql if x.startswith("UPDATE mercado.curvas")]
    assert len(upd) == 1, cur.sql
    assert "instrumento = %(s)s" in upd[0]
    assert "jsonb_set" in upd[0] and "'{ticker}'" in upd[0]


def test_y_se_PIDE_la_pata_para_que_se_vea_en_el_acto():
    """Es la mitad que desbloqueó automatizar esto. El motor arma su universo al
    arrancar, pero el `adhoc_watcher` pollea cada 5 s: sin esta suscripción el
    cambio no se vería hasta el próximo reinicio, y *una acción que se aplica y
    no se ve destruye la confianza en todas las demás* (§0.u)."""
    cur = _Cur()
    pedidas, curvas = _aplicar(cur)
    assert pedidas == [_OK]
    assert curvas.invalidado, "sin invalidar, el master cacheado sigue viejo"


def test_no_se_escribe_un_simbolo_QUE_NO_EXISTE():
    cur = _Cur(existe=False)
    with pytest.raises(ValueError, match="especies"):
        _aplicar(cur)
    assert not [x for x in cur.sql if x.startswith("UPDATE")]


def test_si_el_UPDATE_no_toca_exactamente_UNA_fila_se_aborta():
    """Cero filas = el ticker no está. Más de una = la PK no es la que creemos y
    estaríamos reescribiendo el símbolo de varios bonos de una."""
    cur = _Cur(rowcount=0)
    with pytest.raises(ValueError, match="curvas"):
        _aplicar(cur)


def test_si_el_master_queda_bien_pero_NO_se_pudo_pedir_se_DICE():
    """El estado a medias es real y no se puede tapar: el campo quedó corregido y
    el precio no va a llegar hasta el próximo reinicio. Callarlo sería prometer
    algo que no pasó."""
    cur = _Cur()
    with pytest.raises(RuntimeError, match="no se pudo pedir"):
        _aplicar(cur, subscribe={"ok": False, "reason": "tope de adhoc"})


# ── verificar / veredicto: separados, como manda §0.ak ───────────────────────

def test_es_SEGUIBLE_porque_el_precio_lo_contesta_el_mercado():
    assert isinstance(_a(), Seguible) and _a().espera_s >= 30 * 60


def test_verificar_exige_que_las_dos_copias_digan_LO_MISMO():
    """Si la columna quedó bien y el blob no, el sistema vuelve a estar partido
    en dos mitades que no se hablan — el incidente de los 4 días."""
    import inspect
    src = inspect.getsource(_a().verificar)
    assert "data->>'ticker'" in src
    assert "quedaron partidos" in src


def test_veredicto_mira_el_PRECIO_y_verificar_no():
    import inspect
    assert "market_snapshot" in inspect.getsource(_a().veredicto)
    assert "market_snapshot" not in inspect.getsource(_a().verificar)
