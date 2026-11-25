# -*- coding: utf-8 -*-
"""Calendario fijo de compras, CSV internos en datos/, acciones del día, tablero y reinicio total."""
import datetime
import os

import openpyxl
import pandas as pd
import pytest

import preve_ciclo as pc
import preve_datos as dt
import preve_depurar as dep
import preve_tablero as tb
from preve_utils import ruta_dato, ultimo_periodo

D = datetime.date
ENE1 = D(2026, 1, 1)


# ── calendario fijo ────────────────────────────────────────────
@pytest.mark.parametrize('fecha, esperado', [
    (D(2026, 1, 1), (1, D(2026, 1, 1), 1)),
    (D(2026, 4, 15), (1, D(2026, 1, 1), 105)),        # día 105 = fecha del pedido del ciclo 1
    (D(2026, 4, 16), (2, D(2026, 4, 16), 1)),
    (D(2026, 11, 9), (3, D(2026, 7, 30), 103)),       # el ejemplo del usuario: ciclo 3, día 103
    (D(2025, 12, 20), (1, D(2026, 1, 1), 1)),         # antes del día 1 cuenta como día 1
])
def test_ciclo_de_calendario(fecha, esperado):
    assert pc.ciclo_de_calendario(fecha, ENE1, 105) == esperado


def test_calendario_ciclos_lista_fechas_de_pedido():
    cal = pc.calendario_ciclos(ENE1, 105, hasta=D(2026, 11, 9), extra=1)
    assert [c['numero'] for c in cal] == [1, 2, 3, 4]
    assert cal[0]['pedido'] == D(2026, 4, 15) and cal[2]['pedido'] == D(2026, 11, 11)
    assert cal[0]['preliminar_hasta'] == D(2026, 2, 11) and cal[0]['borrador_hasta'] == D(2026, 3, 25)


def test_registrar_importacion_con_ancla_ignora_la_primera_importacion(carpeta_salida):
    info = pc.registrar_importacion(carpeta_salida, fecha=D(2026, 11, 9), ancla=ENE1, duracion_dias=105)
    assert (info['ciclo_numero'], info['dia_ciclo'], info['etiqueta']) == (3, 103, 'FINAL')
    assert info['fecha_limite_pedido'] == '2026-11-11'
    # al pasar al ciclo siguiente se reinicia el conteo de importaciones
    info = pc.registrar_importacion(carpeta_salida, fecha=D(2026, 11, 20), ancla=ENE1, duracion_dias=105)
    assert (info['ciclo_numero'], info['dia_ciclo'], info['importacion_num'], info['etiqueta']) == \
        (4, 9, 1, 'PRELIMINAR')


def test_registrar_importacion_sin_ancla_mantiene_el_comportamiento_anterior(carpeta_salida):
    info = pc.registrar_importacion(carpeta_salida, fecha=D(2026, 11, 9), duracion_dias=105)
    assert (info['ciclo_numero'], info['dia_ciclo']) == (1, 1)


def test_ancla_por_defecto_es_1_de_enero_y_queda_guardada():
    from preve_config import Config
    conf = Config.actual()
    ancla = pc.ancla_calendario(conf)
    assert ancla == D(D.today().year, 1, 1)
    assert conf.get('calendario_inicio') == ancla.isoformat()
    conf.set('calendario_fijo', False)
    assert pc.ancla_calendario(conf) is None


# ── CSV internos en datos/ ─────────────────────────────────────
def test_ruta_dato_escribe_en_datos_y_lee_el_formato_antiguo(tmp_path):
    per = str(tmp_path / '2026-01-01_a_2026-01-14')
    os.makedirs(per)
    open(os.path.join(per, 'x.csv'), 'w').write('a')                       # análisis de una versión anterior
    assert ruta_dato(per, 'x.csv') == os.path.join(per, 'x.csv')
    nueva = ruta_dato(per, 'x.csv', escribir=True)
    assert nueva == os.path.join(per, 'datos', 'x.csv') and os.path.isdir(os.path.dirname(nueva))
    open(nueva, 'w').write('b')
    assert ruta_dato(per, 'x.csv') == nueva                                 # la nueva ubicación manda


def _rec(per):
    df = pd.DataFrame({
        'sku': ['A', 'B', 'C', 'D'], 'nombre': list('abcd'), 'categoria': ['X', 'X', 'Y', ''],
        'proveedor_principal': ['P1', 'P2', 'P1', ''], 'clase_abc': ['A', 'B', 'C', 'C'],
        'accion': ['RECOMPRAR', 'RECOMPRAR', 'LIQUIDAR', 'VIGILAR'],
        'decision': ['COMPRAR', 'COMPRAR', 'NO COMPRAR', 'NO COMPRAR'],
        'demanda_p50_141d': [141.0, 70.0, 0.0, 5.0], 'demanda_p10_141d': [120.0, 60.0, 0.0, 2.0],
        'demanda_p90_141d': [160.0, 80.0, 0.0, 9.0], 'stock_actual': [0.0, 50.0, 30.0, 10.0],
        'compra_sugerida_unid': [150, 40, 0, 0], 'costo_total_compra_pen': [1500.0, 400.0, 0.0, 0.0],
        'costo_puesto_almacen_pen': [10.0, 10.0, 5.0, 2.0], 'precio_regular_pen': [20.0, 20.0, 9.0, 4.0],
        'revisar_antes_de_comprar': [False, True, False, False], 'segmento': ['Regular'] * 4,
    })
    df.to_csv(ruta_dato(per, 'recomendacion_compra.csv', True), index=False)


@pytest.fixture
def datos(carpeta_salida):
    per = os.path.join(carpeta_salida, '2026-01-01_a_2026-01-14')
    _rec(per)
    assert ultimo_periodo(carpeta_salida) == per                          # se encuentra dentro de datos/
    d = dt.Datos(carpeta_salida, 'San José')
    assert d.tiene_datos
    return d


def test_acciones_hoy_prioriza_lo_urgente(datos):
    acc = datos.acciones_hoy()
    assert acc[0]['nivel'] == 'urgente' and acc[0]['titulo'].startswith('1 productos ya están sin stock')
    assert acc[0]['destino'] == 'recom' and acc[0]['filtro'] == 'urg'
    titulos = ' | '.join(a['titulo'] for a in acc)
    assert 'venta fuera de lo común' in titulos and 'parados' in titulos
    assert [a['nivel'] for a in acc] == sorted((a['nivel'] for a in acc),
                                               key=['urgente', 'importante', 'info'].index)


def test_resumen_y_exportacion_del_tablero(datos, tmp_path):
    cuadros = tb.resumen_tablero(datos)
    assert {'Productos por acción', 'Urgencia de compra', 'Inversión por categoría'} <= set(cuadros)
    cat = cuadros['Inversión por categoría']['tabla']
    assert cat['inversion_pen'].sum() == pytest.approx(1900.0)
    assert all(c['lectura'] for c in cuadros.values())
    ruta = tb.exportar_tablero(datos, str(tmp_path / 'tablero.xlsx'))
    wb = openpyxl.load_workbook(ruta)
    assert wb.sheetnames[0] == 'Resumen' and len(wb.sheetnames) == len(cuadros) + 1
    assert all(len(wb[h]._images) == 1 for h in wb.sheetnames[1:])


def test_info_ciclo_usa_el_calendario_fijo(datos):
    from preve_config import Config
    Config.actual().set('calendario_inicio', '2026-01-01')
    ci = datos.info_ciclo(hoy=D(2026, 11, 9))
    assert (ci['numero'], ci['dia'], ci['limite']) == (3, 103, D(2026, 11, 11))


# ── reinicio total ─────────────────────────────────────────────
def test_reiniciar_todo_borra_la_base_aunque_este_abierta(tmp_path, monkeypatch):
    from database import Database
    from preve_config import Config
    app = tmp_path / 'app'
    (app / 'salida' / 'articulo' / 'figuras').mkdir(parents=True)
    (app / 'salida' / 'articulo' / 'tablas_articulo.xlsx').write_text('x')
    monkeypatch.setattr(dep, 'APP_DIR', str(app))
    Database.cerrar_todas()
    Config.reiniciar()
    monkeypatch.setenv('PREVE_DB_URL', 'sqlite:///' + str(app / 'preve.db').replace('\\', '/'))
    conf = Config.actual()                              # conexión abierta, como en la app
    conf.set('usuario_nombre', 'Ana')
    rep = dep.depurar_datos(conservar_config=False, hacer_respaldo=True, carpetas_salida=[])
    assert not rep['errores'], rep['errores']
    assert not (app / 'preve.db').exists() and not (app / 'salida' / 'articulo').exists()
    Config.reiniciar()
    assert Config.actual().get('usuario_nombre') == 'Usuario'          # la base se recrea limpia


def test_borrar_datos_reinicia_el_ciclo_pero_conserva_la_configuracion(tmp_path, monkeypatch, carpeta_salida):
    from database import Database
    from preve_config import Config
    app = tmp_path / 'app'
    app.mkdir()
    monkeypatch.setattr(dep, 'APP_DIR', str(app))
    Database.cerrar_todas()
    Config.reiniciar()
    monkeypatch.setenv('PREVE_DB_URL', 'sqlite:///' + str(app / 'preve.db').replace('\\', '/'))
    Config.actual().set('calendario_inicio', '2026-01-01')
    pc.registrar_importacion(carpeta_salida, fecha=D(2026, 5, 1), sucursal='San José', duracion_dias=105)
    assert pc.cargar_estado_ciclo(carpeta_salida, 'San José') is not None
    rep = dep.depurar_datos(conservar_config=True, hacer_respaldo=False, carpetas_salida=[carpeta_salida])
    assert not rep['errores'], rep['errores']
    Config.reiniciar()
    assert pc.cargar_estado_ciclo(carpeta_salida, 'San José') is None
    assert Config.actual().get('calendario_inicio') == '2026-01-01'
