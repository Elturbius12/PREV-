# -*- coding: utf-8 -*-
"""Tests de la Fase 5: motor de reglas, plantillas de categorías y su uso en pipeline/simulación/UI."""
import numpy as np
import pandas as pd
import pytest

import preve_reglas as rg
from seeds.plantillas_categorias import (PLANTILLA_DISTRIBUCION, PLANTILLA_MANUFACTURA, PLANTILLA_RETAIL,
                                         PLANTILLA_RETAIL_CLASICO, PLANTILLAS_SISTEMA, UMBRAL_NEGOCIAR, sembrar)

H, LT = 141, 36


def producto(**kw):
    """Fila con valores neutros; demanda 141 u en 141 días = 1 u/día, así stock = días de stock."""
    base = dict(sku='A', demanda_p50_141d=141.0, stock_actual=50.0, margen_pct=0.3, dato_incompleto=False,
                venta_sem_reciente=7.0, cantidad_a_pedir=100.0, compra_sugerida_unid=100.0, dias_sin_venta=3,
                tendencia_venta=0.0, clase_abc='B', segmento='Regular / alta rotación')
    base.update(kw)
    return base


def accion(plantilla, **kw):
    return rg.clasificar(pd.DataFrame([producto(**kw)]), plantilla, H, LT)['accion'].iloc[0]


# ── validación ────────────────────────────────────────────────────
@pytest.mark.parametrize('roto,mensaje', [
    ({'reglas': []}, 'al menos una regla'),
    ({'reglas': [{'accion': 'X', 'condiciones': []}], 'accion_por_defecto': 'Y'}, 'al menos una condición'),
    ({'reglas': [{'accion': 'X', 'condiciones': [{'variable': 'nope', 'operador': '>', 'valor': 1}]}],
      'accion_por_defecto': 'Y'}, 'variable desconocida'),
    ({'reglas': [{'accion': 'X', 'condiciones': [{'variable': 'margen', 'operador': 'import os', 'valor': 1}]}],
      'accion_por_defecto': 'Y'}, 'operador desconocido'),
    ({'reglas': [{'accion': 'X', 'condiciones': [{'variable': 'margen', 'operador': '>', 'valor': '__import__'}]}],
      'accion_por_defecto': 'Y'}, 'número'),
    ({'reglas': [{'accion': 'X', 'condiciones': [{'variable': 'margen', 'operador': '>', 'valor': 1}]}]},
     'acción por defecto'),
])
def test_plantillas_mal_formadas_se_rechazan(roto, mensaje):
    with pytest.raises(rg.ReglaInvalida, match=mensaje):
        rg.validar_plantilla(roto)


@pytest.mark.parametrize('p', PLANTILLAS_SISTEMA, ids=lambda p: p['nombre'])
def test_plantillas_del_sistema_son_validas(p):
    rg.validar_plantilla(p)
    assert set(p['acciones_de_compra']) <= set(rg.acciones(p))


# ── motor ─────────────────────────────────────────────────────────
def test_gana_la_primera_regla_y_hay_accion_por_defecto():
    p = {'accion_por_defecto': 'NADA', 'acciones_de_compra': [], 'reglas': [
        {'accion': 'PRIMERA', 'condiciones': [{'variable': 'stock_actual', 'operador': '>', 'valor': 10}]},
        {'accion': 'SEGUNDA', 'condiciones': [{'variable': 'stock_actual', 'operador': '>', 'valor': 0}]}]}
    assert accion(p, stock_actual=50) == 'PRIMERA'
    assert accion(p, stock_actual=5) == 'SEGUNDA'
    assert accion(p, stock_actual=0) == 'NADA'


def test_comparacion_contra_otra_variable_por_factor():
    p = {'accion_por_defecto': 'NO', 'reglas': [{'accion': 'SI', 'condiciones': [
        {'variable': 'ventas_sem', 'operador': '<', 'valor': {'variable': 'pronostico_sem', 'factor': 0.7}}]}]}
    assert accion(p, venta_sem_reciente=4.0) == 'SI'          # pronóstico 7/sem × 0.7 = 4.9
    assert accion(p, venta_sem_reciente=5.0) == 'NO'


def test_margen_desconocido_no_cumple_ninguna_condicion():
    p = {'accion_por_defecto': 'NO', 'reglas': [
        {'accion': 'BAJO', 'condiciones': [{'variable': 'margen', 'operador': '<', 'valor': 0.1}]}]}
    assert accion(p, margen_pct=0.0, dato_incompleto=True) == 'NO'
    assert accion(p, margen_pct=0.05) == 'BAJO'


def test_motivo_explica_valores_y_cantidad():
    r = rg.clasificar(pd.DataFrame([producto(stock_actual=10, margen_pct=0.3)]), PLANTILLA_RETAIL, H, LT)
    assert r['accion'].iloc[0] == 'RECOMPRAR' and bool(r['es_compra'].iloc[0])
    assert 'stock_dias = 10' in r['motivo_accion'].iloc[0] and 'Comprar 100 unidades' in r['motivo_accion'].iloc[0]


# ── plantilla RETAIL (especificación) ─────────────────────────────
@pytest.mark.parametrize('kw,esperada', [
    (dict(stock_actual=10, margen_pct=0.2), 'RECOMPRAR'),                     # stock_dias 10 < LT 36
    (dict(stock_actual=10, margen_pct=-0.1, clase_abc='A'), 'NEGOCIAR'),      # sin margen no recompra…
    # …y si no es clase A: REVISAR_COMPRA (Fase 6, antes caía en MANTENER por defecto)
    (dict(stock_actual=10, margen_pct=-0.1, clase_abc='B'), 'REVISAR_COMPRA'),
    (dict(stock_actual=10, margen_pct=0.3, dato_incompleto=True), 'REVISAR_COMPRA'),
    (dict(stock_actual=500, demanda_p50_141d=141), 'LIQUIDAR'),               # 500 días, rotación 0.28
    (dict(stock_actual=45, margen_pct=0.05, clase_abc='A'), 'NEGOCIAR'),
    (dict(stock_actual=45, margen_pct=0.5, venta_sem_reciente=3), 'PUBLICITAR'),
    (dict(stock_actual=250, demanda_p50_141d=141, margen_pct=0.2), 'REDUCIR'),  # 250 días, rotación 0.56
    (dict(stock_actual=45), 'MANTENER'),                                       # entre LT y 60 días
    (dict(dias_sin_venta=200, stock_actual=10), 'DESCONTINUAR'),               # tiene prioridad
])
def test_reglas_retail(kw, esperada):
    assert accion(PLANTILLA_RETAIL, **kw) == esperada


def test_manufactura_y_distribucion():
    assert accion(PLANTILLA_MANUFACTURA, stock_actual=10) == 'REABASTECER'
    assert accion(PLANTILLA_MANUFACTURA, stock_actual=50, venta_sem_reciente=10) == 'PRODUCIR_MAS'
    assert accion(PLANTILLA_MANUFACTURA, stock_actual=100, margen_pct=0.02, tendencia_venta=-1) == 'SUSTITUIR'
    assert accion(PLANTILLA_DISTRIBUCION, stock_actual=10) == 'REPONER'
    assert accion(PLANTILLA_DISTRIBUCION, stock_actual=200) == 'TRANSFERIR_SUCURSAL'
    assert accion(PLANTILLA_DISTRIBUCION, stock_actual=60, margen_pct=0.5, venta_sem_reciente=2) == 'PROMOCIONAR'


# ── RETAIL_CLASICO = lógica anterior, exacta ──────────────────────
def test_retail_clasico_reproduce_la_logica_anterior():
    from preve_pipeline import _clasificar_accion
    rng = np.random.default_rng(7)
    n = 3000
    df = pd.DataFrame({
        'sku': [str(i) for i in range(n)],
        'demanda_p50_141d': rng.choice([0, 0.5, 5, 50, 300], n) * rng.random(n),
        'stock_actual': rng.choice([0, 1, 20, 100, 900], n) * rng.random(n),
        'margen_pct': rng.uniform(-0.2, 0.6, n),
        'tendencia_venta': rng.uniform(-2, 2, n),
        'clase_abc': rng.choice(['A', 'B', 'C'], n),
        'segmento': rng.choice(['Regular / alta rotación', 'Intermitente', 'Lanzamiento nuevo (<13 sem.)',
                                'Sin historia suficiente'], n),
        'compra_sugerida_unid': rng.choice([0, 0, 10, 300], n),
        'rop': 10.0, 'nivel_objetivo': 20.0, 'stock_en_transito': 0.0,
        'dato_incompleto': rng.random(n) < 0.1})
    df['cantidad_a_pedir'] = df['compra_sugerida_unid']
    df.loc[df['dato_incompleto'], 'margen_pct'] = 0.0             # así lo dejaba el pipeline
    legado = df.apply(_clasificar_accion, axis=1, result_type='expand')[0]
    motor = rg.clasificar(df, PLANTILLA_RETAIL_CLASICO, H, LT)['accion']
    assert (legado == motor).all(), df[legado != motor].head()


# ── seeds ─────────────────────────────────────────────────────────
def test_sembrar_es_idempotente_y_no_pisa_ediciones():
    from database import Database
    db = Database.default()
    db.guardar_plantilla_categorias('RETAIL', [{'accion': 'X', 'cuando': 'formato Fase 3'}], 'vieja', True)
    assert 'RETAIL' in sembrar(db)                                # el formato antiguo se reemplaza
    assert sembrar(db) == []                                      # idempotente
    vieja = rg.copiar(PLANTILLA_RETAIL)
    vieja['version'] = 1
    rg.guardar_plantilla(vieja, db, es_sistema=True)
    assert sembrar(db) == ['RETAIL']                              # versión anterior → se actualiza
    propia = dict(rg.copiar(PLANTILLA_RETAIL), nombre='RETAIL_PROPIA', accion_por_defecto='VIGILAR')
    rg.guardar_plantilla(propia, db)
    sembrar(db)
    assert rg.cargar_plantilla('RETAIL_PROPIA', db)['accion_por_defecto'] == 'VIGILAR'   # lo propio no se toca
    assert {'RETAIL', 'RETAIL_CLASICO', 'MANUFACTURA', 'DISTRIBUCION'} <= set(db.listar_plantillas_categorias()['nombre'])


def test_config_siembra_y_usa_retail_por_defecto():
    from preve_config import Config
    cfg = Config.actual()
    assert cfg.get('plantilla_categorias') == 'RETAIL'
    assert rg.cargar_plantilla('RETAIL')['reglas'][0]['accion'] == 'DESCONTINUAR'


# ── integración ───────────────────────────────────────────────────
def test_pipeline_con_plantilla_calcula_variables_y_decision(log):
    from preve_pipeline import calcular_recomendacion
    from tests.test_fase1 import _entradas_recomendacion
    rec = calcular_recomendacion(*_entradas_recomendacion(stock_val=5.0), log, plantilla=PLANTILLA_RETAIL).iloc[0]
    assert rec['venta_sem_reciente'] == 7.0 and rec['dias_sin_venta'] >= 0
    assert rec['accion'] == 'RECOMPRAR' and rec['decision'] == 'COMPRAR'
    dist = calcular_recomendacion(*_entradas_recomendacion(stock_val=5.0), log,
                                  plantilla=PLANTILLA_DISTRIBUCION).iloc[0]
    assert dist['accion'] == 'REPONER' and dist['decision'] == 'COMPRAR'


def test_simulacion_sin_cambios_reproduce_la_recomendacion(log):
    """Regresión: tras la Fase 1 la simulación usaba 'rop' como nivel objetivo."""
    from preve_pipeline import calcular_recomendacion
    from preve_simulacion import simular
    from tests.test_fase1 import _entradas_recomendacion
    rec = calcular_recomendacion(*_entradas_recomendacion(), log, stock_en_transito=pd.Series({'A': 30.0}),
                                 plantilla=PLANTILLA_RETAIL)
    res = simular(rec, {}, log=log, plantilla=PLANTILLA_RETAIL)
    sim = res['recomendacion'].iloc[0]
    assert sim['compra_sugerida_unid'] == rec['compra_sugerida_unid'].iloc[0]
    assert sim['rop'] == pytest.approx(rec['rop'].iloc[0])
    assert sim['accion'] == rec['accion'].iloc[0]


def test_negociar_unificado_en_la_simulacion_simple():
    from preve_simulacion import _reclasificar_accion
    fila = dict(stock_actual=1, demanda_p50_141d=10, rop=5, compra_sugerida_unid=5, tendencia_venta=0)
    assert UMBRAL_NEGOCIAR == 0.10
    assert _reclasificar_accion(dict(fila, margen_pct=0.05)) == 'NEGOCIAR'
    assert _reclasificar_accion(dict(fila, margen_pct=-0.02)) == 'RECOMPRAR'   # antes < -5%: incoherente
    assert _reclasificar_accion(dict(fila, margen_pct=0.2)) == 'RECOMPRAR'


def test_interfaz_usa_la_decision_de_compra():
    import preve_datos as dt
    df = pd.DataFrame([producto(accion='REPONER', decision='COMPRAR', costo_total_compra_pen=500.0,
                                costo_puesto_almacen_pen=5.0, precio_regular_pen=10.0),
                       producto(sku='B', accion='MANTENER', decision='NO COMPRAR', costo_total_compra_pen=0.0,
                                costo_puesto_almacen_pen=5.0, precio_regular_pen=10.0)])
    d = dt.derivar(df)
    assert d['es_compra'].tolist() == [True, False]
    assert d['urgencia'].tolist() == ['PRONTO', 'MONITOREAR']
    assert 'Recomiendo comprar' in dt.explicar(d.iloc[0], pd.Timestamp('2025-01-01'))


def test_historial_cuenta_compras_por_decision():
    from database import Database
    db = Database.default()
    rec = pd.DataFrame({'sku': ['A', 'B'], 'accion': ['REPONER', 'MANTENER'], 'decision': ['COMPRAR', 'NO COMPRAR'],
                        'costo_total_compra_pen': [500.0, 0.0]})
    db.registrar_ejecucion('San José', rec)
    ej = db.consultar_ejecuciones('San José').iloc[0]
    assert (ej['skus_recomprar'], ej['inversion_total']) == (1, 500.0)


def test_informe_describe_acciones_de_la_plantilla():
    from preve_config import Config
    from preve_informe import descripciones_acciones
    Config.actual()
    d = descripciones_acciones()
    assert 'MANTENER' in d and 'DESCONTINUAR' in d and '10%' in d['NEGOCIAR']
