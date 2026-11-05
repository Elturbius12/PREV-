# -*- coding: utf-8 -*-
"""
PREVÉ — Asistente de configuración inicial (Fase 3).

Se abre solo cuando configuracion.wizard_completado es False, y también desde Configuración para
editar. Seis pantallas:
    1. Empresa (nombre, moneda, IGV)
    2. Sucursales (agregar / editar / quitar, con sus tiendas y almacenes)
    3. Ciclos de compra (N ciclos: duración, lead time, día del reporte final, horizonte)
    4. Frecuencia de análisis (días entre importaciones)
    5. Mapeo de columnas del Excel de ventas
    6. Plantilla de categorías de acción

La lógica (estado inicial, validación y guardado) está separada de la interfaz para poder testearla.
"""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from preve_config import Config, ConfigError, DEFAULTS
from preve_utils import get_logger

logger = get_logger('wizard')

PASOS = ['Empresa', 'Sucursales', 'Ciclos de compra', 'Frecuencia de análisis', 'Columnas de ventas',
         'Categorías de acción']
CAMPOS_VENTAS = [('fecha', 'Fecha de la venta', True), ('codigo_barras', 'Código de barras', True),
                 ('producto', 'Nombre del producto', False), ('cantidad', 'Cantidad vendida', True),
                 ('precio', 'Precio unitario', False), ('sucursal', 'Sucursal / almacén', False)]

# ══════════════════════════════════════════════════════════════════
# Lógica (sin interfaz)
# ══════════════════════════════════════════════════════════════════
def asegurar_plantillas(cfg):
    # Fase 5: las plantillas del sistema viven en seeds/plantillas_categorias.py
    from seeds.plantillas_categorias import sembrar
    sembrar(cfg.db)


def estado_inicial(cfg):
    """Datos del asistente precargados con la configuración actual."""
    asegurar_plantillas(cfg)
    emp = cfg.empresa
    sucursales = [{'nombre': s['nombre'], 'almacenes': ', '.join(s['almacenes']), 'activa': s['activa']}
                  for s in cfg.sucursales if s['activa']]
    ciclos = [{k: c[k] for k in ('nombre', 'duracion_dias', 'lead_time_dias', 'dia_reporte_final',
                                 'horizonte_pronostico_dias')} for c in cfg.ciclos]
    return {
        'empresa': {'nombre': emp['nombre'], 'moneda': emp['moneda'], 'igv_factor': emp['igv_factor']},
        'sucursales': sucursales or [{'nombre': '', 'almacenes': '', 'activa': True}],
        'ciclos': ciclos,
        'dias_entre_importaciones': cfg.get('dias_entre_importaciones'),
        'mapeo': dict(DEFAULTS['mapeo_columnas_ventas'][0], **(cfg.get('mapeo_columnas_ventas') or {})),
        'plantilla': cfg.get('plantilla_categorias'),
        'plantillas_disponibles': cfg.db.listar_plantillas_categorias()[['nombre', 'descripcion']].to_dict('records'),
    }


def _num(v, tipo=float):
    try:
        return tipo(str(v).replace(',', '.').strip())
    except (TypeError, ValueError):
        return None


def validar_paso(i, d):
    """Lista de errores (vacía si el paso es válido)."""
    err = []
    if i == 0:
        e = d['empresa']
        if not str(e.get('nombre', '')).strip():
            err.append('Escribe el nombre de la empresa.')
        if not str(e.get('moneda', '')).strip():
            err.append('Indica la moneda (ej. PEN).')
        igv = _num(e.get('igv_factor'))
        if igv is None or not 1.0 <= igv < 2.0:
            err.append('El factor de IGV debe ser un número entre 1.00 y 1.99 (1.18 = 18%).')
    elif i == 1:
        nombres = [s['nombre'].strip() for s in d['sucursales'] if s['nombre'].strip()]
        if not nombres:
            err.append('Agrega al menos una sucursal.')
        if len({n.lower() for n in nombres}) != len(nombres):
            err.append('Hay sucursales repetidas.')
        for s in d['sucursales']:
            if s['nombre'].strip() and not s['almacenes'].strip():
                err.append(f'Indica las tiendas/almacenes de "{s["nombre"]}" (como aparecen en el ERP).')
    elif i == 2:
        if not d['ciclos']:
            err.append('Agrega al menos un ciclo de compra.')
        nombres = [str(c['nombre']).strip() for c in d['ciclos']]
        if any(not n for n in nombres):
            err.append('Cada ciclo necesita un nombre.')
        if len({n.lower() for n in nombres}) != len(nombres):
            err.append('Hay ciclos con el mismo nombre.')
        for c in d['ciclos']:
            dur, lt = _num(c['duracion_dias'], int), _num(c['lead_time_dias'], int)
            fin = _num(c.get('dia_reporte_final'), int)
            if not dur or dur <= 0:
                err.append(f'"{c["nombre"]}": la duración debe ser un número de días mayor que 0.')
            if lt is None or lt < 0:
                err.append(f'"{c["nombre"]}": el lead time debe ser un número de días (0 o más).')
            if dur and fin is not None and not 1 <= fin <= dur:
                err.append(f'"{c["nombre"]}": el día del reporte final debe estar entre 1 y {dur}.')
    elif i == 3:
        n = _num(d['dias_entre_importaciones'], int)
        if not n or not 1 <= n <= 120:
            err.append('Los días entre importaciones deben estar entre 1 y 120.')
    elif i == 4:
        for campo, titulo, obligatorio in CAMPOS_VENTAS:
            if obligatorio and not str(d['mapeo'].get(campo, '')).strip():
                err.append(f'Indica qué columna del Excel corresponde a "{titulo}".')
    elif i == 5:
        if not d.get('plantilla'):
            err.append('Elige una plantilla de categorías.')
    return err


def guardar_asistente(cfg, d):
    """Valida todo y guarda en la base. Marca wizard_completado = True."""
    errores = [e for i in range(len(PASOS)) for e in validar_paso(i, d)]
    if errores:
        raise ConfigError('\n'.join(errores))
    e = d['empresa']
    cfg.set_empresa(e['nombre'], e['moneda'], _num(e['igv_factor']))
    cfg.guardar_sucursales([{'nombre': s['nombre'].strip(), 'activa': True,
                             'almacenes': [a.strip() for a in s['almacenes'].split(',') if a.strip()]}
                            for s in d['sucursales'] if s['nombre'].strip()])
    ciclos = []
    for c in d['ciclos']:
        dur, lt = _num(c['duracion_dias'], int), _num(c['lead_time_dias'], int)
        hz = _num(c.get('horizonte_pronostico_dias'), int) or dur + lt
        ciclos.append({'nombre': str(c['nombre']).strip(), 'duracion_dias': dur, 'lead_time_dias': lt,
                       'dia_reporte_final': _num(c.get('dia_reporte_final'), int), 'horizonte_pronostico_dias': hz})
    cfg.guardar_ciclos(ciclos)
    cfg.set('dias_entre_importaciones', _num(d['dias_entre_importaciones'], int))
    cfg.set('mapeo_columnas_ventas', {k: str(v).strip() for k, v in d['mapeo'].items() if str(v).strip()})
    cfg.set('plantilla_categorias', d['plantilla'])
    if cfg.get('sucursal_activa') not in cfg.nombres_sucursales:
        cfg.set('sucursal_activa', cfg.nombres_sucursales[0])
    cfg.set('wizard_completado', True)
    logger.info('Asistente de configuración completado')


def columnas_de_archivo(ruta):
    """Encabezados de un Excel/CSV de ventas (para elegir columnas en el paso 5)."""
    import pandas as pd
    if ruta.lower().endswith(('.csv', '.tsv', '.txt')):
        sep = '\t' if ruta.lower().endswith('.tsv') else None
        return [str(c).strip() for c in pd.read_csv(ruta, nrows=0, sep=sep, engine='python').columns]
    from preve_pipeline import _detectar_fila_encabezado
    hoja = pd.ExcelFile(ruta).sheet_names[0]
    fila = _detectar_fila_encabezado(ruta, hoja)
    cols = pd.read_excel(ruta, sheet_name=hoja, header=fila, nrows=0).columns
    return [str(c).strip() for c in cols if not str(c).startswith('Unnamed')]


# ══════════════════════════════════════════════════════════════════
# Interfaz
# ══════════════════════════════════════════════════════════════════
class Asistente(tk.Toplevel):
    """Ventana modal del asistente. al_terminar() se llama solo si se guardó."""

    def __init__(self, root, al_terminar=None, paso_inicial=0, cfg=None):
        import preve_ui as ui
        self.ui = ui
        super().__init__(root, bg=ui.BG)
        self.cfg = cfg or Config.actual()
        self.datos = estado_inicial(self.cfg)
        self.paso = paso_inicial
        self.al_terminar = al_terminar
        self.columnas_ejemplo = []
        self.title('PREVÉ · Configuración inicial')
        self.geometry(f'{ui.S(820)}x{ui.S(640)}')
        self.minsize(ui.S(720), ui.S(560))
        self.transient(root)
        self.protocol('WM_DELETE_WINDOW', self._cancelar)
        self._construir()
        self._render()
        self.grab_set()

    # ── estructura ─────────────────────────────────────────
    def _construir(self):
        ui = self.ui
        cab = tk.Frame(self, bg=ui.SURFACE)
        cab.pack(fill='x')
        self.lbl_titulo = ui.label(cab, '', 16, 'bold', bg=ui.SURFACE)
        self.lbl_titulo.pack(anchor='w', padx=ui.S(26), pady=(ui.S(18), 0))
        self.lbl_paso = ui.label(cab, '', 9, color=ui.TEXT_DIM, bg=ui.SURFACE)
        self.lbl_paso.pack(anchor='w', padx=ui.S(26), pady=(0, ui.S(10)))
        self.barra = ui.ProgressBar(cab, 4)
        self.barra.pack(fill='x')
        self.cuerpo = ui.ScrollFrame(self)
        self.cuerpo.pack(fill='both', expand=True)
        self.lbl_error = ui.label(self, '', 9, color=ui.DANGER, wraplength=ui.S(760), justify='left')
        self.lbl_error.pack(fill='x', padx=ui.S(26))
        pie = tk.Frame(self, bg=ui.BG)
        pie.pack(fill='x', padx=ui.S(26), pady=ui.S(14))
        self.btn_sig = ui.Btn(pie, 'Siguiente →', self._siguiente, 'primary', 'md', min_width=150)
        self.btn_sig.pack(side='right')
        self.btn_atras = ui.Btn(pie, '← Atrás', self._atras, 'secondary', 'md')
        self.btn_atras.pack(side='right', padx=ui.S(8))
        ui.Btn(pie, 'Cancelar', self._cancelar, 'ghost', 'md').pack(side='left')

    def _render(self):
        ui = self.ui
        for w in self.cuerpo.inner.winfo_children():
            w.destroy()
        self.lbl_error.config(text='')
        self.vars = {}
        self.lbl_titulo.config(text=PASOS[self.paso])
        self.lbl_paso.config(text=f'Paso {self.paso + 1} de {len(PASOS)} · los cambios se guardan al terminar')
        self.barra.set((self.paso + 1) / len(PASOS))
        self.btn_sig.set_text('Terminar ✓' if self.paso == len(PASOS) - 1 else 'Siguiente →')
        self.btn_atras.set_state(self.paso > 0)
        c = tk.Frame(self.cuerpo.inner, bg=ui.BG)
        c.pack(fill='both', expand=True, padx=ui.S(26), pady=ui.S(16))
        [self._p_empresa, self._p_sucursales, self._p_ciclos, self._p_frecuencia, self._p_columnas,
         self._p_categorias][self.paso](c)

    # ── helpers de formulario ──────────────────────────────
    def _ayuda(self, parent, texto):
        self.ui.label(parent, texto, 9, color=self.ui.TEXT_DIM, wraplength=self.ui.S(720), justify='left'
                      ).pack(anchor='w', pady=(0, self.ui.S(12)))

    def _campo(self, parent, clave, titulo, valor, ancho=30, ayuda=''):
        ui = self.ui
        fila = tk.Frame(parent, bg=ui.BG)
        fila.pack(fill='x', pady=ui.S(5))
        ui.label(fila, titulo, 10, 'bold', width=26, anchor='w').pack(side='left')
        var = tk.StringVar(value='' if valor is None else str(valor))
        ui.entry(fila, var, width=ancho).pack(side='left', ipady=ui.S(4))
        if ayuda:
            ui.label(fila, ayuda, 9, color=ui.TEXT_MUTED).pack(side='left', padx=ui.S(10))
        self.vars[clave] = var
        return var

    # ── pantallas ──────────────────────────────────────────
    def _p_empresa(self, c):
        self._ayuda(c, 'Estos datos aparecen en los reportes y se usan para quitar el impuesto a los precios '
                       'al calcular márgenes.')
        e = self.datos['empresa']
        self._campo(c, 'nombre', 'Nombre de la empresa', e['nombre'], 36)
        self._campo(c, 'moneda', 'Moneda', e['moneda'], 8, 'Código, ej. PEN, USD')
        self._campo(c, 'igv_factor', 'Factor de IGV / IVA', e['igv_factor'], 8, '1.18 = 18%')

    def _p_sucursales(self, c):
        ui = self.ui
        self._ayuda(c, 'Cada sucursal agrupa las tiendas y almacenes que comparten stock, escritos como '
                       'aparecen en los reportes del ERP y separados por comas. Los traslados entre ellas no '
                       'cambian el stock de la sucursal. Quitar una sucursal no borra su historial.')
        self.filas_suc = []
        for i, s in enumerate(self.datos['sucursales']):
            fila = tk.Frame(c, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
            fila.pack(fill='x', pady=ui.S(4))
            vn, va = tk.StringVar(value=s['nombre']), tk.StringVar(value=s['almacenes'])
            ui.label(fila, 'Nombre', 8, 'bold', ui.TEXT_MUTED, bg=ui.SURFACE).grid(row=0, column=0, sticky='w',
                                                                                    padx=ui.S(10), pady=(ui.S(6), 0))
            ui.label(fila, 'Tiendas / almacenes', 8, 'bold', ui.TEXT_MUTED, bg=ui.SURFACE).grid(
                row=0, column=1, sticky='w', padx=ui.S(10), pady=(ui.S(6), 0))
            ui.entry(fila, vn, width=20).grid(row=1, column=0, padx=ui.S(10), pady=ui.S(8), ipady=ui.S(3))
            ui.entry(fila, va, width=48).grid(row=1, column=1, padx=ui.S(10), pady=ui.S(8), ipady=ui.S(3))
            ui.Btn(fila, 'Quitar', lambda i=i: self._quitar('sucursales', i), 'ghost', 'sm').grid(
                row=1, column=2, padx=ui.S(6))
            self.filas_suc.append((vn, va))
        ui.Btn(c, '+ Agregar sucursal', lambda: self._agregar('sucursales', {'nombre': '', 'almacenes': '',
                                                                            'activa': True}),
               'soft', 'sm').pack(anchor='w', pady=ui.S(8))

    def _p_ciclos(self, c):
        ui = self.ui
        self._ayuda(c, 'Un ciclo por tipo de proveedor. Duración = días entre pedidos; lead time = días que tarda '
                       'en llegar la mercadería; el horizonte (duración + lead time) es lo que debe cubrir cada '
                       'compra. El ciclo de mayor horizonte es el principal (define las etiquetas PRELIMINAR / '
                       'BORRADOR / FINAL); los demás generan sus propias notas de compra.')
        self.filas_ciclo = []
        cols = [('nombre', 'Nombre', 16), ('duracion_dias', 'Duración (días)', 8), ('lead_time_dias', 'Lead time', 8),
                ('dia_reporte_final', 'Día reporte final', 8), ('horizonte_pronostico_dias', 'Horizonte', 8)]
        for i, ciclo in enumerate(self.datos['ciclos']):
            fila = tk.Frame(c, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
            fila.pack(fill='x', pady=ui.S(4))
            vs = {}
            for j, (k, t, w) in enumerate(cols):
                ui.label(fila, t, 8, 'bold', ui.TEXT_MUTED, bg=ui.SURFACE).grid(row=0, column=j, sticky='w',
                                                                                 padx=ui.S(8), pady=(ui.S(6), 0))
                vs[k] = tk.StringVar(value='' if ciclo.get(k) is None else str(ciclo[k]))
                ui.entry(fila, vs[k], width=w).grid(row=1, column=j, padx=ui.S(8), pady=ui.S(8), ipady=ui.S(3))
            ui.Btn(fila, 'Quitar', lambda i=i: self._quitar('ciclos', i), 'ghost', 'sm').grid(row=1, column=len(cols),
                                                                                          padx=ui.S(4))
            self.filas_ciclo.append(vs)
        ui.Btn(c, '+ Agregar ciclo', lambda: self._agregar('ciclos', {'nombre': '', 'duracion_dias': 30,
                                                                     'lead_time_dias': 10, 'dia_reporte_final': '',
                                                                     'horizonte_pronostico_dias': ''}),
               'soft', 'sm').pack(anchor='w', pady=ui.S(8))
        ui.label(c, 'Si dejas el horizonte vacío se calcula como duración + lead time; el día del reporte final, '
                    'como el penúltimo día del ciclo.', 9, color=ui.TEXT_MUTED, wraplength=ui.S(720),
                 justify='left').pack(anchor='w')

    def _p_frecuencia(self, c):
        self._ayuda(c, 'Cada cuántos días piensas exportar los reportes del ERP y generar un análisis nuevo. '
                       'Se usa para planificar las validaciones y el backtesting.')
        self._campo(c, 'dias', 'Días entre importaciones', self.datos['dias_entre_importaciones'], 8)

    def _p_columnas(self, c):
        ui = self.ui
        self._ayuda(c, 'Indica qué columna del Excel de ventas corresponde a cada dato. Puedes cargar un archivo '
                       'de ejemplo para elegir de una lista. Los valores precargados son los nombres que exporta '
                       'Negosy.')
        Btn = ui.Btn
        Btn(c, 'Cargar un Excel/CSV de ventas de ejemplo…', self._cargar_ejemplo, 'secondary', 'sm'
            ).pack(anchor='w', pady=(0, ui.S(10)))
        for campo, titulo, obligatorio in CAMPOS_VENTAS:
            fila = tk.Frame(c, bg=ui.BG)
            fila.pack(fill='x', pady=ui.S(4))
            ui.label(fila, titulo + (' *' if obligatorio else ''), 10, 'bold', width=26, anchor='w').pack(side='left')
            var = tk.StringVar(value=self.datos['mapeo'].get(campo, ''))
            cb = ttk.Combobox(fila, textvariable=var, values=self.columnas_ejemplo, width=34)
            cb.pack(side='left')
            self.vars[campo] = var
        ui.label(c, '* obligatorio', 8, color=ui.TEXT_MUTED).pack(anchor='w', pady=(ui.S(6), 0))

    def _p_categorias(self, c):
        ui = self.ui
        self._ayuda(c, 'La plantilla define qué acciones recomienda PREVÉ para cada producto (RECOMPRAR, LIQUIDAR…). '
                       'Podrás editar sus reglas más adelante.')
        var = tk.StringVar(value=self.datos['plantilla'])
        self.vars['plantilla'] = var
        for p in self.datos['plantillas_disponibles']:
            fila = tk.Frame(c, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
            fila.pack(fill='x', pady=ui.S(4))
            tk.Radiobutton(fila, text=p['nombre'], variable=var, value=p['nombre'], font=ui.f(10, 'bold'),
                           bg=ui.SURFACE, activebackground=ui.SURFACE, anchor='w').pack(anchor='w', padx=ui.S(10),
                                                                                       pady=(ui.S(6), 0))
            ui.label(fila, p['descripcion'] or '', 9, color=ui.TEXT_DIM, bg=ui.SURFACE, wraplength=ui.S(700),
                     justify='left').pack(anchor='w', padx=ui.S(32), pady=(0, ui.S(8)))

    # ── navegación ─────────────────────────────────────────
    def _leer(self):
        """Pasa lo escrito en la pantalla actual a self.datos."""
        d, v = self.datos, self.vars
        if self.paso == 0:
            d['empresa'] = {k: v[k].get() for k in ('nombre', 'moneda', 'igv_factor')}
        elif self.paso == 1:
            d['sucursales'] = [{'nombre': n.get(), 'almacenes': a.get(), 'activa': True} for n, a in self.filas_suc]
        elif self.paso == 2:
            d['ciclos'] = [{k: var.get() for k, var in fila.items()} for fila in self.filas_ciclo]
        elif self.paso == 3:
            d['dias_entre_importaciones'] = v['dias'].get()
        elif self.paso == 4:
            d['mapeo'] = {campo: v[campo].get() for campo, _, _ in CAMPOS_VENTAS}
        elif self.paso == 5:
            d['plantilla'] = v['plantilla'].get()

    def _agregar(self, clave, fila):
        self._leer()
        self.datos[clave].append(fila)
        self._render()

    def _quitar(self, clave, i):
        self._leer()
        del self.datos[clave][i]
        self._render()

    def _cargar_ejemplo(self):
        self._leer()
        ruta = filedialog.askopenfilename(parent=self, title='Elige un reporte de ventas de ejemplo',
                                          filetypes=[('Excel o CSV', '*.xlsx *.xls *.csv *.tsv'), ('Todos', '*.*')])
        if not ruta:
            return
        try:
            self.columnas_ejemplo = columnas_de_archivo(ruta)
        except (OSError, ValueError, ImportError) as e:
            messagebox.showerror('PREVÉ', f'No se pudo leer {os.path.basename(ruta)}:\n{e}', parent=self)
            return
        self._render()

    def _siguiente(self):
        self._leer()
        errores = validar_paso(self.paso, self.datos)
        if errores:
            self.lbl_error.config(text='\n'.join('• ' + e for e in errores))
            return
        if self.paso < len(PASOS) - 1:
            self.paso += 1
            self._render()
            return
        try:
            guardar_asistente(self.cfg, self.datos)
        except ConfigError as e:
            self.lbl_error.config(text=str(e))
            return
        self.grab_release()
        self.destroy()
        if self.al_terminar:
            self.al_terminar()

    def _atras(self):
        if self.paso > 0:
            self._leer()
            self.paso -= 1
            self._render()

    def _cancelar(self):
        if not self.cfg.get('wizard_completado'):
            if not messagebox.askyesno('PREVÉ', 'La configuración inicial no está completa. Puedes seguir usando '
                                                'PREVÉ con los valores actuales y volver a abrir el asistente desde '
                                                'Configuración.\n\n¿Cerrar el asistente?', parent=self):
                return
        self.grab_release()
        self.destroy()
