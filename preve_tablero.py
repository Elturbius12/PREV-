# -*- coding: utf-8 -*-
"""
PREVÉ — Tablero y Calendario dentro de la app (reemplazan al dashboard.html).

- `resumen_tablero(datos)`: cuadros del tablero como DataFrames (lógica pura, sin interfaz).
- `exportar_tablero(datos, ruta)`: un solo Excel con todos los cuadros y sus figuras incrustadas.
- `crear_vistas(...)`: clases de las vistas Tablero y Calendario (sobre la base Vista de preve_vistas).

Cada cuadro trae una frase de "lectura" para que el usuario no tenga que interpretar el gráfico.
"""
import os
import datetime
import tkinter as tk

import numpy as np
import pandas as pd

import preve_ui as ui
from preve_ui import (S, Card, Btn, Chip, ProgressBar, ScrollFrame, label, BG, SURFACE, TEXT_DIM, TEXT_MUTED,
                      ACCENT, CARD_ALT, OK, WARN, DANGER)
import preve_datos as dt
from preve_datos import fmt_num, fmt_soles, fecha_larga
from preve_utils import get_logger

logger = get_logger('tablero')

NOMBRE_TABLERO_XLSX = 'PREVE_Tablero.xlsx'
COLOR_URGENCIA = {'URGENTE': DANGER, 'PRONTO': WARN, 'MONITOREAR': OK}
COLOR_CONFIANZA = {'ALTA': OK, 'MEDIA': WARN, 'BAJA': DANGER}


# ══════════════════════════════════════════════════════════════════
# Cuadros (lógica pura)
# ══════════════════════════════════════════════════════════════════
def _pct(a, b):
    return a / b * 100 if b else 0.0


def resumen_tablero(datos):
    """dict titulo → {'tabla': DataFrame, 'lectura': str, 'valor': col, 'etiqueta': col}. Vacío si no hay datos."""
    if not datos.tiene_datos:
        return {}
    d = datos.df
    inv = d['costo_total_compra_pen'].where(d['es_compra'], 0).fillna(0)
    total_inv = inv.sum()
    out = {}

    acc = (d.assign(_inv=inv).groupby('accion')
           .agg(productos=('sku', 'size'), inversion_pen=('_inv', 'sum'))
           .sort_values('productos', ascending=False).reset_index())
    acc['pct_productos'] = acc['productos'].map(lambda n: round(_pct(n, len(d)), 1))
    top = acc.iloc[0] if len(acc) else None
    out['Productos por acción'] = {
        'tabla': acc, 'valor': 'productos', 'etiqueta': 'accion',
        'lectura': (f'La acción más frecuente es {top["accion"]}: {int(top["productos"])} de {len(d)} productos '
                    f'({top["pct_productos"]:.0f}%).') if top is not None else ''}

    urg = (d.assign(_inv=inv).groupby('urgencia')
           .agg(productos=('sku', 'size'), inversion_pen=('_inv', 'sum'))
           .reindex(['URGENTE', 'PRONTO', 'MONITOREAR']).fillna(0).reset_index())
    n_urg = int(urg.loc[urg['urgencia'] == 'URGENTE', 'productos'].sum())
    i_urg = float(urg.loc[urg['urgencia'] == 'URGENTE', 'inversion_pen'].sum())
    out['Urgencia de compra'] = {
        'tabla': urg, 'valor': 'productos', 'etiqueta': 'urgencia',
        'lectura': (f'{n_urg} productos son urgentes y concentran {fmt_soles(i_urg)} '
                    f'({_pct(i_urg, total_inv):.0f}% de la inversión).') if n_urg else
                   'No hay compras urgentes.'}

    if total_inv > 0:
        cat = (d.assign(_inv=inv)[inv > 0].groupby('categoria')
               .agg(inversion_pen=('_inv', 'sum'), productos=('sku', 'size'))
               .sort_values('inversion_pen', ascending=False).reset_index())
        cat['categoria'] = cat['categoria'].replace('', 'Sin categoría')
        cat['pct_inversion'] = (cat['inversion_pen'] / total_inv * 100).round(1)
        top3 = cat.head(3)['pct_inversion'].sum()
        out['Inversión por categoría'] = {
            'tabla': cat, 'valor': 'inversion_pen', 'etiqueta': 'categoria',
            'lectura': f'Las 3 categorías principales concentran el {top3:.0f}% de la inversión sugerida.'}

        abc = (d.assign(_inv=inv).groupby('clase_abc')
               .agg(productos=('sku', 'size'), inversion_pen=('_inv', 'sum')).reset_index())
        abc['clase_abc'] = abc['clase_abc'].replace('', 'Sin clase')
        abc['pct_inversion'] = (abc['inversion_pen'] / total_inv * 100).round(1)
        a = abc.loc[abc['clase_abc'] == 'A']
        out['Inversión por clase ABC'] = {
            'tabla': abc, 'valor': 'inversion_pen', 'etiqueta': 'clase_abc',
            'lectura': (f'Los productos clase A (los que más venden) reciben el {float(a["pct_inversion"].iloc[0]):.0f}% '
                        'de la inversión.') if len(a) else ''}

    conf = (d[d['es_compra']].groupby('confianza').agg(productos=('sku', 'size'))
            .reindex(['ALTA', 'MEDIA', 'BAJA']).fillna(0).reset_index())
    n_c = conf['productos'].sum()
    alta = float(conf.loc[conf['confianza'] == 'ALTA', 'productos'].sum())
    out['Confianza del pronóstico (compras)'] = {
        'tabla': conf, 'valor': 'productos', 'etiqueta': 'confianza',
        'lectura': (f'El {_pct(alta, n_c):.0f}% de las compras sugeridas tiene confianza alta; las de confianza '
                    'baja conviene revisarlas antes de comprar.') if n_c else ''}

    prov = (d.assign(_inv=inv)[inv > 0].groupby('proveedor_principal')
            .agg(inversion_pen=('_inv', 'sum'), productos=('sku', 'size'))
            .sort_values('inversion_pen', ascending=False).head(10).reset_index())
    if len(prov):
        prov['proveedor_principal'] = prov['proveedor_principal'].replace('', 'Sin proveedor')
        p0 = prov.iloc[0]
        out['Top 10 proveedores por inversión'] = {
            'tabla': prov, 'valor': 'inversion_pen', 'etiqueta': 'proveedor_principal',
            'lectura': f'El proveedor con más compra sugerida es {p0["proveedor_principal"]} '
                       f'({fmt_soles(p0["inversion_pen"])}).'}
    return out


def kpis_tablero(datos):
    """Fila de indicadores del tablero (los del antiguo dashboard.html)."""
    d = datos.df
    return [('Productos analizados', fmt_num(len(d)), ''),
            ('A comprar', fmt_num(int(d['es_compra'].sum())), ''),
            ('Inversión sugerida', fmt_soles(d.loc[d['es_compra'], 'costo_total_compra_pen'].sum()), ''),
            ('Revisar antes de comprar', fmt_num(int(d['revisar_antes_de_comprar'].sum())), 'venta fuera de lo común'),
            ('Datos incompletos', fmt_num(int(d['dato_incompleto'].sum())), 'sin costo o precio')]


def exportar_tablero(datos, ruta):
    """Guarda en UN Excel: hoja Resumen (lecturas) + una hoja por cuadro con su figura incrustada."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from openpyxl.drawing.image import Image as XLImage
    import tempfile

    cuadros = resumen_tablero(datos)
    if not cuadros:
        raise ValueError('No hay recomendación cargada.')
    tmp = tempfile.mkdtemp(prefix='preve_tablero_')
    resumen = pd.DataFrame([{'Cuadro': k, 'Lectura': v['lectura']} for k, v in cuadros.items()])
    with pd.ExcelWriter(ruta, engine='openpyxl') as xw:
        pd.DataFrame([{'Indicador': a, 'Valor': b, 'Nota': c} for a, b, c in kpis_tablero(datos)]
                     ).to_excel(xw, sheet_name='Resumen', index=False)
        resumen.to_excel(xw, sheet_name='Resumen', index=False, startrow=8)
        for i, (titulo, c) in enumerate(cuadros.items(), 1):
            hoja = f'{i}_{titulo}'[:31].replace('/', '-')
            c['tabla'].to_excel(xw, sheet_name=hoja, index=False)
            t = c['tabla'].head(12)
            fig, ax = plt.subplots(figsize=(7, 0.45 * len(t) + 1.2), dpi=150)
            ax.barh(t[c['etiqueta']].astype(str)[::-1], t[c['valor']][::-1], color='#2D7D5F')
            ax.set_title(titulo, fontsize=11, loc='left')
            ax.spines[['top', 'right']].set_visible(False)
            ax.set_xlabel('S/' if c['valor'].endswith('_pen') else 'productos')
            fig.tight_layout()
            png = os.path.join(tmp, f'{i}.png')
            fig.savefig(png)
            plt.close(fig)
            img = XLImage(png)
            img.width, img.height = img.width * 0.5, img.height * 0.5
            xw.sheets[hoja].add_image(img, f'{chr(ord("A") + len(t.columns) + 1)}2')
        for ws in xw.sheets.values():
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(60, max(12, *(len(str(x.value or ''))
                                                                                   for x in col[:50])))
    return ruta


# ══════════════════════════════════════════════════════════════════
# Vistas
# ══════════════════════════════════════════════════════════════════
def _barras(parent, tabla, etiqueta, valor, colores=None, n=8, soles=False):
    """Barras horizontales simples: nombre · barra proporcional · valor."""
    t = tabla.head(n)
    maximo = float(t[valor].max()) if len(t) else 0
    for _, r in t.iterrows():
        fila = tk.Frame(parent, bg=SURFACE)
        fila.pack(fill='x', pady=S(3))
        fila.columnconfigure(1, weight=1)
        label(fila, str(r[etiqueta])[:28], 9, width=24, anchor='w').grid(row=0, column=0, sticky='w')
        b = ProgressBar(fila, 10)
        b.grid(row=0, column=1, sticky='ew', padx=S(8))
        color = (colores or {}).get(r[etiqueta], ACCENT)
        b.after(30, lambda b=b, v=r[valor], c=color: b.set(float(v) / maximo if maximo else 0, c))
        label(fila, fmt_soles(r[valor]) if soles else fmt_num(r[valor]), 9, 'bold', mono=True, width=12,
              anchor='e').grid(row=0, column=2, sticky='e')


def crear_vistas(Vista, PADX, fila_cards, stat_card, estado_vacio):
    """Define las clases de vista sobre la base `Vista` de preve_vistas."""

    class _Tablero(Vista):
        def build(self):
            self.scroll = ScrollFrame(self)
            self.scroll.pack(fill='both', expand=True)
            self.cont = self.scroll.inner

        def on_show(self, **kw):
            self.limpiar(self.cont)
            d = self.app.datos
            enc = self.encabezado(self.cont, 'Tablero', f'Resumen visual del último análisis · {self.app.sucursal}')
            if not d.tiene_datos:
                estado_vacio(self.cont, 'Aún no hay datos para el tablero',
                             'Genera una recomendación y aquí verás los cuadros y gráficos.',
                             'Generar recomendación', self.app.ir_generar).pack(pady=S(40))
                return
            Btn(enc, '⬇ Exportar tablero a Excel', self.exportar, 'primary', 'md').pack(side='right')
            fila_cards(self.cont, [lambda p, k=k: stat_card(p, *k) for k in kpis_tablero(d)])
            cuadros = resumen_tablero(d)
            grid = tk.Frame(self.cont, bg=BG)
            grid.pack(fill='x', padx=S(PADX), pady=(0, S(24)))
            grid.columnconfigure(0, weight=1, uniform='t')
            grid.columnconfigure(1, weight=1, uniform='t')
            colores = {'Urgencia de compra': COLOR_URGENCIA, 'Confianza del pronóstico (compras)': COLOR_CONFIANZA}
            for i, (titulo, c) in enumerate(cuadros.items()):
                card = Card(grid, padx=18, pady=14)
                card.grid(row=i // 2, column=i % 2, sticky='nsew',
                          padx=(0 if i % 2 == 0 else S(7), S(7) if i % 2 == 0 else 0), pady=(0, S(14)))
                label(card.body, titulo, 11, 'bold').pack(anchor='w')
                if c['lectura']:
                    label(card.body, c['lectura'], 9, color=TEXT_DIM, wraplength=S(430), justify='left'
                          ).pack(anchor='w', pady=(S(2), S(8)))
                _barras(card.body, c['tabla'], c['etiqueta'], c['valor'], colores.get(titulo),
                        soles=c['valor'].endswith('_pen'))

        def exportar(self):
            d = self.app.datos
            ruta = os.path.join(d.carpeta_periodo, NOMBRE_TABLERO_XLSX)
            try:
                exportar_tablero(d, ruta)
            except (OSError, ValueError, PermissionError) as e:
                logger.exception('No se pudo exportar el tablero')
                self.app.toast(f'No se pudo exportar el tablero (¿está abierto en Excel?): {e}', 'error')
                return
            self.app.toast(f'Tablero guardado: {NOMBRE_TABLERO_XLSX}', 'ok',
                           accion=('Abrir', lambda: self.app.abrir_archivo(ruta)))

    class _Calendario(Vista):
        def build(self):
            self.scroll = ScrollFrame(self)
            self.scroll.pack(fill='both', expand=True)
            self.cont = self.scroll.inner

        def on_show(self, **kw):
            from preve_ciclo import ancla_calendario, calendario_ciclos, ciclo_de_calendario
            self.limpiar(self.cont)
            ancla = ancla_calendario()
            dur = int(dt.duracion_ciclo())
            self.encabezado(self.cont, 'Calendario de compras',
                            f'Ciclos fijos de {dur} días. El último día de cada ciclo es la fecha del pedido.')
            if ancla is None:
                card = Card(self.cont, padx=22, pady=18)
                card.pack(fill='x', padx=S(PADX))
                label(card.body, 'El calendario fijo está apagado: el ciclo empieza el día de la primera '
                                 'importación. Actívalo en Configuración › Calendario de compras.', 10,
                      color=TEXT_DIM, wraplength=S(700), justify='left').pack(anchor='w')
                return
            hoy = datetime.date.today()
            n, inicio, dia = ciclo_de_calendario(hoy, ancla, dur)
            pedido = inicio + datetime.timedelta(days=dur - 1)
            fila_cards(self.cont, [
                lambda p: stat_card(p, 'Hoy', f'Ciclo {n} · día {dia}', f'de {dur} días', ACCENT),
                lambda p: stat_card(p, 'Fecha del pedido', fecha_larga(pedido),
                                    f'faltan {(pedido - hoy).days} días',
                                    WARN if (pedido - hoy).days <= 14 else ui.TEXT),
                lambda p: stat_card(p, 'Día 1 del calendario', fecha_larga(ancla), 'se cambia en Configuración'),
            ])
            card = Card(self.cont, padx=22, pady=16)
            card.pack(fill='x', padx=S(PADX), pady=(0, S(24)))
            cab = tk.Frame(card.body, bg=SURFACE)
            cab.pack(fill='x', pady=(0, S(6)))
            for j, (t, w) in enumerate((('Ciclo', 8), ('Inicio (día 1)', 18), ('Preliminar hasta', 18),
                                        ('Borrador hasta', 18), ('Pedido (FINAL)', 18), ('Estado', 14))):
                label(cab, t.upper(), 8, 'bold', TEXT_MUTED, width=w, anchor='w').grid(row=0, column=j, sticky='w')
            for c in calendario_ciclos(ancla, dur, hoy, extra=2):
                fr = tk.Frame(card.body, bg=SURFACE)
                fr.pack(fill='x', pady=S(3))
                actual = c['numero'] == n
                peso = 'bold' if actual else 'normal'
                for j, (v, w) in enumerate(((f'#{c["numero"]}', 8), (fecha_larga(c['inicio']), 18),
                                            (fecha_larga(c['preliminar_hasta']), 18),
                                            (fecha_larga(c['borrador_hasta']), 18), (fecha_larga(c['pedido']), 18))):
                    label(fr, v, 10, peso, width=w, anchor='w').grid(row=0, column=j, sticky='w')
                estado = ('EN CURSO', 'accent') if actual else (('CERRADO', 'neutral') if c['pedido'] < hoy
                                                               else ('PRÓXIMO', 'PRELIMINAR'))
                Chip(fr, estado[0], estado[1]).grid(row=0, column=5, sticky='w')
            label(card.body, 'PRELIMINAR: solo para ver tendencias · BORRADOR: valida cantidades con tu equipo · '
                             'FINAL: las cantidades están listas para convertirse en órdenes de compra.', 9,
                  color=TEXT_DIM, wraplength=S(760), justify='left').pack(anchor='w', pady=(S(12), 0))

    return _Tablero, _Calendario
