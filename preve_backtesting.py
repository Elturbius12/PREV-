# -*- coding: utf-8 -*-
"""
PREVÉ — Modo backtesting con walk-forward validation (Fase 6).

    bt = Backtester(DatosBacktest.desde_carpeta(carpeta_entrada, 'San José'), params, plantilla)
    res = bt.run('2026-05-01', '2026-10-23')
    res['ruta_excel'], res['mape_promedio']

En cada ventana [t, t + ventana) el modelo se entrena SOLO con ventas anteriores a t, pronostica la
ventana y se compara con lo que realmente se vendió. Además, en cada día de reporte final del ciclo de
compras se genera la sugerencia de compra con los datos disponibles a medianoche de ese día y se valida
contra las ventas reales de todo su horizonte.

Datos insuficientes (6.2):
  - sin ventas ni compras en toda la ventana  → se omite la ventana ("sin datos globales")
  - hay compras pero todavía no ventas        → se omite el pronóstico ("solo compras": el inventario sí cuenta)
  - producto sin primera venta antes de t     → se omite ese producto en esa ventana
  - producto con 0 ventas mientras otros venden → se evalúa normalmente (es demanda intermitente)
"""
import dataclasses
import datetime
import json
import os

import numpy as np
import pandas as pd

from preve_utils import get_logger

logger = get_logger('backtesting')

CARPETA_REPORTES = 'reportes_backtesting'
COLUMNAS_PERIODO = {'sku': 'Código', 'nombre': 'Nombre', 'modelo': 'Modelo', 'clasificacion': 'Clasif',
                    'pronostico': 'Pronóstico', 'real': 'Real', 'error_abs': 'Error Abs', 'error_pct': 'Error %',
                    'bias': 'Bias'}


class BacktestCancelado(Exception):
    pass


# ══════════════════════════════════════════════════════════════════
# Datos
# ══════════════════════════════════════════════════════════════════
@dataclasses.dataclass
class DatosBacktest:
    """Historia completa de una sucursal. ventas_diarias: sku / fecha / mov (mov < 0 = unidades vendidas)."""
    ventas_diarias: pd.DataFrame
    dim: pd.DataFrame
    stock: pd.DataFrame                       # índice fecha × columnas sku (stock al cierre del día)
    fechas_compra: pd.Series                  # fechas (normalizadas) con al menos una compra

    @property
    def primera_venta(self):
        v = self.ventas_diarias[self.ventas_diarias['mov'] < 0]
        return v.groupby('sku')['fecha'].min()

    @property
    def ultima_fecha(self):
        return pd.Timestamp(self.ventas_diarias['fecha'].max()).normalize()

    @classmethod
    def desde_carpeta(cls, carpeta_entrada, sucursal, log=lambda m: None, params=None):
        """Lee los reportes del ERP con las mismas reglas que el pipeline normal."""
        from preve_config import Config
        from preve_pipeline import (aplicar_mapeo_ventas, cargar_datos, construir_dim_producto, construir_mapas,
                                    reconstruir_stock, _parse_fechas)
        conf = Config.actual()
        params = params or conf.parametros_modelo(sucursal)
        listado, ventas, compras, movs = cargar_datos(carpeta_entrada, log)
        ventas = aplicar_mapeo_ventas(ventas, conf.get('mapeo_columnas_ventas'), log)
        dim, mapa_df = construir_dim_producto(listado, log)
        stock, vd, _ = reconstruir_stock(
            ventas, compras, movs, dim, *construir_mapas(dim, mapa_df), listado.attrs.get('fecha_listado'), log,
            ventas_son_salidas_de_almacen=params.ventas_son_salidas_de_almacen,
            compras_son_entradas_de_almacen=params.compras_son_entradas_de_almacen,
            almacenes_propios=params.almacenes_propios)
        fechas = _parse_fechas(compras['FECHA'], ['%d-%m-%Y', '%d-%m-%Y %H:%M'], log, 'Compras').dt.normalize()
        return cls(vd, dim, stock, pd.Series(sorted(fechas.dropna().unique())))

    def hasta(self, fecha):
        """Datos visibles a medianoche de `fecha` (todo lo anterior, nada del día)."""
        f = pd.Timestamp(fecha).normalize()
        return (self.ventas_diarias[self.ventas_diarias['fecha'] < f],
                self.stock[self.stock.index < f])


# ══════════════════════════════════════════════════════════════════
# Pronosticador (inyectable, para testear sin entrenar modelos reales)
# ══════════════════════════════════════════════════════════════════
def pronosticador_pipeline(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    """Segmenta y entrena los 3 modelos con los datos dados. Devuelve (pronostico, panel, segmentacion, fecha_fin)."""
    from preve_pipeline import construir_panel_y_segmentacion, entrenar_y_pronosticar
    p = dataclasses.replace(params, horizonte_dias=float(horizonte_dias))
    panel, seg, fecha_fin = construir_panel_y_segmentacion(stock_hasta, ventas_hasta, dim, log, params=p)
    pron, _ = entrenar_y_pronosticar(panel, seg, dim, fecha_fin, log, params=p)
    pron = pron.merge(seg[['sku', 'modelo_asignado', 'segmento']], on='sku', how='left')
    return pron, panel, seg, fecha_fin


# ══════════════════════════════════════════════════════════════════
# Métricas
# ══════════════════════════════════════════════════════════════════
def error_porcentual(pronostico, real):
    """APE si real > 0; si real = 0, MAPE simétrico (0 si ambos son 0, 200 si solo hubo pronóstico)."""
    f, r = np.asarray(pronostico, dtype=float), np.asarray(real, dtype=float)
    with np.errstate(divide='ignore', invalid='ignore'):
        ape = np.abs(f - r) / r * 100
        smape = np.where((np.abs(f) + np.abs(r)) > 0, 2 * np.abs(f - r) / (np.abs(f) + np.abs(r)) * 100, 0.0)
    return np.where(r > 0, ape, smape)


def resumir(df):
    """MAPE, WMAPE, MAE, RMSE, Bias y muestras de un DataFrame con pronostico/real/error_pct."""
    if df.empty:
        return {'mape': np.nan, 'wmape': np.nan, 'mae': np.nan, 'rmse': np.nan, 'bias': np.nan, 'muestras': 0}
    err = df['pronostico'] - df['real']
    real_tot = df['real'].sum()
    return {'mape': float(df['error_pct'].mean()),
            'wmape': float(err.abs().sum() / real_tot * 100) if real_tot > 0 else np.nan,
            'mae': float(err.abs().mean()), 'rmse': float(np.sqrt((err ** 2).mean())),
            'bias': float(err.mean()), 'muestras': int(len(df))}


# ══════════════════════════════════════════════════════════════════
# Backtester
# ══════════════════════════════════════════════════════════════════
class Backtester:
    def __init__(self, datos, params=None, plantilla=None, ventana_dias=None, sucursal='San José', ciclo=None,
                 inicio_ciclo=None, pronosticador=None, db=None, carpeta_salida=None, log=print,
                 progreso=None, cancelar=None):
        from preve_config import Config, ParametrosModelo
        conf = Config.actual()
        self.datos, self.sucursal = datos, sucursal
        self.params = params or ParametrosModelo()
        self.plantilla = plantilla
        self.ventana_dias = int(ventana_dias or conf.get('dias_entre_importaciones'))
        self.ciclo = ciclo or conf.ciclo_principal
        self.inicio_ciclo = inicio_ciclo
        self.pronosticador = pronosticador or pronosticador_pipeline
        self._db = db
        self.carpeta_salida = carpeta_salida or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                             CARPETA_REPORTES)
        self.log, self.progreso, self.cancelar = log, progreso or (lambda f, txt: None), cancelar or (lambda: False)
        self._silencio = lambda m: logger.debug(m)

    @property
    def db(self):
        if self._db is None:
            from database import Database
            self._db = Database.default()
        return self._db

    # ── ventanas ───────────────────────────────────────────
    def ventanas(self, fecha_inicio, fecha_fin):
        """[(inicio, fin_exclusivo)] de `ventana_dias`, alineadas al lunes (el panel es semanal)."""
        ini = pd.Timestamp(fecha_inicio).normalize()
        ini = ini + pd.Timedelta(days=(7 - ini.weekday()) % 7)
        fin = pd.Timestamp(fecha_fin).normalize()
        res = []
        t = ini
        while t + pd.Timedelta(days=self.ventana_dias - 1) <= fin:
            res.append((t, t + pd.Timedelta(days=self.ventana_dias)))
            t += pd.Timedelta(days=self.ventana_dias)
        return res

    def estado_ventana(self, t, fin):
        """'ok' | 'sin_datos_globales' | 'solo_compras' | 'sin_historia'."""
        vd = self.datos.ventas_diarias
        hay_venta = ((vd['fecha'] >= t) & (vd['fecha'] < fin) & (vd['mov'] < 0)).any()
        fc = self.datos.fechas_compra
        hay_compra = ((fc >= t) & (fc < fin)).any() if len(fc) else False
        if not hay_venta and not hay_compra:
            return 'sin_datos_globales'
        if not ((vd['fecha'] < t) & (vd['mov'] < 0)).any():
            return 'solo_compras' if hay_compra else 'sin_historia'
        if not hay_venta:
            return 'solo_compras'
        return 'ok'

    def _ventas_reales(self, desde, hasta):
        vd = self.datos.ventas_diarias
        v = vd[(vd['fecha'] >= desde) & (vd['fecha'] < hasta)]
        return (-v.groupby('sku')['mov'].sum()).rename('real')

    # ── evaluación de una ventana ──────────────────────────
    def evaluar_ventana(self, t, fin):
        ventas_hasta, stock_hasta = self.datos.hasta(t)
        pron, _, seg, _ = self.pronosticador(ventas_hasta, stock_hasta, self.datos.dim, self.ventana_dias,
                                             self.params, self._silencio)
        primera = self.datos.primera_venta
        elegibles = primera[primera < t].index                      # 6.2: sin primera venta → se omite
        real = self._ventas_reales(t, fin)
        df = pd.DataFrame({'sku': elegibles}).merge(pron, on='sku', how='inner')
        sin_pronostico = len(elegibles) - len(df)
        df['real'] = df['sku'].map(real).fillna(0.0)                # 0 ventas = demanda intermitente, se evalúa
        df['pronostico'] = df['demanda_p50_141d'].clip(lower=0)
        df['error_abs'] = (df['pronostico'] - df['real']).abs()
        df['error_pct'] = error_porcentual(df['pronostico'], df['real'])
        df['bias'] = df['pronostico'] - df['real']
        dim = self.datos.dim.set_index('sku')
        df['nombre'] = df['sku'].map(dim['nombre']) if 'nombre' in dim.columns else ''
        df['modelo'] = df['modelo_asignado']
        df['clasificacion'] = df['segmento']
        df['periodo_inicio'], df['periodo_fin'] = t, fin - pd.Timedelta(days=1)
        return df, {'productos_sin_pronostico': int(sin_pronostico),
                    'productos_sin_primera_venta': int(len(primera) - len(elegibles))}

    # ── simulación del día de reporte final ────────────────
    def dias_reporte(self, fecha_inicio, fecha_fin):
        """Días de reporte final del ciclo (a medianoche) dentro del rango."""
        dur, final = int(self.ciclo['duracion_dias']), int(self.ciclo.get('dia_reporte_final') or 0) or None
        final = final or dur - 1
        inicio = pd.Timestamp(self.inicio_ciclo or fecha_inicio).normalize()
        ini, fin = pd.Timestamp(fecha_inicio).normalize(), pd.Timestamp(fecha_fin).normalize()
        k = int(np.floor((ini - inicio).days / dur)) - 1
        dias = []
        while True:
            d = inicio + pd.Timedelta(days=k * dur + final - 1)
            if d > fin:
                break
            if d >= ini:
                dias.append(d)
            k += 1
        return dias

    def simular_compra(self, dia):
        from preve_pipeline import calcular_recomendacion
        hz = int(self.ciclo['horizonte_pronostico_dias'])
        ventas_hasta, stock_hasta = self.datos.hasta(dia)
        if stock_hasta.empty or ventas_hasta.empty:
            return None, {'dia': dia, 'estado': 'sin datos previos'}
        pron, panel, seg, fecha_fin = self.pronosticador(ventas_hasta, stock_hasta, self.datos.dim, hz,
                                                         self.params, self._silencio)
        p = dataclasses.replace(self.params, horizonte_dias=float(hz),
                                lead_time_dias=float(self.ciclo['lead_time_dias']))
        rec = calcular_recomendacion(pron.drop(columns=['modelo_asignado', 'segmento'], errors='ignore'), seg,
                                     self.datos.dim, stock_hasta, panel, fecha_fin, self._silencio,
                                     params=p, plantilla=self.plantilla)
        fin = dia + pd.Timedelta(days=hz)
        cubiertos = int(min(hz, (self.datos.ultima_fecha - dia).days + 1))
        rec['real_horizonte'] = rec['sku'].map(self._ventas_reales(dia, fin)).fillna(0.0)
        # Si todavía no pasaron los `hz` días, la venta real observada es parcial: compararla tal cual con una
        # compra pensada para todo el horizonte infla el sobrante. Se extrapola proporcionalmente y se marca.
        completo = cubiertos >= hz
        factor = 1.0 if completo else hz / max(1, cubiertos)
        rec['horizonte_completo'] = completo
        rec['real_horizonte_estimado'] = rec['real_horizonte'] * factor
        base = rec['real_horizonte_estimado']
        stock = rec['stock_actual'].clip(lower=0)
        rec['necesario_real'] = (base - stock).clip(lower=0)
        rec['faltante'] = (base - stock - rec['compra_sugerida_unid']).clip(lower=0)
        rec['sobrante'] = (stock + rec['compra_sugerida_unid'] - base).clip(lower=0)
        rec['dia_reporte'] = dia
        resumen = {'dia': dia, 'estado': 'ok' if completo else f'horizonte parcial ({cubiertos} de {hz} días): '
                                                             f'venta real extrapolada ×{factor:.2f}',
                   'dias_cubiertos': cubiertos, 'horizonte': hz,
                   'productos_con_compra': int((rec['decision'] == 'COMPRAR').sum()),
                   'unidades_sugeridas': float(rec['compra_sugerida_unid'].sum()),
                   'unidades_necesarias_reales': float(rec['necesario_real'].sum()),
                   'productos_con_quiebre': int((rec['faltante'] > 0).sum()),
                   'unidades_faltantes': float(rec['faltante'].sum()),
                   'unidades_sobrantes': float(rec['sobrante'].sum())}
        return rec, resumen

    # ── ejecución ──────────────────────────────────────────
    def run(self, fecha_inicio, fecha_fin, simular_compras=True):
        t0 = datetime.datetime.now()
        fecha_fin = min(pd.Timestamp(fecha_fin), self.datos.ultima_fecha)
        ventanas = self.ventanas(fecha_inicio, fecha_fin)
        dias = self.dias_reporte(fecha_inicio, fecha_fin) if simular_compras else []
        total = max(1, len(ventanas) + len(dias))
        if not ventanas:
            raise ValueError(f'El rango {pd.Timestamp(fecha_inicio).date()} – {fecha_fin.date()} no alcanza para '
                             f'una ventana de {self.ventana_dias} días.')
        self.log(f'Backtesting {self.sucursal}: {len(ventanas)} ventanas de {self.ventana_dias} días '
                 f'({ventanas[0][0].date()} – {(ventanas[-1][1] - pd.Timedelta(days=1)).date()})'
                 + (f' + {len(dias)} simulación(es) de compra' if dias else ''))
        periodos, omitidas, extras = [], [], []
        for i, (t, fin) in enumerate(ventanas):
            if self.cancelar():
                raise BacktestCancelado()
            etiqueta = f'{t.date()} – {(fin - pd.Timedelta(days=1)).date()}'
            self.progreso(i / total, f'Período {i + 1}/{len(ventanas)}: {etiqueta}')
            estado = self.estado_ventana(t, fin)
            if estado != 'ok':
                motivo = {'sin_datos_globales': 'sin ventas ni compras en toda la ventana',
                          'solo_compras': 'hay compras pero todavía no ventas (se registran en inventario)',
                          'sin_historia': 'todavía no hay historia de ventas para entrenar'}[estado]
                omitidas.append({'periodo': etiqueta, 'estado': estado, 'motivo': motivo})
                self.log(f'  · {etiqueta}: se omite ({motivo})')
                continue
            df, info = self.evaluar_ventana(t, fin)
            r = resumir(df)
            extras.append(dict(info, periodo=etiqueta))
            periodos.append((etiqueta, t, fin, df))
            self.log(f'  ✓ {etiqueta}: {r["muestras"]:,} productos · MAPE {r["mape"]:.1f}% · WMAPE {r["wmape"]:.1f}%')
        compras, resumen_compras = [], []
        for j, dia in enumerate(dias):
            if self.cancelar():
                raise BacktestCancelado()
            self.progreso((len(ventanas) + j) / total, f'Simulando la compra del día de reporte {dia.date()}')
            rec, res = self.simular_compra(dia)
            resumen_compras.append(res)
            if rec is not None:
                compras.append(rec)
                self.log(f'  ✓ Compra simulada {dia.date()}: {res["productos_con_compra"]:,} productos, '
                         f'{res["productos_con_quiebre"]:,} con quiebre real ({res["dias_cubiertos"]} de '
                         f'{res["horizonte"]} días con ventas reales)')
        if not periodos:
            raise ValueError('Ninguna ventana tuvo datos suficientes para evaluar el pronóstico.')
        self.progreso(0.97, 'Guardando resultados…')
        resultado = self._consolidar(periodos, omitidas, extras, compras, resumen_compras)
        resultado['ruta_excel'] = exportar_excel(resultado, self.carpeta_salida)
        resultado['ejecucion_id'] = self._guardar(resultado, fecha_inicio, fecha_fin)
        resultado['duracion_s'] = (datetime.datetime.now() - t0).total_seconds()
        self.progreso(1.0, 'Listo')
        self.log(f'Backtesting terminado: {resultado["total_productos"]:,} productos, {resultado["total_periodos"]} '
                 f'períodos, MAPE {resultado["mape_promedio"]:.1f}% → {resultado["ruta_excel"]}')
        return resultado

    def _consolidar(self, periodos, omitidas, extras, compras, resumen_compras):
        todo = pd.concat([df for *_, df in periodos], ignore_index=True)
        por_modelo = pd.DataFrame([dict(modelo=m, **resumir(g), productos=g['sku'].nunique())
                                   for m, g in todo.groupby('modelo')]).sort_values('modelo')
        evolucion = pd.DataFrame([dict(periodo=e, **resumir(df)) for e, _, _, df in periodos])
        por_sku = (todo.groupby(['sku', 'nombre', 'modelo'], dropna=False)
                   .agg(error_pct=('error_pct', 'mean'), real=('real', 'sum'), pronostico=('pronostico', 'sum'),
                        periodos=('periodo_inicio', 'nunique')).reset_index())
        evaluables = por_sku[por_sku['periodos'] >= 2]
        glob = resumir(todo)
        return {'sucursal': self.sucursal, 'ventana_dias': self.ventana_dias, 'global': glob,
                'mape_promedio': glob['mape'], 'total_productos': int(todo['sku'].nunique()),
                'total_periodos': len(periodos), 'periodos': periodos, 'detalle': todo, 'por_modelo': por_modelo,
                'evolucion': evolucion, 'peores': evaluables.nlargest(20, 'error_pct'),
                'mejores': evaluables.nsmallest(20, 'error_pct'), 'omitidas': pd.DataFrame(omitidas),
                'extras': pd.DataFrame(extras), 'compras': pd.concat(compras, ignore_index=True) if compras else None,
                'resumen_compras': pd.DataFrame(resumen_compras)}

    def _guardar(self, r, fecha_inicio, fecha_fin):
        """6.5: métricas, pronósticos con venta real y la ejecución quedan en la base."""
        db, det = self.db, r['detalle']
        db.guardar_productos(self.datos.dim)
        db.guardar_metricas(det.rename(columns={'error_pct': 'mape', 'error_abs': 'mae', 'bias': 'bias'})
                            .assign(rmse=det['error_abs'], muestras=1)
                            [['sku', 'modelo', 'periodo_inicio', 'periodo_fin', 'mape', 'mae', 'rmse', 'bias',
                              'muestras']])
        agregadas = []
        for e, t, fin, df in r['periodos']:
            for m, g in df.groupby('modelo'):
                agregadas.append(dict(modelo=m, periodo_inicio=t, periodo_fin=fin - pd.Timedelta(days=1),
                                      **{k: v for k, v in resumir(g).items() if k != 'wmape'}))
        db.guardar_metricas(pd.DataFrame(agregadas))
        for e, t, fin, df in r['periodos']:
            db.guardar_pronosticos(self.sucursal, df.rename(columns={'real': 'venta_real'}),
                                   datetime.datetime.combine(t.date(), datetime.time()), t,
                                   fin - pd.Timedelta(days=1))
        config = {'sucursal': self.sucursal, 'ventana_dias': self.ventana_dias, 'ciclo': self.ciclo['nombre'],
                  'plantilla': (self.plantilla or {}).get('nombre'), 'global': r['global'],
                  'omitidas': len(r['omitidas'])}
        return db.guardar_ejecucion_backtesting(fecha_inicio, fecha_fin, r['total_productos'], r['total_periodos'],
                                                r['mape_promedio'], r.get('ruta_excel'),
                                                json.loads(json.dumps(config, default=str)))


# ══════════════════════════════════════════════════════════════════
# Excel
# ══════════════════════════════════════════════════════════════════
def _nombre_hoja(i, t, fin):
    return f'Período {i} ({t:%d-%m} - {fin - pd.Timedelta(days=1):%d-%m})'[:31]


def _formatear(ws, anchos=None):
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='2D7D5F')
    for j, col in enumerate(ws.iter_cols(min_row=1, max_row=1), 1):
        ws.column_dimensions[get_column_letter(j)].width = (anchos or {}).get(j, max(12, len(str(col[0].value)) + 4))
    ws.freeze_panes = 'A2'


def exportar_excel(r, carpeta):
    """reportes_backtesting/backtesting_YYYY-MM-DD_HH-MM.xlsx con las hojas de 6.3."""
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f'backtesting_{datetime.datetime.now():%Y-%m-%d_%H-%M}.xlsx')
    g = r['global']
    general = pd.DataFrame([
        ('Sucursal', r['sucursal']), ('Ventana (días)', r['ventana_dias']),
        ('Productos evaluados', r['total_productos']), ('Períodos evaluados', r['total_periodos']),
        ('Períodos omitidos (datos insuficientes)', len(r['omitidas'])),
        ('MAPE promedio (%) — sMAPE cuando la venta real es 0', round(g['mape'], 2)),
        ('WMAPE (%)', round(g['wmape'], 2) if g['wmape'] == g['wmape'] else None),
        ('MAE (u)', round(g['mae'], 3)), ('RMSE (u)', round(g['rmse'], 3)), ('Bias medio (u)', round(g['bias'], 3)),
    ], columns=['Métrica', 'Valor'])
    tabla_sku = {'sku': 'Código', 'nombre': 'Nombre', 'modelo': 'Modelo', 'periodos': 'Períodos',
                 'pronostico': 'Pronóstico total', 'real': 'Real total', 'error_pct': 'Error % medio'}
    with pd.ExcelWriter(ruta, engine='openpyxl') as xw:
        general.to_excel(xw, sheet_name='Resumen General', index=False)
        titulos, fila = [], len(general) + 2           # fila 0-based donde va el próximo título
        for titulo, df in (('Evolución por período', r['evolucion']),
                           ('Comparación por modelo', r['por_modelo']),
                           ('Top 20 peor pronóstico (productos con 2+ períodos)', r['peores'].rename(columns=tabla_sku)),
                           ('Top 20 mejor pronóstico', r['mejores'].rename(columns=tabla_sku))):
            titulos.append((fila + 1, titulo))
            df.round(2).to_excel(xw, sheet_name='Resumen General', index=False, startrow=fila + 1)
            fila += len(df) + 4
        r['por_modelo'].round(2).to_excel(xw, sheet_name='Resumen por Modelo', index=False)
        for i, (_, t, fin, df) in enumerate(r['periodos'], 1):
            df[list(COLUMNAS_PERIODO)].rename(columns=COLUMNAS_PERIODO).round(2).to_excel(
                xw, sheet_name=_nombre_hoja(i, t, fin), index=False)
        if r['compras'] is not None:
            cols = {'dia_reporte': 'Día de reporte', 'sku': 'Código', 'nombre': 'Nombre', 'accion': 'Acción',
                    'stock_actual': 'Stock ese día', 'compra_sugerida_unid': 'Compra sugerida',
                    'real_horizonte': 'Venta real observada', 'horizonte_completo': '¿Horizonte completo?',
                    'real_horizonte_estimado': 'Venta real (horizonte, estimada si es parcial)',
                    'necesario_real': 'Compra necesaria real',
                    'faltante': 'Faltante', 'sobrante': 'Sobrante'}
            r['compras'][list(cols)].rename(columns=cols).round(1).to_excel(xw, sheet_name='Simulación Compras',
                                                                           index=False)
            r['resumen_compras'].to_excel(xw, sheet_name='Simulación Compras', index=False,
                                          startcol=len(cols) + 1)
        if len(r['omitidas']):
            r['omitidas'].to_excel(xw, sheet_name='Ventanas omitidas', index=False)
        wb = xw.book
        from openpyxl.styles import Font
        hoja_gen = wb['Resumen General']
        for fila_titulo, texto in titulos:            # 1-based: la fila libre justo encima de cada tabla
            hoja_gen.cell(row=fila_titulo, column=1, value=texto).font = Font(bold=True, size=12, color='2D7D5F')
        for ws in wb.worksheets:
            _formatear(ws, {1: 44} if ws.title == 'Resumen General' else None)
    return ruta
