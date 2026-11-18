#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maze: visualizador gráfico de los laberintos y recorridos de a.exe.

Uso:
    python maze_gui.py [laberinto.in]

La interfaz abre un .in con selector de archivos, ejecuta a.exe y dibuja el
laberinto junto con los .sol generados (preorden, postorden, BFS y soluciones).
"""

from __future__ import annotations

import ctypes
import glob
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Maze"
APP_SUBTITLE = "El granjero, su guadaña y el camino al granero"
PAD = 24
TICK_MS = 40
SOLVER_TIMEOUT = 120
SPEED_VALUES = [1, 2, 5, 10, 25, 50, 100, 250, 1000]

# (clave del archivo .sol, etiqueta, grupo, descripción)
KINDS = [
    ("preorder", "Preorden", "tree", "Recorrido DFS del campo desde el granjero A."),
    ("postorder", "Postorden", "tree", "Cosecha DFS: zonas internas antes que el granjero."),
    ("bfs", "BFS por niveles", "tree", "Exploración BFS: anillos de distancia desde A."),
    ("dfs_solution", "Ruta DFS", "path", "Camino del granjero A hasta el granero B con el árbol DFS."),
    ("bfs_solution", "Ruta BFS", "path", "Camino del granjero A hasta el granero B con el árbol BFS."),
]
KIND_LABEL = {key: label for key, label, _, _ in KINDS}
KIND_DESC = {key: desc for key, _, _, desc in KINDS}
PATH_KINDS = {"dfs_solution", "bfs_solution"}


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


def _rounded_points(x1, y1, x2, y2, radius):
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + radius, y1,
        x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2,
        x1 + radius, y2, x1, y2, x1, y2 - radius,
        x1, y1 + radius, x1, y1,
    ]


class RoundedButton(tk.Canvas):
    """Botón de texto con esquinas redondeadas, sin dependencias externas."""

    def __init__(self, master, text, command=None, palette=None, kind="secondary",
                 small=False, width=None, **kwargs):
        self.palette = palette or {}
        self._kind = kind
        self._text = str(text)
        self._command = command
        self._enabled = True
        self._hover = False
        self._selected = False
        self._small = small
        self._padx = 10 if small else 14
        self._font = self.palette.get("font_button_small" if small else "font_button")
        self._parent_bg = master.cget("bg") if "bg" in master.keys() else self.palette.get("bg", "#ffffff")
        try:
            measured = tkfont.Font(font=self._font).measure(self._text)
        except tk.TclError:
            measured = len(self._text) * 7
        wanted_w = max(measured + 2 * self._padx, 30)
        if width:
            wanted_w = max(wanted_w, int(width) * 8 + 14)
        wanted_h = 28 if small else 34
        super().__init__(
            master, width=wanted_w, height=wanted_h, bg=self._parent_bg,
            highlightthickness=0, bd=0, cursor="hand2", takefocus=True,
        )
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Return>", self._on_activate)
        self.bind("<space>", self._on_activate)
        self._draw()

    def _colors(self):
        p = self.palette
        if not self._enabled:
            return p.get("card_alt", "#ece6d7"), p.get("disabled", "#aaa494"), p.get("border", "#d8cbb2")
        primary = self._kind == "primary" or self._selected
        if primary:
            return p.get("accent", "#6e8f57"), "#fffdf6", p.get("accent", "#6e8f57")
        if self._kind == "ghost":
            return self._parent_bg, p.get("accent_dark", "#4f6b3f"), ""
        bg = p.get("card_hover" if self._hover else "card_alt", "#f1e9d8")
        return bg, p.get("text", "#33402c"), p.get("border", "#d8cbb2")

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), int(self.cget("width")), 20)
        h = max(self.winfo_height(), int(self.cget("height")), 18)
        bg, fg, border = self._colors()
        radius = 10 if self._small else 13
        if border and not self._selected and self._kind != "primary":
            self.create_polygon(
                *_rounded_points(1, 2, w - 1, h, radius - 1),
                smooth=True, splinesteps=18,
                fill=self.palette.get("shadow", "#e3d9c5"), outline="",
            )
        self.create_polygon(
            *_rounded_points(1, 1, w - 1, h - 2, radius),
            smooth=True, splinesteps=20, fill=bg, outline=border,
        )
        self.create_text(w / 2, h / 2 - 1, text=self._text, fill=fg, font=self._font)

    def _on_press(self, event):
        if not self._enabled:
            return
        self._pressed = True
        self.focus_set()
        x1, y1, x2, y2 = self.bbox("all") or (0, 0, 0, 0)
        if not (x1 <= event.x <= x2 and y1 <= event.y <= y2):
            self._pressed = False

    def _on_release(self, _event):
        if self._enabled and getattr(self, "_pressed", False):
            self._pressed = False
            if self._command:
                self._command()

    def _on_enter(self, _event):
        self._hover = True
        if self._enabled:
            self.configure(cursor="hand2")
            self._draw()

    def _on_leave(self, _event):
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_activate(self, _event=None):
        if self._enabled and self._command:
            self._command()
        return "break"

    def set_text(self, text):
        self._text = str(text)
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if "text" in kwargs:
            self._text = str(kwargs.pop("text"))
        if "state" in kwargs:
            self._enabled = str(kwargs.pop("state")) != "disabled"
        for ignored in ("bg", "fg", "activebackground", "activeforeground",
                        "highlightbackground", "highlightcolor", "relief"):
            kwargs.pop(ignored, None)
        result = super().configure(cnf, **kwargs)
        if cnf is None:
            self._draw()
        return result

    config = configure


class RoundedCard(tk.Canvas):
    """Tarjeta clara con borde redondeado y un marco interior para el contenido."""

    def __init__(self, master, palette, radius=16, pad=14, **kwargs):
        self.palette = palette
        self._card_bg = palette.get("card", "#fffdf6")
        self._border = palette.get("border", "#d8cbb2")
        self._radius = radius
        self._pad = pad
        parent_bg = master.cget("bg") if "bg" in master.keys() else palette.get("bg", "#f3eedf")
        super().__init__(master, bg=parent_bg, highlightthickness=0, bd=0, height=1, **kwargs)
        self.inner = tk.Frame(self, bg=self._card_bg)
        self._window = self.create_window(self._pad, self._pad, anchor="nw", window=self.inner)
        self.bind("<Configure>", self._on_configure)
        self.inner.bind("<Configure>", self._on_inner)

    def _on_configure(self, event):
        self.delete("cardbg")
        w, h = max(event.width, 2), max(event.height, 2)
        self.create_polygon(
            *_rounded_points(1, 2, w - 1, h, self._radius),
            smooth=True, splinesteps=20,
            fill=self.palette.get("shadow", "#e3d9c5"), outline="",
            tags="cardbg",
        )
        self.create_polygon(
            *_rounded_points(1, 1, w - 1, h - 2, self._radius),
            smooth=True, splinesteps=20,
            fill=self._card_bg, outline=self._border,
            tags="cardbg",
        )
        self.tag_lower("cardbg")
        self._sync_size(w)

    def _on_inner(self, _event):
        self._sync_size()

    def _sync_size(self, width=None):
        if width is None:
            width = max(1, self.winfo_width())
        inner_h = max(1, self.inner.winfo_reqheight())
        self.itemconfigure(
            self._window,
            width=max(1, width - 2 * self._pad),
            height=inner_h,
        )
        target = inner_h + 2 * self._pad
        if abs(int(self.cget("height")) - target) > 1:
            self.configure(height=target)


class KindRow(tk.Canvas):
    """Fila seleccionable redondeada con un punto de color."""

    def __init__(self, master, kind, text, palette, on_click):
        super().__init__(
            master, height=31, bg=palette["card"],
            highlightthickness=0, bd=0, cursor="hand2",
        )
        self.kind = kind
        self.text = text
        self.palette = palette
        self.on_click = on_click
        self.enabled = False
        self.selected = False
        self.hover = False
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        if self.enabled and self.on_click:
            self.on_click(self.kind)

    def _enter(self, _event=None):
        self.hover = True
        self._paint()

    def _leave(self, _event=None):
        self.hover = False
        self._paint()

    def _paint(self):
        self.delete("all")
        p = self.palette
        w = max(self.winfo_width(), 200)
        h = max(self.winfo_height(), 28)
        if self.selected:
            bg, border, dot, fg = p["card_sel"], p["accent"], p["accent"], p["text"]
        elif self.hover and self.enabled:
            bg, border, dot, fg = p["card_hover"], p["border"], p["muted"], p["text"]
        else:
            bg, border, dot, fg = p["card_alt"], p["border"], p["muted"], p["text"]
        if not self.enabled:
            fg, dot = p["disabled"], p["disabled"]
        self.create_polygon(
            *_rounded_points(2, 2, w - 3, h - 2, 12),
            smooth=True, splinesteps=18, fill=bg, outline=border,
        )
        self.create_oval(12, h / 2 - 4, 20, h / 2 + 4, fill=dot, outline="")
        self.create_text(29, h / 2, text=self.text, anchor="w", fill=fg, font=p["font_row"])

    def render(self, selected, enabled):
        self.selected = selected
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()


class MazeGui:
    BG = "#f3eedf"
    PANEL = "#fbf7ea"
    CARD = "#fffdf6"
    CARD_ALT = "#f1e9d8"
    CARD_HOVER = "#e7ddc8"
    CARD_SEL = "#dde8cf"
    BORDER = "#d8cbb2"
    SHADOW = "#e3d9c5"
    TEXT = "#33402c"
    MUTED = "#7b876d"
    DISABLED = "#b6b09e"
    ACCENT = "#6e8f57"
    ACCENT_DARK = "#4f6b3f"
    WARN = "#b7792f"
    DANGER = "#b45b4b"
    CANVAS_BG = "#efe7d4"
    WALL = "#8d897b"
    WALL_LIGHT = "#9a9688"
    WALL_MOSS = "#7e8f63"
    WALL_SIDE_LIGHT = "#726e61"
    WALL_SIDE_DARK = "#5a574d"
    FLOOR = "#6e9a4a"
    GRASS_LIGHT = "#83ad58"
    GRASS_DARK = "#5c8740"
    CUT_GRASS = "#d6c58c"
    STUBBLE = "#a89a61"
    ROOT_COLOR = "#c9b48a"
    TARGET_COLOR = "#b98a6a"
    UNKNOWN = "#8b8578"
    GRID = "#c7b99b"
    CURRENT = "#d9b86c"
    TRAIL = "#dccb93"
    BARN_BODY = "#a94f3c"
    BARN_ROOF = "#704235"
    BARN_TRIM = "#efe4c8"
    BARN_DARK = "#4e342a"
    HAT = "#d9b86c"
    SHIRT = "#6f89a0"
    SKIN = "#d8a87b"
    SKY_TOP = "#dcebf0"
    SKY_BOTTOM = "#f6efd8"
    HILL_FAR = "#b9c7a8"
    HILL_NEAR = "#9db18a"
    FIELD_STRIPE = "#e7ddc0"
    SUN_COLOR = "#f2d98a"
    CLOUD = "#fbf7ea"
    GRADIENT_STOPS = (
        (0.00, (150, 180, 98)),
        (0.42, (193, 197, 116)),
        (0.74, (218, 202, 134)),
        (1.00, (232, 214, 155)),
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
        self.view_mode = "iso"
        self.view_states = {
            "iso": {"zoom": 1.0, "auto_fit": True, "base_cell": 4.0, "xview": 0.0, "yview": 0.0},
            "top": {"zoom": 1.0, "auto_fit": True, "base_cell": 4.0, "xview": 0.0, "yview": 0.0},
        }
        self.auto_fit = True
        self.zoom = 1.0
        self.base_cell = 4.0
        self.cell_px = 0.0
        self.ox = 0.0
        self.oy = 0.0
        self.cell_items = []
        self.marker_items = []
        self.trail_items = []
        self.grass_items = {}
        self.cut_counts = {}
        self._cut_prefix = {}
        self._cut_total = 0
        self._grass_budget = 6000
        self._farmer_xy = None
        self._walk_job = None
        self._walk_token = 0
        self._swing_job = None
        self._clip_jobs = []
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
        self._update_view_buttons()
        self._populate_order_list()
        self._update_progress()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if initial_file:
            self.root.after(150, lambda: self.load_path(initial_file))
        else:
            self.root.after(150, self.redraw)

    def _setup_fonts(self):
        family = "Segoe UI" if os.name == "nt" else "Helvetica"
        title_family = "Georgia" if os.name == "nt" else "DejaVu Serif"
        self.font_title = (title_family, 22, "bold")
        self.font_subtitle = (family, 9)
        self.font_body = (family, 10)
        self.font_small = (family, 9)
        self.font_card = (family, 8, "bold")
        self.font_row = (family, 9)
        self.font_button = (family, 9, "bold")
        self.font_button_small = (family, 8, "bold")
        self.font_value = (family, 9, "bold")
        self.font_placeholder = (title_family, 14, "bold")
        self.font_mono = ("Consolas", 9) if os.name == "nt" else ("DejaVu Sans Mono", 9)

        self.palette = {
            "card": self.CARD,
            "card_alt": self.CARD_ALT,
            "card_hover": self.CARD_HOVER,
            "card_sel": self.CARD_SEL,
            "border": self.BORDER,
            "shadow": self.SHADOW,
            "text": self.TEXT,
            "muted": self.MUTED,
            "disabled": self.DISABLED,
            "accent": self.ACCENT,
            "accent_dark": self.ACCENT_DARK,
            "bg": self.BG,
            "panel": self.PANEL,
            "font_row": self.font_row,
            "font_button": self.font_button,
            "font_button_small": self.font_button_small,
            "font_card": self.font_card,
        }

    def _setup_root(self):
        self.root.title(APP_NAME)
        self.root.configure(bg=self.BG)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1360, int(screen_w * 0.92))
        height = min(980, int(screen_h * 0.92))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry("{}x{}+{}+{}".format(width, height, x, y))
        self.root.minsize(1040, 640)

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
            lightcolor=self.CARD,
            darkcolor=self.BORDER,
            troughcolor=self.CARD_ALT,
            focuscolor=self.ACCENT,
            font=self.font_body,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=self.CARD,
            background=self.CARD,
            foreground=self.TEXT,
            arrowcolor=self.ACCENT_DARK,
            bordercolor=self.BORDER,
            lightcolor=self.CARD,
            darkcolor=self.CARD,
            padding=4,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", self.CARD)],
            foreground=[("readonly", self.TEXT)],
            selectbackground=[("readonly", self.CARD)],
            selectforeground=[("readonly", self.TEXT)],
            bordercolor=[("focus", self.ACCENT)],
            arrowcolor=[("active", self.ACCENT_DARK)],
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
            troughcolor=self.CARD_ALT,
            bordercolor=self.BORDER,
            lightcolor=self.ACCENT,
            darkcolor=self.ACCENT_DARK,
        )
        style.configure(
            "Accent.Horizontal.TScale",
            background=self.CARD,
            troughcolor=self.CARD_ALT,
            bordercolor=self.BORDER,
            lightcolor=self.ACCENT,
            darkcolor=self.ACCENT_DARK,
        )
        self.root.option_add("*TCombobox*Listbox.background", self.CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#fffdf6")

    # ------------------------------------------------------------------ UI --
    def _flat_button(self, parent, text, command, kind="secondary", small=False, **kwargs):
        return RoundedButton(
            parent, text, command=command, palette=self.palette,
            kind=kind, small=small, **kwargs
        )

    def _make_card(self, parent, title):
        outer = RoundedCard(parent, self.palette, radius=16, pad=14)
        head = tk.Frame(outer.inner, bg=self.CARD)
        head.pack(fill="x")
        tk.Label(
            head,
            text=title.upper(),
            bg=self.CARD,
            fg=self.ACCENT_DARK,
            font=self.font_card,
            anchor="w",
        ).pack(side="left")
        body = tk.Frame(outer.inner, bg=self.CARD)
        body.pack(fill="both", expand=True, pady=(6, 0))
        return outer, body, head

    def _legend_chip(self, parent, color, text, outlined=False):
        frame = tk.Frame(parent, bg=self.BG)
        canvas = tk.Canvas(frame, width=12, height=12, bg=self.BG, highlightthickness=0, bd=0)
        canvas.create_oval(
            1, 1, 11, 11, fill=color,
            outline=self.BORDER if outlined else color,
        )
        canvas.pack(side="left", padx=(0, 5))
        tk.Label(frame, text=text, bg=self.BG, fg=self.MUTED, font=self.font_small).pack(side="left")
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
        label.pack(fill="x", pady=(2, 2))
        return label

    def _build_ui(self):
        self.file_var = tk.StringVar(value="Ningún campo seleccionado")
        self.status_var = tk.StringVar(value="Abre un campo .in para iniciar la cosecha.")
        self.zoom_var = tk.StringVar(value="100 %")
        self.desc_var = tk.StringVar(value="Elige una ruta de cosecha disponible.")
        self.warn_var = tk.StringVar(value="Sin advertencias.")
        self.step_var = tk.StringVar(value="—")
        self.speed_label_var = tk.StringVar(value="10 celdas/s")
        self.speed_var = tk.DoubleVar(value=3.0)
        self.quick_var = tk.StringVar(value="")

        header = tk.Frame(self.root, bg=self.PANEL)
        header.pack(fill="x")
        header_inner = tk.Frame(header, bg=self.PANEL)
        header_inner.pack(fill="x", padx=18, pady=12)
        header_inner.grid_columnconfigure(0, weight=1)

        titles = tk.Frame(header_inner, bg=self.PANEL)
        titles.grid(row=0, column=0, sticky="w")
        tk.Label(
            titles, text=APP_NAME, bg=self.PANEL, fg=self.TEXT, font=self.font_title, anchor="w"
        ).pack(anchor="w")
        tk.Label(
            titles, text=APP_SUBTITLE, bg=self.PANEL, fg=self.MUTED, font=self.font_subtitle, anchor="w"
        ).pack(anchor="w")
        tk.Label(
            titles,
            textvariable=self.file_var,
            bg=self.PANEL,
            fg=self.ACCENT_DARK,
            font=self.font_small,
            anchor="w",
        ).pack(anchor="w", pady=(3, 0))

        controls = tk.Frame(header_inner, bg=self.PANEL)
        controls.grid(row=0, column=1, sticky="e")
        tk.Label(
            controls, text="Campos:", bg=self.PANEL, fg=self.MUTED, font=self.font_small
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
        self._flat_button(controls, "Abrir campo", self.open_dialog, kind="primary").pack(side="left")
        self.regenerate_btn = self._flat_button(controls, "Regenerar", self.run_solver)
        self.regenerate_btn.pack(side="left", padx=(8, 0))

        status = tk.Label(
            self.root,
            textvariable=self.status_var,
            bg=self.PANEL,
            fg=self.MUTED,
            font=self.font_small,
            anchor="w",
            padx=16,
            pady=7,
        )
        status.pack(fill="x", side="bottom")
        self.status_lbl = status

        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 10))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, minsize=372, weight=0)

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
        self._legend_chip(toolbar, self.WALL, "Piedra", outlined=True)
        self._legend_chip(toolbar, self.FLOOR, "Pasto alto")
        self._legend_chip(toolbar, self.ROOT_COLOR, "Granjero A", outlined=True)
        self._legend_chip(toolbar, self.TARGET_COLOR, "Granero B", outlined=True)
        self._legend_chip(toolbar, self.gradient[80], "Pasto cortado")
        self._legend_chip(toolbar, self.CURRENT, "Granjero")
        self._legend_chip(toolbar, self.TRAIL, "Sendero")

        zoom_box = tk.Frame(toolbar, bg=self.BG)
        zoom_box.pack(side="right")
        tk.Label(zoom_box, textvariable=self.zoom_var, bg=self.BG, fg=self.MUTED,
                 font=self.font_small, width=6).pack(side="left", padx=(0, 6))
        self._flat_button(zoom_box, "−", lambda: self._zoom_by(1 / 1.25), small=True, width=2).pack(side="left")
        self._flat_button(zoom_box, "+", lambda: self._zoom_by(1.25), small=True, width=2).pack(
            side="left", padx=4
        )
        self._flat_button(zoom_box, "Ajustar", self.fit_view, small=True).pack(side="left")

        view_box = tk.Frame(toolbar, bg=self.BG)
        view_box.pack(side="right", padx=(0, 16))
        tk.Label(view_box, text="Vista:", bg=self.BG, fg=self.MUTED,
                 font=self.font_small).pack(side="left", padx=(0, 6))
        self.view_iso_btn = self._flat_button(
            view_box, "3D", lambda: self._switch_view("iso"), small=True, width=2
        )
        self.view_iso_btn.pack(side="left")
        self.view_top_btn = self._flat_button(
            view_box, "2D", lambda: self._switch_view("top"), small=True, width=2
        )
        self.view_top_btn.pack(side="left", padx=(4, 0))

        self._build_right_panel(body)

    def _build_right_panel(self, body):
        wrap = tk.Frame(body, bg=self.BG)
        wrap.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.right_canvas = tk.Canvas(
            wrap, bg=self.BG, highlightthickness=0, bd=0, width=372
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
        info_card, info_body, info_head = self._make_card(right, "Campo")
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
            ("Dimensiones", "dims"),
            ("Granjero A", "root"),
            ("Granero B", "target"),
            ("Pasto alto", "paths"),
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
            wraplength=300,
        )
        self.warn_lbl.grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Tarjeta de recorridos
        trav_card, trav_body, _ = self._make_card(right, "Ruta de cosecha")
        trav_card.pack(fill="x", pady=(0, 10))

        self._group_label(trav_body, "EXPLORACIÓN DEL CAMPO")
        self.kind_rows = {}
        for key, label, group, _desc in KINDS:
            if group != "tree":
                continue
            row = KindRow(trav_body, key, label, self.palette, self._on_kind_click)
            row.pack(fill="x", pady=(0, 2))
            self.kind_rows[key] = row

        separator = tk.Frame(trav_body, bg=self.BORDER, height=1)
        separator.pack(fill="x", pady=(6, 0))
        self._group_label(trav_body, "CAMINO AL GRANERO")
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
            wraplength=300,
        ).pack(fill="x", pady=(4, 5))

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
        speed_box.pack(fill="x", pady=(6, 0))
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
        self.progress.pack(fill="x", pady=(5, 3))
        tk.Label(
            trav_body,
            textvariable=self.step_var,
            bg=self.CARD,
            fg=self.MUTED,
            font=self.font_small,
            anchor="w",
        ).pack(fill="x")

        # Tarjeta de orden de nodos
        order_card, order_body, _ = self._make_card(right, "Registro de corte")
        order_card.pack(fill="x")
        self.order_card = order_card
        order_body.grid_rowconfigure(1, weight=1)
        order_body.grid_columnconfigure(0, weight=1)
        tk.Label(
            order_body,
            text="Registro  ·  (fila, col)   —   clic para saltar",
            bg=self.CARD,
            fg=self.MUTED,
            font=self.font_small,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 3))
        self.order_list = tk.Listbox(
            order_body,
            bg=self.CARD_ALT,
            fg=self.TEXT,
            selectbackground=self.ACCENT,
            selectforeground="#fffdf6",
            activestyle="none",
            exportselection=False,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.BORDER,
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
            title="Selecciona un campo (.in)",
            initialdir=self.base_dir,
            filetypes=[("Campos", "*.in"), ("Todos los archivos", "*.*")],
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
        self._reset_view_states()
        self._update_zoom_label()

        self.file_var.set(self._short_path(path))
        self._update_quick_combo()
        self._update_info()
        self._update_kind_rows()
        self._populate_order_list()
        self._set_controls_enabled(False)
        self._update_progress()
        self.redraw()
        self.set_status("Campo cargado. Ejecutando a.exe…", "info")
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
                    temp_root = tempfile.mkdtemp(prefix="maze_")
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
        raw = list(self.sequences[kind])
        self.sequence = list(reversed(raw)) if kind in PATH_KINDS else raw
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
        self.speed_label_var.set("{} celdas/s".format(SPEED_VALUES[idx]))

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
        self.btn_play.configure(text="Pausa")
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
        self.btn_play.configure(text="Reproducir")
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
        self.btn_play.configure(text="Reproducir")
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
            self.btn_play.configure(text="Reproducir")

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
        if len(self.sequence) > 1500:
            self.index = 0
            self.redraw()
            self._update_progress()
            self._update_order_selection()
        else:
            self._clear_overlay()
            self._update_marker_and_trail(animate=False)
            self._update_progress()
            self._update_order_selection()
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

        animate = False
        if target < self.index:
            for i in range(self.index - 1, target - 1, -1):
                self._uncut_step(i)
            self.index = target
        else:
            animate = (target - self.index) == 1 and target >= 2
            for i in range(self.index, target):
                self._cut_step(i, animate=animate and i == target - 1)
            self.index = target

        self._update_marker_and_trail(animate=animate)
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
    def _base_color(self, ch):
        return {
            "0": self.WALL,
            "1": self.FLOOR,
            "A": self.ROOT_COLOR,
            "B": self.TARGET_COLOR,
        }.get(ch, self.UNKNOWN)

    def _cell_rect(self, row, col):
        tw = self.cell_px
        if self.view_mode == "top":
            x = self.ox + col * tw
            y = self.oy + row * tw
            return x, y, x + tw, y + tw
        cx = self.ox + (col - row) * tw * 0.5
        cy = self.oy + (col + row) * tw * 0.25
        return cx - tw * 0.5, cy - tw * 0.25, cx + tw * 0.5, cy + tw * 0.25

    def _gradient_for(self, step, total):
        if total <= 1:
            return self.gradient[0]
        idx = int(127 * step / (total - 1))
        return self.gradient[max(0, min(127, idx))]

    def _prepare_cut_state(self):
        self.cut_counts = {}
        self._cut_prefix = {}
        self._cut_total = len(self.sequence)
        if not self.sequence or self.index <= 0 or not self.maze:
            return
        last = {}
        for i in range(min(self.index, len(self.sequence))):
            r, c = self.sequence[i]
            if self.maze.grid[r][c] != "1":
                continue
            self.cut_counts[(r, c)] = self.cut_counts.get((r, c), 0) + 1
            last[(r, c)] = i
        self._cut_prefix = last

    def _cell_fill(self, ch, row, col):
        if ch == "1" and (row, col) in self._cut_prefix:
            return self._gradient_for(self._cut_prefix[(row, col)], self._cut_total)
        return self._base_color(ch)

    def _draw_backdrop(self, width, height):
        bands = 30
        top = _rgb(self.SKY_TOP)
        bottom = _rgb(self.SKY_BOTTOM)
        for i in range(bands):
            t = i / max(1, bands - 1)
            y0 = height * i / bands
            y1 = height * (i + 1) / bands + 1
            self.canvas.create_rectangle(
                0, y0, width, y1, fill=_hex(_mix(top, bottom, t)), outline=""
            )

        sun_x, sun_y = width * 0.82, height * 0.17
        sun_r = min(width, height) * 0.052
        for factor, color in ((2.1, "#f5eccb"), (1.55, "#f3e4b6")):
            self.canvas.create_oval(
                sun_x - sun_r * factor,
                sun_y - sun_r * factor,
                sun_x + sun_r * factor,
                sun_y + sun_r * factor,
                fill=color, outline="", stipple="gray12",
            )
        self.canvas.create_oval(
            sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r,
            fill=self.SUN_COLOR, outline="#e7c96f",
        )

        for fx, fy, size in ((0.13, 0.16, 1.0), (0.37, 0.10, 0.72), (0.62, 0.21, 0.86)):
            x, y = width * fx, height * fy
            w = min(width, height) * 0.10 * size
            h = w * 0.34
            self.canvas.create_oval(x - w, y - h, x + w, y + h, fill=self.CLOUD, outline="")
            self.canvas.create_oval(
                x - w * 0.55, y - h * 1.7, x + w * 0.45, y + h * 0.35,
                fill=self.CLOUD, outline="",
            )
            self.canvas.create_oval(
                x - w * 0.10, y - h * 2.1, x + w * 0.85, y + h * 0.40,
                fill=self.CLOUD, outline="",
            )

        horizon = height * 0.62
        far = [
            -width * 0.15, horizon,
            width * 0.08, horizon - height * 0.09,
            width * 0.30, horizon - height * 0.02,
            width * 0.50, horizon - height * 0.12,
            width * 0.72, horizon - height * 0.03,
            width * 0.92, horizon - height * 0.10,
            width * 1.15, horizon,
        ]
        self.canvas.create_polygon(
            *far, smooth=True, splinesteps=30, fill=self.HILL_FAR, outline=""
        )
        near = [
            -width * 0.15, horizon + height * 0.02,
            width * 0.12, horizon - height * 0.055,
            width * 0.34, horizon + height * 0.005,
            width * 0.58, horizon - height * 0.06,
            width * 0.82, horizon + height * 0.010,
            width * 1.15, horizon - height * 0.02,
            width * 1.15, height,
            -width * 0.15, height,
        ]
        self.canvas.create_polygon(
            *near, smooth=True, splinesteps=30, fill=self.HILL_NEAR, outline=""
        )

        for i in range(7):
            y = horizon + height * (0.045 + i * 0.060)
            self.canvas.create_line(
                0, y, width, y + height * 0.015,
                fill=self.FIELD_STRIPE, width=max(1, int(height * 0.018)),
            )

    def redraw(self):
        self._cancel_farmer_anims()
        self._cancel_clipping()
        self.canvas.delete("all")
        self.cell_items = []
        self.marker_items = []
        self.trail_items = []
        self.grass_items = {}
        self.cut_counts = {}
        self._cut_prefix = {}
        self._cut_total = len(self.sequence)
        self._grass_budget = 6000
        self._farmer_xy = None
        self._prepare_cut_state()

        width = max(self.canvas.winfo_width(), 240)
        height = max(self.canvas.winfo_height(), 200)
        self._draw_backdrop(width, height)

        if not self.maze:
            self.cell_px = 0
            self.ox = 0
            self.oy = 0
            self.wall_h = 0
            self.canvas.create_text(
                width / 2,
                height / 2,
                text="Abre un campo .in para comenzar la cosecha",
                fill=self.MUTED,
                font=self.font_placeholder,
                justify="center",
            )
            self.canvas.configure(scrollregion=(0, 0, width, height))
            return

        if self.view_mode == "top":
            self._redraw_top(width, height)
        else:
            self._redraw_iso(width, height)

        self._update_marker_and_trail(animate=False)

    def _redraw_iso(self, width, height):
        maze = self.maze
        rows, cols = maze.rows, maze.cols
        span = rows + cols
        avail_w = max(40.0, width - 2 * PAD - 52.0)
        avail_h = max(40.0, height - 2 * PAD - 52.0)

        if self.auto_fit:
            fit_w = 2.0 * avail_w / max(1, span)
            fit_h = avail_h / max(1.0, span / 4.0 + 0.30)
            self.base_cell = max(2.5, min(fit_w, fit_h))

        tw = max(2.5, self.base_cell * self.zoom)
        self.cell_px = tw
        hw, hh = tw * 0.5, tw * 0.25
        self.wall_h = wall_h = max(1.5, min(tw * 0.30, 26.0))
        plate_pad = max(5.0, min(26.0, tw * 0.40))
        plate_depth = max(4.0, wall_h * 0.8)

        content_w = span * hw
        content_h = span * hh + wall_h
        margin_x = max(0.0, (width - 2 * PAD - 2 * plate_pad - content_w) / 2.0)
        margin_y = max(0.0, (height - 2 * PAD - 2 * plate_pad - content_h) / 2.0)
        self.ox = PAD + plate_pad + margin_x + rows * hw
        self.oy = PAD + plate_pad + margin_y + hh + wall_h

        left = self.ox - rows * hw - plate_pad
        right = self.ox + cols * hw + plate_pad
        top = self.oy - hh - wall_h - plate_pad
        bottom = self.oy + (rows + cols - 1) * hh + plate_pad

        plate = (
            self.ox, top,
            right, self.oy + (cols - 1) * hh,
            self.ox + (cols - rows) * hw, bottom,
            left, self.oy + (rows - 1) * hh,
        )
        self._draw_plate_iso(plate, plate_depth, tw)

        ids = [[None] * cols for _ in range(rows)]
        for depth in range(rows + cols - 1):
            first = max(0, depth - (cols - 1))
            last = min(rows - 1, depth)
            for r in range(first, last + 1):
                c = depth - r
                ids[r][c] = self._draw_cell_iso(r, c, hw, hh, wall_h, tw)
                if maze.target == (r, c):
                    self._draw_barn_iso(r, c)
                if maze.grid[r][c] == "1" and (r, c) not in self._cut_prefix:
                    self._add_grass_iso(r, c, self.ox + (c - r) * hw, self.oy + (c + r) * hh, tw)
        self.cell_items = ids

        shadow_extent = plate_depth + max(4.0, tw * 0.18)
        scroll_w = max(width, right + shadow_extent + PAD)
        scroll_h = max(height, bottom + shadow_extent + PAD)
        self.canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))

    def _draw_plate_iso(self, plate, depth, tw):
        self.canvas.create_polygon(
            *self._shift_points(plate, 0.0, depth + max(4.0, tw * 0.18)),
            fill="#b9a77e", outline="",
        )
        self.canvas.create_polygon(
            plate[6], plate[7], plate[4], plate[5],
            plate[4], plate[5] + depth, plate[6], plate[7] + depth,
            fill="#9f8a62", outline="",
        )
        self.canvas.create_polygon(
            plate[4], plate[5], plate[2], plate[3],
            plate[2], plate[3] + depth, plate[4], plate[5] + depth,
            fill="#8f7a54", outline="",
        )
        self.canvas.create_polygon(plate, fill="#c8b58b", outline="#a9956d", width=1)
        cx = sum(plate[0::2]) / 4.0
        cy = sum(plate[1::2]) / 4.0
        radius = max(8.0, tw * 0.6)
        self.canvas.create_oval(
            cx - radius, cy - radius * 0.38,
            cx + radius, cy + radius * 0.38,
            outline="#b5a078", width=max(1, int(tw * 0.04)),
            stipple="gray50",
        )

    def _redraw_top(self, width, height):
        maze = self.maze
        rows, cols = maze.rows, maze.cols
        avail_w = max(40.0, width - 2 * PAD)
        avail_h = max(40.0, height - 2 * PAD)

        if self.auto_fit:
            fit_w = avail_w / max(1, cols)
            fit_h = avail_h / max(1, rows)
            self.base_cell = max(2.5, min(fit_w, fit_h) * 0.96)

        tw = max(2.5, self.base_cell * self.zoom)
        self.cell_px = tw
        self.wall_h = 0.0
        plate_pad = max(7.0, min(26.0, tw * 0.45))
        board_w = cols * tw
        board_h = rows * tw
        margin_x = max(0.0, (width - 2 * PAD - 2 * plate_pad - board_w) / 2.0)
        margin_y = max(0.0, (height - 2 * PAD - 2 * plate_pad - board_h) / 2.0)
        self.ox = PAD + plate_pad + margin_x
        self.oy = PAD + plate_pad + margin_y

        left = self.ox - plate_pad
        top = self.oy - plate_pad
        right = self.ox + board_w + plate_pad
        bottom = self.oy + board_h + plate_pad
        radius = max(5.0, min(14.0, tw * 0.7))
        self.canvas.create_polygon(
            *_rounded_points(left + 4, top + 6, right + 4, bottom + 6, radius),
            smooth=True, splinesteps=22, fill="#b9a77e", outline="",
        )
        self.canvas.create_polygon(
            *_rounded_points(left, top, right, bottom, radius),
            smooth=True, splinesteps=22, fill="#c8b58b", outline="#a9956d", width=1,
        )
        self.canvas.create_rectangle(
            self.ox, self.oy, self.ox + board_w, self.oy + board_h,
            fill="#d8caa9", outline="#b8a77f",
        )

        ids = [[None] * cols for _ in range(rows)]
        for r in range(rows):
            for c in range(cols):
                ids[r][c] = self._draw_cell_top(r, c, tw)
                if maze.target == (r, c):
                    self._draw_barn_top(r, c)
                if maze.grid[r][c] == "1" and (r, c) not in self._cut_prefix:
                    self._add_grass_top(r, c, tw)
        self.cell_items = ids

        scroll_w = max(width, right + 14 + PAD)
        scroll_h = max(height, bottom + 14 + PAD)
        self.canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))

    @staticmethod
    def _shift_points(points, dx, dy):
        shifted = list(points)
        for i in range(0, len(shifted), 2):
            shifted[i] += dx
            shifted[i + 1] += dy
        return shifted

    @staticmethod
    def _is_wall_char(ch):
        return ch not in "1AB"

    def _draw_cell_iso(self, row, col, hw, hh, wall_h, tw):
        ch = self.maze.grid[row][col]
        cx = self.ox + (col - row) * hw
        cy = self.oy + (col + row) * hh

        if self._is_wall_char(ch):
            top_y = cy - wall_h
            wall_color = self.WALL if ch == "0" else self.UNKNOWN
            if tw < 7.5:
                return self.canvas.create_polygon(
                    cx, top_y - hh, cx + hw, top_y,
                    cx, top_y + hh, cx - hw, top_y,
                    fill=wall_color, outline="",
                )
            left_covered = row + 1 < self.maze.rows and self._is_wall_char(self.maze.grid[row + 1][col])
            right_covered = col + 1 < self.maze.cols and self._is_wall_char(self.maze.grid[row][col + 1])
            if not left_covered:
                self.canvas.create_polygon(
                    cx - hw, top_y, cx, top_y + hh,
                    cx, cy + hh, cx - hw, cy,
                    fill=self.WALL_SIDE_LIGHT, outline="",
                )
            if not right_covered:
                self.canvas.create_polygon(
                    cx, top_y + hh, cx + hw, top_y,
                    cx + hw, cy, cx, cy + hh,
                    fill=self.WALL_SIDE_DARK, outline="",
                )
            top = self.canvas.create_polygon(
                cx, top_y - hh, cx + hw, top_y,
                cx, top_y + hh, cx - hw, top_y,
                fill=wall_color, outline=self.GRID, width=1,
            )
            if tw >= 18:
                self._decorate_stone_iso(cx, top_y, hw, hh)
            return top

        base = self._cell_fill(ch, row, col)
        if tw < 9.5:
            return self.canvas.create_polygon(
                cx, cy - hh, cx + hw, cy,
                cx, cy + hh, cx - hw, cy,
                fill=base, outline="",
            )

        inset = max(0.35, min(2.0, tw * 0.06))
        k = max(0.55, 1.0 - 2.0 * inset / tw)
        self.canvas.create_polygon(
            cx, cy - hh, cx + hw, cy,
            cx, cy + hh, cx - hw, cy,
            fill="#b8a57b", outline=self.GRID, width=1,
        )
        return self.canvas.create_polygon(
            cx, cy - hh * k, cx + hw * k, cy,
            cx, cy + hh * k, cx - hw * k, cy,
            fill=base, outline="",
        )

    def _decorate_stone_iso(self, cx, cy, hw, hh):
        n = (cx, cy - hh)
        e = (cx + hw, cy)
        s = (cx, cy + hh)
        w = (cx - hw, cy)
        mid_ne = ((n[0] + e[0]) / 2, (n[1] + e[1]) / 2)
        mid_se = ((e[0] + s[0]) / 2, (e[1] + s[1]) / 2)
        mid_sw = ((s[0] + w[0]) / 2, (s[1] + w[1]) / 2)
        mid_nw = ((w[0] + n[0]) / 2, (w[1] + n[1]) / 2)
        center = (cx, cy)
        stones = (
            (n, mid_ne, center, mid_nw, self.WALL_LIGHT),
            (mid_ne, e, mid_se, center, self.WALL),
            (center, mid_se, s, mid_sw, self.WALL_LIGHT),
            (mid_nw, center, mid_sw, w, self.WALL),
        )
        for a, b, c, d, color in stones:
            self.canvas.create_polygon(
                a[0], a[1], b[0], b[1], c[0], c[1], d[0], d[1],
                fill=color, outline=self.WALL_SIDE_LIGHT, width=1,
            )
        self.canvas.create_line(
            w[0] * 0.86 + cx * 0.14, w[1] * 0.86 + cy * 0.14,
            mid_sw[0] * 0.92 + cx * 0.08, mid_sw[1] * 0.92 + cy * 0.08,
            fill=self.WALL_MOSS, width=max(1, int(self.cell_px * 0.035)),
            capstyle="round",
        )

    def _draw_cell_top(self, row, col, tw):
        ch = self.maze.grid[row][col]
        x0 = self.ox + col * tw
        y0 = self.oy + row * tw
        x1, y1 = x0 + tw, y0 + tw
        if self._is_wall_char(ch):
            wall_color = self.WALL if ch == "0" else self.UNKNOWN
            inset = max(0.0, min(2.0, tw * 0.06))
            if tw >= 6:
                self.canvas.create_rectangle(
                    x0 + inset + 1.4, y0 + inset + 1.8,
                    x1 - inset + 1.4, y1 - inset + 1.8,
                    fill="#b9a77e", outline="",
                )
            item = self.canvas.create_rectangle(
                x0 + inset, y0 + inset, x1 - inset, y1 - inset,
                fill=wall_color, outline=self.GRID if tw >= 9 else "",
            )
            if tw >= 18:
                self._decorate_stone_top(x0 + inset, y0 + inset, x1 - inset, y1 - inset)
            return item

        base = self._cell_fill(ch, row, col)
        inset = max(0.0, min(2.0, tw * 0.045))
        return self.canvas.create_rectangle(
            x0 + inset, y0 + inset, x1 - inset, y1 - inset,
            fill=base, outline=self.GRID if tw >= 10 else "",
        )

    def _decorate_stone_top(self, x0, y0, x1, y1):
        w, h = x1 - x0, y1 - y0
        mid_x, mid_y = x0 + w / 2, y0 + h / 2
        stones = (
            (x0, y0, mid_x - 1, mid_y - 1),
            (mid_x + 1, y0, x1, mid_y - 1),
            (x0, mid_y + 1, mid_x - 1, y1),
            (mid_x + 1, mid_y + 1, x1, y1),
        )
        for idx, (a, b, c, d) in enumerate(stones):
            self.canvas.create_rectangle(
                a, b, c, d,
                fill=self.WALL_LIGHT if idx % 2 == 0 else self.WALL,
                outline=self.WALL_SIDE_LIGHT,
            )
        self.canvas.create_line(
            x0 + w * 0.08, y1 - h * 0.10, x0 + w * 0.36, y1 - h * 0.16,
            fill=self.WALL_MOSS, width=max(1, int(self.cell_px * 0.045)),
            capstyle="round",
        )

    def _add_grass_iso(self, row, col, cx, cy, tw):
        if tw < 12 or self._grass_budget <= 0:
            return
        rng = random.Random(row * 100003 + col * 97 + 17)
        items = []
        count = 3 if tw < 24 else 5
        for i in range(count):
            if self._grass_budget <= 0:
                break
            bx = cx + rng.uniform(-0.26, 0.26) * tw
            by = cy + rng.uniform(-0.03, 0.13) * tw
            height = tw * rng.uniform(0.06, 0.14)
            lean = rng.uniform(-0.045, 0.045) * tw
            color = self.GRASS_LIGHT if i % 3 else self.GRASS_DARK
            items.append(
                self.canvas.create_line(
                    bx, by, bx + lean, by - height,
                    fill=color, width=max(1, int(tw * 0.016)),
                    capstyle="round",
                )
            )
            self._grass_budget -= 1
        self.grass_items[(row, col)] = items

    def _add_grass_top(self, row, col, tw):
        if tw < 12 or self._grass_budget <= 0:
            return
        rng = random.Random(row * 100019 + col * 89 + 31)
        x0 = self.ox + col * tw
        y0 = self.oy + row * tw
        items = []
        count = 3 if tw < 24 else 5
        for i in range(count):
            if self._grass_budget <= 0:
                break
            bx = x0 + rng.uniform(0.24, 0.76) * tw
            by = y0 + rng.uniform(0.60, 0.84) * tw
            height = tw * rng.uniform(0.10, 0.20)
            lean = rng.uniform(-0.055, 0.055) * tw
            color = self.GRASS_LIGHT if i % 3 else self.GRASS_DARK
            items.append(
                self.canvas.create_line(
                    bx, by, bx + lean, by - height,
                    fill=color, width=max(1, int(tw * 0.02)),
                    capstyle="round",
                )
            )
            self._grass_budget -= 1
        self.grass_items[(row, col)] = items

    def _set_grass_visible(self, row, col, visible):
        for item in self.grass_items.get((row, col), ()):
            try:
                self.canvas.itemconfigure(item, state="normal" if visible else "hidden")
            except tk.TclError:
                pass

    def _apply_cut_visual(self, row, col, step, total):
        item = self.cell_items[row][col]
        if item is not None:
            self.canvas.itemconfigure(item, fill=self._gradient_for(step, total))
        self._set_grass_visible(row, col, False)

    def _draw_barn_iso(self, row, col):
        cx, cy = self._sprite_center(row, col)
        s = self.cell_px
        if s < 7:
            self.canvas.create_rectangle(
                cx - s * 0.20, cy - s * 0.24, cx + s * 0.20, cy + s * 0.16,
                fill=self.BARN_BODY, outline=self.BARN_DARK,
            )
            self.canvas.create_polygon(
                cx - s * 0.26, cy - s * 0.22, cx, cy - s * 0.44,
                cx + s * 0.26, cy - s * 0.22,
                fill=self.BARN_ROOF, outline=self.BARN_DARK,
            )
            return
        self.canvas.create_oval(
            cx - s * 0.44, cy + s * 0.18, cx + s * 0.44, cy + s * 0.39,
            fill="#a99772", outline="", stipple="gray50",
        )
        bw = s * 0.34
        bh = s * 0.34
        self.canvas.create_rectangle(
            cx - bw, cy - bh * 0.34, cx + bw, cy + bh,
            fill=self.BARN_BODY, outline=self.BARN_DARK, width=max(1, int(s * 0.02)),
        )
        self.canvas.create_polygon(
            cx - bw - s * 0.05, cy - bh * 0.30,
            cx, cy - bh * 1.10,
            cx + bw + s * 0.05, cy - bh * 0.30,
            fill=self.BARN_ROOF, outline=self.BARN_DARK, width=max(1, int(s * 0.02)),
        )
        self.canvas.create_rectangle(
            cx - s * 0.105, cy + s * 0.04, cx + s * 0.105, cy + bh,
            fill=self.BARN_DARK, outline=self.BARN_TRIM,
            width=max(1, int(s * 0.018)),
        )
        self.canvas.create_line(
            cx - s * 0.10, cy + s * 0.05, cx + s * 0.10, cy + s * 0.30,
            fill=self.BARN_TRIM, width=max(1, int(s * 0.016)),
        )
        self.canvas.create_line(
            cx + s * 0.10, cy + s * 0.05, cx - s * 0.10, cy + s * 0.30,
            fill=self.BARN_TRIM, width=max(1, int(s * 0.016)),
        )
        self.canvas.create_rectangle(
            cx - bw, cy - bh * 0.34, cx + bw, cy - bh * 0.27,
            fill=self.BARN_TRIM, outline="",
        )
        if s >= 16:
            self.canvas.create_oval(
                cx + bw * 0.50, cy + bh * 0.40, cx + bw * 1.10, cy + bh * 0.80,
                fill="#d8b86a", outline="#9f8545",
            )

    def _draw_barn_top(self, row, col):
        cx, cy = self._sprite_center(row, col)
        s = self.cell_px
        if s < 7:
            self.canvas.create_rectangle(
                cx - s * 0.25, cy - s * 0.22, cx + s * 0.25, cy + s * 0.22,
                fill=self.BARN_ROOF, outline=self.BARN_DARK,
            )
            return
        self.canvas.create_oval(
            cx - s * 0.36, cy - s * 0.24, cx + s * 0.36, cy + s * 0.32,
            fill="#b9a77e", outline="",
        )
        self.canvas.create_rectangle(
            cx - s * 0.32, cy - s * 0.26, cx + s * 0.32, cy + s * 0.26,
            fill=self.BARN_BODY, outline=self.BARN_DARK, width=max(1, int(s * 0.02)),
        )
        self.canvas.create_polygon(
            cx - s * 0.36, cy - s * 0.20,
            cx, cy - s * 0.42,
            cx + s * 0.36, cy - s * 0.20,
            cx + s * 0.30, cy - s * 0.06,
            cx - s * 0.30, cy - s * 0.06,
            fill=self.BARN_ROOF, outline=self.BARN_DARK,
        )
        self.canvas.create_line(
            cx, cy - s * 0.40, cx, cy - s * 0.08,
            fill=self.BARN_TRIM, width=max(1, int(s * 0.022)),
        )
        self.canvas.create_rectangle(
            cx - s * 0.10, cy + s * 0.02, cx + s * 0.10, cy + s * 0.24,
            fill=self.BARN_DARK, outline=self.BARN_TRIM,
            width=max(1, int(s * 0.014)),
        )


    def _sprite_center(self, row, col):
        x0, y0, x1, y1 = self._cell_rect(row, col)
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0

    def _draw_farmer(self, cx, cy, angle=-0.95, walking=False, phase=0.0):
        if self.view_mode == "top":
            return self._draw_farmer_top(cx, cy, angle, walking, phase)
        return self._draw_farmer_iso(cx, cy, angle, walking, phase)

    def _scythe_items(self, cx, cy, angle, s):
        ss = min(s, 84.0)
        hx, hy = cx + ss * 0.10, cy - ss * 0.10
        dx, dy = math.cos(angle), math.sin(angle)
        ex, ey = hx + dx * ss * 0.46, hy + dy * ss * 0.46
        items = [
            self.canvas.create_line(
                hx, hy, ex, ey, fill="#7c5a3e",
                width=max(1, int(ss * 0.035)), capstyle="round",
            )
        ]
        px, py = -dy, dx
        midx = ex + px * ss * 0.14 - dx * ss * 0.015
        midy = ey + py * ss * 0.14 - dy * ss * 0.015
        tipx = ex + px * ss * 0.31 + dx * ss * 0.07
        tipy = ey + py * ss * 0.31 + dy * ss * 0.07
        items.append(
            self.canvas.create_line(
                ex, ey, midx, midy, tipx, tipy,
                fill="#6f7a80", width=max(2, int(ss * 0.050)),
                smooth=True, splinesteps=12, capstyle="round",
            )
        )
        items.append(
            self.canvas.create_line(
                ex, ey, midx, midy, tipx, tipy,
                fill="#d7dee0", width=max(1, int(ss * 0.026)),
                smooth=True, splinesteps=12, capstyle="round",
            )
        )
        return items

    def _draw_farmer_iso(self, cx, cy, angle=-0.95, walking=False, phase=0.0):
        s = min(self.cell_px, 88.0)
        if s < 7:
            r = max(1.6, s * 0.24)
            return [
                self.canvas.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    fill=self.CURRENT, outline=self.BARN_DARK,
                )
            ]
        items = []
        bob = math.sin(phase) * s * 0.018 if walking else 0.0
        cy -= bob
        items.append(
            self.canvas.create_oval(
                cx - s * 0.30, cy + s * 0.20, cx + s * 0.30, cy + s * 0.36,
                fill="#a99772", outline="", stipple="gray50",
            )
        )
        swing = math.sin(phase) * s * 0.05 if walking else 0.0
        items.append(
            self.canvas.create_line(
                cx - s * 0.05, cy + s * 0.10,
                cx - s * 0.10 + swing, cy + s * 0.30,
                fill="#5c5140", width=max(1, int(s * 0.045)), capstyle="round",
            )
        )
        items.append(
            self.canvas.create_line(
                cx + s * 0.05, cy + s * 0.10,
                cx + s * 0.10 - swing, cy + s * 0.30,
                fill="#5c5140", width=max(1, int(s * 0.045)), capstyle="round",
            )
        )
        items.append(
            self.canvas.create_oval(
                cx - s * 0.14, cy - s * 0.14, cx + s * 0.14, cy + s * 0.15,
                fill=self.SHIRT, outline="#4e6276", width=max(1, int(s * 0.02)),
            )
        )
        items.append(
            self.canvas.create_line(
                cx + s * 0.08, cy - s * 0.06, cx + s * 0.17, cy + s * 0.03,
                fill=self.SKIN, width=max(1, int(s * 0.035)), capstyle="round",
            )
        )
        items.append(
            self.canvas.create_line(
                cx - s * 0.08, cy - s * 0.06, cx - s * 0.16, cy + s * 0.04,
                fill=self.SKIN, width=max(1, int(s * 0.035)), capstyle="round",
            )
        )
        items.append(
            self.canvas.create_oval(
                cx - s * 0.10, cy - s * 0.29, cx + s * 0.10, cy - s * 0.09,
                fill=self.SKIN, outline="#b8875f", width=max(1, int(s * 0.015)),
            )
        )
        items.append(
            self.canvas.create_oval(
                cx - s * 0.20, cy - s * 0.31, cx + s * 0.20, cy - s * 0.23,
                fill=self.HAT, outline="#a88947",
            )
        )
        items.append(
            self.canvas.create_oval(
                cx - s * 0.11, cy - s * 0.39, cx + s * 0.11, cy - s * 0.25,
                fill=self.HAT, outline="#a88947",
            )
        )
        items.extend(self._scythe_items(cx, cy, angle, s))
        return items

    def _draw_farmer_top(self, cx, cy, angle=-0.95, walking=False, phase=0.0):
        s = min(self.cell_px, 88.0)
        if s < 7:
            r = max(1.6, s * 0.24)
            return [
                self.canvas.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    fill=self.CURRENT, outline=self.BARN_DARK,
                )
            ]
        items = [
            self.canvas.create_oval(
                cx - s * 0.30, cy - s * 0.20, cx + s * 0.30, cy + s * 0.30,
                fill="#a99772", outline="", stipple="gray50",
            )
        ]
        items.extend(self._scythe_items(cx - s * 0.05, cy + s * 0.02, angle, s))
        items.append(
            self.canvas.create_oval(
                cx - s * 0.16, cy - s * 0.13, cx + s * 0.16, cy + s * 0.17,
                fill=self.SHIRT, outline="#4e6276", width=max(1, int(s * 0.02)),
            )
        )
        items.append(
            self.canvas.create_oval(
                cx - s * 0.18, cy - s * 0.22, cx + s * 0.18, cy - s * 0.12,
                fill=self.HAT, outline="#a88947",
            )
        )
        items.append(
            self.canvas.create_oval(
                cx - s * 0.10, cy - s * 0.27, cx + s * 0.10, cy - s * 0.14,
                fill=self.HAT, outline="#a88947",
            )
        )
        return items

    def _render_farmer_at(self, cx, cy, angle=-0.95, walking=False, phase=0.0):
        self._delete_marker()
        self._farmer_xy = (cx, cy)
        self.marker_items = self._draw_farmer(cx, cy, angle, walking, phase)
        for item in self.marker_items:
            self.canvas.tag_raise(item)

    def _place_farmer(self, cx, cy, animate=False):
        self._cancel_walk()
        self._cancel_swing()
        self._render_farmer_at(cx, cy, -0.95, False, 0.0)
        if animate:
            self._start_swing()

    def _start_walk(self, start_xy, end_xy):
        if self._current_speed() > 100 or self.cell_px < 6:
            self._place_farmer(*end_xy, animate=True)
            return
        sx, sy = start_xy
        tx, ty = end_xy
        distance = math.hypot(tx - sx, ty - sy)
        duration = max(80.0, min(180.0, 80.0 + distance * 0.9))
        self._cancel_walk()
        self._cancel_swing()
        self._walk_token += 1
        token = self._walk_token
        start = time.perf_counter()

        def frame():
            if token != self._walk_token:
                return
            t = min(1.0, (time.perf_counter() - start) * 1000.0 / duration)
            ease = t * t * (3.0 - 2.0 * t)
            x = sx + (tx - sx) * ease
            y = sy + (ty - sy) * ease
            bob = math.sin(t * math.pi) * self.cell_px * 0.035
            angle = -0.95 + math.sin(t * math.pi) * 0.55
            self._render_farmer_at(x, y - bob, angle, True, t * math.pi * 2.0)
            if t < 1.0:
                self._walk_job = self.root.after(16, frame)
            else:
                self._walk_job = None
                self._place_farmer(tx, ty, animate=True)

        frame()

    def _start_swing(self):
        if self._farmer_xy is None or self.cell_px < 7:
            return
        self._cancel_swing()
        self._walk_token += 1
        token = self._walk_token
        frames = 5
        cx, cy = self._farmer_xy

        def step(k):
            if token != self._walk_token:
                return
            if k > frames:
                self._swing_job = None
                self._render_farmer_at(cx, cy, -0.95, False, 0.0)
                return
            t = k / frames
            angle = -0.95 + math.sin(t * math.pi) * 1.35
            self._render_farmer_at(cx, cy, angle, False, 0.0)
            self._swing_job = self.root.after(26, lambda: step(k + 1))

        step(1)

    def _cancel_walk(self):
        if self._walk_job is not None:
            try:
                self.root.after_cancel(self._walk_job)
            except tk.TclError:
                pass
            self._walk_job = None

    def _cancel_swing(self):
        if self._swing_job is not None:
            try:
                self.root.after_cancel(self._swing_job)
            except tk.TclError:
                pass
            self._swing_job = None

    def _cancel_farmer_anims(self):
        self._cancel_walk()
        self._cancel_swing()
        self._walk_token += 1

    def _spawn_clippings(self, cx, cy):
        if self.cell_px < 9 or self._current_speed() > 100 or len(self._clip_jobs) >= 4:
            return
        pieces = []
        for _ in range(4):
            x = cx + random.uniform(-0.24, 0.24) * self.cell_px
            y = cy + random.uniform(-0.10, 0.16) * self.cell_px
            vx = random.uniform(-0.55, 0.55) * self.cell_px
            vy = random.uniform(-1.1, -0.45) * self.cell_px
            item = self.canvas.create_line(
                x, y, x + self.cell_px * 0.05, y - self.cell_px * 0.05,
                fill=random.choice((self.GRASS_LIGHT, self.GRASS_DARK, self.CUT_GRASS)),
                width=max(1, int(self.cell_px * 0.025)), capstyle="round",
            )
            pieces.append((item, x, y, vx, vy))
        job = {"items": pieces, "start": time.perf_counter(), "id": None}
        self._clip_jobs.append(job)

        def frame():
            if job not in self._clip_jobs:
                return
            t = min(1.0, (time.perf_counter() - job["start"]) / 0.28)
            for item, x, y, vx, vy in pieces:
                nx = x + vx * t
                ny = y + vy * t + 0.5 * self.cell_px * 3.0 * t * t
                try:
                    self.canvas.coords(
                        item, nx, ny, nx + self.cell_px * 0.05, ny - self.cell_px * 0.04
                    )
                except tk.TclError:
                    return
            if t < 1.0:
                job["id"] = self.root.after(16, frame)
            else:
                for item, _x, _y, _vx, _vy in pieces:
                    self.canvas.delete(item)
                if job in self._clip_jobs:
                    self._clip_jobs.remove(job)

        frame()

    def _cancel_clipping(self):
        for job in self._clip_jobs:
            if job.get("id") is not None:
                try:
                    self.root.after_cancel(job["id"])
                except tk.TclError:
                    pass
            for item, _x, _y, _vx, _vy in job["items"]:
                try:
                    self.canvas.delete(item)
                except tk.TclError:
                    pass
        self._clip_jobs = []

    def _cut_step(self, index, animate=False):
        if not self.sequence or not self.cell_items:
            return
        total = len(self.sequence)
        if index >= total:
            return
        r, c = self.sequence[index]
        ch = self.maze.grid[r][c]
        if ch != "1":
            return
        count = self.cut_counts.get((r, c), 0)
        self.cut_counts[(r, c)] = count + 1
        if count == 0:
            self._apply_cut_visual(r, c, index, total)
            if animate:
                self._spawn_clippings(*self._sprite_center(r, c))

    def _uncut_step(self, index):
        if not self.sequence or not self.cell_items:
            return
        total = len(self.sequence)
        if index >= total:
            return
        r, c = self.sequence[index]
        ch = self.maze.grid[r][c]
        if ch != "1":
            return
        count = self.cut_counts.get((r, c), 0)
        if count <= 1:
            self.cut_counts.pop((r, c), None)
            item = self.cell_items[r][c]
            if item is not None:
                self.canvas.itemconfigure(item, fill=self.FLOOR)
            self._set_grass_visible(r, c, True)
        else:
            self.cut_counts[(r, c)] = count - 1

    def _delete_marker(self):
        for item in self.marker_items:
            try:
                self.canvas.delete(item)
            except tk.TclError:
                pass
        self.marker_items = []

    def _delete_trail(self):
        for item in self.trail_items:
            try:
                self.canvas.delete(item)
            except tk.TclError:
                pass
        self.trail_items = []

    def _clear_overlay(self):
        self._cancel_farmer_anims()
        self._cancel_clipping()
        if self.cell_items and self.maze:
            for r, c in list(self.cut_counts):
                item = self.cell_items[r][c]
                if item is not None:
                    self.canvas.itemconfigure(item, fill=self.FLOOR)
                self._set_grass_visible(r, c, True)
        self.cut_counts = {}
        self._cut_prefix = {}
        self._delete_marker()
        self._delete_trail()
        self._farmer_xy = None
        self.index = 0
        if hasattr(self, "progress"):
            self._update_progress()

    def _update_marker_and_trail(self, animate=True):
        self._delete_trail()
        if not self.cell_items or not self.maze:
            self._delete_marker()
            return

        if self.current_kind in PATH_KINDS and self.sequence and self.index >= 2:
            points = []
            for rr, cc in self.sequence[: self.index]:
                xx0, yy0, xx1, yy1 = self._cell_rect(rr, cc)
                points.extend(((xx0 + xx1) / 2.0, (yy0 + yy1) / 2.0))
            trail_width = max(1.4, min(3.6, self.cell_px * 0.16))
            trail_layers = (
                (trail_width * 2.3, "#9d8a5a"),
                (trail_width * 1.4, self.TRAIL),
                (trail_width * 0.55, "#f2e7c8"),
            )
            for width, color in trail_layers:
                self.trail_items.append(
                    self.canvas.create_line(
                        *points, fill=color, width=width,
                        capstyle="round", joinstyle="round",
                    )
                )

        if self.sequence and self.index > 0:
            r, c = self.sequence[self.index - 1]
        elif self.maze.root:
            r, c = self.maze.root
        else:
            self._delete_marker()
            return

        cx, cy = self._sprite_center(r, c)
        if (animate and self.current_kind in PATH_KINDS and self.sequence
                and self.index >= 2 and self._farmer_xy is not None):
            distance = math.hypot(cx - self._farmer_xy[0], cy - self._farmer_xy[1])
            if distance > 2.0:
                self._start_walk(self._farmer_xy, (cx, cy))
            else:
                self._place_farmer(cx, cy, animate=True)
        else:
            self._place_farmer(cx, cy, animate=animate)


    def _save_view_state(self):
        state = self.view_states[self.view_mode]
        state["zoom"] = self.zoom
        state["auto_fit"] = self.auto_fit
        state["base_cell"] = self.base_cell
        try:
            state["xview"] = self.canvas.xview()[0]
            state["yview"] = self.canvas.yview()[0]
        except tk.TclError:
            state["xview"] = state["yview"] = 0.0

    def _load_view_state(self):
        state = self.view_states[self.view_mode]
        self.zoom = state["zoom"]
        self.auto_fit = state["auto_fit"]
        self.base_cell = state["base_cell"]

    def _reset_view_states(self):
        for state in self.view_states.values():
            state.update(zoom=1.0, auto_fit=True, base_cell=4.0, xview=0.0, yview=0.0)
        self._load_view_state()

    def _switch_view(self, mode):
        if mode not in self.view_states or mode == self.view_mode:
            return
        self._save_view_state()
        self._cancel_farmer_anims()
        self.view_mode = mode
        self._load_view_state()
        self._update_view_buttons()
        self._update_zoom_label()
        self.redraw()
        state = self.view_states[mode]
        self.canvas.xview_moveto(state["xview"])
        self.canvas.yview_moveto(state["yview"])

    def _update_view_buttons(self):
        if not hasattr(self, "view_iso_btn"):
            return
        self.view_iso_btn.set_selected(self.view_mode == "iso")
        self.view_top_btn.set_selected(self.view_mode == "top")

    # ------------------------------------------------------------ navegación --
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
        self._cancel_farmer_anims()
        self._cancel_clipping()
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





