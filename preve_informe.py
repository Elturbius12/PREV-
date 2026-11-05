# -*- coding: utf-8 -*-
"""
PREVÉ — Informe para gerencia (Word + PDF).

Convierte la última recomendación en un informe de lectura rápida: qué comprar,
cuánto cuesta, qué es urgente, qué hay que corregir en el inventario y qué
tan confiable es el pronóstico. Todo el texto se arma con cifras reales del
análisis; si un dato no existe, no se menciona (nunca se inventa).

El contenido se describe una sola vez como una lista de bloques y dos
"renderizadores" lo escriben en .docx y en .pdf.
"""
import os
import shutil
import tempfile
import datetime
import numpy as np
import pandas as pd

import preve_datos as dt
from preve_datos import fmt_num, fmt_soles, fecha_larga
# FIX: safe_int en vez de int() donde el valor puede ser NaN (antes ValueError abortaba el informe)
from preve_utils import safe_int, get_logger

logger = get_logger('informe')

VERDE = '#2D7D5F'
VERDE_OSC = '#1A2B23'
GRIS = '#5E6F66'
ROJO = '#B4453A'
AMBAR = '#C78A2E'

NOMBRE_DOCX = 'PREVE_Informe_Gerencia.docx'
NOMBRE_PDF = 'PREVE_Informe_Gerencia.pdf'

GUIA_ETIQUETA = {
    'PRELIMINAR': 'Informe preliminar: los pronósticos aún se afinan. Úselo para ver tendencias, no para cerrar pedidos.',
    'BORRADOR': 'Informe borrador: los pronósticos ya tienen buena base. Valide las cantidades antes del informe final.',
    'FINAL': 'Informe final: las cantidades están listas para convertirse en órdenes de compra.',
}
DESC_ACCION = {
    'RECOMPRAR': 'Stock bajo el punto de reorden: hay que reponer.',
    'VIGILAR': 'Producto nuevo o con poca historia: comprar con cautela.',
    'REDUCIR': 'Sobre-stock moderado: no comprar y dejar que baje.',
    'LIQUIDAR': 'Stock excesivo o sin demanda: vender lo que hay.',
    'PUBLICITAR': 'Buen margen pero demanda cayendo: promocionar.',
    'NEGOCIAR': 'Alto volumen y margen bajo: negociar mejor precio.',
    'TRASLADAR': 'Excedente en una sucursal que falta en otra.',
}


# ══════════════════════════════════════════════════════════════════
# 1) Contenido
# ══════════════════════════════════════════════════════════════════
def _pct(a, b):
    return a / b * 100 if b else None


def _graficos(d, rec, carpeta_tmp):
    """Devuelve {nombre: ruta_png}. Si matplotlib falla, devuelve lo que se alcanzó a generar."""
    out = {}
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.rcParams.update({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False,
                             'axes.edgecolor': '#DDE3DD', 'axes.labelcolor': GRIS, 'xtick.color': GRIS,
                             'ytick.color': GRIS})

        prov = (rec.assign(p=rec['proveedor_principal'].replace('', 'Sin proveedor'))
                .groupby('p')['costo_total_compra_pen'].sum().sort_values(ascending=False).head(8)[::-1])
        if len(prov) and prov.sum() > 0:
            fig, ax = plt.subplots(figsize=(6.4, 3.0))
            ax.barh([x[:34] for x in prov.index], prov.values, color=VERDE)
            ax.set_xlabel('Inversión sugerida (S/)')
            ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f'{v:,.0f}'))
            fig.tight_layout()
            out['proveedores'] = os.path.join(carpeta_tmp, 'g_prov.png')
            fig.savefig(out['proveedores'], dpi=200)
            plt.close(fig)

        acc = d.df['accion'].value_counts()
        if len(acc):
            colores = {'RECOMPRAR': VERDE, 'VIGILAR': '#3B6EA5', 'REDUCIR': AMBAR, 'LIQUIDAR': ROJO,
                       'PUBLICITAR': '#6B5B95', 'NEGOCIAR': '#A0602A', 'TRASLADAR': '#2F7F86'}
            fig, ax = plt.subplots(figsize=(6.4, 2.6))
            ax.bar(acc.index, acc.values, color=[colores.get(a, GRIS) for a in acc.index])
            for i, v in enumerate(acc.values):
                ax.text(i, v, f'{v:,}', ha='center', va='bottom', fontsize=8, color=VERDE_OSC)
            ax.set_ylabel('Productos')
            fig.tight_layout()
            out['acciones'] = os.path.join(carpeta_tmp, 'g_acc.png')
            fig.savefig(out['acciones'], dpi=200)
            plt.close(fig)

        seg = d.df['segmento'].replace('', np.nan).dropna().value_counts()
        if len(seg):
            fig, ax = plt.subplots(figsize=(6.4, 2.4))
            ax.barh(seg.index[::-1], seg.values[::-1], color=VERDE)
            for i, v in enumerate(seg.values[::-1]):
                ax.text(v, i, f' {v:,}', va='center', fontsize=8, color=VERDE_OSC)
            ax.set_xlabel('Productos')
            fig.tight_layout()
            out['segmentos'] = os.path.join(carpeta_tmp, 'g_seg.png')
            fig.savefig(out['segmentos'], dpi=200)
            plt.close(fig)
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        # FIX: antes except Exception: pass; el informe sigue sin gráficos pero el motivo queda en el log
        logger.exception('No se pudieron generar los gráficos del informe')
    return out


def construir_contenido(carpeta_salida, sucursal='San José', carpeta_tmp=None):
    """Arma la lista de bloques del informe. Devuelve (meta, bloques)."""
    d = dt.Datos(carpeta_salida, sucursal)
    if not d.tiene_datos:
        raise RuntimeError('No hay una recomendación generada para armar el informe.')
    k = d.kpis()
    df = d.df
    rec = d.recompras()
    ci = d.info_ciclo()
    comp = d.comparacion
    actual = comp['actual'] if comp else None
    previa = comp['previa'] if comp else None
    etiqueta = (actual or {}).get('etiqueta', '')
    hoy = datetime.date.today()
    graf = _graficos(d, rec, carpeta_tmp) if carpeta_tmp else {}

    inv_total = float(rec['costo_total_compra_pen'].sum()) if len(rec) else 0.0
    urg = rec[rec['urgencia'] == 'URGENTE']
    inv_urg = float(urg['costo_total_compra_pen'].sum()) if len(urg) else 0.0
    sin_pron = 0
    ruta_sp = os.path.join(carpeta_salida, 'skus_sin_pronostico.csv')
    if os.path.exists(ruta_sp):
        try:
            sin_pron = len(pd.read_csv(ruta_sp))
        except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
            # FIX: antes except Exception: pass
            logger.warning('No se pudo leer %s', ruta_sp, exc_info=True)

    meta = {'titulo': 'Informe de compras para gerencia', 'sucursal': sucursal,
            'subtitulo': f'{dt.empresa_nombre()} · {sucursal}' + (f' · Ciclo #{ci["numero"]}' if ci else '')
                         + (f' · {etiqueta.capitalize()}' if etiqueta else ''),
            'fecha': fecha_larga(hoy) + f' de {hoy.year}',
            'datos': f'Datos al {d.fecha_ref.strftime("%d/%m/%Y")}'}
    B = []

    # ── Resumen ejecutivo ──
    B.append(('h1', 'Resumen ejecutivo'))
    B.append(('kpis', [
        ('Inversión sugerida', fmt_soles(inv_total), f'{len(rec):,} productos a recomprar'),
        ('Inversión urgente', fmt_soles(inv_urg), f'{len(urg):,} productos con quiebre antes de la entrega'),
        ('Capital inmovilizado', fmt_soles(k['capital_muerto']), 'en productos a liquidar'),
        ('En riesgo de quiebre', f'{k["pct_riesgo"]:.0f}%' if k['pct_riesgo'] is not None else '—',
         f'{k["riesgo_n"]:,} productos con stock < {dt.LEAD} días'),
        ('Rotación estimada', f'{k["rotacion"]:.1f}× al año' if k['rotacion'] is not None else '—',
         'valorizada a costo'),
        ('Margen esperado', fmt_soles(k['margen_esperado']), f'próximos {dt.HORIZONTE_DIAS} días, sin IGV'),
    ]))
    pts = []
    if len(rec):
        pts.append(f'PREVÉ recomienda invertir {fmt_soles(inv_total)} en {len(rec):,} productos. '
                   f'De ese monto, {fmt_soles(inv_urg)} ({len(urg):,} productos) es urgente: el stock actual '
                   f'no alcanza para cubrir los {dt.LEAD} días que tarda la mercadería en llegar.')
        top10 = rec.sort_values('costo_total_compra_pen', ascending=False).head(10)['costo_total_compra_pen'].sum()
        p10 = _pct(top10, inv_total)
        pv = (rec.assign(p=rec['proveedor_principal'].replace('', 'Sin proveedor'))
              .groupby('p')['costo_total_compra_pen'].sum().sort_values(ascending=False))
        if p10 is not None and len(pv):
            pts.append(f'La inversión está concentrada: 10 productos explican el {p10:.0f}% del monto y el proveedor '
                       f'principal ({pv.index[0]}) concentra el {_pct(pv.iloc[0], inv_total):.0f}%.')
    else:
        pts.append('En este análisis ningún producto requiere recompra inmediata.')
    if k['pct_riesgo'] is not None:
        pts.append(f'{k["pct_riesgo"]:.0f}% de los productos con demanda ({k["riesgo_n"]:,}) podría quedarse sin '
                   f'stock antes de que llegue un pedido hecho hoy.')
    if k['capital_muerto']:
        n_liq = int((df['accion'] == 'LIQUIDAR').sum())
        pts.append(f'Hay {fmt_soles(k["capital_muerto"])} inmovilizados en {n_liq:,} productos con stock excesivo o '
                   f'sin demanda proyectada.')
    if previa and previa.get('inversion'):
        var = _pct(actual['inversion'] - previa['inversion'], previa['inversion'])
        try:
            f_prev = datetime.datetime.fromisoformat(previa['fecha']).strftime('%d/%m/%Y')
        except ValueError:
            f_prev = previa['fecha'][:10]
        pts.append(f'Frente al análisis anterior ({f_prev}), la inversión sugerida '
                   f'{"sube" if var > 0 else "baja"} {abs(var):.0f}% '
                   f'({fmt_soles(previa["inversion"])} → {fmt_soles(actual["inversion"])}).')
    B.append(('bullets', pts))

    # ── Decisiones que se piden ──
    B.append(('h2', 'Decisiones que se le piden a gerencia'))
    dec = []
    if len(urg):
        dec.append(f'Aprobar la compra urgente de {len(urg):,} productos por {fmt_soles(inv_urg)}; cada día de '
                   f'demora aumenta el riesgo de quiebre.')
    rest = len(rec) - len(urg)
    if rest > 0:
        dec.append(f'Programar la compra de los {rest:,} productos restantes ({fmt_soles(inv_total - inv_urg)}) '
                   f'dentro de este ciclo.')
    if k['revisar_n']:
        dec.append(f'Revisar manualmente {k["revisar_n"]:,} productos marcados por cantidad inusual antes de '
                   f'emitir la orden.')
    n_liq = int((df['accion'] == 'LIQUIDAR').sum())
    if n_liq:
        dec.append(f'Definir una política de liquidación para {n_liq:,} productos que inmovilizan '
                   f'{fmt_soles(k["capital_muerto"])}.')
    n_neg = int((df['accion'] == 'NEGOCIAR').sum())
    if n_neg:
        dec.append(f'Renegociar precio con proveedores de {n_neg:,} productos de alta rotación y margen bajo (<10%).')
    if ci and not ci['vencido']:
        dec.append(f'Tener presente la fecha límite del pedido internacional: {fecha_larga(ci["limite"])} '
                   f'(faltan {ci["dias_restantes"]} días).')
    B.append(('bullets', dec or ['No hay decisiones pendientes en este análisis.']))
    if d.decisiones and len(rec):
        est = rec['sku'].map(d.estado)
        linea = []
        for e, txt in (('comprado', 'comprados'), ('pospuesto', 'pospuestos'), ('descartado', 'descartados')):
            m = est == e
            if m.any():
                linea.append(f'{int(m.sum()):,} {txt} ({fmt_soles(rec.loc[m, "costo_total_compra_pen"].sum())})')
        pend = est == 'pendiente'
        if linea:
            B.append(('p', 'Avance de decisiones en este ciclo: ' + ', '.join(linea) +
                      f'. Quedan {int(pend.sum()):,} pendientes por {fmt_soles(rec.loc[pend, "costo_total_compra_pen"].sum())}.'))
    if etiqueta in GUIA_ETIQUETA:
        B.append(('nota', GUIA_ETIQUETA[etiqueta]))

    # ── Qué comprar ──
    B.append(('h1', '1. Qué comprar'))
    if len(rec):
        B.append(('p', 'Los 15 productos de mayor inversión concentran la parte más importante del pedido:'))
        t = rec.sort_values('costo_total_compra_pen', ascending=False).head(15)
        B.append(('table', ['Producto', 'Proveedor', 'Stock', 'Comprar', 'Inversión', 'Prioridad'],
                  [[_corto(r['nombre'], 38), _corto(r['proveedor_principal'] or '—', 22), fmt_num(r['stock_actual']),
                    f'{safe_int(r["compra_sugerida_unid"]):,}', fmt_soles(r['costo_total_compra_pen']),
                    r['urgencia'].capitalize()] for _, r in t.iterrows()],
                  ['l', 'l', 'r', 'r', 'r', 'l'], [5.2, 3.2, 1.4, 1.6, 2.2, 1.9]))
        if 'proveedores' in graf:
            B.append(('h2', 'Inversión por proveedor'))
            B.append(('img', graf['proveedores'], 15.5))
        cat = (rec.assign(c=rec['categoria'].replace('', 'Sin categoría'))
               .groupby('c').agg(prod=('sku', 'count'), unid=('compra_sugerida_unid', 'sum'),
                                 inv=('costo_total_compra_pen', 'sum')).sort_values('inv', ascending=False).head(8))
        if len(cat):
            B.append(('h2', 'Inversión por categoría'))
            B.append(('table', ['Categoría', 'Productos', 'Unidades', 'Inversión', '% del total'],
                      [[_corto(i, 30), f'{safe_int(r["prod"]):,}', f'{safe_int(r["unid"]):,}', fmt_soles(r['inv']),
                        f'{_pct(r["inv"], inv_total):.0f}%' if inv_total else '—'] for i, r in cat.iterrows()],
                      ['l', 'r', 'r', 'r', 'r'], [5.5, 2.4, 2.4, 3.0, 2.2]))

    # ── Urgentes ──
    B.append(('h1', '2. Productos urgentes'))
    if len(urg):
        B.append(('p', 'Estos productos se quedarían sin stock antes de que llegue un pedido hecho hoy '
                       f'(entrega estimada: {dt.LEAD} días). Se muestran los 10 de mayor impacto.'))
        u = urg.sort_values('costo_total_compra_pen', ascending=False).head(10)
        filas = []
        for _, r in u.iterrows():
            quiebre = ('ya en quiebre' if r['stock_actual'] <= 0 else
                       fecha_larga(d.fecha_ref + datetime.timedelta(days=float(r['dias_stock']))))
            filas.append([_corto(r['nombre'], 38), fmt_num(r['stock_actual']),
                          '—' if np.isinf(r['dias_stock']) else f'{r["dias_stock"]:.0f}', quiebre,
                          f'{safe_int(r["compra_sugerida_unid"]):,}', fmt_soles(r['costo_total_compra_pen'])])
        B.append(('table', ['Producto', 'Stock', 'Días de stock', 'Quiebre estimado', 'Comprar', 'Inversión'],
                  filas, ['l', 'r', 'r', 'l', 'r', 'r'], [5.2, 1.5, 2.0, 3.0, 1.6, 2.2]))
    else:
        B.append(('p', 'Ningún producto presenta riesgo de quiebre antes de la entrega.'))

    # ── Inventario a corregir ──
    B.append(('h1', '3. Inventario a corregir'))
    if 'acciones' in graf:
        B.append(('img', graf['acciones'], 15.0))
    filas = []
    for a, desc in DESC_ACCION.items():
        n = int((df['accion'] == a).sum())
        if n:
            filas.append([a.capitalize(), f'{n:,}', desc])
    if filas:
        B.append(('table', ['Acción', 'Productos', 'Qué significa'], filas, ['l', 'r', 'l'], [3.0, 2.2, 11.0]))
    liq = df[(df['accion'] == 'LIQUIDAR') & df['costo_puesto_almacen_pen'].notna()].copy()
    if len(liq):
        liq['capital'] = liq['stock_actual'].clip(lower=0) * liq['costo_puesto_almacen_pen']
        liq = liq.sort_values('capital', ascending=False).head(10)
        B.append(('h2', 'Mayor capital inmovilizado (a liquidar)'))
        B.append(('table', ['Producto', 'Stock', 'Demanda esperada', 'Capital inmovilizado'],
                  [[_corto(r['nombre'], 44), fmt_num(r['stock_actual']), fmt_num(r['demanda_p50_141d']),
                    fmt_soles(r['capital'])] for _, r in liq.iterrows()],
                  ['l', 'r', 'r', 'r'], [7.0, 2.0, 3.4, 3.8]))
    neg = df[df['accion'] == 'NEGOCIAR'].sort_values('costo_total_compra_pen', ascending=False).head(8)
    if len(neg):
        B.append(('h2', 'Candidatos a renegociar precio'))
        B.append(('table', ['Producto', 'Proveedor', 'Margen', 'Inversión'],
                  [[_corto(r['nombre'], 38), _corto(r['proveedor_principal'] or '—', 24),
                    f'{r["margen_pct"] * 100:.1f}%' if pd.notna(r['margen_pct']) else '—',
                    fmt_soles(r['costo_total_compra_pen'])] for _, r in neg.iterrows()],
                  ['l', 'l', 'r', 'r'], [6.0, 4.5, 2.0, 3.2]))

    # ── Comparación ──
    if previa:
        B.append(('h1', '4. Comparación con el análisis anterior'))
        B.append(('p', f'Inversión sugerida: {fmt_soles(actual["inversion"])} ahora frente a '
                       f'{fmt_soles(previa["inversion"])} antes. Productos a recomprar: {actual["recomprar"]:,} ahora, '
                       f'{previa["recomprar"]:,} antes.'))
        cambios = []
        for _, r in rec.iterrows():
            antes = d.previa_unidades(r['sku'])
            ahora = safe_int(r['compra_sugerida_unid'])
            if antes is not None and safe_int(antes) != ahora:
                cambios.append((abs(ahora - safe_int(antes)), r, safe_int(antes), ahora))
        cambios.sort(key=lambda x: -x[0])
        if cambios:
            B.append(('table', ['Producto', 'Antes (u)', 'Ahora (u)', 'Cambio'],
                      [[_corto(r['nombre'], 44), f'{a:,}', f'{n:,}', f'{n - a:+,}'] for _, r, a, n in cambios[:8]],
                      ['l', 'r', 'r', 'r'], [8.0, 2.6, 2.6, 2.4]))

    # ── Catálogo ──
    B.append(('h1', '5. Cómo está compuesto el catálogo'))
    if 'segmentos' in graf:
        B.append(('img', graf['segmentos'], 15.0))
    abc = df['clase_abc'].value_counts()
    if len(abc):
        B.append(('p', 'Clasificación ABC por margen: ' + ', '.join(
            f'clase {c}: {int(abc.get(c, 0)):,} productos' for c in ('A', 'B', 'C')) +
            '. La clase A es el ~80% del margen y merece revisión más frecuente.'))

    # ── Confiabilidad ──
    B.append(('h1', '6. Qué tan confiable es el pronóstico'))
    conf = df['confianza'].value_counts()
    pr = d.precision_historica()
    items = []
    if pr is not None:
        items.append(f'Precisión histórica de la última validación contra ventas reales: {pr:.0f}% '
                     f'(calificación: {d.scorecard.get("calificacion") or "—"}).')
    else:
        items.append('Todavía no hay una validación contra ventas reales: la precisión histórica se medirá en el '
                     'próximo análisis.')
    items.append('Confianza por producto — alta: {:,}, media: {:,}, baja: {:,}.'.format(
        int(conf.get('ALTA', 0)), int(conf.get('MEDIA', 0)), int(conf.get('BAJA', 0))))
    if k['revisar_n']:
        items.append(f'{k["revisar_n"]:,} productos tienen una cantidad sugerida inusual y requieren revisión manual.')
    inc = int(df['dato_incompleto'].sum())
    if inc:
        items.append(f'{inc:,} productos tienen costo o precio incompleto en el ERP: su inversión y margen pueden '
                     f'estar subestimados.')
    if sin_pron:
        items.append(f'{sin_pron:,} productos no tienen pronóstico por falta de datos y no están incluidos en las cifras.')
    B.append(('bullets', items))

    # ── Metodología ──
    B.append(('h1', 'Anexo. Cómo se calculan las cifras'))
    B.append(('bullets', [
        f'Horizonte de planificación: {dt.HORIZONTE_DIAS} días ({dt.duracion_ciclo()} de intervalo de revisión + {dt.LEAD} de entrega).',
        'Pronóstico: cada producto se asigna a LightGBM, CatBoost o Croston-TSB según su patrón de ventas '
        '(ADI/CV², umbrales de Syntetos-Boylan).',
        'Nivel objetivo = demanda esperada del horizonte + stock de seguridad (niveles de servicio por clase ABC). '
        'Punto de reorden = demanda durante el tiempo de entrega + su stock de seguridad. '
        'Cantidad a comprar = nivel objetivo − stock actual − stock en tránsito.',
        'Urgente: hay que recomprar y el stock alcanza para menos días que el tiempo de entrega.',
        'Rotación y margen esperado se calculan con costos y precios del ERP (precio sin IGV). El stock es el '
        'reconstruido por PREVÉ a partir de ventas, compras y movimientos.',
    ]))
    return meta, B


def _corto(txt, n):
    txt = str(txt or '')
    return txt if len(txt) <= n else txt[:n - 1] + '…'


# ══════════════════════════════════════════════════════════════════
# 2) Word
# ══════════════════════════════════════════════════════════════════
def _escribir_docx(meta, B, ruta):
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    def rgb(h):
        h = h.lstrip('#')
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def sombrear(celda, hex_):
        tcPr = celda._tc.get_or_add_tcPr()
        sh = OxmlElement('w:shd')
        sh.set(qn('w:val'), 'clear')
        sh.set(qn('w:color'), 'auto')
        sh.set(qn('w:fill'), hex_.lstrip('#'))
        tcPr.append(sh)

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin = sec.bottom_margin = Cm(2)
    normal = doc.styles['Normal']
    normal.font.name = 'Calibri'
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    for nombre, tam, color in (('Heading 1', 16, VERDE), ('Heading 2', 12.5, VERDE_OSC)):
        st = doc.styles[nombre]
        st.font.name, st.font.size, st.font.bold = 'Calibri', Pt(tam), True
        st.font.color.rgb = rgb(color)
        st.paragraph_format.space_before, st.paragraph_format.space_after = Pt(14), Pt(6)

    # Portada compacta
    t = doc.add_paragraph()
    r = t.add_run('PREVÉ')
    r.bold, r.font.size, r.font.color.rgb = True, Pt(12), rgb(VERDE)
    t = doc.add_paragraph()
    r = t.add_run(meta['titulo'])
    r.bold, r.font.size, r.font.color.rgb = True, Pt(24), rgb(VERDE_OSC)
    t.paragraph_format.space_after = Pt(2)
    t = doc.add_paragraph()
    r = t.add_run(meta['subtitulo'])
    r.font.size, r.font.color.rgb = Pt(12), rgb(GRIS)
    t.paragraph_format.space_after = Pt(0)
    t = doc.add_paragraph()
    r = t.add_run(f'{meta["fecha"]} · {meta["datos"]}')
    r.font.size, r.font.color.rgb = Pt(9.5), rgb(GRIS)

    # Pie con número de página
    p = sec.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f'PREVÉ · {dt.empresa_nombre()} — Página ')
    r.font.size, r.font.color.rgb = Pt(8), rgb(GRIS)
    r2 = p.add_run()
    r2.font.size = Pt(8)
    for tipo, texto in (('begin', None), (None, 'PAGE'), ('end', None)):
        if tipo:
            el = OxmlElement('w:fldChar')
            el.set(qn('w:fldCharType'), tipo)
        else:
            el = OxmlElement('w:instrText')
            el.set(qn('xml:space'), 'preserve')
            el.text = texto
        r2._r.append(el)

    for b in B:
        tipo = b[0]
        if tipo == 'h1':
            doc.add_heading(b[1], level=1)
        elif tipo == 'h2':
            doc.add_heading(b[1], level=2)
        elif tipo == 'p':
            doc.add_paragraph(b[1])
        elif tipo == 'nota':
            tb = doc.add_table(rows=1, cols=1)
            c = tb.rows[0].cells[0]
            sombrear(c, '#E3F0EA')
            c.text = ''
            rr = c.paragraphs[0].add_run(b[1])
            rr.italic, rr.font.size, rr.font.color.rgb = True, Pt(10), rgb(VERDE_OSC)
            doc.add_paragraph()
        elif tipo == 'bullets':
            for it in b[1]:
                doc.add_paragraph(it, style='List Bullet')
        elif tipo == 'kpis':
            items = b[1]
            tb = doc.add_table(rows=(len(items) + 2) // 3, cols=3)
            tb.alignment = WD_TABLE_ALIGNMENT.CENTER
            tb.style = 'Table Grid'
            for i, (lab, val, sub) in enumerate(items):
                c = tb.rows[i // 3].cells[i % 3]
                sombrear(c, '#F7F8F6')
                c.text = ''
                p1 = c.paragraphs[0]
                a = p1.add_run(lab.upper())
                a.font.size, a.bold, a.font.color.rgb = Pt(7.5), True, rgb(GRIS)
                p2 = c.add_paragraph()
                v = p2.add_run(val)
                v.bold, v.font.size, v.font.color.rgb = True, Pt(15), rgb(VERDE)
                p3 = c.add_paragraph()
                s = p3.add_run(sub)
                s.font.size, s.font.color.rgb = Pt(8), rgb(GRIS)
                for pp in (p1, p2, p3):
                    pp.paragraph_format.space_after = Pt(1)
            doc.add_paragraph()
        elif tipo == 'table':
            _, enc, filas, alin, anchos = b
            tb = doc.add_table(rows=1, cols=len(enc))
            tb.style = 'Table Grid'
            tb.alignment = WD_TABLE_ALIGNMENT.CENTER
            for j, h in enumerate(enc):
                c = tb.rows[0].cells[j]
                sombrear(c, VERDE)
                c.text = ''
                rr = c.paragraphs[0].add_run(h)
                rr.bold, rr.font.size, rr.font.color.rgb = True, Pt(8.5), RGBColor(255, 255, 255)
                if alin[j] == 'r':
                    c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
            for i, fila in enumerate(filas):
                cells = tb.add_row().cells
                for j, v in enumerate(fila):
                    cells[j].text = ''
                    rr = cells[j].paragraphs[0].add_run(str(v))
                    rr.font.size = Pt(8.5)
                    if alin[j] == 'r':
                        cells[j].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    if i % 2:
                        sombrear(cells[j], '#F3F8F5')
            for row in tb.rows:
                for j, w in enumerate(anchos):
                    row.cells[j].width = Cm(w)
                    for pp in row.cells[j].paragraphs:
                        pp.paragraph_format.space_after = Pt(1)
            doc.add_paragraph()
        elif tipo == 'img':
            doc.add_picture(b[1], width=Cm(b[2]))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.save(ruta)


# ══════════════════════════════════════════════════════════════════
# 3) PDF
# ══════════════════════════════════════════════════════════════════
def _escribir_pdf(meta, B, ruta):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
                                    ListFlowable, ListItem, KeepTogether)

    verde, oscuro, gris = colors.HexColor(VERDE), colors.HexColor(VERDE_OSC), colors.HexColor(GRIS)
    S = {
        'p': ParagraphStyle('p', fontName='Helvetica', fontSize=9.5, leading=13.5, textColor=oscuro, spaceAfter=6),
        'h1': ParagraphStyle('h1', fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=verde,
                             spaceBefore=14, spaceAfter=6),
        'h2': ParagraphStyle('h2', fontName='Helvetica-Bold', fontSize=11.5, leading=15, textColor=oscuro,
                             spaceBefore=10, spaceAfter=4),
        'nota': ParagraphStyle('nota', fontName='Helvetica-Oblique', fontSize=9.5, leading=13, textColor=oscuro),
        'celda': ParagraphStyle('celda', fontName='Helvetica', fontSize=8, leading=10, textColor=oscuro),
        'celda_r': ParagraphStyle('celda_r', fontName='Helvetica', fontSize=8, leading=10, textColor=oscuro,
                                  alignment=TA_RIGHT),
        'th': ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white),
        'th_r': ParagraphStyle('th_r', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white,
                               alignment=TA_RIGHT),
        'kl': ParagraphStyle('kl', fontName='Helvetica-Bold', fontSize=7, leading=9, textColor=gris),
        'kv': ParagraphStyle('kv', fontName='Helvetica-Bold', fontSize=14, leading=18, textColor=verde),
        'ks': ParagraphStyle('ks', fontName='Helvetica', fontSize=7.5, leading=9.5, textColor=gris),
    }
    esc = lambda s: str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def pie(canvas, doc_):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(gris)
        canvas.drawCentredString(A4[0] / 2, 1.1 * cm, f'PREVÉ · {dt.empresa_nombre()} — Página {doc_.page}')
        canvas.setFillColor(colors.HexColor('#FF6B00'))
        canvas.rect(0, A4[1] - 0.25 * cm, A4[0], 0.25 * cm, stroke=0, fill=1)   # banda Yuju
        canvas.restoreState()

    doc = SimpleDocTemplate(ruta, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=1.8 * cm, title=meta['titulo'], author=f'PREVÉ · {dt.empresa_nombre()}')
    ancho = A4[0] - 4 * cm
    E = [Paragraph('<font color="%s"><b>PREVÉ</b></font>' % VERDE, ParagraphStyle('x', fontName='Helvetica', fontSize=11)),
         Spacer(1, 4),
         Paragraph(esc(meta['titulo']), ParagraphStyle('t', fontName='Helvetica-Bold', fontSize=22, leading=26,
                                                         textColor=oscuro)),
         Paragraph(esc(meta['subtitulo']), ParagraphStyle('s', fontName='Helvetica', fontSize=11.5, leading=15,
                                                            textColor=gris)),
         Paragraph(esc(f'{meta["fecha"]} · {meta["datos"]}'), ParagraphStyle('f', fontName='Helvetica', fontSize=9,
                                                                             textColor=gris, spaceAfter=6))]
    for b in B:
        tipo = b[0]
        if tipo in ('h1', 'h2', 'p'):
            E.append(Paragraph(esc(b[1]), S[tipo]))
        elif tipo == 'nota':
            t = Table([[Paragraph(esc(b[1]), S['nota'])]], colWidths=[ancho])
            t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#E3F0EA')),
                                   ('LEFTPADDING', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 8),
                                   ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
            E += [Spacer(1, 4), t, Spacer(1, 6)]
        elif tipo == 'bullets':
            E.append(ListFlowable([ListItem(Paragraph(esc(i), S['p']), leftIndent=14) for i in b[1]],
                                  bulletType='bullet', start='•', leftIndent=14, bulletColor=verde))
        elif tipo == 'kpis':
            celdas = [[Paragraph(esc(l.upper()), S['kl']), Paragraph(esc(v), S['kv']), Paragraph(esc(s), S['ks'])]
                      for l, v, s in b[1]]
            while len(celdas) % 3:
                celdas.append('')
            filas = [celdas[i:i + 3] for i in range(0, len(celdas), 3)]
            t = Table(filas, colWidths=[ancho / 3] * 3)
            t.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#DDE3DD')),
                                   ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#DDE3DD')),
                                   ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F7F8F6')),
                                   ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 7),
                                   ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (-1, -1), 9)]))
            E += [Spacer(1, 4), t, Spacer(1, 8)]
        elif tipo == 'table':
            _, enc, filas, alin, anchos = b
            tot = sum(anchos)
            cw = [a / tot * ancho for a in anchos]
            data = [[Paragraph(esc(h), S['th_r' if alin[j] == 'r' else 'th']) for j, h in enumerate(enc)]]
            for fila in filas:
                data.append([Paragraph(esc(v), S['celda_r' if alin[j] == 'r' else 'celda'])
                             for j, v in enumerate(fila)])
            t = Table(data, colWidths=cw, repeatRows=1)
            t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), verde),
                                   ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F3F8F5')]),
                                   ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDE3DD')),
                                   ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 4),
                                   ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
            E += [t, Spacer(1, 8)]
        elif tipo == 'img':
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(b[1]).getSize()
            w = min(b[2] * cm, ancho)
            E += [KeepTogether([Image(b[1], width=w, height=w * ih / iw)]), Spacer(1, 6)]
    doc.build(E, onFirstPage=pie, onLaterPages=pie)


# ══════════════════════════════════════════════════════════════════
# 4) API
# ══════════════════════════════════════════════════════════════════
def generar_informe(carpeta_salida, sucursal='San José', formatos=('docx', 'pdf'), log=print):
    """Genera el informe y devuelve {'docx': ruta, 'pdf': ruta} con lo que se pudo escribir."""
    tmp = tempfile.mkdtemp(prefix='preve_informe_')
    resultado = {}
    try:
        meta, bloques = construir_contenido(carpeta_salida, sucursal, tmp)
        for fmt, nombre, fn in (('docx', NOMBRE_DOCX, _escribir_docx), ('pdf', NOMBRE_PDF, _escribir_pdf)):
            if fmt not in formatos:
                continue
            ruta = os.path.join(carpeta_salida, nombre)
            try:
                fn(meta, bloques, ruta)
                resultado[fmt] = ruta
                log(f'Informe para gerencia ({fmt.upper()}): {ruta}')
            except ImportError as e:
                log(f'⚠ No se pudo generar el informe {fmt.upper()}: falta instalar una librería ({e}). '
                    f'Ejecuta INSTALAR_Y_EJECUTAR otra vez.')
            except PermissionError:
                log(f'⚠ No se pudo escribir {nombre}: ciérralo si lo tienes abierto y vuelve a intentar.')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return resultado
