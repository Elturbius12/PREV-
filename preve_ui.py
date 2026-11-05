# -*- coding: utf-8 -*-
"""
PREVÉ — Sistema de diseño y widgets de la interfaz.

Paleta "Verde salvia profesional": el acento aparece solo en acciones y datos
clave. El naranja de Yuju Store queda únicamente como banda delgada del header.
"""
import os
import sys
import ctypes
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from preve_utils import get_logger

logger = get_logger('ui')

# ── Paleta ─────────────────────────────────────────────────────────
BG = '#F7F8F6'
SURFACE = '#FFFFFF'
TEXT = '#1A2B23'
TEXT_DIM = '#5E6F66'
TEXT_MUTED = '#8A978F'
ACCENT = '#2D7D5F'
ACCENT_HOVER = '#256A50'
ACCENT_TINT = '#E3F0EA'
CARD_ALT = '#E8EDE8'
BORDER = '#DDE3DD'
OK = '#4A9B6E'
WARN = '#C78A2E'
DANGER = '#B4453A'
OK_BG = '#E1F0E7'
WARN_BG = '#F7EBD3'
DANGER_BG = '#F6E2DF'
BRAND_ORANGE = '#FF6B00'          # Yuju Store: solo la banda del header

CHIPS = {                          # (texto, fondo)
    'URGENTE': (DANGER, DANGER_BG),
    'PRONTO': ('#8E6119', WARN_BG),
    'MONITOREAR': ('#2F7656', OK_BG),
    'neutral': (TEXT_DIM, CARD_ALT),
    'accent': (ACCENT, ACCENT_TINT),
    'ALTA': ('#2F7656', OK_BG),
    'MEDIA': ('#8E6119', WARN_BG),
    'BAJA': (DANGER, DANGER_BG),
    'RECOMPRAR': (ACCENT, ACCENT_TINT),
    'VIGILAR': ('#3B6EA5', '#E4ECF4'),
    'REDUCIR': ('#8E6119', WARN_BG),
    'LIQUIDAR': (DANGER, DANGER_BG),
    'PUBLICITAR': ('#6B5B95', '#ECE8F3'),
    'NEGOCIAR': ('#A0602A', '#F4E8DC'),
    'TRASLADAR': ('#2F7F86', '#DDEEF0'),
    'PRELIMINAR': ('#3B6EA5', '#E4ECF4'),
    'BORRADOR': ('#8E6119', WARN_BG),
    'FINAL': ('#2F7656', OK_BG),
    'comprado': ('#2F7656', OK_BG),
    'pospuesto': ('#8E6119', WARN_BG),
    'descartado': (TEXT_DIM, CARD_ALT),
    'pendiente': (TEXT_DIM, CARD_ALT),
}

# ── Escala (DPI) y tipografías ─────────────────────────────────────
SCALE = 1.0
F_UI = 'Segoe UI'
F_MONO = 'Consolas'


def init_dpi():
    """Hay que llamarlo ANTES de crear la ventana para que el texto sea nítido."""
    if sys.platform.startswith('win'):
        # FIX: excepciones específicas en vez de except Exception: pass (Windows antiguos no tienen shcore)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                logger.info('No se pudo activar el modo DPI; el texto puede verse borroso')


def S(v):
    """Píxeles lógicos (a 96 dpi) → píxeles reales de esta pantalla."""
    return int(round(v * SCALE))


def init_ui(root):
    global SCALE, F_UI, F_MONO
    try:
        SCALE = max(1.0, root.winfo_fpixels('1i') / 96.0)
    except tk.TclError:   # FIX: antes except Exception
        SCALE = 1.0
    # Fuentes opcionales: si pones Inter / JetBrains Mono (.ttf) en la carpeta "fonts" se usan solas.
    carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
    if sys.platform.startswith('win') and os.path.isdir(carpeta):
        for nombre in os.listdir(carpeta):
            if nombre.lower().endswith(('.ttf', '.otf')):
                try:
                    ctypes.windll.gdi32.AddFontResourceExW(os.path.join(carpeta, nombre), 0x10, 0)
                except (AttributeError, OSError):
                    # FIX: antes except Exception: pass
                    logger.warning('No se pudo cargar la fuente %s', nombre)
    familias = set(tkfont.families(root))
    F_UI = 'Inter' if 'Inter' in familias else 'Segoe UI'
    F_MONO = 'JetBrains Mono' if 'JetBrains Mono' in familias else 'Consolas'
    _init_styles(root)


def f(size=10, weight='normal', mono=False, italic=False):
    estilo = weight + (' italic' if italic else '')
    return (F_MONO if mono else F_UI, size, estilo.strip())


def _init_styles(root):
    st = ttk.Style(root)
    st.theme_use('clam')
    st.layout('Preve.Vertical.TScrollbar',
              [('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
                  ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
    st.configure('Preve.Vertical.TScrollbar', background=BORDER, troughcolor=BG, borderwidth=0,
                 relief='flat', width=S(10), gripcount=0, lightcolor=BORDER, darkcolor=BORDER, bordercolor=BG)
    st.map('Preve.Vertical.TScrollbar', background=[('active', TEXT_MUTED), ('pressed', TEXT_DIM)])


def _bg(widget):
    try:
        return widget.cget('bg')
    except (tk.TclError, AttributeError):   # FIX: antes except Exception (widget sin opción bg)
        return BG


def rrect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def mezclar(c1, c2, t):
    """Color intermedio entre c1 y c2 (t=0 → c1, t=1 → c2). Sirve para los fundidos."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ══════════════════════════════════════════════════════════════════
# Widgets
# ══════════════════════════════════════════════════════════════════
class Card(tk.Canvas):
    """Tarjeta con esquinas redondeadas sutiles. Se llena a través de .body"""

    def __init__(self, parent, bg=SURFACE, outline=BORDER, radius=8, padx=18, pady=16, **kw):
        super().__init__(parent, bg=_bg(parent), highlightthickness=0, bd=0, **kw)
        self._fill, self._outline, self._r = bg, outline, S(radius)
        self._px, self._py = S(padx), S(pady)
        self.body = tk.Frame(self, bg=bg)
        self._win = self.create_window(self._px, self._py, window=self.body, anchor='nw')
        self.bind('<Configure>', self._on_cfg)
        self.body.bind('<Configure>', self._on_body)

    def _on_cfg(self, e):
        self.itemconfigure(self._win, width=max(1, e.width - 2 * self._px))
        self._redraw()

    def _on_body(self, e):
        h = self.body.winfo_reqheight() + 2 * self._py
        if int(self.cget('height')) != h:
            self.configure(height=h)
        self._redraw()

    def _redraw(self):
        self.delete('bg')
        w, h = self.winfo_width(), self.winfo_height()
        if w > 2 and h > 2:
            rrect(self, 1, 1, w - 1, h - 1, self._r, fill=self._fill, outline=self._outline, tags='bg')
            self.tag_lower('bg')


class Btn(tk.Canvas):
    KINDS = {
        'primary': dict(bg=ACCENT, hover=ACCENT_HOVER, fg='#FFFFFF', border=ACCENT),
        'secondary': dict(bg=SURFACE, hover=CARD_ALT, fg=TEXT, border=BORDER),
        'ghost': dict(bg=None, hover=CARD_ALT, fg=TEXT_DIM, border=None),
        'danger': dict(bg=SURFACE, hover=DANGER_BG, fg=DANGER, border=BORDER),
        'soft': dict(bg=ACCENT_TINT, hover='#D3E8DE', fg=ACCENT, border=None),
    }
    SIZES = {'sm': (30, 9, 12), 'md': (38, 10, 18), 'lg': (52, 12, 30)}

    def __init__(self, parent, text, command=None, kind='primary', size='md', anchor='center', min_width=0):
        h, fs, px = self.SIZES[size]
        self._font = f(fs, 'bold')
        self._text, self._cmd, self._kind, self._anchor = text, command, kind, anchor
        self._h, self._px = S(h), S(px)
        self._hover, self._disabled = False, False
        w = max(S(min_width), tkfont.Font(font=self._font).measure(text) + 2 * self._px)
        super().__init__(parent, width=w, height=self._h, bg=_bg(parent), highlightthickness=0, bd=0,
                         cursor='hand2')
        self.bind('<Configure>', lambda e: self._draw())
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.bind('<ButtonRelease-1>', self._click)

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def _click(self, e):
        if self._disabled or not self._cmd:
            return
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._cmd()

    def set_text(self, text):
        self._text = text
        self._draw()

    def set_command(self, cmd):
        self._cmd = cmd

    def set_state(self, enabled):
        self._disabled = not enabled
        self.configure(cursor='hand2' if enabled else 'arrow')
        self._draw()

    def _draw(self):
        self.delete('all')
        w, h = self.winfo_width(), self._h
        if w < 4:
            return
        st = self.KINDS[self._kind]
        if self._disabled:
            bg, fg, border = CARD_ALT, TEXT_MUTED, None
        else:
            bg = st['hover'] if self._hover else st['bg']
            fg, border = st['fg'], st['border']
        if bg:
            rrect(self, 1, 1, w - 1, h - 1, S(6), fill=bg, outline=border or bg)
        if self._anchor == 'w':
            self.create_text(self._px, h / 2, text=self._text, font=self._font, fill=fg, anchor='w')
        else:
            self.create_text(w / 2, h / 2, text=self._text, font=self._font, fill=fg)


class Chip(tk.Canvas):
    """Píldora de estado (urgencia, acción, confianza...)."""

    def __init__(self, parent, text, kind='neutral', fg=None, bg=None):
        c_fg, c_bg = CHIPS.get(kind, CHIPS['neutral'])
        fg, bg = fg or c_fg, bg or c_bg
        fnt = f(8, 'bold')
        h = S(22)
        w = tkfont.Font(font=fnt).measure(text) + S(20)
        super().__init__(parent, width=w, height=h, bg=_bg(parent), highlightthickness=0, bd=0)
        rrect(self, 0, 0, w, h, h // 2, fill=bg, outline=bg)
        self.create_text(w / 2, h / 2, text=text, font=fnt, fill=fg)


class ProgressBar(tk.Canvas):
    def __init__(self, parent, height=8, track=CARD_ALT, color=ACCENT):
        super().__init__(parent, height=S(height), bg=_bg(parent), highlightthickness=0, bd=0)
        self._v, self._track, self._color = 0.0, track, color
        self.bind('<Configure>', lambda e: self._draw())

    def set(self, frac, color=None):
        self._v = max(0.0, min(1.0, frac))
        if color:
            self._color = color
        self._draw()

    def _draw(self):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        rrect(self, 0, 0, w, h, h // 2, fill=self._track, outline=self._track)
        fw = int(w * self._v)
        if fw >= h:
            rrect(self, 0, 0, fw, h, h // 2, fill=self._color, outline=self._color)
        elif fw > 0:
            self.create_oval(0, 0, h, h, fill=self._color, outline=self._color)


class Slider(tk.Canvas):
    """Control deslizante (en vez de cajas numéricas)."""

    def __init__(self, parent, desde, hasta, valor, paso=1, command=None):
        super().__init__(parent, height=S(30), bg=_bg(parent), highlightthickness=0, bd=0, cursor='hand2')
        self.desde, self.hasta, self.paso, self._cmd = desde, hasta, paso, command
        self._v = valor
        self.bind('<Configure>', lambda e: self._draw())
        self.bind('<Button-1>', self._mover)
        self.bind('<B1-Motion>', self._mover)
        self.bind('<Left>', lambda e: self.set(self._v - self.paso, True))
        self.bind('<Right>', lambda e: self.set(self._v + self.paso, True))

    def get(self):
        return self._v

    def set(self, v, notificar=False):
        v = round((max(self.desde, min(self.hasta, v)) - self.desde) / self.paso) * self.paso + self.desde
        v = max(self.desde, min(self.hasta, v))
        self._v = int(v) if float(v).is_integer() else v
        self._draw()
        if notificar and self._cmd:
            self._cmd(self._v)

    def _mover(self, e):
        m = S(10)
        w = max(1, self.winfo_width() - 2 * m)
        self.focus_set()
        self.set(self.desde + (e.x - m) / w * (self.hasta - self.desde), True)

    def _draw(self):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 30:
            return
        m, cy, th = S(10), h // 2, S(6)
        x = m + (self._v - self.desde) / (self.hasta - self.desde) * (w - 2 * m)
        rrect(self, m, cy - th // 2, w - m, cy + th // 2, th // 2, fill=CARD_ALT, outline=CARD_ALT)
        rrect(self, m, cy - th // 2, max(x, m + th), cy + th // 2, th // 2, fill=ACCENT, outline=ACCENT)
        r = S(9)
        self.create_oval(x - r, cy - r, x + r, cy + r, fill=SURFACE, outline=ACCENT, width=S(2))


class ScrollFrame(tk.Frame):
    """Zona con scroll vertical; el contenido va en .inner"""

    def __init__(self, parent, bg=BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview,
                                 style='Preve.Vertical.TScrollbar')
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.vsb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind('<Enter>', self._bind_wheel)
        self.bind('<Leave>', self._unbind_wheel)

    def _on_scroll(self, lo, hi):
        self.vsb.set(lo, hi)

    def _bind_wheel(self, e=None):
        self.bind_all('<MouseWheel>', self._wheel)

    def _unbind_wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            if w is self:
                return
            w = getattr(w, 'master', None)
        self.unbind_all('<MouseWheel>')

    def _wheel(self, e):
        lo, hi = self.canvas.yview()
        if lo <= 0.0 and hi >= 1.0:
            return
        self.canvas.yview_scroll(int(-e.delta / 120 * 3), 'units')

    def ir_arriba(self):
        self.canvas.yview_moveto(0)

    def ver_widget(self, w):
        """Desplaza hasta que el widget quede visible."""
        self.update_idletasks()
        total = max(1, self.inner.winfo_height())
        y = w.winfo_rooty() - self.inner.winfo_rooty()
        self.canvas.yview_moveto(max(0, (y - S(80))) / total)


class Skeleton(tk.Frame):
    """Esqueleto de carga con brillo suave, mientras se leen los datos."""

    def __init__(self, parent, filas=5, bg=SURFACE):
        super().__init__(parent, bg=bg)
        self._barras = []
        for _ in range(filas):
            fila = tk.Frame(self, bg=bg)
            fila.pack(fill='x', pady=S(10), padx=S(16))
            for ancho, expand in ((48, False), (260, True), (90, False), (70, False)):
                b = tk.Frame(fila, bg=CARD_ALT, height=S(14), width=S(ancho))
                b.pack(side='left', padx=(0, S(16)), fill='x' if expand else None, expand=expand)
                self._barras.append(b)
        self._fase, self._activo = 0.0, True
        self._animar()

    def _animar(self):
        if not self._activo or not self.winfo_exists():
            return
        self._fase = (self._fase + 0.08) % 2
        t = self._fase if self._fase <= 1 else 2 - self._fase
        color = mezclar(CARD_ALT, '#F4F6F4', t)
        for b in self._barras:
            b.configure(bg=color)
        self.after(50, self._animar)

    def detener(self):
        self._activo = False
        self.destroy()


class Toasts:
    """Avisos chicos abajo-derecha que desaparecen solos (en vez de popups bloqueantes)."""

    COLORES = {'ok': OK, 'error': DANGER, 'info': ACCENT, 'warn': WARN}

    def __init__(self, root):
        self.root = root
        self.activos = []

    def show(self, msg, tipo='info', ms=4000, accion=None):
        t = tk.Frame(self.root, bg=TEXT)
        tk.Frame(t, bg=self.COLORES.get(tipo, ACCENT), width=S(4)).pack(side='left', fill='y')
        tk.Label(t, text=msg, bg=TEXT, fg='#FFFFFF', font=f(10), wraplength=S(320), justify='left',
                 padx=S(14), pady=S(10)).pack(side='left')
        if accion:
            texto, cb = accion
            b = tk.Label(t, text=texto, bg=TEXT, fg='#9FD8BE', font=f(10, 'bold'), cursor='hand2',
                         padx=S(12))
            b.pack(side='left')
            b.bind('<Button-1>', lambda e: (cb(), self._cerrar(t)))
        self.activos.append(t)
        self._acomodar()
        t.after(ms, lambda: self._cerrar(t))

    def _cerrar(self, t):
        if t in self.activos:
            self.activos.remove(t)
            t.destroy()
            self._acomodar()

    def _acomodar(self):
        y = S(22)
        for t in reversed(self.activos):
            t.place(relx=1.0, rely=1.0, x=-S(22), y=-y, anchor='se')
            t.lift()
            t.update_idletasks()
            y += t.winfo_reqheight() + S(8)


def entry(parent, var=None, width=None, justify='left', fuente=None):
    """Campo de texto plano con borde que se pinta de acento al enfocar."""
    return tk.Entry(parent, textvariable=var, width=width, justify=justify, font=fuente or f(10),
                    bg=SURFACE, fg=TEXT, relief='flat', bd=0, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=ACCENT, insertbackground=TEXT)


def label(parent, text, size=10, weight='normal', color=TEXT, mono=False, italic=False, **kw):
    return tk.Label(parent, text=text, font=f(size, weight, mono, italic), fg=color,
                    bg=kw.pop('bg', _bg(parent)), **kw)


def menu(parent):
    return tk.Menu(parent, tearoff=0, bg=SURFACE, fg=TEXT, activebackground=ACCENT_TINT,
                   activeforeground=ACCENT, relief='flat', bd=1, font=f(10))
