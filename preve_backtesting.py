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

from preve_utils import get_logger, log_info

logger = get_logger('backtesting')

CARPETA_REPORTES = 'reportes_backtesting'
COLUMNAS_PERIODO = {'sku': 'Código', 'nombre': 'Nombre', 'modelo': 'Modelo', 'clasificacion': 'Clasif',
                    'pronostico': 'Pronóstico', 'real': 'Real (demanda evaluada)', 'real_total': 'Real total',
                    'atipico': 'Pedido atípico', 'con_stock': 'Con stock al inicio', 'error_abs': 'Error Abs',
                    'error_pct': 'Error %', 'bias': 'Bias'}


SIN_DATO = 'Sin dato'


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
        """Fecha de la primera venta de cada SKU."""
        v = self.ventas_diarias[self.ventas_diarias['mov'] < 0]
        return v.groupby('sku')['fecha'].min()

    @property
    def ultima_fecha(self):
        """Última fecha con ventas registradas."""
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
            almacenes_propios=params.almacenes_propios,
            pedido_atipico_min_unidades=params.pedido_atipico_min_unidades,
            pedido_atipico_factor=params.pedido_atipico_factor)
        vd.attrs.pop('pedidos_atipicos', None)        # el detalle por línea no se usa aquí y ralentiza pandas
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
    pron, _ = entrenar_y_pronosticar(panel, seg, dim, fecha_fin, log, params=p, stock=stock_hasta)
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


NOTA_MAPE = 'poco confiable con demanda intermitente (muchas ventas reales en 0)'
ETIQ_WMAPE = 'WMAPE (demanda al detalle)'
ETIQ_WMAPE_TOTAL = 'WMAPE (venta total)'
ETIQ_WMAPE_STOCK = 'WMAPE (productos con stock)'


def wmape(df, col_real='real'):
    """Error absoluto ponderado por volumen (%) del pronóstico contra la columna `col_real`."""
    if df.empty or col_real not in df.columns:
        return np.nan
    real_tot = df[col_real].sum()
    return float((df['pronostico'] - df[col_real]).abs().sum() / real_tot * 100) if real_tot > 0 else np.nan


def resumir(df):
    """WMAPE, MAE, RMSE, Bias, muestras y MAPE de un DataFrame con pronostico/real/error_pct.

    El WMAPE es el indicador principal; el MAPE queda al final (ver NOTA_MAPE).
    """
    if df.empty:
        return {'wmape': np.nan, 'mae': np.nan, 'rmse': np.nan, 'bias': np.nan, 'muestras': 0, 'mape': np.nan}
    err = df['pronostico'] - df['real']
    return {'wmape': wmape(df, 'real'),
            'mae': float(err.abs().mean()), 'rmse': float(np.sqrt((err ** 2).mean())),
            'bias': float(err.mean()), 'muestras': int(len(df)), 'mape': float(df['error_pct'].mean())}


def resumir_completo(df):
    """`resumir` (WMAPE de la demanda al detalle) + WMAPE de la venta total y de los productos con stock."""
    r = resumir(df)
    tiene = 'con_stock' in df.columns
    out = {'wmape': r['wmape'],
           'wmape_total': wmape(df, 'real_total'),
           'wmape_con_stock': wmape(df[df['con_stock']], 'real') if tiene else np.nan}
    out.update({k: v for k, v in r.items() if k != 'wmape'})
    out['muestras_con_stock'] = int(df['con_stock'].sum()) if tiene else 0
    return out


def desagregar(df, por):
    """Una fila por grupo (columnas `por`, p. ej. ['clase_abc'] o ['clasificacion', 'modelo']) con
    resumir_completo (WMAPE, MAE, Bias, …) y la cantidad de productos. Vacío si falta alguna columna."""
    if df.empty or any(c not in df.columns for c in por):
        return pd.DataFrame(columns=list(por))
    filas = []
    for clave, g in df.groupby(por, dropna=False):
        clave = clave if isinstance(clave, tuple) else (clave,)
        filas.append(dict(zip(por, clave), **resumir_completo(g), productos=int(g['sku'].nunique())))
    return pd.DataFrame(filas).sort_values(list(por)).reset_index(drop=True)


def _pc(x):
    return f'{x:.1f}%' if x is not None and x == x else '—'


def _r(x, n):
    return round(x, n) if x is not None and x == x else None


# ══════════════════════════════════════════════════════════════════
# Backtester
# ══════════════════════════════════════════════════════════════════
class Backtester:
    def __init__(self, datos, params=None, plantilla=None, ventana_dias=None, sucursal='San José', ciclo=None,
                 inicio_ciclo=None, pronosticador=None, db=None, carpeta_salida=None, log=log_info,
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
        """Base de datos (la por defecto si no se pasó una)."""
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

    def _excluye_atipicos(self):
        return bool(getattr(self.params, 'excluir_atipicos_de_la_demanda', False)) \
            and 'mov_atipico' in self.datos.ventas_diarias.columns

    def _ventas_reales(self, desde, hasta):
        """Venta real por SKU. Con `excluir_atipicos_de_la_demanda` activo es la demanda al detalle (sin pedidos
        atípicos); `_ventas_reales_detalle` da además el total y los atípicos por separado."""
        return self._ventas_reales_detalle(desde, hasta)['real'].rename('real')

    def _ventas_reales_detalle(self, desde, hasta):
        """DataFrame por SKU: real_total (todo lo vendido), atipico (pedidos atípicos) y real (la que se evalúa)."""
        vd = self.datos.ventas_diarias
        v = vd[(vd['fecha'] >= desde) & (vd['fecha'] < hasta)]
        total = -v.groupby('sku')['mov'].sum()
        atipico = (-v.groupby('sku')['mov_atipico'].sum()) if 'mov_atipico' in v.columns else total * 0.0
        out = pd.DataFrame({'real_total': total, 'atipico': atipico})
        out['real'] = out['real_total'] - out['atipico'] if self._excluye_atipicos() else out['real_total']
        return out

    # ── evaluación de una ventana ──────────────────────────
    def evaluar_ventana(self, t, fin):
        """Entrena con datos anteriores a `t`, pronostica [t, fin) y compara con lo vendido."""
        ventas_hasta, stock_hasta = self.datos.hasta(t)
        pron, _, seg, _ = self.pronosticador(ventas_hasta, stock_hasta, self.datos.dim, self.ventana_dias,
                                             self.params, self._silencio)
        primera = self.datos.primera_venta
        elegibles = primera[primera < t].index                      # 6.2: sin primera venta → se omite
        real = self._ventas_reales_detalle(t, fin)
        df = pd.DataFrame({'sku': elegibles}).merge(pron, on='sku', how='inner')
        sin_pronostico = len(elegibles) - len(df)
        for c in ('real', 'real_total', 'atipico'):
            df[c] = df['sku'].map(real[c]).fillna(0.0)              # 0 ventas = demanda intermitente, se evalúa
        # stock > 0 al inicio de la ventana (cierre del día anterior); stock negativo del ERP cuenta como sin stock
        stock_ini = stock_hasta.iloc[-1] if len(stock_hasta) else pd.Series(dtype=float)
        df['con_stock'] = (df['sku'].map(stock_ini).fillna(0.0) > 0)
        df['pronostico'] = df['demanda_p50_141d'].clip(lower=0)
        df['error_abs'] = (df['pronostico'] - df['real']).abs()
        df['error_pct'] = error_porcentual(df['pronostico'], df['real'])
        df['bias'] = df['pronostico'] - df['real']
        dim = self.datos.dim.set_index('sku')
        df['nombre'] = df['sku'].map(dim['nombre']) if 'nombre' in dim.columns else ''
        df['modelo'] = df['modelo_asignado']
        df['clasificacion'] = df['segmento']
        # clase ABC de la segmentación de esta ventana (con los datos visibles en t), para el desglose por ABC
        abc = seg.set_index('sku')['clase_abc'] if seg is not None and 'clase_abc' in seg.columns else pd.Series(
            dtype=object)
        df['clase_abc'] = df['sku'].map(abc).fillna(SIN_DATO)
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
        """Sugerencia de compra a medianoche de `dia`, validada contra las ventas reales del horizonte."""
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
    def run(self, fecha_inicio, fecha_fin, simular_compras=True, persistir=True):
        """Walk-forward en el rango + simulación de compras; guarda en la base y exporta el Excel.

        persistir=False: solo calcula (no escribe en la base ni crea el Excel; ruta_excel y ejecucion_id quedan
        en None). Lo usa el modo comparación, que evalúa muchos modelos y exporta un único Excel propio.
        """
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
            r = resumir_completo(df)
            extras.append(dict(info, periodo=etiqueta))
            periodos.append((etiqueta, t, fin, df))
            self.log(f'  ✓ {etiqueta}: {r["muestras"]:,} productos · {ETIQ_WMAPE} {_pc(r["wmape"])} · '
                     f'{ETIQ_WMAPE_TOTAL} {_pc(r["wmape_total"])} · {ETIQ_WMAPE_STOCK} {_pc(r["wmape_con_stock"])} · '
                     f'MAPE {_pc(r["mape"])} ({NOTA_MAPE})')
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
        if not any(len(df) for *_, df in periodos):
            raise ValueError('El modelo no generó pronóstico para ningún producto en ninguna ventana '
                             '(p. ej. CatBoost necesita más de 30 productos con historia para entrenar).')
        self.progreso(0.97, 'Guardando resultados…' if persistir else 'Consolidando resultados…')
        resultado = self._consolidar(periodos, omitidas, extras, compras, resumen_compras)
        resultado['ruta_excel'] = exportar_excel(resultado, self.carpeta_salida) if persistir else None
        resultado['ejecucion_id'] = self._guardar(resultado, fecha_inicio, fecha_fin) if persistir else None
        resultado['duracion_s'] = (datetime.datetime.now() - t0).total_seconds()
        self.progreso(1.0, 'Listo')
        g = resultado['global']
        self.log(f'Backtesting terminado: {resultado["total_productos"]:,} productos, {resultado["total_periodos"]} '
                 f'períodos · {ETIQ_WMAPE} {_pc(g["wmape"])} · {ETIQ_WMAPE_TOTAL} {_pc(g["wmape_total"])} · '
                 f'{ETIQ_WMAPE_STOCK} {_pc(g["wmape_con_stock"])} · MAPE {_pc(g["mape"])} ({NOTA_MAPE})'
                 + (f' → {resultado["ruta_excel"]}' if persistir else ''))
        return resultado

    def _consolidar(self, periodos, omitidas, extras, compras, resumen_compras):
        todo = pd.concat([df for *_, df in periodos], ignore_index=True)
        por_modelo = pd.DataFrame([dict(modelo=m, **resumir_completo(g), productos=g['sku'].nunique())
                                   for m, g in todo.groupby('modelo')]).sort_values('modelo')
        evolucion = pd.DataFrame([dict(periodo=e, **resumir_completo(df), real_total=df['real_total'].sum(),
                                       atipico=df['atipico'].sum()) for e, _, _, df in periodos])
        por_sku = (todo.groupby(['sku', 'nombre', 'modelo'], dropna=False)
                   .agg(error_pct=('error_pct', 'mean'), real=('real', 'sum'), pronostico=('pronostico', 'sum'),
                        periodos=('periodo_inicio', 'nunique')).reset_index())
        evaluables = por_sku[por_sku['periodos'] >= 2]
        glob = resumir_completo(todo)
        glob['real_total'], glob['atipico'] = float(todo['real_total'].sum()), float(todo['atipico'].sum())

        # Métricas desagregadas por segmento ADI/CV², clase ABC y modelo (WMAPE/MAE/Bias reales de cada grupo)
        return {'sucursal': self.sucursal, 'ventana_dias': self.ventana_dias, 'global': glob,
                'por_segmento': desagregar(todo, ['clasificacion']).rename(columns={'clasificacion': 'segmento'}),
                'por_clase_abc': desagregar(todo, ['clase_abc']),
                'por_segmento_modelo': desagregar(todo, ['clasificacion', 'modelo']).rename(
                    columns={'clasificacion': 'segmento'}),
                'mape_promedio': glob['mape'], 'excluye_atipicos': self._excluye_atipicos(), 'total_productos': int(todo['sku'].nunique()),
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


def _modo_avanzado():
    from preve_config import Config
    return bool(Config.actual().get('mostrar_metricas_avanzadas'))


def _traducir_tabla(df, avanzado):
    """COPIA de df con los nombres de modelo / segmento traducidos (salvo en modo avanzado)."""
    from preve_traducciones import traducir_modelo, traducir_segmento
    out = df.copy()
    if avanzado:
        return out
    for col in ('modelo', 'modelo_asignado'):
        if col in out.columns:
            out[col] = [traducir_modelo(x) for x in out[col]]
    for col in ('clasificacion', 'segmento'):
        if col in out.columns:
            out[col] = [traducir_segmento(x) for x in out[col]]
    return out


def exportar_excel(r, carpeta, avanzado=None):
    """reportes_backtesting/backtesting_YYYY-MM-DD_HH-MM.xlsx con las hojas de 6.3.

    Sin `mostrar_metricas_avanzadas` (avanzado=None lee la configuración) los nombres de modelo y de segmento se
    muestran en lenguaje de negocio y el WMAPE como "Margen de error estimado" con su interpretación.
    """
    from preve_traducciones import ETIQUETA_ERROR, interpretar_wmape
    avanzado = _modo_avanzado() if avanzado is None else avanzado
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f'backtesting_{datetime.datetime.now():%Y-%m-%d_%H-%M}.xlsx')
    g = r['global']

    def interp(x):
        return interpretar_wmape(x)[2]
    general = pd.DataFrame([
        (ETIQUETA_ERROR + ' (%) — en palabras simples', _r(g['wmape'], 2), interp(g['wmape'])),
        ('Sucursal', r['sucursal'], ''), ('Ventana (días)', r['ventana_dias'], ''),
        ('Productos evaluados', r['total_productos'], ''), ('Períodos evaluados', r['total_periodos'], ''),
        ('Períodos omitidos (datos insuficientes)', len(r['omitidas']), ''),
        (ETIQ_WMAPE + ' (%) — indicador principal', _r(g['wmape'], 2), interp(g['wmape'])),
        (ETIQ_WMAPE_TOTAL + ' (%) — contra todo lo vendido, con pedidos atípicos', _r(g['wmape_total'], 2),
         interp(g['wmape_total'])),
        (ETIQ_WMAPE_STOCK + ' (%) — solo productos con stock al inicio de la ventana', _r(g['wmape_con_stock'], 2),
         interp(g['wmape_con_stock'])),
        ('Unidades vendidas (total)', _r(g.get('real_total'), 0), ''),
        ('Unidades en pedidos atípicos', _r(g.get('atipico'), 0), ''),
        ('MAE (u)', round(g['mae'], 3), 'Error promedio por producto y período, en unidades.'),
        ('RMSE (u)', round(g['rmse'], 3), ''),
        ('Bias medio (u)', round(g['bias'], 3), 'Positivo: el sistema pronostica de más; negativo: de menos.'),
        ('MAPE promedio (%) — ' + NOTA_MAPE, _r(g['mape'], 2), ''),
    ], columns=['Métrica', 'Valor', 'Interpretación'])
    if avanzado:
        renombre_metricas = {'wmape': ETIQ_WMAPE, 'wmape_total': ETIQ_WMAPE_TOTAL,
                             'wmape_con_stock': ETIQ_WMAPE_STOCK, 'mape': 'MAPE (' + NOTA_MAPE + ')'}
    else:
        renombre_metricas = {'wmape': ETIQUETA_ERROR + ' (demanda al detalle, %)',
                             'wmape_total': ETIQUETA_ERROR + ' (venta total, %)',
                             'wmape_con_stock': ETIQUETA_ERROR + ' (productos con stock, %)',
                             'mae': 'Error promedio (u)', 'rmse': 'Error típico (u)',
                             'bias': 'Sesgo (+ de más, − de menos)',
                             'mape': 'Error porcentual promedio (' + NOTA_MAPE + ')'}
    tabla_sku = {'sku': 'Código', 'nombre': 'Nombre', 'modelo': 'Modelo', 'periodos': 'Períodos',
                 'pronostico': 'Pronóstico total', 'real': 'Real total', 'error_pct': 'Error % medio'}
    with pd.ExcelWriter(ruta, engine='openpyxl') as xw:
        general.to_excel(xw, sheet_name='Resumen General', index=False)
        titulos, fila = [], len(general) + 2           # fila 0-based donde va el próximo título
        for titulo, df in (('Evolución por período', r['evolucion'].rename(columns=renombre_metricas)),
                           ('Comparación por modelo',
                            _traducir_tabla(r['por_modelo'], avanzado).rename(columns=renombre_metricas)),
                           ('Top 20 peor pronóstico (productos con 2+ períodos)',
                            _traducir_tabla(r['peores'], avanzado).rename(columns=tabla_sku)),
                           ('Top 20 mejor pronóstico', _traducir_tabla(r['mejores'], avanzado).rename(columns=tabla_sku))):
            titulos.append((fila + 1, titulo))
            df.round(2).to_excel(xw, sheet_name='Resumen General', index=False, startrow=fila + 1)
            fila += len(df) + 4
        _traducir_tabla(r['por_modelo'], avanzado).rename(columns=renombre_metricas).round(2).to_excel(
            xw, sheet_name='Resumen por Modelo', index=False)
        for i, (_, t, fin, df) in enumerate(r['periodos'], 1):
            _traducir_tabla(df[list(COLUMNAS_PERIODO)], avanzado).rename(columns=COLUMNAS_PERIODO).round(2).to_excel(
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
        # Hoja de métricas desagregadas por segmento ADI/CV², clase ABC y segmento × modelo
        _escribir_desagregadas(xw, r, renombre_metricas, avanzado)
        wb = xw.book
        from openpyxl.styles import Font
        hoja_gen = wb['Resumen General']
        for fila_titulo, texto in titulos:            # 1-based: la fila libre justo encima de cada tabla
            hoja_gen.cell(row=fila_titulo, column=1, value=texto).font = Font(bold=True, size=12, color='2D7D5F')
        for ws in wb.worksheets:
            _formatear(ws, {1: 44, 3: 70} if ws.title == 'Resumen General' else None)
    return ruta


HOJA_DESAGREGADA = 'Métricas por Segmento'


def _escribir_desagregadas(xw, r, renombre_metricas, avanzado, hoja=HOJA_DESAGREGADA):
    """Tablas por segmento ADI/CV², por clase ABC y por segmento × modelo en una sola hoja, con título."""
    from openpyxl.styles import Font
    fila, titulos = 0, []
    for titulo, clave in (('Por segmento ADI/CV²' if avanzado else 'Por tipo de venta', 'por_segmento'),
                          ('Por clase ABC', 'por_clase_abc'),
                          ('Por segmento × modelo' if avanzado else 'Por tipo de venta y método',
                           'por_segmento_modelo')):
        df_d = r.get(clave)
        if df_d is not None and len(df_d):
            titulos.append((fila + 1, titulo))
            _traducir_tabla(df_d, avanzado).rename(columns=renombre_metricas).round(2).to_excel(
                xw, sheet_name=hoja, index=False, startrow=fila + 1)
            fila += len(df_d) + 4
    if hoja in xw.book.sheetnames:
        for ft, txt in titulos:
            xw.book[hoja].cell(row=ft, column=1, value=txt).font = Font(bold=True, size=12, color='2D7D5F')


# ══════════════════════════════════════════════════════════════════
# Comparación multi-modelo y selección automática por SKU (modo comparación)
# ══════════════════════════════════════════════════════════════════
CRITERIO_WMAPE = 'Menor WMAPE (desempate: menor MAE)'
CRITERIO_MAE = 'Menor MAE (sin ventas en los períodos evaluados: el WMAPE no existe)'
NOTAS_COMPARACION = [
    ('WMAPE por SKU y modelo', 'Σ|pronóstico − real| / Σ real × 100, sumando todos los períodos evaluados.'),
    ('MAE', 'Promedio de |pronóstico − real| por período (unidades).'),
    ('Bias', 'Promedio de (pronóstico − real) por período: + sobreestima, − subestima.'),
    ('Selección', 'Por SKU gana el menor WMAPE; si empatan, el menor MAE; si siguen empatados, el primero en el '
                  'orden de modelos de la comparación.'),
    ('Σ real = 0', 'Si el SKU no vendió nada en los períodos evaluados el WMAPE no existe: se elige por MAE (el '
                   'que más se acercó a 0) y la confianza queda "Sin dato".'),
    ('Base común por SKU', 'Solo compiten los modelos que pronosticaron el SKU en todos sus períodos evaluados (un '
                           'modelo que no pudo pronosticar alguna ventana no compite con menos datos).'),
    ('WMAPE base común', 'WMAPE global de cada modelo sobre los pares (SKU, período) que TODOS los modelos '
                         'pronosticaron: es la comparación global justa.'),
    ('Confianza', 'Alta: WMAPE ≤ 30 %; Media: ≤ 60 %; Baja: > 60 %; Sin dato: Σ real = 0 (preve_traducciones).'),
]


def metricas_por_sku_modelo(detalle):
    """WMAPE, MAE y Bias reales por (sku, modelo). `detalle`: una fila por SKU × período × modelo con
    pronostico y real. WMAPE = Σ|f − r| / Σr × 100; NaN si Σr = 0 (ver NOTAS_COMPARACION)."""
    err = detalle['pronostico'] - detalle['real']
    d = detalle.assign(_abs=err.abs(), _err=err)
    m = (d.groupby(['sku', 'modelo'], sort=True)
         .agg(suma_abs=('_abs', 'sum'), suma_real=('real', 'sum'), suma_pronostico=('pronostico', 'sum'),
              mae=('_abs', 'mean'), bias=('_err', 'mean'), periodos=('real', 'size'))
         .reset_index())
    m['wmape'] = m['suma_abs'] / m['suma_real'].where(m['suma_real'] > 0) * 100
    return m


def seleccionar_mejor_modelo(metricas, orden=None):
    """Un modelo por SKU según NOTAS_COMPARACION. Devuelve sku, modelo, wmape, mae, bias, periodos, venta_real,
    criterio, confianza y precision (texto de negocio). `orden`: lista de modelos para el último desempate."""
    from preve_traducciones import nivel_confianza, texto_precision
    import preve_modelos_baseline as mb
    columnas = ['sku', 'modelo', 'wmape', 'mae', 'bias', 'periodos', 'venta_real', 'criterio', 'confianza',
                'precision']
    if metricas.empty:
        return pd.DataFrame(columns=columnas)
    m = metricas.copy()
    m = m[m['periodos'] == m.groupby('sku')['periodos'].transform('max')]
    pos = {n: i for i, n in enumerate(orden or mb.expandir_modelos(mb.MODELOS_COMPARABLES))}
    m['_orden'] = [pos.get(n, len(pos)) for n in m['modelo']]
    m = m.sort_values(['sku', 'wmape', 'mae', '_orden', 'modelo'], na_position='last')
    mejor = m.groupby('sku', sort=True).head(1).reset_index(drop=True)
    mejor['criterio'] = np.where(mejor['wmape'].notna(), CRITERIO_WMAPE, CRITERIO_MAE)
    mejor['confianza'] = [nivel_confianza(w) for w in mejor['wmape']]
    mejor['precision'] = [texto_precision(w) for w in mejor['wmape']]
    return mejor.rename(columns={'suma_real': 'venta_real'})[columnas]


def _wmape_base_comun(detalle, modelos):
    """{modelo: WMAPE} sobre los (sku, período) pronosticados por todos los modelos."""
    claves = detalle.groupby(['sku', 'periodo_inicio'])['modelo'].nunique()
    comunes = set(claves[claves == len(modelos)].index)
    if not comunes:
        return {m: np.nan for m in modelos}
    en_comun = [k in comunes for k in zip(detalle['sku'], detalle['periodo_inicio'])]
    return {m: wmape(g) for m, g in detalle[en_comun].groupby('modelo')}


def _importancias_comparacion(datos, fecha_fin, params, horizonte_dias, nombres, log):
    """Importancia de variables del GBM (con y/o sin disponibilidad) entrenado con todos los datos hasta
    `fecha_fin`. DataFrame modelo, variable, descripcion, importancia, segundos; vacío si no aplica."""
    import time
    import preve_modelos_baseline as mb
    from preve_modelo_gbm import pronosticar_gbm
    from preve_pipeline import construir_panel_y_segmentacion
    from preve_traducciones import traducir_variable
    variantes = [(n, n == mb.MODELO_GBM) for n in (mb.MODELO_GBM, mb.MODELO_GBM_SIN_DISP) if n in nombres]
    if not variantes:
        return pd.DataFrame()
    ventas, stock = datos.hasta(pd.Timestamp(fecha_fin) + pd.Timedelta(days=1))
    p = dataclasses.replace(params, horizonte_dias=float(horizonte_dias))
    panel, _, ff = construir_panel_y_segmentacion(stock, ventas, datos.dim, lambda m: None, params=p)
    tablas = []
    for nombre, usar in variantes:
        t0 = time.perf_counter()
        _, ex = pronosticar_gbm(panel, stock, ff, p.horizonte_semanas, lambda m: None, usar_disponibilidad=usar,
                                calcular_importancias=True, devolver_extras=True)
        seg = time.perf_counter() - t0
        if ex['importancias'] is None:
            log(f'  Importancia de variables ({nombre}): historia insuficiente para entrenar el modelo.')
            continue
        imp = ex['importancias'].assign(modelo=nombre, segundos=round(seg, 2))
        imp['descripcion'] = [traducir_variable(v) for v in imp['variable']]
        tablas.append(imp[['modelo', 'variable', 'descripcion', 'importancia', 'segundos']])
        log(f'  Importancia de variables ({nombre}): calculada en {seg:.1f} s.')
    return pd.concat(tablas, ignore_index=True) if tablas else pd.DataFrame()


def comparar_modelos(datos, fecha_inicio, fecha_fin, params=None, modelos=None, plantilla=None, ventana_dias=None,
                     sucursal='San José', ciclo=None, log=log_info, progreso=None, cancelar=None, db=None,
                     carpeta_salida=None, pronosticadores=None, guardar_asignacion=True,
                     calcular_importancias=True, etiqueta_modelo=None, log_detalle=None):
    """Backtesting walk-forward de cada modelo y selección del mejor por SKU.

    modelos: nombres configurables (preve_modelos_baseline.MODELOS_COMPARABLES; None = todos). Los desconocidos se
        omiten con aviso. El 'Promedio móvil' se expande con params.ventanas_promedio_movil.
    pronosticadores: {nombre: función} para inyectar pronosticadores (tests); reemplaza a `modelos`.
    etiqueta_modelo: función nombre -> texto para los mensajes de log y progreso (p. ej. traducir_modelo para el
        usuario no técnico). log_detalle: log del backtesting de cada modelo (por defecto, `log`).
    No escribe el Excel ni las métricas de cada modelo (Backtester.run(persistir=False)): solo crea el Excel de
    comparación y, si guardar_asignacion, guarda el modelo elegido por SKU en la base (tabla asignacion_modelos).

    Devuelve dict: modelos, resumen (por modelo), metricas_sku, mejor_por_sku, comparacion (pivote WMAPE),
    detalle, por_segmento_modelo, por_clase_abc_modelo, importancias, fallidos, ruta_excel,
    asignaciones_guardadas, duracion_s.
    """
    import preve_modelos_baseline as mb
    from preve_config import ParametrosModelo
    t0 = datetime.datetime.now()
    params = params or ParametrosModelo()
    cancelar = cancelar or (lambda: False)
    progreso = progreso or (lambda f, t: None)
    et = etiqueta_modelo or (lambda nombre: nombre)
    log_detalle = log_detalle or log
    if pronosticadores is None:
        pedidos = list(mb.MODELOS_COMPARABLES) if modelos is None else list(modelos)
        validos, desconocidos = mb.validar_modelos(pedidos)
        if desconocidos:
            log(f'⚠ Modelos desconocidos (se omiten): {", ".join(map(str, desconocidos))}. '
                f'Válidos: {", ".join(mb.MODELOS_COMPARABLES)}')
        if not validos:
            raise ValueError('Ninguno de los modelos pedidos es válido.')
        pronosticadores = {n: mb.pronosticador(n)
                           for n in mb.expandir_modelos(validos, params.ventanas_promedio_movil)}
    nombres = list(pronosticadores)
    n = len(nombres)
    log(f'Modo comparación: {n} métodos — {", ".join(et(x) for x in nombres)}')

    detalles, globales, fallidos, horizonte = {}, {}, {}, None
    for i, (nombre, fn) in enumerate(pronosticadores.items()):
        if cancelar():
            raise BacktestCancelado()
        log(f'═══ Método {i + 1}/{n}: {et(nombre)} ═══')

        def prog(f, txt, i=i, nombre=nombre):
            progreso(min(0.95, (i + min(max(f, 0.0), 1.0)) / n * 0.95), f'Método {i + 1}/{n} ({et(nombre)}): {txt}')
        try:
            bt = Backtester(datos, params=params, plantilla=plantilla, ventana_dias=ventana_dias, sucursal=sucursal,
                            ciclo=ciclo, pronosticador=fn, db=db, carpeta_salida=carpeta_salida, log=log_detalle,
                            progreso=prog, cancelar=cancelar)
            horizonte = bt.ventana_dias
            res = bt.run(fecha_inicio, fecha_fin, simular_compras=False, persistir=False)
        except BacktestCancelado:
            raise
        except Exception as e:  # noqa: BLE001 — un modelo que falla no detiene la comparación; queda registrado
            logger.exception('Falló el modelo %s en la comparación', nombre)
            fallidos[nombre] = f'{type(e).__name__}: {e}'
            log(f'  ⚠ {et(nombre)}: no se pudo evaluar ({fallidos[nombre]})')
            continue
        det = res['detalle'].copy()
        det['modelo'] = nombre                       # el nombre del modelo comparado, en todas partes
        detalles[nombre] = det
        globales[nombre] = dict(res['global'], productos=res['total_productos'], periodos=res['total_periodos'])
    if not detalles:
        raise ValueError('Ningún modelo pudo completar el backtesting: '
                         + '; '.join(f'{k}: {v}' for k, v in fallidos.items()))

    progreso(0.96, 'Eligiendo el mejor modelo para cada producto…')
    detalle = pd.concat(detalles.values(), ignore_index=True)
    metricas = metricas_por_sku_modelo(detalle)
    mejor = seleccionar_mejor_modelo(metricas, orden=nombres)
    nombres_prod = datos.dim.set_index('sku')['nombre'] if 'nombre' in datos.dim.columns else pd.Series(dtype=object)
    mejor.insert(1, 'nombre', mejor['sku'].map(nombres_prod))
    comunes = _wmape_base_comun(detalle, list(detalles))
    conteo = mejor['modelo'].value_counts()
    resumen = pd.DataFrame([{
        'modelo': m, 'wmape': g['wmape'], 'wmape_base_comun': comunes.get(m, np.nan), 'wmape_total': g['wmape_total'],
        'wmape_con_stock': g['wmape_con_stock'], 'mae': g['mae'], 'bias': g['bias'], 'productos': g['productos'],
        'periodos': g['periodos'], 'sku_donde_es_el_mejor': int(conteo.get(m, 0))} for m, g in globales.items()])
    importancias = pd.DataFrame()
    if calcular_importancias:
        try:
            importancias = _importancias_comparacion(datos, min(pd.Timestamp(fecha_fin), datos.ultima_fecha),
                                                     params, horizonte, nombres, log_detalle)
        except Exception as e:  # noqa: BLE001 — es un anexo técnico, no debe impedir la comparación
            logger.exception('No se pudo calcular la importancia de variables')
            log(f'  ⚠ Importancia de variables: no se pudo calcular ({e})')
    resultado = {
        'sucursal': sucursal, 'modelos': list(detalles), 'resumen': resumen, 'metricas_sku': metricas,
        'mejor_por_sku': mejor, 'detalle': detalle,
        'comparacion': metricas.pivot_table(index='sku', columns='modelo', values='wmape').round(1),
        'por_segmento_modelo': desagregar(detalle, ['clasificacion', 'modelo']).rename(
            columns={'clasificacion': 'segmento'}),
        'por_clase_abc_modelo': desagregar(detalle, ['clase_abc', 'modelo']),
        'importancias': importancias, 'fallidos': fallidos,
    }
    carpeta = carpeta_salida or os.path.join(os.path.dirname(os.path.abspath(__file__)), CARPETA_REPORTES)
    resultado['carpeta'] = carpeta
    resultado['ruta_excel'] = exportar_comparacion(resultado, carpeta)
    resultado['asignaciones_guardadas'] = (guardar_asignacion_modelos(mejor, sucursal, db)
                                           if guardar_asignacion else 0)
    resultado['duracion_s'] = (datetime.datetime.now() - t0).total_seconds()
    log('Método elegido por producto: ' + ' · '.join(f'{et(m)}: {k}' for m, k in conteo.items()))
    log(f'Excel de comparación (datos técnicos): {resultado["ruta_excel"]}'
        + (f' · método guardado para {resultado["asignaciones_guardadas"]:,} productos' if guardar_asignacion else ''))
    progreso(1.0, 'Comparación terminada')
    return resultado


def guardar_asignacion_modelos(mejor_por_sku, sucursal, db=None):
    """Guarda en la base (tabla asignacion_modelos) el modelo elegido por SKU. Devuelve las filas guardadas.

    mejor_por_sku: DataFrame de seleccionar_mejor_modelo (sku, modelo, wmape, mae, bias, periodos, confianza).
    """
    if db is None:
        from database import Database
        db = Database.default()
    return db.guardar_asignacion_modelos(sucursal, mejor_por_sku)


def exportar_comparacion(r, carpeta):
    """Excel TÉCNICO de la comparación (para análisis / el artículo): métricas con sus nombres técnicos."""
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f'comparacion_modelos_{datetime.datetime.now():%Y-%m-%d_%H-%M}.xlsx')
    cols_res = {'modelo': 'Modelo', 'wmape': 'WMAPE (%)', 'wmape_base_comun': 'WMAPE base común (%)',
                'wmape_total': 'WMAPE venta total (%)', 'wmape_con_stock': 'WMAPE con stock (%)', 'mae': 'MAE (u)',
                'bias': 'Bias (u)', 'productos': 'Productos', 'periodos': 'Períodos',
                'sku_donde_es_el_mejor': 'SKU donde es el mejor'}
    metricas_cols = {'wmape': 'WMAPE (%)', 'wmape_total': 'WMAPE venta total (%)',
                     'wmape_con_stock': 'WMAPE con stock (%)', 'mae': 'MAE (u)', 'rmse': 'RMSE (u)',
                     'bias': 'Bias (u)', 'mape': 'MAPE (%) — ' + NOTA_MAPE}
    with pd.ExcelWriter(ruta, engine='openpyxl') as xw:
        r['resumen'].sort_values('wmape_base_comun').rename(columns=cols_res).round(2).to_excel(
            xw, sheet_name='Resumen por Modelo', index=False)
        r['mejor_por_sku'].round(2).to_excel(xw, sheet_name='Mejor Modelo por SKU', index=False)
        r['metricas_sku'][['sku', 'modelo', 'wmape', 'mae', 'bias', 'periodos', 'suma_real', 'suma_pronostico']
                          ].round(2).to_excel(xw, sheet_name='Métricas SKU x Modelo', index=False)
        r['comparacion'].to_excel(xw, sheet_name='WMAPE por SKU y Modelo')
        _escribir_desagregadas(xw, {'por_segmento': r['por_segmento_modelo'],
                                    'por_clase_abc': r['por_clase_abc_modelo']}, metricas_cols, True)
        if len(r['importancias']):
            r['importancias'].round(4).to_excel(xw, sheet_name='Importancia de variables', index=False)
        if r['fallidos']:
            pd.DataFrame(list(r['fallidos'].items()), columns=['Modelo', 'Error']).to_excel(
                xw, sheet_name='Modelos con error', index=False)
        pd.DataFrame(NOTAS_COMPARACION, columns=['Concepto', 'Definición']).to_excel(
            xw, sheet_name='Notas', index=False)
        for ws in xw.book.worksheets:
            _formatear(ws, {2: 90} if ws.title == 'Notas' else None)
    return ruta


def exportar_reporte_gestion(r, carpeta):
    """Excel para el REPORTE DE GESTIÓN: lenguaje de negocio, sin siglas ni nombres técnicos de modelos."""
    from preve_traducciones import CONFIANZAS, interpretar_confianza, traducir_modelo
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f'reporte_gestion_metodos_{datetime.datetime.now():%Y-%m-%d_%H-%M}.xlsx')
    mejor = r['mejor_por_sku'].sort_values('venta_real', ascending=False)
    productos = pd.DataFrame({
        'Código': mejor['sku'], 'Producto': mejor['nombre'],
        'Método recomendado': [traducir_modelo(m) for m in mejor['modelo']],
        'Confiabilidad': mejor['confianza'], 'Precisión estimada': mejor['precision'],
        'Margen de error estimado (%)': mejor['wmape'].round(0),
        'Qué significa': [interpretar_confianza(c)[1] for c in mejor['confianza']],
        'Unidades vendidas en el período evaluado': mejor['venta_real'].round(0),
    })
    conteo = mejor['confianza'].value_counts()
    metodos = mejor['modelo'].value_counts()
    filas = [('Productos analizados', f'{len(mejor):,}'),
             ('Métodos de pronóstico comparados', f'{len(r["modelos"])}'),
             ('Método recomendado para más productos',
              f'{traducir_modelo(metodos.index[0])} ({metodos.iloc[0]:,} productos)' if len(metodos) else '—')]
    for c in CONFIANZAS:
        filas.append((f'Productos con confiabilidad {c.lower()}',
                      f'{int(conteo.get(c, 0)):,} — {interpretar_confianza(c)[1]}'))
    filas.append(('Cómo se eligió', 'Para cada producto se simuló cómo habría pronosticado cada método en el '
                                    'pasado y se eligió el que menos se equivocó frente a las ventas reales.'))
    with pd.ExcelWriter(ruta, engine='openpyxl') as xw:
        pd.DataFrame(filas, columns=['Resumen', 'Detalle']).to_excel(xw, sheet_name='Resumen', index=False)
        productos.to_excel(xw, sheet_name='Productos', index=False)
        pd.DataFrame({'Método': [traducir_modelo(m) for m in metodos.index],
                      'Productos donde es el mejor': metodos.values}).to_excel(xw, sheet_name='Métodos', index=False)
        for ws in xw.book.worksheets:
            _formatear(ws, {1: 44, 2: 90} if ws.title == 'Resumen' else {2: 44, 3: 44, 5: 46, 7: 70})
    return ruta
