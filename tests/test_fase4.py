# -*- coding: utf-8 -*-
"""Tests de la Fase 4: importación flexible de ventas y plugins de ERP."""
import os

import pandas as pd
import pytest

import preve_importador as imp


@pytest.fixture
def catalogo():
    """Catálogo mínimo en la base + configuración con dos sucursales."""
    from database import Database
    from preve_config import Config
    db = Database.default()
    db.guardar_productos(pd.DataFrame({'sku': ['7751', '7752', '7752-2'], 'codigo_barras': ['7751', '7752', '7752'],
                                       'nombre': ['A', 'B', 'B bis']}))
    Config.actual().guardar_sucursales([{'nombre': 'San José', 'almacenes': ['Tienda Juliaca']},
                                        {'nombre': 'Lima', 'almacenes': ['Tienda Lima']}])
    return db


# ── formato y lectura ─────────────────────────────────────────────
@pytest.mark.parametrize('nombre,contenido,esperado', [
    ('v.csv', 'a,b\n1,2\n', 'csv'), ('v.txt', 'a;b\n1;2\n', 'csv'), ('v.csv', 'a\tb\tc\n1\t2\t3\n', 'tsv'),
    ('v.tsv', 'a\tb\n1\t2\n', 'tsv'),
])
def test_detecta_formato(tmp_path, nombre, contenido, esperado):
    ruta = tmp_path / nombre
    ruta.write_text(contenido, encoding='utf-8')
    assert imp.detectar_formato(str(ruta)) == esperado


def test_formato_no_soportado(tmp_path):
    with pytest.raises(ValueError, match='no soportado'):
        imp.detectar_formato(str(tmp_path / 'x.pdf'))


def test_lee_csv_con_punto_y_coma_latin1_y_codigos_con_ceros(tmp_path):
    ruta = tmp_path / 'ventas.csv'
    ruta.write_bytes('Fecha;Código;Cantidad\n01/02/2025;007751;3\n'.encode('latin-1'))
    df = imp.leer_archivo(str(ruta))
    assert list(df.columns) == ['Fecha', 'Código', 'Cantidad']
    assert df.loc[0, 'Código'] == '007751'                      # no se pierden los ceros


def test_lee_excel_con_filas_de_titulo(tmp_path):
    ruta = tmp_path / 'v.xlsx'
    pd.DataFrame([['Reporte', None, None], ['FECHA', 'SKU', 'CANTIDAD'], ['01-01-2025', '7751', 2]]
                 ).to_excel(ruta, header=False, index=False)
    df = imp.leer_archivo(str(ruta))
    assert list(df.columns) == ['FECHA', 'SKU', 'CANTIDAD'] and len(df) == 1


# ── mapeo y fechas ────────────────────────────────────────────────
def test_sugiere_mapeo_negosy_y_genericos():
    negosy = ['N', 'FECHA/HORA', 'CÓDIGO DE BARRA', 'PRODUCTO', 'ALMACÉN', 'PRECIO X UNIDAD', 'CANTIDAD', 'TOTAL']
    assert imp.sugerir_mapeo(negosy) == {'fecha': 'FECHA/HORA', 'producto': 'CÓDIGO DE BARRA', 'cantidad': 'CANTIDAD',
                                         'sucursal': 'ALMACÉN', 'precio': 'PRECIO X UNIDAD', 'nombre': 'PRODUCTO'}
    m = imp.sugerir_mapeo(['Date', 'EAN', 'Qty', 'Store'])
    assert m == {'fecha': 'Date', 'producto': 'EAN', 'cantidad': 'Qty', 'sucursal': 'Store'}


@pytest.mark.parametrize('valores,esperado', [
    (['31-12-2025 23:06', '01-01-2026 10:00'], '%d-%m-%Y %H:%M'),
    (['2025-12-31', '2026-01-01'], '%Y-%m-%d'),
    (['12/31/2025', '01/15/2026'], '%m/%d/%Y'),                     # solo tiene sentido mes/día
    (['31/12/2025', '15/01/2026'], '%d/%m/%Y'),
])
def test_detecta_formato_de_fecha(valores, esperado):
    assert imp.detectar_formato_fecha(pd.Series(valores)) == esperado


def test_numeros_con_formatos_regionales():
    s = pd.Series(['1,234.5', '1.234,5', 'S/ 3.50', '7', 'abc', '2,5'])
    assert imp._a_numero(s).tolist()[:4] == [1234.5, 1234.5, 3.5, 7.0]
    assert pd.isna(imp._a_numero(s)[4]) and imp._a_numero(s)[5] == 2.5


# ── validación con errores por fila ───────────────────────────────
def test_valida_y_reporta_errores_por_fila(catalogo):
    df = pd.DataFrame({'F': ['01-01-2025', 'xx', '02-01-2025', '03-01-2025', '04-01-2025'],
                       'P': ['7751', '7751', '9999', '7752', '7751'],
                       'Q': ['2', '1', '1', 'muchos', '5'],
                       'S': ['San José', 'San José', 'Lima', 'lima', 'Cusco']})
    mapeo = {'fecha': 'F', 'producto': 'P', 'cantidad': 'Q', 'sucursal': 'S'}
    validas, errores = imp.validar(df, mapeo, '%d-%m-%Y', catalogo.productos_por_codigo(), ['San José', 'Lima'])
    assert len(validas) == 1 and validas.loc[0, 'cantidad'] == 2
    por_fila = errores.groupby('fila')['error'].apply(list).to_dict()
    assert set(por_fila) == {2, 3, 4, 5}
    assert 'Fecha no válida' in por_fila[2][0]
    assert por_fila[3] == ['El producto no existe en el catálogo']
    assert por_fila[4] == ['La cantidad no es un número']
    assert por_fila[5] == ['Sucursal desconocida (no está en la configuración)']


def test_sucursal_por_nombre_de_tienda_o_almacen(catalogo):
    df = pd.DataFrame({'F': ['01-01-2025'] * 3, 'P': ['7751'] * 3, 'Q': ['1'] * 3,
                       'S': ['Tienda Juliaca', 'TIENDA LIMA', 'San José']})
    validas, errores = imp.validar(df, {'fecha': 'F', 'producto': 'P', 'cantidad': 'Q', 'sucursal': 'S'},
                                   'auto', None, imp.sucursales_y_ubicaciones())
    assert errores.empty and validas['sucursal'].tolist() == ['San José', 'Lima', 'San José']


def test_mapeo_incompleto_es_error():
    with pytest.raises(ValueError, match='Cantidad'):
        imp.validar(pd.DataFrame({'F': [], 'P': []}), {'fecha': 'F', 'producto': 'P'})


def test_importar_guarda_ventas_y_escribe_reporte(catalogo, tmp_path, log):
    ruta = tmp_path / 'ventas.csv'
    ruta.write_text('Fecha,EAN,Unidades,Local\n2025-01-01,7751,2,San José\n2025-01-01,7751,3,San José\n'
                    '2025-01-02,7752,1,\n2025-01-02,0000,1,Lima\n', encoding='utf-8')
    res = imp.importar(str(ruta), {'fecha': 'Fecha', 'producto': 'EAN', 'cantidad': 'Unidades', 'sucursal': 'Local'},
                       catalogo, formato_fecha='detectar', sucursal_defecto='Lima', log=log)
    assert (res['filas_leidas'], res['filas_validas'], res['filas_con_error']) == (4, 3, 1)
    assert res['por_sucursal'] == {'Lima': 1, 'San José': 1}       # 2 + 3 del mismo día = 1 día-producto
    assert catalogo.obtener_ventas('San José')['cantidad'].tolist() == [5.0]
    assert os.path.exists(res['ruta_errores'])
    assert pd.read_excel(res['ruta_errores'], sheet_name='Errores por fila')['fila'].tolist() == [4]


def test_importar_sin_catalogo_pide_crearlo(tmp_path, log):
    ruta = tmp_path / 'v.csv'
    ruta.write_text('Fecha,EAN,Q\n2025-01-01,1,1\n', encoding='utf-8')
    with pytest.raises(ValueError, match='catálogo'):
        imp.importar(str(ruta), {'fecha': 'Fecha', 'producto': 'EAN', 'cantidad': 'Q'}, log=log)


def test_perfiles_de_importacion(catalogo):
    catalogo.guardar_perfil_importacion('Negosy', {'fecha': 'FECHA/HORA'}, '%d-%m-%Y %H:%M')
    catalogo.guardar_perfil_importacion('Negosy', {'fecha': 'FECHA'}, None)
    catalogo.guardar_perfil_importacion('Otro', {'fecha': 'Date'})
    assert list(catalogo.listar_perfiles_importacion()['nombre']) == ['Negosy', 'Otro']
    p = catalogo.obtener_perfil_importacion('Negosy')
    assert p['mapeo'] == {'fecha': 'FECHA'} and p['formato_fecha'] is None
    catalogo.eliminar_perfil_importacion('Otro')
    assert catalogo.obtener_perfil_importacion('Otro') is None


# ── plugins ───────────────────────────────────────────────────────
def test_registro_dinamico_encuentra_negosy():
    from plugins import listar_plugins, obtener_plugin
    assert 'negosy' in listar_plugins()
    p = obtener_plugin('negosy')
    assert p.nombre == 'Negosy' and 'tiendas' in p.opciones_disponibles()
    assert obtener_plugin('') is None and obtener_plugin('no_existe') is None


def test_plugin_base_es_abstracto_y_registrar_exige_clave():
    from plugins.base import PluginERP, registrar
    with pytest.raises(TypeError):
        PluginERP()

    class SinClave(PluginERP):
        def extraer(self, carpeta_destino, opciones=None, log=print):
            return carpeta_destino
    with pytest.raises(ValueError):
        registrar(SinClave)


def test_plugin_negosy_exige_credenciales_y_usa_subcarpeta(tmp_path, monkeypatch):
    from plugins import obtener_plugin
    import plugins.negosy.extractor as ext
    p = obtener_plugin('negosy')
    monkeypatch.delenv('NEGOSY_USUARIO', raising=False)
    monkeypatch.delenv('NEGOSY_CONTRASENA', raising=False)
    with pytest.raises(RuntimeError, match='credenciales'):
        p.extraer(str(tmp_path))
    llamadas = {}
    monkeypatch.setenv('NEGOSY_USUARIO', 'u')
    monkeypatch.setenv('NEGOSY_CONTRASENA', 'c')
    monkeypatch.setattr(ext, 'extraer_datos', lambda carpeta, u, c, **kw: llamadas.update(carpeta=carpeta, **kw))
    carpeta = p.extraer(str(tmp_path), {'tiendas': ['lima']})
    assert os.path.dirname(carpeta) == str(tmp_path) and llamadas['carpeta'] == carpeta
    assert llamadas['tiendas'] == ['lima'] and llamadas['crear_subcarpeta'] is False


def test_alias_preve_extractor_sigue_funcionando():
    import preve_extractor
    from plugins.negosy import extractor
    assert preve_extractor.boton_modal_permitido is extractor.boton_modal_permitido
    raiz_app = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(extractor.__file__))))
    assert os.path.dirname(extractor.CONFIG_PATH) == raiz_app       # la config sigue en la carpeta de la app
