# -*- coding: utf-8 -*-
"""
PREVÉ — Modo Simulación ("What-If").

Permite al usuario responder preguntas como:
  - ¿Qué pasa si la demanda sube 20%?
  - ¿Cuánto necesito comprar si el lead time sube a 50 días?
  - ¿Cuál sería la inversión para cubrir solo 90 días en vez de 141?
  - ¿Qué pasa si el stock es diferente al que dice el sistema?

La simulación NO re-entrena modelos: toma la recomendación ya calculada
y aplica ajustes paramétricos sobre ella.
"""
import copy
import pandas as pd
import numpy as np


def simular(recomendacion, escenario, log=print):
    """Aplica un escenario what-if a una recomendación existente.

    Args:
        recomendacion: DataFrame con la recomendación original.
        escenario: dict con los parámetros del escenario. Claves aceptadas:
            - factor_demanda: float, multiplicador de la demanda (ej: 1.2 = +20%).
            - lead_time_dias: int, nuevo lead time en días (sobreescribe el original).
            - horizonte_dias: int, nuevo horizonte total en días.
            - factor_costo: float, multiplicador de costos unitarios.
            - stock_override: dict {sku: nuevo_stock}, sobreescribe stock para SKUs específicos.
            - filtro_categorias: list[str], solo simular estas categorías.
            - filtro_proveedores: list[str], solo simular estos proveedores.
            - nivel_servicio_override: dict {'A': z, 'B': z, 'C': z}, nuevos niveles Z.
        log: función de progreso.

    Returns:
        dict con:
            - recomendacion: DataFrame simulado.
            - comparacion: DataFrame con original vs simulado side by side.
            - resumen: dict con métricas agregadas del escenario.
    """
    log(f'Simulación: aplicando escenario {_describir_escenario(escenario)}')
    df = recomendacion.copy()

    # ── Filtros ──
    if 'filtro_categorias' in escenario:
        cats = [c.upper().strip() for c in escenario['filtro_categorias']]
        if 'categoria' in df.columns:
            df = df[df['categoria'].str.upper().str.strip().isin(cats)].copy()
            log(f'  Filtrado a {len(df)} SKUs en categorías: {cats}')

    if 'filtro_proveedores' in escenario:
        provs = [p.strip() for p in escenario['filtro_proveedores']]
        if 'proveedor_principal' in df.columns:
            df = df[df['proveedor_principal'].isin(provs)].copy()
            log(f'  Filtrado a {len(df)} SKUs de proveedores: {provs}')

    # ── Guardar valores originales ──
    df['_orig_demanda_p50'] = df['demanda_p50_141d'].copy()
    df['_orig_stock'] = df['stock_actual'].copy()
    df['_orig_compra'] = df['compra_sugerida_unid'].copy()
    df['_orig_inversion'] = df['costo_total_compra_pen'].copy()
    df['_orig_rop'] = df['rop'].copy()

    # ── Ajustar demanda ──
    factor_demanda = escenario.get('factor_demanda', 1.0)
    if factor_demanda != 1.0:
        for col in ['demanda_p50_141d', 'demanda_p10_141d', 'demanda_p90_141d']:
            if col in df.columns:
                df[col] = df[col] * factor_demanda
        log(f'  Demanda ajustada: x{factor_demanda:.2f}')

    # ── Ajustar horizonte ──
    horizonte_dias = escenario.get('horizonte_dias')
    if horizonte_dias:
        ratio = horizonte_dias / 141.0
        for col in ['demanda_p50_141d', 'demanda_p10_141d', 'demanda_p90_141d']:
            if col in df.columns:
                # Si ya se aplicó factor_demanda, esto lo reescala al nuevo horizonte
                df[col] = (df['_orig_demanda_p50'] * factor_demanda * ratio
                           if col == 'demanda_p50_141d' else df[col] * ratio)
        log(f'  Horizonte ajustado: {horizonte_dias} días (ratio {ratio:.2f})')

    # ── Ajustar lead time → afecta stock de seguridad y ROP ──
    lead_time = escenario.get('lead_time_dias')
    if lead_time and 'stock_seguridad' in df.columns:
        # Recalcular stock de seguridad con nuevo lead time
        # SS = Z * σ_semanal * √(LT_semanas + R_semanas)
        # Simplificación: escalar SS proporcionalmente a la raíz del ratio de lead time
        lt_orig = 36.0  # días originales
        ratio_lt = np.sqrt(lead_time / lt_orig)
        df['stock_seguridad'] = df['stock_seguridad'] * ratio_lt
        log(f'  Lead time ajustado: {lead_time} días (SS escalado x{ratio_lt:.2f})')

    # ── Override de stock ──
    if 'stock_override' in escenario:
        overrides = escenario['stock_override']
        for sku, nuevo_stock in overrides.items():
            mask = df['sku'] == sku
            if mask.any():
                df.loc[mask, 'stock_actual'] = nuevo_stock
        log(f'  Stock manual para {len(overrides)} SKU(s)')

    # ── Niveles de servicio ──
    z_override = escenario.get('nivel_servicio_override')
    if z_override and 'stock_seguridad' in df.columns and 'clase_abc' in df.columns:
        from preve_pipeline import Z_NIVEL_SERVICIO
        for clase, z_new in z_override.items():
            z_old = Z_NIVEL_SERVICIO.get(clase, 1.0)
            if z_old > 0:
                mask = df['clase_abc'] == clase
                df.loc[mask, 'stock_seguridad'] = df.loc[mask, 'stock_seguridad'] * (z_new / z_old)
        log(f'  Niveles de servicio ajustados: {z_override}')

    # ── Recalcular ROP y compra sugerida ──
    if 'stock_seguridad' in df.columns:
        df['rop'] = df['demanda_p50_141d'] + df['stock_seguridad']
    df['compra_sugerida_unid'] = (df['rop'] - df['stock_actual']).clip(lower=0).round(0)

    # ── Ajustar costos ──
    factor_costo = escenario.get('factor_costo', 1.0)
    if factor_costo != 1.0 and 'costo_puesto_almacen_pen' in df.columns:
        df['costo_puesto_almacen_pen'] = df['costo_puesto_almacen_pen'] * factor_costo
        log(f'  Costos ajustados: x{factor_costo:.2f}')

    if 'costo_puesto_almacen_pen' in df.columns:
        df['costo_total_compra_pen'] = df['compra_sugerida_unid'] * df['costo_puesto_almacen_pen']

    # ── Reclasificar acciones ──
    if 'accion' in df.columns:
        df['accion'] = df.apply(_reclasificar_accion, axis=1)

    # ── Comparación ──
    comp = pd.DataFrame({
        'sku': df['sku'],
        'nombre': df.get('nombre', ''),
        'stock_actual_orig': df['_orig_stock'],
        'stock_actual_sim': df['stock_actual'],
        'demanda_orig': df['_orig_demanda_p50'],
        'demanda_sim': df['demanda_p50_141d'],
        'rop_orig': df['_orig_rop'],
        'rop_sim': df['rop'],
        'compra_orig': df['_orig_compra'],
        'compra_sim': df['compra_sugerida_unid'],
        'inversion_orig': df['_orig_inversion'],
        'inversion_sim': df['costo_total_compra_pen'] if 'costo_total_compra_pen' in df.columns else 0,
    })
    comp['delta_compra'] = comp['compra_sim'] - comp['compra_orig']
    comp['delta_inversion'] = comp['inversion_sim'] - comp['inversion_orig']

    # Limpiar columnas internas
    df.drop(columns=[c for c in df.columns if c.startswith('_orig_')], inplace=True)

    # ── Resumen ──
    resumen = {
        'escenario': _describir_escenario(escenario),
        'skus_simulados': len(df),
        'compra_total_orig': float(comp['compra_orig'].sum()),
        'compra_total_sim': float(comp['compra_sim'].sum()),
        'inversion_orig': float(comp['inversion_orig'].sum()),
        'inversion_sim': float(comp['inversion_sim'].sum()),
        'delta_inversion': float(comp['delta_inversion'].sum()),
        'delta_inversion_pct': float(
            comp['delta_inversion'].sum() / comp['inversion_orig'].sum() * 100
            if comp['inversion_orig'].sum() > 0 else 0
        ),
        'skus_compra_aumenta': int((comp['delta_compra'] > 0).sum()),
        'skus_compra_disminuye': int((comp['delta_compra'] < 0).sum()),
        'skus_sin_cambio': int((comp['delta_compra'] == 0).sum()),
    }

    log(f'  Resultado: inversión {resumen["inversion_orig"]:,.0f} → {resumen["inversion_sim"]:,.0f} '
        f'({resumen["delta_inversion_pct"]:+.1f}%)')

    return {
        'recomendacion': df,
        'comparacion': comp,
        'resumen': resumen,
    }


def _reclasificar_accion(row):
    """Versión simplificada de la clasificación de acciones para simulación."""
    stock = row.get('stock_actual', 0)
    demanda = row.get('demanda_p50_141d', 0)
    rop = row.get('rop', 0)
    compra = row.get('compra_sugerida_unid', 0)
    margen = row.get('margen_pct', 0)
    tendencia = row.get('tendencia_venta', 0)

    if demanda <= 0 and stock > 0:
        return 'LIQUIDAR'
    if compra > 0 and margen < -0.05:
        return 'NEGOCIAR'
    if compra > 0:
        return 'RECOMPRAR'
    if stock > demanda * 2 and demanda > 0:
        if tendencia < -0.5:
            return 'LIQUIDAR'
        return 'REDUCIR'
    if tendencia < -1.0:
        return 'PUBLICITAR'
    return 'VIGILAR'


def _describir_escenario(escenario):
    """Genera una descripción legible del escenario."""
    partes = []
    if escenario.get('factor_demanda', 1.0) != 1.0:
        pct = (escenario['factor_demanda'] - 1) * 100
        partes.append(f'demanda {pct:+.0f}%')
    if escenario.get('horizonte_dias'):
        partes.append(f'horizonte {escenario["horizonte_dias"]}d')
    if escenario.get('lead_time_dias'):
        partes.append(f'lead time {escenario["lead_time_dias"]}d')
    if escenario.get('factor_costo', 1.0) != 1.0:
        pct = (escenario['factor_costo'] - 1) * 100
        partes.append(f'costos {pct:+.0f}%')
    if escenario.get('stock_override'):
        partes.append(f'stock manual ({len(escenario["stock_override"])} SKUs)')
    if escenario.get('filtro_categorias'):
        partes.append(f'cats: {escenario["filtro_categorias"]}')
    if escenario.get('filtro_proveedores'):
        partes.append(f'provs: {escenario["filtro_proveedores"]}')
    if escenario.get('nivel_servicio_override'):
        partes.append(f'niveles servicio ajustados')
    return ' | '.join(partes) if partes else 'sin cambios'


def generar_excel_simulacion(resultado, carpeta_salida, nombre='PREVE_Simulacion.xlsx', log=print):
    """Genera un Excel con la comparación original vs simulado."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    import os

    ORANGE = 'FF6B00'
    comp = resultado['comparacion']
    resumen = resultado['resumen']

    wb = Workbook()

    # Hoja resumen
    ws = wb.active
    ws.title = 'Resumen simulación'
    ws.merge_cells('A1:D1')
    ws['A1'] = f'PREVÉ · Simulación What-If'
    ws['A1'].font = Font(size=16, bold=True, color='FFFFFF')
    ws['A1'].fill = PatternFill('solid', fgColor=ORANGE)
    ws['A2'] = f'Escenario: {resumen["escenario"]}'
    ws['A2'].font = Font(size=11, italic=True)

    metricas = [
        ('SKUs simulados', resumen['skus_simulados']),
        ('', ''),
        ('Compra total (unidades) — original', f'{resumen["compra_total_orig"]:,.0f}'),
        ('Compra total (unidades) — simulado', f'{resumen["compra_total_sim"]:,.0f}'),
        ('', ''),
        ('Inversión original (S/)', f'{resumen["inversion_orig"]:,.2f}'),
        ('Inversión simulada (S/)', f'{resumen["inversion_sim"]:,.2f}'),
        ('Delta inversión (S/)', f'{resumen["delta_inversion"]:+,.2f}'),
        ('Delta inversión (%)', f'{resumen["delta_inversion_pct"]:+.1f}%'),
        ('', ''),
        ('SKUs donde compra aumenta', resumen['skus_compra_aumenta']),
        ('SKUs donde compra disminuye', resumen['skus_compra_disminuye']),
        ('SKUs sin cambio', resumen['skus_sin_cambio']),
    ]
    for i, (label, val) in enumerate(metricas, start=4):
        ws.cell(row=i, column=1, value=label).font = Font(bold=True)
        ws.cell(row=i, column=2, value=val)
    ws.column_dimensions['A'].width = 45
    ws.column_dimensions['B'].width = 25

    # Hoja detalle
    ws2 = wb.create_sheet('Detalle por SKU')
    headers = ['SKU', 'Producto', 'Stock orig', 'Stock sim',
               'Demanda orig', 'Demanda sim', 'ROP orig', 'ROP sim',
               'Compra orig', 'Compra sim', 'Δ Compra',
               'Inversión orig', 'Inversión sim', 'Δ Inversión']
    for j, h in enumerate(headers, 1):
        c = ws2.cell(row=1, column=j, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=ORANGE)
        c.alignment = Alignment(horizontal='center', wrap_text=True)

    for i, row in enumerate(comp.itertuples(index=False), start=2):
        for j, val in enumerate(row, start=1):
            cell = ws2.cell(row=i, column=j, value=val)
            # Colorear deltas positivos en rojo, negativos en verde
            if j in (11, 14):  # delta_compra, delta_inversion
                try:
                    v = float(val)
                    if v > 0:
                        cell.font = Font(color='CC0000')
                    elif v < 0:
                        cell.font = Font(color='006100')
                except (ValueError, TypeError):
                    pass

    for j in range(1, len(headers) + 1):
        ws2.column_dimensions[get_column_letter(j)].width = max(14, min(30, len(headers[j-1]) + 4))
    ws2.freeze_panes = 'A2'

    path = os.path.join(carpeta_salida, nombre)
    wb.save(path)
    log(f'Excel de simulación: {path}')
    return path
