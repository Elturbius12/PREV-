# -*- coding: utf-8 -*-
"""PREVÉ — Traducción de términos técnicos a lenguaje de negocio.

Centraliza las traducciones para que la interfaz, los Excel y el dashboard muestren lenguaje accesible al
usuario no técnico: nunca "WMAPE", "ADI/CV²" ni nombres de modelo sin traducir (salvo que se active
`mostrar_metricas_avanzadas`). Ninguna función de este módulo modifica los DataFrames que recibe.
"""
import pandas as pd

import preve_modelos_baseline as mb

# ── Nombres de modelos → descripción en lenguaje simple ──
# Claves: los nombres canónicos de preve_modelos_baseline + las etiquetas internas del pipeline de producción.
NOMBRE_MODELO = {
    mb.MODELO_PRODUCCION: 'Método actual de PREVÉ',
    mb.MODELO_GBM: 'Método principal (aprende de todos los productos)',
    mb.MODELO_GBM_SIN_DISP: 'Método principal sin corregir quiebres de stock',
    mb.MODELO_CATBOOST: 'Estimación por categoría y precio',
    mb.MODELO_CROSTON_TSB: 'Método para ventas esporádicas',
    mb.MODELO_NAIVE: 'Repetir la última semana',
    mb.MODELO_PROMEDIO_MOVIL: 'Promedio de las últimas semanas',
    mb.MODELO_SES: 'Promedio que da más peso a lo reciente',
    mb.MODELO_HOLT: 'Promedio con tendencia suavizada',
    mb.MODELO_CROSTON: 'Método clásico para ventas esporádicas',
    mb.MODELO_SBA: 'Método corregido para ventas esporádicas',
    mb.MODELO_TSB_NEGBIN: 'Método para ventas esporádicas y en lotes',
    # etiquetas internas que escribe el pipeline en segmentacion['modelo_asignado']
    'GBM global (Poisson)': 'Método principal (aprende de todos los productos)',
    'LightGBM': 'Método principal (aprende de todos los productos)',
}
assert set(mb.MODELOS_COMPARABLES) <= set(NOMBRE_MODELO)

# ── Confianza: umbrales ÚNICOS del margen de error (WMAPE, %) ──
UMBRAL_CONFIANZA_ALTA = 30.0      # margen de error <= 30 % → Alta
UMBRAL_CONFIANZA_MEDIA = 60.0     # <= 60 % → Media; más → Baja
CONFIANZAS = ('Alta', 'Media', 'Baja', 'Sin dato')
COLOR_CONFIANZA = {'Alta': 'verde', 'Media': 'amarillo', 'Baja': 'rojo', 'Sin dato': 'gris'}

ETIQUETA_ERROR = 'Margen de error estimado'


def _es_nan(x):
    return x is None or (isinstance(x, float) and x != x)


def nivel_confianza(wmape):
    """'Alta' | 'Media' | 'Baja' según el margen de error; 'Sin dato' si no se pudo medir."""
    if _es_nan(wmape):
        return 'Sin dato'
    if wmape <= UMBRAL_CONFIANZA_ALTA:
        return 'Alta'
    if wmape <= UMBRAL_CONFIANZA_MEDIA:
        return 'Media'
    return 'Baja'


def texto_precision(wmape):
    """'Precisión estimada: 35% de margen de error' (o la razón por la que no se pudo medir)."""
    if _es_nan(wmape):
        return 'Precisión estimada: sin ventas en el período evaluado para medirla'
    return f'Precisión estimada: {wmape:.0f}% de margen de error'


def interpretar_wmape(wmape):
    """(etiqueta, color, explicación) en lenguaje de negocio para un margen de error (WMAPE)."""
    nivel = nivel_confianza(wmape)
    if nivel == 'Sin dato':
        return 'Sin datos', 'gris', 'No hay suficientes ventas para medir la precisión.'
    textos = {
        'Alta': ('Buena precisión', 'El sistema acierta bien.'),
        'Media': ('Precisión moderada', 'Revisar manualmente los productos más importantes.'),
        'Baja': ('Precisión baja', 'No confiar solo en el sistema; verificar con el equipo de ventas.'),
    }
    etiqueta, consejo = textos[nivel]
    return etiqueta, COLOR_CONFIANZA[nivel], f'{ETIQUETA_ERROR}: {wmape:.0f}%. {consejo}'


def interpretar_confianza(confianza):
    """(color, texto) para una etiqueta de confianza."""
    textos = {
        'Alta': 'El pronóstico es confiable para este producto.',
        'Media': 'El pronóstico tiene precisión moderada; revisar antes de comprar grandes cantidades.',
        'Baja': 'El pronóstico no es confiable; verificar manualmente con el historial de ventas.',
        'Sin dato': 'No hubo ventas en el período evaluado: no se pudo medir la precisión.',
    }
    return COLOR_CONFIANZA.get(confianza, 'gris'), textos.get(confianza, 'Sin información de confianza.')


# ── Métricas → lenguaje de negocio ──
NOMBRE_METRICA = {
    'wmape': ETIQUETA_ERROR,
    'mae': 'Error promedio (unidades)',
    'rmse': 'Error típico (unidades)',
    'bias': 'Sesgo (+ sobreestima, − subestima)',
    'mape': 'Error porcentual promedio',
}

# ── Segmentos → lenguaje de negocio (pipeline + clasificación de analisis_censura) ──
NOMBRE_SEGMENTO = {
    'Regular / alta rotación': 'Venta frecuente y estable',
    'Intermitente': 'Venta poco frecuente o irregular',
    'Lanzamiento nuevo (<13 sem.)': 'Producto nuevo (menos de 3 meses)',
    'Sin historia suficiente': 'Sin datos suficientes para pronosticar',
    'Suave': 'Venta frecuente y estable',
    'Errático': 'Venta frecuente pero muy variable',
    'Lumpy': 'Venta esporádica y en cantidades irregulares',
}

# ── Acciones → por qué se recomienda (todas las de seeds/plantillas_categorias.py y _clasificar_accion) ──
EXPLICACION_ACCION = {
    'RECOMPRAR': 'Comprar: el stock no alcanza para cubrir la venta esperada hasta que llegue el próximo pedido.',
    'REABASTECER': 'Comprar: el stock no alcanza para cubrir la venta esperada hasta que llegue el próximo pedido.',
    'REPONER': 'Comprar: el stock no alcanza para cubrir la venta esperada hasta que llegue el próximo pedido.',
    'REVISAR_COMPRA': 'Revisar antes de comprar: se agotará antes del próximo pedido, pero su margen no es '
                      'positivo o no se conoce.',
    'PRODUCIR_MAS': 'Producir más: se vende más de lo previsto y queda poco stock.',
    'MANTENER': 'No hace falta ninguna acción: el stock alcanza para la venta esperada.',
    'REDUCIR': 'No comprar por ahora: hay más stock del necesario; dejar que baje con las ventas.',
    'LIQUIDAR': 'Considerar liquidar: hay stock para mucho tiempo y el producto casi no rota.',
    'DESCONTINUAR': 'Evaluar dejar de venderlo: lleva mucho tiempo sin ventas.',
    'NEGOCIAR': 'Negociar con el proveedor: se vende mucho pero deja poco margen.',
    'PUBLICITAR': 'Promocionarlo: tiene buen margen pero se vende menos de lo esperado.',
    'PROMOCIONAR': 'Promocionarlo: tiene buen margen pero se vende menos de lo esperado.',
    'SUSTITUIR': 'Buscar un reemplazo: deja muy poco margen y sus ventas van en baja.',
    'TRANSFERIR_SUCURSAL': 'Trasladar parte a otra sucursal: aquí sobra stock de un producto que sí se vende.',
    'VIGILAR': 'Producto nuevo o con poca historia: comprar lo mínimo y observar cómo se vende.',
}

# ── Variables del modelo principal (preve_modelo_gbm.FEATURES) → descripción ──
NOMBRE_VARIABLE = {
    's1': 'Ventas de la última semana', 's2': 'Ventas promedio de las últimas 2 semanas',
    's4': 'Ventas promedio de las últimas 4 semanas', 's8': 'Ventas promedio de las últimas 8 semanas',
    's13': 'Ventas promedio de las últimas 13 semanas', 's26': 'Ventas promedio de las últimas 26 semanas',
    's52': 'Ventas promedio de las últimas 52 semanas',
    'nz4': 'Semanas con venta (últimas 4)', 'nz13': 'Semanas con venta (últimas 13)',
    'nz26': 'Semanas con venta (últimas 26)',
    'av4': 'Días con stock (últimas 4 semanas)', 'av13': 'Días con stock (últimas 13 semanas)',
    'av26': 'Días con stock (últimas 26 semanas)',
    'since_last': 'Semanas desde la última venta', 'age': 'Antigüedad del producto (semanas)',
    'stock': 'Stock al inicio de la semana', 'trend': 'Tendencia reciente (2 vs. 8 semanas)',
}


def traducir_modelo(nombre_tecnico, modo_avanzado=False):
    """Nombre del modelo en lenguaje de negocio (o el técnico si está en modo avanzado)."""
    if modo_avanzado or _es_nan(nombre_tecnico):
        return nombre_tecnico
    w = mb.ventana_promedio_movil(nombre_tecnico)
    if w is not None:
        return f'Promedio de las últimas {w} semanas'
    return NOMBRE_MODELO.get(nombre_tecnico, nombre_tecnico)


def traducir_segmento(segmento_tecnico, modo_avanzado=False):
    """Nombre del segmento en lenguaje de negocio."""
    if modo_avanzado or _es_nan(segmento_tecnico):
        return segmento_tecnico
    return NOMBRE_SEGMENTO.get(segmento_tecnico, segmento_tecnico)


def traducir_variable(variable):
    return NOMBRE_VARIABLE.get(variable, variable)


def nota_accion(accion, motivo='', nota_confianza=''):
    """Por qué se recomienda la acción (+ aviso de menor confianza). Acción desconocida → `motivo_accion`."""
    accion = '' if _es_nan(accion) else str(accion)
    motivo = '' if _es_nan(motivo) else str(motivo)
    base = EXPLICACION_ACCION.get(accion) or motivo or 'Sin información adicional.'
    if not _es_nan(nota_confianza) and str(nota_confianza):
        base += f' ⚠ {nota_confianza}'
    return base


def agregar_columnas_negocio(df, modo_avanzado=False):
    """COPIA de `df` con modelo_negocio, segmento_negocio y nota_producto (no modifica el original)."""
    out = df.copy()
    if 'modelo_asignado' in out.columns:
        out['modelo_negocio'] = [traducir_modelo(x, modo_avanzado) for x in out['modelo_asignado']]
    if 'segmento' in out.columns:
        out['segmento_negocio'] = [traducir_segmento(x, modo_avanzado) for x in out['segmento']]
    n = len(out)
    acc = out['accion'] if 'accion' in out.columns else pd.Series([''] * n, index=out.index)
    mot = out['motivo_accion'] if 'motivo_accion' in out.columns else pd.Series([''] * n, index=out.index)
    conf = out['nota_confianza'] if 'nota_confianza' in out.columns else pd.Series([''] * n, index=out.index)
    out['nota_producto'] = [nota_accion(a, m, c) for a, m, c in zip(acc, mot, conf)]
    return out
