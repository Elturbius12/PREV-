# -*- coding: utf-8 -*-
"""PREVÉ — Traducción de términos técnicos a lenguaje de negocio.

Centraliza las traducciones para que la UI, los reportes Excel y el dashboard
muestren lenguaje accesible al usuario no técnico.
"""

# ── Nombres de modelos → descripción en lenguaje simple ──
NOMBRE_MODELO = {
    'GBM global (Poisson)': 'Modelo principal',
    'GBM sin disponibilidad': 'Modelo sin corrección de stock',
    'LightGBM': 'Modelo principal',
    'Croston-TSB': 'Modelo para productos intermitentes',
    'CatBoost': 'Modelo para productos nuevos',
    'Naive': 'Repetir última semana',
    'SMA(4)': 'Promedio últimas 4 semanas',
    'SMA(8)': 'Promedio últimas 8 semanas',
    'SMA(13)': 'Promedio últimas 13 semanas',
    'SES': 'Suavizado exponencial',
    'Holt amortiguado': 'Tendencia suavizada',
    'Croston clásico': 'Modelo clásico intermitente',
    'SBA': 'Modelo Syntetos-Boylan',
    'TSB NegBin': 'Modelo intermitente avanzado',
    'Pipeline (producción)': 'Modelo de producción actual',
}

# ── WMAPE → interpretación en texto ──
def interpretar_wmape(wmape):
    """Convierte un WMAPE numérico a una etiqueta de negocio con semáforo."""
    if wmape is None or wmape != wmape:
        return 'Sin datos', 'gris', 'No hay suficientes datos para medir la precisión.'
    if wmape <= 25:
        return 'Buena precisión', 'verde', f'Margen de error estimado: {wmape:.0f}%. El sistema acierta bien.'
    if wmape <= 50:
        return 'Precisión moderada', 'amarillo', (f'Margen de error estimado: {wmape:.0f}%. '
                                                    'Revisar manualmente los productos más importantes.')
    return 'Precisión baja', 'rojo', (f'Margen de error estimado: {wmape:.0f}%. '
                                       'No confiar solo en el sistema; verificar con el equipo de ventas.')


def interpretar_confianza(confianza):
    """Traduce la etiqueta de confianza a texto para el usuario."""
    traducciones = {
        'Alta': ('verde', 'El pronóstico es confiable para este producto.'),
        'Media': ('amarillo', 'El pronóstico tiene precisión moderada; revisar antes de comprar grandes cantidades.'),
        'Baja': ('rojo', 'El pronóstico no es confiable; verificar manualmente con el historial de ventas.'),
    }
    return traducciones.get(confianza, ('gris', 'Sin información de confianza.'))


# ── Nombres de métricas → lenguaje de negocio ──
NOMBRE_METRICA = {
    'wmape': 'Margen de error estimado',
    'mae': 'Error promedio (unidades)',
    'rmse': 'Error típico (unidades)',
    'bias': 'Sesgo (+ sobreestima, − subestima)',
    'mape': 'Error porcentual promedio',
}

# ── Segmentos ADI/CV² → lenguaje de negocio ──
NOMBRE_SEGMENTO = {
    'Regular / alta rotación': 'Venta frecuente y estable',
    'Intermitente': 'Venta poco frecuente o irregular',
    'Lanzamiento nuevo (<13 sem.)': 'Producto nuevo (menos de 3 meses)',
    'Sin historia suficiente': 'Sin datos suficientes para pronosticar',
    'Suave': 'Venta frecuente y estable',
    'Errático': 'Venta frecuente pero muy variable',
    'Lumpy': 'Venta esporádica y en cantidades irregulares',
}

# ── Acciones → explicación breve ──
EXPLICACION_ACCION = {
    'RECOMPRAR': 'Se recomienda comprar: el stock está bajo el nivel necesario para cubrir el próximo ciclo.',
    'LIQUIDAR': 'Considerar liquidar: el producto tiene stock pero casi no se vende.',
    'REDUCIR': 'No comprar por ahora: el stock actual es suficiente para el horizonte.',
    'DESCONTINUAR': 'Evaluar si seguir vendiendo: lleva mucho tiempo sin ventas.',
    'REVISAR': 'Requiere revisión manual: los datos no son suficientes para una recomendación automática.',
    'NUEVO': 'Producto nuevo: aún no hay historial suficiente para pronosticar con confianza.',
}


def traducir_modelo(nombre_tecnico, modo_avanzado=False):
    """Devuelve el nombre del modelo en lenguaje de negocio (o el técnico si está en modo avanzado)."""
    if modo_avanzado:
        return nombre_tecnico
    return NOMBRE_MODELO.get(nombre_tecnico, nombre_tecnico)


def traducir_segmento(segmento_tecnico, modo_avanzado=False):
    """Devuelve el nombre del segmento en lenguaje de negocio."""
    if modo_avanzado:
        return segmento_tecnico
    return NOMBRE_SEGMENTO.get(segmento_tecnico, segmento_tecnico)


def nota_accion(accion, motivo='', nota_confianza=''):
    """Genera la nota explicativa para una acción de compra."""
    base = EXPLICACION_ACCION.get(accion, motivo or 'Sin información adicional.')
    if nota_confianza:
        base += f' ⚠ {nota_confianza}'
    return base
