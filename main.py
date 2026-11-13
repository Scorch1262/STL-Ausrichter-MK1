"""STL-Ausrichter-MK1 – STL laden, frei drehen, Achsen ausrichten, verlustfrei speichern."""

from __future__ import annotations

import os
import sys

# In der fensterlosen exe gibt es keine Konsole (sys.stdout/stderr = None).
# Bibliotheken, die etwas ausgeben (traceback.print_exc …), würden sonst abstürzen.
if sys.stdout is None or sys.stderr is None:
    _null = open(os.devnull, "w")
    sys.stdout = sys.stdout or _null
    sys.stderr = sys.stderr or _null

import multiprocessing
import queue as queue_module
import shutil
import tempfile
import time

import numpy as np
from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QSurfaceFormat
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

import stl_io
from version import APP_NAME, VERSION
import jobs
import layflat
from viewer import GLView, R_to_euler, euler_to_R


def resource(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


STYLE = """
* { font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 10pt; color: #d6dee8; }
QMainWindow, QWidget#root { background: #0b0f14; }
QWidget#side { background: #0b0f14; }
QScrollArea { border: none; background: #0b0f14; }
QFrame#card { background: #111821; border: 1px solid #1e2a37; border-radius: 10px; }
QLabel#cardTitle { color: #7d8a99; font-size: 8pt; font-weight: 600; letter-spacing: 2px; }
QLabel#muted { color: #7d8a99; }
QLabel#value { font-family: Consolas, 'Cascadia Mono', monospace; color: #e6edf5; }
QLabel#warn { color: #f5a524; font-weight: 600; }
QWidget#header { background: #0e141b; border-bottom: 1px solid #1e2a37; }
QLabel#title { font-size: 13pt; font-weight: 700; letter-spacing: 3px; color: #e6edf5; }
QLabel#ver { color: #2dd4bf; font-family: Consolas, monospace; }
QLabel#dot { color: #f5a524; font-size: 14pt; }
QPushButton {
    background: #16202b; border: 1px solid #263445; border-radius: 7px;
    padding: 6px 10px; color: #d6dee8;
}
QPushButton:hover { border-color: #2dd4bf; background: #1a2733; }
QPushButton:pressed { background: #0f171f; }
QPushButton:disabled { color: #4b5866; border-color: #1b2531; }
QPushButton#primary {
    background: #f5a524; color: #111; border: 1px solid #f5a524; font-weight: 700; padding: 9px;
}
QPushButton#primary:hover { background: #ffb840; }
QPushButton#primary:disabled { background: #3a3220; color: #7a6a45; border-color: #3a3220; }
QPushButton#lay { border-color: #f5a524; color: #f5a524; font-weight: 700; padding: 8px; }
QPushButton#lay:hover { background: #2a2210; }
QPushButton#lay:checked { background: #f5a524; color: #111; }
QPushButton#teal { border-color: #2dd4bf; color: #2dd4bf; font-weight: 600; }
QPushButton#teal:hover { background: #12302c; }
QPushButton#small { padding: 4px 6px; font-family: Consolas, monospace; }
QSlider::groove:horizontal { height: 4px; background: #263445; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #2dd4bf; border-radius: 2px; }
QSlider::handle:horizontal { background: #f5a524; width: 14px; margin: -6px 0; border-radius: 7px; }
QLabel#hint { color: #7d8a99; font-size: 8.5pt; }
QDoubleSpinBox, QSpinBox, QComboBox {
    background: #0b1117; border: 1px solid #263445; border-radius: 6px; padding: 4px 6px;
    font-family: Consolas, monospace; selection-background-color: #2dd4bf; selection-color: #000;
}
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus { border-color: #2dd4bf; }
QComboBox QAbstractItemView { background: #111821; border: 1px solid #263445; outline: 0;
    selection-background-color: #1d3a37; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px; border: 1px solid #33465a;
    background: #0b1117; }
QCheckBox::indicator:checked { background: #2dd4bf; border-color: #2dd4bf; }
QProgressBar { background: #0b1117; border: 1px solid #1e2a37; border-radius: 6px; height: 14px;
    text-align: center; color: #d6dee8; font-family: Consolas, monospace; font-size: 8pt; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #2dd4bf, stop:1 #f5a524);
    border-radius: 5px; }
QWidget#status { background: #0e141b; border-top: 1px solid #1e2a37; }
QMenuBar { background: #0e141b; border-bottom: 1px solid #1e2a37; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: #1a2733; }
QMenu { background: #111821; border: 1px solid #263445; padding: 4px; }
QMenu::item { padding: 5px 22px; border-radius: 4px; }
QMenu::item:selected { background: #1d3a37; }
QMessageBox { background: #111821; }
QToolTip { background: #111821; color: #d6dee8; border: 1px solid #2dd4bf; }
QScrollBar:vertical { background: #0b0f14; width: 8px; }
QScrollBar::handle:vertical { background: #263445; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}".replace(",", ".") if unit == "B" else f"{n:.2f} {unit}".replace(".", ",")
        n /= 1024


def fmt_num(x, d=3):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_int(n):
    return f"{n:,}".replace(",", ".")


# ------------------------------------------------------------------ Worker
class Worker(QThread):
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            res = self.fn(*self.args, progress=lambda p, t: self.progress.emit(p, t),
                          cancel=lambda: self._cancel)
            self.done.emit(res)
        except stl_io.Abgebrochen:
            self.failed.emit("__abgebrochen__")
        except Exception as e:  # noqa
            self.failed.emit(f"{type(e).__name__}: {e}")


class ProcessRunner(QThread):
    """Startet einen Auftrag aus jobs.py in einem eigenen Prozess."""
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, target, *args):
        super().__init__()
        self.target, self.args = target, args
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=self.target, args=(*self.args, q))
        p.start()
        finished = False
        try:
            while True:
                if self._cancel:
                    _kill_tree(p)
                    self.failed.emit("__abgebrochen__")
                    return
                try:
                    item = q.get(timeout=0.3)
                except queue_module.Empty:
                    if not p.is_alive():
                        break
                    continue
                if item[0] == "progress":
                    self.progress.emit(int(item[1]), str(item[2]))
                elif item[0] == "done":
                    finished = True
                    self.done.emit(item[1])
                    break
                elif item[0] == "error":
                    finished = True
                    self.failed.emit(item[1])
                    break
        finally:
            p.join(timeout=5)
            if p.is_alive():
                _kill_tree(p)
        if not finished:
            self.failed.emit("Der Vorgang wurde unerwartet beendet (vermutlich nicht genug "
                             "Arbeitsspeicher). Bitte stärker vereinfachen und erneut versuchen.")


def _kill_tree(p):
    try:
        import psutil
        for c in psutil.Process(p.pid).children(recursive=True):
            c.kill()
    except Exception:
        pass
    p.terminate()
    p.join(timeout=5)


# ------------------------------------------------------------------ UI-Helfer
def card(title):
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(7)
    t = QLabel(title.upper())
    t.setObjectName("cardTitle")
    lay.addWidget(t)
    return f, lay


def kv(grid, row, key, obj_name="value"):
    k = QLabel(key)
    k.setObjectName("muted")
    v = QLabel("–")
    v.setObjectName(obj_name)
    v.setTextInteractionFlags(Qt.TextSelectableByMouse)
    grid.addWidget(k, row, 0)
    grid.addWidget(v, row, 1)
    return v


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{VERSION}")
        self.setWindowIcon(QIcon(resource("assets/icon.ico")))
        self.resize(1400, 880)
        self.setAcceptDrops(True)
        self.info: stl_io.StlInfo | None = None
        self.worker: Worker | None = None
        self._t0 = 0.0
        self._updating = False

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header ------------------------------------------------------
        header = QWidget()
        header.setObjectName("header")
        header.setFixedHeight(48)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        dot = QLabel("●")
        dot.setObjectName("dot")
        title = QLabel("STL-AUSRICHTER")
        title.setObjectName("title")
        ver = QLabel(f"MK1 · v{VERSION}")
        ver.setObjectName("ver")
        self.lbl_file = QLabel("keine Datei geladen")
        self.lbl_file.setObjectName("muted")
        hl.addWidget(dot)
        hl.addWidget(title)
        hl.addSpacing(8)
        hl.addWidget(ver)
        hl.addStretch(1)
        hl.addWidget(self.lbl_file)
        outer.addWidget(header)

        # Hauptbereich ----------------------------------------------------
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        side_w = QWidget()
        side_w.setObjectName("side")
        side = QVBoxLayout(side_w)
        side.setContentsMargins(12, 12, 8, 12)
        side.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side_w)
        scroll.setFixedWidth(372)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.addWidget(scroll)

        view_wrap = QFrame()
        view_wrap.setObjectName("card")
        vw = QVBoxLayout(view_wrap)
        vw.setContentsMargins(1, 1, 1, 1)
        self.view = GLView()
        self.view.setAcceptDrops(True)
        vw.addWidget(self.view)
        vwrap_outer = QVBoxLayout()
        vwrap_outer.setContentsMargins(4, 12, 12, 12)
        vwrap_outer.addWidget(view_wrap)
        body.addLayout(vwrap_outer, 1)

        # Karte: Datei ------------------------------------------------------
        c, l = card("Datei")
        row = QHBoxLayout()
        self.btn_open = QPushButton("Öffnen …")
        self.btn_save = QPushButton("Speichern unter …")
        self.btn_save.setObjectName("teal")
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_save)
        l.addLayout(row)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(3)
        self.v_name = kv(g, 0, "Name")
        self.v_fmt = kv(g, 1, "Format")
        self.v_size = kv(g, 2, "Größe")
        self.v_tris = kv(g, 3, "Dreiecke")
        self.v_disp = kv(g, 4, "Angezeigt")
        l.addLayout(g)
        dq = QHBoxLayout()
        lab = QLabel("Anzeige-Detail")
        lab.setObjectName("muted")
        self.cmb_detail = QComboBox()
        for txt, val in (("1 Mio. Dreiecke", 1_000_000), ("3 Mio. Dreiecke", 3_000_000),
                         ("6 Mio. Dreiecke", 6_000_000), ("10 Mio. Dreiecke", 10_000_000)):
            self.cmb_detail.addItem(txt, val)
        self.cmb_detail.setCurrentIndex(1)
        self.cmb_detail.setToolTip("Nur die Darstellung wird ausgedünnt –\n"
                                   "beim Speichern werden immer ALLE Dreiecke verarbeitet.")
        dq.addWidget(lab)
        dq.addWidget(self.cmb_detail, 1)
        l.addLayout(dq)
        side.addWidget(c)

        # Karte: Drehung --------------------------------------------------
        c, l = card("Drehung (ausstehend)")
        hint = QLabel("Objekt mit der linken Maustaste so hindrehen, wie es liegen soll, dann "
                      "„Achsen ausrichten“: Eine Achse zeigt danach genau zum Betrachter, die "
                      "beiden anderen verlaufen parallel zum Fensterrand.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        l.addWidget(hint)
        g = QGridLayout()
        g.setHorizontalSpacing(5)
        g.setVerticalSpacing(5)
        self.spins = []
        for i, (ax, col) in enumerate((("X", "#f0544f"), ("Y", "#4dd973"), ("Z", "#4d8fff"))):
            la = QLabel(ax)
            la.setStyleSheet(f"color:{col}; font-weight:700; font-family:Consolas;")
            sp = QDoubleSpinBox()
            sp.setRange(-360.0, 360.0)
            sp.setDecimals(2)
            sp.setSingleStep(0.5)
            sp.setSuffix(" °")
            sp.setWrapping(True)
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(self.on_spin)
            self.spins.append(sp)
            g.addWidget(la, i, 0)
            g.addWidget(sp, i, 1)
            axis = [0, 0, 0]
            axis[i] = 1
            for j, deg in enumerate((-90, -1, 1, 90)):
                b = QPushButton(f"{deg:+d}")
                b.setObjectName("small")
                b.setFixedWidth(40)
                b.clicked.connect(lambda _=False, a=tuple(axis), d=deg: (self._unlock(), self.view.rotate_pending(a, d)))
                g.addWidget(b, i, 2 + j)
        l.addLayout(g)
        row = QHBoxLayout()
        self.btn_reset_pending = QPushButton("Drehung verwerfen")
        row.addWidget(self.btn_reset_pending)
        l.addLayout(row)
        piv = QHBoxLayout()
        lab = QLabel("Drehpunkt")
        lab.setObjectName("muted")
        self.cmb_pivot = QComboBox()
        self.cmb_pivot.addItem("Objektmitte (Lage bleibt)", True)
        self.cmb_pivot.addItem("Ursprung 0/0/0", False)
        piv.addWidget(lab)
        piv.addWidget(self.cmb_pivot, 1)
        l.addLayout(piv)
        self.btn_bake = QPushButton("ACHSEN AUSRICHTEN")
        self.btn_bake.setObjectName("primary")
        self.btn_bake.setToolTip("Richtet die Achsen an der aktuellen Ansicht aus: Das Objekt bleibt so\n"
                                 "liegen, wie es angezeigt wird. Eine Achse zeigt danach exakt zum\n"
                                 "Betrachter, die beiden anderen verlaufen parallel zum Fensterrand.")
        l.addWidget(self.btn_bake)
        self.lbl_pending = QLabel("")
        self.lbl_pending.setObjectName("warn")
        self.lbl_pending.setWordWrap(True)
        l.addWidget(self.lbl_pending)
        side.addWidget(c)

        # Karte: Auf Fläche legen -------------------------------------------
        c, l = card("Auf Fläche legen")
        hint = QLabel("Zeigt die ebenen Flächen des Körpers (wie „Auf Fläche legen“ in "
                      "Bambu Studio). Angeklickte Fläche wird auf die gewählte Ebene gelegt.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        l.addWidget(hint)
        self.btn_lay = QPushButton("FLÄCHEN ANZEIGEN  (F)")
        self.btn_lay.setObjectName("lay")
        self.btn_lay.setCheckable(True)
        l.addWidget(self.btn_lay)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        lab = QLabel("Zielebene")
        lab.setObjectName("muted")
        self.cmb_target = QComboBox()
        for txt, d in layflat.TARGETS:
            self.cmb_target.addItem(txt, d)
        g.addWidget(lab, 0, 0)
        g.addWidget(self.cmb_target, 0, 1)
        lab = QLabel("Ursprung")
        lab.setObjectName("muted")
        self.cmb_origin = QComboBox()
        self.cmb_origin.addItem("Mitte des Körpers (Box)", "box")
        self.cmb_origin.addItem("Volumenschwerpunkt", "centroid")
        self.cmb_origin.addItem("nicht verschieben", "none")
        self.cmb_origin.setToolTip("Wohin der Ursprung 0/0/0 nach dem Legen gesetzt wird.\n"
                                   "Wird exakt über ALLE Dreiecke der Datei berechnet.")
        g.addWidget(lab, 1, 0)
        g.addWidget(self.cmb_origin, 1, 1)
        lab = QLabel("Ebenheit")
        lab.setObjectName("muted")
        self.spn_flat = QDoubleSpinBox()
        self.spn_flat.setRange(0.1, 10.0)
        self.spn_flat.setDecimals(1)
        self.spn_flat.setSingleStep(0.5)
        self.spn_flat.setValue(1.0)
        self.spn_flat.setSuffix(" °")
        self.spn_flat.setToolTip("Wie stark Teilflächen voneinander abweichen dürfen, um noch als\n"
                                 "eine ebene Fläche zu gelten (größer = toleranter, z. B. für Scans).")
        g.addWidget(lab, 2, 0)
        g.addWidget(self.spn_flat, 2, 1)
        l.addLayout(g)
        self.chk_edge = QCheckBox("Längste Kante an Achse ausrichten")
        self.chk_edge.setChecked(True)
        self.chk_edge.setToolTip("Dreht zusätzlich in der Ebene, bis die längste Kante der Fläche\n"
                                 "parallel zur nächstliegenden Achse liegt.")
        self.chk_follow = QCheckBox("Folge-Ebene: vorherige Lage beibehalten")
        self.chk_follow.setChecked(True)
        self.chk_follow.setToolTip("Wird nach dem ersten Legen eine seitliche Fläche auf eine andere\n"
                                   "Ebene gelegt, dreht das Objekt nur noch um die bereits\n"
                                   "festgelegte Achse – die erste Fläche bleibt liegen.")
        l.addWidget(self.chk_edge)
        l.addWidget(self.chk_follow)
        self.btn_center = QPushButton("Ursprung jetzt in Körpermitte setzen")
        l.addWidget(self.btn_center)
        self.lbl_lay = QLabel("")
        self.lbl_lay.setObjectName("hint")
        self.lbl_lay.setWordWrap(True)
        l.addWidget(self.lbl_lay)
        side.addWidget(c)
        self.lay_lock = []

        # Karte: Gesamtänderung ------------------------------------------
        c, l = card("Übernommene Änderung")
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(3)
        self.v_rot = kv(g, 0, "Drehung")
        self.v_shift = kv(g, 1, "Versatz")
        self.v_dims = kv(g, 2, "Maße ≈")
        self.v_bbox0 = kv(g, 3, "Original")
        l.addLayout(g)
        self.btn_reset_all = QPushButton("Alles zurücksetzen")
        l.addWidget(self.btn_reset_all)
        side.addWidget(c)

        # Karte: STEP / Vereinfachen (optional) --------------------------
        c, l = card("Optional: STEP & Vereinfachen")
        hint = QLabel("Die Ausrichtung („Speichern unter …“) bleibt immer verlustfrei. "
                      "Diese Funktionen erzeugen zusätzlich eine STEP-Datei bzw. eine "
                      "vereinfachte STL – jeweils in der aktuellen Ausrichtung.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        l.addWidget(hint)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        lab = QLabel("Vereinfachung")
        lab.setObjectName("muted")
        self.sld_dec = QSlider(Qt.Horizontal)
        self.sld_dec.setRange(1, 100)
        self.sld_dec.setValue(100)
        self.lbl_dec = QLabel("100 %")
        self.lbl_dec.setObjectName("value")
        self.lbl_dec.setFixedWidth(46)
        self.sld_dec.valueChanged.connect(lambda v: self.lbl_dec.setText(f"{v} %"))
        self.sld_dec.setToolTip("Ziel-Anzahl der Dreiecke in Prozent des Originals (100 % = keine Vereinfachung)")
        g.addWidget(lab, 0, 0)
        g.addWidget(self.sld_dec, 0, 1)
        g.addWidget(self.lbl_dec, 0, 2)
        lab = QLabel("Glättung")
        lab.setObjectName("muted")
        self.spn_smooth = QSpinBox()
        self.spn_smooth.setRange(0, 10)
        self.spn_smooth.setSuffix(" ×")
        self.spn_smooth.setToolTip("Taubin-Glättung (Rauschen entfernen), 0 = aus")
        g.addWidget(lab, 1, 0)
        g.addWidget(self.spn_smooth, 1, 1, 1, 2)
        l.addLayout(g)
        self.chk_merge = QCheckBox("STEP: ebene Flächen zusammenführen")
        self.chk_detect = QCheckBox("STEP: Zylinder/Kugeln ersetzen")
        self.chk_nurbs = QCheckBox("STEP: Scan-Oberfläche glätten (NURBS)")
        self.chk_merge.setChecked(True)
        self.chk_detect.setChecked(True)
        for cb in (self.chk_merge, self.chk_detect, self.chk_nurbs):
            l.addWidget(cb)
        self.btn_step = QPushButton("Als STEP (.stp) speichern …")
        self.btn_step.setObjectName("teal")
        self.btn_simplify = QPushButton("Vereinfacht als STL speichern …")
        l.addWidget(self.btn_step)
        l.addWidget(self.btn_simplify)
        side.addWidget(c)

        # Karte: Ansicht --------------------------------------------------
        c, l = card("Ansicht")
        g = QGridLayout()
        g.setSpacing(5)
        views = [("Iso", "iso"), ("Vorne", "vorne"), ("Oben", "oben"), ("Rechts", "rechts"),
                 ("Hinten", "hinten"), ("Unten", "unten"), ("Links", "links")]
        for i, (txt, key) in enumerate(views):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, k=key: self.view.set_view(k))
            g.addWidget(b, i // 4, i % 4)
        bfit = QPushButton("Einpassen")
        bfit.clicked.connect(self.view.fit)
        g.addWidget(bfit, 1, 3)
        l.addLayout(g)
        self.chk_planes = QCheckBox("Objekt-Ebenen XY / XZ / YZ")
        self.chk_axes = QCheckBox("Objekt-Achsen X / Y / Z")
        self.chk_world = QCheckBox("Welt-Referenz (fest)")
        self.chk_ortho = QCheckBox("Orthografische Ansicht")
        for cb in (self.chk_planes, self.chk_axes, self.chk_world, self.chk_ortho):
            cb.setChecked(True)
            cb.toggled.connect(self.on_display_opts)
            l.addWidget(cb)
        side.addWidget(c)
        side.addStretch(1)

        # Statusleiste --------------------------------------------------
        status = QWidget()
        status.setObjectName("status")
        status.setFixedHeight(34)
        sl = QHBoxLayout(status)
        sl.setContentsMargins(14, 4, 14, 4)
        self.lbl_status = QLabel("Bereit.")
        self.lbl_status.setObjectName("muted")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(320)
        self.progress.setVisible(False)
        self.btn_cancel = QPushButton("Abbrechen")
        self.btn_cancel.setVisible(False)
        sl.addWidget(self.lbl_status, 1)
        sl.addWidget(self.progress)
        sl.addWidget(self.btn_cancel)
        outer.addWidget(status)

        # Menü ------------------------------------------------------------
        m = self.menuBar().addMenu("&Datei")
        a = QAction("Öffnen …", self, shortcut=QKeySequence.Open, triggered=self.open_dialog)
        m.addAction(a)
        a = QAction("Speichern unter …", self, shortcut=QKeySequence("Ctrl+Shift+S"), triggered=self.save_dialog)
        m.addAction(a)
        m.addAction(QAction("Als STEP speichern …", self, triggered=self.step_dialog))
        m.addAction(QAction("Vereinfacht als STL speichern …", self, triggered=self.simplify_dialog))
        m.addSeparator()
        m.addAction(QAction("Beenden", self, shortcut=QKeySequence.Quit, triggered=self.close))
        m = self.menuBar().addMenu("&Bearbeiten")
        m.addAction(QAction("Achsen ausrichten", self, shortcut=QKeySequence("Ctrl+Return"), triggered=self.bake))
        m.addAction(QAction("Drehung verwerfen / Flächenmodus beenden", self, shortcut=QKeySequence("Esc"),
                            triggered=self.on_escape))
        m.addAction(QAction("Auf Fläche legen", self, shortcut=QKeySequence("F"),
                            triggered=lambda: self.btn_lay.isEnabled() and self.btn_lay.click()))
        m.addAction(QAction("Ursprung in Körpermitte setzen", self, triggered=self.center_origin))
        m = self.menuBar().addMenu("&Hilfe")
        m.addAction(QAction("Über …", self, triggered=self.about))

        # Signale -----------------------------------------------------
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_save.clicked.connect(self.save_dialog)
        self.btn_bake.clicked.connect(self.bake)
        self.btn_reset_pending.clicked.connect(self.view.reset_pending)
        self.btn_reset_all.clicked.connect(self.reset_all)
        self.btn_cancel.clicked.connect(self.cancel_worker)
        self.btn_step.clicked.connect(self.step_dialog)
        self.btn_simplify.clicked.connect(self.simplify_dialog)
        self.cmb_pivot.currentIndexChanged.connect(self.on_pivot)
        self.view.rotationChanged.connect(self.on_rotation_changed)
        self.view.interactionFinished.connect(self.update_dims)
        self.view.interactionFinished.connect(self._unlock)
        self.view.layFaceClicked.connect(self.on_lay_face)
        self.btn_lay.toggled.connect(self.on_lay_toggled)
        self.btn_center.clicked.connect(self.center_origin)
        self.view.fileDropped.connect(self.load)
        self._dims_timer = QTimer(self, singleShot=True, interval=150, timeout=self.update_dims)
        self.set_enabled(False)

    # ------------------------------------------------------------ Status
    def set_enabled(self, has_file):
        busy = self.worker is not None
        lay = self.view.lay_mode
        for w in (self.btn_save, self.btn_reset_all, self.btn_step, self.btn_simplify,
                  self.btn_lay, self.btn_center, self.cmb_target, self.cmb_origin):
            w.setEnabled(has_file and not busy)
        for w in (self.btn_bake, self.btn_reset_pending, self.cmb_pivot, self.spn_flat, *self.spins,
                  *self.findChildren(QPushButton, "small")):
            w.setEnabled(has_file and not busy and not lay)
        self.btn_open.setEnabled(not busy)
        self.cmb_detail.setEnabled(not busy)

    def busy(self, on, text=""):
        self.progress.setVisible(on)
        self.btn_cancel.setVisible(on)
        self.progress.setValue(0)
        if text:
            self.lbl_status.setText(text)

    def on_progress(self, pct, text):
        self.progress.setValue(pct)
        el = time.time() - self._t0
        eta = ""
        if 3 < pct < 100 and el > 2:
            rest = el * (100 - pct) / pct
            eta = f"  ·  noch ca. {int(rest // 60)}:{int(rest % 60):02d} min"
        self.lbl_status.setText(text + eta)

    def cancel_worker(self):
        if self.worker:
            self.worker.cancel()
            self.lbl_status.setText("Breche ab …")

    # ------------------------------------------------------------ Laden
    def open_dialog(self):
        if self.worker:
            return
        start = os.path.dirname(self.info.path) if self.info else ""
        p, _ = QFileDialog.getOpenFileName(self, "STL-Datei öffnen", start, "STL-Dateien (*.stl *.STL);;Alle Dateien (*)")
        if p:
            self.load(p)

    def load(self, path):
        if self.worker:
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, APP_NAME, f"Datei nicht gefunden:\n{path}")
            return
        if self.info and self.has_changes():
            r = QMessageBox.question(self, APP_NAME, "Es gibt nicht gespeicherte Änderungen.\nTrotzdem neue Datei laden?")
            if r != QMessageBox.Yes:
                return
        self.btn_lay.setChecked(False)
        self.lay_lock = []
        self._t0 = time.time()
        self.busy(True, "Lade …")
        self.view.clear_mesh("Lade …")
        self.worker = Worker(stl_io.load_stl, path, int(self.cmb_detail.currentData()))
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_loaded)
        self.worker.failed.connect(self.on_load_failed)
        self.set_enabled(False)
        self.worker.start()

    def on_loaded(self, info: stl_io.StlInfo):
        self._finish_worker()
        self.info = info
        radius = float(np.linalg.norm(info.bbox_max - info.bbox_min) / 2) or 1.0
        self.view.pivot_center = bool(self.cmb_pivot.currentData())
        self.view.set_mesh(info.sample, info.center, radius)
        self.view.set_view("iso")
        name = os.path.basename(info.path)
        self.lbl_file.setText(info.path)
        self.v_name.setText(name if len(name) < 34 else name[:31] + "…")
        self.v_name.setToolTip(info.path)
        self.v_fmt.setText("Binär" if info.fmt == "binary" else "ASCII")
        self.v_size.setText(f"{fmt_bytes(info.file_size)}")
        self.v_tris.setText(fmt_int(info.tri_count))
        self.v_disp.setText(f"{fmt_int(len(info.sample))}" + (f"  (jedes {info.stride}.)" if info.stride > 1 else "  (alle)"))
        d = info.bbox_max - info.bbox_min
        self.v_bbox0.setText(" × ".join(fmt_num(x, 2) for x in d))
        self.set_enabled(True)
        self.on_rotation_changed()
        self.update_dims()
        self.lbl_status.setText(f"Geladen in {time.time() - self._t0:.1f} s: {info.path}")

    def on_load_failed(self, msg):
        self._finish_worker()
        self.info = None
        self.view.clear_mesh("STL-Datei hier hineinziehen\noder über „Öffnen …“ laden")
        self.set_enabled(False)
        if msg == "__abgebrochen__":
            self.lbl_status.setText("Laden abgebrochen.")
        else:
            self.lbl_status.setText("Fehler beim Laden.")
            QMessageBox.critical(self, APP_NAME, f"Datei konnte nicht geladen werden:\n\n{msg}")

    def _finish_worker(self):
        if self.worker:
            self.worker.wait()
        self.worker = None
        self.busy(False)

    # ------------------------------------------------------------ Drehung
    def has_pending(self):
        return not stl_io.is_identity(self.view.R_view, eps=1e-12)

    def has_changes(self):
        return self.has_pending() or not stl_io.is_identity(self.view.R_total, self.view.t_total, eps=1e-12)

    def on_spin(self):
        if self._updating:
            return
        rx, ry, rz = (s.value() for s in self.spins)
        self._unlock()
        self.view.set_pending(euler_to_R(rx, ry, rz))
        self._dims_timer.start()

    def on_rotation_changed(self):
        rx, ry, rz = R_to_euler(self.view.R_view)
        self._updating = True
        for s, v in zip(self.spins, (rx, ry, rz)):
            v = round(v, 6) + 0.0
            s.setValue(v)
        self._updating = False
        self.lbl_pending.setText("⚠ Ausstehende Drehung – mit „Achsen ausrichten“ übernehmen."
                                 if self.has_pending() else "")
        ax, ay, az = (round(v, 4) + 0.0 for v in R_to_euler(self.view.R_total))
        self.v_rot.setText(f"X {fmt_num(ax, 2)}°  Y {fmt_num(ay, 2)}°  Z {fmt_num(az, 2)}°")
        t = self.view.t_total
        self.v_shift.setText("  ".join(fmt_num(x, 3) for x in t))
        if self.view._last is None:
            self._dims_timer.start()

    def update_dims(self):
        if not self.info or self.info.sample is None or not len(self.info.sample):
            return
        s = self.info.sample.reshape(-1, 3)
        if len(s) > 300_000:
            s = s[:: len(s) // 300_000]
        R, t = self.view.combined()
        p = s.astype(np.float64) @ R.T + t
        d = p.max(0) - p.min(0)
        self.v_dims.setText(" × ".join(fmt_num(x, 2) for x in d))

    def bake(self):
        """ACHSEN AUSRICHTEN: Achsen an der aktuellen Ansicht ausrichten."""
        if not self.info:
            return
        self._unlock()
        rr, uu, bb = self.view.align_to_view()
        self.update_dims()
        self.lbl_status.setText(f"Achsen an der Ansicht ausgerichtet: {bb} zeigt zum Betrachter, "
                                f"{rr} nach rechts, {uu} nach oben. Mit „Speichern unter …“ sichern.")

    def reset_all(self):
        self.btn_lay.setChecked(False)
        self._unlock()
        self.view.reset_all()
        self.update_dims()

    # ------------------------------------------------------------ Auf Fläche legen
    def _unlock(self):
        self.lay_lock = []

    def on_escape(self):
        if self.view.lay_mode or self.btn_lay.isChecked():
            self.btn_lay.setChecked(False)
        else:
            self.view.reset_pending()

    def _world_points(self, max_pts=None):
        p = self.info.sample.reshape(-1, 3)
        if max_pts and len(p) > max_pts:
            p = p[:: len(p) // max_pts]
        return p.astype(np.float64) @ self.view.R_total.T + self.view.t_total

    def on_lay_toggled(self, on):
        if not on:
            if self.worker and getattr(self, "_lay_computing", False):
                self.worker.cancel()
            self.view.set_lay_mode(False)
            self.lbl_lay.setText("")
            self.set_enabled(self.info is not None)
            return
        if not self.info or self.worker:
            self.btn_lay.setChecked(False)
            return
        if self.has_pending():
            self.view.bake()   # angezeigte Drehung übernehmen, Flächen gelten für die aktuelle Lage
        pts = self._world_points()
        self._t0 = time.time()
        self._lay_computing = True
        self.busy(True, "Suche ebene Flächen …")
        tol = self.spn_flat.value()
        self.worker = Worker(lambda pts, progress=None, cancel=None:
                             layflat.compute_candidates(pts, tol, progress), pts)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_lay_ready)
        self.worker.failed.connect(self.on_lay_failed)
        self.set_enabled(True)
        self.worker.start()

    def on_lay_ready(self, cands):
        self._lay_computing = False
        self._finish_worker()
        if not self.btn_lay.isChecked():
            self.set_enabled(True)
            return
        self.view.set_lay_mode(True, [(c["poly"], c["normal"]) for c in cands])
        self.set_enabled(True)
        self.lbl_status.setText(f"{len(cands)} ebene Flächen gefunden – Fläche im 3D-Fenster anklicken.")
        self.lbl_lay.setText("Fläche anklicken. Mehrfach möglich: z. B. erst Boden auf XY, "
                             "dann eine Seitenfläche auf XZ.")

    def on_lay_failed(self, msg):
        self._lay_computing = False
        self._finish_worker()
        self.btn_lay.setChecked(False)
        self.set_enabled(True)
        if msg != "__abgebrochen__":
            QMessageBox.critical(self, APP_NAME, f"Flächen konnten nicht ermittelt werden:\n\n{msg}")

    def on_lay_face(self, idx):
        if self.worker or idx < 0 or idx >= len(self.view.lay_faces):
            return
        poly, n = self.view.lay_faces[idx]
        size = 2 * self.view.radius
        n_ref = layflat.refine_normal(self._world_points(), {"normal": n, "offset": float(poly[0] @ n)}, size)
        d = np.array(self.cmb_target.currentData(), dtype=float)
        lock = self.lay_lock if self.chk_follow.isChecked() else []
        R, new_lock, desc = layflat.lay_rotation(n_ref, poly, d, lock, self.chk_edge.isChecked())
        A_old = self.view.total_affine()
        self.view.set_pending(R)
        self.view.bake()
        A_new = self.view.total_affine()
        self.view.transform_lay(A_new @ np.linalg.inv(A_old))
        self.lay_lock = new_lock
        self.update_dims()
        plane = self.cmb_target.currentText().split(" ")[0]
        self.lbl_lay.setText(f"✔ {desc}: Fläche auf {plane}." + (
            "  Nächste Fläche auf eine andere Ebene legen, um auch die übrigen Achsen festzulegen."
            if len(new_lock) == 1 else "  Alle drei Achsen sind festgelegt."))
        if self.cmb_origin.currentData() != "none":
            self.center_origin()
        else:
            self.lbl_status.setText("Fläche gelegt.")

    def center_origin(self):
        if not self.info or self.worker:
            return
        if self.has_pending():
            self.view.bake()
        mode = self.cmb_origin.currentData()
        if mode == "none":
            mode = "box"
        self._center_mode = mode
        self._t0 = time.time()
        self.busy(True, "Ermittle exakte Maße …")
        self.worker = Worker(stl_io.compute_bounds, self.info.path,
                             self.view.R_total.copy(), self.view.t_total.copy())
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_center_done)
        self.worker.failed.connect(self.on_center_failed)
        self.set_enabled(True)
        self.worker.start()

    def on_center_done(self, b):
        self._finish_worker()
        c = b["center"]
        note = "Mitte der Begrenzungsbox"
        if self._center_mode == "centroid":
            if b["centroid"] is not None and np.all(np.isfinite(b["centroid"])):
                c = b["centroid"]
                note = "Volumenschwerpunkt"
            else:
                note = "Mitte der Begrenzungsbox (Körper nicht geschlossen – kein Schwerpunkt)"
        c = np.asarray(c, dtype=float)
        self.view.t_total = self.view.t_total - c
        A = np.eye(4)
        A[:3, 3] = -c
        self.view.transform_lay(A)
        self.view.target = self.view.target - c
        self.view.update()
        self.set_enabled(True)
        self.on_rotation_changed()
        ext = b["max"] - b["min"]
        self.v_dims.setText(" × ".join(fmt_num(x, 2) for x in ext))
        self.lbl_status.setText(f"Ursprung gesetzt ({note}). Exakte Maße: "
                                + " × ".join(fmt_num(x, 3) for x in ext))

    def on_center_failed(self, msg):
        self._finish_worker()
        self.set_enabled(True)
        if msg != "__abgebrochen__":
            QMessageBox.critical(self, APP_NAME, f"Maße konnten nicht ermittelt werden:\n\n{msg}")

    def on_pivot(self):
        self.view.pivot_center = bool(self.cmb_pivot.currentData())
        self.view.update()

    def on_display_opts(self):
        self.view.show_obj_planes = self.chk_planes.isChecked()
        self.view.show_obj_axes = self.chk_axes.isChecked()
        self.view.show_world = self.chk_world.isChecked()
        self.view.ortho = self.chk_ortho.isChecked()
        self.view.update()

    # ------------------------------------------------------------ Speichern
    def ask_pending(self) -> bool:
        """Fragt nach, ob eine noch nicht übernommene Drehung einbezogen werden soll.
        Gibt False zurück, wenn abgebrochen wurde."""
        if self.has_pending():
            r = QMessageBox.question(
                self, APP_NAME,
                "Die aktuell angezeigte Drehung wurde noch nicht mit „Achsen ausrichten“ übernommen.\n\n"
                "Jetzt übernehmen und einbeziehen?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if r == QMessageBox.Cancel:
                return False
            if r == QMessageBox.Yes:
                self.view.bake()
                self.update_dims()
        return True

    def save_dialog(self):
        if not self.info or self.worker:
            return
        if not self.ask_pending():
            return
        if stl_io.is_identity(self.view.R_total, self.view.t_total, eps=0):
            r = QMessageBox.question(self, APP_NAME, "Es wurde keine Änderung vorgenommen.\nTrotzdem speichern (1:1-Kopie)?")
            if r != QMessageBox.Yes:
                return
        base, ext = os.path.splitext(self.info.path)
        suggestion = base + "_ausgerichtet" + (ext or ".stl")
        p, _ = QFileDialog.getSaveFileName(self, "Ausgerichtete STL speichern", suggestion, "STL-Dateien (*.stl)")
        if not p:
            return
        if not p.lower().endswith(".stl"):
            p += ".stl"
        try:
            free = _free_space(os.path.dirname(os.path.abspath(p)))
            if free is not None and free < self.info.file_size * 1.02:
                QMessageBox.critical(self, APP_NAME, f"Nicht genug freier Speicher am Ziel.\nBenötigt: "
                                     f"{fmt_bytes(self.info.file_size)}, frei: {fmt_bytes(free)}")
                return
        except OSError:
            pass
        self._save_target = p
        R, t = self.view.R_total.copy(), self.view.t_total.copy()
        self._t0 = time.time()
        self.busy(True, "Speichere …")
        self.worker = Worker(stl_io.save_transformed, self.info.path, p, R, t)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_saved)
        self.worker.failed.connect(self.on_save_failed)
        self.set_enabled(False)
        self.worker.start()

    def on_saved(self, _):
        self._finish_worker()
        p = self._save_target
        same = os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(self.info.path))
        msg = (f"Gespeichert in {time.time() - self._t0:.1f} s:\n{p}\n\n"
               f"Größe: {fmt_bytes(os.path.getsize(p))} ({fmt_int(os.path.getsize(p))} Bytes)")
        self.set_enabled(True)
        self.lbl_status.setText("Gespeichert: " + p)
        QMessageBox.information(self, APP_NAME, msg)
        if same:
            # Quelldatei wurde überschrieben -> neu laden, damit Ausgangslage stimmt
            self.view.reset_all()
            self.load(p)

    def on_save_failed(self, msg):
        self._finish_worker()
        self.set_enabled(True)
        if msg == "__abgebrochen__":
            self.lbl_status.setText("Speichern abgebrochen – Zieldatei wurde nicht verändert.")
        else:
            self.lbl_status.setText("Fehler beim Speichern.")
            QMessageBox.critical(self, APP_NAME, f"Speichern fehlgeschlagen:\n\n{msg}")

    # ------------------------------------------------------------ STEP / Vereinfachen
    def _conv_settings(self, decimate):
        return dict(smoothing_iterations=self.spn_smooth.value(),
                    decimate_percent=decimate,
                    merge_planar=self.chk_merge.isChecked(),
                    detect_curved_shapes=self.chk_detect.isChecked(),
                    smooth_scan_surfaces=self.chk_nurbs.isChecked(),
                    worker_count=0)

    def _start_job(self, target, dst, decimate, title):
        self._workdir = tempfile.mkdtemp(prefix="stl_ausrichter_")
        self._job_dst = dst
        R, t = self.view.R_total.copy(), self.view.t_total.copy()
        self._t0 = time.time()
        self.busy(True, title)
        self.worker = ProcessRunner(target, self.info.path, dst, R, t,
                                    self._conv_settings(decimate), self._workdir)
        self.worker.progress.connect(self.on_progress)
        self.set_enabled(False)
        return self.worker

    def _job_cleanup(self, failed):
        shutil.rmtree(getattr(self, "_workdir", ""), ignore_errors=True)
        if failed:
            for p in (self._job_dst + ".teil", self._job_dst + ".teil.stl"):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

    def step_dialog(self):
        if not self.info or self.worker or not self.ask_pending():
            return
        base = os.path.splitext(self.info.path)[0]
        p, _ = QFileDialog.getSaveFileName(self, "Als STEP speichern", base + ".stp",
                                           "STEP-Dateien (*.stp *.step)")
        if not p:
            return
        if not p.lower().endswith((".stp", ".step")):
            p += ".stp"
        dec = self.sld_dec.value()
        w = self._start_job(jobs.step_job, p, dec, "Wandle in STEP um …")
        w.done.connect(self.on_step_done)
        w.failed.connect(lambda m: self.on_job_failed(m, "STEP-Umwandlung"))
        w.start()

    def simplify_dialog(self):
        if not self.info or self.worker or not self.ask_pending():
            return
        if self.sld_dec.value() >= 100 and self.spn_smooth.value() == 0:
            QMessageBox.information(self, APP_NAME, "Vereinfachung steht auf 100 % und Glättung auf 0 –\n"
                                    "bitte zuerst einen kleineren Prozentwert einstellen.")
            return
        base = os.path.splitext(self.info.path)[0]
        p, _ = QFileDialog.getSaveFileName(self, "Vereinfachte STL speichern",
                                           f"{base}_vereinfacht_{self.sld_dec.value()}.stl", "STL-Dateien (*.stl)")
        if not p:
            return
        if not p.lower().endswith(".stl"):
            p += ".stl"
        if os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(self.info.path)):
            QMessageBox.warning(self, APP_NAME, "Die Originaldatei kann hier nicht überschrieben werden.\n"
                                "Bitte einen anderen Dateinamen wählen.")
            return
        w = self._start_job(jobs.simplify_job, p, self.sld_dec.value(), "Vereinfache …")
        w.done.connect(self.on_simplify_done)
        w.failed.connect(lambda m: self.on_job_failed(m, "Vereinfachung"))
        w.start()

    def on_step_done(self, d):
        self._finish_worker()
        self._job_cleanup(False)
        self.set_enabled(True)
        txt = (f"STEP gespeichert in {time.time() - self._t0:.0f} s:\n{d['path']}\n\n"
               f"Volumenkörper: {'ja' if d['is_solid'] else 'nein (offene Fläche)'}\n"
               f"Dreiecke: {fmt_int(d['faces_before'])} → STEP-Flächen: {fmt_int(d['faces_after'])}")
        if d.get("volume"):
            txt += f"\nVolumen: {fmt_num(d['volume'], 2)}"
        rep = [s for s in d.get("shapes", []) if s[2]]
        if rep:
            txt += f"\nErsetzte Zylinder/Kugeln: {len(rep)}"
        self.lbl_status.setText("STEP gespeichert: " + d["path"])
        QMessageBox.information(self, APP_NAME, txt)

    def on_simplify_done(self, d):
        self._finish_worker()
        self._job_cleanup(False)
        self.set_enabled(True)
        self.lbl_status.setText("Vereinfachte STL gespeichert: " + d["path"])
        r = QMessageBox.question(
            self, APP_NAME,
            f"Vereinfachte STL gespeichert in {time.time() - self._t0:.0f} s:\n{d['path']}\n\n"
            f"Dreiecke: {fmt_int(d['faces_before'])} → {fmt_int(d['faces_after'])}\n"
            f"Größe: {fmt_bytes(os.path.getsize(d['path']))}\n\nVereinfachte Datei jetzt öffnen?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            self.view.reset_all()
            self.load(d["path"])

    def on_job_failed(self, msg, what):
        self._finish_worker()
        self._job_cleanup(True)
        self.set_enabled(True)
        if msg == "__abgebrochen__":
            self.lbl_status.setText(f"{what} abgebrochen.")
        else:
            self.lbl_status.setText(f"{what} fehlgeschlagen.")
            QMessageBox.critical(self, APP_NAME, f"{what} fehlgeschlagen:\n\n{msg}")

    # ------------------------------------------------------------ Sonstiges
    def about(self):
        QMessageBox.about(self, APP_NAME,
                          f"<b>{APP_NAME}</b> v{VERSION}<br><br>"
                          "STL-Dateien frei drehen, X/Y/Z-Achsen gerade ausrichten und<br>"
                          "strukturgleich speichern (Binär: bytegleiche Dateigröße,<br>"
                          "Header, Reihenfolge und Attribute bleiben erhalten).<br><br>"
                          "Optional: Umwandlung in STEP (Flächenrückführung aus<br>"
                          "STL-STEP-Konverter-MK1) und Vereinfachen als STL.")

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() and any(u.toLocalFile().lower().endswith(".stl") for u in md.urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(".stl"):
                self.load(u.toLocalFile())
                break

    def closeEvent(self, e):
        if self.worker:
            r = QMessageBox.question(self, APP_NAME, "Ein Vorgang läuft noch. Abbrechen und beenden?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.cancel()
            self.worker.wait()
        elif self.info and self.has_changes():
            r = QMessageBox.question(self, APP_NAME, "Es gibt nicht gespeicherte Änderungen.\nTrotzdem beenden?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        e.accept()


def _free_space(path):
    import shutil
    return shutil.disk_usage(path).free


def main():
    multiprocessing.freeze_support()
    fmt = QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon(resource("assets/icon.ico")))
    w = MainWindow()
    w.show()
    # Start über Explorer ("Öffnen mit" / Doppelklick / Datei auf die exe ziehen)
    args = [a for a in sys.argv[1:] if a.lower().endswith(".stl")]
    if args:
        QTimer.singleShot(200, lambda: w.load(os.path.abspath(args[0])))
    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
