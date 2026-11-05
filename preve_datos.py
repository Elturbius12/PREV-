# -*- coding: utf-8 -*-
"""
PREVÉ — Capa de datos de la interfaz.

Lee lo que el pipeline ya generó (recomendacion_compra.csv, historial SQLite,
ciclo_estado.json) y lo convierte en lo que la interfaz necesita: urgencia por
producto, explicación en lenguaje natural, KPIs, comparación con el análisis
anterior y decisiones del usuario.

Regla de la casa: nunca inventa un número. Todo KPI sale de columnas reales
del CSV; si no se puede calcular, se devuelve None y la interfaz muestra "—".
"""
import os
import json
import shutil
import sqlite3
import datetime
import numpy as np
import pandas as pd

import preve_historial as hist
from preve_ciclo import cargar_estado_ciclo, CicloCorruptoError, CICLO_INTERNACIONAL_DIAS, LEAD_TIME_INTERNACIONAL
from preve_utils import get_logger

logger = get_logger('datos')

HORIZONTE_DIAS = 141
LEAD = LEAD_TIME_INTERNACIONAL
SUCURSALES = ['San José', 'Santa Barbara', 'Lima']

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preve_config.json')
_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
          'septiembre', 'octubre', 'noviembre', 'diciembre']


# ══════════════════════════════════════════════════════════════════
# Configuración persistente (carpetas, sucursal activa, usuario)
# ══════════════════════════════════════════════════════════════════
def config_defecto():
    return {
        'usuario': 'Prevedor',
        'sucursal': 'San José',
        'sucursales': {s: {'entrada': '', 'salida': ''} for s in SUCURSALES},
        'ultima_duracion_s': None,
        # True si el ERP registra cada venta también como "Salida" de almacén (evita doble descuento).
        # Con los reportes de Yuju (nov-2026) las salidas son ajustes manuales (~1% de las ventas): False.
        'ventas_son_salidas_de_almacen': False,
        # Ídem para compras y "Entrada" de almacén.
        'compras_son_entradas_de_almacen': False,
    }


def _respaldar_corrupto(ruta):
    """Copia un archivo ilegible a <ruta>.corrupto-<timestamp> antes de que se sobrescriba."""
    destino = f'{ruta}.corrupto-{datetime.datetime.now():%Y%m%d-%H%M%S}'
    try:
        shutil.copy2(ruta, destino)
        logger.error('Configuración ilegible; respaldada en %s y se usan valores por defecto', destino)
    except OSError:
        logger.exception('No se pudo respaldar la configuración corrupta %s', ruta)
    return destino


def cargar_config():
    cfg = config_defecto()
    # FIX: antes cualquier error se silenciaba y el archivo corrupto se sobrescribía con los defaults
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            guardada = json.load(f)
    except FileNotFoundError:
        return cfg
    except (json.JSONDecodeError, UnicodeDecodeError):
        _respaldar_corrupto(_CONFIG_PATH)
        return cfg
    except OSError:
        logger.exception('No se pudo leer %s', _CONFIG_PATH)
        return cfg
    if not isinstance(guardada, dict):
        _respaldar_corrupto(_CONFIG_PATH)
        return cfg
    for k, v in guardada.items():
        if k == 'sucursales' and isinstance(v, dict):
            for s, d in v.items():
                cfg['sucursales'].setdefault(s, {'entrada': '', 'salida': ''}).update(d)
        else:
            cfg[k] = v
    return cfg


def guardar_config(cfg):
    # FIX: escritura atómica (archivo temporal + reemplazo) y error registrado en el log
    tmp = _CONFIG_PATH + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _CONFIG_PATH)
    except (OSError, TypeError, ValueError):
        logger.exception('No se pudo guardar la configuración en %s', _CONFIG_PATH)


# ══════════════════════════════════════════════════════════════════
# Formato
# ══════════════════════════════════════════════════════════════════
def fmt_num(n, dec=0):
    if n is None or (isinstance(n, float) and (np.isnan(n) or np.isinf(n))):
        return '—'
    return f'{n:,.{dec}f}'


def fmt_soles(n, dec=0):
    v = fmt_num(n, dec)
    return v if v == '—' else f'S/ {v}'


def fecha_larga(d):
    return f'{d.day} de {_MESES[d.month - 1]}'


def hace_texto(dt):
    """'hoy', 'ayer', 'hace 3 días' ..."""
    if dt is None:
        return '—'
    dias = (datetime.datetime.now().date() - dt.date()).days
    if dias <= 0:
        return 'hoy'
    if dias == 1:
        return 'ayer'
    if dias < 30:
        return f'hace {dias} días'
    meses = dias // 30
    return f'hace {meses} mes' if meses == 1 else f'hace {meses} meses'


def semanas_texto(sem):
    if sem is None or np.isinf(sem):
        return 'bastante tiempo'
    if sem < 1:
        d = max(0, round(sem * 7))
        return 'menos de 1 día' if d == 0 else (f'{d} día' if d == 1 else f'{d} días')
    n = round(sem)
    return '1 semana' if n == 1 else f'{n} semanas'


# ══════════════════════════════════════════════════════════════════
# Derivados por producto
# ══════════════════════════════════════════════════════════════════
_NUM = ['stock_actual', 'demanda_p50_141d', 'demanda_p10_141d', 'demanda_p90_141d', 'compra_sugerida_unid',
        'costo_total_compra_pen', 'costo_puesto_almacen_pen', 'precio_regular_pen', 'rop', 'margen_pct',
        'tendencia_venta']


def derivar(df, lead=LEAD):
    d = df.copy()
    for c in _NUM:
        d[c] = pd.to_numeric(d[c], errors='coerce') if c in d.columns else np.nan
    for c in ('nombre', 'categoria', 'proveedor_principal', 'accion', 'motivo_accion', 'segmento',
              'clase_abc', 'modelo_asignado'):
        if c not in d.columns:
            d[c] = ''
        d[c] = d[c].fillna('').astype(str)
    for c in ('revisar_antes_de_comprar', 'dato_incompleto'):
        if c not in d.columns:
            d[c] = False
        d[c] = d[c].astype(str).str.lower().isin(['true', '1'])

    d['venta_sem'] = d['demanda_p50_141d'] / (HORIZONTE_DIAS / 7.0)
    venta_dia = d['demanda_p50_141d'] / HORIZONTE_DIAS
    stock_pos = d['stock_actual'].clip(lower=0)
    d['dias_stock'] = np.where(venta_dia > 0, stock_pos / venta_dia.replace(0, np.nan), np.inf)

    comprar = d['accion'] == 'RECOMPRAR'
    d['urgencia'] = np.where(comprar & (d['dias_stock'] <= lead), 'URGENTE',
                             np.where(comprar, 'PRONTO', 'MONITOREAR'))
    d['urgencia_ord'] = d['urgencia'].map({'URGENTE': 0, 'PRONTO': 1, 'MONITOREAR': 2})

    # Confianza del pronóstico: a partir de lo que realmente sabemos de cada producto
    # (ancho del intervalo P10–P90, historia suficiente, datos completos, alertas de sanidad).
    p50 = d['demanda_p50_141d'].clip(lower=1)
    ancho = (d['demanda_p90_141d'] - d['demanda_p10_141d']) / p50
    poca_historia = d['segmento'].str.contains('Lanzamiento|Sin historia', case=False, regex=True)
    baja = d['revisar_antes_de_comprar'] | d['dato_incompleto'] | poca_historia | (ancho > 1.5)
    media = ancho > 0.8
    d['confianza'] = np.where(baja, 'BAJA', np.where(media, 'MEDIA', 'ALTA'))
    d['_ancho'] = ancho
    return d


def confianza_motivo(r):
    if r['revisar_antes_de_comprar']:
        return 'La cantidad parece inflada por una venta puntual fuera de lo común.'
    if r['dato_incompleto']:
        return 'Faltan datos de costo o precio de este producto.'
    if 'Lanzamiento' in r['segmento'] or 'Sin historia' in r['segmento']:
        return 'Es un producto nuevo o con poca historia de ventas — pronóstico incierto.'
    if r['confianza'] == 'BAJA':
        return 'El rango de ventas posibles es muy amplio — pronóstico incierto.'
    if r['confianza'] == 'MEDIA':
        return 'El rango de ventas posibles es moderado.'
    return 'Historia suficiente y rango de ventas estrecho.'


def explicar(r, fecha_datos, lead=LEAD):
    """Explicación en lenguaje natural de por qué PREVÉ recomienda lo que recomienda."""
    venta = r['venta_sem']
    venta_t = f'{venta:.0f}' if venta >= 10 else (f'{venta:.1f}'.rstrip('0').rstrip('.') or '0')
    dias = r['dias_stock']
    sem = None if np.isinf(dias) else dias / 7.0
    cant = int(r['compra_sugerida_unid']) if pd.notna(r['compra_sugerida_unid']) else 0

    if r['accion'] == 'RECOMPRAR':
        stock_t = ('ya no tienes stock' if r['stock_actual'] <= 0
                   else f'tienes stock para {semanas_texto(sem)}')
        txt = (f'Recomiendo comprar {cant:,} unidades porque: vendes ~{venta_t} por semana, '
               f'{stock_t} y el proveedor tarda {lead} días en traer.')
        if r['stock_actual'] <= 0:
            txt += ' Hoy estás en quiebre: cada día sin stock es venta perdida.'
        elif not np.isinf(dias):
            quiebre = fecha_datos + datetime.timedelta(days=float(dias))
            if dias <= lead:
                txt += (f' Si no compras ahora, te quedas sin stock el {fecha_larga(quiebre)}, '
                        f'antes de que llegue el pedido.')
            else:
                txt += (f' Tu stock actual alcanza hasta el {fecha_larga(quiebre)}, pero necesitas cubrir '
                        f'{HORIZONTE_DIAS} días más un colchón de seguridad.')
        return txt

    base = r['motivo_accion'] or 'Sin recomendación especial por ahora.'
    if not np.isinf(dias) and r['stock_actual'] > 0 and r['accion'] in ('REDUCIR', 'LIQUIDAR', 'VIGILAR'):
        base += f' Tu stock alcanza para ~{semanas_texto(sem)} de ventas.'
    return base


# ══════════════════════════════════════════════════════════════════
# Contenedor de datos de una sucursal
# ══════════════════════════════════════════════════════════════════
class Datos:
    def __init__(self, carpeta_salida, sucursal='San José'):
        self.carpeta = carpeta_salida
        self.sucursal = sucursal
        self.recargar()

    # ── carga ──────────────────────────────────────────────
    def recargar(self):
        self.df = None
        self.fecha_datos = None
        self.ciclo = None
        self.ciclo_num = 0
        self.decisiones = {}
        self.comparacion = None
        self.scorecard = None
        self.error_ciclo = None
        carpeta = self.carpeta
        if not carpeta or not os.path.isdir(carpeta):
            return
        csv_path = os.path.join(carpeta, 'recomendacion_compra.csv')
        if os.path.exists(csv_path):
            try:
                raw = pd.read_csv(csv_path, dtype={'sku': str})
                self.fecha_datos = datetime.datetime.fromtimestamp(os.path.getmtime(csv_path))
                self.df = derivar(raw)
            except (OSError, ValueError, pd.errors.ParserError):
                # FIX: antes except Exception silencioso
                logger.exception('No se pudo leer %s', csv_path)
                self.df = None
        # FIX: un ciclo corrupto ya no se reinicia en silencio: se informa a la interfaz
        try:
            self.ciclo = cargar_estado_ciclo(carpeta)
        except CicloCorruptoError as e:
            self.error_ciclo = str(e)
            self.ciclo = None
        self.ciclo_num = (self.ciclo or {}).get('ciclo_numero', 0)
        try:
            self.decisiones = hist.cargar_decisiones(carpeta, self.ciclo_num, self.sucursal)
            # FIX: comparación y scorecard de la sucursal activa
            self.comparacion = hist.comparar_ultimas(carpeta, self.sucursal)
            self.scorecard = hist.ultimo_scorecard(carpeta, self.sucursal)
        except sqlite3.Error:
            logger.exception('No se pudo leer el historial de %s', carpeta)

    @property
    def tiene_datos(self):
        return self.df is not None and len(self.df) > 0

    @property
    def fecha_ref(self):
        return self.fecha_datos or datetime.datetime.now()

    # ── decisiones del usuario ─────────────────────────────
    def estado(self, sku):
        """'pendiente' | 'comprado' | 'pospuesto' | 'descartado' (un pospuesto vencido vuelve a pendiente)."""
        d = self.decisiones.get(str(sku))
        if not d:
            return 'pendiente'
        if d['estado'] == 'pospuesto' and d.get('hasta'):
            try:
                if datetime.date.fromisoformat(d['hasta']) <= datetime.date.today():
                    return 'pendiente'
            except ValueError:
                pass
        return d['estado']

    def decidir(self, sku, estado, razon='', dias_posponer=7):
        hasta = None
        if estado == 'pospuesto':
            hasta = (datetime.date.today() + datetime.timedelta(days=dias_posponer)).isoformat()
        hist.guardar_decision(self.carpeta, sku, self.ciclo_num, estado, razon, hasta, self.sucursal)
        self.decisiones[str(sku)] = {'estado': estado, 'razon': razon, 'hasta': hasta,
                                     'fecha': datetime.datetime.now().isoformat()}

    def deshacer(self, sku):
        hist.borrar_decision(self.carpeta, sku, self.ciclo_num, self.sucursal)
        self.decisiones.pop(str(sku), None)

    # ── vistas de la tabla ─────────────────────────────────
    def recompras(self):
        return self.df[self.df['accion'] == 'RECOMPRAR'] if self.tiene_datos else pd.DataFrame()

    def pendientes(self):
        r = self.recompras()
        if r.empty:
            return r
        return r[r['sku'].map(self.estado) == 'pendiente']

    def top_impacto(self, n=20):
        p = self.pendientes()
        return p.sort_values('costo_total_compra_pen', ascending=False).head(n)

    def previa_unidades(self, sku):
        """Unidades recomendadas en el análisis anterior (None si no existía)."""
        if not self.comparacion or not self.comparacion.get('previa'):
            return None
        return self.comparacion['previa_por_sku'].get(str(sku))

    # ── ciclo ──────────────────────────────────────────────
    def info_ciclo(self):
        """Estado del ciclo calculado con la fecha de hoy."""
        if not self.ciclo or 'ciclo_inicio' not in self.ciclo:
            return None
        inicio = datetime.date.fromisoformat(self.ciclo['ciclo_inicio'])
        hoy = datetime.date.today()
        dia = (hoy - inicio).days + 1
        limite = inicio + datetime.timedelta(days=CICLO_INTERNACIONAL_DIAS - 1)
        return {
            'numero': self.ciclo.get('ciclo_numero', 1),
            'dia': max(1, min(dia, CICLO_INTERNACIONAL_DIAS)),
            'dias_totales': CICLO_INTERNACIONAL_DIAS,
            'dias_restantes': max(0, (limite - hoy).days),
            'limite': limite,
            'vencido': hoy > limite,
        }

    # ── KPIs (todos derivados de columnas reales) ──────────
    def kpis(self):
        if not self.tiene_datos:
            return None
        d = self.df
        costo = d['costo_puesto_almacen_pen']
        con_costo = costo.notna()

        valor_stock = (d['stock_actual'].clip(lower=0) * costo)[con_costo].sum()
        consumo_anual = (d['demanda_p50_141d'].clip(lower=0) * costo)[con_costo].sum() * (365.0 / HORIZONTE_DIAS)
        rotacion = consumo_anual / valor_stock if valor_stock > 0 else None

        con_demanda = d['demanda_p50_141d'] > 0
        riesgo = (con_demanda & (d['dias_stock'] <= LEAD)).sum()
        pct_riesgo = riesgo / con_demanda.sum() * 100 if con_demanda.sum() > 0 else None

        liq = d[(d['accion'] == 'LIQUIDAR') & con_costo]
        capital_muerto = (liq['stock_actual'].clip(lower=0) * liq['costo_puesto_almacen_pen']).sum()

        # Margen esperado: unidades que podrías vender (limitadas por stock + compra) × margen unitario sin IGV
        precio_neto = d['precio_regular_pen'] / 1.18
        ok = precio_neto.notna() & con_costo
        vendibles = np.minimum(d['demanda_p50_141d'].clip(lower=0),
                               d['stock_actual'].clip(lower=0) + d['compra_sugerida_unid'].fillna(0))
        margen = (vendibles * (precio_neto - costo))[ok].sum() if ok.any() else None

        rec = self.recompras()
        pend = self.pendientes()
        return {
            'rotacion': rotacion,
            'pct_riesgo': pct_riesgo,
            'riesgo_n': int(riesgo),
            'capital_muerto': capital_muerto,
            'margen_esperado': margen,
            'recomprar_n': len(rec),
            'inversion_total': rec['costo_total_compra_pen'].sum() if len(rec) else 0.0,
            'pendientes_n': len(pend),
            'inversion_pendiente': pend['costo_total_compra_pen'].sum() if len(pend) else 0.0,
            'urgentes_n': int((pend['urgencia'] == 'URGENTE').sum()) if len(pend) else 0,
            'total': len(d),
            'revisar_n': int(d['revisar_antes_de_comprar'].sum()),
        }

    def precision_historica(self):
        """100 − WMAPE de la última validación real, o None si todavía no hubo validación."""
        if self.scorecard and self.scorecard.get('wmape') is not None:
            return max(0.0, 100.0 - float(self.scorecard['wmape']))
        return None

    # ── búsqueda global ────────────────────────────────────
    def buscar(self, texto, limite=6):
        texto = (texto or '').strip().lower()
        res = {'recomendacion': [], 'historial': [], 'traslados': []}
        if len(texto) < 2:
            return res
        if self.tiene_datos:
            d = self.df
            hay = (d['sku'].str.lower() + ' ' + d['nombre'].str.lower() + ' ' + d['categoria'].str.lower() + ' '
                   + d['proveedor_principal'].str.lower())
            sub = d[hay.str.contains(texto, regex=False)]
            sub = sub.sort_values(['urgencia_ord', 'costo_total_compra_pen'], ascending=[True, False]).head(limite)
            res['recomendacion'] = sub.to_dict('records')
        try:
            vistos = {r['sku'] for r in res['recomendacion']}
            res['historial'] = [h for h in hist.buscar_en_historial(self.carpeta, texto, limite + 3,
                                                                    sucursal=self.sucursal)
                                if h['sku'] not in vistos][:limite]
        except sqlite3.Error:
            # FIX: antes except Exception silencioso
            logger.exception('Búsqueda en el historial falló')
        for base in (self.carpeta, os.path.dirname(self.carpeta or '')):
            ruta = os.path.join(base or '', 'PREVE_Traslados_Sugeridos.csv')
            if os.path.exists(ruta):
                try:
                    t = pd.read_csv(ruta, dtype={'sku': str})
                    m = (t['sku'].str.lower() + ' ' + t['nombre'].fillna('').str.lower()).str.contains(
                        texto, regex=False)
                    res['traslados'] = t[m].head(limite).to_dict('records')
                except (OSError, KeyError, ValueError, pd.errors.ParserError):
                    # FIX: antes except Exception silencioso
                    logger.exception('No se pudo leer %s', ruta)
                break
        return res
