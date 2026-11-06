# -*- coding: utf-8 -*-
"""Resultados por período: cada análisis vive en salida/AAAA-MM-DD_a_AAAA-MM-DD."""
import os
import zipfile

import pandas as pd
import pytest

import preve_depurar as dep
import preve_datos as dt
from preve_orquestador import carpeta_periodo, _carpeta_periodo_actual, _intentar_validacion
from preve_utils import ultimo_periodo, listar_periodos, fin_de_periodo, es_carpeta_periodo


def _csv_rec(carpeta, unidades=5):
    os.makedirs(carpeta, exist_ok=True)
    pd.DataFrame({'sku': ['A', 'B'], 'nombre': ['a', 'b'], 'accion': ['RECOMPRAR', 'VIGILAR'],
                  'demanda_p50_141d': [10.0, 2.0], 'stock_actual': [1.0, 9.0],
                  'compra_sugerida_unid': [unidades, 0]}).to_csv(os.path.join(carpeta, 'recomendacion_compra.csv'),
                                                                 index=False)


# ── carpeta_periodo ────────────────────────────────────────────
def test_carpeta_periodo_nombre_y_creacion(carpeta_salida):
    ruta = carpeta_periodo(carpeta_salida, '2026-01-01', pd.Timestamp('2026-01-14'))
    assert os.path.basename(ruta) == '2026-01-01_a_2026-01-14'
    assert os.path.isdir(ruta) and os.path.dirname(ruta) == carpeta_salida
    assert carpeta_periodo(carpeta_salida, '2026-01-01', '2026-01-14') == ruta      # idempotente


def test_periodo_actual_usa_dias_entre_importaciones(carpeta_salida):
    ruta = _carpeta_periodo_actual(carpeta_salida, pd.Timestamp('2026-01-28 15:00'), 14)
    assert os.path.basename(ruta) == '2026-01-15_a_2026-01-28'
    ruta7 = _carpeta_periodo_actual(carpeta_salida, pd.Timestamp('2026-01-28'), 7)
    assert os.path.basename(ruta7) == '2026-01-22_a_2026-01-28'


@pytest.mark.parametrize('malo', [None, pd.NaT, 'no es fecha'])
def test_periodo_sin_fecha_clara_usa_hoy_y_no_falla(carpeta_salida, malo):
    import datetime
    ruta = _carpeta_periodo_actual(carpeta_salida, malo, 14)
    hoy = datetime.date.today().isoformat()
    assert os.path.basename(ruta) == f'{hoy}_a_{hoy}'


# ── ultimo_periodo / Datos ─────────────────────────────────────
def test_ultimo_periodo_toma_el_mas_reciente_e_ignora_lo_demas(carpeta_salida):
    for n in ('2026-01-01_a_2026-01-14', '2026-02-12_a_2026-02-25', '2026-01-15_a_2026-01-28'):
        _csv_rec(os.path.join(carpeta_salida, n))
    os.makedirs(os.path.join(carpeta_salida, 'basura'))
    os.makedirs(os.path.join(carpeta_salida, '2026-13_a_x'))
    open(os.path.join(carpeta_salida, '2030-01-01_a_2030-01-14'), 'w').close()      # archivo, no carpeta
    assert os.path.basename(ultimo_periodo(carpeta_salida)) == '2026-02-12_a_2026-02-25'
    assert len(listar_periodos(carpeta_salida)) == 3
    assert fin_de_periodo(ultimo_periodo(carpeta_salida)).isoformat() == '2026-02-25'
    assert es_carpeta_periodo('2026-01-01_a_2026-01-14') and not es_carpeta_periodo('2026-01-01')


def test_ultimo_periodo_ignora_un_analisis_que_fallo_a_medias(carpeta_salida):
    _csv_rec(os.path.join(carpeta_salida, '2026-01-01_a_2026-01-14'))
    os.makedirs(os.path.join(carpeta_salida, '2026-01-15_a_2026-01-28'))            # sin recomendación
    assert os.path.basename(ultimo_periodo(carpeta_salida)) == '2026-01-01_a_2026-01-14'
    assert os.path.basename(ultimo_periodo(carpeta_salida, None)) == '2026-01-15_a_2026-01-28'


def test_datos_lee_del_ultimo_periodo(carpeta_salida):
    _csv_rec(os.path.join(carpeta_salida, '2026-01-01_a_2026-01-14'), unidades=1)
    _csv_rec(os.path.join(carpeta_salida, '2026-01-15_a_2026-01-28'), unidades=99)
    d = dt.Datos(carpeta_salida, 'San José')
    assert d.tiene_datos
    assert d.carpeta == carpeta_salida
    assert os.path.basename(d.carpeta_periodo) == '2026-01-15_a_2026-01-28'
    assert os.path.basename(d.ultimo_periodo()) == '2026-01-15_a_2026-01-28'
    assert d.df['compra_sugerida_unid'].max() == 99


def test_datos_sin_periodos_no_falla(carpeta_salida):
    open(os.path.join(carpeta_salida, 'recomendacion_compra.csv'), 'w').write('sku\nA\n')   # suelto de antes: se ignora
    d = dt.Datos(carpeta_salida, 'San José')
    assert d.tiene_datos is False and d.carpeta_periodo is None and d.ultimo_periodo() is None
    assert d.kpis() is None
    assert dt.Datos(os.path.join(carpeta_salida, 'no_existe'), 'San José').tiene_datos is False


# ── validación: lee el período anterior ────────────────────────
def test_validacion_con_referencia_antigua_en_la_raiz(carpeta_salida, log):
    """Compatibilidad: sin período anterior se usa recomendacion_anterior.csv de la raíz (sin moverlo)."""
    import json
    rec = pd.DataFrame({'sku': ['A'], 'demanda_p50_141d': [141.0], 'demanda_p10_141d': [70.0],
                        'demanda_p90_141d': [200.0], 'stock_actual': [10.0], 'compra_sugerida_unid': [5.0],
                        'accion': ['RECOMPRAR'], 'clase_abc': ['A'], 'costo_puesto_almacen_pen': [5.0],
                        'precio_regular_pen': [11.8]})
    rec.to_csv(os.path.join(carpeta_salida, 'recomendacion_anterior.csv'), index=False)
    with open(os.path.join(carpeta_salida, 'recomendacion_anterior_meta.json'), 'w') as f:
        json.dump({'fecha_datos': '2025-01-10'}, f)
    vd = pd.DataFrame({'sku': 'A', 'fecha': pd.date_range('2025-01-01', periods=30), 'mov': -1.0})
    per = carpeta_periodo(carpeta_salida, '2025-01-11', '2025-01-24')
    res = _intentar_validacion(rec, vd, carpeta_salida, log, fecha_datos=pd.Timestamp('2025-01-24'),
                               carpeta_periodo=per)
    assert res is not None and os.path.exists(os.path.join(per, 'PREVE_Scorecard.xlsx'))
    assert os.path.exists(os.path.join(carpeta_salida, 'recomendacion_anterior.csv'))      # no se tocó


# ── depurar ────────────────────────────────────────────────────
@pytest.fixture
def app_aislada(tmp_path, monkeypatch):
    """Evita que los tests de depuración toquen preve.db / preve.log reales de la app."""
    app = tmp_path / 'app'
    app.mkdir()
    monkeypatch.setattr(dep, 'APP_DIR', str(app))
    return app


def test_depurar_borra_solo_periodos(carpeta_salida, app_aislada):
    p1 = os.path.join(carpeta_salida, '2026-01-01_a_2026-01-14')
    p2 = os.path.join(carpeta_salida, '2026-01-15_a_2026-01-28')
    _csv_rec(p1)
    _csv_rec(p2)
    ordenes = os.path.join(carpeta_salida, 'ordenes_compra_abiertas.csv')
    suelto = os.path.join(carpeta_salida, 'recomendacion_compra.csv')          # archivo viejo sin patrón de fecha
    ciclo = os.path.join(carpeta_salida, 'ciclo_estado.json')
    for f in (ordenes, suelto, ciclo):
        open(f, 'w').write('x')
    otra = os.path.join(carpeta_salida, 'mis_notas')
    os.makedirs(otra)
    open(os.path.join(otra, 'a.txt'), 'w').write('x')
    casi = os.path.join(carpeta_salida, '2026-01-01_a_2026-01-14_copia')       # no cumple el patrón exacto
    os.makedirs(casi)

    prev = dep.preview_depuracion(carpetas_salida=[carpeta_salida])
    assert len(prev['periodos']) == 2 and prev['total_bytes'] > 0
    assert os.path.isdir(p1)                                                    # el preview no borra

    rep = dep.depurar_datos(conservar_config=True, carpetas_salida=[carpeta_salida])
    assert not rep['errores'], rep['errores']
    assert len(rep['periodos_borrados']) == 2
    assert not os.path.exists(p1) and not os.path.exists(p2)
    for f in (ordenes, suelto, ciclo, os.path.join(otra, 'a.txt')):
        assert os.path.exists(f), f
    assert os.path.isdir(casi)
    assert 'análisis guardados' in dep.resumen_para_usuario(rep)


def test_depurar_respalda_los_periodos_en_el_zip(carpeta_salida, app_aislada):
    _csv_rec(os.path.join(carpeta_salida, '2026-01-01_a_2026-01-14'))
    rep = dep.depurar_datos(conservar_config=True, carpetas_salida=[carpeta_salida])
    with zipfile.ZipFile(rep['respaldo']) as z:
        nombres = z.namelist()
    assert any(n.endswith('2026-01-01_a_2026-01-14/recomendacion_compra.csv') for n in nombres)


def test_depurar_multisucursal_borra_periodos_de_subcarpetas(carpeta_salida, app_aislada):
    suc = os.path.join(carpeta_salida, 'San_José')
    per = os.path.join(suc, '2026-01-01_a_2026-01-14')
    _csv_rec(per)
    otro = os.path.join(suc, 'datos_importantes.csv')
    open(otro, 'w').write('x')
    traslados = os.path.join(carpeta_salida, '2026-01-01_a_2026-01-14')
    os.makedirs(traslados)
    rep = dep.depurar_datos(conservar_config=True, hacer_respaldo=False, carpetas_salida=[carpeta_salida])
    assert not os.path.exists(per) and not os.path.exists(traslados)
    assert os.path.exists(otro) and os.path.isdir(suc)
    assert rep['respaldo'] is None


def test_depurar_sin_carpetas_de_salida_no_falla(app_aislada):
    rep = dep.depurar_datos(conservar_config=True, hacer_respaldo=False, carpetas_salida=['/no/existe', ''])
    assert rep['periodos_borrados'] == [] and not rep['errores']


# ── punta a punta ──────────────────────────────────────────────
from tests.test_fase7 import carpeta_erp  # noqa: E402,F401  (fixture con reportes sintéticos del ERP)


def test_pipeline_completo_escribe_todo_en_la_carpeta_de_periodo(carpeta_erp, carpeta_salida, log):
    from preve_orquestador import ejecutar_pipeline
    erp, _ = carpeta_erp
    rec = ejecutar_pipeline(erp, carpeta_salida, progreso=log, sucursal='San José')
    assert len(rec) > 0

    periodos = listar_periodos(carpeta_salida)
    assert len(periodos) == 1
    per = periodos[0]
    assert es_carpeta_periodo(os.path.basename(per))
    assert os.path.basename(per) == '2025-09-23_a_2025-10-06', per          # 14 días que terminan en la fecha de los datos
    esperados = {'recomendacion_compra.csv', 'skus_sin_pronostico.csv', 'PREVE_Clasificacion_Productos.xlsx',
                 'PREVE_Recomendacion_Compra.xlsx', 'dashboard.html'}
    assert esperados <= set(os.listdir(per)), set(os.listdir(per))
    assert {'recomendacion_compra_local.csv', 'PREVE_Informe_Gerencia.docx', 'PREVE_Informe_Gerencia.pdf'} <= set(os.listdir(per))
    # En la raíz solo queda lo que no es resultado de un análisis
    raiz = set(os.listdir(carpeta_salida))
    assert raiz == {os.path.basename(per), 'ordenes_compra_abiertas.csv'}, raiz
    # La app lee del período
    d = dt.Datos(carpeta_salida, 'San José')
    assert d.tiene_datos and d.carpeta_periodo == per
    assert any(os.path.join(per, 'PREVE_Clasificacion_Productos.xlsx') in m for m in log.mensajes)
