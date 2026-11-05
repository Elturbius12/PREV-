# -*- coding: utf-8 -*-
"""Tests de regresión de la Fase 1: cada test reproduce un bug de AUDITORIA.md.
Todos los datos son sintéticos."""
import json
import os

import numpy as np
import pandas as pd
import pytest


# ══════════════════════════════════════════════════════════════════
# Datos sintéticos con los mismos nombres de columna que exporta Negosy
# ══════════════════════════════════════════════════════════════════
def _listado(filas):
    return pd.DataFrame([{'CODIGO BARRAS': bc, 'PRODUCTO': nom, 'STOCK': st, 'PRECIO COMPRA': 5.0,
                          'PRECIO VENTA': 11.8, 'CATEGORÍA': 'X', 'SUBCATEGORÍA': 'Y', '__mtime': mt}
                         for bc, nom, st, mt in filas])


def _ventas(filas):
    return pd.DataFrame([{'CÓDIGO DE BARRA': bc, 'PRODUCTO': nom, 'FECHA/HORA': f, 'CANTIDAD': q}
                         for bc, nom, f, q in filas])


def _compras(filas):
    return pd.DataFrame([{'COD. BARRA': bc, 'PRODUCTO': nom, 'FECHA': f, 'CANTIDAD': q}
                         for bc, nom, f, q in filas])


def _movs(filas):
    return pd.DataFrame([{'PRODUCTO': nom, 'FECHA': f, 'CANTIDAD': q, 'TIPO DE MOVIMIENTO': t,
                          'ORIGEN': o, 'DESTINO': d} for nom, f, q, t, o, d in filas])


def _mapas(dim, mapa_df):
    from preve_pipeline import construir_mapas
    return construir_mapas(dim, mapa_df)


# ══════════════════════════════════════════════════════════════════
# 1.1 Duplicación de datos entre exportaciones solapadas
# ══════════════════════════════════════════════════════════════════
def test_1_1_exportaciones_solapadas_no_duplican():
    from preve_pipeline import deduplicar_entre_archivos
    ene = _ventas([('A', 'P', '01-01-2025 10:00', 2), ('A', 'P', '02-01-2025 10:00', 3)])
    ene_feb = _ventas([('A', 'P', '01-01-2025 10:00', 2), ('A', 'P', '02-01-2025 10:00', 3),
                       ('A', 'P', '03-02-2025 10:00', 4)])
    res = deduplicar_entre_archivos([ene, ene_feb])
    assert len(res) == 3
    assert res['CANTIDAD'].sum() == 9


def test_1_1_lineas_identicas_del_mismo_archivo_se_conservan():
    """Dos líneas iguales en un mismo ticket son ventas reales, no duplicados."""
    from preve_pipeline import deduplicar_entre_archivos
    f = _ventas([('A', 'P', '01-01-2025 10:00', 1), ('A', 'P', '01-01-2025 10:00', 1)])
    assert len(deduplicar_entre_archivos([f, f.copy()])) == 2


def test_1_1_extractor_usa_subcarpeta_con_timestamp(tmp_path):
    from preve_extractor import carpeta_descarga
    ruta = carpeta_descarga(str(tmp_path))
    import re
    assert os.path.dirname(ruta) == str(tmp_path)
    assert os.path.isdir(ruta)
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}', os.path.basename(ruta))


# ══════════════════════════════════════════════════════════════════
# 1.2 Target de LightGBM
# ══════════════════════════════════════════════════════════════════
def test_1_2_target_es_suma_futura_sin_solape():
    from preve_pipeline import construir_target_horizonte
    s = pd.Series(np.arange(1, 31, dtype=float))         # semana t vende t+1
    y = construir_target_horizonte(s, 5)
    # en t=0 el target debe ser semanas 1..5 → valores 2+3+4+5+6
    assert y.iloc[0] == 2 + 3 + 4 + 5 + 6
    # las últimas 5 semanas no tienen futuro completo → NaN (no targets parciales)
    assert y.iloc[-5:].isna().all()
    assert y.iloc[-6] == s.iloc[-5:].sum()


def test_1_2_features_incluyen_semana_actual():
    from preve_pipeline import construir_features_lgb
    sub = pd.DataFrame({'sku': ['a'] * 6, 'semana_inicio': pd.date_range('2025-01-06', periods=6, freq='W-MON'),
                        'unidades_venta_neta': [1., 2, 3, 4, 5, 6]})
    feats, cols = construir_features_lgb(sub)
    assert feats['ventas_4s'].iloc[-1] == 3 + 4 + 5 + 6


# ══════════════════════════════════════════════════════════════════
# 1.3 Scorecard
# ══════════════════════════════════════════════════════════════════
def _rec(p50=141.0, stock=10.0, compra=50.0, precio=11.8):
    return pd.DataFrame({'sku': ['A'], 'demanda_p50_141d': [p50], 'demanda_p10_141d': [p50 * 0.5],
                         'demanda_p90_141d': [p50 * 1.5], 'stock_actual': [stock],
                         'compra_sugerida_unid': [compra], 'accion': ['RECOMPRAR'], 'clase_abc': ['A'],
                         'costo_puesto_almacen_pen': [5.0], 'precio_regular_pen': [precio]})


def _ventas_diarias(desde, dias, por_dia=1.0):
    fechas = pd.date_range(desde, periods=dias, freq='D')
    return pd.DataFrame({'sku': 'A', 'fecha': fechas, 'mov': -por_dia})


def test_1_3_validacion_usa_mov_y_solo_periodo_nuevo(carpeta_salida, log):
    from preve_orquestador import _intentar_validacion
    # 1ª corrida: guarda la referencia con fecha 2025-01-10
    _intentar_validacion(_rec(), _ventas_diarias('2024-01-01', 376), carpeta_salida, log,
                         fecha_datos=pd.Timestamp('2025-01-10'))
    # 2ª corrida 14 días después: el historial completo tiene 390 días de venta
    res = _intentar_validacion(_rec(), _ventas_diarias('2024-01-01', 390), carpeta_salida, log,
                               fecha_datos=pd.Timestamp('2025-01-24'))
    assert res is not None, log.mensajes
    det = pd.read_excel(os.path.join(carpeta_salida, 'PREVE_Scorecard.xlsx'), sheet_name='Detalle por SKU')
    assert det['Venta real'].iloc[0] == 14            # solo las ventas del período, en positivo
    # C6: pronóstico prorrateado a 14 de 141 días → 141*14/141 = 14 → error 0
    assert det['Pronóstico'].iloc[0] == pytest.approx(14.0)
    assert res['agregadas']['wmape'] == 0


def test_1_3_sin_referencia_fechada_no_valida(carpeta_salida, log):
    from preve_orquestador import _intentar_validacion
    _rec().to_csv(os.path.join(carpeta_salida, 'recomendacion_anterior.csv'), index=False)   # formato antiguo
    res = _intentar_validacion(_rec(), _ventas_diarias('2025-01-01', 30), carpeta_salida, log,
                               fecha_datos=pd.Timestamp('2025-01-30'))
    assert res is None


def test_a14_scorecard_viejo_no_se_reutiliza(carpeta_salida, log):
    from preve_orquestador import _intentar_validacion
    with open(os.path.join(carpeta_salida, 'scorecard_ultimo.json'), 'w') as f:
        json.dump({'agregadas': {'wmape': 99}}, f)
    _intentar_validacion(_rec(), _ventas_diarias('2025-01-01', 10), carpeta_salida, log,
                         fecha_datos=pd.Timestamp('2025-01-10'))
    assert not os.path.exists(os.path.join(carpeta_salida, 'scorecard_ultimo.json'))


# ══════════════════════════════════════════════════════════════════
# 1.4 ROI
# ══════════════════════════════════════════════════════════════════
def test_1_4_roi_positivo_con_ventas_recuperadas(log):
    from preve_validacion import calcular_metricas_pronostico, analisis_contrafactual, estimar_roi
    rec = _rec(stock=10, compra=50, precio=11.8)
    met, _ = calcular_metricas_pronostico(rec, pd.DataFrame({'sku': ['A'], 'venta_real': [40.0]}), log)
    assert 'precio_regular_pen' in met.columns
    contra, resumen = analisis_contrafactual(met, log)
    roi = estimar_roi(met, contra, resumen, log)
    # 30 unidades salvadas × (11.8/1.18 − 5) = 30 × 5 = 150
    assert roi['ventas_recuperadas_margen'] == pytest.approx(150.0)
    assert roi['roi_pct'] > 0


# ══════════════════════════════════════════════════════════════════
# 1.5 Stock en tránsito, ROP y cantidad a pedir
# ══════════════════════════════════════════════════════════════════
def _entradas_recomendacion(stock_val=0.0):
    pron = pd.DataFrame({'sku': ['A'], 'demanda_p10_141d': [100.0], 'demanda_p50_141d': [141.0],
                         'demanda_p90_141d': [141.0 + 1.2816 * 10]})
    seg = pd.DataFrame({'sku': ['A'], 'clase_abc': ['A'], 'modelo_asignado': ['LightGBM'],
                        'segmento': ['Regular / alta rotación']})
    dim = pd.DataFrame({'sku': ['A'], 'nombre': ['P'], 'categoria': ['X'], 'costo_puesto_almacen_pen': [5.0],
                        'precio_regular_pen': [11.8]})
    fechas = pd.date_range('2025-01-01', periods=3)
    stock = pd.DataFrame({'A': [stock_val] * 3}, index=fechas)
    panel = pd.DataFrame({'sku': ['A'] * 3, 'semana_inicio': pd.date_range('2024-12-16', periods=3, freq='W-MON'),
                          'unidades_venta_neta': [7.0, 7.0, 7.0]})
    return pron, seg, dim, stock, panel, fechas[-1]


def test_1_5_rop_y_nivel_objetivo_son_distintos(log):
    from preve_pipeline import calcular_recomendacion
    rec = calcular_recomendacion(*_entradas_recomendacion(), log, lead_time_dias=36)
    r = rec.iloc[0]
    ss = r['stock_seguridad']
    assert r['demanda_lead_time'] == pytest.approx(36.0)        # 141 u en 141 días → 1/día
    assert r['rop'] == pytest.approx(36.0 + ss)
    assert r['nivel_objetivo'] == pytest.approx(141.0 + ss)
    assert r['cantidad_a_pedir'] == round(141.0 + ss)
    assert r['compra_sugerida_unid'] == r['cantidad_a_pedir']    # alias para la UI


def test_1_5_stock_en_transito_reduce_la_compra(log):
    from preve_pipeline import calcular_recomendacion
    sin = calcular_recomendacion(*_entradas_recomendacion(), log).iloc[0]
    con = calcular_recomendacion(*_entradas_recomendacion(), log,
                                 stock_en_transito=pd.Series({'A': 100.0})).iloc[0]
    assert con['stock_en_transito'] == 100
    assert con['cantidad_a_pedir'] == sin['cantidad_a_pedir'] - 100


def test_1_5_ordenes_abiertas_crea_plantilla(carpeta_salida):
    from preve_pipeline import cargar_ordenes_abiertas, NOMBRE_ORDENES_ABIERTAS
    s = cargar_ordenes_abiertas(carpeta_salida)
    assert s.empty
    ruta = os.path.join(carpeta_salida, NOMBRE_ORDENES_ABIERTAS)
    assert os.path.exists(ruta)
    pd.DataFrame({'sku': ['A', 'A', 'B'], 'cantidad': [10, 5, 7], 'fecha_pedido': ['2025-01-01'] * 3,
                  'fecha_estimada_llegada': ['2025-02-01'] * 3,
                  'estado': ['abierta', 'abierta', 'recibida']}).to_csv(ruta, index=False)
    s = cargar_ordenes_abiertas(carpeta_salida)
    assert s.to_dict() == {'A': 15}


# ══════════════════════════════════════════════════════════════════
# 1.6 Doble descuento de stock
# ══════════════════════════════════════════════════════════════════
def _escenario_stock():
    """Listado con 50 u al 03-01. El 03-01 hay venta 10, compra 20 y salida 10.
    El stock guardado es el del cierre de cada día; el del 02-01 es el stock antes de esos movimientos.
    La venta de 0 u el 01-01 solo abre el rango de fechas."""
    from preve_pipeline import construir_dim_producto
    dim, mapa_df = construir_dim_producto(_listado([('111', 'PROD A', 50, 1)]), print)
    ventas = _ventas([('111', 'PROD A', '01-01-2025 09:00', 0), ('111', 'PROD A', '03-01-2025 10:00', 10)])
    compras = _compras([('111', 'PROD A', '03-01-2025', 20)])
    movs = _movs([('PROD A', '03-01-2025 11:00 AM', 10, 'Salida de productos', 'Tienda Juliaca', '')])
    return dim, mapa_df, ventas, compras, movs


ANCLA = pd.Timestamp('2025-01-03')
ANTES = '2025-01-02'


def test_1_6_por_defecto_se_restan_ventas_y_salidas(log):
    from preve_pipeline import reconstruir_stock
    dim, mapa_df, v, c, m = _escenario_stock()
    stock, _, _ = reconstruir_stock(v, c, m, dim, *_mapas(dim, mapa_df), ANCLA, log)
    # antes: 50 - (-10 venta +20 compra -10 salida) = 50
    assert stock.loc[ANTES, '111'] == 50


def test_1_6_ventas_son_salidas_no_descuenta_dos_veces(log):
    from preve_pipeline import reconstruir_stock
    dim, mapa_df, v, c, m = _escenario_stock()
    stock, ventas_d, _ = reconstruir_stock(v, c, m, dim, *_mapas(dim, mapa_df), ANCLA,
                                           log, ventas_son_salidas_de_almacen=True)
    # solo cuentan compra (+20) y salida (-10): antes había 40
    assert stock.loc[ANTES, '111'] == 40
    assert ventas_d['mov'].sum() == -10        # la demanda sigue saliendo de las ventas


def test_c11_compras_son_entradas_no_suma_dos_veces(log):
    from preve_pipeline import reconstruir_stock
    dim, mapa_df, v, c, _ = _escenario_stock()
    m = _movs([('PROD A', '03-01-2025 11:00 AM', 20, 'Entrada', '', 'Tienda Juliaca')])
    stock, _, _ = reconstruir_stock(v, c, m, dim, *_mapas(dim, mapa_df), ANCLA,
                                    log, compras_son_entradas_de_almacen=True)
    assert stock.loc[ANTES, '111'] == 50 - (-10 + 20)


def test_1_6_detecta_posible_doble_descuento(log):
    from preve_pipeline import reconstruir_stock
    dim, mapa_df, v, c, _ = _escenario_stock()
    m = _movs([('PROD A', '03-01-2025 11:00 AM', 10, 'Salida', 'Tienda Juliaca', '')])
    reconstruir_stock(v, c, m, dim, *_mapas(dim, mapa_df), ANCLA, log)
    assert any('ventas_son_salidas_de_almacen' in x for x in log.mensajes)


# ══════════════════════════════════════════════════════════════════
# 1.7 Historial
# ══════════════════════════════════════════════════════════════════
def _rec_hist(accion, compra, nombre='PROD A'):
    return pd.DataFrame({'sku': ['A'], 'nombre': [nombre], 'accion': [accion],
                         'compra_sugerida_unid': [compra], 'costo_total_compra_pen': [compra * 5.0]})


def test_1_7_buscar_devuelve_el_ultimo_registro(carpeta_salida, log):
    import preve_historial as h
    h.registrar_ejecucion(carpeta_salida, _rec_hist('RECOMPRAR', 10, 'NOMBRE VIEJO'), log=log)
    h.registrar_ejecucion(carpeta_salida, _rec_hist('LIQUIDAR', 0, 'NOMBRE NUEVO'), log=log)
    r = h.buscar_en_historial(carpeta_salida, 'NOMBRE')
    assert len(r) == 1
    assert (r[0]['accion'], r[0]['nombre'], r[0]['veces']) == ('LIQUIDAR', 'NOMBRE NUEVO', 2)


def test_1_7_comparaciones_filtran_por_sucursal(carpeta_salida, log):
    import preve_historial as h
    h.registrar_ejecucion(carpeta_salida, _rec_hist('RECOMPRAR', 10), sucursal='San José', log=log)
    h.registrar_ejecucion(carpeta_salida, _rec_hist('RECOMPRAR', 99), sucursal='Lima', log=log)
    comp = h.comparar_ultimas(carpeta_salida, sucursal='San José')
    assert comp['previa'] is None
    assert comp['actual']['inversion'] == 50
    assert len(h.ejecuciones_con_diff(carpeta_salida, sucursal='Lima')) == 1
    assert h.ultimo_scorecard(carpeta_salida, sucursal='Lima') is None


# ══════════════════════════════════════════════════════════════════
# 1.8 Extractor
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize('texto,permitido', [
    ('Cerrar caja', False), ('CERRAR', False), ('No', False), ('Aceptar', False), ('Cerrar sesión', False),
    ('Mantener caja abierta', True), ('  continuar sin cerrar ', True), ('Quedarse aquí', True),
])
def test_1_8_modal_caja_lista_blanca_exacta(texto, permitido):
    from preve_extractor import boton_modal_permitido
    assert boton_modal_permitido(texto) is permitido


@pytest.mark.parametrize('data,esperado', [
    ({'url': 'u1'}, 'u1'),
    ({'file_url': 'u2'}, 'u2'),
    ({'result': {'url': 'u3'}}, 'u3'),
    ({'result': 'texto', 'file': 'u4'}, 'u4'),
    ({'url': 'u1', 'result': 'texto', 'file': 'u4'}, 'u1'),     # bug: antes devolvía 'u4'
])
def test_1_8_precedencia_url_celery(data, esperado):
    from preve_extractor import extraer_download_url
    assert extraer_download_url(data) == esperado


# ══════════════════════════════════════════════════════════════════
# 1.9 Robustez
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize('val,esperado', [(3.7, 3), ('5', 5), (np.nan, 0), (None, 0), ('x', 0), (np.inf, 0)])
def test_1_9_safe_int(val, esperado):
    from preve_utils import safe_int
    assert safe_int(val) == esperado


@pytest.mark.parametrize('val,esperado', [(7751234567890.0, '7751234567890'), ('7751234567890.0', '7751234567890'),
                                          (' 0012 ', '0012'), (np.nan, ''), (123, '123')])
def test_1_9_codigo_barras_como_texto(val, esperado):
    from preve_pipeline import normalizar_codigo
    assert normalizar_codigo(val) == esperado


def test_1_9_colision_se_resuelve_antes_de_deduplicar(log):
    from preve_pipeline import construir_dim_producto
    listado = _listado([('111', 'PROD A', 5, 1), ('111', 'PROD B', 7, 1),        # colisión real
                        ('222', 'PROD C', 1, 1), ('222', 'PROD C', 3, 2)])       # mismo producto en 2 archivos
    dim, _ = construir_dim_producto(listado, log)
    assert sorted(dim['sku']) == ['111', '111-2', '222']
    assert dim.set_index('sku').loc['222', 'stock_listado_actual'] == 3          # gana el más reciente


def test_a6_ventas_de_codigo_con_colision_van_al_producto_correcto(log):
    from preve_pipeline import construir_dim_producto, _mapear_por_codigo_barra
    dim, mapa_df = construir_dim_producto(_listado([('111', 'PROD A', 5, 1), ('111', 'PROD B', 7, 1)]), log)
    mapa_bc, mapa_bc_nom, _ = _mapas(dim, mapa_df)
    v = _ventas([('111', 'PROD B', '01-01-2025 10:00', 1), ('111', 'PROD A', '01-01-2025 10:00', 1)])
    res = _mapear_por_codigo_barra(v, 'CÓDIGO DE BARRA', mapa_bc, log, 'Ventas',
                                   col_nombre='PRODUCTO', mapa_bc_nombre=mapa_bc_nom)
    assert list(res['sku']) == ['111-2', '111']


def test_1_9_fecha_ancla_desde_nombre_del_listado():
    from preve_pipeline import fecha_desde_nombre
    assert fecha_desde_nombre('listado_productos_2026-10-2345136 filtrado.xlsx') == pd.Timestamp('2026-10-23')
    assert fecha_desde_nombre('listado_productos_san_jose_20251103.xlsx') == pd.Timestamp('2025-11-03')
    assert fecha_desde_nombre('listado_productos.xlsx') is None


def test_1_9_sin_fecha_ancla_usa_ultima_transaccion(log):
    from preve_pipeline import reconstruir_stock
    dim, mapa_df, v, c, m = _escenario_stock()
    stock, _, _ = reconstruir_stock(v, c, m, dim, *_mapas(dim, mapa_df), None, log)
    assert stock.index.max() == ANCLA


def test_a15_compras_con_otro_formato_de_fecha_no_se_pierden(log):
    from preve_pipeline import reconstruir_stock
    dim, mapa_df, v, _, m = _escenario_stock()
    c = _compras([('111', 'PROD A', '2025-01-03', 20)])       # ISO en vez de dd-mm-YYYY
    stock, _, _ = reconstruir_stock(v, c, m, dim, *_mapas(dim, mapa_df), ANCLA, log)
    assert stock.loc[ANTES, '111'] == 50


def test_1_9_config_corrupto_se_respalda(tmp_path, monkeypatch, log):
    import preve_datos as dt
    ruta = tmp_path / 'preve_config.json'
    ruta.write_text('{esto no es json', encoding='utf-8')
    monkeypatch.setattr(dt, '_CONFIG_PATH', str(ruta))
    cfg = dt.cargar_config()
    assert cfg['usuario'] == dt.config_defecto()['usuario']
    respaldos = [p for p in os.listdir(tmp_path) if p.startswith('preve_config.json.corrupto')]
    assert len(respaldos) == 1
    assert cfg['ventas_son_salidas_de_almacen'] is False


def test_1_9_ciclo_corrupto_no_reinicia(carpeta_salida):
    import preve_ciclo as pc
    ruta = os.path.join(carpeta_salida, 'ciclo_estado.json')
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write('{"ciclo_numero": 7, ')
    with pytest.raises(pc.CicloCorruptoError):
        pc.registrar_importacion(carpeta_salida)
    with open(ruta, encoding='utf-8') as f:
        assert f.read() == '{"ciclo_numero": 7, '          # no se sobrescribió


def test_a12_vista_previa_no_registra_y_mismo_dia_no_duplica(carpeta_salida):
    import datetime
    import preve_ciclo as pc
    hoy = datetime.date(2025, 1, 10)
    pc.registrar_importacion(carpeta_salida, hoy, guardar=False)
    assert pc.cargar_estado_ciclo(carpeta_salida) is None
    pc.registrar_importacion(carpeta_salida, hoy)
    info = pc.registrar_importacion(carpeta_salida, hoy)
    assert info['importacion_num'] == 1


def test_a13_ciclo_vencido_no_se_desplaza(carpeta_salida):
    import datetime
    import preve_ciclo as pc
    pc.registrar_importacion(carpeta_salida, datetime.date(2025, 1, 1))
    info = pc.registrar_importacion(carpeta_salida, datetime.date(2025, 1, 1) + datetime.timedelta(days=110))
    assert info['ciclo_inicio'] == str(datetime.date(2025, 1, 1) + datetime.timedelta(days=105))
    assert info['dia_ciclo'] == 6
    assert info['ciclo_numero'] == 2
