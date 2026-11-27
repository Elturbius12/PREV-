# -*- coding: utf-8 -*-
"""PREVÉ — Genera TODAS las tablas y figuras del artículo con un solo comando (análisis retrospectivo).

    python generar_resultados_articulo.py                      # sucursales con carpeta de entrada configurada
    python generar_resultados_articulo.py --sucursal "San José" --entrada "C:/datos/SJ"
    python generar_resultados_articulo.py --rapido             # sin horizontes largos ni el zoo completo
    python generar_resultados_articulo.py --congelar           # además copia todo a salida/articulo_canonico/
    python generar_resultados_articulo.py --muestra-anonima    # además genera la muestra anónima de 50 SKU

Corrida canónica: con --congelar, al terminar se copia salida/articulo/ a salida/articulo_canonico/ con un
META.json (fecha, Python, pip freeze, SHA-256 de cada archivo de entrada, comando y commit de git). Toda corrida
sin --congelar compara el WMAPE de la Tabla 4 con el canónico y avisa (WARNING) si difiere en más de 0,5 pp.
El artículo cita siempre las cifras de salida/articulo_canonico/.

Salida: salida/articulo/  →  tablas_articulo.xlsx (todas las tablas, una hoja cada una, con índice),
figuras/Figura_1..3.png, metodologia.txt, log_articulo.txt y datos_csv/ (las mismas tablas en CSV).

Solo usa las 4 fuentes del ERP (ventas, compras, movimientos, catálogo). No escribe en la base de datos ni
cambia la configuración. Todo lo no calculable se reporta como "no disponible" en metodologia.txt.
"""
import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import platform
import sys
import time

import numpy as np
import pandas as pd

import analisis_censura as ac
import preve_articulo as pa
import preve_modelos_baseline as mb
from preve_utils import get_logger

logger = get_logger('generar_articulo')

CARPETA = os.path.join('salida', 'articulo')
CARPETA_FIGURAS = os.path.join(CARPETA, 'figuras')
CARPETA_CSV = os.path.join(CARPETA, 'datos_csv')
CARPETA_CANONICA = os.path.join('salida', 'articulo_canonico')
TOLERANCIA_WMAPE_PP = 0.5
MODELOS_RESIDUOS = (mb.MODELO_PRODUCCION, mb.MODELO_GBM)
HORIZONTES = (14, 40, 70, 141)
MODELOS_HORIZONTE = (mb.MODELO_PRODUCCION, mb.MODELO_GBM, mb.MODELO_GBM_SIN_DISP, mb.MODELO_SBA,
                     mb.nombre_promedio_movil(8))
NOMBRES_METRICAS = {'wmape': 'WMAPE (%)', 'mae': 'MAE (u)', 'rmse': 'RMSE (u)', 'bias': 'Bias (u)',
                    'ic_inf': 'IC95 inf (%)', 'ic_sup': 'IC95 sup (%)', 'muestras': 'Observaciones',
                    'productos': 'SKU'}


class Registro:
    """Log + lista de limitaciones para metodologia.txt."""
    def __init__(self, ruta):
        self.f = open(ruta, 'w', encoding='utf-8')
        self.limitaciones = []

    def __call__(self, m):
        linea = f'[{datetime.datetime.now():%H:%M:%S}] {m}'
        try:
            print(linea, flush=True)
        except UnicodeEncodeError:                      # consola Windows cp1252
            print(linea.encode('ascii', 'replace').decode(), flush=True)
        self.f.write(linea + '\n')
        self.f.flush()

    def limitacion(self, m):
        self.limitaciones.append(m)
        self('LIMITACIÓN: ' + m)


def usar_carpeta(ruta):
    """Cambia la carpeta de salida (por defecto salida/articulo)."""
    global CARPETA, CARPETA_FIGURAS, CARPETA_CSV
    CARPETA = ruta
    CARPETA_FIGURAS = os.path.join(ruta, 'figuras')
    CARPETA_CSV = os.path.join(ruta, 'datos_csv')


def _guardar(tablas, nombre, df):
    df = df.copy()
    tablas[nombre] = df
    # Un solo Excel con todas las tablas (tablas_articulo.xlsx); los CSV quedan aparte para reproducibilidad
    df.to_csv(os.path.join(CARPETA_CSV, f'{nombre}.csv'), index=False, encoding='utf-8-sig')


def _preparar_carpeta():
    """Crea salida/articulo/{figuras,datos_csv} y quita lo generado por corridas anteriores (incluido el
    formato antiguo con un .xlsx y un .csv sueltos por tabla), para que nunca se mezclen resultados."""
    import glob
    import shutil
    for sub in (CARPETA_FIGURAS, CARPETA_CSV):
        shutil.rmtree(sub, ignore_errors=True)
        os.makedirs(sub, exist_ok=True)
    for patron in ('Tabla_*.csv', 'Tabla_*.xlsx', 'Datos_*.csv', 'Datos_*.xlsx', 'Figura_*.png', 'tablas_articulo.xlsx'):
        for r in glob.glob(os.path.join(CARPETA, patron)):
            try:
                os.remove(r)
            except OSError:
                pass


def _resumen_modelos(detalle):
    from preve_backtesting import resumir
    filas = [dict(modelo=m, **resumir(g), productos=int(g['sku'].nunique())) for m, g in detalle.groupby('modelo')]
    return pd.DataFrame(filas)


def _desagregado(detalle, por):
    from preve_backtesting import desagregar
    return desagregar(detalle, por)[list(por) + ['wmape', 'mae', 'rmse', 'bias', 'muestras', 'productos']]


def cargar_sucursales(args, log):
    from preve_config import Config
    conf = Config.actual()
    if args.sucursal:
        lista = [(args.sucursal, args.entrada or (conf.sucursal(args.sucursal) or {}).get('entrada', ''))]
    else:
        lista = [(s['nombre'], s.get('entrada', '')) for s in conf.sucursales if s.get('activa', True)]
    ok = []
    for nombre, carpeta in lista:
        if not carpeta or not os.path.isdir(carpeta):
            log.limitacion(f'Sucursal "{nombre}": sin carpeta de datos configurada; no entra en el análisis.')
            continue
        ok.append((nombre, carpeta))
    return conf, ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sucursal')
    ap.add_argument('--entrada', help='Carpeta con los 4 reportes del ERP (con --sucursal)')
    ap.add_argument('--desde', help='Inicio del backtesting (AAAA-MM-DD). Por defecto: 1.ª venta + semanas_calentamiento')
    ap.add_argument('--hasta', help='Fin del backtesting. Por defecto: última venta')
    ap.add_argument('--semanas-calentamiento', type=int, default=26)
    ap.add_argument('--propuesto', default=mb.MODELO_PRODUCCION)
    ap.add_argument('--modelos', nargs='*', help='Modelos (por defecto, modelos_activos de la configuración)')
    ap.add_argument('--horizontes', nargs='*', type=int, default=list(HORIZONTES))
    ap.add_argument('--bootstrap', type=int, default=pa.N_BOOTSTRAP)
    ap.add_argument('--semilla', type=int, default=pa.SEMILLA)
    ap.add_argument('--rapido', action='store_true', help='Solo horizonte 14 d y sin simulación de compras')
    ap.add_argument('--congelar', action='store_true',
                    help='Al terminar, copia la salida a salida/articulo_canonico/ con META.json')
    ap.add_argument('--muestra-anonima', action='store_true',
                    help='Genera <salida>/muestra_anonima/ (50 SKU anonimizados) antes del análisis')
    ap.add_argument('--salida', default=os.path.join('salida', 'articulo'), help='Carpeta de salida')
    args = ap.parse_args(argv)

    usar_carpeta(args.salida)
    os.makedirs(CARPETA, exist_ok=True)
    _preparar_carpeta()
    log = Registro(os.path.join(CARPETA, 'log_articulo.txt'))
    np.random.seed(args.semilla)
    t_ini = time.time()
    conf, sucursales = cargar_sucursales(args, log)
    if not sucursales:
        log('No hay ninguna sucursal con datos. Fin.')
        return 1
    if args.muestra_anonima:
        from preve_muestra import generar_muestra_anonima
        r = generar_muestra_anonima(sucursales[0][1], os.path.join(CARPETA, 'muestra_anonima'), log=log)
        log(f'Muestra anónima: {r["skus"]} SKU → {r["carpeta"]}')
    pedidos = args.modelos or conf.get('modelos_activos') or list(mb.MODELOS_COMPARABLES)
    validos, desconocidos = mb.validar_modelos(pedidos)
    if desconocidos:
        log.limitacion(f'Modelos desconocidos ignorados: {desconocidos}')
    from preve_backtesting import DatosBacktest
    from preve_pipeline import construir_panel_y_segmentacion

    tablas, meta = {}, {'sucursales': [], 'semilla': args.semilla, 'bootstrap': args.bootstrap}
    _guardar(tablas, 'Tabla_0_variables', pa.diccionario_variables())
    muestra, censura_sku, detalles, compras, importancias, horiz = [], [], [], [], [], []
    for nombre, carpeta in sucursales:
        log(f'══ Sucursal {nombre} ({carpeta}) ══')
        params = conf.parametros_modelo(nombre)
        modelos = mb.expandir_modelos(validos, params.ventanas_promedio_movil)
        datos = DatosBacktest.desde_carpeta(carpeta, nombre, log=lambda m: None, params=params)
        panel, seg, ff = construir_panel_y_segmentacion(datos.stock, datos.ventas_diarias, datos.dim,
                                                        lambda m: None, params=params)
        primera, ultima = datos.ventas_diarias['fecha'].min(), datos.ultima_fecha
        desde = pd.Timestamp(args.desde) if args.desde else primera + pd.Timedelta(weeks=args.semanas_calentamiento)
        hasta = pd.Timestamp(args.hasta) if args.hasta else ultima
        meta['sucursales'].append(dict(sucursal=nombre, carpeta=carpeta, datos_desde=primera.date(),
                                       datos_hasta=ultima.date(), backtest_desde=desde.date(),
                                       backtest_hasta=hasta.date(), params=params, modelos=modelos))
        # ── C1/C2: censura e ICD ──
        cens = ac.analizar_censura(panel, datos.stock, seg, adi_umbral=params.adi_umbral,
                                   cv2_umbral=params.cv2_umbral)
        censura_sku.append(cens['por_sku'].assign(sucursal=nombre))
        muestra.append(dict(sucursal=nombre, panel=panel, seg=seg, cens=cens['por_sku']))
        if datos.stock is None or datos.stock.empty:
            log.limitacion(f'{nombre}: sin stock reconstruido; la censura no es medible.')
        # ── A/B: walk-forward de cada modelo (horizonte por defecto) + simulación de compras ──
        hz0 = int(conf.get('dias_entre_importaciones'))
        for i, m in enumerate(modelos):
            t0 = time.time()
            try:
                det, comp = pa.evaluar_modelo(datos, m, desde, hasta, params, horizonte_dias=hz0,
                                              simular_compras=not args.rapido, sucursal=nombre)
            except Exception as e:  # noqa: BLE001
                logger.exception('Falló %s', m)
                log.limitacion(f'{nombre} · {m}: no se pudo evaluar ({type(e).__name__}: {e}).')
                continue
            detalles.append(det)
            if comp is not None:
                compras.append(comp)
            log(f'  [{i + 1}/{len(modelos)}] {m}: {det["periodo_inicio"].nunique()} ventanas, '
                f'WMAPE {pa.bootstrap_wmape(det, n=1)["wmape"].iloc[0]:.1f} % ({time.time() - t0:.0f} s)')
        # ── A2: importancia de variables ──
        try:
            from preve_backtesting import _importancias_comparacion
            imp = _importancias_comparacion(datos, hasta, params, hz0, modelos, log)
            if len(imp):
                importancias.append(imp.assign(sucursal=nombre))
        except Exception as e:  # noqa: BLE001
            log.limitacion(f'{nombre}: importancia de variables no disponible ({e}).')
        # ── B3: sensibilidad al horizonte ──
        for h in ([] if args.rapido else args.horizontes):
            if h == hz0:
                continue
            for m in [x for x in MODELOS_HORIZONTE if x in modelos]:
                try:
                    det, _ = pa.evaluar_modelo(datos, m, desde, hasta, params, horizonte_dias=h,
                                               simular_compras=False, sucursal=nombre)
                    horiz.append(det)
                    log(f'  Horizonte {h} d · {m}: {det["periodo_inicio"].nunique()} ventanas')
                except Exception as e:  # noqa: BLE001
                    log.limitacion(f'{nombre} · horizonte {h} d · {m}: no evaluable ({e}).')
        meta['hz0'] = hz0

    detalle = pd.concat(detalles, ignore_index=True) if detalles else pd.DataFrame()
    por_sku_c = pd.concat(censura_sku, ignore_index=True)
    icd = ac.indice_ceguera(por_sku_c)

    # Tabla 1 — muestra
    filas = []
    for s in muestra:
        p, sg = s['panel'], s['seg']
        sem = pd.to_datetime(p['semana_inicio'])
        base = dict(sucursal=s['sucursal'], skus=int(p['sku'].nunique()), semana_desde=sem.min().date(),
                    semana_hasta=sem.max().date(), semanas=int(sem.nunique()))
        filas.append(dict(base, dimension='Total', grupo='Total', n_skus=base['skus']))
        for dim_, col, df in (('Segmento ADI/CV²', 'segmento_original', s['cens']),
                              ('Clase ABC', 'clase_abc', sg), ('Segmento PREVÉ', 'segmento', sg)):
            if col in df.columns:
                for k, n in df[col].value_counts().sort_index().items():
                    filas.append(dict(base, dimension=dim_, grupo=k, n_skus=int(n)))
    t1 = pd.DataFrame(filas)
    t1['pct_skus'] = (t1['n_skus'] / t1['skus'] * 100).round(1)
    _guardar(tablas, 'Tabla_1_muestra', t1)

    # Tabla 2 — ICD
    t2 = pd.concat([icd['empresa'].rename(columns={'nivel': 'grupo'}).assign(corte='Empresa'),
                    icd['por_sucursal'].rename(columns={'sucursal': 'grupo'}).assign(corte='Sucursal'),
                    icd['por_clase_abc'].rename(columns={'clase_abc': 'grupo'}).assign(corte='Clase ABC'),
                    icd['por_segmento'].rename(columns={'segmento': 'grupo'}).assign(corte='Segmento ADI/CV²')],
                   ignore_index=True)
    t2 = t2[['corte', 'grupo'] + ac.COLUMNAS_ICD].round(4)
    _guardar(tablas, 'Tabla_2_ICD', t2)
    _guardar(tablas, 'Tabla_2b_ICD_distribucion', icd['distribucion'])
    _guardar(tablas, 'Tabla_2c_ICD_por_SKU', icd['por_sku'].round(4))

    # Tabla 3 — reclasificación
    t3 = pd.crosstab(por_sku_c['segmento_original'], por_sku_c['segmento_corregido'], margins=True,
                     margins_name='Total').reset_index().rename(columns={'segmento_original': 'original \\ corregido'})
    _guardar(tablas, 'Tabla_3_reclasificacion', t3)

    if detalle.empty:
        log.limitacion('Ningún modelo pudo evaluarse: tablas 4 a 9 no disponibles.')
    else:
        comun = pa.base_comun(detalle)
        n_comun = len(comun)
        log(f'Base común de comparación: {n_comun:,} observaciones SKU×ventana de {len(detalle):,}.')
        # Tabla 4 — comparación de modelos con IC bootstrap
        t4 = _resumen_modelos(comun).merge(pa.bootstrap_wmape(comun, n=args.bootstrap, semilla=args.semilla)
                                           [['modelo', 'ic_inf', 'ic_sup', 'ventanas']], on='modelo')
        t4 = t4[['modelo', 'wmape', 'ic_inf', 'ic_sup', 'mae', 'rmse', 'bias', 'muestras', 'productos',
                 'ventanas']].sort_values('wmape').round(3)
        _guardar(tablas, 'Tabla_4_comparacion_modelos', t4)
        _guardar(tablas, 'Tabla_4b_por_sucursal', _desagregado(comun, ['sucursal', 'modelo']).round(3))
        _guardar(tablas, 'Tabla_4c_por_segmento', _desagregado(comun, ['clasificacion', 'modelo']).round(3))
        _guardar(tablas, 'Tabla_4d_por_ABC', _desagregado(comun, ['clase_abc', 'modelo']).round(3))
        # Tabla 5 — ablación
        abl = comun[comun['modelo'].isin([mb.MODELO_GBM, mb.MODELO_GBM_SIN_DISP])]
        if abl['modelo'].nunique() == 2:
            partes = []
            for corte, col in (('Global', None), ('Segmento', 'clasificacion'), ('Clase ABC', 'clase_abc'),
                               ('Sucursal', 'sucursal')):
                g = _desagregado(abl.assign(_t='Total'), [col or '_t', 'modelo']).rename(columns={col or '_t': 'grupo'})
                partes.append(g.assign(corte=corte))
            t5 = pd.concat(partes, ignore_index=True)
            t5 = t5.pivot_table(index=['corte', 'grupo'], columns='modelo',
                                values=['wmape', 'mae', 'bias', 'productos']).round(3)
            t5.columns = [f'{a} · {b}' for a, b in t5.columns]
            t5 = t5.reset_index()
            t5['mejora_wmape_pp'] = (t5[f'wmape · {mb.MODELO_GBM_SIN_DISP}'] - t5[f'wmape · {mb.MODELO_GBM}']).round(3)
            _guardar(tablas, 'Tabla_5_ablacion_disponibilidad', t5)
        else:
            log.limitacion('Ablación con/sin disponibilidad no disponible: falta alguna variante del GBM.')
        # Tabla 9 — significancia
        t9 = pa.comparar_significancia(comun, args.propuesto, n=args.bootstrap, semilla=args.semilla)
        if len(t9):
            _guardar(tablas, 'Tabla_9_significancia', t9.round(4))
            if mb.MODELO_GBM in comun['modelo'].unique():
                _guardar(tablas, 'Tabla_9b_significancia_ablacion',
                         pa.comparar_significancia(abl, mb.MODELO_GBM, n=args.bootstrap, semilla=args.semilla).round(4))
        else:
            log.limitacion(f'Modelo propuesto "{args.propuesto}" sin resultados: tabla 9 no disponible.')
        _guardar(tablas, 'Datos_detalle_backtesting', detalle[[c for c in (
            'sucursal', 'modelo', 'horizonte_dias', 'periodo_inicio', 'periodo_fin', 'sku', 'clasificacion',
            'clase_abc', 'con_stock', 'pronostico', 'real', 'real_total', 'error_abs', 'bias') if c in detalle]])
        # Tabla 10 — diagnóstico de residuos
        mods_res = [m for m in dict.fromkeys(MODELOS_RESIDUOS + (args.propuesto,)) if m in comun['modelo'].unique()]
        if mods_res:
            _guardar(tablas, 'Tabla_10_residuos', pa.analisis_residuos(comun, mods_res, semilla=args.semilla).round(4))
        else:
            log.limitacion('Diagnóstico de residuos no disponible: faltan los modelos Pipeline / GBM.')
        # Tabla 12 — intervalos P10/P90 fuera de muestra (M8)
        oos = pa.intervalos_oos(comun)
        _guardar(tablas, 'Tabla_12_cobertura_intervalos_oos', pa.cobertura_intervalos(oos).round(3))
        _guardar(tablas, 'Datos_cuantiles_residuos_sku', pa.cuantiles_residuos_sku(comun).round(4))

    # Tabla 6 — importancias
    if importancias:
        _guardar(tablas, 'Tabla_6_importancia_variables',
                 pd.concat(importancias, ignore_index=True).sort_values(['sucursal', 'modelo', 'importancia'],
                                                                         ascending=[True, True, False]).round(5))
    else:
        log.limitacion('Importancia de variables no disponible.')

    # Tabla 7 — economía
    if compras:
        cv = pd.concat(compras, ignore_index=True)
        base_e0 = cv[cv['modelo'] == pa.BASELINE_EXCESO]
        if base_e0.empty:
            log.limitacion(f'Sin simulación de "{pa.BASELINE_EXCESO}": exceso evitado no disponible.')
        b = base_e0 if len(base_e0) else None
        t7 = pd.DataFrame([dict(escenario=pa.escenario_de(m), modelo=m, **pa.resumen_economico(g, baseline=b))
                           for m, g in cv.groupby('modelo')])
        t7b = pd.DataFrame([dict(sucursal=s, escenario=pa.escenario_de(m), modelo=m, **pa.resumen_economico(
            g, baseline=None if b is None else b[b['sucursal'] == s]))
            for (s, m), g in cv.groupby(['sucursal', 'modelo'])])
        _guardar(tablas, 'Tabla_7_simulacion_compras', t7.sort_values('nivel_servicio_pct', ascending=False).round(2))
        _guardar(tablas, 'Tabla_7b_simulacion_por_sucursal', t7b.round(2))
        meta['ciclos'] = int(cv['dia_reporte'].nunique())
    else:
        cv = None
    # Tabla 11 — escenarios E0/E1/E2
    t4 = tablas.get('Tabla_4_comparacion_modelos')
    if t4 is not None:
        t11 = pa.tabla_escenarios(t4, cv)
        _guardar(tablas, 'Tabla_11_escenarios', t11.round(3))
        faltan = list(t11.loc[t11['wmape'].isna(), 'modelo'])
        if faltan:
            log.limitacion(f'Escenarios sin resultados de precisión: {faltan}.')
    if cv is None:
        log.limitacion('Simulación de compras no disponible (modo rápido o sin días de reporte en el rango).')

    # Tabla 8 — horizontes
    if horiz or not detalle.empty:
        hd = pd.concat(horiz + [detalle[detalle['modelo'].isin(MODELOS_HORIZONTE)]], ignore_index=True)
        t8 = _desagregado(hd, ['horizonte_dias', 'modelo'])
        t8 = t8.merge(hd.groupby(['horizonte_dias', 'modelo'])['periodo_inicio'].nunique().rename('ventanas')
                      .reset_index(), on=['horizonte_dias', 'modelo'])
        _guardar(tablas, 'Tabla_8_sensibilidad_horizonte', t8.round(3))
        pocos = t8[t8['ventanas'] < 3]
        if len(pocos):
            log.limitacion('Horizontes con menos de 3 ventanas (resultado poco estable): '
                           + ', '.join(sorted({f'{h} d' for h in pocos['horizonte_dias']})))

    with pd.ExcelWriter(os.path.join(CARPETA, 'tablas_articulo.xlsx'), engine='openpyxl') as xw:
        hojas = [k for k in tablas if not k.startswith('Datos_')]
        pd.DataFrame({'Hoja': [k[:31] for k in hojas], 'Filas': [len(tablas[k]) for k in hojas]}
                     ).to_excel(xw, sheet_name='Indice', index=False)
        for k in hojas:
            tablas[k].to_excel(xw, sheet_name=k[:31], index=False)

    figuras(icd, detalle, tablas, log)
    if not args.congelar:
        for aviso in comparar_con_canonico(tablas.get('Tabla_4_comparacion_modelos'), meta):
            log.limitacion(aviso)
    escribir_metodologia(meta, tablas, log, args, time.time() - t_ini)
    log(f'Listo en {(time.time() - t_ini) / 60:.1f} min → {os.path.abspath(CARPETA)}')
    if args.congelar:
        log.f.close()
        destino = congelar(CARPETA, CARPETA_CANONICA, sucursales, argv, meta)
        print(f'Corrida canónica congelada en {os.path.abspath(destino)}', flush=True)
    else:
        log.f.close()
    return 0


# ══════════════════════════════════════════════════════════════════
# Corrida canónica
# ══════════════════════════════════════════════════════════════════
def sha256(ruta, bloque=1 << 20):
    h = hashlib.sha256()
    with open(ruta, 'rb') as f:
        for b in iter(lambda: f.read(bloque), b''):
            h.update(b)
    return h.hexdigest()


def hashes_entrada(sucursales):
    """{sucursal: {tipo: [{archivo, sha256, bytes}]}} de los 4 reportes del ERP de cada sucursal."""
    from preve_pipeline import _clasificar_archivos
    out = {}
    for nombre, carpeta in sucursales:
        clasif, _ = _clasificar_archivos(carpeta)
        out[nombre] = {tipo: [{'archivo': os.path.relpath(a, carpeta), 'sha256': sha256(a),
                               'bytes': os.path.getsize(a)} for a in sorted(archivos)]
                       for tipo, archivos in clasif.items()}
    return out


def _ejecutar(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def metadatos(sucursales, argv=None, meta=None):
    """Contenido de META.json."""
    freeze = _ejecutar([sys.executable, '-m', 'pip', 'freeze'])
    commit = _ejecutar(['git', 'rev-parse', 'HEAD'])
    sucio = _ejecutar(['git', 'status', '--porcelain', '--untracked-files=no'])
    args_cmd = sys.argv[1:] if argv is None else list(argv)
    return {'fecha_ejecucion': datetime.datetime.now().isoformat(timespec='seconds'),
            'python': sys.version, 'plataforma': platform.platform(),
            'librerias': freeze.splitlines() if freeze else None,
            'comando': ' '.join(['python', os.path.basename(__file__)] + args_cmd),
            'git_commit': commit, 'git_cambios_sin_commit': bool(sucio) if sucio is not None else None,
            'sucursales': [n for n, _ in sucursales],
            'semilla': (meta or {}).get('semilla'), 'bootstrap': (meta or {}).get('bootstrap'),
            'archivos_entrada': hashes_entrada(sucursales)}


def congelar(origen, destino, sucursales, argv=None, meta=None):
    """Copia `origen` completo a `destino` (reemplazándolo) y escribe destino/META.json."""
    if os.path.abspath(origen) == os.path.abspath(destino):
        raise ValueError('La carpeta canónica no puede ser la misma que la de salida.')
    shutil.rmtree(destino, ignore_errors=True)
    shutil.copytree(origen, destino)
    with open(os.path.join(destino, 'META.json'), 'w', encoding='utf-8') as f:
        json.dump(metadatos(sucursales, argv, meta), f, ensure_ascii=False, indent=2, default=str)
    return destino


def comparar_con_canonico(t4, meta=None, carpeta=None, tolerancia=TOLERANCIA_WMAPE_PP):
    """Avisos (lista de textos) si el WMAPE de la Tabla 4 difiere del canónico en más de `tolerancia` pp.

    Solo compara si la corrida canónica existe y es de las mismas sucursales. Un modelo que está en una sola
    de las dos corridas también se avisa."""
    carpeta = carpeta or CARPETA_CANONICA
    ruta = os.path.join(carpeta, 'datos_csv', 'Tabla_4_comparacion_modelos.csv')
    if t4 is None or not os.path.exists(ruta):
        return []
    try:
        with open(os.path.join(carpeta, 'META.json'), encoding='utf-8') as f:
            suc_can = json.load(f).get('sucursales')
    except (OSError, ValueError):
        suc_can = None
    suc_act = [s['sucursal'] for s in (meta or {}).get('sucursales', [])]
    if suc_can is not None and meta is not None and sorted(suc_can) != sorted(suc_act):
        return []
    can = pd.read_csv(ruta, encoding='utf-8-sig').set_index('modelo')['wmape']
    act = t4.set_index('modelo')['wmape']
    avisos = []
    for m in sorted(set(can.index) | set(act.index)):
        if m not in act.index or m not in can.index:
            avisos.append(f'WARNING: "{m}" está solo en la corrida {"canónica" if m in can.index else "actual"}.')
            continue
        d = float(act[m]) - float(can[m])
        if abs(d) > tolerancia:
            avisos.append(f'WARNING: WMAPE de "{m}" difiere del canónico en {d:+.2f} pp '
                          f'({float(act[m]):.2f} vs {float(can[m]):.2f}); el artículo cita salida/articulo_canonico/.')
    if avisos:
        logger.warning('Diferencias con la corrida canónica: %s', avisos)
    return avisos


def figuras(icd, detalle, tablas, log):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    d = icd['por_sku'].dropna(subset=['icd'])
    if len(d):
        grupos = sorted(d['clase_abc'].unique())
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.boxplot([d.loc[d['clase_abc'] == g, 'icd'] for g in grupos])
        ax.set_xticks(range(1, len(grupos) + 1), grupos)
        ax.set_xlabel('Clase ABC'); ax.set_ylabel('ICD por SKU'); ax.set_ylim(-0.02, 1.02)
        ax.set_title('Figura 1. Distribución del ICD por categoría ABC')
        fig.tight_layout(); fig.savefig(os.path.join(CARPETA_FIGURAS, 'Figura_1_ICD_por_ABC.png'), dpi=200); plt.close(fig)
    if not detalle.empty:
        c = pa.base_comun(detalle)
        pv = c.assign(_a=(c['pronostico'] - c['real']).abs()).groupby(['modelo', 'periodo_inicio']).agg(
            a=('_a', 'sum'), r=('real', 'sum')).reset_index()
        pv['wmape'] = pv['a'] / pv['r'].where(pv['r'] > 0) * 100
        fig, ax = plt.subplots(figsize=(9, 5))
        for m, g in pv.groupby('modelo'):
            ax.plot(g['periodo_inicio'], g['wmape'], marker='o', ms=3, lw=1, label=m)
        ax.set_ylabel('WMAPE (%)'); ax.set_xlabel('Inicio de la ventana')
        ax.set_title('Figura 2. WMAPE por ventana de backtesting y modelo'); ax.legend(fontsize=7, ncol=2)
        fig.autofmt_xdate(); fig.tight_layout()
        fig.savefig(os.path.join(CARPETA_FIGURAS, 'Figura_2_WMAPE_por_ventana.png'), dpi=200); plt.close(fig)
    t7 = tablas.get('Tabla_7_simulacion_compras')
    if t7 is not None and len(t7):
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.scatter(t7['exceso_pen'], t7['nivel_servicio_pct'])
        for _, r in t7.iterrows():
            ax.annotate(r['modelo'], (r['exceso_pen'], r['nivel_servicio_pct']), fontsize=7,
                        xytext=(3, 3), textcoords='offset points')
        ax.set_xlabel('Exceso de inventario simulado (S/.)'); ax.set_ylabel('Nivel de servicio simulado (%)')
        ax.set_title('Figura 3. Nivel de servicio vs exceso de inventario')
        fig.tight_layout(); fig.savefig(os.path.join(CARPETA_FIGURAS, 'Figura_3_servicio_vs_exceso.png'), dpi=200)
        plt.close(fig)
    else:
        log.limitacion('Figura 3 no disponible (sin simulación de compras).')


def escribir_metodologia(meta, tablas, log, args, segundos):
    import preve_modelo_gbm as gbm
    L = ['METODOLOGÍA — resultados generados por generar_resultados_articulo.py',
         f'Fecha de generación: {datetime.datetime.now():%Y-%m-%d %H:%M}',
         f'Entorno: Python {sys.version.split()[0]}, pandas {pd.__version__}, numpy {np.__version__}, '
         f'{platform.platform()}', f'Duración: {segundos / 60:.1f} min', '',
         '1. DATOS (únicas fuentes: reporte de ventas, reporte de compras, movimientos por producto, catálogo)']
    for s in meta['sucursales']:
        L += [f'  · {s["sucursal"]}: datos {s["datos_desde"]} a {s["datos_hasta"]}; backtesting '
              f'{s["backtest_desde"]} a {s["backtest_hasta"]} (calentamiento: {args.semanas_calentamiento} semanas).']
    t1 = tablas.get('Tabla_1_muestra')
    if t1 is not None:
        for _, r in t1[t1['dimension'] == 'Total'].iterrows():
            L.append(f'    {r["sucursal"]}: {r["skus"]} SKU, {r["semanas"]} semanas ({r["semana_desde"]} a {r["semana_hasta"]}).')
    L += ['  · Stock diario reconstruido desde el stock del catálogo hacia atrás con compras, ventas y movimientos.',
          '', '2. DISEÑO DE EVALUACIÓN',
          f'  · Walk-forward: ventanas no solapadas de {meta.get("hz0")} días; en cada ventana el modelo se '
          'entrena SOLO con datos anteriores a su inicio.',
          '  · Comparación sobre la BASE COMÚN (SKU×ventana pronosticados por todos los modelos).',
          '  · WMAPE = Σ|pronóstico − real| / Σ real × 100; MAE, RMSE y Bias (pronóstico − real) en unidades.',
          f'  · IC 95 % por bootstrap de bloques (ventanas completas), {args.bootstrap} réplicas, semilla {args.semilla}.',
          '  · Diebold-Mariano con corrección HLN sobre d_t = WMAPE_baseline(t) − WMAPE_propuesto(t); '
          'Shapiro-Wilk sobre d_t decide entre DM (normal) y Wilcoxon de rangos con signo (no normal).',
          f'  · Modelo propuesto: {args.propuesto}. Ablación: "{mb.MODELO_GBM}" vs "{mb.MODELO_GBM_SIN_DISP}" '
          '(mismo modelo y datos; única diferencia: variables de disponibilidad av4, av13, av26 y stock).',
          f'  · Sensibilidad al horizonte: {", ".join(map(str, args.horizontes))} días, modelos: '
          f'{", ".join(MODELOS_HORIZONTE)}.',
          '  · Residuos (Tabla 10): e = pronóstico − real. Shapiro-Wilk sobre el residuo medio por ventana (y sobre '
          'los residuos SKU×ventana, submuestra ≤ 5000); Ljung-Box (statsmodels) sobre el residuo medio por ventana '
          'con lag = mín(10, n_ventanas/2).',
          '  · Intervalos P10/P90 fuera de muestra (Tabla 12): r = real − pronóstico; P10 = pronóstico + q10(r), '
          'P90 = pronóstico + q90(r), con los residuos del mismo modelo y SKU en ventanas ANTERIORES (≥ 3; si no, '
          'los de la misma clase ABC). Se compara su cobertura con la del intervalo paramétrico de cada modelo.',
          '  · Escenarios (Tabla 11): ' + '; '.join(f'{e} = {m}' for e, (m, _) in pa.ESCENARIOS.items())
          + '. Mejora = 100 × (WMAPE_E0 − WMAPE_E2) / WMAPE_E0. La economía de la Tabla 11 se calcula sobre la '
          'base común (sucursal, SKU, día de reporte) de los tres modelos.',
          '  · Variables y notación: Tabla 0. Política de compras formal: formulacion_compras.md.',
          '', '3. MODELOS Y PARÁMETROS']
    if meta['sucursales']:
        p = meta['sucursales'][0]['params']
        L.append('  · Modelos evaluados: ' + ', '.join(meta['sucursales'][0]['modelos']))
        L.append('  · ParametrosModelo: ' + '; '.join(f'{k}={v}' for k, v in dataclasses.asdict(p).items()))
    L += [f'  · GBM: HistGradientBoostingRegressor {getattr(gbm, "PARAMS_GBM", "(ver preve_modelo_gbm.py)")}, '
          'random_state=42; importancia por permutación (semilla 42, 5 repeticiones).',
          '  · Baselines (preve_modelos_baseline.py): Naive; promedio móvil 4/8/13 semanas; SES, Holt amortiguado, '
          'Croston clásico y SBA (statsforecast); TSB con Binomial Negativa (alpha_d = alpha_p = 0,1).',
          '', '4. SEMILLAS', f'  · Bootstrap y tests: {args.semilla}. GBM e importancias: 42. numpy global: {args.semilla}.',
          '', '5. ICD (ver ICD_metodo.md)',
          '  · ICD = semanas censuradas / semanas sin venta; semana censurada = venta 0 y disponibilidad < 50 % '
          'de los días; solo dentro de la vida del producto. Agregados: suma de numeradores / suma de denominadores.',
          '', '6. SUPUESTOS DE VALORIZACIÓN'] + [f'  · {s}' for s in pa.SUPUESTOS_VALORIZACION]
    if 'ciclos' in meta:
        L.append(f'  · Ciclos de compra simulados: {meta["ciclos"]}.')
    L += ['', '7. LIMITACIONES']
    L += [f'  · {x}' for x in log.limitaciones] or ['  · (ninguna registrada en esta ejecución)']
    L += ['  · Análisis 100 % retrospectivo: no hubo implementación ni prueba en tiempo real.',
          '  · La venta real usada como referencia también está censurada por quiebres de stock.',
          '  · El costo proviene del catálogo actual (no del costo histórico de cada compra).']
    with open(os.path.join(CARPETA, 'metodologia.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


if __name__ == '__main__':
    sys.exit(main())
