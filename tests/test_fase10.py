# -*- coding: utf-8 -*-
"""Fase 10: plugin de Negosy configurable, descargas incrementales por período. Sin conexión a Negosy real."""
import datetime
import os

import pytest

import preve_utils as U
from preve_config import Config, ConfigError
from plugins.negosy import NegosyPlugin, _REPORTES
from plugins.negosy import tiendas as T
from plugins.negosy import extractor as ex

HOY = datetime.date.today()
AYER = HOY - datetime.timedelta(days=1)


@pytest.fixture
def cfg():
    c = Config.actual()
    c.guardar_sucursales([{'nombre': 'San José', 'almacenes': ['Tienda Juliaca'], 'activa': True},
                          {'nombre': 'Santa Bárbara', 'almacenes': ['SANTA BARBARA'], 'activa': True},
                          {'nombre': 'Lima', 'almacenes': ['YUJU STORE PRINCIPAL'], 'activa': True}])
    return c


@pytest.fixture(autouse=True)
def sin_env(monkeypatch):
    monkeypatch.delenv('NEGOSY_USUARIO', raising=False)
    monkeypatch.delenv('NEGOSY_CONTRASENA', raising=False)


class ExtractorFalso:
    """Reemplaza a extraer_datos: no toca la red; escribe los archivos que 'descargaría'."""

    def __init__(self, monkeypatch, omitir=()):
        self.llamadas = []
        self.omitir = omitir
        monkeypatch.setattr(ex, 'extraer_datos', self)

    def __call__(self, carpeta, usuario, contrasena, **kw):
        self.llamadas.append(dict(kw, carpeta=carpeta, usuario=usuario, contrasena=contrasena))
        for k in kw['tiendas']:
            for r in _REPORTES:
                if r == 'listado_productos' and k in kw['tiendas_sin_listado']:
                    continue
                if (r, k) in self.omitir:
                    continue
                open(os.path.join(carpeta, f'{r}_{k}_{HOY:%Y%m%d}.xlsx'), 'w').write('x')
        return {}


def _periodo_importado(entrada, inicio, fin, tienda='san_jose', con_marca=True, fecha_listado='20240101'):
    ruta = os.path.join(entrada, U.nombre_periodo(inicio, fin))
    os.makedirs(ruta, exist_ok=True)
    open(os.path.join(ruta, f'listado_productos_{tienda}_{fecha_listado}.xlsx'), 'w').write('viejo')
    if con_marca:
        U.marcar_descarga_completa(ruta)
    return ruta


@pytest.fixture
def entrada(tmp_path):
    d = tmp_path / 'entrada'
    d.mkdir()
    return str(d)


def _plugin_con_credenciales(cfg):
    cfg.set('negosy_usuario', 'david')
    cfg.set('negosy_contrasena', 'secreta')
    return NegosyPlugin()


# ── URLs y tiendas desde Config ────────────────────────────────
def test_valores_de_fabrica_cuando_no_hay_nada_guardado(cfg):
    t = T.tiendas_configuradas(cfg)
    assert t['san_jose']['base_url'] == ex.TIENDAS_DEFECTO['san_jose']['base_url']
    assert t['san_jose']['selector_texto'] == 'SAN JOSE JULIACA'
    assert t['santa_barbara']['selector_texto'] == 'SANTA BARBARA'
    assert t['principal']['sucursal'] == 'Lima' and t['principal']['selector_texto'] == 'YUJU STORE'
    assert T.login_url(cfg) == ex.LOGIN_URL_DEFECTO


def test_sucursal_nueva_usa_su_nombre_como_selector_y_sin_url(cfg):
    cfg.guardar_sucursales([{'nombre': 'Arequipa', 'almacenes': ['Arequipa'], 'activa': True},
                            {'nombre': 'San José', 'almacenes': ['x'], 'activa': True}])
    t = T.tiendas_configuradas(cfg)
    assert t['arequipa']['selector_texto'] == 'Arequipa' and t['arequipa']['base_url'] == ''


def test_extraer_usa_la_url_base_guardada_en_config(cfg, entrada, monkeypatch):
    falso = ExtractorFalso(monkeypatch)
    T.guardar_ajustes(cfg, 'https://login.otro.com/entrar/', '', '',
                      {'san_jose': {'base_url': 'https://nueva-tienda.negosy.com/', 'selector_texto': 'MI TIENDA'}}, True)
    p = _plugin_con_credenciales(cfg)
    p.extraer(entrada, sucursal='San José')
    kw = falso.llamadas[0]
    assert kw['login_url'] == 'https://login.otro.com/entrar/'
    base = kw['tiendas_config']['san_jose']['base_url']
    assert base == 'https://nueva-tienda.negosy.com'                       # no la de TIENDAS_DEFECTO
    assert base != ex.TIENDAS_DEFECTO['san_jose']['base_url']
    assert ex._url_listado_productos(base).startswith('https://nueva-tienda.negosy.com/u/report/')
    assert kw['tiendas_config']['san_jose']['selector_texto'] == 'MI TIENDA'


def test_falta_url_base_de_sucursal_nueva_da_error_claro(cfg, entrada, monkeypatch):
    ExtractorFalso(monkeypatch)
    cfg.guardar_sucursales([{'nombre': 'Arequipa', 'almacenes': ['Arequipa'], 'activa': True}])
    with pytest.raises(RuntimeError, match='URL base'):
        _plugin_con_credenciales(cfg).extraer(entrada, sucursal='Arequipa')


# ── credenciales ───────────────────────────────────────────────
def test_credenciales_de_la_base_ganan_sobre_env(cfg, monkeypatch):
    monkeypatch.setenv('NEGOSY_USUARIO', 'del_env')
    monkeypatch.setenv('NEGOSY_CONTRASENA', 'clave_env')
    p = NegosyPlugin()
    assert p.credenciales() == {'usuario': 'del_env', 'contrasena': 'clave_env'}       # solo .env: sigue funcionando
    assert p.origen_credenciales() == 'env' and not p.credenciales_en_ambos_lados()
    cfg.set('negosy_usuario', 'de_la_base')
    cfg.set('negosy_contrasena', 'clave_base')
    assert p.credenciales() == {'usuario': 'de_la_base', 'contrasena': 'clave_base'}
    assert p.origen_credenciales() == 'configuracion' and p.credenciales_en_ambos_lados()


def test_credenciales_a_medias_en_la_base_caen_al_env(cfg, monkeypatch):
    monkeypatch.setenv('NEGOSY_USUARIO', 'del_env')
    monkeypatch.setenv('NEGOSY_CONTRASENA', 'clave_env')
    cfg.set('negosy_usuario', 'solo_usuario')
    assert NegosyPlugin().credenciales()['usuario'] == 'del_env'


def test_guardar_ajustes_valida(cfg):
    with pytest.raises(ConfigError, match='https://'):
        T.guardar_ajustes(cfg, 'http://inseguro.com', '', '', {}, True)
    with pytest.raises(ConfigError, match='juntos'):
        T.guardar_ajustes(cfg, 'https://ok.com/login/', 'david', '', {}, True)
    with pytest.raises(ConfigError, match='juntos'):
        T.guardar_ajustes(cfg, 'https://ok.com/login/', '', 'clave', {}, True)
    with pytest.raises(ConfigError, match='https://'):
        T.guardar_ajustes(cfg, 'https://ok.com/login/', '', '', {'san_jose': {'base_url': 'ftp://x'}}, True)
    assert cfg.get('negosy_login_url') == ex.LOGIN_URL_DEFECTO                         # nada se guardó a medias
    T.guardar_ajustes(cfg, 'https://ok.com/login/', 'david', 'clave', {}, False)
    assert cfg.get('negosy_usuario') == 'david' and cfg.get('negosy_actualizar_catalogo') is False


# ── rango de fechas ────────────────────────────────────────────
def test_sin_periodo_previo_primera_carga_de_730_dias(entrada):
    assert U.rango_descarga(entrada, HOY) == (HOY - datetime.timedelta(days=730), AYER)


def test_con_periodo_previo_desde_el_dia_siguiente_hasta_ayer(entrada):
    fin_previo = HOY - datetime.timedelta(days=15)
    _periodo_importado(entrada, fin_previo - datetime.timedelta(days=30), fin_previo)
    assert U.rango_descarga(entrada, HOY) == (fin_previo + datetime.timedelta(days=1), AYER)


def test_periodo_incompleto_no_cuenta_como_importado(entrada):
    fin_previo = HOY - datetime.timedelta(days=15)
    _periodo_importado(entrada, fin_previo - datetime.timedelta(days=30), fin_previo, con_marca=False)
    assert U.ultimo_periodo_importado(entrada) is None
    assert U.rango_descarga(entrada, HOY)[0] == HOY - datetime.timedelta(days=730)


def test_al_dia_no_hay_rango(entrada):
    _periodo_importado(entrada, AYER - datetime.timedelta(days=10), AYER)
    assert U.rango_descarga(entrada, HOY) is None


def test_extraer_al_dia_no_descarga_nada(cfg, entrada, monkeypatch):
    falso = ExtractorFalso(monkeypatch)
    _periodo_importado(entrada, AYER - datetime.timedelta(days=10), AYER)
    assert _plugin_con_credenciales(cfg).extraer(entrada, sucursal='San José') is None
    assert falso.llamadas == []


# ── carpeta del período y marcador ─────────────────────────────
def test_carpeta_descarga_usa_el_formato_de_periodo_y_se_reutiliza(tmp_path):
    a = ex.carpeta_descarga(str(tmp_path), datetime.date(2026, 1, 1), datetime.date(2026, 1, 14))
    assert os.path.basename(a) == '2026-01-01_a_2026-01-14'
    assert ex.carpeta_descarga(str(tmp_path), datetime.date(2026, 1, 1), datetime.date(2026, 1, 14)) == a
    assert os.listdir(str(tmp_path)) == ['2026-01-01_a_2026-01-14']


def test_nombre_periodo_es_el_mismo_que_usa_la_salida(tmp_path):
    from preve_orquestador import carpeta_periodo
    assert os.path.basename(carpeta_periodo(str(tmp_path), '2026-01-01', datetime.date(2026, 1, 14))) \
        == U.nombre_periodo(datetime.datetime(2026, 1, 1), '2026-01-14')


def test_descarga_completa_deja_marcador_y_reintento_reutiliza_carpeta(cfg, entrada, monkeypatch):
    falso = ExtractorFalso(monkeypatch)
    p = _plugin_con_credenciales(cfg)
    c1 = p.extraer(entrada, sucursal='San José')
    assert os.path.exists(os.path.join(c1, U.MARCADOR_DESCARGA))
    assert os.path.basename(c1) == U.nombre_periodo(HOY - datetime.timedelta(days=730), AYER)
    # ya está al día; si se borra el marcador (descarga "cancelada"), el reintento cae en la misma carpeta
    os.remove(os.path.join(c1, U.MARCADOR_DESCARGA))
    c2 = p.extraer(entrada, sucursal='San José')
    assert c2 == c1 and len(os.listdir(entrada)) == 1
    assert len(falso.llamadas) == 2


def test_descarga_incompleta_no_se_marca_y_avisa(cfg, entrada, monkeypatch):
    ExtractorFalso(monkeypatch, omitir={('reporte_ventas_producto', 'san_jose')})
    with pytest.raises(RuntimeError, match='incompleta'):
        _plugin_con_credenciales(cfg).extraer(entrada, sucursal='San José')
    assert U.ultimo_periodo_importado(entrada) is None
    assert U.rango_descarga(entrada, HOY)[0] == HOY - datetime.timedelta(days=730)


# ── catálogo ───────────────────────────────────────────────────
def test_catalogo_se_descarga_por_defecto(cfg, entrada, monkeypatch):
    falso = ExtractorFalso(monkeypatch)
    fin_previo = HOY - datetime.timedelta(days=15)
    _periodo_importado(entrada, fin_previo - datetime.timedelta(days=30), fin_previo)
    _plugin_con_credenciales(cfg).extraer(entrada, sucursal='San José')
    assert falso.llamadas[0]['tiendas_sin_listado'] == set()


def test_sin_actualizar_catalogo_copia_el_anterior_y_no_lo_descarga(cfg, entrada, monkeypatch):
    fin_previo = HOY - datetime.timedelta(days=15)
    previo = _periodo_importado(entrada, fin_previo - datetime.timedelta(days=30), fin_previo)

    class NoDebeDescargarListado(ExtractorFalso):
        def __call__(self, carpeta, usuario, contrasena, **kw):
            assert 'san_jose' in kw['tiendas_sin_listado'], 'intentó descargar el catálogo'
            return super().__call__(carpeta, usuario, contrasena, **kw)
    falso = NoDebeDescargarListado(monkeypatch)
    mensajes = []
    c = _plugin_con_credenciales(cfg).extraer(entrada, opciones={'actualizar_catalogo': False},
                                              log=mensajes.append, sucursal='San José')
    copia = os.path.join(c, 'listado_productos_san_jose_20240101.xlsx')
    assert open(copia).read() == 'viejo' and c != previo
    assert any('Catálogo: copiado del período anterior' in m for m in mensajes)
    assert os.path.exists(os.path.join(c, U.MARCADOR_DESCARGA))                        # copia + 4 reportes = completo


def test_sin_actualizar_catalogo_pero_sin_listado_previo_lo_descarga(cfg, entrada, monkeypatch):
    falso = ExtractorFalso(monkeypatch)
    mensajes = []
    _plugin_con_credenciales(cfg).extraer(entrada, opciones={'actualizar_catalogo': False}, log=mensajes.append,
                                          sucursal='San José')
    assert falso.llamadas[0]['tiendas_sin_listado'] == set()
    assert any('Catálogo: descargado de Negosy' in m for m in mensajes)


def test_la_opcion_de_config_controla_el_catalogo(cfg, entrada, monkeypatch):
    fin_previo = HOY - datetime.timedelta(days=15)
    _periodo_importado(entrada, fin_previo - datetime.timedelta(days=30), fin_previo)
    falso = ExtractorFalso(monkeypatch)
    cfg.set('negosy_actualizar_catalogo', False)
    _plugin_con_credenciales(cfg).extraer(entrada, sucursal='San José')
    assert falso.llamadas[0]['tiendas_sin_listado'] == {'san_jose'}


def test_ultimo_listado_elige_el_mas_reciente_de_periodos_completos(entrada):
    _periodo_importado(entrada, '2026-01-01', '2026-01-14', fecha_listado='20260114')
    _periodo_importado(entrada, '2026-01-15', '2026-01-28', fecha_listado='20260128')
    _periodo_importado(entrada, '2026-01-29', '2026-02-11', fecha_listado='20260211', con_marca=False)
    from plugins.negosy import ultimo_listado
    assert os.path.basename(ultimo_listado(entrada, 'san_jose')) == 'listado_productos_san_jose_20260128.xlsx'
    assert ultimo_listado(entrada, 'santa_barbara') is None
