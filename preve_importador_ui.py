# -*- coding: utf-8 -*-
"""
PREVÉ — Ventana de importación manual de ventas (Fase 4).

El usuario arrastra cada columna del archivo (izquierda) hasta el campo de PREVÉ que corresponde
(derecha). También funciona con clic: clic en la columna y luego clic en el campo. La vista previa
muestra las primeras filas ya convertidas y cuántos errores habría.
"""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import preve_importador as imp
import preve_ui as ui
from preve_ui import S, Btn, label
from preve_utils import get_logger

logger = get_logger('importador_ui')

FILAS_PREVIA = 8
MUESTRA_VALIDACION = 300


class VentanaImportacion(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, bg=ui.BG)
        from database import Database
        from preve_config import Config
        self.app, self.db, self.cfg = app, Database.default(), Config.actual()
        self.df, self.ruta, self.mapeo, self.seleccion = None, None, {}, None
        self.productos = self.db.productos_por_codigo()
        self.title('PREVÉ · Importar ventas')
        self.geometry(f'{S(980)}x{S(720)}')
        self.minsize(S(860), S(600))
        self.transient(app.root)
        self._construir()
        self._cargar_perfil(self.cfg.get('perfil_importacion'), silencioso=True)

    # ── estructura ─────────────────────────────────────────
    def _construir(self):
        cab = tk.Frame(self, bg=ui.SURFACE)
        cab.pack(fill='x')
        label(cab, 'Importar ventas desde un archivo', 15, 'bold', bg=ui.SURFACE).pack(anchor='w', padx=S(22),
                                                                                       pady=(S(14), 0))
        label(cab, 'Excel o CSV de cualquier sistema. Arrastra cada columna del archivo al campo de PREVÉ que '
                   'le corresponde (o haz clic en la columna y luego en el campo).', 9, color=ui.TEXT_DIM,
              bg=ui.SURFACE, wraplength=S(900), justify='left').pack(anchor='w', padx=S(22), pady=(0, S(12)))

        fila = tk.Frame(self, bg=ui.BG)
        fila.pack(fill='x', padx=S(22), pady=(S(12), S(6)))
        Btn(fila, 'Elegir archivo…', self._elegir, 'primary', 'md').pack(side='left')
        self.lbl_archivo = label(fila, 'Ningún archivo', 9, color=ui.TEXT_MUTED, mono=True)
        self.lbl_archivo.pack(side='left', padx=S(12))

        op = tk.Frame(self, bg=ui.BG)
        op.pack(fill='x', padx=S(22), pady=S(4))
        label(op, 'Perfil', 9, 'bold').pack(side='left')
        self.var_perfil = tk.StringVar(value=self.cfg.get('perfil_importacion'))
        self.cb_perfil = ttk.Combobox(op, textvariable=self.var_perfil, width=20, state='readonly',
                                      values=self._perfiles())
        self.cb_perfil.pack(side='left', padx=(S(6), S(4)))
        self.cb_perfil.bind('<<ComboboxSelected>>', lambda e: self._cargar_perfil(self.var_perfil.get()))
        Btn(op, 'Guardar perfil…', self._guardar_perfil, 'ghost', 'sm').pack(side='left', padx=(0, S(16)))
        label(op, 'Formato de fecha', 9, 'bold').pack(side='left')
        self.var_fecha = tk.StringVar(value='detectar')
        cb = ttk.Combobox(op, textvariable=self.var_fecha, width=18,
                          values=['detectar', 'auto'] + [f for f, _ in imp.FORMATOS_FECHA])
        cb.pack(side='left', padx=(S(6), S(16)))
        cb.bind('<<ComboboxSelected>>', lambda e: self._previa())
        label(op, 'Sucursal si falta', 9, 'bold').pack(side='left')
        sucs = self.cfg.nombres_sucursales
        self.var_suc = tk.StringVar(value=self.app.sucursal if self.app.sucursal in sucs else (sucs or [''])[0])
        cb = ttk.Combobox(op, textvariable=self.var_suc, values=sucs, width=16, state='readonly')
        cb.pack(side='left', padx=S(6))
        cb.bind('<<ComboboxSelected>>', lambda e: self._previa())
        self.var_existentes = tk.BooleanVar(value=bool(self.productos))
        tk.Checkbutton(op, text='Solo productos del catálogo', variable=self.var_existentes, command=self._previa,
                       font=ui.f(9), bg=ui.BG, activebackground=ui.BG).pack(side='left', padx=S(10))

        medio = tk.Frame(self, bg=ui.BG)
        medio.pack(fill='both', expand=True, padx=S(22), pady=S(8))
        medio.columnconfigure(0, weight=1, uniform='m')
        medio.columnconfigure(1, weight=1, uniform='m')
        izq = tk.Frame(medio, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        izq.grid(row=0, column=0, sticky='nsew', padx=(0, S(8)))
        label(izq, 'COLUMNAS DEL ARCHIVO', 8, 'bold', ui.TEXT_MUTED, bg=ui.SURFACE).pack(anchor='w', padx=S(12),
                                                                                         pady=(S(10), S(6)))
        self.zona_cols = tk.Frame(izq, bg=ui.SURFACE)
        self.zona_cols.pack(fill='both', expand=True, padx=S(10), pady=(0, S(10)))
        der = tk.Frame(medio, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        der.grid(row=0, column=1, sticky='nsew')
        label(der, 'CAMPOS DE PREVÉ', 8, 'bold', ui.TEXT_MUTED, bg=ui.SURFACE).pack(anchor='w', padx=S(12),
                                                                                    pady=(S(10), S(6)))
        self.zonas = {}
        for campo, (titulo, obligatorio, _) in imp.CAMPOS.items():
            z = tk.Frame(der, bg=ui.BG, highlightthickness=1, highlightbackground=ui.BORDER, cursor='hand2')
            z.pack(fill='x', padx=S(10), pady=S(3))
            z._campo = campo
            lt = label(z, titulo + (' *' if obligatorio else ''), 9, 'bold', bg=ui.BG, width=30, anchor='w')
            lt.pack(side='left', padx=S(8), pady=S(7))
            lt._campo = campo
            lv = label(z, '', 9, color=ui.TEXT_MUTED, bg=ui.BG, mono=True, anchor='w')
            lv.pack(side='left', fill='x', expand=True)
            lv._campo = campo
            bx = label(z, '✕', 9, color=ui.TEXT_MUTED, bg=ui.BG, cursor='hand2')
            bx.pack(side='right', padx=S(8))
            bx.bind('<Button-1>', lambda e, c=campo: self._asignar(c, None))
            for w in (z, lt, lv):
                w.bind('<Button-1>', lambda e, c=campo: self._click_zona(c))
            self.zonas[campo] = (z, lv)

        abajo = tk.Frame(self, bg=ui.BG)
        abajo.pack(fill='x', padx=S(22))
        self.lbl_previa = label(abajo, 'VISTA PREVIA', 8, 'bold', ui.TEXT_MUTED)
        self.lbl_previa.pack(anchor='w', pady=(S(4), S(4)))
        cols = ('sku', 'fecha', 'cantidad', 'precio_unitario', 'sucursal', 'nombre')
        self.tabla = ttk.Treeview(abajo, columns=cols, show='headings', height=FILAS_PREVIA)
        for c, w in zip(cols, (130, 90, 70, 90, 120, 300)):
            self.tabla.heading(c, text=c.replace('_', ' ').capitalize())
            self.tabla.column(c, width=S(w), anchor='w')
        self.tabla.pack(fill='x')

        pie = tk.Frame(self, bg=ui.BG)
        pie.pack(fill='x', padx=S(22), pady=S(12))
        self.btn_importar = Btn(pie, 'Importar ventas', self._importar, 'primary', 'md', min_width=180)
        self.btn_importar.pack(side='right')
        self.btn_importar.set_state(False)
        Btn(pie, 'Cerrar', self.destroy, 'ghost', 'md').pack(side='left')

    # ── archivo ────────────────────────────────────────────
    def _elegir(self):
        ruta = filedialog.askopenfilename(parent=self, title='Elige el archivo de ventas',
                                          filetypes=[('Excel o CSV', '*.xlsx *.xls *.csv *.tsv *.txt'),
                                                     ('Todos', '*.*')])
        if not ruta:
            return
        try:
            self.df = imp.leer_archivo(ruta)
        except (OSError, ValueError, ImportError) as e:
            messagebox.showerror('PREVÉ', f'No se pudo leer el archivo:\n{e}', parent=self)
            return
        self.ruta = ruta
        self.lbl_archivo.config(text=f'{os.path.basename(ruta)} · {len(self.df):,} filas · '
                                     f'{len(self.df.columns)} columnas', fg=ui.TEXT)
        # conservar lo que el perfil ya mapeaba si esas columnas existen; sugerir el resto
        sugerido = imp.sugerir_mapeo(self.df.columns)
        self.mapeo = {c: v for c, v in self.mapeo.items() if v in self.df.columns}
        for c, v in sugerido.items():
            self.mapeo.setdefault(c, v)
        self._pintar_columnas()
        self._pintar_zonas()
        self._previa()

    def _pintar_columnas(self):
        for w in self.zona_cols.winfo_children():
            w.destroy()
        usadas = set(self.mapeo.values())
        fila, ancho = None, 0
        for col in (self.df.columns if self.df is not None else []):
            if fila is None or ancho > 4:
                fila = tk.Frame(self.zona_cols, bg=ui.SURFACE)
                fila.pack(fill='x', pady=S(2))
                ancho = 0
            sel = col == self.seleccion
            bg = ui.ACCENT if sel else (ui.CARD_ALT if col in usadas else ui.ACCENT_TINT)
            chip = tk.Label(fila, text=col, font=ui.f(9), bg=bg, fg='#FFFFFF' if sel else ui.TEXT,
                            padx=S(8), pady=S(3), cursor='fleur')
            chip.pack(side='left', padx=S(3))
            chip.bind('<ButtonPress-1>', lambda e, c=col: self._inicio_arrastre(e, c))
            chip.bind('<B1-Motion>', self._mover_arrastre)
            chip.bind('<ButtonRelease-1>', self._fin_arrastre)
            ancho += 1 + len(col) // 14

    def _pintar_zonas(self):
        for campo, (z, lv) in self.zonas.items():
            col = self.mapeo.get(campo)
            lv.config(text=col or 'arrastra una columna aquí', fg=ui.ACCENT if col else ui.TEXT_MUTED)
            obligatorio = imp.CAMPOS[campo][1]
            z.config(highlightbackground=ui.ACCENT if col else (ui.DANGER if obligatorio and self.df is not None
                                                                 else ui.BORDER))

    # ── arrastrar y soltar ─────────────────────────────────
    def _inicio_arrastre(self, e, col):
        self._arrastre = {'col': col, 'x': e.x_root, 'y': e.y_root, 'ventana': None}

    def _mover_arrastre(self, e):
        a = getattr(self, '_arrastre', None)
        if not a:
            return
        if a['ventana'] is None and abs(e.x_root - a['x']) + abs(e.y_root - a['y']) > 6:
            v = tk.Toplevel(self)
            v.overrideredirect(True)
            v.attributes('-topmost', True)
            tk.Label(v, text=a['col'], font=ui.f(9, 'bold'), bg=ui.ACCENT, fg='#FFFFFF', padx=S(8),
                     pady=S(3)).pack()
            a['ventana'] = v
        if a['ventana'] is not None:
            a['ventana'].geometry(f'+{e.x_root + 12}+{e.y_root + 8}')

    def _fin_arrastre(self, e):
        a, self._arrastre = getattr(self, '_arrastre', None), None
        if not a:
            return
        if a['ventana'] is None:                 # fue un clic: seleccionar la columna
            self.seleccion = None if self.seleccion == a['col'] else a['col']
            self._pintar_columnas()
            return
        a['ventana'].destroy()
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None and not hasattr(w, '_campo'):
            w = w.master
        if w is not None:
            self._asignar(w._campo, a['col'])

    def _click_zona(self, campo):
        if self.seleccion:
            self._asignar(campo, self.seleccion)

    def _asignar(self, campo, col):
        if col is None:
            self.mapeo.pop(campo, None)
        else:
            for c, v in list(self.mapeo.items()):      # una columna va a un solo campo
                if v == col:
                    del self.mapeo[c]
            self.mapeo[campo] = col
        self.seleccion = None
        self._pintar_columnas()
        self._pintar_zonas()
        self._previa()

    # ── perfiles ───────────────────────────────────────────
    def _perfiles(self):
        df = self.db.listar_perfiles_importacion('ventas')
        return list(df['nombre']) if not df.empty else []

    def _cargar_perfil(self, nombre, silencioso=False):
        p = self.db.obtener_perfil_importacion(nombre) if nombre else None
        if not p:
            if not silencioso:
                messagebox.showwarning('PREVÉ', f'No existe el perfil "{nombre}".', parent=self)
            return
        self.mapeo = dict(p['mapeo'])
        self.var_fecha.set(p['formato_fecha'] or 'detectar')
        self.var_perfil.set(nombre)
        if self.df is not None:
            self.mapeo = {c: v for c, v in self.mapeo.items() if v in self.df.columns}
            self._pintar_columnas()
            self._previa()
        self._pintar_zonas()

    def _guardar_perfil(self):
        if not self.mapeo:
            messagebox.showinfo('PREVÉ', 'Primero asigna al menos una columna.', parent=self)
            return
        nombre = simpledialog.askstring('PREVÉ', 'Nombre del perfil (p. ej. "Ventas Negosy"):', parent=self,
                                        initialvalue=self.var_perfil.get() or '')
        if not nombre or not nombre.strip():
            return
        fecha = self.var_fecha.get()
        self.db.guardar_perfil_importacion(nombre.strip(), self.mapeo, None if fecha == 'detectar' else fecha)
        self.cfg.set('perfil_importacion', nombre.strip())
        self.cb_perfil.config(values=self._perfiles())
        self.var_perfil.set(nombre.strip())
        self.app.toast(f'Perfil "{nombre.strip()}" guardado', 'ok')

    # ── vista previa e importación ─────────────────────────
    def _formato_fecha(self, df):
        f = self.var_fecha.get()
        return imp.detectar_formato_fecha(df[self.mapeo['fecha']]) if f == 'detectar' else f

    def _previa(self):
        self.tabla.delete(*self.tabla.get_children())
        listo = self.df is not None and all(self.mapeo.get(c) for c in imp.CAMPOS_OBLIGATORIOS)
        self.btn_importar.set_state(bool(listo))
        if not listo:
            faltan = [imp.CAMPOS[c][0] for c in imp.CAMPOS_OBLIGATORIOS if not self.mapeo.get(c)]
            self.lbl_previa.config(text='VISTA PREVIA · falta asignar: ' + ', '.join(faltan) if self.df is not None
                                   else 'VISTA PREVIA', fg=ui.TEXT_MUTED)
            return
        muestra = self.df.head(MUESTRA_VALIDACION)
        try:
            fmt = self._formato_fecha(muestra)
            validas, errores = imp.validar(muestra, self.mapeo, fmt,
                                           self.productos if self.var_existentes.get() else None,
                                           imp.sucursales_y_ubicaciones(), self.var_suc.get())
        except ValueError as e:
            self.lbl_previa.config(text=f'VISTA PREVIA · {e}', fg=ui.DANGER)
            return
        for r in validas.head(FILAS_PREVIA).itertuples(index=False):
            self.tabla.insert('', 'end', values=(r.sku, r.fecha.date(), r.cantidad,
                                                 '' if r.precio_unitario != r.precio_unitario else r.precio_unitario,
                                                 r.sucursal, r.nombre))
        malas = errores['fila'].nunique() if len(errores) else 0
        top = errores['error'].value_counts().head(2).index.tolist() if malas else []
        self.lbl_previa.config(
            text=(f'VISTA PREVIA · fecha {fmt} · en las primeras {len(muestra)} filas: {len(validas)} válidas, '
                  f'{malas} con error' + (f' ({"; ".join(top)})' if top else '')),
            fg=ui.WARN if malas else ui.OK)

    def _importar(self):
        ruta, mapeo, suc = self.ruta, dict(self.mapeo), self.var_suc.get()
        fmt, existentes = self.var_fecha.get(), self.var_existentes.get()
        self.btn_importar.set_text('Importando…')
        self.btn_importar.set_state(False)

        def trabajo():
            return imp.importar(ruta, mapeo, self.db, formato_fecha=fmt, sucursal_defecto=suc,
                                exigir_producto_existente=existentes, log=lambda m: logger.info(m))

        def listo(res, err):
            if not self.winfo_exists():
                return
            self.btn_importar.set_text('Importar ventas')
            self.btn_importar.set_state(True)
            if err:
                messagebox.showerror('PREVÉ', f'No se pudo importar:\n{err}', parent=self)
                return
            if res['ruta_errores'] and messagebox.askyesno(
                    'PREVÉ', res.resumen + '\n\n¿Abrir el reporte de errores?', parent=self):
                self.app.abrir_archivo(res['ruta_errores'])
            elif not res['ruta_errores']:
                messagebox.showinfo('PREVÉ', res.resumen, parent=self)
            self.app.toast(res.resumen, 'ok')
        self.app.en_segundo_plano(trabajo, listo)
