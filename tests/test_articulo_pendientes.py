# -*- coding: utf-8 -*-
"""Pendientes del artículo: residuos (T10), intervalos OOS (M8), exceso evitado (M10), diccionario (T0),
escenarios E0/E1/E2 (T11), corrida canónica, muestra anónima y documentación. Datos sintéticos."""
import json
import os

import numpy as np
import pandas as pd
import pytest

import generar_resultados_articulo as gra
import preve_articulo as pa

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _detalle(rng, n_ventanas=10, n_sku=30, modelos=('A', 'B'), sesgo=(0.0, 2.0)):
    filas = []
    for t in pd.date_range('2025-01-06', periods=n_ventanas, freq='14D'):
        for s in range(n_sku):
            real = float(rng.poisson(8))
            for m, b in zip(modelos, sesgo):
                filas.append(dict(modelo=m, periodo_inicio=t, sku=s, clase_abc='A' if s < 10 else 'C', real=real,
                                  pronostico=max(0.0, real + b + rng.normal(0, 1))))
    return pd.DataFrame(filas)


# ── Tabla 10: residuos ───────────────────────────────────────────
def test_residuos_columnas_y_estadisticos():
    d = _detalle(np.random.default_rng(1))
    t = pa.analisis_residuos(d, ['A', 'B', 'no_existe'])
    assert list(t['modelo']) == ['A', 'B']
    for c in ('shapiro_w', 'shapiro_p', 'ljung_box_Q', 'ljung_box_p', 'media_residuos', 'mediana_residuos',
              'asimetria', 'curtosis', 'n_observaciones'):
        assert c in t.columns
    a, b = t.set_index('modelo').loc['A'], t.set_index('modelo').loc['B']
    assert a['n_observaciones'] == 300 and a['n_ventanas'] == 10
    assert a['ljung_box_lag'] == 5                                   # mín(10, 10 // 2)
    assert b['media_residuos'] == pytest.approx(2.0, abs=0.3)        # residuo = pronóstico − real
    assert 0 <= a['shapiro_p'] <= 1 and 0 <= a['ljung_box_p'] <= 1


def test_residuos_serie_degenerada_no_falla():
    d = pd.DataFrame({'modelo': 'A', 'periodo_inicio': pd.date_range('2025-01-01', periods=2), 'sku': 1,
                      'real': [1.0, 1.0], 'pronostico': [1.0, 1.0]})
    r = pa.analisis_residuos(d, ['A']).iloc[0]
    assert np.isnan(r['shapiro_p']) and np.isnan(r['ljung_box_p']) and r['n_observaciones'] == 2


# ── M8: intervalos fuera de muestra ──────────────────────────────
def test_intervalos_oos_usan_solo_ventanas_anteriores():
    t = pd.date_range('2025-01-06', periods=5, freq='14D')
    resid = [1.0, -2.0, 3.0, 100.0, 0.0]                              # r = real − pronóstico
    d = pd.DataFrame({'modelo': 'A', 'sku': 'x', 'clase_abc': 'A', 'periodo_inicio': t,
                      'pronostico': 10.0, 'real': [10.0 + r for r in resid]})
    o = pa.intervalos_oos(d, min_obs=3).set_index('periodo_inicio')
    assert o['p10_oos'].iloc[:3].isna().all()                        # < 3 residuos previos y sin pool
    r3 = np.array(resid[:3])
    assert o['p10_oos'].iloc[3] == pytest.approx(10 + np.quantile(r3, .1))
    assert o['p90_oos'].iloc[3] == pytest.approx(10 + np.quantile(r3, .9))
    assert o['fuente_intervalo'].iloc[3] == 'sku' and o['n_residuos'].iloc[3] == 3
    # el residuo 100 de la ventana 4 no se usa en su propia ventana, sí en la siguiente
    assert o['p90_oos'].iloc[3] < 20 and o['p90_oos'].iloc[4] > o['p90_oos'].iloc[3]


def test_intervalos_oos_recurren_a_la_clase_abc_y_p10_no_negativo():
    t = pd.date_range('2025-01-06', periods=3, freq='14D')
    filas = [dict(modelo='A', sku=f's{i}', clase_abc='A', periodo_inicio=t[0], pronostico=1.0, real=1.0 + i)
             for i in range(5)]
    filas.append(dict(modelo='A', sku='nuevo', clase_abc='A', periodo_inicio=t[1], pronostico=0.5, real=0.0))
    o = pa.intervalos_oos(pd.DataFrame(filas))
    n = o[o['sku'] == 'nuevo'].iloc[0]
    assert n['fuente_intervalo'] == 'clase_abc' and n['n_residuos'] == 5
    assert n['p10_oos'] >= 0


def test_cobertura_oos_calibrada_mejor_que_intervalo_estrecho():
    rng = np.random.default_rng(3)
    d = _detalle(rng, n_ventanas=12, modelos=('A',), sesgo=(0.0,))
    d['demanda_p10_141d'] = d['pronostico'] - 0.1                     # intervalo "in-sample" demasiado estrecho
    d['demanda_p90_141d'] = d['pronostico'] + 0.1
    c = pa.cobertura_intervalos(pa.intervalos_oos(d)).iloc[0]
    assert c['nominal_pct'] == pytest.approx(80)
    assert abs(c['cobertura_oos_pct'] - 80) < abs(c['cobertura_modelo_pct'] - 80)


def test_cuantiles_residuos_sku_todas_las_ventanas():
    d = pd.DataFrame({'modelo': 'A', 'sku': 'x', 'periodo_inicio': range(5), 'pronostico': 0.0,
                      'real': [1.0, 2.0, 3.0, 4.0, 5.0]})
    q = pa.cuantiles_residuos_sku(d).iloc[0]
    assert q['q_inf'] == pytest.approx(1.4) and q['q_sup'] == pytest.approx(4.6) and q['n_residuos'] == 5


# ── M10: exceso evitado ──────────────────────────────────────────
def _compras(modelo, exceso, skus=('a', 'b'), val=True):
    return pd.DataFrame({'modelo': modelo, 'sucursal': 'S', 'sku': list(skus),
                         'dia_reporte': pd.Timestamp('2025-06-01'), 'exceso_pen': exceso,
                         'valorizacion_disponible': val, 'demanda_obs': 10.0, 'atendido': 9.0,
                         'compra_sugerida_unid': 5.0, 'sobrante': 1.0, 'faltante': 1.0,
                         'venta_perdida_pen': 2.0, 'inversion_pen': 50.0})


def test_exceso_evitado_sobre_base_comun():
    naive = _compras('Naive', [100.0, 50.0])
    prop = _compras('P', [30.0, 20.0, 999.0], skus=('a', 'b', 'solo_prop'))
    r = pa.exceso_evitado(prop, naive)
    assert r['exceso_evitado_pen'] == pytest.approx(150 - 50)       # 'solo_prop' no está en la base común
    assert r['filas_base_comun'] == 2
    fila = pa.resumen_economico(prop, baseline=naive)
    assert fila['exceso_evitado_pen'] == pytest.approx(100)
    assert 'exceso_evitado_pen' not in pa.resumen_economico(prop)    # sin baseline: igual que antes
    assert pa.resumen_economico(naive, baseline=naive)['exceso_evitado_pen'] == 0


def test_exceso_evitado_en_supuestos():
    assert any('exceso_evitado = exceso_baseline − exceso_propuesto' in s for s in pa.SUPUESTOS_VALORIZACION)


# ── Tabla 11: escenarios ─────────────────────────────────────────
def test_escenarios_mapeo_y_mejora():
    assert pa.escenario_de('Naive') == 'E0' and pa.escenario_de('GBM sin disponibilidad') == 'E1'
    assert pa.escenario_de('Pipeline (producción)') == 'E2' and pa.escenario_de('SES') == ''
    t4 = pd.DataFrame({'modelo': ['Naive', 'GBM sin disponibilidad', 'Pipeline (producción)'],
                       'wmape': [80.0, 70.0, 60.0], 'ic_inf': [75.0, 65.0, 55.0], 'ic_sup': [85.0, 75.0, 65.0]})
    cv = pd.concat([_compras('Naive', [100.0, 50.0]), _compras('GBM sin disponibilidad', [60.0, 40.0]),
                    _compras('Pipeline (producción)', [30.0, 20.0, 5.0], skus=('a', 'b', 'c'))])
    t = pa.tabla_escenarios(t4, cv).set_index('escenario')
    assert list(t.index) == ['E0', 'E1', 'E2']
    assert t.loc['E2', 'mejora_wmape_vs_E0_pct'] == pytest.approx(100 * (80 - 60) / 80)
    assert t.loc['E0', 'mejora_wmape_vs_E0_pct'] == 0
    assert t.loc['E2', 'exceso_pen'] == pytest.approx(50)            # 'c' queda fuera de la base común
    assert t.loc['E2', 'exceso_evitado_pen'] == pytest.approx(100)
    for c in ('wmape', 'ic_inf', 'ic_sup', 'nivel_servicio_pct', 'exceso_pen', 'venta_perdida_pen',
              'inversion_pen'):
        assert c in t.columns


def test_escenarios_con_un_escenario_sin_simular():
    t4 = pd.DataFrame({'modelo': ['Naive', 'Pipeline (producción)'], 'wmape': [80.0, 60.0],
                       'ic_inf': [1.0, 1.0], 'ic_sup': [2.0, 2.0]})
    cv = pd.concat([_compras('Naive', [100.0, 50.0]), _compras('Pipeline (producción)', [30.0, 20.0])])
    t = pa.tabla_escenarios(t4, cv).set_index('escenario')
    assert np.isnan(t.loc['E1', 'wmape']) and np.isnan(t.loc['E1', 'exceso_pen'])
    assert t.loc['E2', 'exceso_evitado_pen'] == pytest.approx(100)


# ── Tabla 0: diccionario de variables ────────────────────────────
def test_diccionario_variables():
    t = pa.diccionario_variables()
    assert list(t.columns) == ['simbolo', 'nombre', 'tipo', 'unidad', 'fuente']
    simbolos = set(t['simbolo'])
    for s in ('d_{i,t}', 'd^*_{i,t}', '\\hat{d}_{i,t}', 'a_{i,t}', 'av_k', 's_{i,t}', 'ICD_i', 'Q_i', 'R_i',
              'H', 'L', 'C', 'z_i', 'WMAPE', 'MAE', 'RMSE', 'Bias', 'MASE', 'NS', 'E', 'VP'):
        assert s in simbolos, s
    assert set(t['tipo']) <= {'continua', 'discreta', 'binaria', 'categórica'}
    assert set(t['fuente']) <= {'catálogo', 'ventas', 'compras', 'calculada', 'parámetro'}
    assert t['simbolo'].is_unique


# ── Corrida canónica ─────────────────────────────────────────────
def _canonica(tmp_path, wmape, sucursales=('S',)):
    can = tmp_path / 'canon'
    (can / 'datos_csv').mkdir(parents=True)
    pd.DataFrame({'modelo': ['A', 'B'], 'wmape': wmape}).to_csv(
        can / 'datos_csv' / 'Tabla_4_comparacion_modelos.csv', index=False, encoding='utf-8-sig')
    (can / 'META.json').write_text(json.dumps({'sucursales': list(sucursales)}), encoding='utf-8')
    return str(can)


def test_comparar_con_canonico_avisa_solo_sobre_tolerancia(tmp_path):
    can = _canonica(tmp_path, [50.0, 60.0])
    meta = {'sucursales': [{'sucursal': 'S'}]}
    t4 = pd.DataFrame({'modelo': ['A', 'B'], 'wmape': [50.4, 61.0]})
    avisos = gra.comparar_con_canonico(t4, meta, carpeta=can)
    assert len(avisos) == 1 and '"B"' in avisos[0] and 'WARNING' in avisos[0]
    assert gra.comparar_con_canonico(t4.assign(wmape=[50.5, 60.5]), meta, carpeta=can) == []
    otro = {'sucursales': [{'sucursal': 'Muestra'}]}
    assert gra.comparar_con_canonico(t4, otro, carpeta=can) == []     # otra muestra de datos: no compara
    assert gra.comparar_con_canonico(t4, meta, carpeta=str(tmp_path / 'no_hay')) == []
    solo = gra.comparar_con_canonico(pd.DataFrame({'modelo': ['A'], 'wmape': [50.0]}), meta, carpeta=can)
    assert len(solo) == 1 and 'solo en la corrida canónica' in solo[0]


def test_congelar_copia_y_escribe_meta(tmp_path):
    erp = tmp_path / 'erp'
    erp.mkdir()
    for n in ('listado_productos_2025-01-01.xlsx', 'reporte_ventas_producto_1.xlsx',
              'Reporte_compras_por_producto_1.xlsx', 'Reporte_productos_por_movimiento_1.xlsx'):
        (erp / n).write_bytes(n.encode())
    origen = tmp_path / 'articulo'
    (origen / 'datos_csv').mkdir(parents=True)
    (origen / 'datos_csv' / 'Tabla_4_comparacion_modelos.csv').write_text('modelo,wmape\nA,1\n', encoding='utf-8')
    destino = tmp_path / 'canonico'
    destino.mkdir()
    (destino / 'viejo.txt').write_text('x')
    gra.congelar(str(origen), str(destino), [('S', str(erp))], argv=['--congelar'], meta={'semilla': 7})
    assert (destino / 'datos_csv' / 'Tabla_4_comparacion_modelos.csv').exists()
    assert not (destino / 'viejo.txt').exists()                       # se reemplaza por completo
    m = json.loads((destino / 'META.json').read_text(encoding='utf-8'))
    for k in ('fecha_ejecucion', 'python', 'librerias', 'comando', 'git_commit', 'archivos_entrada'):
        assert k in m
    assert m['comando'].endswith('generar_resultados_articulo.py --congelar') and m['semilla'] == 7
    ventas = m['archivos_entrada']['S']['ventas'][0]
    import hashlib
    assert ventas['sha256'] == hashlib.sha256(b'reporte_ventas_producto_1.xlsx').hexdigest()
    assert set(m['archivos_entrada']['S']) == {'listado_productos', 'ventas', 'compras', 'movimientos_producto'}
    with pytest.raises(ValueError):
        gra.congelar(str(origen), str(origen), [])


def test_argumentos_nuevos():
    import inspect
    src = inspect.getsource(gra.main)
    for a in ('--congelar', '--muestra-anonima', '--salida'):
        assert a in src


# ── Muestra anónima ──────────────────────────────────────────────
def _erp_sintetico(carpeta, n_sku=6):
    import preve_muestra as pm
    os.makedirs(carpeta, exist_ok=True)
    cat = pd.DataFrame({'N': range(1, n_sku + 1), 'CODIGO BARRAS': [f'77500000000{i:02d}' for i in range(n_sku)],
                        'SKU': [f'REAL-{i}' for i in range(n_sku)],
                        'PRODUCTO': [f'PRODUCTO SECRETO {i}' for i in range(n_sku)], 'CATEGORÍA': 'HOGAR',
                        'PRECIO COMPRA': 10.0, 'PRECIO VENTA': 23.6, 'STOCK': 5})
    ven = pd.DataFrame({'N': range(1, 2 * n_sku + 1), 'FECHA/HORA': '05-01-2025 10:00',
                        'COMPROBANTE': [f'B-{i}' for i in range(2 * n_sku)],
                        'CÓDIGO DE BARRA': [f'77500000000{i % n_sku:02d}' for i in range(2 * n_sku)],
                        'CLIENTE': 'JUAN PEREZ', 'PRODUCTO': [f'PRODUCTO SECRETO {i % n_sku}' for i in range(2 * n_sku)],
                        'SKU': [f'REAL-{i % n_sku}' for i in range(2 * n_sku)], 'EMPLEADOS': 'ANA',
                        'PRECIO X UNIDAD': 23.6, 'CANTIDAD': 1, 'TOTAL': 23.6})
    com = pd.DataFrame({'N°': [1], 'FECHA': ['02-01-2025'], 'COMPROBANTE': ['F-1'], 'SKU': ['REAL-0'],
                        'PRODUCTO': ['PRODUCTO SECRETO 0'], 'COD. BARRA': ['7750000000000'], 'RUC': ['20123456789'],
                        'PROVEEDOR': ['PROVEEDOR REAL SAC'], 'PRECIO': [10.0], 'CANTIDAD': [3], 'TOTAL': [30.0]})
    mov = pd.DataFrame({'N': [1], 'FECHA': ['03-01-2025 10:00 AM'], 'PRODUCTO': ['PRODUCTO SECRETO 1'],
                        'TIPO DE MOVIMIENTO': ['Entrada'], 'NRO. MOVIMIENTO': ['MOV-99'], 'ORIGEN': [''],
                        'DESTINO': ['Tienda Juliaca'], 'CANTIDAD': [2], 'PRECIO': [10.0], 'IMPORTE': [20.0]})
    for df, nombre, tipo in ((cat, 'listado_productos_2025-01-10.xlsx', 'listado_productos'),
                             (ven, 'reporte_ventas_producto_1.xlsx', 'ventas'),
                             (com, 'Reporte_compras_por_producto_1.xlsx', 'compras'),
                             (mov, 'Reporte_productos_por_movimiento_1.xlsx', 'movimientos_producto')):
        pm._escribir_erp(df, os.path.join(carpeta, nombre), pm._TITULOS[tipo])


def test_muestra_anonima_formato_erp_y_anonimizacion(tmp_path):
    import preve_muestra as pm
    from preve_pipeline import cargar_datos
    _erp_sintetico(str(tmp_path / 'real'))
    r = pm.generar_muestra_anonima(str(tmp_path / 'real'), str(tmp_path / 'm'), n=4, semilla=1)
    assert r['skus'] == 4
    for f in ('ventas_muestra.csv', 'catalogo_muestra.csv', 'README_muestra.md'):
        assert (tmp_path / 'm' / f).exists()
    listado, ventas, compras, movs = cargar_datos(r['erp'], lambda m: None)    # el pipeline lo lee tal cual
    assert len(listado) == 4 and set(listado['PRODUCTO']) == {f'SKU_00{i}' for i in range(1, 5)}
    assert set(ventas['PRODUCTO']) <= set(listado['PRODUCTO']) and len(ventas) == 8
    todo = ' '.join(pd.concat([listado, ventas, compras, movs]).astype(str).to_numpy().ravel())
    for secreto in ('SECRETO', 'JUAN PEREZ', 'ANA', 'REAL-', '20123456789', 'PROVEEDOR REAL', '77500000000',
                    'MOV-99', 'B-1'):
        assert secreto not in todo, secreto
    # los cruces se conservan: cada venta apunta al mismo alias que su código en el catálogo
    mapa = dict(zip(listado['CODIGO BARRAS'], listado['PRODUCTO']))
    assert all(mapa[bc] == p for bc, p in zip(ventas['CÓDIGO DE BARRA'], ventas['PRODUCTO']))
    # todos los importes escalados por el mismo factor (se conserva la razón precio/costo)
    razon = listado['PRECIO VENTA'] / listado['PRECIO COMPRA']
    assert np.allclose(razon, 2.36, rtol=1e-4) and not listado['PRECIO COMPRA'].eq(10.0).any()
    assert ventas['CANTIDAD'].sum() == 8                              # cantidades intactas
    # reproducible con la misma semilla
    r2 = pm.generar_muestra_anonima(str(tmp_path / 'real'), str(tmp_path / 'm2'), n=4, semilla=1)
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / 'm' / 'catalogo_muestra.csv'),
                                  pd.read_csv(tmp_path / 'm2' / 'catalogo_muestra.csv'))
    assert r2['filas'] == r['filas']


# ── Robustez: recomendación con pronóstico vacío ─────────────────
def test_recomendacion_con_pronostico_vacio(log):
    from preve_pipeline import calcular_recomendacion
    pron = pd.DataFrame({'sku': pd.Series(dtype=object), 'demanda_p10_141d': pd.Series(dtype=float),
                         'demanda_p50_141d': pd.Series(dtype=float), 'demanda_p90_141d': pd.Series(dtype=float)})
    seg = pd.DataFrame({'sku': ['A'], 'clase_abc': ['A'], 'modelo_asignado': ['GBM'], 'segmento': ['Suave']})
    dim = pd.DataFrame({'sku': ['A'], 'nombre': ['P'], 'categoria': ['X'], 'costo_puesto_almacen_pen': [5.0],
                        'precio_regular_pen': [11.8]})
    fechas = pd.date_range('2025-01-01', periods=3)
    stock = pd.DataFrame({'A': [1.0] * 3}, index=fechas)
    panel = pd.DataFrame({'sku': ['A'] * 3, 'semana_inicio': pd.date_range('2024-12-16', periods=3, freq='W-MON'),
                          'unidades_venta_neta': [7.0, 7.0, 7.0]})
    rec = calcular_recomendacion(pron, seg, dim, stock, panel, fechas[-1], log)
    assert len(rec) == 0 and {'accion', 'decision', 'compra_sugerida_unid'} <= set(rec.columns)


# ── Documentación y dependencias ─────────────────────────────────
def test_requirements_lock_pinea_todo():
    with open(os.path.join(RAIZ, 'requirements-lock.txt'), encoding='utf-8') as f:
        lineas = [x.strip() for x in f if x.strip() and not x.startswith('#')]
    assert lineas and all('==' in x or ' @ ' in x for x in lineas)
    nombres = {x.split('==')[0].lower() for x in lineas}
    for p in ('pandas', 'numpy', 'scipy', 'statsmodels', 'scikit-learn'):
        assert p in nombres, p
    with open(os.path.join(RAIZ, 'requirements.txt'), encoding='utf-8') as f:
        assert 'statsmodels' in f.read()


def test_formulacion_compras_refleja_el_codigo():
    with open(os.path.join(RAIZ, 'formulacion_compras.md'), encoding='utf-8') as f:
        txt = f.read()
    for frag in ('1.2816', 'nivel_objetivo', 'cantidad_a_pedir', 'E0', 'E1', 'E2', 'calcular_recomendacion',
                 'stock_en_transito'):
        assert frag in txt, frag


# ── Botón del artículo en la app ─────────────────────────────────
def test_comando_articulo_congelar_solo_en_version_completa():
    import sys
    import preve_app
    base = os.path.join('x', 'y')
    assert preve_app.comando_articulo(base)[1:] == [os.path.join(base, 'generar_resultados_articulo.py')]
    assert preve_app.comando_articulo(base, congelar=True)[-1] == '--congelar'
    r = preve_app.comando_articulo(base, rapido=True, congelar=True)
    assert r[-1] == '--rapido' and '--congelar' not in r             # una corrida rápida nunca se congela
    assert preve_app.comando_articulo(base)[0] == sys.executable
