"""LA IDENTIDAD DE UN CASO EN EL EVAL SET ES UNA SOLA — `clave_caso`.

El bug que esto congela (2026-08-22): `votar()` guardaba `caso.upper()` y los
lectores (`ya_votados`, `es_ruido`, la búsqueda de la vista y el dedup de
`_voto_previo`) comparaban SIN upper. Un bono (`BPOA7`) matcheaba *de
casualidad*; los votos sobre motores y tablas (`manager.salud_eventos`,
`motor_cedears`, `Finnhub news`) no se recordaban NUNCA: el user marcaba
«✔ sirve» o «✖ no acertó», cambiaba de tab, y los botones volvían intactos —
con el voto guardado hasta 9 veces porque el dedup tampoco encontraba el
previo. REGLA #9: la misma identidad con dos criterios, cero errores.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from api.services import av_agent_evals as ev

RAIZ = Path(__file__).resolve().parents[2]


def _fuente_sin_comentarios(p: Path) -> str:
    """El fuente EJECUTABLE: sin comentarios NI docstrings — los dos citan el
    bug por nombre y un test que lee texto no ejecutable se caza a sí mismo."""
    t = re.sub(r'"""[\s\S]*?"""', '""', p.read_text(encoding="utf-8"))
    out = []
    for ln in t.splitlines():
        out.append(ln.split("#")[0] if "#" in ln and not ln.strip().startswith('"') else ln)
    return "\n".join(out)


def test_la_clave_es_una_normalizacion_y_no_dos():
    assert ev.clave_caso("  manager.salud_eventos ") == "MANAGER.SALUD_EVENTOS"
    assert ev.clave_caso("BPOA7") == "BPOA7"
    assert ev.clave_caso(None) == ""


def test_votar_y_buscar_hablan_del_MISMO_caso():
    """Lo que `votar` persiste y lo que la vista busca tienen que coincidir
    para un caso en minúscula — el que falló en prod."""
    queries = []

    class _Cur:
        def execute(self, sql, params=None):
            queries.append((sql, params))

        def fetchone(self):
            return (7,)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Conn()

    with patch.multiple(ev, _voto_previo=lambda c, ca, o="humano": None,
                        get_pool=lambda: _Pool()):
        r = ev.votar(caso="manager.salud_eventos", dominio="sistema",
                     causa="sin_escribir", acierta=False, nota="no aplica",
                     origen="humano")
    assert r["ok"] is True
    insert = next(p for s, p in queries if s and "INSERT" in s)
    # El caso INSERTADO es exactamente la clave con la que la vista va a buscar.
    assert insert[0] == ev.clave_caso("manager.salud_eventos")


def test_ningun_upper_suelto_en_el_camino_del_caso():
    """El `.upper()` inline en el INSERT fue el bug: la normalización vive en
    `clave_caso` y nadie la reimplementa. Se lee el fuente EJECUTABLE (sin
    comentarios — un test ya se cazó a sí mismo con su propio comentario)."""
    src = _fuente_sin_comentarios(RAIZ / "api/services/av_agent_evals.py")
    assert "caso.upper()" not in src, \
        "hay un .upper() suelto sobre el caso: usá clave_caso — es EL bug de §0.ck"


def test_la_vista_busca_los_votos_con_clave_caso():
    """El lado que LEE también pasa por la clave. Sin esto, el próximo lookup
    nuevo nace con `.strip()` pelado y el bug vuelve por otra puerta."""
    src = _fuente_sin_comentarios(RAIZ / "api/services/av_agent_vista.py")
    for patron, donde in [
        (r"votados\.get\(\(av_agent_evals\.clave_caso", "la búsqueda de votados"),
        (r"av_agent_evals\.clave_caso\(h\.get\(\"ticker\"\)\)[\s\S]{0,120}in ruido",
         "la búsqueda de ruido"),
    ]:
        assert re.search(patron, src), f"{donde} no usa clave_caso"
