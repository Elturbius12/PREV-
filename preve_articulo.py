# -*- coding: utf-8 -*-
"""PREVÉ — Evaluación con rigor publicable (artículo). Módulo ADITIVO: producción no lo usa.

  · valorizar_compras / resumen_economico  (B4): nivel de servicio, exceso de inventario y venta perdida
    simulados a partir de las compras que `Backtester.simular_compra` habría recomendado.
  · bootstrap_wmape / comparar_significancia (B2): IC bootstrap por ventanas y test de Diebold-Mariano
    (con corrección de Harvey-Leybourne-Newbold) + Wilcoxon pareado como alternativa no paramétrica.
  · evaluar_modelo (B1/B3/B4): walk-forward de un modelo con cualquier horizonte y su simulación de compras.

Supuestos de valorización (todos derivados del catálogo; nada se imputa):
  · costo unitario  = 'PRECIO COMPRA' del catálogo (costo_puesto_almacen_pen).
  · margen unitario = PRECIO VENTA / factor IGV − costo   (misma fórmula que el pipeline).
  · exceso de inventario S/. = unidades sobrantes × costo  (capital inmovilizado, no pérdida).
  · venta perdida S/.        = unidades faltantes × margen (margen no capturado).
  · un SKU sin costo o sin precio queda como "no disponible" (NaN) y se cuenta aparte; no se rellena.
  · la "demanda" contra la que se mide es la venta OBSERVADA del horizonte (también censurada por quiebres):
    el nivel de servicio simulado es por eso una cota optimista. Se declara como limitación.
"""
import numpy as np
import pandas as pd

from preve_utils import get_logger

logger = get_logger('articulo')

SEMILLA = 20250101
N_BOOTSTRAP = 2000

SUPUESTOS_VALORIZACION = [
    'Costo unitario: columna PRECIO COMPRA del catálogo de productos (costo puesto en almacén, S/.).',
    'Margen unitario: PRECIO VENTA / factor IGV − costo unitario (misma fórmula que usa PREVÉ).',
    'Exceso de inventario (S/.): unidades sobrantes al final del horizonte × costo unitario (capital inmovilizado).',
    'Venta perdida (S/.): unidades faltantes en el horizonte × margen unitario (margen no capturado).',
    'Nivel de servicio: unidades atendidas / venta observada del horizonte; atendidas = mín(venta, stock + compra).',
    'La venta observada también está censurada por quiebres: el nivel de servicio es una cota optimista.',
    'SKU sin costo o sin precio en el catálogo: valor "no disponible"; no se imputa y se reporta su número.',
    'Horizonte incompleto al final de los datos: la venta real se extrapola proporcionalmente (lo marca '
    'Backtester.simular_compra como horizonte parcial).',
]


# ══════════════════════════════════════════════════════════════════
# B4 — impacto económico
# ══════════════════════════════════════════════════════════════════
def valorizar_compras(compras, dim, igv_factor=1.18, log=lambda m: None):
    """Agrega a las compras simuladas (salida de simular_compra) atendido, nivel de servicio y valores en S/."""
    c = compras.copy()
    d = dim.drop_duplicates('sku').set_index('sku')
    costo = c['sku'].map(d['costo_puesto_almacen_pen']) if 'costo_puesto_almacen_pen' in d else np.nan
    precio = c['sku'].map(d['precio_regular_pen']) if 'precio_regular_pen' in d else np.nan
    c['costo_unit'] = pd.to_numeric(costo, errors='coerce')
    c['precio_unit'] = pd.to_numeric(precio, errors='coerce')
    c['costo_unit'] = c['costo_unit'].where(c['costo_unit'] > 0)
    c['margen_unit'] = (c['precio_unit'] / igv_factor - c['costo_unit']).where(c['precio_unit'] > 0)
    demanda = c['real_horizonte_estimado']
    disponible = c['stock_actual'].clip(lower=0) + c['compra_sugerida_unid']
    c['demanda_obs'] = demanda
    c['atendido'] = np.minimum(demanda, disponible)
    c['exceso_pen'] = c['sobrante'] * c['costo_unit']
    c['venta_perdida_pen'] = c['faltante'] * c['margen_unit']
    c['inversion_pen'] = c['compra_sugerida_unid'] * c['costo_unit']
    c['valorizacion_disponible'] = c['costo_unit'].notna() & c['margen_unit'].notna()
    n_no = int((~c['valorizacion_disponible']).groupby(c['sku']).all().sum())
    if n_no:
        log(f'  Valorización no disponible para {n_no} SKU (sin costo o precio en el catálogo).')
        logger.info('Valorización no disponible para %d SKU', n_no)
    return c


def resumen_economico(c):
    """Una fila con los indicadores de compras de un conjunto de compras valorizadas."""
    dem = float(c['demanda_obs'].sum())
    val = c[c['valorizacion_disponible']]
    return {'nivel_servicio_pct': float(c['atendido'].sum() / dem * 100) if dem > 0 else np.nan,
            'unidades_compradas': float(c['compra_sugerida_unid'].sum()),
            'exceso_unid': float(c['sobrante'].sum()), 'exceso_pen': float(val['exceso_pen'].sum()),
            'venta_perdida_unid': float(c['faltante'].sum()),
            'venta_perdida_pen': float(val['venta_perdida_pen'].sum()),
            'inversion_pen': float(val['inversion_pen'].sum()),
            'skus_simulados': int(c['sku'].nunique()),
            'skus_sin_valorizacion': int((~c['valorizacion_disponible']).groupby(c['sku']).all().sum()),
            'ciclos_simulados': int(c['dia_reporte'].nunique())}


# ══════════════════════════════════════════════════════════════════
# B2 — significancia
# ══════════════════════════════════════════════════════════════════
def _por_ventana(detalle):
    """Σ|error| y Σreal por (modelo, ventana)."""
    d = detalle.assign(_abs=(detalle['pronostico'] - detalle['real']).abs())
    return d.groupby(['modelo', 'periodo_inicio']).agg(abs=('_abs', 'sum'), real=('real', 'sum')).reset_index()


def base_comun(detalle):
    """Filas (sku, periodo) que todos los modelos pronosticaron: la comparación es sobre la misma muestra."""
    n = detalle['modelo'].nunique()
    k = detalle.groupby(['sku', 'periodo_inicio'])['modelo'].transform('nunique')
    return detalle[k == n]


def bootstrap_wmape(detalle, n=N_BOOTSTRAP, semilla=SEMILLA, nivel=0.95):
    """IC bootstrap del WMAPE de cada modelo remuestreando VENTANAS completas (bloques; respeta la dependencia
    entre SKU de una misma ventana). Devuelve modelo, wmape, ic_inf, ic_sup, ventanas."""
    pv = _por_ventana(detalle)
    rng = np.random.default_rng(semilla)
    ventanas = np.sort(pv['periodo_inicio'].unique())
    idx = rng.integers(0, len(ventanas), size=(n, len(ventanas)))
    a = (1 - nivel) / 2
    filas = []
    for m, g in pv.groupby('modelo'):
        g = g.set_index('periodo_inicio').reindex(ventanas).fillna(0)
        A, R = g['abs'].to_numpy()[idx].sum(1), g['real'].to_numpy()[idx].sum(1)
        boot = np.where(R > 0, A / np.where(R > 0, R, 1) * 100, np.nan)
        filas.append({'modelo': m, 'wmape': g['abs'].sum() / g['real'].sum() * 100 if g['real'].sum() else np.nan,
                      'ic_inf': float(np.nanquantile(boot, a)), 'ic_sup': float(np.nanquantile(boot, 1 - a)),
                      'ventanas': len(ventanas), 'replicas': n, 'semilla': semilla})
    return pd.DataFrame(filas)


def diebold_mariano(d, h=1):
    """DM con varianza HAC (Newey-West, h−1 rezagos) y corrección HLN; p bilateral con t(T−1)."""
    from scipy import stats
    d = np.asarray(d, dtype=float)
    d = d[~np.isnan(d)]
    T = len(d)
    if T < 3 or np.allclose(d, d[0]):
        return np.nan, np.nan
    dm = d.mean()
    g = [np.mean((d[k:] - dm) * (d[:T - k] - dm)) for k in range(h)]
    var = (g[0] + 2 * sum(g[1:])) / T
    if var <= 0:
        return np.nan, np.nan
    stat = dm / np.sqrt(var) * np.sqrt((T + 1 - 2 * h + h * (h - 1) / T) / T)
    return float(stat), float(2 * stats.t.sf(abs(stat), T - 1))


def comparar_significancia(detalle, propuesto, n=N_BOOTSTRAP, semilla=SEMILLA):
    """Propuesto vs cada baseline, sobre la base común. Pérdida por ventana = WMAPE de la ventana.

    d_t = WMAPE_baseline(t) − WMAPE_propuesto(t)  (> 0: el propuesto es mejor). Reporta DM-HLN, Shapiro-Wilk de
    d_t (supuesto de normalidad del DM en muestras chicas), Wilcoxon de rangos con signo (no paramétrico) sobre
    d_t, e IC bootstrap pareado (por ventanas) de la diferencia de WMAPE global.
    """
    from scipy import stats
    pv = _por_ventana(base_comun(detalle))
    pv['wmape'] = pv['abs'] / pv['real'].where(pv['real'] > 0) * 100
    tabla = pv.pivot(index='periodo_inicio', columns='modelo', values='wmape')
    A = pv.pivot(index='periodo_inicio', columns='modelo', values='abs').fillna(0)
    R = pv.pivot(index='periodo_inicio', columns='modelo', values='real').fillna(0)
    if propuesto not in tabla.columns:
        return pd.DataFrame()
    rng = np.random.default_rng(semilla)
    idx = rng.integers(0, len(tabla), size=(n, len(tabla)))
    filas = []
    for b in tabla.columns:
        if b == propuesto:
            continue
        d = (tabla[b] - tabla[propuesto]).dropna().to_numpy()
        dm, p_dm = diebold_mariano(d)
        p_sw = float(stats.shapiro(d).pvalue) if len(d) >= 3 and np.ptp(d) > 0 else np.nan
        try:
            p_w = float(stats.wilcoxon(d).pvalue) if np.any(d != 0) else np.nan
        except ValueError:
            p_w = np.nan
        Ab, Ap, Rr = A[b].to_numpy()[idx].sum(1), A[propuesto].to_numpy()[idx].sum(1), R[propuesto].to_numpy()[idx].sum(1)
        diff = np.where(Rr > 0, (Ab - Ap) / np.where(Rr > 0, Rr, 1) * 100, np.nan)
        normal = (p_sw >= 0.05) if not np.isnan(p_sw) else None
        filas.append({'propuesto': propuesto, 'baseline': b, 'ventanas': len(d),
                      'mejora_media_wmape_pp': float(d.mean()) if len(d) else np.nan,
                      'dm_estadistico': dm, 'dm_p': p_dm, 'shapiro_p': p_sw, 'wilcoxon_p': p_w,
                      'dif_wmape_ic_inf': float(np.nanquantile(diff, .025)),
                      'dif_wmape_ic_sup': float(np.nanquantile(diff, .975)),
                      'test_recomendado': ('Diebold-Mariano' if normal else 'Wilcoxon' if normal is False
                                           else 'no evaluable'),
                      'p_recomendado': p_dm if normal else p_w if normal is False else np.nan})
    return pd.DataFrame(filas)


# ══════════════════════════════════════════════════════════════════
# Walk-forward de un modelo (B1/B3/B4)
# ══════════════════════════════════════════════════════════════════
def evaluar_modelo(datos, nombre, fecha_inicio, fecha_fin, params, horizonte_dias=None, simular_compras=True,
                   sucursal='San José', plantilla=None, log=lambda m: None, pronosticador=None):
    """Backtester.run(persistir=False) con el pronosticador `nombre`. Devuelve (detalle, compras_valorizadas|None).

    horizonte_dias: largo de cada ventana de evaluación (= horizonte del pronóstico); None = el configurado.
    """
    import preve_modelos_baseline as mb
    from preve_backtesting import Backtester
    fn = pronosticador or mb.pronosticador(nombre)
    bt = Backtester(datos, params=params, plantilla=plantilla, ventana_dias=horizonte_dias, sucursal=sucursal,
                    pronosticador=fn, log=log, db=_SinBase())
    r = bt.run(fecha_inicio, fecha_fin, simular_compras=simular_compras, persistir=False)
    det = r['detalle'].assign(modelo=nombre, sucursal=sucursal, horizonte_dias=bt.ventana_dias)
    comp = None
    if r['compras'] is not None and len(r['compras']):
        comp = valorizar_compras(r['compras'], datos.dim, getattr(params, 'igv_factor', 1.18), log).assign(
            modelo=nombre, sucursal=sucursal)
    return det, comp


class _SinBase:
    """Backtester con persistir=False no escribe; evita abrir la base por accidente."""
    def __getattr__(self, k):
        raise RuntimeError('El análisis del artículo no escribe en la base de datos.')
