# -*- coding: utf-8 -*-
"""ICD, valorización económica, bootstrap y Diebold-Mariano (módulos del artículo)."""
import numpy as np
import pandas as pd
import pytest

import analisis_censura as ac
import preve_articulo as pa


def test_icd_serie_definicion():
    ventas = [0, 0, 3, 0, 5, 0]
    sin_stock = [True, False, False, True, False, False]
    assert ac.icd_serie(ventas, sin_stock) == (2, 4, 0.5)
    assert np.isnan(ac.icd_serie([1, 2], [True, True])[2])


def _por_sku():
    return pd.DataFrame({'sku': ['a', 'b', 'c'], 'clase_abc': ['A', 'A', 'C'],
                         'segmento_original': ['Suave', 'Lumpy', 'Lumpy'],
                         'semanas_sin_venta': [4, 0, 6], 'semanas_censuradas': [2, 0, 6]})


def test_icd_agregado_ponderado():
    r = ac.indice_ceguera(_por_sku(), sucursal='X')
    assert r['empresa']['icd'].iloc[0] == pytest.approx(8 / 10)       # Σ num / Σ den, no promedio
    abc = r['por_clase_abc'].set_index('clase_abc')
    assert abc.loc['A', 'icd'] == pytest.approx(0.5)
    assert abc.loc['A', 'skus_con_icd'] == 1                         # 'b' sin semanas sin venta → NaN
    assert r['distribucion']['skus'].sum() == 2
    assert set(r['por_sku']['sucursal']) == {'X'}


def test_icd_desde_analizar_censura():
    sem = pd.date_range('2025-01-06', periods=6, freq='W-MON')
    panel = pd.DataFrame({'sku': 'a', 'semana_inicio': sem, 'unidades_venta_neta': [2, 0, 0, 3, 0, 1]})
    dias = pd.date_range(sem[0], sem[-1] + pd.Timedelta(days=6))
    st = pd.Series(5.0, index=dias)
    st[(dias >= sem[1]) & (dias < sem[2])] = 0                     # semana 2 sin stock
    r = ac.analizar_censura(panel, pd.DataFrame({'a': st}))
    icd = ac.indice_ceguera(r['por_sku'])
    assert icd['empresa']['icd'].iloc[0] == pytest.approx(1 / 3)


def test_valorizacion_y_resumen():
    compras = pd.DataFrame({'sku': ['a', 'b'], 'real_horizonte_estimado': [10.0, 4.0], 'stock_actual': [2.0, 0.0],
                            'compra_sugerida_unid': [5.0, 8.0], 'faltante': [3.0, 0.0], 'sobrante': [0.0, 4.0],
                            'dia_reporte': pd.Timestamp('2025-06-01')})
    dim = pd.DataFrame({'sku': ['a', 'b'], 'costo_puesto_almacen_pen': [10.0, np.nan],
                        'precio_regular_pen': [23.6, 50.0]})
    c = pa.valorizar_compras(compras, dim, igv_factor=1.18)
    assert c.loc[0, 'margen_unit'] == pytest.approx(10.0)
    assert c.loc[0, 'venta_perdida_pen'] == pytest.approx(30.0)
    assert not c.loc[1, 'valorizacion_disponible']                  # sin costo: no se imputa
    r = pa.resumen_economico(c)
    assert r['nivel_servicio_pct'] == pytest.approx((7 + 4) / 14 * 100)
    assert r['exceso_pen'] == 0 and r['skus_sin_valorizacion'] == 1


def _detalle(rng, n_ventanas=12):
    filas = []
    for t in pd.date_range('2025-01-06', periods=n_ventanas, freq='14D'):
        for s in range(20):
            real = rng.poisson(5)
            filas += [dict(modelo='bueno', periodo_inicio=t, sku=s, real=real, pronostico=real + rng.normal(0, 1)),
                      dict(modelo='malo', periodo_inicio=t, sku=s, real=real, pronostico=real + rng.normal(3, 2))]
    return pd.DataFrame(filas)


def test_bootstrap_reproducible_y_contiene_estimador():
    d = _detalle(np.random.default_rng(0))
    a, b = pa.bootstrap_wmape(d, n=300, semilla=1), pa.bootstrap_wmape(d, n=300, semilla=1)
    pd.testing.assert_frame_equal(a, b)
    assert ((a['ic_inf'] <= a['wmape']) & (a['wmape'] <= a['ic_sup'])).all()


def test_significancia_detecta_diferencia():
    t = pa.comparar_significancia(_detalle(np.random.default_rng(0)), 'bueno', n=300)
    r = t.iloc[0]
    assert r['baseline'] == 'malo' and r['mejora_media_wmape_pp'] > 0
    assert r['dm_p'] < 0.01 and r['wilcoxon_p'] < 0.01 and r['dif_wmape_ic_inf'] > 0


def test_diebold_mariano_sin_diferencia():
    stat, p = pa.diebold_mariano(np.random.default_rng(3).normal(0, 1, 200))
    assert p > 0.01
    assert np.isnan(pa.diebold_mariano([1, 1, 1])[0])
