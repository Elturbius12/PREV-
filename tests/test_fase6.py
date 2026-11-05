# -*- coding: utf-8 -*-
"""Tests de la Fase 6: backtesting walk-forward (con un pronosticador falso: no entrena modelos)."""
import os

import numpy as np
import pandas as pd
import pytest

import preve_backtesting as bt

INICIO, FIN = pd.Timestamp('2025-01-01'), pd.Timestamp('2025-06-29')
HUECO = (pd.Timestamp('2025-03-03'), pd.Timestamp('2025-03-17'))      # nadie vende ni compra


def datos_sinteticos():
    fechas = pd.date_range(INICIO, FIN, freq='D')
    filas = []
    for f in fechas:
        if HUECO[0] <= f < HUECO[1]:
            continue
        filas.append(('A', f, -2.0))                                    # regular: 2 u/día
        if f >= pd.Timestamp('2025-04-01'):
            filas.append(('B', f, -1.0))                                # nuevo: primera venta en abril
        if f.weekday() == 0:
            filas.append(('C', f, -1.0))                                # intermitente: 1 u los lunes
    vd = pd.DataFrame(filas, columns=['sku', 'fecha', 'mov'])
    dim = pd.DataFrame({'sku': ['A', 'B', 'C'], 'nombre': ['Prod A', 'Prod B', 'Prod C'], 'categoria': 'X',
                        'subcategoria': 'Y', 'costo_puesto_almacen_pen': 5.0, 'precio_regular_pen': 11.8,
                        'stock_listado_actual': 100.0, 'codigo_barras': ['A', 'B', 'C']})
    stock = pd.DataFrame(100.0, index=fechas, columns=['A', 'B', 'C'])
    compras = pd.Series([pd.Timestamp('2025-02-10'), pd.Timestamp('2025-05-05')])
    return bt.DatosBacktest(vd, dim, stock, compras)


class PronosticadorFalso:
    """Promedio diario histórico × horizonte. Registra la última fecha que vio en cada llamada."""

    def __init__(self):
        self.vistas = []

    def __call__(self, ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
        from preve_pipeline import construir_panel_y_segmentacion
        self.vistas.append(ventas_hasta['fecha'].max())
        panel, seg, fecha_fin = construir_panel_y_segmentacion(stock_hasta, ventas_hasta, dim, log, params=params)
        dias = max(1, (ventas_hasta['fecha'].max() - ventas_hasta['fecha'].min()).days + 1)
        media = (-ventas_hasta.groupby('sku')['mov'].sum()) / dias
        pron = pd.DataFrame({'sku': media.index, 'demanda_p50_141d': media.values * horizonte_dias})
        pron['demanda_p10_141d'] = pron['demanda_p50_141d'] * 0.5
        pron['demanda_p90_141d'] = pron['demanda_p50_141d'] * 1.5
        pron = pron.merge(seg[['sku', 'modelo_asignado', 'segmento']], on='sku', how='left')
        return pron, panel, seg, fecha_fin


@pytest.fixture
def backtester(tmp_path, log):
    from preve_config import Config
    falso = PronosticadorFalso()
    ciclo = dict(Config.actual().ciclo_principal, duracion_dias=30, dia_reporte_final=29,
                 horizonte_pronostico_dias=40, lead_time_dias=10)
    b = bt.Backtester(datos_sinteticos(), ventana_dias=14, ciclo=ciclo, inicio_ciclo='2025-01-01',
                      pronosticador=falso, carpeta_salida=str(tmp_path / 'reportes'), log=log)
    b.falso = falso
    return b


# ── piezas ────────────────────────────────────────────────────────
def test_ventanas_alineadas_al_lunes_y_completas(backtester):
    v = backtester.ventanas('2025-02-05', '2025-03-10')
    assert [(a.date().isoformat(), b.date().isoformat()) for a, b in v] == [
        ('2025-02-10', '2025-02-24'), ('2025-02-24', '2025-03-10')]
    assert all(a.weekday() == 0 for a, _ in v)


def test_error_porcentual_con_mape_simetrico():
    e = bt.error_porcentual([10, 0, 5, 0], [8, 0, 0, 4])
    # real > 0 → APE (|0−4|/4 = 100%); real = 0 → sMAPE (0 si ambos son 0, 200 si solo hubo pronóstico)
    assert e.tolist() == pytest.approx([25.0, 0.0, 200.0, 100.0])


@pytest.mark.parametrize('inicio,esperado', [
    ('2025-01-06', 'ok'), (HUECO[0], 'sin_datos_globales'),
])
def test_estado_de_la_ventana(backtester, inicio, esperado):
    t = pd.Timestamp(inicio)
    assert backtester.estado_ventana(t, t + pd.Timedelta(days=14)) == esperado


def test_solo_compras_sin_historia_de_ventas(backtester):
    d = backtester.datos
    d.ventas_diarias = d.ventas_diarias[d.ventas_diarias['fecha'] >= '2025-02-17']
    t = pd.Timestamp('2025-02-03')
    assert backtester.estado_ventana(t, t + pd.Timedelta(days=14)) == 'solo_compras'


def test_dias_de_reporte_final(backtester):
    assert [d.date().isoformat() for d in backtester.dias_reporte('2025-02-01', '2025-04-30')] == [
        '2025-02-28', '2025-03-30', '2025-04-29']


# ── ejecución completa ───────────────────────────────────────────
def test_walk_forward_sin_fuga_y_con_datos_insuficientes(backtester):
    res = backtester.run('2025-02-01', '2025-05-31')
    ventanas = backtester.ventanas('2025-02-01', '2025-05-31')
    evaluadas = [t for t, f in ventanas if backtester.estado_ventana(t, f) == 'ok']
    # sin fuga: en cada ventana el modelo solo vio datos anteriores a su inicio
    for t, visto in zip(evaluadas, backtester.falso.vistas):
        assert visto < t
    # el hueco se omite y se registra
    assert res['omitidas']['estado'].tolist() == ['sin_datos_globales']
    det = res['detalle']
    # B no se evalúa antes de su primera venta (1 de abril)
    assert det.loc[det['sku'] == 'B', 'periodo_inicio'].min() >= pd.Timestamp('2025-04-07')
    # C (intermitente) se evalúa también en ventanas donde no vende
    assert det['sku'].eq('C').sum() == len(evaluadas)
    # A: pronóstico = promedio histórico × 14 contra 28 reales. Antes del hueco el error es 0; después, el
    # hueco de 14 días sin ventas baja el promedio (hasta ~16%) y el error se va reduciendo
    err_a = det.loc[det['sku'] == 'A'].sort_values('periodo_inicio')['error_pct']
    assert err_a.iloc[0] == pytest.approx(0) and err_a.max() < 20 and err_a.iloc[-1] < err_a.max()
    assert res['total_periodos'] == len(evaluadas) and res['total_productos'] == 3


def test_resultados_quedan_en_la_base_y_en_el_excel(backtester):
    from database import Database
    res = backtester.run('2025-02-01', '2025-05-31')
    db = Database.default()
    ej = db.listar_ejecuciones_backtesting()
    assert len(ej) == 1 and ej.loc[0, 'total_periodos'] == res['total_periodos']
    assert db.contar('metricas_periodo') > len(res['detalle'])                    # por producto + por modelo
    p = db.obtener_pronosticos('San José')
    assert p['venta_real'].notna().all() and len(p) == len(res['detalle'])
    hojas = pd.ExcelFile(res['ruta_excel']).sheet_names
    assert os.path.basename(res['ruta_excel']).startswith('backtesting_')
    assert hojas[:2] == ['Resumen General', 'Resumen por Modelo']
    assert any(h.startswith('Período 1 (') for h in hojas)
    assert 'Simulación Compras' in hojas and 'Ventanas omitidas' in hojas
    periodo = pd.read_excel(res['ruta_excel'], sheet_name=[h for h in hojas if h.startswith('Período 1')][0])
    assert list(periodo.columns) == ['Código', 'Nombre', 'Modelo', 'Clasif', 'Pronóstico', 'Real', 'Error Abs',
                                     'Error %', 'Bias']


def test_simulacion_de_compra_valida_contra_el_horizonte_real(backtester):
    rec, resumen = backtester.simular_compra(pd.Timestamp('2025-03-30'))
    assert resumen['estado'] == 'ok' and resumen['horizonte'] == 40
    a = rec.set_index('sku').loc['A']
    assert a['real_horizonte'] == 80 and bool(a['horizonte_completo'])  # 2 u/día × 40 días reales
    assert a['faltante'] == max(0, 80 - a['stock_actual'] - a['compra_sugerida_unid'])
    assert backtester.falso.vistas[-1] < pd.Timestamp('2025-03-30')   # "a medianoche": sin datos del día


def test_simulacion_con_horizonte_parcial_extrapola(backtester):
    rec, resumen = backtester.simular_compra(pd.Timestamp('2025-06-10'))      # solo 20 de 40 días con datos
    a = rec.set_index('sku').loc['A']
    assert resumen['dias_cubiertos'] == 20 and 'parcial' in resumen['estado']
    assert a['real_horizonte'] == 40 and a['real_horizonte_estimado'] == 80 and not a['horizonte_completo']


def test_cancelar(backtester):
    backtester.cancelar = lambda: True
    with pytest.raises(bt.BacktestCancelado):
        backtester.run('2025-02-01', '2025-05-31')


def test_rango_demasiado_corto(backtester):
    with pytest.raises(ValueError, match='no alcanza'):
        backtester.run('2025-02-03', '2025-02-08')
