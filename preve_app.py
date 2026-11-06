# -*- coding: utf-8 -*-
"""
PREVÉ — Aplicación de escritorio
==================================
Barra lateral + área de trabajo (el patrón de Shopify, Linear, Metabase):

  Inicio · Generar · Recomendaciones · Decisión rápida · Simular · Historial · Configuración

Atajos: Ctrl+G generar · Ctrl+S simular · Ctrl+K buscar · Ctrl+H historial
        Ctrl+R recomendaciones · Ctrl+D decisión rápida · Ctrl+I inicio
No requiere escribir ni ver código.
"""
import os
import sys
import re
import time
import queue
import random
import base64
import threading
import traceback
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import preve_ui as ui
from preve_ui import (S, f, Btn, Card, Chip, ProgressBar, Toasts, label, menu, mezclar,
                      BG, SURFACE, TEXT, TEXT_DIM, TEXT_MUTED, ACCENT, ACCENT_TINT, BORDER,
                      DANGER, BRAND_ORANGE)
import preve_datos as dt
import preve_vistas as vs
from preve_utils import safe_int, get_logger, configurar_logging

logger = get_logger('app')

# ══════════════════════════════════════════════════════════════════
# Frases, consejos y datos curiosos para mostrar mientras se genera
# ══════════════════════════════════════════════════════════════════
# FIX (Fase 7): se quitaron citas mal atribuidas o de autoría dudosa (p. ej. la de "adaptarse al cambio",
# que no es de Darwin) y una estadística sin fuente. Las que quedan tienen atribución reconocida.
FRASES_CARGA = [
    # — Frases de empresarios —
    '💡 "Hay un solo jefe: el cliente. Él puede despedir a todos, desde el presidente para abajo, simplemente gastando su dinero en otro lado." — Sam Walton, fundador de Walmart',
    '💡 "Haz un cliente, no una venta." — Katherine Barchetti',
    '💡 "Gestión es hacer las cosas bien; liderazgo es hacer lo correcto." — Peter Drucker',
    '💡 "Tu tiempo es limitado, no lo malgastes viviendo la vida de alguien distinto." — Steve Jobs',
    '💡 "No esperes. Nunca va a ser el momento justo. Empieza donde te encuentras." — Napoleón Hill',
    '💡 "Lo más peligroso es no evolucionar." — Jeff Bezos, fundador de Amazon',
    '💡 "El éxito no se mide por lo que logras, sino por los obstáculos que superas." — Booker T. Washington',

    # — Consejos de compras e inventario —
    '📦 Consejo: Un producto sin rotación ocupa espacio que podría usar un producto estrella. Revisa tus LIQUIDAR.',
    '📦 Consejo: El stock de seguridad protege contra lo inesperado, pero demasiado stock de seguridad congela tu capital.',
    '📦 Consejo: Negociar mejores precios con proveedores puede mejorar tu margen sin necesidad de subir precios al público.',
    '📦 Consejo: Los productos clase A (tu 20% más rentable) merecen revisión más frecuente que el resto.',
    '📦 Consejo: Antes de comprar más, verifica si otra sucursal tiene excedente del mismo producto.',
    '📦 Consejo: Un quiebre de stock (quedarte sin producto) puede costar más que el sobre-stock: pierdes la venta Y al cliente.',
    '📦 Consejo: Conocer tu lead time real es clave. Si tu proveedor tarda 36 días, necesitas planificar con 36 días de anticipación.',
    '📦 Consejo: Los productos de temporada necesitan un ojo especial: lo que se vende hoy puede no venderse el próximo mes.',
    '📦 Consejo: Diversificar proveedores reduce el riesgo de quedarte sin mercadería si uno falla.',

    # — Datos curiosos del comercio —
    '🌎 ¿Sabías que? Walmart, la tienda más grande del mundo, empezó como una sola tienda en Arkansas en 1962.',
    # FIX (usabilidad): se quitó "25 billones" (dato impreciso; el comercio global está sobre US$ 33 billones en 2024).
    '🌎 ¿Sabías que? El concepto de "código de barras" se inventó en 1952, pero no se usó comercialmente hasta 1974.',
    '🌎 ¿Sabías que? Amazon empezó vendiendo solo libros desde un garaje. Hoy es la tienda más grande del mundo online.',
    '🌎 ¿Sabías que? El método ABC de inventarios se basa en el principio de Pareto: el 20% de tus productos genera el 80% de tu ganancia.',
    '🌎 ¿Sabías que? La primera tienda por departamentos del mundo fue Le Bon Marché, abierta en París en 1838.',
    # FIX (usabilidad): se quitaron las frases hardcodeadas de Juliaca y de autopartes, que no aplican a cualquier empresa.

    # — Motivación para el usuario —
    '🚀 Cada vez que usas datos para decidir qué comprar, le llevas ventaja a tu competencia.',
    '🚀 PREVÉ analiza miles de productos en minutos. Manualmente, esto tomaría días.',
    '🚀 Un negocio que mide, mejora. Estás en el camino correcto.',
    '🚀 Mientras esperas, PREVÉ está entrenando 3 modelos de inteligencia artificial con tus datos reales.',
    '🚀 Invertir en los productos correctos es la diferencia entre crecer y solo sobrevivir.',
    '🚀 Tus sucursales pueden trabajar como un solo equipo: lo que sobra en una, se necesita en otra.',
]

PASOS = ['Cargando tus reportes del ERP', 'Identificando productos y códigos de barra',
         'Reconstruyendo el stock histórico', 'Segmentando productos',
         'Entrenando los modelos de pronóstico', 'Calculando compras internacionales',
         'Calculando compras locales']
PESOS_DEFECTO = [3, 5, 12, 12, 45, 13, 10]     # solo hasta la primera corrida; luego se usan tiempos reales


def parsear_frase(txt):
    """'💡 "..." — Autor' → (frase, autor). Quita el emoji y los prefijos 'Consejo:' / '¿Sabías que?'."""
    txt = txt.split(' ', 1)[1] if ' ' in txt else txt
    autor = ''
    if txt.startswith('"') and ' — ' in txt:
        txt, autor = txt.rsplit(' — ', 1)
        txt = txt.strip('"')
        autor = '— ' + autor
    else:
        for pre, etiqueta in (('Consejo: ', 'Consejo de inventario'), ('¿Sabías que? ', '¿Sabías que?')):
            if txt.startswith(pre):
                txt, autor = txt[len(pre):], etiqueta
        if not autor:
            autor = 'Para ti'
    return txt, autor


def notificar_windows(titulo, mensaje):
    """Notificación nativa de Windows (sin instalar nada). Si falla, no pasa nada."""
    if not sys.platform.startswith('win'):
        return
    esc = lambda s: s.replace("'", "''")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
          "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
          f"$t=$x.GetElementsByTagName('text');$t.Item(0).AppendChild($x.CreateTextNode('{esc(titulo)}'))|Out-Null;"
          f"$t.Item(1).AppendChild($x.CreateTextNode('{esc(mensaje)}'))|Out-Null;"
          "$n=[Windows.UI.Notifications.ToastNotification]::new($x);"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n)")
    cod = base64.b64encode(ps.encode('utf-16-le')).decode()
    try:
        subprocess.Popen(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-EncodedCommand', cod],
                         creationflags=0x08000000)
    except OSError:
        # FIX: antes except Exception: pass
        logger.warning('No se pudo mostrar la notificación de Windows', exc_info=True)


# ══════════════════════════════════════════════════════════════════
# Pantalla de carga completa
# ══════════════════════════════════════════════════════════════════
class PantallaCarga(tk.Frame):
    def __init__(self, app):
        super().__init__(app.root, bg=BG)
        self.app = app
        self.activo = False
        centro = tk.Frame(self, bg=BG)
        centro.place(relx=0.5, rely=0.46, anchor='center')
        self.titulo = label(centro, 'Analizando tu inventario', 24, 'bold')
        self.titulo.pack()
        self.sub = label(centro, '', 10, color=TEXT_DIM)
        self.sub.pack(pady=(S(4), S(26)))

        fila = tk.Frame(centro, bg=BG, width=S(560))
        fila.pack(fill='x')
        self.barra = ProgressBar(fila, 10)
        self.barra.pack(side='left', fill='x', expand=True)
        self.pct = label(fila, '0%', 11, 'bold', mono=True, width=5, anchor='e')
        self.pct.pack(side='left', padx=(S(10), 0))
        tk.Frame(centro, width=S(560), height=1, bg=BG).pack()
        self.paso = label(centro, '', 11, 'bold')
        self.paso.pack(pady=(S(12), S(2)))
        self.tiempo = label(centro, '', 10, color=TEXT_DIM, mono=True)
        self.tiempo.pack()

        card = self.card_frase = Card(centro, padx=34, pady=26, width=S(560))
        card.pack(pady=(S(30), S(24)))
        self.frase = label(card.body, '', 13, color=SURFACE, bg=SURFACE, wraplength=S(480), justify='center')
        self.frase.pack()
        self.autor = label(card.body, '', 10, color=SURFACE, bg=SURFACE)
        self.autor.pack(pady=(S(8), 0))

        botones = self.botones = tk.Frame(centro, bg=BG)
        botones.pack()
        Btn(botones, 'Minimizar y avisarme', self.minimizar, 'secondary', 'md').pack(side='left')
        self.btn_clasif = Btn(botones, 'Abrir clasificación de productos', None, 'soft', 'md')
        self.btn_detalle = Btn(botones, 'Ver detalle técnico', self._toggle_detalle, 'ghost', 'md')
        self.btn_detalle.pack(side='left', padx=(S(8), 0))
        self.log = tk.Text(centro, height=8, width=70, font=ui.f(9, mono=True), bg=SURFACE, fg=TEXT_DIM,
                           relief='flat', highlightthickness=1, highlightbackground=BORDER, wrap='word')
        self.detalle_visible = False
        self.error_btns = tk.Frame(centro, bg=BG)

    # ── ciclo de vida ──────────────────────────────────────
    def iniciar(self, pesos):
        self.activo = True
        self.t0 = time.time()
        self.paso_n, self.t_paso = 0, {}
        w = pesos if pesos and len(pesos) == 7 and sum(pesos) > 0 else PESOS_DEFECTO
        tot = float(sum(w))
        self.pesos = [x / tot for x in w]
        self.base = [sum(self.pesos[:i]) for i in range(7)]
        self.tau = [max(4.0, x * (self.app.cfg.get('ultima_duracion_s') or 240) / 2.5) for x in self.pesos]
        self.frac_mostrada = 0.0
        self.titulo.config(text='Analizando tu inventario', fg=TEXT)
        self.sub.config(text=f'{self.app.sucursal} · puedes minimizar esta ventana, te avisaremos al terminar')
        self.log.delete('1.0', 'end')
        self.error_btns.pack_forget()
        self.btn_clasif.pack_forget()
        self.log.pack_forget()
        self.detalle_visible = False
        if not self.botones.winfo_ismapped():
            self.botones.pack()
        if not self.card_frase.winfo_ismapped():
            self.card_frase.pack(before=self.botones, pady=(S(30), S(24)))
        self.paso.config(text='Preparando…')
        self.barra.set(0, ACCENT)
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        self._fase_frase = 0
        self._ciclo_frase()
        self._tick()

    def set_paso(self, n):
        if n == self.paso_n:
            return
        self.paso_n = n
        self.t_paso[n] = time.time()
        self.paso.config(text=f'Paso {n} de 7 — {PASOS[n - 1]}')

    def mostrar_clasificacion(self, ruta):
        """El Excel de clasificación sale antes de entrenar: se puede abrir mientras el análisis sigue."""
        self.btn_clasif.set_command(lambda: self.app.abrir_archivo(ruta))
        self.btn_clasif.pack(side='left', padx=(S(8), 0), before=self.btn_detalle)
        self.app.toast('Clasificación de productos lista: puedes abrirla mientras termina el análisis.', 'ok')

    def log_linea(self, t):
        self.log.insert('end', t + '\n')
        self.log.see('end')

    def terminar(self):
        self.activo = False
        self.place_forget()

    def duraciones_pasos(self):
        """Duración real de cada paso (para estimar mejor la próxima vez)."""
        fin, res = time.time(), []
        for n in range(1, 8):
            ini = self.t_paso.get(n)
            sig = self.t_paso.get(n + 1, fin)
            res.append(round(sig - ini, 1) if ini else 0)
        return res

    def mostrar_error(self, detalle):
        self.activo = False
        self.titulo.config(text='No se pudo completar el análisis', fg=DANGER)
        self.sub.config(text='Revisa el detalle: casi siempre es un reporte con otro nombre o faltante.')
        self.paso.config(text='')
        self.tiempo.config(text='')
        self.barra.set(self.frac_mostrada, DANGER)
        self.card_frase.pack_forget()
        self.botones.pack_forget()
        self._mostrar_detalle(True)
        self.log_linea('ERROR:\n' + detalle)
        for w in self.error_btns.winfo_children():
            w.destroy()
        Btn(self.error_btns, 'Volver', self.terminar, 'primary', 'md').pack(side='left')
        Btn(self.error_btns, 'Copiar detalle', lambda: self._copiar(detalle), 'secondary', 'md'
            ).pack(side='left', padx=(S(8), 0))
        self.error_btns.pack(pady=(S(12), 0))

    def _copiar(self, txt):
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.app.toast('Detalle copiado', 'ok')

    # ── piezas ─────────────────────────────────────────────
    def minimizar(self):
        self.app.root.iconify()

    def _toggle_detalle(self):
        self._mostrar_detalle(not self.detalle_visible)

    def _mostrar_detalle(self, v):
        self.detalle_visible = v
        if v:
            self.log.pack(pady=(S(14), 0))
        else:
            self.log.pack_forget()

    def _tick(self):
        if not self.activo:
            return
        el = time.time() - self.t0
        n = self.paso_n
        if n:
            dentro = time.time() - self.t_paso[n]
            avance = 1 - 2.718 ** (-dentro / self.tau[n - 1])
            objetivo = self.base[n - 1] + self.pesos[n - 1] * min(0.93, avance)
        else:
            objetivo = 0.01
        self.frac_mostrada = max(self.frac_mostrada, min(0.99, objetivo))
        self.barra.set(self.frac_mostrada)
        self.pct.config(text=f'{self.frac_mostrada * 100:.0f}%')
        ult = self.app.cfg.get('ultima_duracion_s')
        if ult:
            falta = max(0, ult - el)
        elif self.frac_mostrada > 0.08:
            falta = el * (1 - self.frac_mostrada) / self.frac_mostrada
        else:
            falta = None
        if falta is None:
            txt = f'Llevamos {vs.fmt_mmss(el)} — calculando el tiempo restante…'
        elif ult and el > ult:
            txt = f'Llevamos {vs.fmt_mmss(el)} — ya casi, unos segundos más'
        else:
            txt = f'Llevamos {vs.fmt_mmss(el)} — faltan ~{vs.fmt_mmss(falta)}'
        self.tiempo.config(text=txt)
        self.after(150, self._tick)

    def _ciclo_frase(self):
        """Frase grande y centrada que rota con fundido."""
        if not self.activo:
            return
        if self._fase_frase == 0:
            usadas = getattr(self, '_usadas', [])
            if len(usadas) >= len(FRASES_CARGA):
                usadas = []
            f_ = random.choice([x for x in FRASES_CARGA if x not in usadas])
            self._usadas = usadas + [f_]
            txt, aut = parsear_frase(f_)
            self.frase.config(text=txt)
            self.autor.config(text=aut)
            self._fase_frase, self._t = 1, 0
        if self._fase_frase == 1:
            self._color(self._t / 8)
            self._t += 1
            if self._t > 8:
                self._fase_frase = 2
            self.after(40, self._ciclo_frase)
        elif self._fase_frase == 2:
            self._fase_frase, self._t = 3, 8
            self.after(8000, self._ciclo_frase)
        elif self._fase_frase == 3:
            self._color(self._t / 8)
            self._t -= 1
            if self._t < 0:
                self._fase_frase = 0
            self.after(40, self._ciclo_frase)

    def _color(self, t):
        self.frase.config(fg=mezclar(SURFACE, TEXT, t))
        self.autor.config(fg=mezclar(SURFACE, TEXT_DIM, t))


# ══════════════════════════════════════════════════════════════════
# Buscador global (Ctrl+K)
# ══════════════════════════════════════════════════════════════════
class Paleta:
    def __init__(self, app):
        self.app = app
        root = app.root
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.configure(bg=BORDER)
        W = S(640)
        x = root.winfo_rootx() + (root.winfo_width() - W) // 2
        self.top.geometry(f'+{x}+{root.winfo_rooty() + S(110)}')
        box = tk.Frame(self.top, bg=SURFACE)
        box.pack(fill='both', expand=True, padx=1, pady=1)
        tk.Frame(box, width=W, height=1, bg=SURFACE).pack()
        cab = tk.Frame(box, bg=SURFACE)
        cab.pack(fill='x', padx=S(18), pady=S(14))
        label(cab, '⌕', 16, color=TEXT_MUTED).pack(side='left')
        self.var = tk.StringVar()
        self.ent = tk.Entry(cab, textvariable=self.var, font=f(13), bg=SURFACE, fg=TEXT, relief='flat', bd=0,
                            highlightthickness=0, insertbackground=TEXT)
        self.ent.pack(side='left', fill='x', expand=True, padx=(S(10), 0))
        tk.Frame(box, bg=BORDER, height=1).pack(fill='x')
        self.lista = tk.Frame(box, bg=SURFACE)
        self.lista.pack(fill='x')
        pie = tk.Frame(box, bg=BG)
        pie.pack(fill='x')
        label(pie, '↑ ↓ moverte   ·   Enter abrir   ·   Esc cerrar', 8, color=TEXT_MUTED, bg=BG
              ).pack(side='left', padx=S(18), pady=S(7))
        self.items, self.sel, self._deb = [], 0, None
        self.var.trace_add('write', self._al_escribir)
        self.ent.bind('<Escape>', lambda e: self.cerrar())
        self.ent.bind('<Down>', lambda e: self._mover(1))
        self.ent.bind('<Up>', lambda e: self._mover(-1))
        self.ent.bind('<Return>', lambda e: self._abrir())
        self.top.bind('<FocusOut>', self._perdio_foco)
        self.top.lift()
        self.top.focus_force()
        self.ent.focus_force()
        self._pintar()

    def cerrar(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        self.app.paleta = None

    def _perdio_foco(self, e):
        self.top.after(150, lambda: self.cerrar() if self.top.winfo_exists() and not self.top.focus_displayof() else None)

    def _al_escribir(self, *a):
        if self._deb:
            self.top.after_cancel(self._deb)
        self._deb = self.top.after(140, self._buscar)

    def _buscar(self):
        self.items, self.sel = [], 0
        res = self.app.datos.buscar(self.var.get(), limite=4)
        for r in res['recomendacion']:
            self.items.append(dict(grupo='En la recomendación', titulo=r['nombre'] or r['sku'],
                                   sub=f'{r["sku"]} · {r["accion"]}' + (f' · comprar {safe_int(r["compra_sugerida_unid"])} u'
                                                                         if r['es_compra'] else ''),
                                   chip=r['urgencia'] if r['es_compra'] else None,
                                   go=lambda r=r: self.app.ir('recom', sku=r['sku'])))
        for h in res['historial']:
            self.items.append(dict(grupo='En el historial', titulo=h['nombre'] or h['sku'],
                                   sub=f'{h["sku"]} · aparece en {h["veces"]} análisis · última acción {h["accion"]}',
                                   go=lambda: self.app.ir('historial')))
        for t in res['traslados']:
            self.items.append(dict(grupo='En traslados', titulo=str(t.get('nombre', t['sku'])),
                                   sub=f'{t["cantidad"]} u · {t["desde"]} → {t["hacia"]}',
                                   go=lambda: self.app.abrir_traslados()))
        self._pintar()

    def _mover(self, d):
        if self.items:
            self.sel = (self.sel + d) % len(self.items)
            self._pintar()
        return 'break'

    def _abrir(self, i=None):
        i = self.sel if i is None else i
        if 0 <= i < len(self.items):
            go = self.items[i]['go']
            self.cerrar()
            go()

    def _pintar(self):
        for w in self.lista.winfo_children():
            w.destroy()
        if not self.items:
            q = self.var.get().strip()
            msg = ('Escribe el nombre de un producto, un SKU o un proveedor.' if len(q) < 2 else
                   f'Sin resultados para “{q}”.')
            label(self.lista, msg, 10, color=TEXT_MUTED, anchor='w').pack(fill='x', padx=S(20), pady=S(18))
            return
        ultimo = None
        for i, it in enumerate(self.items):
            if it['grupo'] != ultimo:
                label(self.lista, it['grupo'].upper(), 8, 'bold', TEXT_MUTED, anchor='w'
                      ).pack(fill='x', padx=S(20), pady=(S(10), S(2)))
                ultimo = it['grupo']
            bg = ACCENT_TINT if i == self.sel else SURFACE
            fila = tk.Frame(self.lista, bg=bg, cursor='hand2')
            fila.pack(fill='x')
            col = tk.Frame(fila, bg=bg)
            col.pack(side='left', padx=S(20), pady=S(7))
            label(col, vs.acortar(it['titulo'], 62), 10, 'bold', bg=bg).pack(anchor='w')
            label(col, it['sub'], 8, color=TEXT_DIM, bg=bg, mono=True).pack(anchor='w')
            if it.get('chip'):
                Chip(fila, it['chip'].capitalize(), it['chip']).pack(side='right', padx=S(20))
            vs.bind_click(fila, lambda i=i: self._abrir(i))


# ══════════════════════════════════════════════════════════════════
# Aplicación
# ══════════════════════════════════════════════════════════════════
# Fase 6: en modo backtesting se ocultan el dashboard (Inicio), las sugerencias y los informes
OCULTAS_EN_BACKTESTING = {'inicio', 'recom', 'decision', 'simular'}
SOLO_EN_BACKTESTING = {'backtesting'}

NAV = [('inicio', 'Inicio', '⌂'), ('generar', 'Generar', '▶'), ('recom', 'Recomendaciones', '☰'),
       ('decision', 'Decisión rápida', '⚡'), ('simular', 'Simular', '◐'), ('historial', 'Historial', '↺'),
       # FIX (usabilidad): etiqueta en lenguaje común en vez de "Backtesting"
       ('backtesting', 'Probar qué tan bien acierta PREVÉ', '⟲'),
       ('config', 'Configuración', '⚙')]


class PreveApp:
    def __init__(self, root):
        self.root = root
        ui.init_ui(root)
        self.cfg = dt.cargar_config()
        self.sucursal = self.cfg['sucursal'] if self.cfg['sucursal'] in dt.sucursales() else dt.sucursales()[0]
        self.generando = False
        self.modo_backtesting = False
        self.backtesting_corriendo = False
        self.paleta = None
        self.cola = queue.Queue()
        self._cola_ui = queue.Queue()
        self.vistas, self.vista_actual, self.nombre_actual = {}, None, None

        root.title(f'PREVÉ · {dt.empresa_nombre()}')  # FIX (Fase 3): empresa configurada
        root.geometry(f'{S(1240)}x{S(800)}')
        root.minsize(S(1020), S(660))
        root.configure(bg=BG)

        self.toasts = Toasts(root)
        self.datos = dt.Datos(self.carpeta('salida'), self.sucursal)
        self._construir()
        self.carga = PantallaCarga(self)
        self._atajos()
        root.protocol('WM_DELETE_WINDOW', self._cerrar)
        self._poll_ui()
        self.ir('inicio')
        self.root.after(300, self._avisar_ciclo_corrupto)
        # Fase 3: si la configuración inicial no está completa, se abre el asistente
        if not dt.Config.actual().get('wizard_completado'):
            self.root.after(500, self.abrir_asistente)

    # ── Fase 4: fuentes de datos ───────────────────────────
    def abrir_importador(self):
        from preve_importador_ui import VentanaImportacion
        VentanaImportacion(self)

    def descargar_desde_erp(self):
        """Descarga los reportes con el plugin configurado dentro de la carpeta de reportes de la sucursal."""
        from plugins import obtener_plugin
        conf = dt.Config.actual()
        plugin = obtener_plugin(conf.get('plugin_erp'))
        if plugin is None:
            self.toast('No hay una integración con ERP configurada.', 'warn')
            return
        destino = self.carpeta('entrada')
        if not destino:
            self.toast('Elige primero la carpeta de reportes del ERP.', 'warn')
            return
        opciones = conf.get('plugin_opciones') or {}
        self.toast(f'Descargando desde {plugin.nombre}… puede tardar varios minutos.', 'info')

        def listo(carpeta, err):
            if err:
                self.toast(f'No se pudo descargar desde {plugin.nombre}: {err}', 'error')
                return
            self.toast(f'Reportes descargados en {os.path.basename(carpeta)}. Ya puedes generar.', 'ok')
            if self.nombre_actual == 'generar':
                self.refrescar()
        self.en_segundo_plano(lambda: plugin.extraer(destino, opciones, log=logger.info), listo)

    def abrir_asistente(self, paso=0):
        from preve_wizard import Asistente
        Asistente(self.root, al_terminar=self._config_cambiada, paso_inicial=paso)

    def _config_cambiada(self):
        """Tras el asistente o el panel de Configuración: recargar todo lo que depende de la configuración."""
        dt.Config.actual().invalidar()
        self.cfg = dt.cargar_config()
        if self.sucursal not in dt.sucursales():
            self.sucursal = self.cfg['sucursal'] if self.cfg['sucursal'] in dt.sucursales() else dt.sucursales()[0]
            self.btn_suc.set_text(f'{self.sucursal} ▾')
        self.root.title(f'PREVÉ · {dt.empresa_nombre()}')
        self.lbl_empresa.config(text=dt.empresa_nombre())
        self.btn_usuario.set_text(f'{self.cfg["usuario"]} ▾')
        self.recargar_datos()
        sim = self.vistas.pop('simular', None)        # sus sliders dependen del ciclo configurado
        if sim is not None:
            if sim is self.vista_actual:
                sim.pack_forget()
                self.vista_actual = None
            sim.destroy()
        self.refrescar()
        self.toast('Configuración guardada', 'ok')

    # ── estructura ─────────────────────────────────────────
    def _construir(self):
        r = self.root
        tk.Frame(r, bg=BRAND_ORANGE, height=S(4)).pack(fill='x')           # única aparición del naranja de Yuju
        top = tk.Frame(r, bg=SURFACE, height=S(60))
        top.pack(fill='x')
        top.pack_propagate(False)
        tk.Frame(r, bg=BORDER, height=1).pack(fill='x')

        logo = tk.Frame(top, bg=SURFACE, width=S(230))
        logo.pack(side='left', fill='y')
        logo.pack_propagate(False)
        label(logo, 'PREVÉ', 17, 'bold', ACCENT).pack(side='left', padx=(S(22), S(8)))
        self.lbl_empresa = label(logo, dt.empresa_nombre(), 9, color=TEXT_MUTED)
        self.lbl_empresa.pack(side='left', pady=(S(4), 0))

        buscar = tk.Frame(top, bg=BG, highlightthickness=1, highlightbackground=BORDER, cursor='hand2')
        buscar.pack(side='left', padx=S(12), pady=S(12), ipadx=S(6), ipady=S(2))
        label(buscar, '⌕  Buscar producto, SKU o proveedor…', 10, color=TEXT_MUTED, bg=BG, width=42, anchor='w'
              ).pack(side='left', padx=(S(8), 0))
        label(buscar, 'Ctrl K', 8, 'bold', TEXT_MUTED, mono=True, bg=BG).pack(side='left', padx=S(8))
        vs.bind_click(buscar, self.abrir_paleta)

        self.btn_usuario = Btn(top, f'{self.cfg["usuario"]} ▾', None, 'ghost', 'md')
        self.btn_usuario.set_command(self._menu_usuario)
        self.btn_usuario.pack(side='right', padx=(0, S(16)), pady=S(10))
        self.btn_suc = Btn(top, f'{self.sucursal} ▾', None, 'secondary', 'md', min_width=130)
        self.btn_suc.set_command(self._menu_sucursal)
        self.btn_suc.pack(side='right', padx=S(6), pady=S(10))
        self.btn_bt = tk.Label(top, text='⟲  Modo Backtesting', font=f(9, 'bold'), cursor='hand2', padx=S(12),
                               pady=S(6), bg=SURFACE, fg=TEXT_DIM, highlightthickness=1,
                               highlightbackground=BORDER)
        self.btn_bt.pack(side='right', padx=S(6), pady=S(12))
        self.btn_bt.bind('<Button-1>', lambda e: self.alternar_backtesting())

        cuerpo = tk.Frame(r, bg=BG)
        cuerpo.pack(fill='both', expand=True)
        self.side = tk.Frame(cuerpo, bg=SURFACE, width=S(230))
        self.side.pack(side='left', fill='y')
        self.side.pack_propagate(False)
        tk.Frame(cuerpo, bg=BORDER, width=1).pack(side='left', fill='y')
        self.area = tk.Frame(cuerpo, bg=BG)
        self.area.pack(side='left', fill='both', expand=True)

        self.nav = {}
        tk.Frame(self.side, bg=SURFACE, height=S(12)).pack()
        self.sep_config = tk.Frame(self.side, bg=BORDER, height=1)
        for clave, texto, icono in NAV:
            self.nav[clave] = self._item_nav(clave, texto, icono)
        self._repintar_nav()
        self.ciclo_box = tk.Frame(self.side, bg=SURFACE)
        self.ciclo_box.pack(side='bottom', fill='x', padx=S(18), pady=S(18))
        self.actualizar_sidebar()

    def _item_nav(self, clave, texto, icono):
        fila = tk.Frame(self.side, bg=SURFACE, height=S(40), cursor='hand2')
        fila.pack(fill='x', padx=S(10), pady=S(1))
        fila.pack_propagate(False)
        barra = tk.Frame(fila, bg=SURFACE, width=S(3))
        barra.pack(side='left', fill='y', pady=S(8))
        ic = tk.Label(fila, text=icono, font=('Segoe UI Symbol', 12), bg=SURFACE, fg=TEXT_DIM, width=2)
        ic.pack(side='left', padx=(S(10), S(4)))
        tx = tk.Label(fila, text=texto, font=f(10), bg=SURFACE, fg=TEXT)
        tx.pack(side='left')
        badge = tk.Label(fila, text='', font=f(8, 'bold'), bg=SURFACE, fg='#FFFFFF')
        badge.pack(side='right', padx=S(10))
        vs.bind_click(fila, lambda c=clave: self.ir(c))
        return dict(fila=fila, barra=barra, ic=ic, tx=tx, badge=badge)

    def _repintar_nav(self):
        """Muestra solo los ítems que corresponden al modo actual (normal o backtesting)."""
        for w in list(self.nav.values()) + [None]:
            (w['fila'] if w else self.sep_config).pack_forget()
        for clave, _, _ in NAV:
            oculto = (clave in OCULTAS_EN_BACKTESTING) if self.modo_backtesting else (clave in SOLO_EN_BACKTESTING)
            if oculto:
                continue
            if clave == 'config':
                self.sep_config.pack(fill='x', padx=S(16), pady=S(8), before=self.ciclo_box
                                     if hasattr(self, 'ciclo_box') else None)
            self.nav[clave]['fila'].pack(fill='x', padx=S(10), pady=S(1), before=self.ciclo_box
                                         if hasattr(self, 'ciclo_box') else None)

    def alternar_backtesting(self):
        """Fase 6: activa/desactiva el modo backtesting (botón naranja de la barra superior)."""
        if self.generando:
            return
        if self.modo_backtesting and self.backtesting_corriendo:
            self.toast('Hay un backtesting en curso: cancélalo o espera a que termine.', 'warn')
            return
        self.modo_backtesting = not self.modo_backtesting
        on = self.modo_backtesting
        self.btn_bt.configure(bg=BRAND_ORANGE if on else SURFACE, fg='#FFFFFF' if on else TEXT_DIM,
                              highlightbackground=BRAND_ORANGE if on else BORDER,
                              text='⟲  Backtesting ACTIVO' if on else '⟲  Modo Backtesting')
        self._repintar_nav()
        if on:
            self.ir('backtesting')
        else:
            # 6.5: lo calculado (ventas, pronósticos, métricas) queda en la base para el modo normal
            self.recargar_datos()
            self.ir('inicio')
            self.toast('Modo normal. Los resultados del backtesting quedan guardados en la base.', 'info')

    def _marcar_nav(self, activo):
        for c, w in self.nav.items():
            on = c == activo
            bg = ACCENT_TINT if on else SURFACE
            for k in ('fila', 'ic', 'tx'):
                w[k].configure(bg=bg)
            w['barra'].configure(bg=ACCENT if on else bg)
            w['ic'].configure(fg=ACCENT if on else TEXT_DIM)
            w['tx'].configure(fg=ACCENT if on else TEXT, font=f(10, 'bold' if on else 'normal'))
            w['badge'].configure(bg=bg)
        self.actualizar_badge()

    def actualizar_badge(self):
        n = len(self.datos.pendientes()) if self.datos.tiene_datos else 0
        w = self.nav['recom']
        w['badge'].configure(text=f' {n} ' if n else '',
                             bg=DANGER if n else w['fila'].cget('bg'))

    def actualizar_sidebar(self):
        for w in self.ciclo_box.winfo_children():
            w.destroy()
        ci = self.datos.info_ciclo()
        if not ci:
            label(self.ciclo_box, 'Sin ciclo activo', 9, color=TEXT_MUTED).pack(anchor='w')
            return
        label(self.ciclo_box, f'CICLO #{ci["numero"]}', 8, 'bold', TEXT_MUTED).pack(anchor='w')
        barra = ProgressBar(self.ciclo_box, 6)
        barra.pack(fill='x', pady=S(5))
        barra.set(ci['dia'] / ci['dias_totales'], DANGER if ci['vencido'] else ACCENT)
        label(self.ciclo_box, f'Día {ci["dia"]} de {ci["dias_totales"]}', 9, color=TEXT_DIM).pack(anchor='w')
        self.actualizar_badge()

    # ── navegación ─────────────────────────────────────────
    def _vista(self, nombre):
        if nombre not in self.vistas:
            clase = dict(inicio=vs.VistaInicio, generar=vs.VistaGenerar, recom=vs.VistaRecom,
                         decision=vs.VistaDecision, simular=vs.VistaSimular, historial=vs.VistaHistorial,
                         config=vs.VistaConfig, backtesting=self._clase_backtesting())[nombre]
            self.vistas[nombre] = clase(self.area, self)
        return self.vistas[nombre]

    @staticmethod
    def _clase_backtesting():
        from preve_vista_backtesting import VistaBacktesting
        return VistaBacktesting

    def ir(self, nombre, **kw):
        if self.generando:
            return
        if self.modo_backtesting and nombre in OCULTAS_EN_BACKTESTING:
            self.toast('Esa sección está oculta en modo backtesting. Desactívalo para volver.', 'info')
            return
        if not self.modo_backtesting and nombre in SOLO_EN_BACKTESTING:
            return
        if self.vista_actual is not None:
            self.vista_actual.pack_forget()
        v = self._vista(nombre)
        v.pack(fill='both', expand=True)
        self.vista_actual, self.nombre_actual = v, nombre
        self._marcar_nav(nombre)
        self.root.focus_set()
        v.on_show(**kw)

    def ir_generar(self):
        self.ir('generar')

    def refrescar(self):
        if self.nombre_actual:
            self.ir(self.nombre_actual)

    # ── menús superiores ───────────────────────────────────
    def _menu_sucursal(self):
        m = menu(self.root)
        for s in dt.sucursales():
            m.add_command(label=('✓  ' if s == self.sucursal else '     ') + s,
                          command=lambda s=s: self.cambiar_sucursal(s))
        b = self.btn_suc
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _menu_usuario(self):
        m = menu(self.root)
        m.add_command(label='Configuración', command=lambda: self.ir('config'))
        m.add_command(label='Atajos de teclado', command=lambda: self.ir('config'))
        b = self.btn_usuario
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def cambiar_sucursal(self, s):
        if s == self.sucursal:
            return
        self.sucursal = self.cfg['sucursal'] = s
        dt.guardar_config(self.cfg)
        self.btn_suc.set_text(f'{s} ▾')
        self.recargar_datos()
        self.refrescar()
        self.toast(f'Sucursal activa: {s}', 'info')

    def guardar_usuario(self, nombre):
        self.cfg['usuario'] = nombre
        dt.guardar_config(self.cfg)
        self.btn_usuario.set_text(f'{nombre} ▾')
        self.toast('Nombre guardado', 'ok')

    # ── Fase 8: depurar datos (empezar de cero para pruebas limpias) ──
    def depurar_datos(self, conservar_config=True):
        """
        Borra toda la data histórica (ventas, pronósticos, métricas, reportes
        generados, logs). Antes empaqueta todo en un ZIP de respaldo. La
        configuración (empresa, sucursales, ciclos, plantillas, parámetros)
        se conserva por defecto.
        """
        from tkinter import messagebox
        import preve_depurar as depurar
        prev = depurar.preview_depuracion()
        titulo = 'Depurar datos — ¿empezar de cero?'
        detalle = [
            'Se BORRARÁ:',
            f'  · {len(prev["archivos"])} archivos ({depurar._humano(sum(a["bytes"] for a in prev["archivos"]))})',
            f'  · {len(prev["carpetas"])} carpetas ({depurar._humano(sum(c["bytes"] for c in prev["carpetas"]))})',
            f'  · {len(prev["periodos"])} análisis guardados en las carpetas de resultados '
            f'({depurar._humano(sum(c["bytes"] for c in prev["periodos"]))}); '
            f'ordenes_compra_abiertas.csv y tus otros archivos no se tocan',
            f'  · {len(prev["tablas_a_vaciar"])} tablas de datos de la base',
            '',
            'Se CONSERVA:' if conservar_config else 'No se conservará nada (empezar completamente de cero).',
        ]
        if conservar_config:
            detalle.append(f'  · Configuración: empresa, sucursales, ciclos, plantillas, parámetros, usuario')
            detalle.append(f'  · ({len(prev["tablas_a_conservar"])} tablas de configuración intactas)')
        detalle.extend([
            '',
            'Antes de borrar se creará un ZIP de respaldo en respaldos_depurar/.',
            'Esta acción NO se puede deshacer (sólo restaurando el ZIP a mano).',
            '',
            '¿Continuar?',
        ])
        if not messagebox.askyesno(titulo, '\n'.join(detalle), icon='warning', parent=self.root):
            return
        try:
            reporte = depurar.depurar_datos(conservar_config=conservar_config, hacer_respaldo=True)
        except (OSError, RuntimeError) as e:
            logger.error('Error depurando datos', exc_info=True)
            self.toast(f'No se pudo depurar: {e}', 'error')
            return
        resumen = depurar.resumen_para_usuario(reporte)
        logger.info('Depuración completada: %s', resumen)
        # Forzar recarga completa
        dt.Config.actual().invalidar()
        self.cfg = dt.cargar_config()
        self.recargar_datos()
        self.refrescar()
        messagebox.showinfo('Depurar datos — completado',
                            f'{resumen}\n\nAhora puedes importar datos nuevos para tu prueba de 6 meses.',
                            parent=self.root)

    # ── datos / carpetas ───────────────────────────────────
    def carpeta(self, clave):
        return self.cfg['sucursales'].setdefault(self.sucursal, {'entrada': '', 'salida': ''}).get(clave, '')

    def elegir_carpeta(self, clave):
        titulo = ('Selecciona la carpeta con tus reportes del ERP' if clave == 'entrada'
                  else 'Selecciona dónde guardar los resultados')
        ruta = filedialog.askdirectory(title=titulo, initialdir=self.carpeta(clave) or None)
        if not ruta:
            return False
        self.cfg['sucursales'][self.sucursal][clave] = ruta
        dt.guardar_config(self.cfg)
        if clave == 'salida':
            self.recargar_datos()
        return True

    def recargar_datos(self):
        self.datos = dt.Datos(self.carpeta('salida'), self.sucursal)
        self.actualizar_sidebar()
        self._avisar_ciclo_corrupto()

    def _avisar_ciclo_corrupto(self):
        # FIX: un ciclo_estado.json dañado ya no se reinicia en silencio: se pide intervención
        if getattr(self.datos, 'error_ciclo', None):
            messagebox.showerror('PREVÉ — ciclo de compras', self.datos.error_ciclo)

    def menu_informe(self, btn):
        m = menu(self.root)
        m.add_command(label='Abrir informe en PDF', command=lambda: self.informe('pdf'))
        m.add_command(label='Abrir informe en Word', command=lambda: self.informe('docx'))
        m.add_separator()
        m.add_command(label='Abrir carpeta de resultados', command=self.abrir_carpeta_resultados)
        m.add_command(label='Actualizar informe (incluye tus decisiones)', command=lambda: self.informe(None))
        m.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())

    def informe(self, abrir='pdf'):
        """Genera (o regenera) el informe para gerencia y abre el formato pedido."""
        if not self.datos.tiene_datos:
            self.toast('Primero genera una recomendación para armar el informe.', 'warn')
            return
        carpeta, suc = self.datos.carpeta, self.sucursal
        self.toast('Armando el informe para gerencia…', 'info', accion=None)

        def trabajo():
            from preve_informe import generar_informe
            avisos = []
            return generar_informe(carpeta, suc, log=avisos.append), avisos

        def listo(res, err):
            if err:
                self.toast(f'No se pudo generar el informe: {err}', 'error')
                return
            rutas, avisos = res
            if not rutas:
                self.toast(avisos[-1] if avisos else 'No se pudo generar el informe.', 'error')
                return
            if abrir and abrir in rutas:
                self.abrir_archivo(rutas[abrir])
                self.toast('Informe listo', 'ok')
            elif abrir:
                self.toast(f'Falta una librería para el formato {abrir.upper()}; revisa INSTALAR_Y_EJECUTAR.', 'warn')
            else:
                self.toast('Informe actualizado (PDF y Word).', 'ok')
        self.en_segundo_plano(trabajo, listo)

    def abrir_traslados(self):
        """Abre PREVE_Traslados_Sugeridos.csv del período más reciente (en la raíz multi-sucursal)."""
        raiz = os.path.dirname(self.datos.carpeta or '')
        for base in (dt.ultimo_periodo_de(raiz, False), self.datos.carpeta_periodo):
            ruta = os.path.join(base or '', 'PREVE_Traslados_Sugeridos.csv')
            if base and os.path.exists(ruta):
                self.abrir_archivo(ruta)
                return
        self.toast('Todavía no hay traslados sugeridos.', 'warn')

    def abrir_carpeta_resultados(self):
        """Abre la carpeta del período más reciente (no la raíz de salida)."""
        carpeta = self.datos.carpeta_periodo or self.datos.ultimo_periodo(False)
        if not carpeta or not os.path.isdir(carpeta):
            self.toast('Todavía no hay resultados: genera una recomendación primero.', 'warn')
            return
        self.abrir_archivo(carpeta)

    def abrir_archivo(self, path):
        if not os.path.exists(path):
            self.toast('Ese archivo todavía no existe.', 'warn')
            return
        if sys.platform.startswith('win'):
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])

    def toast(self, msg, tipo='info', accion=None):
        self.toasts.show(msg, tipo, accion=accion)

    # ── trabajos en segundo plano (el resultado vuelve por el hilo principal) ──
    def en_segundo_plano(self, trabajo, al_terminar):
        def correr():
            try:
                res = (trabajo(), None)
            except Exception as e:
                res = (None, e)
            self._cola_ui.put((al_terminar, res))
        threading.Thread(target=correr, daemon=True).start()

    def _poll_ui(self):
        try:
            while True:
                fn, (res, err) = self._cola_ui.get_nowait()
                fn(res, err)
        except queue.Empty:
            pass
        self.root.after(60, self._poll_ui)

    # ── atajos ─────────────────────────────────────────────
    def _atajos(self):
        def reg(tecla, fn):
            for t in (tecla, tecla.upper()):
                self.root.bind_all(f'<Control-{t}>', lambda e, fn=fn: (fn(), 'break')[1])
        reg('g', self._atajo_generar)
        reg('s', self._atajo_simular)
        reg('k', self.abrir_paleta)
        reg('h', lambda: self.ir('historial'))
        reg('r', lambda: self.ir('recom'))
        reg('d', lambda: self.ir('decision'))
        reg('i', lambda: self.ir('inicio'))
        for k in ('Left', 'Right'):
            self.root.bind_all(f'<{k}>', lambda e, k=k: self._flecha(k))

    def _flecha(self, k):
        if isinstance(self.root.focus_get(), (tk.Entry, tk.Text)):
            return
        if self.vista_actual and not self.generando:
            self.vista_actual.tecla(k)

    def _atajo_generar(self):
        if self.generando:
            return
        if self.nombre_actual == 'generar':
            self.generar()
        else:
            self.ir('generar')

    def _atajo_simular(self):
        if self.generando:
            return
        if self.nombre_actual == 'simular':
            self.vista_actual.simular()
        else:
            self.ir('simular')

    def abrir_paleta(self):
        if self.generando:
            return
        if self.paleta:
            self.paleta.cerrar()
            return
        self.paleta = Paleta(self)

    # ── generar ────────────────────────────────────────────
    def generar(self):
        if self.generando:
            return
        if not self.carpeta('entrada') or not self.carpeta('salida'):
            self.toast('Elige primero las dos carpetas (reportes y resultados).', 'warn')
            self.ir('generar')
            return
        if self.paleta:
            self.paleta.cerrar()
        self.generando = True
        self.cola = queue.Queue()
        self.carga.iniciar(self.cfg.get('pasos_s'))
        threading.Thread(target=self._ejecutar, daemon=True).start()
        self._poll()

    def _ejecutar(self):
        try:
            from preve_orquestador import ejecutar_pipeline
            ejecutar_pipeline(self.carpeta('entrada'), self.carpeta('salida'),
                              progreso=lambda t: self.cola.put(('log', str(t))), sucursal=self.sucursal)
            self.cola.put(('fin', None))
        except Exception as e:
            self.cola.put(('error', f'{e}\n\n{traceback.format_exc()}'))

    def _poll(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == 'log':
                    self.carga.log_linea(dato)
                    mc = re.search(r'Excel de clasificación listo:\s*(.+)$', dato)
                    if mc:
                        self.carga.mostrar_clasificacion(mc.group(1).strip())
                    m = re.search(r'Paso\s+(\d)\s*/\s*7', dato)
                    if m and 1 <= int(m.group(1)) <= 7:
                        self.carga.set_paso(int(m.group(1)))
                elif tipo == 'fin':
                    # FIX: try/finally — si _al_terminar_ok falla, la app no queda bloqueada en "generando"
                    try:
                        self._al_terminar_ok()
                    except Exception as e:  # noqa: BLE001 — se muestra en pantalla, no se pierde
                        logger.exception('Falló la actualización de la interfaz tras el análisis')
                        self.carga.mostrar_error(f'El análisis terminó, pero falló al mostrar los resultados: '
                                                 f'{e}\n\n{traceback.format_exc()}')
                    finally:
                        self.generando = False
                    return
                elif tipo == 'error':
                    try:
                        self.carga.mostrar_error(dato)
                        if self.root.state() == 'iconic':
                            self.root.deiconify()
                            notificar_windows('PREVÉ', 'El análisis no se pudo completar. Revisa el detalle.')
                    finally:
                        self.generando = False
                    return
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _al_terminar_ok(self):
        dur = time.time() - self.carga.t0
        pasos = self.carga.duraciones_pasos()
        self.cfg['ultima_duracion_s'] = round(dur, 1)
        if all(p > 0 for p in pasos):
            self.cfg['pasos_s'] = pasos
        dt.guardar_config(self.cfg)
        minimizada = self.root.state() == 'iconic'
        self.carga.terminar()
        self.generando = False     # antes de ir(): ir() no navega mientras generando es True
        self.recargar_datos()
        if minimizada:
            self.root.deiconify()
        self.root.lift()
        self.ir('inicio')
        k = self.datos.kpis()
        resumen = (f'{k["pendientes_n"]} productos por recomprar · {dt.fmt_soles(k["inversion_pendiente"])}'
                   if k else 'Recomendación lista')
        self.toast(f'¡Recomendación lista! {resumen}', 'ok',
                   accion=('Revisar', lambda: self.ir('recom')))
        notificar_windows('PREVÉ — recomendación lista', resumen)

    def _cerrar(self):
        if self.backtesting_corriendo and not messagebox.askyesno(
                'PREVÉ', 'Hay un backtesting en marcha. Si cierras ahora se perderá.\n\n¿Cerrar de todos modos?'):
            return
        if self.generando and not messagebox.askyesno(
                'PREVÉ', 'El análisis sigue en marcha. Si cierras ahora se perderá el avance.\n\n¿Cerrar de todos modos?'):
            return
        self.root.destroy()


if __name__ == '__main__':
    configurar_logging()
    ui.init_dpi()
    root = tk.Tk()
    app = PreveApp(root)
    root.mainloop()
