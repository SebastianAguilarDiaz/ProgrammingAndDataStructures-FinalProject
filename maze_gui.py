#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Neon Maze: visualizador luminoso de laberintos y recorridos de a.exe.

Uso:
    python maze_gui.py [laberinto.in]

La interfaz abre un .in con selector de archivos, ejecuta a.exe y dibuja el
laberinto 2D junto con los .sol generados (preorden, postorden, BFS y
soluciones), respetando el orden exacto de cada archivo.
"""

from __future__ import annotations

import ctypes
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Neon Maze"
APP_SUBTITLE = "Laberintos con recorridos luminosos"
PAD = 24
TICK_MS = 40
SOLVER_TIMEOUT = 120
SPEED_VALUES = [1, 2, 5, 10, 25, 50, 100, 250, 1000]

# (clave del archivo .sol, etiqueta, grupo, descripción)
KINDS = [
    ("preorder", "Preorden", "tree", "Orden de visita del preorden, tal como viene en el .sol."),
    ("postorder", "Postorden", "tree", "Orden de visita del postorden, tal como viene en el .sol."),
    ("bfs", "BFS por niveles", "tree", "Orden de visita BFS, tal como viene en el .sol."),
    ("dfs_solution", "Ruta DFS", "path", "Solución DFS con el orden exacto del archivo: B → A."),
    ("bfs_solution", "Ruta BFS", "path", "Solución BFS con el orden exacto del archivo: B → A."),
]
KIND_LABEL = {key: label for key, label, _, _ in KINDS}
KIND_DESC = {key: desc for key, _, _, desc in KINDS}
PATH_KINDS = {"dfs_solution", "bfs_solution"}
PATH_COLORS = {"dfs_solution": "#35e7ff", "bfs_solution": "#ff4fd8"}


class MazeError(Exception):
    """Error de formato del archivo .in."""


class Maze:
    __slots__ = ("rows", "cols", "grid", "root", "target", "path_cells", "warnings")

    def __init__(self, rows, cols, grid, root, target, path_cells, warnings):
        self.rows = rows
        self.cols = cols
        self.grid = grid
        self.root = root
        self.target = target
        self.path_cells = path_cells
        self.warnings = warnings


def parse_maze(path):
    """Lee el .in con la misma semántica que loadMatrix() de maze.cpp.

    El C++ usa `fin >> filas >> columnas` y luego `fin >> caracter`, por lo que
    los caracteres se consumen como un flujo continuo, ignorando saltos de
    línea. Esto mantiene las coordenadas alineadas con los .sol de a.exe.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    tokens = text.split()
    if len(tokens) < 2:
        raise MazeError("El archivo debe iniciar con el número de filas y columnas.")

    try:
        rows = int(tokens[0])
        cols = int(tokens[1])
    except ValueError:
        raise MazeError("Las primeras dos palabras deben ser números: filas y columnas.")

    if rows <= 0 or cols <= 0:
        raise MazeError("Las filas y columnas deben ser mayores que cero.")
    if rows * cols > 4_000_000:
        raise MazeError("El laberinto es demasiado grande para dibujarlo.")

    chars = "".join(tokens[2:])
    needed = rows * cols
    if len(chars) < needed:
        raise MazeError(
            "Faltan celdas: el encabezado pide {} y el archivo solo tiene {}.".format(needed, len(chars))
        )

    warnings = []
    if len(chars) > needed:
        warnings.append(
            "Hay {} caracteres extra después de la cuadrícula; a.exe los ignora.".format(len(chars) - needed)
        )

    grid = [list(chars[r * cols:(r + 1) * cols]) for r in range(rows)]

    # Compara el largo de las líneas con las columnas declaradas (solo si hay
    # exactamente una línea por fila, que es el formato esperado).
    raw_lines = [line for line in text.splitlines() if line.strip()]
    if len(raw_lines) - 1 == rows:
        header = raw_lines[0].split()
        if len(header) == 2:
            bad = []
            for idx, line in enumerate(raw_lines[1:], start=2):
                width = len("".join(line.split()))
                if width != cols:
                    bad.append((idx, width))
            if bad:
                first_line, first_width = bad[0]
                if len(bad) == 1:
                    warnings.append(
                        "La línea {} tiene {} caracteres; el encabezado declara {} columnas.".format(
                            first_line, first_width, cols
                        )
                    )
                else:
                    warnings.append(
                        "{} líneas tienen {} caracteres; el encabezado declara {} columnas. "
                        "a.exe los reacomoda como flujo continuo.".format(len(bad), first_width, cols)
                    )

    # Primer A y primer B en orden de lectura, igual que findNode().
    root = target = None
    extra_root = extra_target = 0
    for r in range(rows):
        for c in range(cols):
            ch = grid[r][c]
            if ch == "A":
                if root is None:
                    root = (r, c)
                else:
                    extra_root += 1
            elif ch == "B":
                if target is None:
                    target = (r, c)
                else:
                    extra_target += 1

    if root is None:
        warnings.append("No se encontró 'A' en las celdas leídas.")
    if target is None:
        warnings.append("No se encontró 'B' en las celdas leídas.")
    if extra_root:
        warnings.append("Hay {} 'A' extra; se usa la primera (fila {}, col {}).".format(
            extra_root, root[0], root[1]))
    if extra_target:
        warnings.append("Hay {} 'B' extra; se usa la primera (fila {}, col {}).".format(
            extra_target, target[0], target[1]))

    allowed = set("01AB")
    unknown = [(r, c, grid[r][c]) for r in range(rows) for c in range(cols) if grid[r][c] not in allowed]
    if unknown:
        r, c, ch = unknown[0]
        warnings.append(
            "Hay {} caracteres desconocidos (el primero: '{}' en fila {}, col {}); a.exe los trata como muro.".format(
                len(unknown), ch, r, c
            )
        )

    path_cells = sum(1 for row in grid for ch in row if ch == "1")
    return Maze(rows, cols, grid, root, target, path_cells, warnings)


def solution_base(path):
    # maze.cpp usa file_path.substr(0, size-3); se replica para leer los .sol.
    if len(path) > 3:
        return path[:-3]
    return path


def read_solution(path, rows, cols):
    """Lee un .sol de a.exe ('fila columna' por línea)."""
    coords = []
    bad = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if not parts:
                    continue
                if len(parts) < 2:
                    bad += 1
                    continue
                try:
                    r, c = int(parts[0]), int(parts[1])
                except ValueError:
                    bad += 1
                    continue
                if 0 <= r < rows and 0 <= c < cols:
                    coords.append((r, c))
                else:
                    bad += 1
    except OSError:
        return [], 0
    return coords, bad


def format_exit_code(code):
    if code is None:
        return "desconocido"
    unsigned = code & 0xFFFFFFFF
    return "{} (0x{:08X})".format(code, unsigned)


def _rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _build_gradient(stops, count=128):
    """Genera una rampa interpolando una lista de paradas (t, rgb)."""
    result = []
    for i in range(count):
        t = i / (count - 1) if count > 1 else 0.0
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t <= t1:
                span = t1 - t0
                local = 0.0 if span <= 0 else (t - t0) / span
                result.append(_hex(_mix(c0, c1, max(0.0, min(1.0, local)))))
                break
    return result


def _round_rect(canvas, x0, y0, x1, y1, radius, **kwargs):
    radius = max(0.0, min(radius, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    points = (
        x0 + radius, y0,
        x1 - radius, y0,
        x1, y0,
        x1, y0 + radius,
        x1, y1 - radius,
        x1, y1,
        x1 - radius, y1,
        x0 + radius, y1,
        x0, y1,
        x0, y1 - radius,
        x0, y0 + radius,
        x0, y0,
    )
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class RoundedButton(tk.Canvas):
    """Botón de texto con esquinas redondeadas y estados neón."""

    def __init__(self, master, text, command, font, bg, fg, hover, border,
                 parent_bg, radius=12, padx=15, pady=6):
        self._font = font
        self._measure = tkfont.Font(font=font)
        self._text = text
        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover
        self._border = border
        self._parent_bg = parent_bg
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._enabled = True
        self._hover = False
        self._req_w = self._measure.measure(text) + 2 * padx
        self._req_h = self._measure.metrics("linespace") + 2 * pady
        super().__init__(
            master,
            width=self._req_w,
            height=self._req_h,
            bg=parent_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def _click(self, _event=None):
        if self._enabled and self._command:
            self._command()

    def _enter(self, _event=None):
        if self._enabled:
            self._hover = True
            self._draw()

    def _leave(self, _event=None):
        self._hover = False
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), self._req_w)
        h = max(self.winfo_height(), self._req_h)
        if not self._enabled:
            fill, fg, border = "#0a1220", "#52627a", "#1a2a44"
        elif self._hover:
            fill, fg, border = self._hover_bg, self._fg, self._border
        else:
            fill, fg, border = self._bg, self._fg, self._border
        _round_rect(
            self, 1, 1, w - 1, h - 1, self._radius,
            fill=fill, outline=border, width=1,
        )
        self.create_text(
            w / 2.0, h / 2.0, text=self._text, fill=fg, font=self._font
        )

    def set_text(self, text):
        self._text = text
        self._req_w = self._measure.measure(text) + 2 * self._padx
        self.configure(width=self._req_w)
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()


class RoundedCard(tk.Frame):
    """Tarjeta con fondo redondeado; expone `inner` para colocar contenido."""

    def __init__(self, master, bg, border, parent_bg, radius=18, pad=14):
        super().__init__(master, bg=parent_bg)
        self._bg = bg
        self._border = border
        self._parent_bg = parent_bg
        self._radius = radius
        self.pad = pad
        self._height = 90
        self.canvas = tk.Canvas(
            self, bg=parent_bg, highlightthickness=0, bd=0, height=self._height
        )
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(
            pad, pad, anchor="nw", window=self.inner
        )
        self.canvas.bind("<Configure>", self._on_canvas)
        self.inner.bind("<Configure>", self._on_inner)

    def _on_inner(self, _event=None):
        height = max(50, self.inner.winfo_reqheight() + 2 * self.pad)
        if height != self._height:
            self._height = height
            self.canvas.configure(height=height)

    def _on_canvas(self, event=None):
        width = max(20, event.width if event is not None else self.canvas.winfo_width())
        height = max(20, event.height if event is not None else self.canvas.winfo_height())
        self.canvas.itemconfigure(self._window, width=max(10, width - 2 * self.pad))
        self._draw(width, height)

    def _draw(self, width, height):
        self.canvas.delete("card_bg")
        self.canvas.create_polygon(
            *_rounded_rect_points(1, 2, width - 1, height + 1, self._radius),
            smooth=True, splinesteps=24, fill="#03060d", outline="", tags="card_bg",
        )
        self.canvas.create_polygon(
            *_rounded_rect_points(1, 1, width - 1, height - 1, self._radius),
            smooth=True, splinesteps=24, fill=self._bg, outline=self._border,
            width=1, tags="card_bg",
        )
        self.canvas.tag_lower("card_bg")


def _rounded_rect_points(x0, y0, x1, y1, radius):
    radius = max(0.0, min(radius, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    return (
        x0 + radius, y0,
        x1 - radius, y0,
        x1, y0,
        x1, y0 + radius,
        x1, y1 - radius,
        x1, y1,
        x1 - radius, y1,
        x0 + radius, y1,
        x0, y1,
        x0, y1 - radius,
        x0, y0 + radius,
        x0, y0,
    )


class KindRow(tk.Canvas):
    """Fila seleccionable y redondeada para elegir un recorrido."""

    def __init__(self, master, kind, text, palette, on_click):
        self.kind = kind
        self.text = text
        self.palette = palette
        self.on_click = on_click
        self.enabled = False
        self.selected = False
        self._hover = False
        super().__init__(
            master,
            height=34,
            bg=palette["parent_bg"],
            highlightthickness=0,
            bd=0,
            cursor="arrow",
        )
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Configure>", lambda _e: self._paint())

    def _click(self, _event=None):
        if self.enabled and self.on_click:
            self.on_click(self.kind)

    def _enter(self, _event=None):
        if self.enabled:
            self._hover = True
            self._paint()

    def _leave(self, _event=None):
        self._hover = False
        self._paint()

    def _paint(self):
        p = self.palette
        self.delete("all")
        width = max(150, self.winfo_width())
        if self.selected:
            bg, border, fg, dot = p["card_sel"], p["accent"], p["text"], p["accent"]
        elif not self.enabled:
            bg, border, fg, dot = p["card_alt"], p["border"], p["disabled"], p["disabled"]
        elif self._hover:
            bg, border, fg, dot = p["card_hover"], p["accent_dark"], p["text"], p["accent"]
        else:
            bg, border, fg, dot = p["card_alt"], p["border"], p["text"], p["muted"]
        _round_rect(self, 1, 1, width - 1, 33, 12, fill=bg, outline=border, width=1)
        self.create_oval(12, 14, 20, 22, fill=dot, outline="")
        self.create_text(
            30, 17, text=self.text, anchor="w", fill=fg, font=p["font_row"]
        )
        if self.selected:
            self.create_text(
                width - 14, 17, text="●", anchor="e", fill=p["accent"],
                font=p["font_row"],
            )

    def render(self, selected, enabled):
        self.selected = selected
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        if not enabled:
            self._hover = False
        self._paint()


class MazeGui:
    BG = "#05070d"
    PANEL = "#0a1020"
    CARD = "#0c1426"
    CARD_ALT = "#101b33"
    CARD_HOVER = "#15233f"
    CARD_SEL = "#102a3b"
    BORDER = "#22395c"
    TEXT = "#eef6ff"
    MUTED = "#8ca3c4"
    DISABLED = "#465a75"
    ACCENT = "#35e7ff"
    ACCENT_LIGHT = "#8ff4ff"
    ACCENT_DARK = "#0c6b83"
    PINK = "#ff4fd8"
    LIME = "#a6ff5c"
    VIOLET = "#8b5cff"
    WARN = "#ffc857"
    DANGER = "#ff6b8a"
    CANVAS_BG = "#03060d"
    WALL = "#1a2a42"
    WALL_EDGE = "#3a5a80"
    FLOOR = "#06101d"
    FLOOR_EDGE = "#12233a"
    ROOT_COLOR = "#a6ff5c"
    TARGET_COLOR = "#ff4fd8"
    UNKNOWN = "#39294a"
    GRID = "#14253d"
    CURRENT = "#ffffff"
    TRAIL = "#35e7ff"
    GRADIENT_STOPS = (
        (0.00, (0, 229, 255)),
        (0.42, (90, 120, 255)),
        (0.72, (180, 80, 255)),
        (1.00, (255, 79, 216)),
    )

    def __init__(self, root, initial_file=None):
        self.root = root
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.exe_path = os.path.join(self.base_dir, "a.exe")

        self.maze = None
        self.path = None
        self.sequences = {}
        self.sequence = []
        self.current_kind = None
        self.index = 0
        self.playing = False
        self.after_id = None
        self.running = False
        self.auto_fit = True
        self.zoom = 1.0
        self.base_cell = 4.0
        self.cell_px = 0.0
        self.ox = 0.0
        self.oy = 0.0
        self.cell_items = []
        self.visited_items = []
        self.visited_tiny = set()
        self.marker_items = []
        self.trail_items = []
        self.solver_issues = []
        self.log = ""
        self.temp_dirs = []
        self._resize_job = None
        self._updating_list = False
        self._step_accum = 0

        self.gradient = _build_gradient(self.GRADIENT_STOPS)

        self._setup_fonts()
        self._setup_root()
        self._setup_styles()
        self._build_ui()
        self._bind_events()
        self._set_controls_enabled(False)
        self._update_kind_rows()
        self._populate_order_list()
        self._update_progress()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if initial_file:
            self.root.after(150, lambda: self.load_path(initial_file))
        else:
            self.root.after(150, self.redraw)

    def _setup_fonts(self):
        family = "Segoe UI" if os.name == "nt" else "Helvetica"
        self.font_title = (family, 21, "bold")
        self.font_subtitle = (family, 9)
        self.font_body = (family, 10)
        self.font_small = (family, 9)
        self.font_card = (family, 8, "bold")
        self.font_row = (family, 10)
        self.font_button = (family, 10, "bold")
        self.font_button_small = (family, 9, "bold")
        self.font_value = (family, 10, "bold")
        self.font_placeholder = (family, 14, "bold")
        self.font_mono = ("Consolas", 9) if os.name == "nt" else ("DejaVu Sans Mono", 9)

        self.palette = {
            "parent_bg": self.CARD,
            "card": self.CARD,
            "card_alt": self.CARD_ALT,
            "card_hover": self.CARD_HOVER,
            "card_sel": self.CARD_SEL,
            "border": self.BORDER,
            "text": self.TEXT,
            "muted": self.MUTED,
            "disabled": self.DISABLED,
            "accent": self.ACCENT,
            "accent_dark": self.ACCENT_DARK,
            "pink": self.PINK,
            "lime": self.LIME,
            "font_row": self.font_row,
        }

    def _setup_root(self):
        self.root.title(APP_NAME)
        self.root.configure(bg=self.BG)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1400, int(screen_w * 0.92))
        height = min(1000, int(screen_h * 0.92))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry("{}x{}+{}+{}".format(width, height, x, y))
        self.root.minsize(1080, 680)

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            ".",
            background=self.BG,
            foreground=self.TEXT,
            fieldbackground=self.CARD_ALT,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
            troughcolor=self.CANVAS_BG,
            focuscolor=self.ACCENT,
            font=self.font_body,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=self.CARD_ALT,
            background=self.CARD_ALT,
            foreground=self.TEXT,
            arrowcolor=self.ACCENT,
            bordercolor=self.BORDER,
            padding=4,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", self.CARD_ALT)],
            foreground=[("readonly", self.TEXT)],
            selectbackground=[("readonly", self.CARD_ALT)],
            selectforeground=[("readonly", self.TEXT)],
            bordercolor=[("focus", self.ACCENT)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=self.CARD_ALT,
            troughcolor=self.BG,
            bordercolor=self.BORDER,
            arrowcolor=self.MUTED,
            lightcolor=self.CARD_ALT,
            darkcolor=self.CARD_ALT,
        )
        style.configure(
            "Horizontal.TScrollbar",
            background=self.CARD_ALT,
            troughcolor=self.BG,
            bordercolor=self.BORDER,
            arrowcolor=self.MUTED,
            lightcolor=self.CARD_ALT,
            darkcolor=self.CARD_ALT,
        )
        style.configure(
            "Accent.Horizontal.TProgressbar",
            background=self.ACCENT,
            troughcolor=self.CANVAS_BG,
            bordercolor=self.BORDER,
            lightcolor=self.ACCENT,
            darkcolor=self.ACCENT_DARK,
        )
        style.configure(
            "Accent.Horizontal.TScale",
            background=self.CARD,
            troughcolor=self.CANVAS_BG,
            bordercolor=self.BORDER,
            lightcolor=self.ACCENT,
            darkcolor=self.ACCENT,
        )
        self.root.option_add("*TCombobox*Listbox.background", self.CARD_ALT)
        self.root.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT_DARK)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # ------------------------------------------------------------------ UI --
    @staticmethod
    def _parent_bg(parent):
        try:
            return parent.cget("bg")
        except tk.TclError:
            return MazeGui.BG

    def _flat_button(self, parent, text, command, kind="secondary", small=False, **kwargs):
        kwargs.pop("width", None)
        if kind == "primary":
            bg, fg, hover, border = self.ACCENT, self.BG, self.ACCENT_LIGHT, self.ACCENT
        elif kind == "pink":
            bg, fg, hover, border = self.PINK, self.BG, "#ff83e6", self.PINK
        else:
            bg, fg, hover, border = self.CARD_ALT, self.TEXT, self.CARD_HOVER, self.BORDER
        return RoundedButton(
            parent,
            text,
            command,
            self.font_button_small if small else self.font_button,
            bg,
            fg,
            hover,
            border,
            self._parent_bg(parent),
            radius=10 if small else 13,
            padx=9 if small else 15,
            pady=4 if small else 6,
        )

    def _make_card(self, parent, title):
        card = RoundedCard(
            parent, self.CARD, self.BORDER, self._parent_bg(parent), radius=18, pad=14
        )
        head = tk.Frame(card.inner, bg=self.CARD)
        head.pack(fill="x")
        tk.Label(
            head,
            text=title.upper(),
            bg=self.CARD,
            fg=self.ACCENT,
            font=self.font_card,
            anchor="w",
        ).pack(side="left")
        body = tk.Frame(card.inner, bg=self.CARD)
        body.pack(fill="both", expand=True, pady=(7, 0))
        return card, body, head

    def _legend_chip(self, parent, color, text):
        bg = self._parent_bg(parent)
        frame = tk.Frame(parent, bg=bg)
        canvas = tk.Canvas(frame, width=14, height=14, bg=bg, highlightthickness=0, bd=0)
        canvas.create_oval(1, 1, 13, 13, fill=color, outline="", stipple="gray50")
        canvas.create_oval(4, 4, 10, 10, fill=color, outline="")
        canvas.pack(side="left", padx=(0, 5))
        tk.Label(frame, text=text, bg=bg, fg=self.MUTED, font=self.font_small).pack(side="left")
        frame.pack(side="left", padx=(0, 14))
        return frame

    def _group_label(self, parent, text):
        label = tk.Label(
            parent,
            text=text,
            bg=self.CARD,
            fg=self.MUTED,
            font=self.font_card,
            anchor="w",
        )
        label.pack(fill="x", pady=(2, 3))
        return label

    def _build_ui(self):
        self.file_var = tk.StringVar(value="Ningún laberinto seleccionado")
        self.status_var = tk.StringVar(value="Abre un laberinto .in para comenzar.")
        self.zoom_var = tk.StringVar(value="100 %")
        self.desc_var = tk.StringVar(value="Elige un recorrido disponible.")
        self.warn_var = tk.StringVar(value="Sin advertencias.")
        self.step_var = tk.StringVar(value="—")
        self.speed_label_var = tk.StringVar(value="10 pasos/s")
        self.speed_var = tk.DoubleVar(value=3.0)
        self.quick_var = tk.StringVar(value="")

        header_card = RoundedCard(
            self.root, self.PANEL, self.BORDER, self.BG, radius=22, pad=16
        )
        header_card.pack(fill="x", padx=16, pady=(14, 0))
        header_inner = header_card.inner
        header_inner.grid_columnconfigure(0, weight=1)

        titles = tk.Frame(header_inner, bg=self.PANEL)
        titles.grid(row=0, column=0, sticky="w")
        brand = tk.Frame(titles, bg=self.PANEL)
        brand.pack(anchor="w")
        tk.Label(
            brand, text="NEON", bg=self.PANEL, fg=self.TEXT,
            font=self.font_title, anchor="w",
        ).pack(side="left")
        tk.Label(
            brand, text=" MAZE", bg=self.PANEL, fg=self.ACCENT,
            font=self.font_title, anchor="w",
        ).pack(side="left")
        tk.Label(
            titles, text=APP_SUBTITLE, bg=self.PANEL, fg=self.MUTED,
            font=self.font_subtitle, anchor="w",
        ).pack(anchor="w")
        tk.Label(
            titles,
            textvariable=self.file_var,
            bg=self.PANEL,
            fg=self.ACCENT,
            font=self.font_small,
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        controls = tk.Frame(header_inner, bg=self.PANEL)
        controls.grid(row=0, column=1, sticky="e")
        tk.Label(
            controls, text="Laberintos:", bg=self.PANEL, fg=self.MUTED,
            font=self.font_small,
        ).pack(side="left", padx=(0, 6))
        self.quick_combo = ttk.Combobox(
            controls,
            textvariable=self.quick_var,
            values=self._bundled_files(),
            state="readonly",
            width=20,
            style="Dark.TCombobox",
        )
        self.quick_combo.pack(side="left", padx=(0, 10))
        self._flat_button(controls, "Abrir .in", self.open_dialog, kind="primary").pack(side="left")
        self.regenerate_btn = self._flat_button(controls, "Regenerar", self.run_solver)
        self.regenerate_btn.pack(side="left", padx=(8, 0))

        status = tk.Label(
            self.root,
            textvariable=self.status_var,
            bg=self.BG,
            fg=self.MUTED,
            font=self.font_small,
            anchor="w",
            padx=22,
            pady=7,
        )
        status.pack(fill="x", side="bottom")
        self.status_lbl = status

        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 8))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, minsize=390, weight=0)

        left = tk.Frame(body, bg=self.BG)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        canvas_wrap = tk.Frame(
            left, bg=self.CARD, bd=0, highlightthickness=1, highlightbackground=self.BORDER
        )
        canvas_wrap.grid(row=0, column=0, sticky="nsew")
        canvas_wrap.grid_rowconfigure(0, weight=1)
        canvas_wrap.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(canvas_wrap, bg=self.CANVAS_BG, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(canvas_wrap, orient="vertical", command=self.canvas.yview)
        hbar = ttk.Scrollbar(canvas_wrap, orient="horizontal", command=self.canvas.xview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        toolbar = tk.Frame(left, bg=self.BG)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._legend_chip(toolbar, self.WALL, "Muro")
        self._legend_chip(toolbar, self.FLOOR, "Camino")
        self._legend_chip(toolbar, self.ROOT_COLOR, "Origen A")
        self._legend_chip(toolbar, self.TARGET_COLOR, "Destino B")
        self._legend_chip(toolbar, self.gradient[90], "Visitado")
        self._legend_chip(toolbar, self.CURRENT, "Cursor")
        self._legend_chip(toolbar, self.TRAIL, "Estela")

        zoom_box = tk.Frame(toolbar, bg=self.BG)
        zoom_box.pack(side="right")
        tk.Label(
            zoom_box, textvariable=self.zoom_var, bg=self.BG, fg=self.MUTED,
            font=self.font_small, width=6,
        ).pack(side="left", padx=(0, 6))
        self._flat_button(zoom_box, "−", lambda: self._zoom_by(1 / 1.25), small=True).pack(side="left")
        self._flat_button(zoom_box, "+", lambda: self._zoom_by(1.25), small=True).pack(
            side="left", padx=4
        )
        self._flat_button(zoom_box, "Ajustar", self.fit_view, small=True).pack(side="left")

        self._build_right_panel(body)

    def _build_right_panel(self, body):
        wrap = tk.Frame(body, bg=self.BG)
        wrap.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.right_canvas = tk.Canvas(
            wrap, bg=self.BG, highlightthickness=0, bd=0, width=390
        )
        self.right_canvas.grid(row=0, column=0, sticky="nsew")
        right_bar = ttk.Scrollbar(wrap, orient="vertical", command=self.right_canvas.yview)
        right_bar.grid(row=0, column=1, sticky="ns")
        self.right_canvas.configure(yscrollcommand=right_bar.set)

        right = tk.Frame(self.right_canvas, bg=self.BG)
        self._right_window = self.right_canvas.create_window(
            (0, 0), window=right, anchor="nw"
        )
        right.bind("<Configure>", self._on_right_configure)
        self.right_canvas.bind("<Configure>", self._on_right_canvas_configure)

        # Tarjeta de información
        info_card, info_body, info_head = self._make_card(right, "Laberinto")
        info_card.pack(fill="x", pady=(0, 10))
        self.details_btn = self._flat_button(
            info_head, "Detalles", self.show_details, small=True
        )
        self.details_btn.pack(side="right")

        self.info_vars = {
            "dims": tk.StringVar(value="—"),
            "root": tk.StringVar(value="—"),
            "target": tk.StringVar(value="—"),
            "paths": tk.StringVar(value="—"),
        }
        rows = [
            ("Tamaño", "dims"),
            ("Origen A", "root"),
            ("Destino B", "target"),
            ("Celdas libres", "paths"),
        ]
        for idx, (text, key) in enumerate(rows):
            tk.Label(
                info_body, text=text, bg=self.CARD, fg=self.MUTED, font=self.font_small, anchor="w"
            ).grid(row=idx, column=0, sticky="w", pady=1)
            tk.Label(
                info_body,
                textvariable=self.info_vars[key],
                bg=self.CARD,
                fg=self.TEXT,
                font=self.font_value,
                anchor="e",
            ).grid(row=idx, column=1, sticky="e", pady=1)
        info_body.grid_columnconfigure(1, weight=1)
        self.warn_lbl = tk.Label(
            info_body,
            textvariable=self.warn_var,
            bg=self.CARD,
            fg=self.MUTED,
            font=self.font_small,
            anchor="w",
            justify="left",
            wraplength=320,
        )
        self.warn_lbl.grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(7, 0))

        # Tarjeta de recorridos
        trav_card, trav_body, _ = self._make_card(right, "Recorridos")
        trav_card.pack(fill="x", pady=(0, 10))

        self._group_label(trav_body, "RECORRIDOS DEL ÁRBOL")
        self.kind_rows = {}
        for key, label, group, _desc in KINDS:
            if group != "tree":
                continue
            row = KindRow(trav_body, key, label, self.palette, self._on_kind_click)
            row.pack(fill="x", pady=(0, 2))
            self.kind_rows[key] = row

        separator = tk.Frame(trav_body, bg=self.BORDER, height=1)
        separator.pack(fill="x", pady=(6, 0))
        self._group_label(trav_body, "SOLUCIONES · ORDEN DEL .SOL (B → A)")
        for key, label, group, _desc in KINDS:
            if group != "path":
                continue
            row = KindRow(trav_body, key, label, self.palette, self._on_kind_click)
            row.pack(fill="x", pady=(0, 2))
            self.kind_rows[key] = row

        tk.Label(
            trav_body,
            textvariable=self.desc_var,
            bg=self.CARD,
            fg=self.MUTED,
            font=self.font_small,
            anchor="w",
            justify="left",
            wraplength=330,
        ).pack(fill="x", pady=(5, 6))

        controls = tk.Frame(trav_body, bg=self.CARD)
        controls.pack(fill="x")
        for col in range(3):
            controls.grid_columnconfigure(col, weight=1)

        self.btn_prev = self._flat_button(controls, "◀", lambda: self._step(-1), small=True)
        self.btn_play = self._flat_button(controls, "Reproducir", self._toggle_play, kind="primary")
        self.btn_next = self._flat_button(controls, "▶", lambda: self._step(1), small=True)
        self.btn_prev.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.btn_play.grid(row=0, column=1, sticky="ew", padx=3)
        self.btn_next.grid(row=0, column=2, sticky="ew", padx=(3, 0))

        self.btn_reset = self._flat_button(controls, "Reiniciar", self.reset_animation, small=True)
        self.btn_full = self._flat_button(controls, "Ver todo", self.show_full, small=True)
        self.btn_reset.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 3), pady=(5, 0))
        self.btn_full.grid(row=1, column=2, sticky="ew", padx=(3, 0), pady=(5, 0))

        speed_box = tk.Frame(trav_body, bg=self.CARD)
        speed_box.pack(fill="x", pady=(7, 0))
        tk.Label(
            speed_box, text="Velocidad", bg=self.CARD, fg=self.MUTED, font=self.font_small
        ).pack(side="left")
        self.speed_label = tk.Label(
            speed_box,
            textvariable=self.speed_label_var,
            bg=self.CARD,
            fg=self.ACCENT,
            font=self.font_small,
        )
        self.speed_label.pack(side="right")
        self.speed = ttk.Scale(
            trav_body,
            from_=0,
            to=len(SPEED_VALUES) - 1,
            orient="horizontal",
            variable=self.speed_var,
            style="Accent.Horizontal.TScale",
            command=self._on_speed_change,
        )
        self.speed.pack(fill="x")

        self.progress = ttk.Progressbar(
            trav_body,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", pady=(6, 3))
        tk.Label(
            trav_body,
            textvariable=self.step_var,
            bg=self.CARD,
            fg=self.MUTED,
            font=self.font_small,
            anchor="w",
        ).pack(fill="x")

        # Tarjeta de orden de nodos
        order_card, order_body, _ = self._make_card(right, "Registro de nodos")
        order_card.pack(fill="x")
        self.order_card = order_card
        order_body.grid_rowconfigure(1, weight=1)
        order_body.grid_columnconfigure(0, weight=1)
        tk.Label(
            order_body,
            text="Orden exacto del archivo · (fila, col) · clic para saltar",
            bg=self.CARD,
            fg=self.MUTED,
            font=self.font_small,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.order_list = tk.Listbox(
            order_body,
            bg=self.CANVAS_BG,
            fg=self.TEXT,
            selectbackground=self.ACCENT_DARK,
            selectforeground="#ffffff",
            activestyle="none",
            exportselection=False,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.ACCENT,
            font=self.font_mono,
            height=8,
        )
        self.order_list.grid(row=1, column=0, sticky="nsew")
        order_bar = ttk.Scrollbar(order_body, orient="vertical", command=self.order_list.yview)
        order_bar.grid(row=1, column=1, sticky="ns")
        self.order_list.configure(yscrollcommand=order_bar.set)

        self._bind_wheel_tree(right, exclude=self.order_list)

    def _on_right_configure(self, _event=None):
        self.right_canvas.configure(scrollregion=self.right_canvas.bbox("all"))

    def _on_right_canvas_configure(self, event):
        self.right_canvas.itemconfigure(self._right_window, width=event.width)

    def _bind_wheel_tree(self, widget, exclude=None):
        if widget is not exclude:
            widget.bind("<MouseWheel>", self._on_right_wheel, add="+")
        for child in widget.winfo_children():
            self._bind_wheel_tree(child, exclude)

    def _on_right_wheel(self, event):
        first, last = self.right_canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return "break"
        self.right_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _bind_events(self):
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.configure(cursor="fleur")
        self.quick_combo.bind("<<ComboboxSelected>>", self._on_quick_select)
        self.order_list.bind("<<ListboxSelect>>", self._on_order_select)

    def _bundled_files(self):
        files = sorted(glob.glob(os.path.join(self.base_dir, "*.in")))
        return [os.path.basename(f) for f in files]

    # -------------------------------------------------------------- archivos --
    def _on_quick_select(self, _event=None):
        name = self.quick_var.get().strip()
        if not name:
            return
        path = os.path.join(self.base_dir, name)
        if os.path.isfile(path):
            self.load_path(path)

    def open_dialog(self):
        self.quick_combo.configure(values=self._bundled_files())
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Selecciona un laberinto (.in)",
            initialdir=self.base_dir,
            filetypes=[("Laberintos", "*.in"), ("Todos los archivos", "*.*")],
        )
        if path:
            self.load_path(path)

    def load_path(self, path):
        path = os.path.abspath(path)
        try:
            maze = parse_maze(path)
        except MazeError as exc:
            messagebox.showerror(APP_NAME, "No se pudo leer el laberinto:\n\n{}".format(exc), parent=self.root)
            self.set_status("Archivo inválido: {}".format(exc), "error")
            return
        except OSError as exc:
            messagebox.showerror(APP_NAME, "No se pudo abrir el archivo:\n\n{}".format(exc), parent=self.root)
            self.set_status("No se pudo abrir el archivo.", "error")
            return

        self._stop_playback()
        self._clear_overlay()
        self.maze = maze
        self.path = path
        self.sequences = {}
        self.sequence = []
        self.current_kind = None
        self.index = 0
        self.solver_issues = []
        self.log = ""
        self.auto_fit = True
        self.zoom = 1.0
        self._update_zoom_label()

        self.file_var.set(self._short_path(path))
        self._update_quick_combo()
        self._update_info()
        self._update_kind_rows()
        self._populate_order_list()
        self._set_controls_enabled(False)
        self._update_progress()
        self.redraw()
        self.set_status("Sector cargado. Ejecutando a.exe…", "info")
        self.root.after(60, self.run_solver)

    def _update_quick_combo(self):
        if not self.path:
            return
        folder = os.path.dirname(self.path)
        if os.path.normcase(folder) == os.path.normcase(self.base_dir):
            self.quick_var.set(os.path.basename(self.path))
        else:
            self.quick_var.set("")

    def _short_path(self, path):
        if len(path) <= 96:
            return path
        return "{}…{}".format(path[:46], path[-46:])

    # ---------------------------------------------------------------- a.exe --
    def run_solver(self):
        if not self.maze:
            self.set_status("Abre un archivo .in primero.", "warn")
            return
        if self.running:
            return
        if not os.path.isfile(self.exe_path):
            self.solver_issues = ["No se encontró a.exe junto a maze_gui.py."]
            self.sequences = {}
            self.sequence = []
            self.current_kind = None
            self.index = 0
            self._update_info()
            self._update_kind_rows()
            self._populate_order_list()
            self._set_controls_enabled(False)
            self.set_status("No se encontró a.exe en la carpeta del proyecto.", "error")
            messagebox.showerror(APP_NAME, self.solver_issues[0], parent=self.root)
            return

        self.running = True
        self.regenerate_btn.configure(state="disabled")
        self._stop_playback()
        self._clear_overlay()
        self.sequences = {}
        self.sequence = []
        self.current_kind = None
        self.index = 0
        self.solver_issues = []
        self.log = ""
        self._update_kind_rows()
        self._populate_order_list()
        self._set_controls_enabled(False)
        self._update_progress()

        base = solution_base(self.path)
        run_started = time.time()
        undeleted = []
        for key, _label, _group, _desc in KINDS:
            sol_path = "{}_{}.sol".format(base, key)
            try:
                os.remove(sol_path)
            except FileNotFoundError:
                pass
            except OSError:
                undeleted.append(os.path.basename(sol_path))
        if undeleted:
            self.solver_issues.append(
                "No se pudieron borrar los .sol anteriores: {}.".format(", ".join(undeleted))
            )

        self.set_status("Ejecutando a.exe sobre {}…".format(os.path.basename(self.path)), "info")
        self.root.update_idletasks()

        sequences = {}
        return_code = None
        log = ""
        temp_used = False

        try:
            proc = self._run_solver_process(self.path)
            return_code = proc.returncode
            log = proc.stdout or ""
        except subprocess.TimeoutExpired:
            self.solver_issues.append(
                "a.exe no terminó en {} segundos.".format(SOLVER_TIMEOUT)
            )
        except OSError as exc:
            self.solver_issues.append("No se pudo ejecutar a.exe: {}".format(exc))
        else:
            sequences, bad_files = self._load_sequences(base, min_mtime=run_started)
            if bad_files:
                self.solver_issues.append(
                    "Se ignoraron líneas inválidas en: {}.".format(", ".join(bad_files))
                )

            # Si a.exe dice haber creado archivos pero no aparecen junto al .in,
            # se reintenta sobre una copia temporal (carpeta sin permisos).
            if not sequences and "created" in log:
                try:
                    temp_root = tempfile.mkdtemp(prefix="mazeview_")
                    temp_input = os.path.join(temp_root, os.path.basename(self.path))
                    shutil.copy2(self.path, temp_input)
                    proc2 = self._run_solver_process(temp_input)
                    log += "\n[reintento en carpeta temporal]\n" + (proc2.stdout or "")
                    return_code = proc2.returncode
                    temp_sequences, bad_files2 = self._load_sequences(
                        solution_base(temp_input), min_mtime=run_started
                    )
                    if temp_sequences:
                        sequences = temp_sequences
                        temp_used = True
                        self.temp_dirs.append(temp_root)
                        self.solver_issues.append(
                            "No se pudo escribir junto al .in; se usó una copia temporal."
                        )
                    else:
                        shutil.rmtree(temp_root, ignore_errors=True)
                    if bad_files2:
                        self.solver_issues.append(
                            "Se ignoraron líneas inválidas en la copia temporal."
                        )
                except OSError as exc:
                    self.solver_issues.append(
                        "No se pudo usar una carpeta temporal: {}".format(exc)
                    )

        self.log = log
        self.sequences = sequences

        if return_code is not None:
            if return_code != 0:
                self.solver_issues.append(
                    "a.exe terminó con código {}.".format(format_exit_code(return_code))
                )
            if "Target or root not found" in log:
                self.solver_issues.append(
                    "a.exe no encontró 'A' o 'B' en su lectura del archivo."
                )
            if "There's no dfs solution" in log:
                self.solver_issues.append("No hay solución DFS: B no es alcanzable desde A.")
            if "There's no bfs solution" in log:
                self.solver_issues.append("No hay solución BFS: B no es alcanzable desde A.")

        missing = [KIND_LABEL[key] for key, _l, _g, _d in KINDS if key not in sequences]
        if missing:
            self.solver_issues.append("No se generó: {}.".format(", ".join(missing)))
        if temp_used:
            self.solver_issues.append("Los .sol leídos están en una carpeta temporal.")

        self.running = False
        self.regenerate_btn.configure(state="normal")
        self._update_info()
        self._update_kind_rows()

        if sequences:
            first = next(key for key, _l, _g, _d in KINDS if key in sequences)
            self._select_kind(first, autoplay=False, full=True)
            if self.solver_issues:
                self.set_status(
                    "Listo con avisos: {} recorrido(s) disponible(s). Revisa «Detalles».".format(
                        len(sequences)
                    ),
                    "warn",
                )
            else:
                self.set_status(
                    "Listo: {} recorrido(s) generado(s). Elige uno para animarlo.".format(
                        len(sequences)
                    ),
                    "ok",
                )
            if return_code not in (0, None) or "Target or root not found" in log:
                messagebox.showwarning(
                    APP_NAME,
                    "{}\n\nSe muestran los recorridos que a.exe alcanzó a generar. "
                    "Usa «Detalles» para ver la salida completa.".format(
                        self._first_issue()
                    ),
                    parent=self.root,
                )
        else:
            self._populate_order_list()
            self._set_controls_enabled(False)
            self._update_progress()
            if return_code not in (0, None):
                self.set_status(
                    "a.exe falló ({}). Revisa «Detalles».".format(format_exit_code(return_code)),
                    "error",
                )
            else:
                self.set_status("a.exe no generó recorridos. Revisa «Detalles».", "warn")
            messagebox.showwarning(
                APP_NAME,
                "{}\n\nUsa «Detalles» para ver la salida de a.exe.".format(self._first_issue()),
                parent=self.root,
            )

    def _first_issue(self):
        if self.solver_issues:
            return self.solver_issues[0]
        return "a.exe no generó ningún recorrido."

    def _run_solver_process(self, input_path):
        kwargs = {
            "cwd": self.base_dir,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": SOLVER_TIMEOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        return subprocess.run([self.exe_path, input_path], **kwargs)

    def _load_sequences(self, base, min_mtime=None):
        sequences = {}
        bad_files = []
        for key, _label, _group, _desc in KINDS:
            sol_path = "{}_{}.sol".format(base, key)
            if not os.path.isfile(sol_path):
                continue
            if min_mtime is not None:
                try:
                    if os.path.getmtime(sol_path) < min_mtime - 1.0:
                        continue
                except OSError:
                    continue
            coords, bad = read_solution(sol_path, self.maze.rows, self.maze.cols)
            if bad:
                bad_files.append("{} ({} línea/s)".format(os.path.basename(sol_path), bad))
            if coords:
                sequences[key] = coords
        return sequences, bad_files

    def _update_info(self):
        if not self.maze:
            self.info_vars["dims"].set("—")
            self.info_vars["root"].set("—")
            self.info_vars["target"].set("—")
            self.info_vars["paths"].set("—")
            self.warn_var.set("Sin advertencias.")
            self.warn_lbl.configure(fg=self.MUTED)
            return

        maze = self.maze
        self.info_vars["dims"].set("{} × {} celdas".format(maze.rows, maze.cols))
        if maze.root:
            self.info_vars["root"].set("fila {}, col {}".format(*maze.root))
        else:
            self.info_vars["root"].set("no encontrada")
        if maze.target:
            self.info_vars["target"].set("fila {}, col {}".format(*maze.target))
        else:
            self.info_vars["target"].set("no encontrada")
        self.info_vars["paths"].set("{}".format(maze.path_cells))

        if maze.warnings:
            first = maze.warnings[0]
            if len(first) > 135:
                first = first[:132] + "…"
            extra = len(maze.warnings) - 1
            if extra > 0:
                first += "  (+{} más)".format(extra)
            self.warn_var.set(first)
            self.warn_lbl.configure(fg=self.WARN)
        elif self.solver_issues:
            self.warn_var.set(
                "{} aviso(s) de a.exe. Usa «Detalles».".format(len(self.solver_issues))
            )
            self.warn_lbl.configure(fg=self.WARN)
        else:
            self.warn_var.set("Sin advertencias.")
            self.warn_lbl.configure(fg=self.MUTED)

    def _update_kind_rows(self):
        for key, row in self.kind_rows.items():
            row.render(selected=(key == self.current_kind), enabled=(key in self.sequences))

    def _populate_order_list(self):
        self._updating_list = True
        self.order_list.delete(0, "end")
        if not self.sequence:
            self.order_list.insert("end", "  (sin recorrido)")
            self.order_list.configure(state="disabled")
            self._updating_list = False
            return
        items = [
            "{:>5}   ({:>3}, {:>3})".format(idx + 1, r, c)
            for idx, (r, c) in enumerate(self.sequence)
        ]
        self.order_list.configure(state="normal")
        self.order_list.insert("end", *items)
        self.order_list.selection_clear(0, "end")
        self._updating_list = False

    def _on_order_select(self, _event=None):
        if self._updating_list or not self.sequence:
            return
        selection = self.order_list.curselection()
        if not selection:
            return
        target = selection[0] + 1
        self._stop_playback()
        self.set_index(target)
        if self.index > 0:
            r, c = self.sequence[self.index - 1]
            self.set_status(
                "Paso {} de {} · fila {}, col {}.".format(self.index, len(self.sequence), r, c),
                "info",
            )

    def show_details(self):
        parts = []
        if self.path:
            parts.append("Archivo: {}".format(self.path))
        if self.maze:
            parts.append("Cuadrícula: {} × {}".format(self.maze.rows, self.maze.cols))
            if self.maze.root:
                parts.append("A: fila {}, col {}".format(*self.maze.root))
            else:
                parts.append("A: no encontrada")
            if self.maze.target:
                parts.append("B: fila {}, col {}".format(*self.maze.target))
            else:
                parts.append("B: no encontrada")
            parts.append("Celdas libres ('1'): {}".format(self.maze.path_cells))
            if self.maze.warnings:
                parts.append("\nAdvertencias del archivo:")
                parts.extend("- {}".format(w) for w in self.maze.warnings)
            else:
                parts.append("\nAdvertencias del archivo: ninguna.")
        if self.solver_issues:
            parts.append("\nAvisos de a.exe:")
            parts.extend("- {}".format(issue) for issue in self.solver_issues)
        if self.log:
            log = self.log.strip()
            if len(log) > 2600:
                log = log[:2600] + "\n…"
            parts.append("\nSalida de a.exe:\n{}".format(log))
        text = "\n".join(parts) if parts else "Sin detalles."
        messagebox.showinfo("Detalles · {}".format(APP_NAME), text, parent=self.root)

    # ------------------------------------------------------------- recorridos --
    def _on_kind_click(self, kind):
        if kind in self.sequences:
            self._select_kind(kind, autoplay=True, full=False)

    def _select_kind(self, kind, autoplay=False, full=False):
        if kind not in self.sequences:
            return
        self._stop_playback()
        self._clear_overlay()
        self.current_kind = kind
        self.sequence = self.sequences[kind]
        self.index = 0
        self._update_kind_rows()
        self._set_controls_enabled(True)
        self._populate_order_list()
        self.desc_var.set(KIND_DESC[kind])
        self._set_default_speed(len(self.sequence))
        self._update_progress()

        if full:
            self.set_index(len(self.sequence))
            self.set_status(
                "Mostrando {} completo: {} nodo(s).".format(KIND_LABEL[kind], len(self.sequence)),
                "ok",
            )
        elif autoplay:
            self._start_playback()
        else:
            self._update_marker_and_trail()
            self.set_status("Elige un recorrido para animarlo.", "info")

    def _set_default_speed(self, length):
        if length <= 200:
            idx = 3
        elif length <= 1200:
            idx = 6
        else:
            idx = 7
        self.speed_var.set(float(idx))
        self._on_speed_change(idx)

    def _on_speed_change(self, _value=None):
        idx = max(0, min(len(SPEED_VALUES) - 1, int(round(float(self.speed_var.get())))))
        self.speed_label_var.set("{} pasos/s".format(SPEED_VALUES[idx]))

    def _current_speed(self):
        idx = max(0, min(len(SPEED_VALUES) - 1, int(round(float(self.speed_var.get())))))
        return SPEED_VALUES[idx]

    def _toggle_play(self):
        if self.playing:
            self._pause()
        else:
            self._start_playback()

    def _start_playback(self):
        if not self.sequence:
            return
        if self.index >= len(self.sequence):
            self.set_index(0)
        self.playing = True
        self._step_accum = 0
        self.btn_play.set_text("Pausa")
        self.set_status(
            "Reproduciendo {} ({} nodos)…".format(
                KIND_LABEL.get(self.current_kind, "recorrido"), len(self.sequence)
            ),
            "info",
        )
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.after_id = self.root.after(10, self._tick)

    def _tick(self):
        self.after_id = None
        if not self.playing or not self.sequence:
            return
        total = len(self.sequence)
        if self.index >= total:
            self._finish_playback()
            return
        self._step_accum += self._current_speed() * TICK_MS
        steps = self._step_accum // 1000
        if steps > 0:
            self._step_accum %= 1000
            self.set_index(min(total, self.index + steps))
        if self.index >= total:
            self._finish_playback()
        else:
            self.after_id = self.root.after(TICK_MS, self._tick)

    def _finish_playback(self):
        self.playing = False
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.btn_play.set_text("Reproducir")
        self.set_status(
            "Recorrido completo: {} nodo(s).".format(len(self.sequence)), "ok"
        )

    def _pause(self):
        if not self.playing:
            return
        self.playing = False
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.btn_play.set_text("Reproducir")
        self.set_status(
            "En pausa: paso {} de {}.".format(self.index, len(self.sequence)), "info"
        )

    def _stop_playback(self):
        self.playing = False
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if hasattr(self, "btn_play"):
            self.btn_play.set_text("Reproducir")

    def _step(self, delta):
        if not self.sequence:
            return
        self._stop_playback()
        self.set_index(self.index + delta)
        if self.index > 0:
            r, c = self.sequence[self.index - 1]
            self.set_status(
                "Paso {} de {} · fila {}, col {}.".format(
                    self.index, len(self.sequence), r, c
                ),
                "info",
            )
        else:
            self.set_status("Inicio del recorrido.", "info")

    def reset_animation(self):
        if not self.sequence:
            return
        self._stop_playback()
        self.set_index(0)
        self.set_status("Reiniciado. Pulsa Reproducir o avanza paso a paso.", "info")

    def show_full(self):
        if not self.sequence:
            return
        self._stop_playback()
        self.set_index(len(self.sequence))
        self.set_status(
            "Mostrando {} completo: {} nodo(s).".format(
                KIND_LABEL.get(self.current_kind, "recorrido"), len(self.sequence)
            ),
            "ok",
        )

    def set_index(self, target):
        if not self.sequence or not self.cell_items:
            return
        total = len(self.sequence)
        target = max(0, min(total, int(target)))
        if target == self.index:
            self._update_progress()
            return

        if abs(target - self.index) > 500 and total > 1500:
            self.index = target
            self.redraw()
            self._update_progress()
            self._update_order_selection()
            return

        if target < self.index:
            for i in range(target, self.index):
                row, col = self.sequence[i]
                self._unvisit_cell(row, col)
            self.index = target
        else:
            self._paint_range(self.index, target)
            self.index = target

        self._update_marker_and_trail()
        self._update_progress()
        self._update_order_selection()

    def _update_progress(self):
        total = len(self.sequence)
        if total == 0:
            self.progress["value"] = 0
            self.step_var.set("—")
            return
        self.progress["value"] = 100.0 * self.index / total
        if self.index == 0:
            self.step_var.set("0 / {} nodos · pulsa Reproducir".format(total))
        else:
            r, c = self.sequence[self.index - 1]
            self.step_var.set(
                "Paso {} / {} · fila {}, col {}".format(self.index, total, r, c)
            )

    def _update_order_selection(self):
        if not self.sequence:
            return
        self._updating_list = True
        try:
            self.order_list.selection_clear(0, "end")
            if self.index > 0:
                item = self.index - 1
                self.order_list.selection_set(item)
                self.order_list.activate(item)
                self.order_list.see(max(0, item - 3))
        finally:
            self._updating_list = False

    def _set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for button in (self.btn_prev, self.btn_play, self.btn_next, self.btn_reset, self.btn_full):
            button.set_enabled(enabled)
        try:
            self.speed.state(["!disabled"] if enabled else ["disabled"])
        except tk.TclError:
            pass
        self.order_list.configure(state=state)

        if hasattr(self, "order_card"):
            if enabled:
                if not self.order_card.winfo_ismapped():
                    self.order_card.pack(fill="x")
            else:
                self.order_card.pack_forget()

    def set_status(self, text, level="info"):
        colors = {"info": self.MUTED, "ok": self.ACCENT, "warn": self.WARN, "error": self.DANGER}
        self.status_var.set(text)
        self.status_lbl.configure(fg=colors.get(level, self.MUTED))

    def _update_zoom_label(self):
        self.zoom_var.set("{} %".format(int(round(self.zoom * 100))))

    # --------------------------------------------------------------- dibujo --
    def _base_style(self, ch):
        if ch == "A":
            return "#0b2a18", self.ROOT_COLOR
        if ch == "B":
            return "#2b0c22", self.TARGET_COLOR
        if ch == "1":
            return self.FLOOR, self.FLOOR_EDGE
        if ch == "0":
            return self.WALL, self.WALL_EDGE
        return self.UNKNOWN, "#5a3a6d"

    def _cell_rect(self, row, col):
        tw = self.cell_px
        x0 = self.ox + col * tw
        y0 = self.oy + row * tw
        return x0, y0, x0 + tw, y0 + tw

    def _sprite_center(self, row, col):
        x0, y0, x1, y1 = self._cell_rect(row, col)
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0

    def _gradient_for(self, step, total):
        if total <= 1:
            return self.gradient[0]
        idx = int(127 * step / (total - 1))
        return self.gradient[max(0, min(127, idx))]

    def _draw_backdrop(self, width, height):
        bands = 26
        top = _rgb("#05070d")
        bottom = _rgb("#0a1222")
        for i in range(bands):
            t = i / max(1, bands - 1)
            y0 = height * i / bands
            y1 = height * (i + 1) / bands + 1
            self.canvas.create_rectangle(
                0, y0, width, y1, fill=_hex(_mix(top, bottom, t)), outline=""
            )

        glows = (
            (0.16, 0.20, 0.30, 0.24, "#0b3344"),
            (0.80, 0.16, 0.26, 0.20, "#2b1238"),
            (0.72, 0.78, 0.32, 0.24, "#102b42"),
            (0.24, 0.82, 0.24, 0.18, "#251632"),
        )
        for fx, fy, rx, ry, color in glows:
            cx, cy = width * fx, height * fy
            self.canvas.create_oval(
                cx - width * rx,
                cy - height * ry,
                cx + width * rx,
                cy + height * ry,
                fill=color,
                outline="",
                stipple="gray12",
            )

        step = 36
        for x in range(step // 2, int(width), step):
            for y in range(step // 2, int(height), step):
                self.canvas.create_oval(
                    x - 0.7, y - 0.7, x + 0.7, y + 0.7,
                    fill="#12223a", outline="",
                )

    def redraw(self):
        self.canvas.delete("all")
        self.cell_items = []
        self.visited_items = []
        self.visited_tiny = set()
        self.marker_items = []
        self.trail_items = []

        width = max(self.canvas.winfo_width(), 260)
        height = max(self.canvas.winfo_height(), 220)
        self._draw_backdrop(width, height)

        if not self.maze:
            self.cell_px = 0
            self.ox = 0
            self.oy = 0
            self.canvas.create_text(
                width / 2,
                height / 2,
                text="Abre un laberinto .in para comenzar",
                fill=self.MUTED,
                font=self.font_placeholder,
                justify="center",
            )
            self.canvas.configure(scrollregion=(0, 0, width, height))
            return

        rows, cols = self.maze.rows, self.maze.cols
        avail_w = max(40.0, width - 2 * PAD)
        avail_h = max(40.0, height - 2 * PAD)
        if self.auto_fit:
            self.base_cell = max(3.0, min(avail_w / cols, avail_h / rows))

        tw = max(2.5, self.base_cell * self.zoom)
        self.cell_px = tw
        board_w = tw * cols
        board_h = tw * rows
        self.ox = max(PAD, (width - board_w) / 2.0)
        self.oy = max(PAD, (height - board_h) / 2.0)

        frame_pad = max(5.0, min(18.0, tw * 0.35))
        left = self.ox - frame_pad
        top = self.oy - frame_pad
        right = self.ox + board_w + frame_pad
        bottom = self.oy + board_h + frame_pad

        self.canvas.create_rectangle(
            left - 3, top - 3, right + 3, bottom + 3,
            fill="", outline="#0d243b", width=4,
        )
        self.canvas.create_rectangle(
            left, top, right, bottom,
            fill="#050b16", outline=self.ACCENT_DARK, width=1,
        )

        base_ids = [[None] * cols for _ in range(rows)]
        overlay_ids = [[None] * cols for _ in range(rows)]
        for r in range(rows):
            for c in range(cols):
                base_ids[r][c] = self._draw_cell(r, c, tw)
        self.cell_items = base_ids
        self.visited_items = overlay_ids

        self._draw_origin(self.maze.root)
        self._draw_target(self.maze.target)

        bracket = min(30.0, max(9.0, min(right - left, bottom - top) * 0.12))
        for cx, cy, sx, sy, color in (
            (left, top, 1, 1, self.ACCENT),
            (right, top, -1, 1, self.PINK),
            (left, bottom, 1, -1, self.PINK),
            (right, bottom, -1, -1, self.ACCENT),
        ):
            self.canvas.create_line(
                cx, cy + sy * bracket, cx, cy, cx + sx * bracket, cy,
                fill=color, width=2,
            )

        scroll_w = max(width, right + PAD)
        scroll_h = max(height, bottom + PAD)
        self.canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))

        if self.sequence and self.index > 0:
            self._paint_range(0, self.index)
            self._update_marker_and_trail()

    @staticmethod
    def _is_wall_char(ch):
        return ch not in "1AB"

    def _draw_cell(self, row, col, tw):
        ch = self.maze.grid[row][col]
        x0, y0, x1, y1 = self._cell_rect(row, col)
        fill, outline = self._base_style(ch)
        width = max(1, int(tw * 0.05))
        if self._is_wall_char(ch):
            width = 1
        elif ch in "AB":
            width = max(1, int(tw * 0.08))
        if tw >= 14:
            inset = max(0.8, tw * 0.06)
            return _round_rect(
                self.canvas,
                x0 + inset,
                y0 + inset,
                x1 - inset,
                y1 - inset,
                max(2.0, tw * 0.16),
                fill=fill,
                outline=outline,
                width=width,
            )
        return self.canvas.create_rectangle(
            x0, y0, x1, y1, fill=fill, outline=outline, width=width
        )

    def _draw_origin(self, cell):
        if not cell:
            return
        cx, cy = self._sprite_center(*cell)
        s = self.cell_px
        if s < 7:
            self.canvas.create_oval(
                cx - 2, cy - 2, cx + 2, cy + 2,
                fill=self.ROOT_COLOR, outline="",
            )
            return
        r = s * 0.27
        self.canvas.create_oval(
            cx - r * 1.5, cy - r * 1.5, cx + r * 1.5, cy + r * 1.5,
            outline=self.ROOT_COLOR, width=max(1, int(s * 0.05)),
        )
        self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill="#102b1c", outline=self.ROOT_COLOR, width=max(1, int(s * 0.06)),
        )
        if s >= 18:
            self.canvas.create_text(
                cx, cy, text="A", fill=self.ROOT_COLOR,
                font=(self.font_button[0], max(8, int(s * 0.24)), "bold"),
            )

    def _draw_target(self, cell):
        if not cell:
            return
        cx, cy = self._sprite_center(*cell)
        s = self.cell_px
        if s < 7:
            self.canvas.create_rectangle(
                cx - 2, cy - 2, cx + 2, cy + 2,
                fill=self.TARGET_COLOR, outline="",
            )
            return
        r = s * 0.29
        diamond = (cx, cy - r * 1.35, cx + r * 1.35, cy, cx, cy + r * 1.35, cx - r * 1.35, cy)
        self.canvas.create_polygon(
            *diamond, fill="#2b0c22", outline=self.TARGET_COLOR,
            width=max(1, int(s * 0.06)),
        )
        self.canvas.create_oval(
            cx - r * 0.36, cy - r * 0.36, cx + r * 0.36, cy + r * 0.36,
            fill=self.TARGET_COLOR, outline="",
        )
        if s >= 18:
            self.canvas.create_text(
                cx, cy + r * 0.78, text="B", fill=self.TARGET_COLOR,
                font=(self.font_button[0], max(8, int(s * 0.20)), "bold"),
            )

    def _edge_color(self, color):
        return _hex(_mix(_rgb(color), (255, 255, 255), 0.45))

    def _visit_cell(self, row, col, step, total):
        if not self.cell_items:
            return
        ch = self.maze.grid[row][col]
        if ch != "1":
            return
        color = self._gradient_for(step, total)
        fill = _hex(_mix(_rgb(color), _rgb(self.FLOOR), 0.18))
        edge = self._edge_color(color)
        tw = self.cell_px
        if tw < 10:
            self.canvas.itemconfigure(
                self.cell_items[row][col],
                fill=fill,
                outline=edge,
                width=1,
            )
            self.visited_tiny.add((row, col))
            return
        item = self.visited_items[row][col]
        if item is None:
            x0, y0, x1, y1 = self._cell_rect(row, col)
            inset = max(0.8, tw * 0.08)
            width = max(1, min(2, int(tw * 0.06)))
            if tw >= 14:
                item = _round_rect(
                    self.canvas,
                    x0 + inset,
                    y0 + inset,
                    x1 - inset,
                    y1 - inset,
                    max(2.0, tw * 0.14),
                    fill=fill,
                    outline=edge,
                    width=width,
                )
            else:
                item = self.canvas.create_rectangle(
                    x0, y0, x1, y1, fill=fill, outline=edge, width=width
                )
            self.visited_items[row][col] = item
        else:
            self.canvas.itemconfigure(item, fill=fill, outline=edge)

    def _unvisit_cell(self, row, col):
        if (row, col) in self.visited_tiny:
            self.visited_tiny.discard((row, col))
            fill, outline = self._base_style(self.maze.grid[row][col])
            self.canvas.itemconfigure(
                self.cell_items[row][col], fill=fill, outline=outline, width=1
            )
            return
        if not self.visited_items:
            return
        item = self.visited_items[row][col]
        if item is not None:
            self.canvas.delete(item)
            self.visited_items[row][col] = None

    def _paint_range(self, start, end):
        if not self.sequence or not self.cell_items:
            return
        total = len(self.sequence)
        for i in range(max(0, start), min(total, end)):
            row, col = self.sequence[i]
            self._visit_cell(row, col, i, total)

    def _delete_marker(self):
        for item in self.marker_items:
            self.canvas.delete(item)
        self.marker_items = []

    def _delete_trail(self):
        for item in self.trail_items:
            self.canvas.delete(item)
        self.trail_items = []

    def _clear_overlay(self):
        for row, col in self.visited_tiny:
            if self.cell_items:
                fill, outline = self._base_style(self.maze.grid[row][col])
                self.canvas.itemconfigure(
                    self.cell_items[row][col], fill=fill, outline=outline, width=1
                )
        self.visited_tiny.clear()
        if self.visited_items:
            for row in self.visited_items:
                for item in row:
                    if item is not None:
                        self.canvas.delete(item)
        if self.cell_items:
            self.visited_items = [[None] * len(row) for row in self.cell_items]
        self._delete_marker()
        self._delete_trail()
        self.index = 0
        if hasattr(self, "progress"):
            self._update_progress()

    def _draw_cursor(self, cx, cy):
        s = self.cell_px
        if s < 7:
            r = max(1.8, s * 0.28)
            return [
                self.canvas.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    fill=self.CURRENT, outline=self.ACCENT,
                )
            ]
        items = []
        r = s * 0.24
        items.append(
            self.canvas.create_oval(
                cx - r * 1.55, cy - r * 1.55, cx + r * 1.55, cy + r * 1.55,
                outline=self.ACCENT, width=max(1, int(s * 0.04)),
                stipple="gray50",
            )
        )
        items.append(
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=self.CURRENT, outline=self.ACCENT,
                width=max(1, int(s * 0.06)),
            )
        )
        tick = r * 1.9
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            items.append(
                self.canvas.create_line(
                    cx + dx * r * 1.30, cy + dy * r * 1.30,
                    cx + dx * tick, cy + dy * tick,
                    fill=self.ACCENT_LIGHT, width=max(1, int(s * 0.03)),
                )
            )
        return items

    def _update_marker_and_trail(self):
        if not self.sequence or self.index <= 0 or not self.cell_items:
            self._delete_marker()
            self._delete_trail()
            return

        row, col = self.sequence[self.index - 1]
        cx, cy = self._sprite_center(row, col)
        self._delete_marker()
        self.marker_items = self._draw_cursor(cx, cy)

        if self.current_kind in PATH_KINDS and self.index >= 2:
            color = PATH_COLORS.get(self.current_kind, self.TRAIL)
            points = []
            for rr, cc in self.sequence[: self.index]:
                xx0, yy0, xx1, yy1 = self._cell_rect(rr, cc)
                points.extend(((xx0 + xx1) / 2.0, (yy0 + yy1) / 2.0))
            trail_width = max(1.5, min(5.0, self.cell_px * 0.20))
            trail_layers = (
                (trail_width * 3.4, "#0b2a33"),
                (trail_width * 1.8, color),
                (trail_width * 0.65, "#ffffff"),
            )
            if not self.trail_items:
                for width, layer_color in trail_layers:
                    self.trail_items.append(
                        self.canvas.create_line(
                            *points,
                            fill=layer_color,
                            width=width,
                            capstyle="round",
                            joinstyle="round",
                        )
                    )
            else:
                for item, (width, layer_color) in zip(self.trail_items, trail_layers):
                    self.canvas.coords(item, *points)
                    self.canvas.itemconfigure(item, width=width, fill=layer_color)
        else:
            self._delete_trail()

        for item in self.trail_items:
            self.canvas.tag_raise(item)
        for item in self.marker_items:
            self.canvas.tag_raise(item)

    def fit_view(self):
        self.auto_fit = True
        self.zoom = 1.0
        self._update_zoom_label()
        self.redraw()
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)

    def _zoom_by(self, factor):
        if not self.maze:
            return
        self.auto_fit = False
        self.zoom = max(0.2, min(12.0, self.zoom * factor))
        self._update_zoom_label()
        try:
            fx = self.canvas.xview()[0]
            fy = self.canvas.yview()[0]
        except tk.TclError:
            fx = fy = 0.0
        self.redraw()
        self.canvas.xview_moveto(fx)
        self.canvas.yview_moveto(fy)

    def _on_mouse_wheel(self, event):
        if not self.maze:
            return "break"
        notches = event.delta / 120.0 if event.delta else 0
        if notches == 0:
            return "break"
        self._zoom_by(1.18 ** notches)
        return "break"

    def _on_canvas_configure(self, _event=None):
        if self._resize_job is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.root.after(150, self._after_resize)

    def _after_resize(self):
        self._resize_job = None
        self.redraw()

    def _on_close(self):
        self._stop_playback()
        for temp_dir in self.temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        self.temp_dirs = []
        self.root.destroy()


def main():
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    try:
        dpi = root.winfo_fpixels("1i")
        if dpi > 0:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except tk.TclError:
        pass

    initial = sys.argv[1] if len(sys.argv) > 1 else None
    if initial and not os.path.isabs(initial):
        initial = os.path.abspath(initial)
    if initial and not os.path.isfile(initial):
        initial = None

    MazeGui(root, initial_file=initial)
    root.mainloop()


if __name__ == "__main__":
    main()





