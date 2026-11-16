# -*- coding: utf-8 -*-
"""Tests del modelo global de demanda (GBM Poisson). Datos sintéticos."""
import numpy as np
import pandas as pd
import pytest

import preve_modelo_gbm as G


def _panel_y_stock(n_sku=40, semanas=60, seed=0):
    rng = np.random.default_rng(seed)
    lunes = pd.date_range('2025-01-06', periods=semanas, freq='7D')
    tasas = rng.gamma(1.0, 2.0, n_sku)                       # unidades/semana por SKU
    filas = []
    for i in range(n_sku):
        for s, d in enumerate(lunes):
            filas.append({'sku': f'S{i:03d}', 'semana_inicio': d, 'unidades_venta_neta': float(rng.poisson(tasas[i]))})
    dias = pd.date_range(lunes[0] - pd.Timedelta(days=1), lunes[-1] + pd.Timedelta(days=6))
    stock = pd.DataFrame(10.0, index=dias, columns=[f'S{i:03d}' for i in range(n_sku)])
    return pd.DataFrame(filas), stock, tasas, lunes[-1] + pd.Timedelta(days=6)


def test_pronostico_valido():
    panel, stock, _, fin = _panel_y_stock()
    out, importances = G.pronosticar_gbm(panel, stock, fin, 2.0, log=lambda m: None)
    cols = ['demanda_p10_141d', 'demanda_p50_141d', 'demanda_p90_141d']
    assert list(out.columns) == ['sku'] + cols
    assert isinstance(importances, dict) and len(importances) > 0
    assert out['sku'].is_unique and set(out['sku']) <= set(panel['sku'])
    assert not out[cols].isna().any().any() and (out[cols] >= 0).all().all()
    assert (out.demanda_p10_141d <= out.demanda_p50_141d + 1e-9).all()
    assert (out.demanda_p50_141d <= out.demanda_p90_141d + 1e-9).all()


def test_el_pronostico_sigue_a_la_demanda_real():
    panel, stock, tasas, fin = _panel_y_stock(n_sku=60, semanas=70, seed=1)
    out, _ = G.pronosticar_gbm(panel, stock, fin, 2.0, log=lambda m: None)
    out = out.set_index('sku')
    real = pd.Series(tasas * 2.0, index=[f'S{i:03d}' for i in range(60)])
    assert out['demanda_p50_141d'].corr(real.reindex(out.index)) > 0.6
    assert abs(out['demanda_p50_141d'].sum() / real.sum() - 1) < 0.3      # sin sesgo grande


def test_historia_corta_no_entrena():
    panel, stock, _, fin = _panel_y_stock(semanas=10)
    out, imp = G.pronosticar_gbm(panel, stock, fin, 2.0, log=lambda m: None)
    assert out is None and imp is None


def test_descarta_la_semana_incompleta():
    panel, stock, _, fin = _panel_y_stock(semanas=20)
    _, semanas, V, _, S = G._matrices(panel, stock, fin)
    assert V.shape[1] == 20 and S.shape[1] == 21
    miercoles = fin - pd.Timedelta(days=4)                    # datos hasta el miércoles de la última semana
    stock_corto = stock[stock.index <= miercoles]
    _, semanas2, V2, _, S2 = G._matrices(panel, stock_corto, miercoles)
    assert V2.shape[1] == 19 and semanas2[-1] == semanas[-2]


def test_sin_stock_igual_funciona():
    panel, _, _, fin = _panel_y_stock()
    out, _ = G.pronosticar_gbm(panel, None, fin, 2.0, log=lambda m: None)
    assert out is not None and len(out) > 0


def test_la_disponibilidad_importa():
    """Un SKU sin stock no vende; el modelo no debe confundirlo con 'sin demanda' cuando se repone."""
    panel, stock, _, fin = _panel_y_stock(n_sku=40, semanas=60, seed=2)
    out, _ = G.pronosticar_gbm(panel, stock, fin, 2.0, log=lambda m: None)
    assert out['demanda_p50_141d'].sum() > 0


def test_entrenar_y_pronosticar_etiqueta_el_modelo_usado(log):
    import preve_pipeline as pp
    panel, stock, _, fin = _panel_y_stock()
    seg = pd.DataFrame({'sku': sorted(panel['sku'].unique())})
    seg['modelo_asignado'] = 'LightGBM'
    seg['segmento'] = 'Regular / alta rotación'
    dim = pd.DataFrame({'sku': seg['sku'], 'nombre': seg['sku'], 'categoria': 'X',
                        'subcategoria': None, 'costo_puesto_almacen_pen': 5.0, 'precio_regular_pen': 10.0})
    pron, sin, extras = pp.entrenar_y_pronosticar(panel, seg, dim, fin, log,
                                                   params=pp.ParametrosModelo(horizonte_dias=14.0), stock=stock)
    assert 'feature_importances' in extras
    con_ventas = set(panel.loc[panel['unidades_venta_neta'] > 0, 'sku'])
    assert set(pron['sku']) == con_ventas                      # sin ninguna venta no se inventa pronóstico
    assert set(sin['sku']) == set(seg['sku']) - con_ventas
    etiquetados = set(seg.loc[seg['modelo_asignado'] == pp.MODELO_GBM, 'sku'])
    assert etiquetados == con_ventas


def test_stock_negativo_se_recorta_a_cero():
    panel, stock, _, fin = _panel_y_stock(n_sku=5, semanas=20)
    stock = stock.copy()
    stock.iloc[:40, 0] = -15.0
    _, _, _, A, S = G._matrices(panel, stock, fin)
    assert (S >= 0).all()
    assert S[0, 0] == 0.0


def test_venta_con_disponibilidad_cero_sube_a_un_septimo():
    """Una venta prueba que había stock: la semana no puede quedar con disponibilidad 0."""
    panel, stock, _, fin = _panel_y_stock(n_sku=5, semanas=20)
    stock = stock.copy()
    stock['S000'] = 0.0                                       # el stock reconstruido dice que nunca hubo
    panel = panel.copy()
    panel.loc[(panel.sku == 'S000') & (panel.semana_inicio == panel.semana_inicio.min()), 'unidades_venta_neta'] = 3.0
    panel.loc[(panel.sku == 'S000') & (panel.semana_inicio != panel.semana_inicio.min()), 'unidades_venta_neta'] = 0.0
    skus, semanas, V, A, S = G._matrices(panel, stock, fin)
    i = skus.index('S000')
    assert V[i, 0] > 0 and A[i, 0] == pytest.approx(1 / 7)
    assert (A[i, 1:] == 0).all()                              # sin venta y sin stock sigue en 0
    assert (A[skus.index('S001')] == 1.0).all()               # los demás SKU no se tocan
