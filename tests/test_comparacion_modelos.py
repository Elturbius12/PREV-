# -*- coding: utf-8 -*-
"""Tests del modo comparación de modelos, la asignación por SKU, la alerta de censura, analisis_censura.py y las
traducciones. Todos los datos son sintéticos."""
import os
import time

import numpy as np
import pandas as pd
import pytest

import preve_backtesting as bt
import preve_modelos_baseline as mb
from tests.test_fase6 import PronosticadorFalso, datos_sinteticos

COLS = ['demanda_p10_141d', 'demanda_p50_141d', 'demanda_p90_141d']
NOMBRES_CONCRETOS = [n for n in mb.expandir_modelos(mb.MODELOS_COMPARABLES) if n != mb.MODELO_PRODUCCION]


def _escenario(n_sku=36, semanas=40, seed=0):
    """ventas_diarias / stock / dim con demanda Poisson por SKU (un tercio intermitente)."""
    rng = np.random.default_rng(seed)
    lunes = pd.date_range('2025-01-06', periods=semanas, freq='7D')
    skus = [f'S{i:03d}' for i in range(n_sku)]
    filas = []
    for i, s in enumerate(skus):
        tasa = rng.gamma(2.0, 1.5)
        for d in lunes:
            v = float(rng.poisson(3)) if (i % 3 == 0 and rng.random() < 0.3) else (
                0.0 if i % 3 == 0 else float(rng.poisson(tasa)))
            if v:
                filas.append((s, d + pd.Timedelta(days=2), -v))
    vd = pd.DataFrame(filas, columns=['sku', 'fecha', 'mov'])
    dias = pd.date_range(lunes[0] - pd.Timedelta(days=1), lunes[-1] + pd.Timedelta(days=6))
    stock = pd.DataFrame(20.0, index=dias, columns=skus)
    dim = pd.DataFrame({'sku': skus, 'nombre': [f'Producto {s}' for s in skus],
                        'categoria': [f'C{i % 3}' for i in range(n_sku)], 'subcategoria': 'X',
                        'costo_puesto_almacen_pen': [3.0 + i % 5 for i in range(n_sku)],
                        'precio_regular_pen': [9.0 + i % 7 for i in range(n_sku)]})
    return vd, stock, dim


# ══════════════════════════════════════════════════════════════════
# Baselines (#1, #6, #14)
# ══════════════════════════════════════════════════════════════════
@pytest.fixture(scope='module')
def escenario():
    return _escenario()


@pytest.mark.parametrize('nombre', NOMBRES_CONCRETOS)
def test_cada_baseline_devuelve_un_pronostico_valido_con_su_nombre(nombre, escenario):
    from preve_config import ParametrosModelo
    vd, stock, dim = escenario
    pron, panel, seg, fecha_fin = mb.pronosticador(nombre)(vd, stock, dim, 14, ParametrosModelo(), lambda m: None)
    assert isinstance(pron, pd.DataFrame) and len(pron) > 0
    assert set(COLS) <= set(pron.columns) and pron['sku'].is_unique
    assert set(pron['sku']) <= set(dim['sku'])
    assert all(str(pron[c].dtype) == 'float64' for c in COLS)
    assert not pron[COLS].isna().any().any() and (pron[COLS] >= 0).all().all()
    assert (pron['demanda_p10_141d'] <= pron['demanda_p50_141d'] + 1e-9).all()
    assert (pron['demanda_p50_141d'] <= pron['demanda_p90_141d'] + 1e-9).all()
    assert set(pron['modelo_asignado']) == {nombre}           # cada pronosticador se etiqueta con su nombre real


def test_sba_usa_la_clase_real_de_statsforecast():
    from statsforecast.models import CrostonSBA          # la clase existe con este nombre (no "SBA")
    serie = np.array([0, 3, 0, 0, 2, 0, 4, 0, 0, 3, 0, 1], dtype=float)
    m = CrostonSBA()
    m.fit(serie)
    assert np.isfinite(m.predict(h=2)['mean']).all()


def test_promedio_movil_usa_las_ventanas_configuradas(escenario):
    import dataclasses
    from preve_config import ParametrosModelo
    nombres = mb.expandir_modelos([mb.MODELO_PROMEDIO_MOVIL], [6, 2])
    assert nombres == ['Promedio móvil 2 sem.', 'Promedio móvil 6 sem.']
    assert [mb.ventana_promedio_movil(n) for n in nombres] == [2, 6]
    # la ventana sí cambia el cálculo: SKU con ventas 0,0,0,…,10 en las 2 últimas semanas
    lunes = pd.date_range('2025-01-06', periods=10, freq='7D')
    panel = pd.DataFrame({'sku': 'X', 'semana_inicio': lunes, 'unidades_venta_neta': [1.0] * 8 + [10.0, 10.0]})
    stock = pd.DataFrame({'X': 5.0}, index=pd.date_range(lunes[0] - pd.Timedelta(days=1), lunes[-1] + pd.Timedelta(days=6)))
    p = dataclasses.replace(ParametrosModelo(), horizonte_dias=7.0)
    seg = pd.DataFrame({'sku': ['X']})
    p2 = mb.pronosticar_desde_panel('Promedio móvil 2 sem.', panel, seg, None, stock, stock.index.max(), p)
    p6 = mb.pronosticar_desde_panel('Promedio móvil 6 sem.', panel, seg, None, stock, stock.index.max(), p)
    assert p2['demanda_p50_141d'].iloc[0] == pytest.approx(10.0)
    assert p6['demanda_p50_141d'].iloc[0] == pytest.approx((4 * 1 + 2 * 10) / 6)
    with pytest.raises(ValueError):
        mb.validar_ventanas([0])
    with pytest.raises(KeyError):
        mb.pronosticador('SMA(4)')                               # nombre viejo: ya no existe


# ══════════════════════════════════════════════════════════════════
# Nombres canónicos y configuración (#2)
# ══════════════════════════════════════════════════════════════════
def test_config_por_defecto_ejecuta_todos_los_modelos_incluido_el_pipeline():
    from preve_config import DEFAULTS, Config
    assert DEFAULTS['modelos_activos'][0] == list(mb.MODELOS_COMPARABLES)
    modelos, desconocidos = Config.actual().modelos_para_comparar()
    assert modelos == list(mb.MODELOS_COMPARABLES) and desconocidos == []
    assert mb.MODELO_PRODUCCION in modelos
    concretos = mb.expandir_modelos(modelos, Config.actual().ventanas_promedio_movil())
    assert len(concretos) == len(mb.MODELOS_COMPARABLES) - 1 + 3        # el promedio móvil en 4, 8 y 13 sem.


def test_config_rechaza_y_avisa_nombres_desconocidos(caplog):
    from preve_config import Config, ConfigError
    cfg = Config.actual()
    with pytest.raises(ConfigError, match='desconocidos'):
        cfg.set('modelos_activos', ['GBM', 'SMA'])                       # los nombres del commit 77f1b52
    cfg.db.set_config('modelos_activos', ['Naive', 'SMA', mb.MODELO_SES])   # guardado a mano en la base
    cfg.invalidar()
    modelos, desconocidos = cfg.modelos_para_comparar()
    assert modelos == [mb.MODELO_NAIVE, mb.MODELO_SES] and desconocidos == ['SMA']
    assert any('desconocidos' in r.message for r in caplog.records)
    cfg.set('ventanas_promedio_movil', [13, 4])
    assert cfg.ventanas_promedio_movil() == [4, 13]
    with pytest.raises(ConfigError):
        cfg.set('ventanas_promedio_movil', [0])


# ══════════════════════════════════════════════════════════════════
# Métricas reales y selección por SKU (#8)
# ══════════════════════════════════════════════════════════════════
def _det(filas):
    return pd.DataFrame(filas, columns=['sku', 'modelo', 'periodo_inicio', 'pronostico', 'real'])


def test_seleccion_usa_wmape_real_y_no_el_promedio_de_error_pct():
    # X: real [1, 100]. A: [2, 100] → WMAPE 1/101 = 0.99 %; B: [1, 80] → 20/101 = 19.8 %.
    # El promedio de error_pct (lo que hacía 77f1b52) elegía B: A = (100 % + 0)/2 = 50 %, B = (0 + 20 %)/2 = 10 %.
    d = _det([('X', 'A', 1, 2, 1), ('X', 'A', 2, 100, 100), ('X', 'B', 1, 1, 1), ('X', 'B', 2, 80, 100)])
    m = bt.metricas_por_sku_modelo(d).set_index('modelo')
    assert m.loc['A', 'wmape'] == pytest.approx(1 / 101 * 100)
    assert m.loc['B', 'wmape'] == pytest.approx(20 / 101 * 100)
    assert m.loc['B', 'mae'] == pytest.approx(10.0) and m.loc['B', 'bias'] == pytest.approx(-10.0)
    mejor = bt.seleccionar_mejor_modelo(bt.metricas_por_sku_modelo(d))
    assert mejor.iloc[0]['modelo'] == 'A' and mejor.iloc[0]['confianza'] == 'Alta'
    assert mejor.iloc[0]['criterio'] == bt.CRITERIO_WMAPE


def test_seleccion_desempata_por_mae_y_define_suma_real_cero():
    d = _det([
        # Y: A (errores 5 y 5) y B (10 y 0) empatan en WMAPE (10/20) y en MAE (5) → decide el orden
        ('Y', 'A', 1, 15, 10), ('Y', 'A', 2, 15, 10), ('Y', 'B', 1, 20, 10), ('Y', 'B', 2, 10, 10),
        # Z: no vendió nada → WMAPE NaN; gana el de menor MAE (más cerca de 0); confianza "Sin dato"
        ('Z', 'A', 1, 3, 0), ('Z', 'A', 2, 3, 0), ('Z', 'B', 1, 1, 0), ('Z', 'B', 2, 0, 0),
        # W: C solo tiene 1 de 2 períodos → no compite aunque su error sea 0
        ('W', 'A', 1, 6, 5), ('W', 'A', 2, 6, 5), ('W', 'C', 1, 5, 5),
    ])
    mejor = bt.seleccionar_mejor_modelo(bt.metricas_por_sku_modelo(d), orden=['B', 'A', 'C']).set_index('sku')
    assert mejor.loc['Y', 'modelo'] == 'B'                 # WMAPE y MAE empatados → primero en `orden`
    assert mejor.loc['Z', 'modelo'] == 'B' and np.isnan(mejor.loc['Z', 'wmape'])
    assert mejor.loc['Z', 'confianza'] == 'Sin dato' and mejor.loc['Z', 'criterio'] == bt.CRITERIO_MAE
    assert mejor.loc['W', 'modelo'] == 'A'


def test_seleccion_desempate_por_mae_cuando_wmape_empata():
    m = pd.DataFrame({'sku': ['K', 'K'], 'modelo': ['A', 'B'], 'wmape': [20.0, 20.0], 'mae': [3.0, 2.0],
                      'bias': [0.0, 0.0], 'periodos': [2, 2], 'suma_real': [10.0, 10.0]})
    assert bt.seleccionar_mejor_modelo(m).iloc[0]['modelo'] == 'B'


# ══════════════════════════════════════════════════════════════════
# comparar_modelos de punta a punta con pronosticadores falsos (#3, #6, #7, #15)
# ══════════════════════════════════════════════════════════════════
class PronosticadorTriple(PronosticadorFalso):
    """El falso de la Fase 6 pero 3 veces más alto: siempre peor."""

    def __call__(self, *a):
        pron, panel, seg, ff = super().__call__(*a)
        pron[COLS] = pron[COLS] * 3
        return pron, panel, seg, ff


def test_comparar_modelos_elige_por_sku_y_solo_crea_el_excel_de_comparacion(tmp_path, log):
    from database import Database
    db = Database.default()
    carpeta = tmp_path / 'reportes'
    r = bt.comparar_modelos(datos_sinteticos(), '2025-02-01', '2025-05-31', ventana_dias=14,
                            pronosticadores={'Bueno': PronosticadorFalso(), 'Malo': PronosticadorTriple()},
                            carpeta_salida=str(carpeta), log=log, db=db, sucursal='San José',
                            calcular_importancias=False)
    assert r['modelos'] == ['Bueno', 'Malo']
    assert set(r['detalle']['modelo']) == {'Bueno', 'Malo'}            # el nombre comparado, no el del pipeline
    # B es nuevo (desde abril): el falso promedia sobre días en que no existía y subestima; el triple se acerca más
    elegido = r['mejor_por_sku'].set_index('sku')['modelo'].to_dict()
    assert elegido == {'A': 'Bueno', 'B': 'Malo', 'C': 'Bueno'}
    # solo el Excel de comparación; nada en las tablas de métricas ni de ejecuciones del backtesting
    archivos = os.listdir(carpeta)
    assert len(archivos) == 1 and archivos[0].startswith('comparacion_modelos_')
    assert db.contar('metricas_periodo') == 0 and db.contar('ejecuciones_backtesting') == 0
    assert db.contar('pronosticos') == 0
    # la asignación quedó guardada
    assert r['asignaciones_guardadas'] == 3
    assert db.mapa_asignacion_modelos('San José') == elegido
    hojas = pd.ExcelFile(r['ruta_excel']).sheet_names
    assert {'Resumen por Modelo', 'Mejor Modelo por SKU', 'Métricas SKU x Modelo', 'Notas',
            bt.HOJA_DESAGREGADA} <= set(hojas)
    # WMAPE del Excel = Σ|f−r|/Σr del detalle
    d = r['detalle'][(r['detalle']['modelo'] == 'Bueno') & (r['detalle']['sku'] == 'A')]
    esperado = (d['pronostico'] - d['real']).abs().sum() / d['real'].sum() * 100
    fila = r['mejor_por_sku'].set_index('sku').loc['A']
    assert fila['wmape'] == pytest.approx(esperado)
    # el reporte de gestión no tiene jerga
    ruta = bt.exportar_reporte_gestion(r, str(carpeta))
    texto = ' '.join(pd.concat(pd.read_excel(ruta, sheet_name=None), ignore_index=True).astype(str)
                     .to_numpy().ravel().tolist() + list(pd.read_excel(ruta, sheet_name='Productos').columns))
    for jerga in ('WMAPE', 'MAE', 'ADI', 'CV²', 'Bias'):
        assert jerga not in texto


def test_comparar_modelos_registra_un_modelo_que_falla_y_sigue(tmp_path, log):
    def roto(*a):
        raise RuntimeError('falla a propósito')
    r = bt.comparar_modelos(datos_sinteticos(), '2025-02-01', '2025-05-31', ventana_dias=14,
                            pronosticadores={'Bueno': PronosticadorFalso(), 'Roto': roto},
                            carpeta_salida=str(tmp_path), log=log, guardar_asignacion=False,
                            calcular_importancias=False)
    assert r['modelos'] == ['Bueno'] and 'Roto' in r['fallidos'] and r['asignaciones_guardadas'] == 0


def test_comparar_modelos_cancelado_no_guarda_nada(tmp_path, log):
    from database import Database
    with pytest.raises(bt.BacktestCancelado):
        bt.comparar_modelos(datos_sinteticos(), '2025-02-01', '2025-05-31', ventana_dias=14,
                            pronosticadores={'Bueno': PronosticadorFalso()}, carpeta_salida=str(tmp_path),
                            log=log, cancelar=lambda: True)
    assert not os.listdir(tmp_path) and Database.default().contar('asignacion_modelos') == 0


def test_run_persistir_false_no_escribe_nada(tmp_path, log):
    from database import Database
    b = bt.Backtester(datos_sinteticos(), ventana_dias=14, pronosticador=PronosticadorFalso(),
                      carpeta_salida=str(tmp_path / 'r'), log=log)
    res = b.run('2025-02-01', '2025-05-31', simular_compras=False, persistir=False)
    assert res['ruta_excel'] is None and res['ejecucion_id'] is None
    assert not (tmp_path / 'r').exists()
    assert Database.default().contar('metricas_periodo') == 0


# ══════════════════════════════════════════════════════════════════
# Clase ABC en el detalle (#5)
# ══════════════════════════════════════════════════════════════════
class PronosticadorABC(PronosticadorFalso):
    """Fuerza la clase ABC = nombre del SKU (A, B, C) para tener las tres clases."""

    def __call__(self, *a):
        pron, panel, seg, ff = super().__call__(*a)
        seg = seg.assign(clase_abc=seg['sku'])
        return pron, panel, seg, ff


def test_detalle_trae_clase_abc_y_el_desglose_tiene_tres_filas(tmp_path, log):
    b = bt.Backtester(datos_sinteticos(), ventana_dias=14, pronosticador=PronosticadorABC(),
                      carpeta_salida=str(tmp_path), log=log)
    res = b.run('2025-04-14', '2025-05-31', simular_compras=False)
    assert (res['detalle']['clase_abc'] == res['detalle']['sku']).all()
    abc = res['por_clase_abc'].set_index('clase_abc')
    assert list(abc.index) == ['A', 'B', 'C']
    assert abc['wmape'].nunique() == 3                    # tres filas distintas, no "Sin dato"
    for c in 'ABC':
        g = res['detalle'][res['detalle']['clase_abc'] == c]
        assert abc.loc[c, 'wmape'] == pytest.approx(bt.wmape(g))
        assert abc.loc[c, 'mae'] == pytest.approx((g['pronostico'] - g['real']).abs().mean())
    hoja = pd.read_excel(res['ruta_excel'], sheet_name=bt.HOJA_DESAGREGADA, header=None)
    assert 'Por clase ABC' in hoja[0].astype(str).tolist()


# ══════════════════════════════════════════════════════════════════
# Asignación guardada (#3) y uso en producción detrás del flag (#4)
# ══════════════════════════════════════════════════════════════════
def test_asignacion_ida_y_vuelta():
    from database import Database
    db = Database.default()
    df = pd.DataFrame({'sku': ['A', 'B'], 'modelo': [mb.MODELO_NAIVE, 'Promedio móvil 4 sem.'],
                       'wmape': [12.5, np.nan], 'mae': [1.0, 0.5], 'bias': [0.1, -0.2], 'periodos': [5, 5],
                       'confianza': ['Alta', 'Sin dato']})
    assert db.guardar_asignacion_modelos('San José', df) == 2
    leido = db.obtener_asignacion_modelos('San José').set_index('sku')
    assert leido.loc['A', 'modelo'] == mb.MODELO_NAIVE and leido.loc['A', 'wmape'] == 12.5
    assert pd.isna(leido.loc['B', 'wmape']) and leido.loc['B', 'confianza'] == 'Sin dato'
    db.guardar_asignacion_modelos('San José', pd.DataFrame({'sku': ['A'], 'modelo': [mb.MODELO_SES]}))   # upsert
    assert db.mapa_asignacion_modelos('San José') == {'A': mb.MODELO_SES, 'B': 'Promedio móvil 4 sem.'}
    assert db.mapa_asignacion_modelos('Otra sucursal') == {}
    assert db.borrar_asignacion_modelos('San José') == 2 and db.mapa_asignacion_modelos('San José') == {}


def _produccion(vd, stock, dim, **kw):
    import preve_pipeline as pp
    params = pp.ParametrosModelo(horizonte_dias=14.0, **kw.pop('params', {}))
    panel, seg, fin = pp.construir_panel_y_segmentacion(stock, vd, dim, lambda m: None, params=params)
    pron, sin = pp.entrenar_y_pronosticar(panel, seg, dim, fin, lambda m: None, params=params, stock=stock, **kw)
    return pron.sort_values('sku').reset_index(drop=True), seg, panel, fin, params


def test_produccion_usa_la_asignacion_solo_con_el_flag(escenario):
    vd, stock, dim = escenario
    base, seg_base, panel, fin, params = _produccion(vd, stock, dim)
    asign = {'S001': mb.MODELO_NAIVE, 'S002': 'Promedio móvil 4 sem.', 'S004': mb.MODELO_PRODUCCION,
             'S005': 'NO EXISTE'}
    # flag apagado (por defecto): la asignación se ignora y la salida es idéntica
    apagado, seg_off, *_ = _produccion(vd, stock, dim, asignacion=asign)
    pd.testing.assert_frame_equal(base, apagado, check_exact=True)
    assert seg_off['modelo_asignado'].equals(seg_base['modelo_asignado'])
    # flag encendido: S001 y S002 cambian; S004 (asignado al pipeline) y S005 (nombre desconocido) no
    encendido, seg_on, *_ = _produccion(vd, stock, dim, asignacion=asign, params={'usar_asignacion_modelos': True})
    naive = mb.pronosticar_desde_panel(mb.MODELO_NAIVE, panel, seg_base, dim, stock, fin, params, skus=['S001'])
    on = encendido.set_index('sku')
    assert on.loc['S001', 'demanda_p50_141d'] == pytest.approx(naive['demanda_p50_141d'].iloc[0])
    m = seg_on.set_index('sku')['modelo_asignado']
    assert m['S001'] == mb.MODELO_NAIVE and m['S002'] == 'Promedio móvil 4 sem.'
    otros = [s for s in base['sku'] if s not in ('S001', 'S002')]
    pd.testing.assert_frame_equal(base.set_index('sku').loc[otros], on.loc[otros], check_exact=True)


def test_produccion_lee_la_asignacion_de_la_base(escenario):
    import preve_pipeline as pp
    from database import Database
    vd, stock, dim = escenario
    Database.default().guardar_asignacion_modelos('San José', pd.DataFrame({'sku': ['S003'],
                                                                           'modelo': [mb.MODELO_SES]}))
    params = pp.ParametrosModelo(horizonte_dias=14.0, usar_asignacion_modelos=True)
    panel, seg, fin = pp.construir_panel_y_segmentacion(stock, vd, dim, lambda m: None, params=params)
    _, _, extras = pp.entrenar_y_pronosticar(panel, seg, dim, fin, lambda m: None, params=params, stock=stock,
                                             devolver_extras=True, sucursal='San José')
    assert extras['asignacion_aplicada'] == {'S003': mb.MODELO_SES}
    assert seg.set_index('sku').loc['S003', 'modelo_asignado'] == mb.MODELO_SES


def test_flag_de_disponibilidad_esta_conectado(escenario):
    vd, stock, dim = escenario
    stock = stock.copy()
    stock.iloc[-60:, :12] = 0.0                       # 12 SKU agotados al final: la disponibilidad sí importa
    con, *_ = _produccion(vd, stock, dim)
    sin, *_ = _produccion(vd, stock, dim, params={'usar_disponibilidad_gbm': False})
    assert not np.allclose(con['demanda_p50_141d'], sin['demanda_p50_141d'])


def test_importancias_opcionales_y_con_nombre_de_variable(escenario):
    import preve_modelo_gbm as G
    from preve_traducciones import NOMBRE_VARIABLE
    vd, stock, dim = escenario
    _, _, panel, fin, params = _produccion(vd, stock, dim)
    t0 = time.perf_counter()
    out, ex = G.pronosticar_gbm(panel, stock, fin, 2.0, lambda m: None, devolver_extras=True)
    t_sin = time.perf_counter() - t0
    assert ex['importancias'] is None                  # apagado por defecto
    t0 = time.perf_counter()
    out2, ex2 = G.pronosticar_gbm(panel, stock, fin, 2.0, lambda m: None, devolver_extras=True,
                                  calcular_importancias=True)
    t_con = time.perf_counter() - t0
    pd.testing.assert_frame_equal(out, out2)          # no cambia el pronóstico
    imp = ex2['importancias']
    assert list(imp.columns) == ['variable', 'importancia'] and set(imp['variable']) == set(G.FEATURES)
    assert set(G.FEATURES) <= set(NOMBRE_VARIABLE)
    assert t_con > t_sin
    _, ex3 = G.pronosticar_gbm(panel, stock, fin, 2.0, lambda m: None, devolver_extras=True,
                               usar_disponibilidad=False, calcular_importancias=True)
    assert not set(G.COLS_DISPONIBILIDAD) & set(ex3['importancias']['variable'])


def test_importancias_van_al_csv_del_analisis(tmp_path):
    import preve_orquestador as orq
    imp = pd.DataFrame({'variable': ['s4', 'stock'], 'importancia': [0.5, 0.2]})
    orq._guardar_importancias(imp, str(tmp_path), lambda m: None)
    from preve_utils import ruta_dato
    df = pd.read_csv(ruta_dato(str(tmp_path), orq.NOMBRE_IMPORTANCIAS_CSV), encoding='utf-8-sig')
    assert list(df['descripcion']) == ['Ventas promedio de las últimas 4 semanas', 'Stock al inicio de la semana']
    orq._guardar_importancias(None, str(tmp_path / 'no'), lambda m: None)        # sin importancias: no hace nada


# ══════════════════════════════════════════════════════════════════
# Alerta de censura en calcular_recomendacion (#9 original)
# ══════════════════════════════════════════════════════════════════
def test_alerta_censura_por_dias_sin_stock():
    import preve_pipeline as pp
    dias = pd.date_range('2025-01-01', periods=120)
    stock = pd.DataFrame({'AGOTADO': 5.0, 'OK': 5.0, 'NUEVO': 0.0}, index=dias)
    stock.iloc[-46:, 0] = 0.0                          # 46 de los últimos 91 días sin stock → 50.5 %
    stock.iloc[-10:, 2] = 3.0                          # NUEVO no existía: sus días previos no son quiebre
    df = pd.DataFrame({'sku': ['AGOTADO', 'OK', 'NUEVO', 'SIN_STOCK_REGISTRADO']})
    out = pp.marcar_alerta_censura(df, stock, umbral=0.4).set_index('sku')
    assert out.loc['AGOTADO', 'frac_dias_sin_stock_13sem'] == pytest.approx(46 / 91)
    assert bool(out.loc['AGOTADO', 'alerta_censura']) and 'agotado 51%' in out.loc['AGOTADO', 'nota_confianza']
    assert not out.loc[['OK', 'NUEVO', 'SIN_STOCK_REGISTRADO'], 'alerta_censura'].any()
    assert (out.loc[['OK', 'NUEVO'], 'nota_confianza'] == '').all()
    assert 'alerta_censura' not in df.columns           # no muta la entrada


def test_calcular_recomendacion_marca_menor_confianza_sin_cambiar_la_accion(log):
    import preve_pipeline as pp
    from tests.test_fase1 import _entradas_recomendacion
    pron, seg, dim, stock, panel, fin = _entradas_recomendacion(stock_val=0.0)
    p = pp.ParametrosModelo()
    rec = pp.calcular_recomendacion(pron, seg, dim, pd.concat([stock.assign(A=5.0).iloc[:1], stock.iloc[1:]]),
                                    panel, fin, log, params=p).iloc[0]
    assert bool(rec['alerta_censura']) and rec['nota_confianza']
    sin_alerta = pp.calcular_recomendacion(pron, seg, dim, stock.assign(A=0.0), panel, fin, log,
                                           params=pp.ParametrosModelo(umbral_censura_alerta=1.1)).iloc[0]
    assert not bool(sin_alerta['alerta_censura'])
    assert rec['accion'] == sin_alerta['accion'] and rec['compra_sugerida_unid'] == sin_alerta['compra_sugerida_unid']


# ══════════════════════════════════════════════════════════════════
# analisis_censura.py (#6 original)
# ══════════════════════════════════════════════════════════════════
def test_analisis_censura_con_resultado_conocido():
    import analisis_censura as ac
    lunes = pd.date_range('2025-01-06', periods=20, freq='7D')
    dias = pd.date_range(lunes[0], lunes[-1] + pd.Timedelta(days=6))
    stock = pd.DataFrame(10.0, index=dias, columns=['S1', 'S2', 'S3'])
    filas = []
    for k, d in enumerate(lunes):
        agotado = 5 <= k <= 13                           # S1: 9 semanas sin stock y sin venta
        filas.append(('S1', d, 0.0 if agotado else 5.0))
        filas.append(('S2', d, 4.0 if k % 2 == 0 else 0.0))   # S2: no vende semana por medio, CON stock
        filas.append(('S3', d, 3.0 if k >= 10 else 0.0))      # S3: no existía antes de la semana 10
        if agotado:
            stock.loc[d:d + pd.Timedelta(days=6), 'S1'] = 0.0
        if k < 10:
            stock.loc[d:d + pd.Timedelta(days=6), 'S3'] = 0.0
    panel = pd.DataFrame(filas, columns=['sku', 'semana_inicio', 'unidades_venta_neta'])
    seg = pd.DataFrame({'sku': ['S1', 'S2', 'S3'], 'clase_abc': ['A', 'B', 'B']})
    r = ac.analizar_censura(panel, stock, seg)
    s = r['por_sku'].set_index('sku')
    assert s.loc['S1', 'semanas_censuradas'] == 9 and s.loc['S1', 'n_quiebres'] == 1
    assert s.loc['S1', 'duracion_max_quiebre'] == 9
    assert s.loc['S1', 'segmento_original'] == 'Intermitente'     # ADI 20/11 = 1.82, CV² 0
    assert s.loc['S1', 'segmento_corregido'] == 'Suave'           # sin las censuradas: ADI 1
    assert s.loc['S2', 'semanas_censuradas'] == 0 and not s.loc['S2', 'cambio_segmento']
    assert s.loc['S3', 'semanas_total'] == 10 and s.loc['S3', 'semanas_censuradas'] == 0
    g = r['resumen_global'].iloc[0]
    assert g['total_semanas_sin_venta'] == 9 + 10 and g['total_semanas_censuradas'] == 9
    assert g['pct_censura_global'] == pytest.approx(round(9 / 19 * 100, 1))
    assert g['skus_con_cambio_segmento'] == 1 and g['duracion_promedio_quiebre_global'] == 9.0
    abc = r['por_clase_abc'].set_index('clase_abc')
    assert abc.loc['A', 'semanas_censuradas'] == 9 and abc.loc['B', 'semanas_censuradas'] == 0
    assert list(r['cambios_segmento']['sku']) == ['S1']


def test_analisis_censura_exporta_excel(tmp_path):
    import analisis_censura as ac
    panel = pd.DataFrame({'sku': ['X'] * 4, 'semana_inicio': pd.date_range('2025-01-06', periods=4, freq='7D'),
                          'unidades_venta_neta': [1.0, 0.0, 2.0, 0.0]})
    r = ac.analizar_censura(panel, None)
    ruta = ac.exportar_excel(r, str(tmp_path / 'c.xlsx'))
    assert pd.ExcelFile(ruta).sheet_names == ['Resumen Global', 'Por Clase ABC', 'Cambios de Segmento',
                                              'Detalle por SKU', 'ICD Empresa', 'ICD por ABC',
                                              'ICD por Segmento', 'ICD Distribución']


# ══════════════════════════════════════════════════════════════════
# Traducciones (#13)
# ══════════════════════════════════════════════════════════════════
def _acciones_reales():
    import seeds.plantillas_categorias as s
    nombres = set()
    for p in s.PLANTILLAS_SISTEMA:
        nombres |= {r['accion'] for r in p['reglas']} | {p['accion_por_defecto']} | set(p['acciones_de_compra'])
    return nombres


def test_explicacion_cubre_todas_las_acciones_reales():
    import preve_pipeline as pp
    from preve_traducciones import EXPLICACION_ACCION, nota_accion
    reales = _acciones_reales()
    assert len(reales) == 15 and reales <= set(EXPLICACION_ACCION)
    # las de la lógica clásica (_clasificar_accion) también
    base = {'stock_actual': 10, 'rop': 5, 'demanda_p50_141d': 10, 'compra_sugerida_unid': 0}
    casos = [dict(base, segmento='Sin historia suficiente'), dict(base, demanda_p50_141d=0),
             dict(base, demanda_p50_141d=6), dict(base, tendencia_venta=-1, clase_abc='A', margen_pct=0.3),
             dict(base, clase_abc='A', margen_pct=0.05), dict(base, compra_sugerida_unid=3), base]
    assert {pp._clasificar_accion(c)[0] for c in casos} <= set(EXPLICACION_ACCION)
    assert nota_accion('ACCION_NUEVA', 'motivo concreto') == 'motivo concreto'      # desconocida → motivo_accion
    assert nota_accion('LIQUIDAR', 'x', 'menos confiable').endswith('⚠ menos confiable')


def test_traducir_modelos_sin_jerga():
    from preve_traducciones import traducir_modelo
    jerga = ('GBM', 'SES', 'SBA', 'Croston', 'Naive', 'TSB', 'Holt', 'CatBoost', 'NegBin', 'Pipeline')
    for n in mb.expandir_modelos(mb.MODELOS_COMPARABLES) + ['GBM global (Poisson)', 'LightGBM']:
        t = traducir_modelo(n)
        assert t and not any(j in t for j in jerga), (n, t)
        assert traducir_modelo(n, modo_avanzado=True) == n
    assert traducir_modelo('Promedio móvil 6 sem.') == 'Promedio de las últimas 6 semanas'


def test_confianza_y_precision():
    from preve_traducciones import interpretar_wmape, nivel_confianza, texto_precision
    assert [nivel_confianza(x) for x in (10, 30, 30.1, 60, 61, np.nan, None)] == [
        'Alta', 'Alta', 'Media', 'Media', 'Baja', 'Sin dato', 'Sin dato']
    assert texto_precision(35.4) == 'Precisión estimada: 35% de margen de error'
    assert interpretar_wmape(70)[1] == 'rojo' and 'WMAPE' not in interpretar_wmape(70)[2]


def test_columnas_de_negocio_no_mutan_el_dataframe():
    from preve_traducciones import agregar_columnas_negocio
    df = pd.DataFrame({'sku': ['A', 'B'], 'modelo_asignado': ['GBM global (Poisson)', 'Croston-TSB'],
                       'segmento': ['Intermitente', 'Regular / alta rotación'], 'accion': ['RECOMPRAR', 'X'],
                       'motivo_accion': ['m1', 'motivo propio'], 'nota_confianza': ['', 'ojo']})
    copia = df.copy()
    out = agregar_columnas_negocio(df)
    pd.testing.assert_frame_equal(df, copia)
    assert list(out['nota_producto']) == [
        'Comprar: el stock no alcanza para cubrir la venta esperada hasta que llegue el próximo pedido.',
        'motivo propio ⚠ ojo']
    assert out['modelo_negocio'].iloc[1] == 'Método para ventas esporádicas'


def test_excel_de_backtesting_traduce_modelos_salvo_modo_avanzado(tmp_path, log):
    from preve_config import Config
    b = bt.Backtester(datos_sinteticos(), ventana_dias=14, pronosticador=PronosticadorFalso(),
                      carpeta_salida=str(tmp_path / 'a'), log=log)
    res = b.run('2025-02-01', '2025-04-30', simular_compras=False)
    por_modelo = pd.read_excel(res['ruta_excel'], sheet_name='Resumen por Modelo')
    assert set(por_modelo['modelo']) == {'Método principal (aprende de todos los productos)',
                                         'Estimación por categoría y precio'}           # LightGBM / CatBoost
    assert not any('WMAPE' in str(c) for c in por_modelo.columns)
    general = pd.read_excel(res['ruta_excel'], sheet_name='Resumen General', header=None)
    assert str(general.iloc[1, 0]).startswith('Margen de error estimado')
    Config.actual().set('mostrar_metricas_avanzadas', True)
    ruta = bt.exportar_excel(res, str(tmp_path / 'b'))
    assert set(pd.read_excel(ruta, sheet_name='Resumen por Modelo')['modelo']) == {'LightGBM', 'CatBoost'}
