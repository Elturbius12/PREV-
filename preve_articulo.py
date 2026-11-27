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
    'Exceso evitado (S/.): exceso_evitado = exceso_baseline − exceso_propuesto, con baseline = Naive (E0, el '
    'comprador sin sistema). Se calcula sobre la base común (sucursal, SKU, día de reporte) que ambos modelos '
    'simularon y que tiene valorización disponible; > 0 significa capital inmovilizado que el modelo evita frente '
    'a Naive. No es el sobre-stock existente de los productos REDUCIR/LIQUIDAR (ítem M10 de AUDITORIA.md).',
]

# Escenarios de la plantilla del artículo (E0 sin sistema, E1 ML sin la innovación, E2 modelo completo)
ESCENARIO_BASE = 'E0'
ESCENARIOS = {
    'E0': ('Naive', 'Base: sin sistema (repite la venta de la última semana × horizonte/7)'),
    'E1': ('GBM sin disponibilidad', 'Propuesto sin innovación: GBM estándar sin variables de censura'),
    'E2': ('Pipeline (producción)', 'Propuesto con innovación: modelo completo con ICD y variables de disponibilidad'),
}
BASELINE_EXCESO = ESCENARIOS['E0'][0]
CUANTIL_INF, CUANTIL_SUP = 0.10, 0.90
MIN_RESIDUOS_SKU = 3


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


def resumen_economico(c, baseline=None):
    """Una fila con los indicadores de compras de un conjunto de compras valorizadas.

    baseline: compras valorizadas del modelo de referencia (Naive = E0). Si se pasa, agrega
    exceso_evitado_pen = exceso_baseline − exceso_propuesto (ver exceso_evitado; M10).
    """
    dem = float(c['demanda_obs'].sum())
    val = c[c['valorizacion_disponible']]
    extra = {} if baseline is None else {'exceso_evitado_pen': exceso_evitado(c, baseline)['exceso_evitado_pen']}
    return {**_resumen_economico(c, dem, val), **extra}


_CLAVE_COMPRA = ['sucursal', 'sku', 'dia_reporte']


def _clave(c):
    return [k for k in _CLAVE_COMPRA if k in c.columns]


def exceso_evitado(propuesto, baseline):
    """exceso_evitado = exceso_baseline − exceso_propuesto (S/.), sobre la base común (sucursal, SKU, día de
    reporte) que ambos simularon con valorización disponible. Devuelve dict con exceso_baseline_pen,
    exceso_propuesto_pen, exceso_evitado_pen y filas_base_comun."""
    k = _clave(propuesto)
    a = propuesto.loc[propuesto['valorizacion_disponible'], k + ['exceso_pen']]
    b = baseline.loc[baseline['valorizacion_disponible'], k + ['exceso_pen']]
    m = a.groupby(k)['exceso_pen'].sum().to_frame('p').join(b.groupby(k)['exceso_pen'].sum().rename('b'),
                                                             how='inner')
    eb, ep = float(m['b'].sum()), float(m['p'].sum())
    return {'exceso_baseline_pen': eb, 'exceso_propuesto_pen': ep, 'exceso_evitado_pen': eb - ep,
            'filas_base_comun': int(len(m))}


def _resumen_economico(c, dem, val):
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


# ══════════════════════════════════════════════════════════════════
# Diagnóstico de residuos (Tabla 10)
# ══════════════════════════════════════════════════════════════════
def analisis_residuos(detalle, modelos, semilla=SEMILLA, max_shapiro=5000):
    """Residuos e = pronóstico − real de cada modelo.

    · shapiro_w / shapiro_p: Shapiro-Wilk sobre el residuo MEDIO por ventana (una observación por ventana).
    · shapiro_w_obs / shapiro_p_obs: Shapiro-Wilk sobre los residuos SKU×ventana (submuestra de ≤ 5000 con
      semilla fija; scipy no garantiza el p-valor por encima de ese tamaño).
    · ljung_box_Q / ljung_box_p: Ljung-Box sobre el residuo medio por ventana (orden temporal), con
      lag = mín(10, n_ventanas // 2) (≥ 1).
    · media, mediana, asimetría y curtosis (exceso de Fisher) de los residuos SKU×ventana.
    """
    from scipy import stats
    filas = []
    rng = np.random.default_rng(semilla)
    for m in modelos:
        g = detalle[detalle['modelo'] == m]
        if g.empty:
            continue
        e = (g['pronostico'] - g['real']).astype(float)
        r = e.to_numpy()
        r = r[np.isfinite(r)]
        pv = e.groupby(g['periodo_inicio']).mean().sort_index().dropna().to_numpy()
        n_v = len(pv)
        sw = (stats.shapiro(pv) if n_v >= 3 and np.ptp(pv) > 0 else (np.nan, np.nan))
        mu = r if len(r) <= max_shapiro else rng.choice(r, max_shapiro, replace=False)
        sw_obs = stats.shapiro(mu) if len(mu) >= 3 and np.ptp(mu) > 0 else (np.nan, np.nan)
        lag = max(1, min(10, n_v // 2))
        q, p_lb = np.nan, np.nan
        if n_v >= 3 and np.ptp(pv) > 0:
            try:
                from statsmodels.stats.diagnostic import acorr_ljungbox
                lb = acorr_ljungbox(pv, lags=[lag], return_df=True)
                q, p_lb = float(lb['lb_stat'].iloc[0]), float(lb['lb_pvalue'].iloc[0])
            except Exception as ex:  # noqa: BLE001 — serie demasiado corta o degenerada
                logger.info('Ljung-Box no evaluable para %s: %s', m, ex)
        filas.append({'modelo': m, 'shapiro_w': float(sw[0]), 'shapiro_p': float(sw[1]),
                      'shapiro_w_obs': float(sw_obs[0]), 'shapiro_p_obs': float(sw_obs[1]),
                      'ljung_box_Q': q, 'ljung_box_p': p_lb, 'ljung_box_lag': lag,
                      'media_residuos': float(np.mean(r)) if len(r) else np.nan,
                      'mediana_residuos': float(np.median(r)) if len(r) else np.nan,
                      'asimetria': float(stats.skew(r)) if len(r) > 2 else np.nan,
                      'curtosis': float(stats.kurtosis(r)) if len(r) > 3 else np.nan,
                      'n_observaciones': int(len(r)), 'n_ventanas': int(n_v)})
    return pd.DataFrame(filas)


# ══════════════════════════════════════════════════════════════════
# Intervalos P10/P90 fuera de muestra (M8)
# ══════════════════════════════════════════════════════════════════
def intervalos_oos(detalle, q_inf=CUANTIL_INF, q_sup=CUANTIL_SUP, min_obs=MIN_RESIDUOS_SKU):
    """Intervalos empíricos fuera de muestra a partir de los residuos del backtesting.

    Residuo r = real − pronóstico (así P10 = pronóstico + q10(r) es la cota inferior; equivale a
    pronóstico − q90(pronóstico − real)). Para la fila (modelo, SKU, ventana t) se usan SOLO los residuos del
    mismo modelo y SKU en ventanas anteriores a t (ventana expansiva: ninguna fila ve su propio error).
    Con menos de `min_obs` residuos propios se usan los de todos los SKU de la misma clase ABC en ventanas
    anteriores; sin ninguno, el intervalo queda NaN (no se imputa).
    Agrega p10_oos, p90_oos (p10 ≥ 0), n_residuos y fuente_intervalo ('sku' / 'clase_abc' / 'sin historia').
    """
    out = []
    d = detalle.copy()
    d['_r'] = d['real'].astype(float) - d['pronostico'].astype(float)
    if 'clase_abc' not in d.columns:
        d['clase_abc'] = 'Total'
    for _, g in d.groupby('modelo', sort=False):
        g = g.copy()
        ventanas = np.sort(g['periodo_inicio'].unique())
        g['_j'] = g['periodo_inicio'].map({v: i for i, v in enumerate(ventanas)})
        M = g.pivot_table(index='sku', columns='_j', values='_r', aggfunc='mean').reindex(
            columns=range(len(ventanas)))
        lo = np.full(len(g), np.nan)
        hi = np.full(len(g), np.nan)
        n = np.zeros(len(g), dtype=int)
        fuente = np.array(['sin historia'] * len(g), dtype=object)
        pos_sku = {s: i for i, s in enumerate(M.index)}
        A = M.to_numpy()
        gi = g.reset_index(drop=True)
        for j in range(1, len(ventanas)):
            filas_j = np.flatnonzero(gi['_j'].to_numpy() == j)
            if not len(filas_j):
                continue
            prev = A[:, :j]
            cnt = np.sum(np.isfinite(prev), axis=1)
            with np.errstate(all='ignore'):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', RuntimeWarning)
                    ql = np.nanquantile(prev, q_inf, axis=1)
                    qh = np.nanquantile(prev, q_sup, axis=1)
            previas = gi[gi['_j'] < j]
            pool = {k: v['_r'].to_numpy() for k, v in previas.groupby('clase_abc')}
            for f in filas_j:
                i = pos_sku[gi.at[f, 'sku']]
                if cnt[i] >= min_obs:
                    lo[f], hi[f], n[f], fuente[f] = ql[i], qh[i], cnt[i], 'sku'
                else:
                    p = pool.get(gi.at[f, 'clase_abc'])
                    if p is not None and len(p) >= min_obs:
                        lo[f], hi[f] = np.quantile(p, q_inf), np.quantile(p, q_sup)
                        n[f], fuente[f] = len(p), 'clase_abc'
        gi['p10_oos'] = np.clip(gi['pronostico'].to_numpy() + lo, 0, None)
        gi['p90_oos'] = gi['pronostico'].to_numpy() + hi
        gi['n_residuos'] = n
        gi['fuente_intervalo'] = fuente
        out.append(gi.drop(columns=['_j']))
    return pd.concat(out, ignore_index=True).drop(columns=['_r']) if out else d.drop(columns=['_r'])


def cobertura_intervalos(d, nominal=(CUANTIL_SUP - CUANTIL_INF) * 100):
    """Cobertura empírica por modelo del intervalo OOS y del intervalo propio del modelo (demanda_p10/p90,
    paramétrico) sobre las MISMAS filas (las que tienen intervalo OOS)."""
    filas = []
    for m, g in d.groupby('modelo'):
        g = g[g['p10_oos'].notna() & g['p90_oos'].notna()]
        if g.empty:
            continue
        real = g['real'].to_numpy(float)
        dentro = (real >= g['p10_oos']) & (real <= g['p90_oos'])
        fila = {'modelo': m, 'nominal_pct': nominal, 'filas': int(len(g)),
                'cobertura_oos_pct': float(dentro.mean() * 100),
                'cobertura_p90_oos_pct': float((real <= g['p90_oos']).mean() * 100),
                'ancho_medio_oos': float((g['p90_oos'] - g['p10_oos']).mean()),
                'pct_filas_fuente_sku': float((g['fuente_intervalo'] == 'sku').mean() * 100)}
        if {'demanda_p10_141d', 'demanda_p90_141d'} <= set(g.columns):
            p10, p90 = g['demanda_p10_141d'].astype(float), g['demanda_p90_141d'].astype(float)
            fila.update({'cobertura_modelo_pct': float(((real >= p10) & (real <= p90)).mean() * 100),
                         'cobertura_p90_modelo_pct': float((real <= p90).mean() * 100),
                         'ancho_medio_modelo': float((p90 - p10).mean())})
        filas.append(fila)
    return pd.DataFrame(filas)


def cuantiles_residuos_sku(detalle, q_inf=CUANTIL_INF, q_sup=CUANTIL_SUP):
    """Para aplicar a un pronóstico NUEVO: por (modelo, SKU), cuantiles de r = real − pronóstico sobre TODAS
    las ventanas del backtesting. P10 = pronóstico + q_inf, P90 = pronóstico + q_sup."""
    d = detalle.assign(_r=detalle['real'].astype(float) - detalle['pronostico'].astype(float))
    g = d.groupby(['modelo', 'sku'])['_r']
    return pd.DataFrame({'q_inf': g.quantile(q_inf), 'q_sup': g.quantile(q_sup), 'n_residuos': g.count()}
                        ).reset_index()


# ══════════════════════════════════════════════════════════════════
# Escenarios E0/E1/E2 (Tabla 11)
# ══════════════════════════════════════════════════════════════════
def escenario_de(modelo, escenarios=ESCENARIOS):
    """'E0'/'E1'/'E2' del modelo, o '' si no es uno de los tres."""
    return next((e for e, (m, _) in escenarios.items() if m == modelo), '')


def tabla_escenarios(t4, compras=None, escenarios=ESCENARIOS):
    """Resumen E0/E1/E2: precisión (Tabla 4) + economía de la simulación de compras.

    La economía se recalcula sobre la base común (sucursal, SKU, día de reporte) que los TRES modelos
    simularon, para que las cifras en S/. sean comparables entre escenarios.
    mejora_wmape_vs_E0_pct = 100 × (WMAPE_E0 − WMAPE_Ex) / WMAPE_E0.
    """
    t4 = t4.set_index('modelo')
    base = escenarios[ESCENARIO_BASE][0]
    w0 = float(t4.loc[base, 'wmape']) if base in t4.index else np.nan
    comunes, cv = None, None
    if compras is not None and len(compras):
        cv = compras[compras['modelo'].isin([m for m, _ in escenarios.values()])]
        n_mod = cv['modelo'].nunique()
        if n_mod and base in set(cv['modelo']):          # base común de los escenarios que se simularon
            comunes = cv.groupby(_clave(cv))['modelo'].transform('nunique') == n_mod
    filas = []
    for e, (m, desc) in escenarios.items():
        f = {'escenario': e, 'modelo': m, 'descripcion': desc}
        for c in ('wmape', 'ic_inf', 'ic_sup'):
            f[c] = float(t4.loc[m, c]) if m in t4.index else np.nan
        f['mejora_wmape_vs_E0_pct'] = 100 * (w0 - f['wmape']) / w0 if w0 and np.isfinite(w0) else np.nan
        if comunes is not None and m in set(cv['modelo']):
            c = cv[comunes & (cv['modelo'] == m)]
            r = resumen_economico(c)
            b = cv[comunes & (cv['modelo'] == base)]
            f.update({'nivel_servicio_pct': r['nivel_servicio_pct'], 'exceso_pen': r['exceso_pen'],
                      'venta_perdida_pen': r['venta_perdida_pen'], 'inversion_pen': r['inversion_pen'],
                      'exceso_evitado_pen': exceso_evitado(c, b)['exceso_evitado_pen'],
                      'skus_base_comun': int(c['sku'].nunique()), 'filas_base_comun': int(len(c))})
        else:
            f.update({k: np.nan for k in ('nivel_servicio_pct', 'exceso_pen', 'venta_perdida_pen',
                                          'inversion_pen', 'exceso_evitado_pen')})
        filas.append(f)
    return pd.DataFrame(filas)


# ══════════════════════════════════════════════════════════════════
# Diccionario de variables (Tabla 0)
# ══════════════════════════════════════════════════════════════════
VARIABLES = [
    # simbolo, nombre, tipo, unidad, fuente
    ('i', 'Índice de producto (SKU)', 'categórica', 'adimensional', 'catálogo'),
    ('t', 'Índice de semana (lunes a domingo)', 'discreta', 'semanas', 'calculada'),
    ('d_{i,t}', 'Demanda observada: venta neta del SKU i en la semana t', 'discreta', 'unidades', 'ventas'),
    ('d^*_{i,t}', 'Demanda latente (no censurada por quiebres de stock); no observable', 'continua', 'unidades',
     'calculada'),
    ('\\hat{d}_{i,t}', 'Pronóstico puntual (P50) de la demanda del horizonte', 'continua', 'unidades', 'calculada'),
    ('\\hat{d}^{P10}_{i}, \\hat{d}^{P90}_{i}', 'Cuantiles 10 y 90 del pronóstico del horizonte', 'continua',
     'unidades', 'calculada'),
    ('s_{i,t}', 'Stock al cierre del día, reconstruido hacia atrás desde el catálogo', 'continua', 'unidades',
     'calculada'),
    ('a_{i,t}', 'Disponibilidad: 1 si s_{i,t} > 0 (día con stock), 0 si no', 'binaria', 'adimensional',
     'calculada'),
    ('av_k', 'Media móvil de la disponibilidad semanal en las últimas k semanas (k = 4, 13, 26)', 'continua',
     'adimensional', 'calculada'),
    ('c_{i,t}', 'Semana censurada: d_{i,t} = 0 y disponibilidad < 50 % de los días', 'binaria', 'adimensional',
     'calculada'),
    ('ICD_i', 'Índice de Ceguera de Demanda = semanas censuradas / semanas sin venta', 'continua',
     'adimensional', 'calculada'),
    ('ADI_i', 'Intervalo medio entre demandas (Syntetos-Boylan)', 'continua', 'semanas', 'calculada'),
    ('CV^2_i', 'Coeficiente de variación al cuadrado de las demandas no nulas', 'continua', 'adimensional',
     'calculada'),
    ('ABC_i', 'Clase ABC por margen acumulado (A ≤ 80 %, B ≤ 95 %, C resto)', 'categórica', 'adimensional', 'calculada'),
    ('c^{u}_i', 'Costo unitario (PRECIO COMPRA)', 'continua', 'S/.', 'catálogo'),
    ('p_i', 'Precio de venta unitario con IGV (PRECIO VENTA)', 'continua', 'S/.', 'catálogo'),
    ('m_i', 'Margen unitario = p_i / factor IGV − c^{u}_i', 'continua', 'S/.', 'calculada'),
    ('q^{tr}_i', 'Stock en tránsito (órdenes abiertas)', 'continua', 'unidades', 'compras'),
    ('\\sigma_i', 'Desvío de la demanda del horizonte = (P90 − P50) / 1,2816', 'continua', 'unidades', 'calculada'),
    ('SS_i', 'Stock de seguridad = z_i · σ_i', 'continua', 'unidades', 'calculada'),
    ('R_i', 'Punto de reorden = \\hat{d}_i · L/H + SS_i · √(mín(1, L/H))', 'continua', 'unidades', 'calculada'),
    ('S_i', 'Nivel objetivo = \\hat{d}_i + SS_i', 'continua', 'unidades', 'calculada'),
    ('Q_i', 'Cantidad a pedir = máx(0, S_i − s_i − q^{tr}_i), redondeada', 'discreta', 'unidades', 'calculada'),
    ('H', 'Horizonte de pronóstico (revisión + lead time)', 'discreta', 'días', 'parámetro'),
    ('L', 'Lead time del proveedor', 'discreta', 'días', 'parámetro'),
    ('C', 'Ciclo de compra (días entre revisiones / pedidos)', 'discreta', 'días', 'parámetro'),
    ('z_i', 'Factor de servicio según clase ABC (A 1,645; B 1,2816; C 1,0364 por defecto)', 'continua',
     'adimensional', 'parámetro'),
    ('\\alpha_i', 'Nivel de servicio objetivo = Φ(z_i)', 'continua', 'adimensional', 'parámetro'),
    ('WMAPE', 'Σ|\\hat{d} − d| / Σ d × 100', 'continua', '%', 'calculada'),
    ('MAE', 'Error absoluto medio', 'continua', 'unidades', 'calculada'),
    ('RMSE', 'Raíz del error cuadrático medio', 'continua', 'unidades', 'calculada'),
    ('Bias', 'Sesgo medio = media(\\hat{d} − d)', 'continua', 'unidades', 'calculada'),
    ('MASE', 'MAE del modelo / MAE del naive (venta real de la ventana anterior del SKU)', 'continua', 'adimensional',
     'calculada'),
    ('NS', 'Nivel de servicio = Σ mín(d, s + Q) / Σ d × 100', 'continua', '%', 'calculada'),
    ('E', 'Exceso de inventario = máx(0, s + Q − d) × c^{u}', 'continua', 'S/.', 'calculada'),
    ('VP', 'Venta perdida = máx(0, d − s − Q) × m', 'continua', 'S/.', 'calculada'),
    ('EE', 'Exceso evitado = E_{Naive} − E_{modelo} (base común)', 'continua', 'S/.', 'calculada'),
]


def diccionario_variables():
    """Tabla 0: símbolo, nombre, tipo, unidad y fuente de cada variable del artículo."""
    return pd.DataFrame(VARIABLES, columns=['simbolo', 'nombre', 'tipo', 'unidad', 'fuente'])


class _SinBase:
    """Backtester con persistir=False no escribe; evita abrir la base por accidente."""
    def __getattr__(self, k):
        raise RuntimeError('El análisis del artículo no escribe en la base de datos.')
