# -*- coding: utf-8 -*-
"""Tests de la Fase 3: configuración parametrizable y asistente inicial."""
import datetime
import json

import numpy as np
import pandas as pd
import pytest

import preve_config
from preve_config import Config, ConfigError, ParametrosModelo


@pytest.fixture
def cfg():
    return Config.actual()


@pytest.fixture
def json_legado(tmp_path, monkeypatch):
    ruta = tmp_path / 'preve_config.json'
    ruta.write_text(json.dumps({
        'usuario': 'Prevedor', 'sucursal': 'San José', 'ultima_duracion_s': 142.0,
        'sucursales': {'San José': {'entrada': 'C:/datos', 'salida': 'C:/salida'},
                       'Santa Barbara': {'entrada': '', 'salida': ''}, 'Lima': {'entrada': '', 'salida': ''}}}),
        encoding='utf-8')
    monkeypatch.setattr(preve_config, 'RUTA_CONFIG_JSON', str(ruta))
    return ruta


# ── valores por defecto = comportamiento original ─────────────────
def test_defaults_replican_los_valores_que_estaban_fijos(cfg):
    import preve_pipeline as pp
    p = cfg.parametros_modelo()
    assert p == ParametrosModelo()
    assert (p.horizonte_dias, p.lead_time_dias, p.igv_factor) == (141, 36, 1.18)
    assert p.z_nivel_servicio == pp.Z_NIVEL_SERVICIO
    assert (p.adi_umbral, p.cv2_umbral, p.semanas_lanzamiento) == (pp.ADI_UMBRAL, pp.CV2_UMBRAL, pp.SEMANAS_LANZAMIENTO)
    assert [c['nombre'] for c in cfg.ciclos] == ['Internacional', 'Local']


def test_set_valida_tipos_e_invalida_cache(cfg):
    cfg.set('adi_umbral', 1.5)
    assert cfg.get('adi_umbral') == 1.5
    assert Config(cfg.db).get('adi_umbral') == 1.5            # persistido
    for clave, malo in (('adi_umbral', 'x'), ('semanas_lanzamiento', 1.5), ('wizard_completado', 'si'),
                        ('z_nivel_servicio', 3)):
        with pytest.raises(ConfigError):
            cfg.set(clave, malo)


def test_importa_preve_config_json_una_sola_vez(json_legado):
    cfg = Config.actual()
    assert cfg.empresa['nombre'] == 'Yuju Store'
    assert cfg.get('usuario_nombre') == 'Prevedor'
    assert cfg.nombres_sucursales == ['San José', 'Santa Barbara', 'Lima']
    assert cfg.sucursal('San José')['almacenes'] == ['Tienda Juliaca', 'Almacén Juliaca']
    assert cfg.sucursal('Lima')['almacenes'] == ['YUJU STORE PRINCIPAL']
    assert cfg.carpetas('San José') == {'entrada': 'C:/datos', 'salida': 'C:/salida'}
    cfg.set('usuario_nombre', 'Otro')
    Config.reiniciar()
    assert Config.actual().get('usuario_nombre') == 'Otro'    # no se re-importa


def test_sucursales_quitar_desactiva_sin_borrar(json_legado):
    cfg = Config.actual()
    cfg.guardar_sucursales([{'nombre': 'San José', 'almacenes': ['Tienda Juliaca']},
                            {'nombre': 'Cusco', 'almacenes': ['Tienda Cusco', 'Almacén Cusco']}])
    assert cfg.nombres_sucursales == ['San José', 'Cusco']
    assert len(cfg.sucursales) == 4                             # Santa Barbara y Lima siguen, inactivas
    with pytest.raises(ConfigError):
        cfg.guardar_sucursales([])
    with pytest.raises(ConfigError):
        cfg.guardar_sucursales([{'nombre': 'A', 'almacenes': []}, {'nombre': 'a', 'almacenes': []}])


def test_n_ciclos_y_el_principal_es_el_de_mayor_horizonte(cfg):
    cfg.guardar_ciclos([{'nombre': 'Local', 'duracion_dias': 30, 'lead_time_dias': 10},
                        {'nombre': 'China', 'duracion_dias': 120, 'lead_time_dias': 60},
                        {'nombre': 'Express', 'duracion_dias': 7, 'lead_time_dias': 2}])
    assert [c['nombre'] for c in cfg.ciclos] == ['China', 'Local', 'Express']
    assert cfg.ciclo_principal['horizonte_pronostico_dias'] == 180
    assert cfg.ciclo_principal['dia_reporte_final'] == 119
    with pytest.raises(ConfigError):
        cfg.guardar_ciclos([{'nombre': 'X', 'duracion_dias': 10, 'lead_time_dias': 1, 'dia_reporte_final': 11}])


def test_parametros_por_sucursal_usan_sus_almacenes(json_legado):
    p = Config.actual().parametros_modelo('San José')
    assert p.almacenes_propios == frozenset({'tienda_juliaca', 'almacen_juliaca'})


def test_guardar_parametros_es_todo_o_nada(cfg):
    base = {'z_A': '1.9', 'z_B': '1,5', 'z_C': 1, 'adi_umbral': 1.4, 'cv2_umbral': 0.5, 'semanas_lanzamiento': 10,
            'dias_entre_importaciones': 7, 'ventas_son_salidas_de_almacen': True, 'lgbm_params': '{"n_estimators": 50}'}
    with pytest.raises(ConfigError):
        cfg.guardar_parametros_modelo(dict(base, cv2_umbral='abc'))
    assert cfg.get('adi_umbral') == 1.32                       # no se escribió nada
    with pytest.raises(ConfigError):
        cfg.guardar_parametros_modelo(dict(base, lgbm_params='[1, 2]'))
    cfg.guardar_parametros_modelo(base)
    p = cfg.parametros_modelo()
    assert p.z_nivel_servicio == {'A': 1.9, 'B': 1.5, 'C': 1.0}
    assert (p.adi_umbral, p.semanas_lanzamiento, p.lgbm_params) == (1.4, 10, {'n_estimators': 50})
    assert p.ventas_son_salidas_de_almacen is True


# ── ciclo en la base ───────────────────────────────────────────────
def test_estado_del_ciclo_por_sucursal_en_la_base(carpeta_salida):
    import os
    import preve_ciclo as pc
    with open(os.path.join(carpeta_salida, 'ciclo_estado.json'), 'w', encoding='utf-8') as f:
        json.dump({'ciclo_inicio': '2025-01-01', 'ciclo_numero': 4, 'importaciones': []}, f)
    info = pc.registrar_importacion(carpeta_salida, datetime.date(2025, 1, 10), sucursal='San José')
    assert info['ciclo_numero'] == 4                            # importado del JSON
    os.remove(os.path.join(carpeta_salida, 'ciclo_estado.json'))
    assert pc.cargar_estado_ciclo(carpeta_salida, 'San José')['ciclo_numero'] == 4    # ya vive en la base
    assert pc.cargar_estado_ciclo(carpeta_salida, 'Lima') is None                    # independiente por sucursal


def test_etiquetas_escalan_con_la_duracion_del_ciclo(carpeta_salida):
    import preve_ciclo as pc
    assert pc.limites_etiqueta(105) == (42, 84)
    inicio = datetime.date(2025, 1, 1)
    pc.registrar_importacion(carpeta_salida, inicio, duracion_dias=30)
    assert pc.registrar_importacion(carpeta_salida, inicio + datetime.timedelta(days=15),
                                    duracion_dias=30)['etiqueta'] == 'BORRADOR'
    info = pc.registrar_importacion(carpeta_salida, inicio + datetime.timedelta(days=31), duracion_dias=30)
    assert (info['ciclo_numero'], info['dia_ciclo']) == (2, 2)


# ── pipeline parametrizado ─────────────────────────────────────────
def _traslado(origen, destino):
    from tests.test_fase1 import _listado, _ventas, _compras, _movs
    from preve_pipeline import construir_dim_producto
    dim, mapa_df = construir_dim_producto(_listado([('111', 'PROD A', 50, 1)]), print)
    v = _ventas([('111', 'PROD A', '01-01-2025 09:00', 0)])
    c = pd.DataFrame(columns=['COD. BARRA', 'PRODUCTO', 'FECHA', 'CANTIDAD'])
    m = _movs([('PROD A', '03-01-2025 11:00 AM', 10, 'Traslado', origen, destino)])
    return dim, mapa_df, v, c, m


@pytest.mark.parametrize('almacenes,esperado', [
    (['Tienda Juliaca', 'Almacén Juliaca'], 60),     # San José: sale del almacén → antes había 10 más
    (['SANTA BARBARA'], 40),                         # Santa Bárbara: le llega → antes había 10 menos
    (['YUJU STORE PRINCIPAL'], 50),                  # Lima: no le afecta
])
def test_traslados_segun_almacenes_de_la_sucursal(almacenes, esperado, log):
    from preve_pipeline import reconstruir_stock, construir_mapas
    dim, mapa_df, v, c, m = _traslado('Almacén Juliaca', 'SANTA BARBARA')
    stock, _, _ = reconstruir_stock(v, c, m, dim, *construir_mapas(dim, mapa_df), pd.Timestamp('2025-01-03'),
                                    log, almacenes_propios=almacenes)
    assert stock.loc['2025-01-02', '111'] == esperado


def test_mapeo_de_columnas_de_ventas(log):
    from preve_pipeline import aplicar_mapeo_ventas
    v = pd.DataFrame({'Fecha': ['01-01-2025 10:00'], 'EAN': ['111'], 'Unidades': [3], 'PRODUCTO': ['A']})
    res = aplicar_mapeo_ventas(v, {'fecha': 'Fecha', 'codigo_barras': 'EAN', 'cantidad': 'Unidades'}, log)
    assert {'FECHA/HORA', 'CÓDIGO DE BARRA', 'CANTIDAD', 'PRODUCTO'} <= set(res.columns)
    with pytest.raises(ValueError, match='codigo_barras'):
        aplicar_mapeo_ventas(v, {'fecha': 'Fecha', 'cantidad': 'Unidades'}, log)


def test_recomendacion_usa_igv_y_z_configurados(log):
    from preve_pipeline import calcular_recomendacion
    from tests.test_fase1 import _entradas_recomendacion
    base = calcular_recomendacion(*_entradas_recomendacion(), log).iloc[0]
    p = ParametrosModelo(igv_factor=1.0, z_nivel_servicio={'A': 3.0, 'B': 2.0, 'C': 1.0})
    otro = calcular_recomendacion(*_entradas_recomendacion(), log, params=p).iloc[0]
    assert otro['margen_pct'] > base['margen_pct']             # sin IGV, el margen sube
    assert otro['stock_seguridad'] == pytest.approx(base['stock_seguridad'] * 3.0 / 1.645)


def test_rop_usa_ss_escalado_al_lead_time(log):
    from preve_pipeline import calcular_recomendacion
    from tests.test_fase1 import _entradas_recomendacion
    r = calcular_recomendacion(*_entradas_recomendacion(), log).iloc[0]
    assert r['stock_seguridad_lead_time'] == pytest.approx(r['stock_seguridad'] * np.sqrt(36 / 141))
    assert r['nivel_objetivo'] == pytest.approx(r['demanda_p50_141d'] + r['stock_seguridad'])


def test_simulacion_usa_lead_time_configurado(log):
    from preve_simulacion import simular
    rec = pd.DataFrame({'sku': ['A'], 'demanda_p50_141d': [100.0], 'stock_actual': [0.0],
                        'compra_sugerida_unid': [120.0], 'costo_total_compra_pen': [600.0], 'rop': [120.0],
                        'stock_seguridad': [20.0], 'costo_puesto_almacen_pen': [5.0]})
    r = simular(rec, {'lead_time_dias': 40}, log=log, lead_time_actual=10)
    assert r['recomendacion']['stock_seguridad'].iloc[0] == pytest.approx(20.0 * 2)   # √(40/10) = 2


# ── interfaz de configuración (dict) y asistente ──────────────────
def test_cargar_y_guardar_config_de_la_interfaz(json_legado):
    import preve_datos as dt
    c = dt.cargar_config()
    assert c['usuario'] == 'Prevedor' and c['sucursales']['San José']['salida'] == 'C:/salida'
    c['usuario'] = 'Ana'
    c['sucursales']['Lima']['entrada'] = 'D:/lima'
    dt.guardar_config(c)
    Config.reiniciar()
    c2 = dt.cargar_config()
    assert c2['usuario'] == 'Ana' and c2['sucursales']['Lima']['entrada'] == 'D:/lima'
    assert dt.LEAD == 36 and dt.HORIZONTE_DIAS == 141 and dt.SUCURSALES == ['San José', 'Santa Barbara', 'Lima']


def test_asistente_valida_y_guarda(json_legado):
    import preve_wizard as w
    cfg = Config.actual()
    d = w.estado_inicial(cfg)
    assert d['empresa']['nombre'] == 'Yuju Store' and d['plantilla'] == 'RETAIL'
    assert d['sucursales'][0]['almacenes'] == 'Tienda Juliaca, Almacén Juliaca'
    assert all(not w.validar_paso(i, d) for i in range(len(w.PASOS)))
    malo = dict(d, empresa=dict(d['empresa'], igv_factor='18'))
    assert w.validar_paso(0, malo)
    with pytest.raises(ConfigError):
        w.guardar_asistente(cfg, malo)
    assert cfg.get('wizard_completado') is False
    d['ciclos'] = [{'nombre': 'Internacional', 'duracion_dias': '90', 'lead_time_dias': '30',
                    'dia_reporte_final': '', 'horizonte_pronostico_dias': ''}]
    d['empresa']['igv_factor'] = '1,18'
    w.guardar_asistente(cfg, d)
    assert cfg.get('wizard_completado') is True
    assert [c['horizonte_pronostico_dias'] for c in cfg.ciclos] == [120]


def test_asistente_lee_columnas_de_un_archivo(tmp_path):
    from preve_wizard import columnas_de_archivo
    ruta = tmp_path / 'ventas.xlsx'
    with pd.ExcelWriter(ruta) as xw:
        pd.DataFrame([['Reporte de ventas', None, None], ['FECHA', 'PRODUCTO', 'CANTIDAD'], ['x', 'y', 1]]
                     ).to_excel(xw, header=False, index=False)
    assert columnas_de_archivo(str(ruta)) == ['FECHA', 'PRODUCTO', 'CANTIDAD']
