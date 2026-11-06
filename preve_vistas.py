# -*- coding: utf-8 -*-
"""
PREVÉ — Vistas de la interfaz (una por opción del menú lateral).
"""
import os
import datetime
import tkinter as tk
import numpy as np
import pandas as pd

import preve_ui as ui
from preve_ui import (S, f, Card, Btn, Chip, ProgressBar, Slider, ScrollFrame, Skeleton, label, entry, menu,
                      BG, SURFACE, TEXT, TEXT_DIM, TEXT_MUTED, ACCENT, ACCENT_TINT, CARD_ALT, BORDER,
                      OK, WARN, DANGER)
import preve_datos as dt
from preve_datos import fmt_num, fmt_soles, fecha_larga
# FIX: safe_int en vez de int() donde el valor puede ser NaN (antes ValueError rompía la vista)
from preve_utils import safe_int, get_logger

logger = get_logger('vistas')

PADX = 32
FILA_SEL = '#F3F8F5'


# ══════════════════════════════════════════════════════════════════
# Utilidades compartidas
# ══════════════════════════════════════════════════════════════════
def bind_click(w, fn):
    w.bind('<Button-1>', lambda e: fn())
    try:
        w.configure(cursor='hand2')
    except tk.TclError:
        pass
    for c in w.winfo_children():
        bind_click(c, fn)


def ruta_corta(ruta, n=70):
    return ruta if len(ruta) <= n else ruta[:3] + '…' + ruta[-(n - 4):]


def fmt_mmss(seg):
    seg = max(0, int(seg))
    return f'{seg // 60}:{seg % 60:02d}'


def acortar(txt, n=64):
    return txt if len(txt) <= n else txt[:n - 1] + '…'


def stat_card(parent, titulo, valor, sub='', color=TEXT):
    c = Card(parent, padx=18, pady=16)
    label(c.body, titulo.upper(), 8, 'bold', TEXT_MUTED).pack(anchor='w')
    label(c.body, valor, 21, 'bold', color, mono=True).pack(anchor='w', pady=(S(6), S(2)))
    if sub:
        label(c.body, sub, 9, color=TEXT_DIM, wraplength=S(190), justify='left').pack(anchor='w')
    return c


def fila_cards(parent, cards, padx=PADX, pady=(0, 14)):
    """Reparte tarjetas en columnas iguales. cards = lista de funciones(parent) -> widget."""
    fr = tk.Frame(parent, bg=BG)
    fr.pack(fill='x', padx=S(padx), pady=(S(pady[0]), S(pady[1])))
    for i, mk in enumerate(cards):
        fr.columnconfigure(i, weight=1, uniform='c')
        mk(fr).grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else S(7), 0 if i == len(cards) - 1 else S(7)))
    return fr


def seccion(parent, titulo, padx=PADX):
    label(parent, titulo, 12, 'bold').pack(anchor='w', padx=S(padx), pady=(S(10), S(8)))


def estado_vacio(parent, titulo, texto, boton=None, comando=None):
    bg = ui._bg(parent)
    fr = tk.Frame(parent, bg=bg)
    cv = tk.Canvas(fr, width=S(132), height=S(100), bg=bg, highlightthickness=0, bd=0)
    cv.pack(pady=(S(6), S(14)))
    ui.rrect(cv, S(6), S(58), S(46), S(94), S(5), fill=CARD_ALT, outline=BORDER)
    ui.rrect(cv, S(34), S(30), S(100), S(94), S(7), fill=ACCENT_TINT, outline=ACCENT)
    cv.create_line(S(34), S(48), S(100), S(48), fill=ACCENT)
    cv.create_line(S(60), S(48), S(60), S(62), fill=ACCENT)
    cv.create_line(S(74), S(48), S(74), S(62), fill=ACCENT)
    for x, h in ((100, 18), (110, 28), (120, 12)):
        cv.create_line(S(x), S(30 - 4), S(x), S(30 - 4 - h), fill=OK, width=S(4), capstyle='round')
    label(fr, titulo, 14, 'bold', bg=bg).pack()
    label(fr, texto, 10, color=TEXT_DIM, bg=bg, wraplength=S(420), justify='center').pack(pady=(S(6), S(16)))
    if boton:
        Btn(fr, boton, comando, 'primary', 'lg').pack()
    return fr


class Vista(tk.Frame):
    """Base de las vistas. Cada una se reconstruye en on_show()."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.build()

    def build(self):
        pass

    def on_show(self, **kw):
        pass

    def tecla(self, k):
        pass

    def encabezado(self, parent, titulo, subtitulo=''):
        fr = tk.Frame(parent, bg=BG)
        fr.pack(fill='x', padx=S(PADX), pady=(S(26), S(16)))
        izq = tk.Frame(fr, bg=BG)
        izq.pack(side='left', fill='x', expand=True)
        label(izq, titulo, 20, 'bold').pack(anchor='w')
        sub = label(izq, subtitulo, 10, color=TEXT_DIM)
        sub.pack(anchor='w', pady=(S(2), 0))
        fr.sub = sub
        return fr

    def limpiar(self, w):
        for c in w.winfo_children():
            c.destroy()


# ══════════════════════════════════════════════════════════════════
# INICIO — dashboard de resumen
# ══════════════════════════════════════════════════════════════════
class VistaInicio(Vista):
    def build(self):
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill='both', expand=True)
        self.cont = self.scroll.inner

    def on_show(self, **kw):
        self.limpiar(self.cont)
        app, d = self.app, self.app.datos
        h = datetime.datetime.now().hour
        saludo = 'Buenos días' if h < 12 else ('Buenas tardes' if h < 19 else 'Buenas noches')
        enc = self.encabezado(self.cont, f'{saludo}, {app.cfg["usuario"]}',
                              f'Así va tu inventario en {app.sucursal}.')
        Btn(enc, 'Generar nueva recomendación', app.ir_generar, 'primary', 'lg').pack(side='right')

        if not d.tiene_datos:
            card = Card(self.cont, padx=40, pady=40)
            card.pack(fill='x', padx=S(PADX), pady=(S(4), S(20)))
            estado_vacio(card.body, 'Aún no has generado ninguna recomendación',
                         'PREVÉ analiza tus reportes del ERP y te dice qué comprar, cuánto y cuándo. '
                         'Empecemos por elegir tus carpetas.', 'Empecemos →', app.ir_generar).pack()
            return

        k = d.kpis()
        ci = d.info_ciclo()
        comp = d.comparacion
        actual = comp['actual'] if comp else None

        # ── Ciclo de compras ──
        if ci:
            card = Card(self.cont, padx=20, pady=16)
            card.pack(fill='x', padx=S(PADX), pady=(0, S(14)))
            top = tk.Frame(card.body, bg=SURFACE)
            top.pack(fill='x')
            label(top, f'Ciclo de compras #{ci["numero"]}', 11, 'bold').pack(side='left')
            if actual and actual['etiqueta']:
                Chip(top, actual['etiqueta'], actual['etiqueta']).pack(side='left', padx=(S(10), 0))
            label(top, f'Día {ci["dia"]} de {ci["dias_totales"]}', 10, color=TEXT_DIM, mono=True).pack(side='right')
            barra = ProgressBar(card.body, 8)
            barra.pack(fill='x', pady=(S(10), S(8)))
            color = DANGER if ci['vencido'] else (WARN if ci['dias_restantes'] <= 14 else ACCENT)
            barra.set(ci['dia'] / ci['dias_totales'], color)
            txt = (f'La fecha límite del pedido ({fecha_larga(ci["limite"])}) ya pasó — conviene cerrar el pedido.'
                   if ci['vencido'] else
                   f'Fecha límite del pedido internacional: {fecha_larga(ci["limite"])} · '
                   f'faltan {ci["dias_restantes"]} días.')
            label(card.body, txt, 9, color=TEXT_DIM).pack(anchor='w')

        # ── 4 tarjetas resumen ──
        fecha_ej = None
        if actual and actual.get('fecha'):
            try:
                fecha_ej = datetime.datetime.fromisoformat(actual['fecha'])
            except ValueError:
                pass
        fecha_ej = fecha_ej or d.fecha_datos
        sub_ult = fecha_ej.strftime('%d/%m/%Y %H:%M') if fecha_ej else ''
        urg = k['urgentes_n']
        fila_cards(self.cont, [
            lambda p: stat_card(p, 'Última recomendación', dt.hace_texto(fecha_ej), sub_ult),
            lambda p: stat_card(p, 'Inversión sugerida pendiente', fmt_soles(k['inversion_pendiente']),
                                f'de {fmt_soles(k["inversion_total"])} recomendados en total', ACCENT),
            lambda p: stat_card(p, 'Productos a recomprar', fmt_num(k['pendientes_n']),
                                f'{urg} urgentes · {k["recomprar_n"]} en total',
                                DANGER if urg else TEXT),
            lambda p: stat_card(p, 'Días hasta la fecha límite', fmt_num(ci['dias_restantes']) if ci else '—',
                                fecha_larga(ci['limite']) if ci else 'Sin ciclo registrado',
                                WARN if ci and ci['dias_restantes'] <= 14 else TEXT),
        ])

        # ── Qué hacer ahora ──
        acc = tk.Frame(self.cont, bg=BG)
        acc.pack(fill='x', padx=S(PADX), pady=(0, S(6)))
        Btn(acc, f'Revisar recomendaciones ({k["pendientes_n"]})', lambda: app.ir('recom'), 'soft', 'md'
            ).pack(side='left')
        Btn(acc, 'Decisión rápida · top 20', lambda: app.ir('decision'), 'secondary', 'md'
            ).pack(side='left', padx=(S(10), 0))
        binf = Btn(acc, 'Informe para gerencia ▾', None, 'secondary', 'md')
        binf.set_command(lambda: app.menu_informe(binf))
        binf.pack(side='left', padx=(S(10), 0))
        Btn(acc, 'Abrir carpeta de resultados', app.abrir_carpeta_resultados, 'ghost', 'md'
            ).pack(side='left', padx=(S(10), 0))

        # ── Salud del inventario ──
        seccion(self.cont, 'Salud del inventario')
        rot = k['rotacion']
        pr = k['pct_riesgo']
        fila_cards(self.cont, [
            lambda p: stat_card(p, 'Rotación estimada', f'{rot:.1f}×' if rot is not None else '—',
                                'veces al año, valorizada a costo'),
            lambda p: stat_card(p, 'En riesgo de quiebre', f'{pr:.0f}%' if pr is not None else '—',
                                f'{k["riesgo_n"]} productos con stock para menos de {dt.LEAD} días',
                                DANGER if pr and pr >= 20 else TEXT),
            lambda p: stat_card(p, 'Capital inmovilizado', fmt_soles(k['capital_muerto']),
                                'en productos a LIQUIDAR', WARN if k['capital_muerto'] else TEXT),
            lambda p: stat_card(p, 'Margen esperado', fmt_soles(k['margen_esperado']),
                                f'próximos {dt.HORIZONTE_DIAS} días, sin IGV, limitado por stock'),
        ])

        # ── Comparación con el análisis anterior ──
        seccion(self.cont, 'Comparado con tu análisis anterior')
        card = Card(self.cont, padx=20, pady=16)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(24)))
        previa = comp['previa'] if comp else None
        if not previa:
            label(card.body, 'Este es tu primer análisis. Cuando generes el siguiente, aquí verás cuánto cambió '
                             'la inversión y qué productos cambiaron de cantidad.', 10, color=TEXT_DIM,
                  wraplength=S(760), justify='left').pack(anchor='w')
        else:
            a, b = actual['inversion'], previa['inversion']
            pct = (a - b) / b * 100 if b else None
            pct_t = f' ({pct:+.0f}%)' if pct is not None else ''
            try:
                f_prev = datetime.datetime.fromisoformat(previa['fecha']).strftime('%d/%m')
            except ValueError:
                f_prev = ''
            label(card.body, f'Este análisis recomienda {fmt_soles(a)}. El análisis anterior ({f_prev}) fue '
                             f'{fmt_soles(b)}{pct_t}.', 11, 'bold', wraplength=S(760), justify='left'
                  ).pack(anchor='w')
            label(card.body, f'Productos a recomprar: {actual["recomprar"]} ahora · {previa["recomprar"]} antes.',
                  10, color=TEXT_DIM).pack(anchor='w', pady=(S(4), S(10)))
            cambios = []
            for r in d.recompras().to_dict('records'):
                antes = d.previa_unidades(r['sku'])
                ahora = safe_int(r['compra_sugerida_unid'])
                if antes is not None and safe_int(antes) != ahora:
                    cambios.append((abs(ahora - safe_int(antes)), r, safe_int(antes), ahora))
            cambios.sort(key=lambda x: -x[0])
            if cambios:
                label(card.body, 'MAYORES CAMBIOS', 8, 'bold', TEXT_MUTED).pack(anchor='w', pady=(0, S(4)))
                for _, r, antes, ahora in cambios[:4]:
                    fr = tk.Frame(card.body, bg=SURFACE)
                    fr.pack(fill='x', pady=S(2))
                    label(fr, acortar(r['nombre'], 52), 10).pack(side='left')
                    label(fr, f'{ahora - antes:+d}', 10, 'bold', DANGER if ahora > antes else OK,
                          mono=True).pack(side='right')
                    label(fr, f'Ahora {ahora} u · Antes {antes} u    ', 9, color=TEXT_DIM, mono=True
                          ).pack(side='right')


# ══════════════════════════════════════════════════════════════════
# GENERAR
# ══════════════════════════════════════════════════════════════════
class VistaGenerar(Vista):
    TIPOS = [('Listado de productos', ('listado', 'producto')), ('Ventas', ('venta',)),
             ('Compras', ('compra',)), ('Movimientos de almacén', ('movimiento',))]

    def build(self):
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill='both', expand=True)
        self.cont = self.scroll.inner

    def on_show(self, **kw):
        self.limpiar(self.cont)
        app = self.app
        self.encabezado(self.cont, 'Generar recomendación',
                        f'Elige tus reportes del ERP y PREVÉ calcula qué comprar para {app.sucursal}.')
        for n, (clave, titulo, ayuda) in enumerate((
                ('entrada', 'Paso 1 · Carpeta con tus reportes del ERP',
                 'Los mismos .xlsx que exportas siempre: productos, ventas, compras y movimientos, todos juntos.'),
                ('salida', 'Paso 2 · Carpeta donde guardar los resultados',
                 'Aquí quedan el Excel, el dashboard y el historial. Usa siempre la misma para comparar ciclos.'))):
            card = Card(self.cont, padx=20, pady=16)
            card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
            label(card.body, titulo, 11, 'bold').pack(anchor='w')
            label(card.body, ayuda, 9, color=TEXT_DIM, wraplength=S(700), justify='left').pack(anchor='w',
                                                                                              pady=(S(2), S(10)))
            fila = tk.Frame(card.body, bg=SURFACE)
            fila.pack(fill='x')
            ruta = app.carpeta(clave)
            caja = tk.Frame(fila, bg=BG, highlightthickness=1, highlightbackground=BORDER)
            caja.pack(side='left', fill='x', expand=True)
            label(caja, ruta_corta(ruta) if ruta else 'Ninguna carpeta seleccionada', 10, color=TEXT if ruta else TEXT_MUTED,
                  mono=bool(ruta), bg=BG, anchor='w').pack(fill='x', padx=S(12), pady=S(8))
            Btn(fila, 'Elegir carpeta…', lambda c=clave: self._elegir(c), 'secondary', 'md'
                ).pack(side='left', padx=(S(10), 0))
            if clave == 'entrada' and ruta:
                chips = tk.Frame(card.body, bg=SURFACE)
                chips.pack(fill='x', pady=(S(10), 0))
                faltan = []
                for nombre, claves in self.TIPOS:
                    ok = self._hay_archivo(ruta, claves)
                    Chip(chips, ('✓ ' if ok else '✗ ') + nombre, 'MONITOREAR' if ok else 'URGENTE'
                         ).pack(side='left', padx=(0, S(6)))
                    if not ok:
                        faltan.append(nombre)
                if faltan:
                    label(card.body, 'Faltan reportes en esta carpeta; el análisis puede fallar.', 9, color=WARN
                          ).pack(anchor='w', pady=(S(6), 0))

        self._card_fuentes()

        lista = tk.Frame(self.cont, bg=BG)
        lista.pack(fill='x', padx=S(PADX), pady=(S(6), S(24)))
        Btn(lista, 'Generar recomendación', app.generar, 'primary', 'lg', min_width=300).pack(side='left')
        seg = app.cfg.get('ultima_duracion_s')
        txt = (f'La última vez tardó {fmt_mmss(seg)}.' if seg else
               'Puede tardar varios minutos: entrena 3 modelos de pronóstico con tus datos reales.')
        label(lista, txt, 9, color=TEXT_DIM).pack(side='left', padx=(S(14), 0))

    def _card_fuentes(self):
        """Fase 4: importación manual siempre disponible; descarga automática solo si hay plugin configurado."""
        from plugins import obtener_plugin
        app = self.app
        plugin = obtener_plugin(dt.Config.actual().get('plugin_erp'))
        card = Card(self.cont, padx=20, pady=16)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        label(card.body, 'Otras fuentes de datos', 11, 'bold').pack(anchor='w')
        label(card.body, 'Importa ventas de cualquier sistema (Excel o CSV) a la base de PREVÉ'
              + (f', o descárgalas directamente desde {plugin.nombre}.' if plugin else
                 '. Para descargar automáticamente desde tu ERP, actívalo en Configuración.'),
              9, color=TEXT_DIM, wraplength=S(700), justify='left').pack(anchor='w', pady=(S(2), S(10)))
        fila = tk.Frame(card.body, bg=SURFACE)
        fila.pack(fill='x')
        Btn(fila, 'Importar ventas desde archivo…', app.abrir_importador, 'secondary', 'md').pack(side='left')
        if plugin:
            Btn(fila, f'Descargar desde {plugin.nombre}', app.descargar_desde_erp, 'soft', 'md').pack(
                side='left', padx=(S(10), 0))
            if not plugin.credenciales_completas():
                label(card.body, f'⚠ Faltan las credenciales de {plugin.nombre}: guárdalas en Configuración › '
                                 'Integración con tu ERP (o en el archivo .env).', 9,
                      color=WARN).pack(anchor='w', pady=(S(6), 0))

    def _elegir(self, clave):
        if self.app.elegir_carpeta(clave):
            self.on_show()

    def _hay_archivo(self, carpeta, claves):
        n = 0
        for raiz, _, archivos in os.walk(carpeta):
            for a in archivos:
                n += 1
                nom = a.lower()
                if nom.endswith(('.xlsx', '.xls')) and not nom.startswith('~$') and all(c in nom for c in claves):
                    return True
            if n > 800:
                break
        return False


# ══════════════════════════════════════════════════════════════════
# RECOMENDACIONES — bandeja de entrada
# ══════════════════════════════════════════════════════════════════
FILTROS = [('pend', 'Pendientes'), ('urg', 'Urgentes'), ('pronto', 'Pronto'), ('mon', 'Monitorear'),
           ('comp', 'Comprados'), ('posp', 'Pospuestos'), ('desc', 'Descartados'), ('todo', 'Todos')]
PAGINA = 40
RAZONES = ['Sin presupuesto', 'Tengo stock en otra sucursal', 'Proveedor sin disponibilidad',
           'Ya no lo vendo']


class VistaRecom(Vista):
    def build(self):
        self.filtro, self.limite = 'pend', PAGINA
        self.abiertos, self.filas = set(), {}
        self.texto = tk.StringVar()
        self._debounce = None
        enc = self.encabezado(self, 'Recomendaciones', '')
        self.lbl_sub = enc.sub
        Btn(enc, '⚡ Decisión rápida', lambda: self.app.ir('decision'), 'soft', 'md').pack(side='right')
        caja = tk.Frame(enc, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        caja.pack(side='right', padx=(0, S(10)))
        label(caja, '⌕', 12, color=TEXT_MUTED, bg=SURFACE).pack(side='left', padx=(S(10), 0))
        self.ent = tk.Entry(caja, textvariable=self.texto, width=22, font=f(10), bg=SURFACE, fg=TEXT, relief='flat',
                            bd=0, highlightthickness=0, insertbackground=TEXT)
        self.ent.pack(side='left', ipady=S(8), padx=(S(6), S(10)))
        self.ent.bind('<FocusIn>', lambda e: caja.configure(highlightbackground=ACCENT))
        self.ent.bind('<FocusOut>', lambda e: caja.configure(highlightbackground=BORDER))
        self.texto.trace_add('write', self._al_escribir)

        barra = tk.Frame(self, bg=BG)
        barra.pack(fill='x', padx=S(PADX), pady=(0, S(10)))
        self.frame_filtros = tk.Frame(barra, bg=BG)
        self.frame_filtros.pack(side='left')

        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill='both', expand=True)
        self.cont = self.scroll.inner

    def _al_escribir(self, *a):
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(250, self._refiltrar)

    def _refiltrar(self):
        self.limite = PAGINA
        self.render()

    def on_show(self, sku=None, filtro=None, **kw):
        if filtro:
            self.filtro = filtro
        self.abiertos = set()
        self.limite = PAGINA
        if sku:
            self.filtro = 'todo'
            self.texto.set('')
            self.abiertos = {str(sku)}
            ids = list(self._filtrar()['sku'])
            if str(sku) in ids:
                self.limite = max(PAGINA, ids.index(str(sku)) + 5)
        self.render()
        if sku and str(sku) in self.filas:
            self.after(60, lambda: self.scroll.ver_widget(self.filas[str(sku)]))

    # ── filtros ────────────────────────────────────────────
    def _mascara(self, clave, df, est):
        if clave == 'pend':
            return df['es_compra'] & (est == 'pendiente')
        if clave == 'urg':
            return (df['urgencia'] == 'URGENTE') & (est == 'pendiente')
        if clave == 'pronto':
            return (df['urgencia'] == 'PRONTO') & (est == 'pendiente')
        if clave == 'mon':
            return ~df['es_compra']
        if clave == 'comp':
            return est == 'comprado'
        if clave == 'posp':
            return est == 'pospuesto'
        if clave == 'desc':
            return est == 'descartado'
        return pd.Series(True, index=df.index)

    def _filtrar(self):
        d = self.app.datos
        df = d.df
        est = df['sku'].map(d.estado)
        sub = df[self._mascara(self.filtro, df, est)]
        q = self.texto.get().strip().lower()
        if q:
            hay = sub['sku'].str.lower() + ' ' + sub['nombre'].str.lower() + ' ' + sub['proveedor_principal'].str.lower()
            sub = sub[hay.str.contains(q, regex=False)]
        return sub.sort_values(['urgencia_ord', 'costo_total_compra_pen', 'dias_stock'],
                               ascending=[True, False, True], na_position='last')

    # ── dibujo ─────────────────────────────────────────────
    def render(self):
        app, d = self.app, self.app.datos
        y = self.scroll.canvas.yview()[0]
        self.limpiar(self.cont)
        self.limpiar(self.frame_filtros)
        self.filas = {}
        if not d.tiene_datos:
            self.lbl_sub.config(text='')
            estado_vacio(self.cont, 'Aún no hay recomendaciones',
                         'Genera tu primera recomendación y aquí aparecerá cada producto para que decidas.',
                         'Empecemos →', app.ir_generar).pack(pady=S(40))
            return
        df = d.df
        est = df['sku'].map(d.estado)
        for clave, nombre in FILTROS:
            n = int(self._mascara(clave, df, est).sum())
            if clave in ('comp', 'posp', 'desc') and n == 0 and self.filtro != clave:
                continue
            Btn(self.frame_filtros, f'{nombre} · {n}', lambda c=clave: self._set_filtro(c),
                'soft' if clave == self.filtro else 'ghost', 'sm').pack(side='left', padx=(0, S(4)))
        k = d.kpis()
        self.lbl_sub.config(text=f'{k["pendientes_n"]} pendientes por decidir · inversión pendiente '
                                 f'{fmt_soles(k["inversion_pendiente"])}')

        sub = self._filtrar()
        if sub.empty:
            vacio = {'pend': ('¡Todo al día!', 'No tienes recomendaciones pendientes. Buen trabajo.'),
                     'urg': ('Nada urgente', 'No hay productos con quiebre inminente.')}.get(
                self.filtro, ('Sin resultados', 'No hay productos con este filtro.'))
            estado_vacio(self.cont, vacio[0], vacio[1]).pack(pady=S(40))
            return

        caja = tk.Frame(self.cont, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        caja.pack(fill='x', padx=S(PADX), pady=(0, S(10)))
        cab = tk.Frame(caja, bg=CARD_ALT)
        cab.pack(fill='x')
        self._grid_cols(cab)
        for c, (t, anc) in enumerate((('PRIORIDAD', 'w'), ('PRODUCTO', 'w'), ('CANTIDAD', 'e'), ('INVERSIÓN', 'e'),
                                      ('ESTADO', 'w'))):
            label(cab, t, 8, 'bold', TEXT_DIM, bg=CARD_ALT).grid(row=0, column=c, sticky=anc, padx=S(8), pady=S(7))
        filas = sub.head(self.limite).to_dict('records')
        for r in filas:
            self._fila(caja, r)
        if len(sub) > self.limite:
            Btn(self.cont, f'Mostrar {min(PAGINA, len(sub) - self.limite)} más  ({len(sub) - self.limite} restantes)',
                self._mas, 'secondary', 'md').pack(pady=(S(2), S(24)))
        else:
            tk.Frame(self.cont, bg=BG, height=S(24)).pack()
        self.update_idletasks()
        self.scroll.canvas.yview_moveto(y)

    def _mas(self):
        self.limite += PAGINA
        self.render()

    def _set_filtro(self, c):
        self.filtro, self.limite, self.abiertos = c, PAGINA, set()
        self.scroll.ir_arriba()
        self.render()

    def _grid_cols(self, fr):
        for c, ms in enumerate((S(120), 0, S(86), S(110), S(116), S(34))):
            fr.columnconfigure(c, weight=1 if c == 1 else 0, minsize=ms)

    def _fila(self, padre, r):
        d = self.app.datos
        sku = r['sku']
        abierto = sku in self.abiertos
        estado = d.estado(sku)
        bg = FILA_SEL if abierto else SURFACE
        cont = tk.Frame(padre, bg=bg)
        cont.pack(fill='x')
        self.filas[sku] = cont
        cab = tk.Frame(cont, bg=bg)
        cab.pack(fill='x')
        self._grid_cols(cab)
        es_compra = bool(r['es_compra'])
        if es_compra:
            Chip(cab, {'URGENTE': 'Urgente', 'PRONTO': 'Pronto', 'MONITOREAR': 'Monitorear'}[r['urgencia']],
                 r['urgencia']).grid(row=0, column=0, sticky='w', padx=S(8), pady=S(11))
        else:
            Chip(cab, r['accion'].capitalize(), r['accion']).grid(row=0, column=0, sticky='w', padx=S(8), pady=S(11))
        nom = tk.Frame(cab, bg=bg)
        nom.grid(row=0, column=1, sticky='w', padx=S(8))
        label(nom, acortar(r['nombre'] or r['sku']), 10, 'bold', bg=bg).pack(anchor='w')
        extra = ' · '.join(x for x in (r['proveedor_principal'], r['categoria']) if x)
        sub = tk.Frame(nom, bg=bg)
        sub.pack(anchor='w')
        label(sub, r['sku'], 8, color=TEXT_MUTED, mono=True, bg=bg).pack(side='left')
        if extra:
            label(sub, '  ' + acortar(extra, 48), 8, color=TEXT_DIM, bg=bg).pack(side='left')
        cant = int(r['compra_sugerida_unid']) if es_compra and pd.notna(r['compra_sugerida_unid']) else None
        label(cab, f'{cant:,} u' if cant else '—', 10, 'bold', mono=True, bg=bg).grid(row=0, column=2, sticky='e',
                                                                                      padx=S(8))
        label(cab, fmt_soles(r['costo_total_compra_pen']) if es_compra else '—', 10, mono=True, bg=bg
              ).grid(row=0, column=3, sticky='e', padx=S(8))
        if estado != 'pendiente':
            Chip(cab, estado.capitalize(), estado).grid(row=0, column=4, sticky='w', padx=S(8))
        label(cab, '▴' if abierto else '▾', 10, color=TEXT_MUTED, bg=bg).grid(row=0, column=5, padx=S(8))
        bind_click(cab, lambda s=sku: self._toggle(s))
        if abierto:
            self._detalle(cont, r, estado, bg)
        tk.Frame(cont, bg=BORDER, height=1).pack(fill='x')

    def _toggle(self, sku):
        if sku in self.abiertos:
            self.abiertos.discard(sku)
        else:
            self.abiertos.add(sku)
        self.render()

    # ── detalle expandido ──────────────────────────────────
    def _detalle(self, cont, r, estado, bg):
        d = self.app.datos
        sku = r['sku']
        det = tk.Frame(cont, bg=bg)
        det.pack(fill='x', padx=S(16), pady=(0, S(14)))
        izq = tk.Frame(det, bg=bg)
        izq.pack(side='left', fill='both', expand=True)
        der = tk.Frame(det, bg=bg)
        der.pack(side='right', fill='y', padx=(S(24), 0))

        label(izq, 'POR QUÉ', 8, 'bold', TEXT_MUTED, bg=bg).pack(anchor='w')
        label(izq, dt.explicar(r, d.fecha_ref), 10, bg=bg, wraplength=S(520), justify='left'
              ).pack(anchor='w', pady=(S(2), S(10)))
        datos = tk.Frame(izq, bg=bg)
        datos.pack(anchor='w', pady=(0, S(12)))
        dias = r['dias_stock']
        hechos = [('Stock actual', fmt_num(r['stock_actual'])),
                  ('Venta esperada', f'{r["venta_sem"]:.1f} /sem'),
                  ('Alcanza para', '—' if np.isinf(dias) else f'{dias:.0f} días'),
                  ('Costo unit.', fmt_soles(r['costo_puesto_almacen_pen'], 2))]
        for i, (t, v) in enumerate(hechos):
            col = tk.Frame(datos, bg=bg)
            col.grid(row=0, column=i, padx=(0, S(26)), sticky='w')
            label(col, t.upper(), 8, 'bold', TEXT_MUTED, bg=bg).pack(anchor='w')
            label(col, v, 11, 'bold', mono=True, bg=bg).pack(anchor='w')

        # derecha: confianza + comparación
        label(der, 'CONFIANZA DEL PRONÓSTICO', 8, 'bold', TEXT_MUTED, bg=bg).pack(anchor='w')
        Chip(der, {'ALTA': 'Alta', 'MEDIA': 'Media', 'BAJA': 'Baja'}[r['confianza']], r['confianza']
             ).pack(anchor='w', pady=(S(3), S(3)))
        label(der, dt.confianza_motivo(r), 9, color=TEXT_DIM, bg=bg, wraplength=S(250), justify='left'
              ).pack(anchor='w')
        prec = d.precision_historica()
        label(der, f'Precisión histórica global: {prec:.0f}%' if prec is not None
              else 'Precisión histórica: aún sin validar con ventas reales', 9, color=TEXT_DIM, bg=bg,
              wraplength=S(250), justify='left').pack(anchor='w', pady=(S(2), S(10)))
        if r['es_compra']:
            antes = d.previa_unidades(sku)
            ahora = safe_int(r['compra_sugerida_unid'])
            label(der, 'VS. ANÁLISIS ANTERIOR', 8, 'bold', TEXT_MUTED, bg=bg).pack(anchor='w')
            if antes is None:
                label(der, f'Este análisis: {ahora} u · antes: sin recomendación', 10, bg=bg, mono=True
                      ).pack(anchor='w')
            else:
                label(der, f'Ahora {ahora} u · Antes {safe_int(antes)} u ({ahora - safe_int(antes):+d})', 10, bg=bg,
                      mono=True).pack(anchor='w')

        # acciones
        if not r['es_compra']:
            return
        acc = tk.Frame(cont, bg=bg)
        acc.pack(fill='x', padx=S(16), pady=(0, S(14)))
        if estado == 'pendiente':
            Btn(acc, '✓ Ya lo compré', lambda: self._decidir(sku, 'comprado'), 'primary', 'sm'
                ).pack(side='left')
            b = Btn(acc, 'Posponer ▾', None, 'secondary', 'sm')
            b.set_command(lambda: self._menu_posponer(sku, b))
            b.pack(side='left', padx=S(8))
            Btn(acc, 'No lo voy a comprar…', lambda: self._pedir_razon(sku, cont, bg), 'danger', 'sm'
                ).pack(side='left')
        else:
            info = d.decisiones.get(sku, {})
            txt = {'comprado': 'Marcado como comprado', 'pospuesto': 'Pospuesto', 'descartado': 'Descartado'}[estado]
            if info.get('hasta') and estado == 'pospuesto':
                txt += f' hasta el {fecha_larga(datetime.date.fromisoformat(info["hasta"]))}'
            if info.get('razon'):
                txt += f' — {info["razon"]}'
            label(acc, txt, 10, color=TEXT_DIM, bg=bg).pack(side='left')
            Btn(acc, 'Deshacer', lambda: self._deshacer(sku), 'ghost', 'sm').pack(side='left', padx=S(10))

    def _menu_posponer(self, sku, btn):
        m = menu(self)
        for dias, txt in ((3, 'Posponer 3 días'), (7, 'Posponer 1 semana'), (14, 'Posponer 2 semanas')):
            m.add_command(label=txt, command=lambda n=dias: self._decidir(sku, 'pospuesto', dias=n))
        m.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())

    def _pedir_razon(self, sku, cont, bg):
        fr = tk.Frame(cont, bg=bg)
        fr.pack(fill='x', padx=S(16), pady=(0, S(14)))
        label(fr, '¿Por qué no lo vas a comprar? (opcional)', 9, color=TEXT_DIM, bg=bg).pack(anchor='w')
        var = tk.StringVar()
        fila = tk.Frame(fr, bg=bg)
        fila.pack(fill='x', pady=(S(4), S(6)))
        e = entry(fila, var, width=44)
        e.pack(side='left', ipady=S(5))
        Btn(fila, 'Confirmar', lambda: self._decidir(sku, 'descartado', var.get().strip()), 'primary', 'sm'
            ).pack(side='left', padx=S(8))
        rap = tk.Frame(fr, bg=bg)
        rap.pack(fill='x')
        for rz in RAZONES:
            Btn(rap, rz, lambda t=rz: var.set(t), 'ghost', 'sm').pack(side='left', padx=(0, S(4)))
        e.focus_set()

    def _decidir(self, sku, estado, razon='', dias=7):
        d = self.app.datos
        d.decidir(sku, estado, razon, dias)
        self.abiertos.discard(sku)
        self.render()
        txt = {'comprado': 'Marcado como comprado', 'pospuesto': f'Pospuesto {dias} días',
               'descartado': 'Descartado'}[estado]
        self.app.toast(txt, 'ok', accion=('Deshacer', lambda: self._deshacer(sku)))
        self.app.actualizar_badge()

    def _deshacer(self, sku):
        self.app.datos.deshacer(sku)
        self.render()
        self.app.actualizar_badge()


# ══════════════════════════════════════════════════════════════════
# DECISIÓN RÁPIDA — top 20 de mayor impacto, uno por uno
# ══════════════════════════════════════════════════════════════════
class VistaDecision(Vista):
    def build(self):
        self.cont = tk.Frame(self, bg=BG)
        self.cont.pack(fill='both', expand=True)
        self.cola, self.i, self.compradas, self.saltadas = [], 0, [], 0

    def on_show(self, **kw):
        d = self.app.datos
        self.cola = d.top_impacto(20).to_dict('records') if d.tiene_datos else []
        self.i, self.compradas, self.saltadas = 0, [], 0
        self.render()

    def tecla(self, k):
        if self.i < len(self.cola):
            if k == 'Right':
                self.comprar()
            elif k == 'Left':
                self.saltar()

    def render(self):
        self.limpiar(self.cont)
        app, d = self.app, self.app.datos
        enc = tk.Frame(self.cont, bg=BG)
        enc.pack(fill='x', padx=S(PADX), pady=(S(26), S(8)))
        label(enc, 'Decisión rápida', 20, 'bold').pack(anchor='w')
        label(enc, 'Las 20 compras de mayor impacto, una por una. Saltar no descarta: el producto sigue pendiente.',
              10, color=TEXT_DIM).pack(anchor='w', pady=(S(2), 0))
        if not d.tiene_datos or not self.cola:
            estado_vacio(self.cont, '¡Nada pendiente!' if d.tiene_datos else 'Aún no hay recomendaciones',
                         'No tienes compras pendientes por decidir.' if d.tiene_datos else
                         'Genera una recomendación para usar este modo.',
                         None if d.tiene_datos else 'Empecemos →', None if d.tiene_datos else app.ir_generar
                         ).pack(pady=S(50))
            return
        if self.i >= len(self.cola):
            self._resumen()
            return
        r = self.cola[self.i]
        wrap = tk.Frame(self.cont, bg=BG)
        wrap.pack(pady=(S(8), 0))
        card = Card(wrap, padx=30, pady=26, width=S(640))
        card.pack()
        b = card.body
        top = tk.Frame(b, bg=SURFACE)
        top.pack(fill='x')
        label(top, f'Producto {self.i + 1} de {len(self.cola)}', 9, color=TEXT_DIM).pack(side='left')
        Chip(top, {'URGENTE': 'Urgente', 'PRONTO': 'Pronto', 'MONITOREAR': 'Monitorear'}[r['urgencia']],
             r['urgencia']).pack(side='right')
        barra = ProgressBar(b, 6)
        barra.pack(fill='x', pady=(S(8), S(16)))
        barra.set(self.i / len(self.cola))
        label(b, r['nombre'] or r['sku'], 16, 'bold', wraplength=S(560), justify='left').pack(anchor='w')
        label(b, f'{r["sku"]}  ·  {r["proveedor_principal"] or "Sin proveedor"}', 9, color=TEXT_MUTED,
              mono=True).pack(anchor='w', pady=(S(2), S(16)))
        nums = tk.Frame(b, bg=SURFACE)
        nums.pack(fill='x')
        for i, (t, v, c) in enumerate((('Comprar', f'{safe_int(r["compra_sugerida_unid"]):,} u', TEXT),
                                       ('Inversión', fmt_soles(r['costo_total_compra_pen']), ACCENT),
                                       ('Stock hoy', fmt_num(r['stock_actual']), TEXT))):
            col = tk.Frame(nums, bg=SURFACE)
            col.pack(side='left', padx=(0, S(36)))
            label(col, t.upper(), 8, 'bold', TEXT_MUTED).pack(anchor='w')
            label(col, v, 20, 'bold', c, mono=True).pack(anchor='w')
        label(b, dt.explicar(r, d.fecha_ref), 10, color=TEXT_DIM, wraplength=S(560), justify='left'
              ).pack(anchor='w', pady=(S(16), S(20)))
        botones = tk.Frame(b, bg=SURFACE)
        botones.pack(fill='x')
        botones.columnconfigure((0, 1), weight=1, uniform='b')
        Btn(botones, '✗  Saltar   ←', self.saltar, 'secondary', 'lg').grid(row=0, column=0, sticky='ew',
                                                                          padx=(0, S(6)))
        Btn(botones, '✓  Comprar   →', self.comprar, 'primary', 'lg').grid(row=0, column=1, sticky='ew',
                                                                          padx=(S(6), 0))

    def comprar(self):
        r = self.cola[self.i]
        self.app.datos.decidir(r['sku'], 'comprado')
        self.compradas.append(r)
        self.i += 1
        self.app.actualizar_badge()
        self.render()

    def saltar(self):
        self.saltadas += 1
        self.i += 1
        self.render()

    def _resumen(self):
        inv = sum(r['costo_total_compra_pen'] for r in self.compradas)
        wrap = tk.Frame(self.cont, bg=BG)
        wrap.pack(pady=S(30))
        card = Card(wrap, padx=40, pady=34, width=S(560))
        card.pack()
        label(card.body, '¡Listo!', 18, 'bold').pack()
        label(card.body, f'Marcaste {len(self.compradas)} compras por {fmt_soles(inv)} y saltaste {self.saltadas}.',
              11, color=TEXT_DIM, wraplength=S(460), justify='center').pack(pady=(S(6), S(18)))
        fila = tk.Frame(card.body, bg=SURFACE)
        fila.pack()
        Btn(fila, 'Ver recomendaciones', lambda: self.app.ir('recom'), 'primary', 'md').pack(side='left')
        Btn(fila, 'Ir al inicio', lambda: self.app.ir('inicio'), 'secondary', 'md').pack(side='left',
                                                                                         padx=(S(8), 0))


# ══════════════════════════════════════════════════════════════════
# SIMULAR — sliders en vez de cajas numéricas
# ══════════════════════════════════════════════════════════════════
class VistaSimular(Vista):
    @property
    def PARAMS(self):
        # FIX (Fase 3): lead time y horizonte actuales desde la configuración (se leen al mostrar la vista)
        lt, hz = dt.lead_time(), dt.horizonte()
        return [
            dict(k='demanda', t='Cambio en la demanda', a='¿Y si vendes más o menos de lo previsto?',
                 lo=-50, hi=100, paso=5, ini=0, fmt=lambda v: f'{v:+d}%' if v else '0%'),
            dict(k='leadtime', t='Días que tarda en llegar la mercadería', a=f'Hoy son {lt} días. ¿Y si tarda más o menos?',
                 lo=5, hi=120, paso=1, ini=lt, fmt=lambda v: f'{v} días'),
            dict(k='horizonte', t='Días de cobertura que quieres tener', a=f'Hoy cubres {hz} días.',
                 lo=30, hi=300, paso=1, ini=hz, fmt=lambda v: f'{v} días'),
            dict(k='costo', t='Cambio en los costos de compra', a='Ej: +10% si el proveedor sube precios.',
                 lo=-30, hi=50, paso=1, ini=0, fmt=lambda v: f'{v:+d}%' if v else '0%'),
        ]

    def build(self):
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill='both', expand=True)
        self.cont = self.scroll.inner
        self.valores = {p['k']: p['ini'] for p in self.PARAMS}
        self.ocupado = False

    def on_show(self, **kw):
        self.limpiar(self.cont)
        self.sliders, self.lbl_val = {}, {}
        self.encabezado(self.cont, '¿Qué pasaría si…?',
                        'Mueve los controles y mira cómo cambiaría tu compra. No modifica nada: solo simula.')
        card = Card(self.cont, padx=22, pady=14)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        for p in self.PARAMS:
            fila = tk.Frame(card.body, bg=SURFACE)
            fila.pack(fill='x', pady=S(9))
            fila.columnconfigure(1, weight=1)
            izq = tk.Frame(fila, bg=SURFACE, width=S(270))
            izq.grid(row=0, column=0, sticky='w')
            izq.pack_propagate(False)
            izq.configure(height=S(46))
            label(izq, p['t'], 10, 'bold').pack(anchor='w')
            label(izq, p['a'], 9, color=TEXT_DIM, wraplength=S(260), justify='left').pack(anchor='w')
            s = Slider(fila, p['lo'], p['hi'], self.valores[p['k']], p['paso'],
                       command=lambda v, k=p['k']: self._cambio(k, v))
            s.grid(row=0, column=1, sticky='ew', padx=S(14))
            lv = label(fila, '', 11, 'bold', mono=True, width=9, anchor='e')
            lv.grid(row=0, column=2)
            Btn(fila, 'Restablecer', lambda k=p['k']: self._reset(k), 'ghost', 'sm').grid(row=0, column=3,
                                                                                         padx=(S(6), 0))
            self.sliders[p['k']], self.lbl_val[p['k']] = s, lv
            self._pintar(p['k'])
        barra = tk.Frame(self.cont, bg=BG)
        barra.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        self.btn = Btn(barra, 'Simular   Ctrl+S', self.simular, 'primary', 'lg', min_width=240)
        self.btn.pack(side='left')
        self.btn_excel = Btn(barra, 'Abrir Excel de la simulación', self._abrir_excel, 'secondary', 'md')
        self.res = tk.Frame(self.cont, bg=BG)
        self.res.pack(fill='x')

    def _param(self, k):
        return next(p for p in self.PARAMS if p['k'] == k)

    def _pintar(self, k):
        p, v = self._param(k), self.valores[k]
        self.lbl_val[k].config(text=p['fmt'](v), fg=ACCENT if v != p['ini'] else TEXT_DIM)

    def _cambio(self, k, v):
        self.valores[k] = v
        self._pintar(k)

    def _reset(self, k):
        p = self._param(k)
        self.sliders[k].set(p['ini'])
        self.valores[k] = p['ini']
        self._pintar(k)

    def simular(self):
        app, d = self.app, self.app.datos
        if self.ocupado:
            return
        if not d.tiene_datos:
            app.toast('Primero genera una recomendación para poder simular escenarios.', 'warn')
            return
        v = self.valores
        esc = {}
        if v['demanda']:
            esc['factor_demanda'] = 1 + v['demanda'] / 100.0
        if v['leadtime'] != dt.LEAD:
            esc['lead_time_dias'] = int(v['leadtime'])
        if v['horizonte'] != dt.HORIZONTE_DIAS:
            esc['horizonte_dias'] = int(v['horizonte'])
        if v['costo']:
            esc['factor_costo'] = 1 + v['costo'] / 100.0
        if not esc:
            app.toast('No cambiaste ningún control — todo está en su valor actual.', 'info')
            return
        self.ocupado = True
        self.btn.set_text('Calculando…')
        self.btn.set_state(False)
        carpeta = self.app.datos.carpeta        # raíz de salida; la simulación usa su período más reciente

        def trabajo():
            from preve_orquestador import ejecutar_simulacion
            return ejecutar_simulacion(carpeta, esc, progreso=lambda t: None)
        self.app.en_segundo_plano(trabajo, lambda res, err: self._mostrar(res, str(err) if err else None))

    def _mostrar(self, res, error):
        self.ocupado = False
        if not self.winfo_exists() or self.app.nombre_actual != 'simular':
            return
        self.btn.set_text('Simular   Ctrl+S')
        self.btn.set_state(True)
        self.limpiar(self.res)
        if error or not res:
            self.app.toast(f'No se pudo simular: {error or "sin datos"}', 'error')
            return
        r = res['resumen']
        self.btn_excel.pack(side='left', padx=(S(10), 0))
        seccion(self.res, 'Resultado del escenario')
        delta = r['delta_inversion']
        fila_cards(self.res, [
            lambda p: stat_card(p, 'Inversión actual', fmt_soles(r['inversion_orig']), 'recomendación vigente'),
            lambda p: stat_card(p, 'Inversión simulada', fmt_soles(r['inversion_sim']), r['escenario'], ACCENT),
            lambda p: stat_card(p, 'Diferencia', f'{fmt_soles(delta)}'.replace('S/ -', '− S/ '),
                                f'{r["delta_inversion_pct"]:+.1f}% frente a hoy',
                                DANGER if delta > 0 else OK),
        ])
        fila_cards(self.res, [
            lambda p: stat_card(p, 'Comprarías más de', fmt_num(r['skus_compra_aumenta']), 'productos'),
            lambda p: stat_card(p, 'Comprarías menos de', fmt_num(r['skus_compra_disminuye']), 'productos'),
            lambda p: stat_card(p, 'Sin cambio', fmt_num(r['skus_sin_cambio']), 'productos'),
        ], pady=(0, 10))
        comp = res['comparacion']
        top = comp.reindex(comp['delta_inversion'].abs().sort_values(ascending=False).index).head(6)
        top = top[top['delta_inversion'] != 0]
        if len(top):
            seccion(self.res, 'Los que más cambian')
            caja = tk.Frame(self.res, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
            caja.pack(fill='x', padx=S(PADX), pady=(0, S(24)))
            for row in top.to_dict('records'):
                fr = tk.Frame(caja, bg=SURFACE)
                fr.pack(fill='x', padx=S(14), pady=S(7))
                label(fr, acortar(str(row['nombre']), 50), 10).pack(side='left')
                dlt = row['delta_inversion']
                label(fr, ('+' if dlt > 0 else '−') + fmt_soles(abs(dlt)), 10, 'bold', DANGER if dlt > 0 else OK,
                      mono=True).pack(side='right')
                label(fr, f'{safe_int(row["compra_orig"])} → {safe_int(row["compra_sim"])} u    ', 9, color=TEXT_DIM,
                      mono=True).pack(side='right')
                tk.Frame(caja, bg=BORDER, height=1).pack(fill='x')
        self.app.toast('Simulación lista. Se guardó PREVE_Simulacion.xlsx', 'ok')

    def _abrir_excel(self):
        self.app.abrir_archivo(os.path.join(self.app.datos.ultimo_periodo() or self.app.datos.carpeta,
                                            'PREVE_Simulacion.xlsx'))


# ══════════════════════════════════════════════════════════════════
# HISTORIAL — lista cronológica con diferencias
# ══════════════════════════════════════════════════════════════════
class VistaHistorial(Vista):
    def build(self):
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill='both', expand=True)
        self.cont = self.scroll.inner
        self._token = 0

    def on_show(self, **kw):
        self.limpiar(self.cont)
        enc = self.encabezado(self.cont, 'Historial de análisis',
                              'Cada recomendación queda guardada para que compares un ciclo con otro.')
        Btn(enc, 'Actualizar', self.on_show, 'secondary', 'md').pack(side='right')
        self.zona = tk.Frame(self.cont, bg=BG)
        self.zona.pack(fill='x', padx=S(PADX), pady=(0, S(24)))
        caja = tk.Frame(self.zona, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        caja.pack(fill='x')
        self.sk = Skeleton(caja, 5)
        self.sk.pack(fill='x', pady=S(6))
        self._token += 1
        token = self._token
        carpeta = self.app.datos.carpeta
        sucursal = self.app.sucursal

        def trabajo():
            import time
            from sqlalchemy.exc import SQLAlchemyError
            import preve_historial as hist
            t0 = time.time()
            try:
                # FIX: historial filtrado por la sucursal activa
                df = hist.ejecuciones_con_diff(carpeta, 50, sucursal=sucursal) if carpeta else pd.DataFrame()
            except SQLAlchemyError:
                # FIX: antes except Exception silencioso
                logger.exception('No se pudo leer el historial')
                df = pd.DataFrame()
            time.sleep(max(0, 0.45 - (time.time() - t0)))      # que el esqueleto se alcance a ver
            return df
        self.app.en_segundo_plano(trabajo, lambda df, err: self._pintar_lista(
            df if df is not None else pd.DataFrame(), token))

    def _pintar_lista(self, df, token):
        if token != self._token or not self.winfo_exists() or self.app.nombre_actual != 'historial':
            return
        self.limpiar(self.zona)
        if df.empty:
            estado_vacio(self.zona, 'Aún no hay análisis guardados',
                         'Cuando generes tu primera recomendación quedará registrada aquí.', 'Empecemos →',
                         self.app.ir_generar).pack(pady=S(36))
            return
        caja = tk.Frame(self.zona, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        caja.pack(fill='x')
        cols = (S(60), S(150), S(110), S(90), S(150), S(150), 0)
        cab = tk.Frame(caja, bg=CARD_ALT)
        cab.pack(fill='x')
        for i, (t, ms) in enumerate(zip(('N.º', 'FECHA', 'TIPO', 'PRODUCTOS', 'INVERSIÓN', 'VS. ANTERIOR',
                                         'PRECISIÓN'), cols)):
            cab.columnconfigure(i, minsize=ms, weight=1 if i == 6 else 0)
            label(cab, t, 8, 'bold', TEXT_DIM, bg=CARD_ALT).grid(row=0, column=i, sticky='w', padx=S(10), pady=S(7))
        for r in df.to_dict('records'):
            fr = tk.Frame(caja, bg=SURFACE)
            fr.pack(fill='x')
            for i, ms in enumerate(cols):
                fr.columnconfigure(i, minsize=ms, weight=1 if i == 6 else 0)
            label(fr, f'#{int(r["id"])}', 10, 'bold', mono=True).grid(row=0, column=0, sticky='w', padx=S(10),
                                                                    pady=S(10))
            try:
                fecha = datetime.datetime.fromisoformat(r['fecha']).strftime('%d/%m/%Y  %H:%M')
            except (ValueError, TypeError):
                fecha = str(r['fecha'])[:16]
            label(fr, fecha, 10, mono=True).grid(row=0, column=1, sticky='w', padx=S(10))
            if r.get('etiqueta'):
                Chip(fr, r['etiqueta'], r['etiqueta']).grid(row=0, column=2, sticky='w', padx=S(10))
            label(fr, fmt_num(r['skus_evaluados']), 10, mono=True).grid(row=0, column=3, sticky='w', padx=S(10))
            label(fr, fmt_soles(r['inversion_total']), 10, 'bold', mono=True).grid(row=0, column=4, sticky='w',
                                                                                  padx=S(10))
            prev = r.get('inversion_prev')
            if pd.notna(prev) and prev:
                pct = (r['inversion_total'] - prev) / prev * 100
                label(fr, f'{"▲" if pct > 0 else "▼" if pct < 0 else "•"} {pct:+.0f}%', 10, mono=True,
                      color=DANGER if pct > 5 else OK if pct < -5 else TEXT_DIM
                      ).grid(row=0, column=5, sticky='w', padx=S(10))
            else:
                label(fr, '—', 10, color=TEXT_MUTED).grid(row=0, column=5, sticky='w', padx=S(10))
            if pd.notna(r.get('wmape')):
                prec = max(0, 100 - float(r['wmape']))
                label(fr, f'{prec:.0f}%  {r.get("calificacion") or ""}', 10, mono=True
                      ).grid(row=0, column=6, sticky='w', padx=S(10))
            else:
                label(fr, 'sin validar', 9, color=TEXT_MUTED).grid(row=0, column=6, sticky='w', padx=S(10))
            tk.Frame(caja, bg=BORDER, height=1).pack(fill='x')


# ══════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════
class VistaConfig(Vista):
    """Preferencias de la PC + (Fase 3) empresa, sucursales, ciclos y parámetros del modelo."""

    def _card_empresa(self):
        app, cfg = self.app, dt.Config.actual()
        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        top = tk.Frame(card.body, bg=SURFACE)
        top.pack(fill='x')
        label(top, 'Empresa, sucursales y ciclos de compra', 11, 'bold').pack(side='left')
        Btn(top, 'Editar en el asistente', lambda: app.abrir_asistente(0), 'secondary', 'sm').pack(side='right')
        e = cfg.empresa
        filas = [('Empresa', f'{e["nombre"]} · {e["moneda"]} · IGV {(e["igv_factor"] - 1) * 100:.0f}%'),
                 ('Sucursales', ' · '.join(f'{s["nombre"]} ({", ".join(s["almacenes"])})'
                                           for s in cfg.sucursales if s['activa']) or '—')]
        for c in cfg.ciclos:
            filas.append((f'Ciclo {c["nombre"]}', f'cada {c["duracion_dias"]} días · lead time {c["lead_time_dias"]} d · '
                                                  f'cubre {c["horizonte_pronostico_dias"]} d'))
        for t, v in filas:
            fr = tk.Frame(card.body, bg=SURFACE)
            fr.pack(fill='x', pady=S(2))
            label(fr, t, 10, 'bold', width=18, anchor='w').pack(side='left')
            label(fr, v, 9, color=TEXT_DIM, wraplength=S(640), justify='left').pack(side='left')

    def _card_categorias(self):
        """Fase 5: elegir plantilla de categorías, ver cuántos productos caen en cada acción y editarla."""
        from tkinter import ttk
        import preve_reglas as rg
        cfg, app = dt.Config.actual(), self.app
        plantillas = cfg.db.listar_plantillas_categorias()
        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        label(card.body, 'Categorías de acción', 11, 'bold').pack(anchor='w')
        label(card.body, 'Qué acción recomienda PREVÉ para cada producto. Gana la primera regla que se cumple.', 9,
              color=TEXT_DIM).pack(anchor='w', pady=(0, S(8)))
        nombres = list(plantillas['nombre']) if not plantillas.empty else []
        var = tk.StringVar(value=cfg.get('plantilla_categorias'))
        fila = tk.Frame(card.body, bg=SURFACE)
        fila.pack(anchor='w')
        cb = ttk.Combobox(fila, textvariable=var, values=nombres, state='readonly', width=26)
        cb.pack(side='left')
        info = tk.Frame(card.body, bg=SURFACE)
        info.pack(fill='x', pady=(S(8), 0))

        def pintar(*_):
            self.limpiar(info)
            p = rg.cargar_plantilla(var.get(), cfg.db)
            if not p:
                return
            label(info, p.get('descripcion') or '', 9, color=TEXT_DIM, wraplength=S(700), justify='left'
                  ).pack(anchor='w')
            for n, r in enumerate(p['reglas'], 1):
                label(info, f'{n}. {r["accion"]}: {rg.describir_regla(r)}', 9, mono=True, color=TEXT_DIM
                      ).pack(anchor='w')
            label(info, f'   sin coincidencia → {p["accion_por_defecto"]}', 9, mono=True, color=TEXT_MUTED
                  ).pack(anchor='w')
            if app.datos.tiene_datos:
                cuenta = rg.vista_previa(app.datos.df, p, dt.horizonte(), dt.lead_time())
                label(info, 'Con tus datos actuales: ' + ' · '.join(f'{a} {n:,}' for a, n in cuenta.items()), 9,
                      'bold', ACCENT, wraplength=S(700), justify='left').pack(anchor='w', pady=(S(6), 0))
        cb.bind('<<ComboboxSelected>>', pintar)

        def usar():
            cfg.set('plantilla_categorias', var.get())
            app.toast(f'Plantilla {var.get()} activada. Se aplica en el próximo análisis.', 'ok')

        def editar():
            from preve_editor_plantillas import EditorPlantillas
            EditorPlantillas(app, var.get())
        Btn(fila, 'Usar esta plantilla', usar, 'secondary', 'sm').pack(side='left', padx=S(8))
        Btn(fila, 'Editar reglas…', editar, 'ghost', 'sm').pack(side='left')
        pintar()

    def _card_erp(self):
        """Fase 4: elegir la integración con ERP (o ninguna = solo importación manual)."""
        from tkinter import ttk
        from plugins import listar_plugins
        cfg = dt.Config.actual()
        plugins = listar_plugins()
        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        label(card.body, 'Integración con tu ERP', 11, 'bold').pack(anchor='w')
        label(card.body, 'Con una integración activa aparece "Descargar desde …" en Generar. Sin integración, '
                         'importas los reportes a mano.', 9, color=TEXT_DIM).pack(anchor='w', pady=(0, S(8)))
        opciones = ['Ninguna (solo importación manual)'] + [f'{p.nombre} ({k})' for k, p in plugins.items()]
        actual = cfg.get('plugin_erp')
        var = tk.StringVar(value=next((o for o in opciones if o.endswith(f'({actual})')), opciones[0]))
        fila = tk.Frame(card.body, bg=SURFACE)
        fila.pack(anchor='w')
        ttk.Combobox(fila, textvariable=var, values=opciones, state='readonly', width=36).pack(side='left')

        def guardar():
            sel = var.get()
            clave = sel[sel.rfind('(') + 1:-1] if sel != opciones[0] else ''
            cfg.set('plugin_erp', clave)
            self.app.toast('Integración guardada', 'ok')
        Btn(fila, 'Guardar', guardar, 'secondary', 'sm').pack(side='left', padx=S(8))
        if actual in plugins:
            label(card.body, plugins[actual].descripcion, 9, color=TEXT_MUTED, wraplength=S(700),
                  justify='left').pack(anchor='w', pady=(S(6), 0))
        if actual == 'negosy' and actual in plugins:
            self._negosy_ajustes(card.body, plugins[actual])

    def _negosy_ajustes(self, cuerpo, plugin):
        """Fase 10: URLs, credenciales y catálogo de Negosy, editables (antes fijos en el código / en .env)."""
        from plugins.negosy.tiendas import tiendas_configuradas, guardar_ajustes
        from preve_config import ConfigError
        cfg = dt.Config.actual()
        tiendas = tiendas_configuradas(cfg)
        label(cuerpo, 'AJUSTES DE NEGOSY', 8, 'bold', TEXT_MUTED).pack(anchor='w', pady=(S(14), S(4)))
        v_login = tk.StringVar(value=cfg.get('negosy_login_url'))
        v_user = tk.StringVar(value=cfg.get('negosy_usuario'))
        v_pass = tk.StringVar(value=cfg.get('negosy_contrasena'))
        v_cat = tk.BooleanVar(value=bool(cfg.get('negosy_actualizar_catalogo')))
        v_base = {t['codigo']: tk.StringVar(value=t['base_url']) for t in tiendas.values()}
        v_sel = {t['codigo']: tk.StringVar(value=t['selector_texto']) for t in tiendas.values()}

        def fila(titulo, var, ancho=60, oculto=False):
            fr = tk.Frame(cuerpo, bg=SURFACE)
            fr.pack(fill='x', pady=S(2))
            label(fr, titulo, 9, 'bold', width=30, anchor='w').pack(side='left')
            e = entry(fr, var, width=ancho)
            if oculto:
                e.config(show='*')
            e.pack(side='left', ipady=S(3))
            return fr

        fila('URL de login', v_login)
        for t in tiendas.values():
            fr = fila(f'URL base · {t["sucursal"]}', v_base[t['codigo']], 40)
            label(fr, '  texto de la tarjeta', 8, color=TEXT_MUTED).pack(side='left')
            entry(fr, v_sel[t['codigo']], width=22).pack(side='left', padx=(S(4), 0), ipady=S(3))
        fila('Usuario', v_user, 30)
        fila('Contraseña', v_pass, 30, oculto=True)
        if plugin.credenciales_en_ambos_lados():
            label(cuerpo, 'Usando las credenciales guardadas en Configuración (no las del archivo .env).', 8,
                  color=TEXT_MUTED).pack(anchor='w', pady=(S(2), 0))
        tk.Checkbutton(cuerpo, text='Actualizar el catálogo de productos en esta descarga', variable=v_cat,
                       font=f(9), bg=SURFACE, activebackground=SURFACE, anchor='w').pack(anchor='w', pady=(S(6), 0))
        label(cuerpo, 'Recomendado: el stock actual sale del catálogo. Desmárcalo solo para pruebas: PREVÉ '
                      'reutilizaría el último catálogo y el stock quedaría con su fecha.', 8, color=TEXT_MUTED,
              wraplength=S(700), justify='left').pack(anchor='w')

        def guardar_negosy():
            try:
                guardar_ajustes(cfg, v_login.get(), v_user.get(), v_pass.get(),
                                {c: {'base_url': v_base[c].get(), 'selector_texto': v_sel[c].get()} for c in v_base},
                                v_cat.get())
            except ConfigError as e:
                self.app.toast(f'No se guardó: {e}', 'error')
                return
            self.app.toast('Ajustes de Negosy guardados', 'ok')
        Btn(cuerpo, 'Guardar ajustes de Negosy', guardar_negosy, 'secondary', 'sm').pack(anchor='w', pady=(S(10), 0))

    def _card_parametros(self):
        import json
        cfg = dt.Config.actual()
        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        label(card.body, 'Parámetros del modelo', 11, 'bold').pack(anchor='w')
        label(card.body, 'Cambian los cálculos del próximo análisis. Los valores por defecto son los que PREVÉ '
                         'usó siempre.', 9, color=TEXT_DIM).pack(anchor='w', pady=(0, S(8)))
        z = cfg.get('z_nivel_servicio')
        campos = [('z_A', 'Z clase A', z.get('A')), ('z_B', 'Z clase B', z.get('B')), ('z_C', 'Z clase C', z.get('C')),
                  ('adi_umbral', 'Umbral ADI', cfg.get('adi_umbral')), ('cv2_umbral', 'Umbral CV²', cfg.get('cv2_umbral')),
                  ('semanas_lanzamiento', 'Semanas de lanzamiento', cfg.get('semanas_lanzamiento')),
                  ('dias_entre_importaciones', 'Días entre importaciones', cfg.get('dias_entre_importaciones'))]
        self.vars_param = {}
        grid = tk.Frame(card.body, bg=SURFACE)
        grid.pack(anchor='w')
        for i, (k, t, v) in enumerate(campos):
            label(grid, t, 9, 'bold', width=22, anchor='w').grid(row=i // 2, column=(i % 2) * 2, sticky='w',
                                                                 pady=S(3))
            self.vars_param[k] = tk.StringVar(value=str(v))
            entry(grid, self.vars_param[k], width=10).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky='w',
                                                           padx=(0, S(24)), ipady=S(3))
        for k, t in (('ventas_son_salidas_de_almacen', 'El ERP registra cada venta también como salida de almacén'),
                     ('compras_son_entradas_de_almacen', 'El ERP registra cada compra también como entrada de almacén')):
            self.vars_param[k] = tk.BooleanVar(value=bool(cfg.get(k)))
            tk.Checkbutton(card.body, text=t, variable=self.vars_param[k], font=f(9), bg=SURFACE,
                           activebackground=SURFACE, anchor='w').pack(anchor='w', pady=(S(4), 0))
        label(card.body, 'AVANZADO · hiperparámetros (JSON)', 8, 'bold', TEXT_MUTED).pack(anchor='w', pady=(S(12), S(4)))
        for k, t in (('lgbm_params', 'LightGBM'), ('catboost_params', 'CatBoost'), ('tsb_params', 'Croston-TSB')):
            fr = tk.Frame(card.body, bg=SURFACE)
            fr.pack(fill='x', pady=S(2))
            label(fr, t, 9, 'bold', width=14, anchor='w').pack(side='left')
            self.vars_param[k] = tk.StringVar(value=json.dumps(cfg.get(k)))
            entry(fr, self.vars_param[k], width=80).pack(side='left', fill='x', expand=True, ipady=S(3))
        Btn(card.body, 'Guardar parámetros', self._guardar_parametros, 'primary', 'md').pack(anchor='w',
                                                                                        pady=(S(12), 0))

    def _guardar_parametros(self):
        from preve_config import ConfigError
        try:
            dt.Config.actual().guardar_parametros_modelo({k: v.get() for k, v in self.vars_param.items()})
        except ConfigError as e:
            self.app.toast(f'No se guardó: {e}', 'error')
            return
        self.app.toast('Parámetros guardados. Se aplican en el próximo análisis.', 'ok')

    def _card_depurar(self):
        """
        Fase 8: botón para borrar toda la data histórica y empezar de cero.
        Pensado para pruebas limpias (p. ej. backtesting de 6 meses sin arrastrar datos viejos).
        """
        import preve_depurar as depurar
        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        top = tk.Frame(card.body, bg=SURFACE)
        top.pack(fill='x')
        label(top, 'Depurar datos — empezar de cero', 11, 'bold', DANGER).pack(side='left')
        label(card.body, 'Borra todo el historial de análisis, pronósticos, métricas y reportes guardados. '
                         'Útil para hacer una prueba limpia (por ejemplo, backtesting de 6 meses con datos '
                         'nuevos) sin arrastrar resultados anteriores.', 9, color=TEXT_DIM,
              wraplength=S(700), justify='left').pack(anchor='w', pady=(S(4), S(8)))
        prev = depurar.preview_depuracion()
        datos_info = (f'Datos actuales en memoria: '
                      f'{len(prev["tablas_a_vaciar"])} tablas de datos, '
                      f'{len(prev["archivos"])} archivos auxiliares, '
                      f'{len(prev["carpetas"])} carpetas de reportes · '
                      f'{len(prev["periodos"])} análisis guardados · '
                      f'total: {prev["total_humano"]}')
        label(card.body, datos_info, 9, color=TEXT_MUTED).pack(anchor='w', pady=(0, S(8)))
        label(card.body, 'Se crea un ZIP de respaldo antes de borrar, dentro de respaldos_depurar/. '
                         'La configuración (empresa, sucursales, ciclos, plantillas, parámetros) '
                         'se mantiene para no repetir el asistente inicial.', 9,
              color=TEXT_DIM, wraplength=S(700), justify='left').pack(anchor='w', pady=(0, S(10)))
        fila = tk.Frame(card.body, bg=SURFACE)
        fila.pack(anchor='w')
        Btn(fila, 'Depurar datos (conservar configuración)',
            lambda: self.app.depurar_datos(conservar_config=True),
            'danger', 'md').pack(side='left')
        Btn(fila, 'Borrar TODO (incluye configuración)',
            lambda: self.app.depurar_datos(conservar_config=False),
            'ghost', 'md').pack(side='left', padx=S(8))

    ATAJOS = [('Ctrl + G', 'Generar recomendación (otra vez, para ejecutar)'), ('Ctrl + S', 'Simular (otra vez, para ejecutar)'),
              ('Ctrl + K', 'Buscador global'), ('Ctrl + H', 'Historial'), ('Ctrl + R', 'Recomendaciones'),
              ('Ctrl + D', 'Decisión rápida'), ('Ctrl + I', 'Inicio'), ('← →', 'Saltar / comprar en decisión rápida')]

    def build(self):
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill='both', expand=True)
        self.cont = self.scroll.inner

    def on_show(self, **kw):
        self.limpiar(self.cont)
        app = self.app
        self.encabezado(self.cont, 'Configuración', 'Preferencias de esta PC.')

        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        label(card.body, 'Tu nombre', 11, 'bold').pack(anchor='w')
        label(card.body, 'Aparece en el saludo del inicio.', 9, color=TEXT_DIM).pack(anchor='w', pady=(0, S(8)))
        var = tk.StringVar(value=app.cfg['usuario'])
        fila = tk.Frame(card.body, bg=SURFACE)
        fila.pack(anchor='w')
        entry(fila, var, width=28).pack(side='left', ipady=S(5))
        # FIX (Fase 3): nombre por defecto genérico (antes 'Julio' fijo)
        Btn(fila, 'Guardar', lambda: app.guardar_usuario(var.get().strip() or 'Usuario'), 'secondary', 'md'
            ).pack(side='left', padx=(S(8), 0))

        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        label(card.body, f'Carpetas de {app.sucursal}', 11, 'bold').pack(anchor='w')
        label(card.body, 'Cada sucursal recuerda sus propias carpetas.', 9, color=TEXT_DIM
              ).pack(anchor='w', pady=(0, S(8)))
        for clave, nombre in (('entrada', 'Reportes del ERP'), ('salida', 'Resultados')):
            fr = tk.Frame(card.body, bg=SURFACE)
            fr.pack(fill='x', pady=S(3))
            Btn(fr, 'Cambiar', lambda c=clave: (app.elegir_carpeta(c), self.on_show()), 'ghost', 'sm'
                ).pack(side='right')
            label(fr, nombre, 10, 'bold', width=16, anchor='w').pack(side='left')
            label(fr, ruta_corta(app.carpeta(clave), 80) or 'sin elegir', 9, color=TEXT_DIM, mono=True
                  ).pack(side='left')

        self._card_empresa()
        self._card_categorias()
        self._card_erp()
        self._card_parametros()
        self._card_depurar()

        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(12)))
        label(card.body, 'Atajos de teclado', 11, 'bold').pack(anchor='w', pady=(0, S(8)))
        for tecla, accion in self.ATAJOS:
            fr = tk.Frame(card.body, bg=SURFACE)
            fr.pack(fill='x', pady=S(2))
            label(fr, tecla, 9, 'bold', ACCENT, mono=True, width=12, anchor='w').pack(side='left')
            label(fr, accion, 10, color=TEXT_DIM).pack(side='left')

        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(PADX), pady=(0, S(24)))
        label(card.body, 'Tipografía', 11, 'bold').pack(anchor='w')
        label(card.body, f'En uso: {ui.F_UI} (interfaz) y {ui.F_MONO} (números y códigos). '
                         'Para usar Inter y JetBrains Mono, copia sus archivos .ttf a la carpeta "fonts" '
                         'junto a la aplicación y reinicia PREVÉ.', 9, color=TEXT_DIM, wraplength=S(700),
              justify='left').pack(anchor='w', pady=(S(4), 0))
