"""La tab CONTROL: qué monitorea el agente y si lo está haciendo (§0.bv).

Lo que se congela acá son las tres afirmaciones que, si se rompen, la pantalla
sigue funcionando y **contesta mal en silencio** — que es el modo de falla que
esta tab vino justamente a hacer visible.
"""
from api.services import av_agent_agenda as ag


class TestSeg:
    """El ritmo se LEE del crontab. Lo que no se entiende no se juzga."""

    def test_las_formas_que_el_repo_usa_de_verdad(self):
        assert ag._seg("*/5 * * * *") == 300
        assert ag._seg("*/30 13-19 * * 1-5") == 1800
        assert ag._seg("0 * * * *") == 3600
        assert ag._seg("0 13-20 * * 1-5") == 3600 * 4
        assert ag._seg("0 12,16,20 * * *") == 3600 * 4
        assert ag._seg("30 23 * * *") == 86400

    def test_lo_ilegible_no_se_juzga(self):
        # ⚠️ `None`, NO un default. Un schedule que no supe leer con una cadencia
        # inventada produce alarmas inventadas — o, peor, verdes inventados.
        assert ag._seg("") is None
        assert ag._seg("   ") is None
        assert ag._seg("*/loquesea * * * *") is None

    def test_el_crontab_real_se_entiende_entero(self):
        """Si mañana entra una forma de cron nueva, esto avisa ANTES de que la
        pantalla empiece a decir «sin poder juzgar» sobre media agenda."""
        from api.services.jobs_catalogo import schedules_por_modulo
        ilegibles = sorted({c for cs in schedules_por_modulo().values()
                            for c in cs if ag._seg(c) is None})
        assert not ilegibles, f"schedules que no sé leer: {ilegibles}"


class TestDelDaemon:
    """Lo que mira el centinela sale de SU lista, no del cron de al lado."""

    def test_solo_lo_que_el_centinela_declara(self):
        from api.services.av_agent_centinela import _CUBRE
        mira = {t for tipos in _CUBRE.values() for t in tipos}
        piezas = {"jobs.x": [{"nombre": n, "tipo": n} for n in
                             sorted(mira | {"falta_en_base", "db_cambio"})]}
        out = {p["nombre"] for p in ag._del_daemon(piezas)}
        assert out == mira

    def test_no_repite_la_pieza_que_corre_en_dos_jobs(self):
        """Un detector puede correr en el daemon Y en un cron — eso es real y se
        muestra en las dos filas. Lo que no puede es salir dos veces en UNA."""
        p = {"nombre": "Sin precio", "tipo": "sin_precio"}
        assert len(ag._del_daemon({"a": [p], "b": [dict(p)]})) == 1


class TestVista:
    """El contrato que lee el front. No toca la base: cae al crontab pelado."""

    def test_nunca_levanta_y_publica_los_cuatro_numeros(self):
        v = ag.vista()
        assert v["ok"] is True
        for k in ("filas", "piezas", "a_pedido", "atrasados", "sin_juzgar", "al_dia"):
            assert k in v, f"falta «{k}»: el front lo lee"

    def test_atrasado_es_tri_estado(self):
        """`None` (no pude juzgar) tiene que poder distinguirse de `False`.
        Colapsarlos a booleano pinta de verde lo que nadie midió."""
        for f in ag.vista()["filas"]:
            assert f["atrasado"] in (True, False, None)

    def test_el_titular_cuenta_piezas_UNICAS(self):
        v = ag.vista()
        unicas = {p["nombre"] for f in v["filas"] for p in f["piezas"]}
        assert v["piezas"] == len(unicas)

    def test_toda_pieza_que_declara_job_aparece_en_alguna_fila(self):
        """**La ley del agente, del lado de CONTROL.** Un detector que declara
        `corre_en` y no sale acá deja la pantalla diciendo que el agente hace 30
        cosas mientras hace 31 — sin fallar, que es lo peor."""
        from api.services import av_agent_skills
        declaran = {s.nombre for s in av_agent_skills.catalogo()
                    if (s.extra or {}).get("corre_en")}
        v = ag.vista()
        salen = {p["nombre"] for f in v["filas"] for p in f["piezas"]}
        # Los controles se fuerzan a `jobs.controles_datos` en el service, así
        # que entran aunque no declaren; por eso se compara por inclusión.
        assert declaran - salen == set(), f"declaran job y no salen: {declaran - salen}"
