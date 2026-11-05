# -*- coding: utf-8 -*-
"""Tests de la Fase 2: capa de datos (database.py) y script de migración."""
import json
import os
import sqlite3

import pandas as pd
import pytest

from database import Database, MIGRACIONES, metadata, slug


@pytest.fixture
def db():
    return Database.default()


def test_esquema_completo_y_versionado(db):
    assert db.version_esquema() == MIGRACIONES[-1][0]
    esperadas = {'empresas', 'sucursales', 'productos', 'proveedores', 'ciclos_compra', 'ventas_diarias',
                 'stock_actual', 'ordenes_compra_abiertas', 'pronosticos', 'metricas_periodo',
                 'ejecuciones_backtesting', 'decisiones_compra', 'configuracion', 'plantillas_categorias',
                 'categorias_activas'}
    assert esperadas <= set(metadata.tables)
    Database(db.url)                      # reabrir no vuelve a migrar ni falla
    assert db.version_esquema() == MIGRACIONES[-1][0]


@pytest.mark.parametrize('valor', ['texto', 3, 2.5, True, False, {'a': [1, 2]}, [1, 'x']])
def test_configuracion_conserva_tipos(db, valor):
    db.set_config('clave', valor)
    assert db.get_config('clave') == valor
    assert db.get_config('no_existe', 'def') == 'def'


def test_slug_estable():
    assert slug('San José') == 'san_jose'
    assert slug('Santa Bárbara  2') == 'santa_barbara_2'


def test_productos_upsert_idempotente(db):
    db.guardar_productos(pd.DataFrame({'sku': ['A', 'B'], 'nombre': ['a', 'b'], 'precio_regular_pen': [1.5, None]}))
    mapa = db.guardar_productos(pd.DataFrame({'sku': ['A'], 'nombre': ['a nuevo']}))
    assert db.contar('productos') == 2
    p = db.obtener_productos().set_index('sku')
    assert p.loc['A', 'nombre'] == 'a nuevo'
    assert p.loc['A', 'precio_regular'] == 1.5          # no se pisa lo que no viene
    assert set(mapa) == {'A', 'B'}


def test_ventas_upsert_y_filtros(db):
    v = pd.DataFrame({'sku': ['A', 'A', 'B'], 'fecha': ['2025-01-01', '2025-01-02', '2025-01-01'],
                      'cantidad': [2, 3, 5]})
    db.guardar_ventas('San José', v)
    db.guardar_ventas('San José', v.assign(cantidad=[2, 4, 5]))     # reimportar corrige, no duplica
    db.guardar_ventas('Lima', v)
    assert db.contar('ventas_diarias') == 6
    sj = db.obtener_ventas('San José')
    assert len(sj) == 3 and sj['cantidad'].sum() == 11
    assert len(db.obtener_ventas('San José', fecha_desde='2025-01-02')) == 1
    assert set(db.obtener_ventas(skus=['B'])['sucursal']) == {'Lima', 'San José'}


def test_stock_en_transito_solo_ordenes_abiertas(db):
    db.guardar_orden_compra('A', 10, proveedor='Prov X', sucursal='San José')
    db.guardar_orden_compra('A', 5)                                   # sin sucursal: aplica a todas
    db.guardar_orden_compra('A', 99, estado='recibida', sucursal='San José')
    db.guardar_orden_compra('B', 7, sucursal='Lima')
    assert db.obtener_stock_en_transito('San José').to_dict() == {'A': 15}
    assert len(db.listar_proveedores()) == 1


def test_pronosticos_y_metricas(db):
    rec = pd.DataFrame({'sku': ['A'], 'modelo_asignado': ['LightGBM'], 'demanda_p10_141d': [1.0],
                        'demanda_p50_141d': [2.0], 'demanda_p90_141d': [3.0]})
    db.guardar_pronosticos('San José', rec, '2025-01-01 10:00', '2025-01-02', '2025-05-22')
    p = db.obtener_pronosticos('San José')
    assert (p.loc[0, 'modelo'], p.loc[0, 'valor_p50']) == ('LightGBM', 2.0)
    db.guardar_metricas(pd.DataFrame({'sku': ['A'], 'modelo': ['LightGBM'], 'periodo_inicio': ['2025-01-01'],
                                      'periodo_fin': ['2025-01-14'], 'mape': [12.5], 'muestras': [14]}))
    assert db.obtener_metricas('LightGBM').loc[0, 'sku'] == 'A'


def test_decisiones_una_por_producto_sucursal_ciclo(db):
    db.guardar_decision('A', 'San José', 1, 'pospuesto', 'sin plata', '2025-02-01')
    db.guardar_decision('A', 'San José', 1, 'comprado')
    db.guardar_decision('A', 'Lima', 1, 'descartado')
    assert db.contar_decisiones() == 2
    d = db.cargar_decisiones('San José', 1)
    assert d['A']['estado'] == 'comprado' and d['A']['hasta'] is None
    db.borrar_decision('A', 'San José', 1)
    assert db.cargar_decisiones('San José', 1) == {}


def test_plantillas_y_categorias(db):
    db.guardar_plantilla_categorias('RETAIL', [{'nombre': 'RECOMPRAR'}], 'Tienda', True)
    db.guardar_plantilla_categorias('RETAIL', [{'nombre': 'RECOMPRAR'}, {'nombre': 'LIQUIDAR'}], 'Tienda', True)
    pl = db.listar_plantillas_categorias()
    assert len(pl) == 1 and len(pl.loc[0, 'reglas']) == 2
    db.guardar_categorias_activas([{'nombre': 'X', 'condicion_python': 'True'}])
    db.guardar_categorias_activas([{'nombre': 'Y', 'condicion_python': 'False'}])
    assert list(db.listar_categorias_activas()['nombre']) == ['Y']


def test_backtesting_registro(db):
    i = db.guardar_ejecucion_backtesting('2025-01-01', '2025-06-30', 10, 13, 22.5, 'x.xlsx', {'ventana': 14})
    df = db.listar_ejecuciones_backtesting()
    assert df.loc[0, 'id'] == i and json.loads(df.loc[0, 'configuracion_json']) == {'ventana': 14}


# ══════════════════════════════════════════════════════════════════
# Historial antiguo
# ══════════════════════════════════════════════════════════════════
def _crear_historial_legado(carpeta):
    """Reproduce el esquema de preve_historial.db de la versión inicial."""
    ruta = os.path.join(carpeta, 'preve_historial.db')
    conn = sqlite3.connect(ruta)
    conn.executescript("""
        CREATE TABLE ejecuciones (id INTEGER PRIMARY KEY, fecha TEXT, sucursal TEXT, etiqueta TEXT,
            ciclo_numero INTEGER, dia_ciclo INTEGER, importacion_num INTEGER, skus_evaluados INTEGER,
            skus_recomprar INTEGER, inversion_total REAL, wmape REAL, calificacion TEXT, metadata_json TEXT);
        CREATE TABLE recomendaciones (id INTEGER PRIMARY KEY, ejecucion_id INTEGER, sku TEXT, nombre TEXT,
            categoria TEXT, clase_abc TEXT, accion TEXT, stock_actual REAL, demanda_p50 REAL, rop REAL,
            compra_sugerida REAL, costo_total REAL, proveedor_principal TEXT, margen_pct REAL, tendencia_venta REAL);
        CREATE TABLE metricas (id INTEGER PRIMARY KEY, ejecucion_id INTEGER, wmape REAL, medape REAL,
            sesgo_medio_pct REAL, cobertura_pct REAL, calificacion TEXT, quiebres_sin_preve INTEGER,
            quiebres_con_preve INTEGER, quiebres_evitados INTEGER, roi_pct REAL, beneficio_total REAL,
            detalle_json TEXT);
        CREATE TABLE decisiones (id INTEGER PRIMARY KEY, sku TEXT, ciclo_numero INTEGER, sucursal TEXT,
            estado TEXT, razon TEXT, hasta TEXT, fecha TEXT);
        INSERT INTO ejecuciones VALUES (1,'2025-01-01T10:00:00','San José','PRELIMINAR',1,1,1,2,1,500.0,NULL,NULL,NULL);
        INSERT INTO ejecuciones VALUES (2,'2025-01-15T10:00:00','San José','PRELIMINAR',1,15,2,2,1,600.0,30.0,'BUENO',NULL);
        INSERT INTO recomendaciones VALUES (1,1,'A','PROD A','X','A','RECOMPRAR',1,10,12,10,500,'P',0.3,0);
        INSERT INTO recomendaciones VALUES (2,1,'B','PROD B','X','C','LIQUIDAR',50,1,2,0,0,'P',0.1,0);
        INSERT INTO recomendaciones VALUES (3,2,'A','PROD A','X','A','RECOMPRAR',1,12,14,12,600,'P',0.3,0);
        INSERT INTO recomendaciones VALUES (4,2,'B','PROD B','X','C','LIQUIDAR',49,1,2,0,0,'P',0.1,0);
        INSERT INTO metricas VALUES (1,2,30.0,20.0,5.0,80.0,'BUENO',3,1,2,15.0,90.0,NULL);
        INSERT INTO decisiones VALUES (1,'A',1,'San José','comprado','',NULL,'2025-01-16T09:00:00');
    """)
    conn.commit()
    conn.close()
    return ruta


def test_importar_historial_legado_conserva_todo_y_es_idempotente(db, carpeta_salida):
    ruta = _crear_historial_legado(carpeta_salida)
    res = db.importar_historial_legado(ruta)
    assert res == {'ejecuciones': [2, 2], 'recomendaciones': [4, 4], 'metricas': [1, 1], 'decisiones': [1, 1]}
    assert db.importar_historial_legado(ruta) is None            # no se duplica
    assert db.contar('ejecuciones') == 2
    ej = db.consultar_ejecuciones('San José')
    assert list(ej['inversion_total']) == [600.0, 500.0]
    assert db.ultimo_scorecard('San José') == {'wmape': 30.0, 'calificacion': 'BUENO'}
    assert db.cargar_decisiones('San José', 1)['A']['estado'] == 'comprado'


def test_historial_importa_automaticamente_la_carpeta(carpeta_salida):
    import preve_historial as h
    _crear_historial_legado(carpeta_salida)
    comp = h.comparar_ultimas(carpeta_salida, 'San José')
    assert comp['actual']['inversion'] == 600.0 and comp['previa']['inversion'] == 500.0
    assert comp['previa_por_sku'] == {'A': 10.0, 'B': 0.0}


# ══════════════════════════════════════════════════════════════════
# Script de migración
# ══════════════════════════════════════════════════════════════════
def test_script_migracion_conteos_coinciden(tmp_path, carpeta_salida, capsys):
    from scripts.migrar_a_sqlite import main
    _crear_historial_legado(carpeta_salida)
    with open(os.path.join(carpeta_salida, 'ciclo_estado.json'), 'w', encoding='utf-8') as f:
        json.dump({'ciclo_inicio': '2025-01-01', 'ciclo_numero': 1,
                   'importaciones': [{'fecha': '2025-01-01'}, {'fecha': '2025-01-15'}]}, f)
    pd.DataFrame({'sku': ['A', 'B'], 'stock_actual': [1, 49], 'modelo_asignado': ['LightGBM', 'Croston-TSB'],
                  'demanda_p10_141d': [5, 0], 'demanda_p50_141d': [12, 1], 'demanda_p90_141d': [20, 2]}
                 ).to_csv(os.path.join(carpeta_salida, 'recomendacion_compra.csv'), index=False)
    pd.DataFrame({'sku': ['A'], 'cantidad': [8], 'proveedor': ['P'], 'fecha_pedido': ['2025-01-10'],
                  'fecha_estimada_llegada': ['2025-02-15'], 'estado': ['abierta']}
                 ).to_csv(os.path.join(carpeta_salida, 'ordenes_compra_abiertas.csv'), index=False)
    cfg = tmp_path / 'preve_config.json'
    cfg.write_text(json.dumps({'usuario': 'Ana', 'sucursal': 'San José',
                               'sucursales': {'San José': {'entrada': '', 'salida': carpeta_salida},
                                              'Lima': {'entrada': '', 'salida': ''}}}), encoding='utf-8')
    assert main(['--config', str(cfg)]) == 0
    salida = capsys.readouterr().out
    assert 'NO COINCIDE' not in salida
    db = Database.default()
    assert db.get_config('usuario_nombre') == 'Ana'
    assert len(db.get_config('ciclo_estado:san_jose')['importaciones']) == 2
    assert db.obtener_stock_en_transito('San José').to_dict() == {'A': 8}
    assert set(db.listar_ciclos_compra()['nombre']) == {'Internacional', 'Local'}
    # segunda corrida: no duplica el historial
    assert main(['--config', str(cfg)]) == 0
    assert db.contar('ejecuciones') == 2
