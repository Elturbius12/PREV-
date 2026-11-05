# -*- coding: utf-8 -*-
"""
PREVÉ — Editor visual de plantillas de categorías de acción (Fase 5).

Todo se elige con listas desplegables y campos numéricos (sin escribir código): variable, operador y
valor (un número u otra variable × factor). La vista previa cuenta cuántos productos de la última
recomendación caerían en cada acción.

Las plantillas del sistema no se sobrescriben: al guardar cambios se pide un nombre nuevo.
"""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import preve_reglas as rg
import preve_ui as ui
from preve_ui import S, Btn, label

ACCIONES_SUGERIDAS = ['RECOMPRAR', 'MANTENER', 'LIQUIDAR', 'REDUCIR', 'PUBLICITAR', 'NEGOCIAR', 'DESCONTINUAR',
                      'VIGILAR', 'REPONER', 'REABASTECER', 'PRODUCIR_MAS', 'PROMOCIONAR', 'TRANSFERIR_SUCURSAL',
                      'SUSTITUIR']
TIPO_NUMERO, TIPO_VARIABLE = 'número', 'otra variable ×'


class EditorPlantillas(tk.Toplevel):
    def __init__(self, app, nombre):
        super().__init__(app.root, bg=ui.BG)
        from database import Database
        self.app, self.db = app, Database.default()
        self.original = rg.cargar_plantilla(nombre, self.db)
        self.p = rg.copiar(self.original)
        sistema = self.db.listar_plantillas_categorias()
        self.es_sistema = bool(sistema.loc[sistema['nombre'] == nombre, 'es_plantilla_sistema'].iloc[0])
        self.title(f'PREVÉ · Plantilla {nombre}')
        self.geometry(f'{S(1040)}x{S(760)}')
        self.minsize(S(900), S(600))
        self.transient(app.root)
        self._construir()
        self._render()

    # ── estructura ─────────────────────────────────────────
    def _construir(self):
        cab = tk.Frame(self, bg=ui.SURFACE)
        cab.pack(fill='x')
        self.lbl_titulo = label(cab, '', 15, 'bold', bg=ui.SURFACE)
        self.lbl_titulo.pack(anchor='w', padx=S(22), pady=(S(14), 0))
        label(cab, 'Las reglas se revisan en orden: cada producto toma la acción de la PRIMERA regla que cumple. '
                   'Dentro de una regla, todas las condiciones deben cumplirse.', 9, color=ui.TEXT_DIM, bg=ui.SURFACE,
              wraplength=S(980), justify='left').pack(anchor='w', padx=S(22), pady=(0, S(12)))
        self.cuerpo = ui.ScrollFrame(self)
        self.cuerpo.pack(fill='both', expand=True)
        self.lbl_previa = label(self, '', 9, color=ui.TEXT_DIM, wraplength=S(980), justify='left')
        self.lbl_previa.pack(fill='x', padx=S(22), pady=(S(6), 0))
        pie = tk.Frame(self, bg=ui.BG)
        pie.pack(fill='x', padx=S(22), pady=S(12))
        Btn(pie, 'Guardar', self._guardar, 'primary', 'md', min_width=140).pack(side='right')
        Btn(pie, 'Vista previa con mis datos', self._previa, 'secondary', 'md').pack(side='right', padx=S(8))
        Btn(pie, 'Cerrar', self.destroy, 'ghost', 'md').pack(side='left')

    def _render(self):
        for w in self.cuerpo.inner.winfo_children():
            w.destroy()
        self.lbl_titulo.config(text=f'Plantilla {self.p["nombre"]}' + (' (del sistema)' if self.es_sistema else ''))
        c = tk.Frame(self.cuerpo.inner, bg=ui.BG)
        c.pack(fill='both', expand=True, padx=S(22), pady=S(8))
        acciones = sorted(set(ACCIONES_SUGERIDAS) | set(rg.acciones(self.p)))
        for i, regla in enumerate(self.p['reglas']):
            self._regla(c, i, regla, acciones)
        Btn(c, '+ Agregar regla', self._agregar_regla, 'soft', 'sm').pack(anchor='w', pady=S(8))

        fin = tk.Frame(c, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        fin.pack(fill='x', pady=(S(10), S(4)))
        label(fin, 'Si ninguna regla se cumple', 9, 'bold', bg=ui.SURFACE).pack(side='left', padx=S(12), pady=S(10))
        self.var_defecto = tk.StringVar(value=self.p['accion_por_defecto'])
        cb = ttk.Combobox(fin, textvariable=self.var_defecto, values=acciones, width=22)
        cb.pack(side='left')
        cb.bind('<<ComboboxSelected>>', lambda e: self.p.update(accion_por_defecto=self.var_defecto.get()))
        cb.bind('<FocusOut>', lambda e: self.p.update(accion_por_defecto=self.var_defecto.get().strip().upper()))

        compra = tk.Frame(c, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        compra.pack(fill='x', pady=S(4))
        label(compra, 'Acciones que implican comprar (suman a la inversión y a las notas de compra)', 9, 'bold',
              bg=ui.SURFACE).pack(anchor='w', padx=S(12), pady=(S(8), S(2)))
        fila = tk.Frame(compra, bg=ui.SURFACE)
        fila.pack(anchor='w', padx=S(8), pady=(0, S(8)))
        self.vars_compra = {}
        for a in rg.acciones(self.p):
            v = tk.BooleanVar(value=a in self.p.get('acciones_de_compra', []))
            tk.Checkbutton(fila, text=a, variable=v, command=self._actualizar_compra, font=ui.f(9), bg=ui.SURFACE,
                           activebackground=ui.SURFACE).pack(side='left', padx=S(4))
            self.vars_compra[a] = v

    def _regla(self, parent, i, regla, acciones):
        caja = tk.Frame(parent, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        caja.pack(fill='x', pady=S(4))
        top = tk.Frame(caja, bg=ui.SURFACE)
        top.pack(fill='x', padx=S(10), pady=(S(8), S(4)))
        label(top, f'{i + 1}.', 10, 'bold', bg=ui.SURFACE).pack(side='left')
        var_acc = tk.StringVar(value=regla['accion'])
        cb = ttk.Combobox(top, textvariable=var_acc, values=acciones, width=22)
        cb.pack(side='left', padx=S(6))

        def poner_accion(e=None):
            regla['accion'] = var_acc.get().strip().upper()
        cb.bind('<<ComboboxSelected>>', poner_accion)
        cb.bind('<FocusOut>', poner_accion)
        var_desc = tk.StringVar(value=regla.get('descripcion', ''))
        ui.entry(top, var_desc, width=60).pack(side='left', padx=S(6), ipady=S(2))
        var_desc.trace_add('write', lambda *a: regla.update(descripcion=var_desc.get()))
        for txt, cmd in (('↑', lambda: self._mover(i, -1)), ('↓', lambda: self._mover(i, 1)),
                         ('Quitar', lambda: self._quitar_regla(i))):
            Btn(top, txt, cmd, 'ghost', 'sm').pack(side='right')
        for j, cond in enumerate(regla['condiciones']):
            self._condicion(caja, regla, j, cond)
        Btn(caja, '+ condición (y…)', lambda: self._agregar_condicion(regla), 'ghost', 'sm').pack(
            anchor='w', padx=S(30), pady=(0, S(8)))

    def _condicion(self, parent, regla, j, cond):
        fila = tk.Frame(parent, bg=ui.SURFACE)
        fila.pack(fill='x', padx=S(30), pady=S(2))
        label(fila, 'si' if j == 0 else 'y', 9, color=ui.TEXT_MUTED, bg=ui.SURFACE, width=3).pack(side='left')
        nombres = list(rg.VARIABLES)
        v_var = tk.StringVar(value=cond['variable'])
        ttk.Combobox(fila, textvariable=v_var, values=nombres, width=18, state='readonly').pack(side='left')
        v_op = tk.StringVar(value=cond['operador'])
        ttk.Combobox(fila, textvariable=v_op, values=list(rg.OPERADORES), width=4, state='readonly').pack(
            side='left', padx=S(4))
        es_var = isinstance(cond['valor'], dict)
        v_tipo = tk.StringVar(value=TIPO_VARIABLE if es_var else TIPO_NUMERO)
        ttk.Combobox(fila, textvariable=v_tipo, values=[TIPO_NUMERO, TIPO_VARIABLE], width=13,
                     state='readonly').pack(side='left', padx=S(4))
        v_num = tk.StringVar(value=str(cond['valor'].get('factor', 1) if es_var else cond['valor']))
        ui.entry(fila, v_num, width=8).pack(side='left', padx=S(4), ipady=S(2))
        v_ref = tk.StringVar(value=cond['valor']['variable'] if es_var else 'pronostico_sem')
        cb_ref = ttk.Combobox(fila, textvariable=v_ref, values=nombres, width=18, state='readonly')
        if es_var:
            cb_ref.pack(side='left', padx=S(4))
        ayuda = label(fila, rg.VARIABLES[cond['variable']][0], 8, color=ui.TEXT_MUTED, bg=ui.SURFACE)
        ayuda.pack(side='left', padx=S(8))
        Btn(fila, '✕', lambda: self._quitar_condicion(regla, j), 'ghost', 'sm').pack(side='right')

        def actualizar(*a):
            cond['variable'], cond['operador'] = v_var.get(), v_op.get()
            ayuda.config(text=rg.VARIABLES[cond['variable']][0])
            try:
                n = float(v_num.get().replace(',', '.'))
            except ValueError:
                n = None
            if v_tipo.get() == TIPO_VARIABLE:
                cond['valor'] = {'variable': v_ref.get(), 'factor': 1.0 if n is None else n}
                if not cb_ref.winfo_ismapped():
                    cb_ref.pack(side='left', padx=S(4), before=ayuda)
            else:
                cond['valor'] = 0.0 if n is None else n
                cb_ref.pack_forget()
        for v in (v_var, v_op, v_tipo, v_num, v_ref):
            v.trace_add('write', actualizar)

    # ── edición ────────────────────────────────────────────
    def _agregar_regla(self):
        self.p['reglas'].append({'accion': 'MANTENER', 'descripcion': '',
                                 'condiciones': [{'variable': 'stock_dias', 'operador': '>', 'valor': 0}]})
        self._render()

    def _quitar_regla(self, i):
        if len(self.p['reglas']) == 1:
            messagebox.showinfo('PREVÉ', 'La plantilla necesita al menos una regla.', parent=self)
            return
        del self.p['reglas'][i]
        self._render()

    def _mover(self, i, d):
        j = i + d
        if 0 <= j < len(self.p['reglas']):
            r = self.p['reglas']
            r[i], r[j] = r[j], r[i]
            self._render()

    def _agregar_condicion(self, regla):
        regla['condiciones'].append({'variable': 'margen', 'operador': '>', 'valor': 0})
        self._render()

    def _quitar_condicion(self, regla, j):
        if len(regla['condiciones']) == 1:
            messagebox.showinfo('PREVÉ', 'Cada regla necesita al menos una condición.', parent=self)
            return
        del regla['condiciones'][j]
        self._render()

    def _actualizar_compra(self):
        self.p['acciones_de_compra'] = [a for a, v in self.vars_compra.items() if v.get()]

    # ── vista previa y guardado ────────────────────────────
    def _previa(self):
        d = self.app.datos
        if not d.tiene_datos:
            self.lbl_previa.config(text='Genera primero una recomendación para ver la vista previa.', fg=ui.WARN)
            return
        try:
            from preve_datos import horizonte, lead_time
            nuevo = rg.vista_previa(d.df, self.p, horizonte(), lead_time())
            antes = rg.vista_previa(d.df, self.original, horizonte(), lead_time())
        except rg.ReglaInvalida as e:
            self.lbl_previa.config(text=f'Regla inválida: {e}', fg=ui.DANGER)
            return
        partes = []
        for a in sorted(set(nuevo) | set(antes), key=lambda x: -nuevo.get(x, 0)):
            n, m = nuevo.get(a, 0), antes.get(a, 0)
            partes.append(f'{a}: {n:,}' + (f' ({n - m:+,})' if n != m else ''))
        self.lbl_previa.config(text=f'Con tus {len(d.df):,} productos → ' + ' · '.join(partes)
                                    + '   (entre paréntesis, el cambio frente a la plantilla guardada)', fg=ui.TEXT)

    def _guardar(self):
        try:
            rg.validar_plantilla(self.p)
        except rg.ReglaInvalida as e:
            messagebox.showerror('PREVÉ', str(e), parent=self)
            return
        if self.es_sistema:
            nombre = simpledialog.askstring('PREVÉ', 'Las plantillas del sistema no se modifican. '
                                                     'Nombre para tu copia:', parent=self,
                                            initialvalue=f'{self.p["nombre"]}_PROPIA')
            if not nombre or not nombre.strip():
                return
            self.p['nombre'] = nombre.strip().upper()
            self.es_sistema = False
        rg.guardar_plantilla(self.p, self.db)
        from preve_config import Config
        Config.actual().set('plantilla_categorias', self.p['nombre'])
        self.original = rg.copiar(self.p)
        self._render()
        self.app.toast(f'Plantilla {self.p["nombre"]} guardada y activada. Se aplica en el próximo análisis.', 'ok')
