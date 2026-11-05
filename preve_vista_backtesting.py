# -*- coding: utf-8 -*-
"""PREVÉ — Vista del modo backtesting (Fase 6): rango, ventana, progreso en tiempo real, log y resumen."""
import datetime
import queue
import threading
import tkinter as tk
import traceback

import pandas as pd

import preve_ui as ui
from preve_ui import S, Btn, Card, ProgressBar, ScrollFrame, label, entry
from preve_utils import get_logger
from preve_vistas import Vista, fmt_mmss

logger = get_logger('vista_backtesting')
NARANJA = ui.BRAND_ORANGE


class VistaBacktesting(Vista):
    def build(self):
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill='both', expand=True)
        self.cont = self.scroll.inner
        self.corriendo = False
        self._cancelar = threading.Event()
        self.cola = queue.Queue()

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
        hoy = datetime.date.today()
        ini_def = (pd.Timestamp(hoy) - pd.DateOffset(months=6)).date()

        card = Card(self.cont, padx=22, pady=18)
        card.pack(fill='x', padx=S(32), pady=(0, S(12)))
        grid = tk.Frame(card.body, bg=ui.SURFACE)
        grid.pack(anchor='w')
        self.vars = {}
        for i, (k, t, v, ayuda) in enumerate((
                ('inicio', 'Desde', ini_def.isoformat(), 'AAAA-MM-DD · por defecto, 6 meses atrás'),
                ('fin', 'Hasta', hoy.isoformat(), 'se recorta a la última venta disponible'),
                ('ventana', 'Ventana (días)', str(Config.actual().get('dias_entre_importaciones')),
                 'cada cuántos días se re-entrena y se evalúa'))):
            label(grid, t, 10, 'bold', width=16, anchor='w').grid(row=i, column=0, sticky='w', pady=S(4))
            self.vars[k] = tk.StringVar(value=v)
            entry(grid, self.vars[k], width=14).grid(row=i, column=1, sticky='w', ipady=S(3))
            label(grid, ayuda, 9, color=ui.TEXT_MUTED).grid(row=i, column=2, sticky='w', padx=S(10))
        self.var_compras = tk.BooleanVar(value=True)
        tk.Checkbutton(card.body, text='Simular también la compra de cada día de reporte final del ciclo',
                       variable=self.var_compras, font=ui.f(9), bg=ui.SURFACE, activebackground=ui.SURFACE
                       ).pack(anchor='w', pady=(S(8), 0))
        label(card.body, 'Usa los reportes de la carpeta de entrada de la sucursal. Cada ventana entrena los 3 modelos '
                         '(≈1 minuto por ventana): 6 meses con ventanas de 14 días tardan unos 15–20 minutos.', 9,
              color=ui.TEXT_DIM, wraplength=S(720), justify='left').pack(anchor='w', pady=(S(6), 0))
        botones = tk.Frame(card.body, bg=ui.SURFACE)
        botones.pack(anchor='w', pady=(S(12), 0))
        self.btn = Btn(botones, 'Ejecutar backtesting', self._ejecutar, 'primary', 'lg', min_width=240)
        self.btn.pack(side='left')
        self.btn_cancelar = Btn(botones, 'Cancelar', self._cancelar.set, 'ghost', 'md')

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
        threading.Thread(target=self._trabajo, args=(carpeta, sucursal, ini, fin, ventana, compras),
                         daemon=True).start()
        self._poll()

    def _trabajo(self, carpeta, sucursal, ini, fin, ventana, compras):
        import preve_backtesting as bt
        from preve_config import Config
        from preve_reglas import cargar_plantilla
        put = self.cola.put
        try:
            conf = Config.actual()
            put(('estado', (0.0, 'Leyendo los reportes y reconstruyendo el stock…')))
            datos = bt.DatosBacktest.desde_carpeta(carpeta, sucursal, log=lambda m: None)
            b = bt.Backtester(datos, conf.parametros_modelo(sucursal), cargar_plantilla(conf.get('plantilla_categorias')),
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
                    self.log.insert('end', dato + '\n')
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
        self.btn.set_text('Ejecutar backtesting')
        self.btn.set_state(True)
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
        self._mostrar_resumen(r)
        self.app.toast(f'Backtesting listo: {r["total_productos"]:,} productos, {r["total_periodos"]} períodos, '
                       f'MAPE {r["mape_promedio"]:.1f}%', 'ok', accion=('Abrir Excel',
                                                                      lambda: self.app.abrir_archivo(r['ruta_excel'])))

    def _mostrar_resumen(self, r):
        from preve_vistas import fila_cards, stat_card
        self.limpiar(self.resumen)
        g = r['global']
        fila_cards(self.resumen, [
            lambda p: stat_card(p, 'Productos', f'{r["total_productos"]:,}', 'evaluados'),
            lambda p: stat_card(p, 'Períodos', f'{r["total_periodos"]}',
                                f'{len(r["omitidas"])} omitidos por datos insuficientes'),
            lambda p: stat_card(p, 'MAPE', f'{g["mape"]:.1f}%', 'sMAPE cuando la venta real es 0', NARANJA),
            lambda p: stat_card(p, 'WMAPE', f'{g["wmape"]:.1f}%' if g['wmape'] == g['wmape'] else '—',
                                'error ponderado por volumen'),
        ], padx=0)
        caja = Card(self.resumen, padx=18, pady=14)
        caja.pack(fill='x', pady=(0, S(10)))
        label(caja.body, 'POR MODELO', 8, 'bold', ui.TEXT_MUTED).pack(anchor='w')
        for m in r['por_modelo'].to_dict('records'):
            label(caja.body, f'{m["modelo"]:<12}  {m["productos"]:>5} productos   MAPE {m["mape"]:6.1f}%   '
                             f'MAE {m["mae"]:7.2f}   Bias {m["bias"]:+7.2f}', 9, mono=True).pack(anchor='w')
        Btn(self.resumen, 'Abrir el Excel del backtesting', lambda: self.app.abrir_archivo(r['ruta_excel']),
            'primary', 'md').pack(anchor='w')

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
                        f'{e["total_periodos"] or 0} períodos · MAPE {e["mape_promedio"] or 0:.1f}%', 9, mono=True
                  ).pack(side='left')
            if e.get('ruta_excel'):
                Btn(fila, 'Abrir', lambda p=e['ruta_excel']: self.app.abrir_archivo(p), 'ghost', 'sm').pack(side='right')
