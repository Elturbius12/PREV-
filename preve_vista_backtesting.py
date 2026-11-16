# -*- coding: utf-8 -*-
"""PREVÉ — Vista del modo backtesting (Fase 6): rango, ventana, progreso en tiempo real, log y resumen.

Con `habilitar_modo_comparacion` aparece el selector Modo normal / Modo comparación: el segundo evalúa todos los
métodos de pronóstico configurados y muestra, por producto, el método recomendado y su confiabilidad.
"""
import datetime
import queue
import threading
import tkinter as tk
import traceback
from tkinter import ttk

import pandas as pd

import preve_ui as ui
from preve_ui import S, Btn, Card, ProgressBar, ScrollFrame, label, entry
from preve_utils import get_logger
from preve_vistas import Vista, fmt_mmss

from preve_backtesting import ETIQ_WMAPE, ETIQ_WMAPE_STOCK, ETIQ_WMAPE_TOTAL, NOTA_MAPE, _pc  # noqa: E402

logger = get_logger('vista_backtesting')
NARANJA = ui.BRAND_ORANGE
MODO_NORMAL, MODO_COMPARACION = 'normal', 'comparacion'
COLOR_SEMAFORO = {'verde': ui.OK, 'amarillo': ui.WARN, 'rojo': ui.DANGER, 'gris': ui.TEXT_MUTED}
FILAS_TABLA = 500          # productos que se listan en pantalla (todos van en las exportaciones)


def sin_jerga(texto):
    """Texto del log para el usuario no técnico: las siglas de métricas en lenguaje de negocio."""
    for tecnico, negocio in ((ETIQ_WMAPE_TOTAL, 'margen de error (venta total)'),
                             (ETIQ_WMAPE_STOCK, 'margen de error (productos con stock)'),
                             (ETIQ_WMAPE, 'margen de error'), ('WMAPE', 'margen de error'),
                             ('MAPE', 'error % medio')):
        texto = texto.replace(tecnico, negocio)
    return texto


class VistaBacktesting(Vista):
    def build(self):
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill='both', expand=True)
        self.cont = self.scroll.inner
        self.corriendo = False
        self._cancelar = threading.Event()
        self.cola = queue.Queue()
        self.ultimo_resultado = None

    def on_show(self, **kw):
        if self.corriendo:
            return                       # no reconstruir mientras corre: se perdería el progreso
        self.limpiar(self.cont)
        app = self.app
        enc = self.encabezado(self.cont, 'Modo backtesting',
                              f'¿Qué tan bien habría pronosticado PREVÉ en {app.sucursal}? Entrena solo con el pasado '
                              f'de cada fecha y compara contra lo que realmente se vendió.')
        label(enc, '● MODO BACKTESTING', 9, 'bold', NARANJA).pack(side='right')
        from preve_config import Config
        conf = Config.actual()
        self.avanzado = bool(conf.get('mostrar_metricas_avanzadas'))
        hoy = datetime.date.today()
        ini_def = (pd.Timestamp(hoy) - pd.DateOffset(months=6)).date()

        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(32), pady=(0, S(12)))

        # (a) selector de modo — solo si el modo comparación está habilitado en Configuración
        self.var_modo = tk.StringVar(value=MODO_NORMAL)
        self.modo_comparacion_habilitado = bool(conf.get('habilitar_modo_comparacion'))
        if self.modo_comparacion_habilitado:
            fila_modo = tk.Frame(card.body, bg=ui.SURFACE)
            fila_modo.pack(anchor='w', pady=(0, S(10)))
            label(fila_modo, 'Modo', 10, 'bold', width=16, anchor='w').pack(side='left')
            for valor, texto in ((MODO_NORMAL, 'Modo normal (el método actual)'),
                                 (MODO_COMPARACION, 'Modo comparación (todos los métodos)')):
                tk.Radiobutton(fila_modo, text=texto, value=valor, variable=self.var_modo, font=ui.f(10),
                               bg=ui.SURFACE, activebackground=ui.SURFACE, command=self._cambio_modo
                               ).pack(side='left', padx=(0, S(16)))

        grid = tk.Frame(card.body, bg=ui.SURFACE)
        grid.pack(anchor='w')
        self.vars = {}
        for i, (k, t, v, ayuda) in enumerate((
                ('inicio', 'Desde', ini_def.isoformat(), 'AAAA-MM-DD · por defecto, 6 meses atrás'),
                ('fin', 'Hasta', hoy.isoformat(), 'se recorta a la última venta disponible'),
                ('ventana', 'Ventana (días)', str(conf.get('dias_entre_importaciones')),
                 'cada cuántos días se re-entrena y se evalúa'))):
            label(grid, t, 10, 'bold', width=16, anchor='w').grid(row=i, column=0, sticky='w', pady=S(4))
            self.vars[k] = tk.StringVar(value=v)
            entry(grid, self.vars[k], width=14).grid(row=i, column=1, sticky='w', ipady=S(3))
            label(grid, ayuda, 9, color=ui.TEXT_MUTED).grid(row=i, column=2, sticky='w', padx=S(10))
        self.var_compras = tk.BooleanVar(value=True)
        self.chk_compras = tk.Checkbutton(card.body, text='Simular también la compra de cada día de reporte final del '
                                                          'ciclo', variable=self.var_compras, font=ui.f(9),
                                          bg=ui.SURFACE, activebackground=ui.SURFACE)
        self.chk_compras.pack(anchor='w', pady=(S(8), 0))
        self.lbl_ayuda_modo = label(card.body, '', 9, color=ui.TEXT_DIM, wraplength=S(720), justify='left')
        self.lbl_ayuda_modo.pack(anchor='w', pady=(S(6), 0))
        if not self.modo_comparacion_habilitado:
            label(card.body, 'Para comparar varios métodos de pronóstico, activa el "Modo comparación" en '
                             'Configuración.', 9, color=ui.TEXT_MUTED).pack(anchor='w', pady=(S(4), 0))
        botones = tk.Frame(card.body, bg=ui.SURFACE)
        botones.pack(anchor='w', pady=(S(12), 0))
        self.btn = Btn(botones, 'Ejecutar backtesting', self._ejecutar, 'primary', 'lg', min_width=240)
        self.btn.pack(side='left')
        self.btn_cancelar = Btn(botones, 'Cancelar', self._cancelar.set, 'ghost', 'md')
        self._cambio_modo()

        self.prog_card = Card(self.cont, padx=22, pady=16)
        self.prog_card.pack(fill='x', padx=S(32), pady=(0, S(12)))
        self.lbl_estado = label(self.prog_card.body, 'Listo para ejecutar.', 10, 'bold')
        self.lbl_estado.pack(anchor='w')
        self.barra = ProgressBar(self.prog_card.body, 8)
        self.barra.pack(fill='x', pady=S(8))
        self.log = tk.Text(self.prog_card.body, height=10, font=ui.f(9, mono=True), bg=ui.BG, fg=ui.TEXT_DIM,
                           relief='flat', highlightthickness=1, highlightbackground=ui.BORDER, wrap='word')
        self.log.pack(fill='x')
        self.resumen = tk.Frame(self.cont, bg=ui.BG)
        self.resumen.pack(fill='x', padx=S(32), pady=(0, S(24)))
        self._anteriores()

    def _cambio_modo(self):
        comparar = self.var_modo.get() == MODO_COMPARACION
        if comparar:
            from preve_config import Config
            import preve_modelos_baseline as mb
            from preve_traducciones import traducir_modelo
            conf = Config.actual()
            modelos, desconocidos = conf.modelos_para_comparar()
            concretos = mb.expandir_modelos(modelos, conf.ventanas_promedio_movil())
            nombres = ', '.join(m if self.avanzado else traducir_modelo(m) for m in concretos)
            txt = (f'Se evaluarán {len(concretos)} métodos con tus ventas pasadas y se elegirá el que menos se '
                   f'equivoca en cada producto: {nombres}. Tarda bastante más que el modo normal (cada método '
                   f'repite el backtesting completo).')
            if desconocidos:
                txt += f'\n⚠ Se ignoran nombres desconocidos en la configuración: {", ".join(map(str, desconocidos))}.'
            self.chk_compras.config(state='disabled')
            self.btn.set_text('Comparar métodos')
        else:
            txt = ('Usa los reportes de la carpeta de entrada de la sucursal. Cada ventana entrena los modelos '
                   '(≈1 minuto por ventana): 6 meses con ventanas de 14 días tardan unos 15–20 minutos.')
            self.chk_compras.config(state='normal')
            self.btn.set_text('Ejecutar backtesting')
        self.lbl_ayuda_modo.config(text=txt)

    # ── ejecución en segundo plano ─────────────────────────
    def _ejecutar(self):
        if self.corriendo:
            return
        try:
            ini = pd.Timestamp(self.vars['inicio'].get().strip())
            fin = pd.Timestamp(self.vars['fin'].get().strip())
            ventana = int(self.vars['ventana'].get())
            if ventana < 1 or ini >= fin:
                raise ValueError
        except ValueError:
            self.app.toast('Revisa las fechas (AAAA-MM-DD, "desde" antes que "hasta") y la ventana.', 'warn')
            return
        carpeta = self.app.carpeta('entrada')
        if not carpeta:
            self.app.toast('Elige primero la carpeta de reportes del ERP (en Generar).', 'warn')
            return
        comparar = self.modo_comparacion_habilitado and self.var_modo.get() == MODO_COMPARACION
        self.corriendo = True
        self._cancelar.clear()
        self.app.backtesting_corriendo = True
        self.btn.set_text('Ejecutando…')
        self.btn.set_state(False)
        self.btn_cancelar.pack(side='left', padx=S(10))
        self.log.delete('1.0', 'end')
        self.limpiar(self.resumen)
        self.t0 = datetime.datetime.now()
        sucursal, compras = self.app.sucursal, self.var_compras.get()
        threading.Thread(target=self._trabajo, args=(carpeta, sucursal, ini, fin, ventana, compras, comparar),
                         daemon=True).start()
        self._poll()

    def _trabajo(self, carpeta, sucursal, ini, fin, ventana, compras, comparar=False):
        import preve_backtesting as bt
        from preve_config import Config
        from preve_reglas import cargar_plantilla
        put = self.cola.put
        try:
            conf = Config.actual()
            put(('estado', (0.0, 'Leyendo los reportes y reconstruyendo el stock…')))
            datos = bt.DatosBacktest.desde_carpeta(carpeta, sucursal, log=lambda m: None)
            if comparar:
                from preve_traducciones import traducir_modelo
                modelos, _ = conf.modelos_para_comparar()
                put(('fin_comparacion', bt.comparar_modelos(
                    datos, ini, fin, params=conf.parametros_modelo(sucursal), modelos=modelos,
                    plantilla=cargar_plantilla(conf.get('plantilla_categorias')), ventana_dias=ventana,
                    sucursal=sucursal, log=lambda m: put(('log', m)),
                    # sin modo avanzado: nombres traducidos; el detalle técnico de cada ventana va solo al log
                    etiqueta_modelo=None if self.avanzado else traducir_modelo,
                    log_detalle=None if self.avanzado else logger.info,
                    progreso=lambda f, t: put(('estado', (f, t))), cancelar=self._cancelar.is_set)))
            else:
                b = bt.Backtester(datos, conf.parametros_modelo(sucursal),
                                  cargar_plantilla(conf.get('plantilla_categorias')),
                                  ventana_dias=ventana, sucursal=sucursal,
                                  log=lambda m: put(('log', m)), progreso=lambda f, t: put(('estado', (f, t))),
                                  cancelar=self._cancelar.is_set)
                put(('fin', b.run(ini, fin, simular_compras=compras)))
        except bt.BacktestCancelado:
            put(('cancelado', None))
        except Exception as e:  # noqa: BLE001 — se muestra en pantalla con el detalle completo
            logger.exception('Falló el backtesting')
            put(('error', f'{e}\n\n{traceback.format_exc()}'))

    def _poll(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == 'log':
                    self.log.insert('end', (dato if self.avanzado else sin_jerga(dato)) + '\n')
                    self.log.see('end')
                elif tipo == 'estado':
                    frac, txt = dato
                    self.barra.set(frac, NARANJA)
                    el = (datetime.datetime.now() - self.t0).total_seconds()
                    self.lbl_estado.config(text=f'{txt}   ·   {fmt_mmss(el)}')
                else:
                    self._terminar(tipo, dato)
                    return
        except queue.Empty:
            pass
        if self.corriendo:
            self.after(200, self._poll)

    def _terminar(self, tipo, dato):
        self.corriendo = False
        self.app.backtesting_corriendo = False
        self.btn.set_state(True)
        self._cambio_modo()
        self.btn_cancelar.pack_forget()
        if tipo == 'cancelado':
            self.lbl_estado.config(text='Backtesting cancelado.')
            return
        if tipo == 'error':
            self.lbl_estado.config(text='No se pudo completar el backtesting.', fg=ui.DANGER)
            self.log.insert('end', '\nERROR:\n' + dato)
            self.log.see('end')
            return
        r = dato
        self.barra.set(1.0, ui.OK)
        self.lbl_estado.config(text=f'Listo en {fmt_mmss(r["duracion_s"])}.', fg=ui.TEXT)
        if tipo == 'fin_comparacion':
            self.ultimo_resultado = r
            self.mostrar_resumen_comparacion(r)
            self.app.toast(f'Comparación lista: {len(r["mejor_por_sku"]):,} productos, {len(r["modelos"])} métodos',
                           'ok')
            return
        self._mostrar_resumen(r)
        g = r['global']
        self.app.toast(f'Backtesting listo: {r["total_productos"]:,} productos, {r["total_periodos"]} períodos, '
                       f'margen de error {_pc(g["wmape"])}', 'ok',
                       accion=('Abrir Excel', lambda: self.app.abrir_archivo(r['ruta_excel'])))

    def _mostrar_resumen(self, r):
        from preve_vistas import fila_cards, stat_card
        from preve_traducciones import ETIQUETA_ERROR, interpretar_wmape, traducir_modelo
        self.limpiar(self.resumen)
        g = r['global']
        av = self.avanzado
        fila_cards(self.resumen, [
            lambda p: stat_card(p, ETIQ_WMAPE if av else ETIQUETA_ERROR, _pc(g['wmape']),
                                'indicador principal · ponderado por volumen' if av else interpretar_wmape(g['wmape'])[0],
                                NARANJA),
            lambda p: stat_card(p, ETIQ_WMAPE_TOTAL if av else 'Error contra toda la venta', _pc(g['wmape_total']),
                                'contra todo lo vendido, con atípicos' if av else 'incluye pedidos de mayoristas'),
            lambda p: stat_card(p, ETIQ_WMAPE_STOCK if av else 'Error con stock disponible', _pc(g['wmape_con_stock']),
                                'stock > 0 al inicio de la ventana' if av else 'solo productos que tenían stock'),
            lambda p: stat_card(p, 'Productos', f'{r["total_productos"]:,}',
                                f'evaluados · {r["total_periodos"]} períodos ({len(r["omitidas"])} omitidos)'),
        ], padx=0)
        caja = Card(self.resumen, padx=18, pady=14)
        caja.pack(fill='x', pady=(0, S(10)))
        label(caja.body, 'POR MODELO' if av else 'POR MÉTODO DE PRONÓSTICO', 8, 'bold', ui.TEXT_MUTED).pack(anchor='w')
        for m in r['por_modelo'].to_dict('records'):
            if av:
                txt = (f'{m["modelo"]:<12}  {m["productos"]:>5} productos   WMAPE {_pc(m["wmape"]):>7}   '
                       f'MAE {m["mae"]:7.2f}   Bias {m["bias"]:+7.2f}   MAPE {_pc(m["mape"]):>7}')
            else:
                txt = (f'{traducir_modelo(m["modelo"])}: {m["productos"]:,} productos · '
                       f'{interpretar_wmape(m["wmape"])[2]}')
            label(caja.body, txt, 9, mono=av).pack(anchor='w')
        if av:
            label(caja.body, f'MAPE: {NOTA_MAPE}.', 8, color=ui.TEXT_MUTED).pack(anchor='w', pady=(S(4), 0))
        Btn(self.resumen, 'Abrir el Excel del backtesting', lambda: self.app.abrir_archivo(r['ruta_excel']),
            'primary', 'md').pack(anchor='w')

    # ── resultado del modo comparación ─────────────────────
    def mostrar_resumen_comparacion(self, r):
        """(b) tabla por producto con semáforo + (c) dos exportaciones distintas."""
        from preve_vistas import fila_cards, stat_card
        from preve_traducciones import interpretar_confianza, traducir_modelo, COLOR_CONFIANZA
        self.limpiar(self.resumen)
        mejor = r['mejor_por_sku'].sort_values('venta_real', ascending=False)
        conteo = mejor['confianza'].value_counts()
        metodos = mejor['modelo'].value_counts()
        principal = traducir_modelo(metodos.index[0], self.avanzado) if len(metodos) else '—'
        fila_cards(self.resumen, [
            # la cifra va en grande; el nombre del método (texto largo) en la línea de abajo, que sí se ajusta
            lambda p: stat_card(p, 'Método más elegido', f'{metodos.iloc[0] if len(metodos) else 0:,} productos',
                                f'{principal} (de {len(mejor):,} productos)', NARANJA),
            lambda p: stat_card(p, 'Confiabilidad alta', f'{int(conteo.get("Alta", 0)):,}',
                                f'media {int(conteo.get("Media", 0)):,} · baja {int(conteo.get("Baja", 0)):,}',
                                ui.OK),
            lambda p: stat_card(p, 'Métodos comparados', f'{len(r["modelos"])}',
                                f'{len(r["fallidos"])} no se pudieron evaluar' if r['fallidos'] else 'todos evaluados'),
        ], padx=0)

        caja = Card(self.resumen, padx=18, pady=14)
        caja.pack(fill='x', pady=(0, S(10)))
        label(caja.body, 'MÉTODO RECOMENDADO POR PRODUCTO', 8, 'bold', ui.TEXT_MUTED).pack(anchor='w')
        label(caja.body, 'Para cada producto se simuló cómo habría pronosticado cada método en el pasado y se eligió '
                         'el que menos se equivocó. ● verde: confiable · ● amarillo: revisar · ● rojo: verificar '
                         'manualmente · ● gris: sin ventas para medir.'
              + (f'  Se muestran los {FILAS_TABLA} productos que más venden; la lista completa está en las '
                 f'exportaciones.' if len(mejor) > FILAS_TABLA else ''),
              9, color=ui.TEXT_DIM, wraplength=S(820), justify='left').pack(anchor='w', pady=(S(2), S(6)))
        estilo = ttk.Style(self)
        estilo.configure('Preve.Treeview', font=ui.f(9), rowheight=S(24), background=ui.SURFACE,
                         fieldbackground=ui.SURFACE, foreground=ui.TEXT, borderwidth=0)
        estilo.configure('Preve.Treeview.Heading', font=ui.f(9, 'bold'), background=ui.BG, foreground=ui.TEXT,
                         relief='flat')
        marco = tk.Frame(caja.body, bg=ui.SURFACE)
        marco.pack(fill='x')
        columnas = ('producto', 'modelo', 'confianza', 'precision')
        tabla = ttk.Treeview(marco, columns=columnas, show='headings', height=14, style='Preve.Treeview')
        for c, t, w in (('producto', 'Producto', 300), ('modelo', 'Método recomendado', 260),
                        ('confianza', 'Confiabilidad', 110), ('precision', 'Precisión estimada', 300)):
            tabla.heading(c, text=t, anchor='w')
            tabla.column(c, width=S(w), anchor='w', stretch=True)
        for nivel, color in COLOR_CONFIANZA.items():
            tabla.tag_configure(nivel, foreground=COLOR_SEMAFORO[color])
        for fila in mejor.head(FILAS_TABLA).to_dict('records'):
            nombre = fila.get('nombre') if isinstance(fila.get('nombre'), str) else fila['sku']
            tabla.insert('', 'end', tags=(fila['confianza'],), values=(
                nombre, traducir_modelo(fila['modelo'], self.avanzado), f'● {fila["confianza"]}', fila['precision']))
        barra = ttk.Scrollbar(marco, orient='vertical', command=tabla.yview, style='Preve.Vertical.TScrollbar')
        tabla.configure(yscrollcommand=barra.set)
        tabla.pack(side='left', fill='x', expand=True)
        barra.pack(side='right', fill='y')
        self.tabla_comparacion = tabla
        self.lbl_detalle = label(caja.body, 'Selecciona un producto para ver qué significa su confiabilidad.', 9,
                                 color=ui.TEXT_DIM, wraplength=S(820), justify='left')
        self.lbl_detalle.pack(anchor='w', pady=(S(6), 0))

        def al_elegir(_):
            sel = tabla.selection()
            if sel:
                conf = tabla.item(sel[0], 'tags')[0]
                self.lbl_detalle.config(text=interpretar_confianza(conf)[1], fg=COLOR_SEMAFORO[COLOR_CONFIANZA[conf]])
        tabla.bind('<<TreeviewSelect>>', al_elegir)

        botones = tk.Frame(self.resumen, bg=ui.BG)
        botones.pack(anchor='w', pady=(S(8), 0))
        Btn(botones, 'Exportar para reporte de gestión', lambda: self.exportar_gestion(r), 'primary', 'md'
            ).pack(side='left', padx=(0, S(8)))
        Btn(botones, 'Exportar datos técnicos', lambda: self.app.abrir_archivo(r['ruta_excel']), 'ghost', 'md'
            ).pack(side='left')
        if r.get('asignaciones_guardadas'):
            label(self.resumen, f'Se guardó el método elegido para {r["asignaciones_guardadas"]:,} productos. Los '
                                f'análisis lo usan solo si activas esa opción en Configuración.', 9,
                  color=ui.TEXT_MUTED).pack(anchor='w', pady=(S(6), 0))

    def exportar_gestion(self, r):
        import preve_backtesting as bt
        try:
            ruta = bt.exportar_reporte_gestion(r, r['carpeta'])
        except OSError as e:
            self.app.toast(f'No se pudo exportar: {e}', 'error')
            return None
        self.app.abrir_archivo(ruta)
        return ruta

    def _anteriores(self):
        from database import Database
        df = Database.default().listar_ejecuciones_backtesting().head(5)
        if df.empty:
            return
        caja = Card(self.resumen, padx=18, pady=14)
        caja.pack(fill='x')
        label(caja.body, 'EJECUCIONES ANTERIORES', 8, 'bold', ui.TEXT_MUTED).pack(anchor='w', pady=(0, S(4)))
        for e in df.to_dict('records'):
            fila = tk.Frame(caja.body, bg=ui.SURFACE)
            fila.pack(fill='x', pady=S(1))
            label(fila, f'{e["fecha_inicio"]} – {e["fecha_fin"]} · {e["total_productos"] or 0:,} productos · '
                        f'{e["total_periodos"] or 0} períodos · {"MAPE" if self.avanzado else "error porcentual medio"} '
                        f'{e["mape_promedio"] or 0:.1f}% (poco confiable)', 9,
                  mono=True
                  ).pack(side='left')
            if e.get('ruta_excel'):
                Btn(fila, 'Abrir', lambda p=e['ruta_excel']: self.app.abrir_archivo(p), 'ghost', 'sm').pack(side='right')
