# -*- coding: utf-8 -*-
"""Tests de las correcciones de datos (fecha del listado, disponibilidad robusta, pedidos atípicos)."""
import pandas as pd
import pytest

import preve_pipeline as pp


def test_fecha_desde_nombre_ignora_el_ruc():
    assert pp.fecha_desde_nombre('listado_productos_20603376162_20261106.xlsx') == pd.Timestamp(2026, 11, 6)


def test_fecha_desde_nombre_con_guiones():
    assert pp.fecha_desde_nombre('reporte_2026-11-06.xlsx') == pd.Timestamp(2026, 11, 6)


def test_fecha_desde_nombre_solo_ruc_es_none():
    assert pp.fecha_desde_nombre('x_20603376162.xlsx') is None


# ══════════════════════════════════════════════════════════════════
# Pedidos atípicos (mayoristas)
# ══════════════════════════════════════════════════════════════════
def _escenario(log, **kw):
    from tests.test_fase1 import _compras, _listado, _mapas, _ventas
    dim, mapa_df = pp.construir_dim_producto(_listado([('111', 'A', 100, 1)]), log)
    lineas = [('111', 'A', f'{d:02d}-01-2025 10:00', 2) for d in range(1, 11)]       # mediana 2 u por línea
    lineas.append(('111', 'A', '11-01-2025 10:00', 40))                               # 40 >= 20 y >= 10 × 2
    v = _ventas(lineas)
    c = _compras([('111', 'A', '01-01-2025', 1)])
    m = pd.DataFrame(columns=['PRODUCTO', 'FECHA', 'CANTIDAD', 'TIPO DE MOVIMIENTO', 'ORIGEN', 'DESTINO'])
    return dim, pp.reconstruir_stock(v, c, m, dim, *_mapas(dim, mapa_df), pd.Timestamp('2025-01-11'), log, **kw)


def test_linea_atipica_no_cambia_el_stock_y_se_separa(log):
    dim, (stock, vd, _) = _escenario(log)
    dim2, (stock_sin, vd_sin, _) = _escenario(log, pedido_atipico_min_unidades=10 ** 6)     # nada es atípico
    assert stock['111'].tolist() == stock_sin['111'].tolist()              # el stock se calcula igual
    assert vd['mov'].sum() == vd_sin['mov'].sum() == -60                    # mov sigue siendo el total
    fila = vd[vd['fecha'] == '2025-01-11'].iloc[0]
    assert fila['mov'] == -40 and fila['mov_atipico'] == -40
    assert vd.loc[vd['fecha'] != '2025-01-11', 'mov_atipico'].eq(0).all()
    assert vd_sin['mov_atipico'].eq(0).all()
    at = pd.DataFrame(vd.attrs['pedidos_atipicos'])
    assert len(at) == 1 and at.iloc[0]['unidades'] == 40 and at.iloc[0]['producto'] == 'A'
    pd.concat([vd, vd])                                                     # los attrs no deben romper concat


def test_linea_atipica_reduce_la_demanda_del_panel(log):
    from preve_config import ParametrosModelo
    dim, (stock, vd, _) = _escenario(log)
    panel_on, *_ = pp.construir_panel_y_segmentacion(stock, vd, dim, log, params=ParametrosModelo())
    panel_off, *_ = pp.construir_panel_y_segmentacion(
        stock, vd, dim, log, params=ParametrosModelo(excluir_atipicos_de_la_demanda=False))
    assert panel_on['unidades_venta_neta'].sum() == 20
    assert panel_off['unidades_venta_neta'].sum() == 60                     # con la opción en False, igual que antes
    # datos antiguos sin la columna: se comporta como siempre
    panel_viejo, *_ = pp.construir_panel_y_segmentacion(stock, vd.drop(columns='mov_atipico'), dim, log,
                                                        params=ParametrosModelo())
    assert panel_viejo['unidades_venta_neta'].sum() == 60


def test_linea_grande_pero_normal_para_el_producto_no_es_atipica():
    skus = pd.Series(['X'] * 5)
    marca, _ = pp.marcar_pedidos_atipicos(skus, [25, 30, 22, 28, 24])      # todas >= 20 pero ninguna 10× la mediana
    assert not marca.any()
    marca, _ = pp.marcar_pedidos_atipicos(pd.Series(['X'] * 6), [1, 1, 1, 1, 1, 30])
    assert marca.tolist() == [False] * 5 + [True]


# ══════════════════════════════════════════════════════════════════
# Backtesting: atípicos, WMAPE etiquetados y "con stock"
# ══════════════════════════════════════════════════════════════════
def _bt(tmp_path, log, excluir=True, con_atipico=True):
    from preve_config import ParametrosModelo
    from tests.test_fase6 import PronosticadorFalso, datos_sinteticos
    import preve_backtesting as bt
    d = datos_sinteticos()
    d.ventas_diarias = d.ventas_diarias.assign(mov_atipico=0.0)
    if con_atipico:                                         # 30 de las 2 u/día del SKU A del 2025-02-12 son un mayorista
        d.ventas_diarias.loc[(d.ventas_diarias.sku == 'A') & (d.ventas_diarias.fecha == '2025-02-12'),
                             ['mov', 'mov_atipico']] = [-32.0, -30.0]
    d.stock = d.stock.copy()
    d.stock['C'] = 0.0                                      # C sin stock en todo el período
    return bt.Backtester(d, ParametrosModelo(excluir_atipicos_de_la_demanda=excluir), ventana_dias=14,
                         pronosticador=PronosticadorFalso(), carpeta_salida=str(tmp_path / 'r'), log=log)


def test_backtesting_separa_atipicos_y_con_stock(tmp_path, log):
    b = _bt(tmp_path, log)
    df, _ = b.evaluar_ventana(pd.Timestamp('2025-02-10'), pd.Timestamp('2025-02-24'))
    a = df[df['sku'] == 'A'].iloc[0]
    assert a['real_total'] == 28 + 30 and a['atipico'] == 30 and a['real'] == 28    # real = sin atípicos
    assert bool(a['con_stock']) and not bool(df[df['sku'] == 'C'].iloc[0]['con_stock'])


def test_backtesting_con_la_opcion_en_false_todo_igual(tmp_path, log):
    b = _bt(tmp_path, log, excluir=False)
    df, _ = b.evaluar_ventana(pd.Timestamp('2025-02-10'), pd.Timestamp('2025-02-24'))
    a = df[df['sku'] == 'A'].iloc[0]
    assert a['real'] == a['real_total'] == 58 and a['atipico'] == 30


def test_backtesting_reporta_tres_wmape_etiquetados(tmp_path, log):
    import preve_backtesting as bt
    res = _bt(tmp_path, log).run('2025-02-01', '2025-05-31', simular_compras=False)
    g = res['global']
    for k in ('wmape', 'wmape_total', 'wmape_con_stock'):
        assert k in g
    assert g['wmape'] != g['wmape_total']                   # el total incluye el mayorista: no se mezclan
    df = res['detalle']
    esperado_total = (df['pronostico'] - df['real_total']).abs().sum() / df['real_total'].sum() * 100
    esperado_detalle = (df['pronostico'] - df['real']).abs().sum() / df['real'].sum() * 100
    assert g['wmape_total'] == pytest.approx(esperado_total) and g['wmape'] == pytest.approx(esperado_detalle)
    sc = df[df['con_stock']]
    assert g['wmape_con_stock'] == pytest.approx((sc['pronostico'] - sc['real']).abs().sum() / sc['real'].sum() * 100)
    general = pd.read_excel(res['ruta_excel'], sheet_name='Resumen General', header=None)
    metricas = general[0].astype(str).tolist()
    ix = {n: next(i for i, t in enumerate(metricas) if t.startswith(n))
          for n in (bt.ETIQ_WMAPE, bt.ETIQ_WMAPE_TOTAL, bt.ETIQ_WMAPE_STOCK, 'MAPE promedio')}
    assert ix[bt.ETIQ_WMAPE] < ix[bt.ETIQ_WMAPE_TOTAL] < ix[bt.ETIQ_WMAPE_STOCK] < ix['MAPE promedio']
    assert 'poco confiable' in metricas[ix['MAPE promedio']]


def test_config_expone_los_parametros_de_pedidos_atipicos():
    from preve_config import DEFAULTS, ParametrosModelo
    assert DEFAULTS['pedido_atipico_min_unidades'][:2] == (20, 'int')
    assert DEFAULTS['pedido_atipico_factor'][:2] == (10.0, 'float')
    assert DEFAULTS['excluir_atipicos_de_la_demanda'][:2] == (True, 'bool')
    p = ParametrosModelo()
    assert (p.pedido_atipico_min_unidades, p.pedido_atipico_factor, p.excluir_atipicos_de_la_demanda) == (20, 10.0, True)


def test_pedidos_atipicos_90_dias_van_al_csv_y_al_informe(tmp_path, log):
    import preve_informe as inf
    import preve_orquestador as orq
    dim, (stock, vd, _) = _escenario(log)
    reg = vd.attrs.pop('pedidos_atipicos')
    reg[0]['cliente'] = 'ASOCIACIÓN X'
    orq._guardar_pedidos_atipicos(reg, pd.Timestamp('2025-01-11'), str(tmp_path), log)
    df = inf._leer_pedidos_atipicos(str(tmp_path))
    assert list(df['unidades']) == [40] and df.iloc[0]['cliente'] == 'ASOCIACIÓN X'
    # fuera de la ventana de 90 días no se lista
    orq._guardar_pedidos_atipicos(reg, pd.Timestamp('2025-09-01'), str(tmp_path), log)
    assert len(inf._leer_pedidos_atipicos(str(tmp_path))) == 0
    assert inf._leer_pedidos_atipicos(str(tmp_path / 'no_existe')) is None


def test_pronostico_siempre_numerico(log):
    """Si el pronóstico queda vacío o mezclado, las columnas deben ser float (antes object y .round() fallaba)."""
    panel = pd.DataFrame({'sku': ['A'], 'semana_inicio': [pd.Timestamp('2025-01-06')], 'unidades_venta_neta': [0.0]})
    seg = pd.DataFrame({'sku': ['A'], 'modelo_asignado': ['Croston-TSB'], 'segmento': ['Sin historia suficiente']})
    dim = pd.DataFrame({'sku': ['A'], 'nombre': ['A'], 'categoria': 'X', 'subcategoria': None,
                        'costo_puesto_almacen_pen': 5.0, 'precio_regular_pen': 10.0})
    pron, _ = pp.entrenar_y_pronosticar(panel, seg, dim, pd.Timestamp('2025-01-12'), log,
                                       params=pp.ParametrosModelo(horizonte_dias=14.0))
    assert all(str(pron[c].dtype) == 'float64' for c in pron.columns if c != 'sku')
