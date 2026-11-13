"""OpenGL-3D-Ansicht für STL-Ausrichter-MK1."""

from __future__ import annotations

import ctypes
import math

import numpy as np
from OpenGL import GL as gl
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget

# ---------------------------------------------------------------- Mathe
def rot_axis(axis, deg) -> np.ndarray:
    a = np.asarray(axis, dtype=np.float64)
    n = np.linalg.norm(a)
    if n == 0:
        return np.eye(3)
    x, y, z = a / n
    th = math.radians(deg)
    c, s, C = math.cos(th), math.sin(th), 1 - math.cos(th)
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def euler_to_R(rx, ry, rz) -> np.ndarray:
    """R = Rz · Ry · Rx (erst um X, dann Y, dann Z – feste Weltachsen)."""
    return rot_axis((0, 0, 1), rz) @ rot_axis((0, 1, 0), ry) @ rot_axis((1, 0, 0), rx)


def R_to_euler(R) -> tuple[float, float, float]:
    sy = -R[2, 0]
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(sy) < 0.999999:
        rx = math.atan2(R[2, 1], R[2, 2])
        rz = math.atan2(R[1, 0], R[0, 0])
    else:
        rx = 0.0
        rz = math.atan2(-R[0, 1], R[1, 1])
    return math.degrees(rx), math.degrees(ry), math.degrees(rz)


def affine(R=None, t=None) -> np.ndarray:
    M = np.eye(4)
    if R is not None:
        M[:3, :3] = R
    if t is not None:
        M[:3, 3] = t
    return M


def look_at(eye, target, up) -> np.ndarray:
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    M = np.eye(4)
    M[0, :3], M[1, :3], M[2, :3] = s, u, -f
    M[:3, 3] = -M[:3, :3] @ eye
    return M


def perspective(fovy, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy) / 2)
    M = np.zeros((4, 4))
    M[0, 0] = f / aspect
    M[1, 1] = f
    M[2, 2] = (far + near) / (near - far)
    M[2, 3] = 2 * far * near / (near - far)
    M[3, 2] = -1
    return M


def ortho(l, r, b, t, n, f):
    M = np.eye(4)
    M[0, 0] = 2 / (r - l)
    M[1, 1] = 2 / (t - b)
    M[2, 2] = -2 / (f - n)
    M[0, 3] = -(r + l) / (r - l)
    M[1, 3] = -(t + b) / (t - b)
    M[2, 3] = -(f + n) / (f - n)
    return M


# ---------------------------------------------------------------- Shader
MESH_VS = """
#version 120
attribute vec3 aPos;
attribute vec3 aNor;
uniform mat4 uMVP;
uniform mat3 uNrm;
varying vec3 vN;
void main(){
    vN = uNrm * aNor;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""
MESH_FS = """
#version 120
varying vec3 vN;
uniform vec3 uColor;
void main(){
    vec3 n = normalize(vN);
    float d = abs(n.z);                       // Kopflicht, beidseitig
    float k = abs(dot(n, normalize(vec3(-0.4, 0.6, 0.7))));
    float l = 0.22 + 0.58 * d + 0.25 * k;
    gl_FragColor = vec4(uColor * l, 1.0);
}
"""
FLAT_VS = """
#version 120
attribute vec3 aPos;
attribute vec4 aCol;
uniform mat4 uMVP;
varying vec4 vC;
void main(){
    vC = aCol;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""
FLAT_FS = """
#version 120
varying vec4 vC;
void main(){ gl_FragColor = vC; }
"""

C_X = (0.94, 0.33, 0.31)
C_Y = (0.30, 0.85, 0.45)
C_Z = (0.30, 0.56, 1.00)
C_AMBER = (0.96, 0.65, 0.14)


def _compile(vs, fs, attribs):
    def sh(src, kind):
        s = gl.glCreateShader(kind)
        gl.glShaderSource(s, src)
        gl.glCompileShader(s)
        if not gl.glGetShaderiv(s, gl.GL_COMPILE_STATUS):
            raise RuntimeError(gl.glGetShaderInfoLog(s).decode(errors="replace"))
        return s
    p = gl.glCreateProgram()
    gl.glAttachShader(p, sh(vs, gl.GL_VERTEX_SHADER))
    gl.glAttachShader(p, sh(fs, gl.GL_FRAGMENT_SHADER))
    for i, name in enumerate(attribs):
        gl.glBindAttribLocation(p, i, name)
    gl.glLinkProgram(p)
    if not gl.glGetProgramiv(p, gl.GL_LINK_STATUS):
        raise RuntimeError(gl.glGetProgramInfoLog(p).decode(errors="replace"))
    return p


class GLView(QOpenGLWidget):
    """3D-Ansicht. Linke Maustaste dreht das OBJEKT, rechte die KAMERA."""

    rotationChanged = Signal()      # ausstehende Drehung hat sich geändert
    interactionFinished = Signal()  # Maus losgelassen
    fileDropped = Signal(str)

    MAX_DRAW = 3_000_000  # Vertices pro Draw-Call

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(False)
        # Objektzustand -------------------------------------------------
        self.c0 = np.zeros(3)            # Mittelpunkt (Originaldaten)
        self.radius = 1.0
        self.R_total = np.eye(3)         # bereits übernommene Drehung
        self.t_total = np.zeros(3)
        self.R_view = np.eye(3)          # ausstehende (angezeigte) Drehung
        self.pivot_center = True
        # Kamera --------------------------------------------------------
        self.az, self.el = -55.0, 25.0
        self.dist = 3.0
        self.target = np.zeros(3)
        self.ortho = True
        self.fov = 35.0
        # Anzeigeoptionen ----------------------------------------------
        self.show_obj_planes = True
        self.show_obj_axes = True
        self.show_world = True
        self.mesh_color = (0.62, 0.72, 0.80)
        # GL ------------------------------------------------------------
        self._pending_upload = None
        self._mesh_vbo = None
        self._mesh_count = 0
        self._has_mesh = False
        self._gl_ok = False
        self._last = None
        self._btn = None
        self.message = "STL-Datei hier hineinziehen\noder über „Öffnen …“ laden"

    # ---------------------------------------------------- Daten
    def set_mesh(self, tris: np.ndarray, center: np.ndarray, radius: float):
        """tris: (N,3,3) float32 in Originalkoordinaten (Stichprobe)."""
        self.c0 = np.asarray(center, dtype=np.float64)
        self.radius = max(float(radius), 1e-9)
        local = (tris.astype(np.float64) - self.c0).astype(np.float32)
        e1 = local[:, 1] - local[:, 0]
        e2 = local[:, 2] - local[:, 0]
        n = np.cross(e1, e2)
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        ln[ln == 0] = 1
        n = (n / ln).astype(np.float32)
        inter = np.empty((len(local), 3, 6), dtype=np.float32)
        inter[:, :, :3] = local
        inter[:, :, 3:] = n[:, None, :]
        self._pending_upload = inter.reshape(-1, 6)
        self.R_total = np.eye(3)
        self.t_total = np.zeros(3)
        self.R_view = np.eye(3)
        self._has_mesh = True
        self.message = ""
        self.fit()
        self.update()

    def clear_mesh(self, message=""):
        self._has_mesh = False
        self._pending_upload = np.zeros((0, 6), np.float32)
        self.message = message
        self.update()

    def current_center(self) -> np.ndarray:
        return self.R_total @ self.c0 + self.t_total

    def pivot(self) -> np.ndarray:
        return self.current_center() if self.pivot_center else np.zeros(3)

    def view_affine(self) -> np.ndarray:
        p = self.pivot()
        return affine(t=p) @ affine(self.R_view) @ affine(t=-p)

    def total_affine(self) -> np.ndarray:
        return affine(self.R_total, self.t_total)

    def combined(self) -> tuple[np.ndarray, np.ndarray]:
        """Gesamttransformation inkl. ausstehender Drehung (R, t)."""
        M = self.view_affine() @ self.total_affine()
        return M[:3, :3].copy(), M[:3, 3].copy()

    def bake(self):
        """Ausstehende Drehung übernehmen: Objektachsen = Weltachsen."""
        M = self.view_affine() @ self.total_affine()
        from stl_io import clean_rotation
        self.R_total = clean_rotation(M[:3, :3])
        self.t_total = M[:3, 3].copy()
        self.R_view = np.eye(3)
        self.target = self.current_center()
        self.rotationChanged.emit()
        self.update()

    def reset_pending(self):
        self.R_view = np.eye(3)
        self.rotationChanged.emit()
        self.update()

    def reset_all(self):
        self.R_view = np.eye(3)
        self.R_total = np.eye(3)
        self.t_total = np.zeros(3)
        self.target = self.current_center()
        self.rotationChanged.emit()
        self.update()

    def set_pending(self, R):
        from stl_io import clean_rotation
        self.R_view = clean_rotation(R)
        self.rotationChanged.emit()
        self.update()

    def rotate_pending(self, axis, deg):
        self.set_pending(rot_axis(axis, deg) @ self.R_view)

    # ---------------------------------------------------- Kamera
    def cam_basis(self):
        az, el = math.radians(self.az), math.radians(self.el)
        back = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
        up = np.array([-math.sin(el) * math.cos(az), -math.sin(el) * math.sin(az), math.cos(el)])
        right = np.cross(up, back)
        return right, up, back

    def set_view(self, name):
        presets = {"iso": (-55, 25), "vorne": (-90, 0), "hinten": (90, 0), "rechts": (0, 0),
                   "links": (180, 0), "oben": (-90, 90), "unten": (-90, -90)}
        self.az, self.el = presets[name]
        self.update()

    def fit(self):
        self.target = self.current_center()
        self.dist = self.radius / math.sin(math.radians(self.fov / 2)) * 1.15
        self.update()

    def matrices(self):
        w, h = max(1, self.width()), max(1, self.height())
        _, up, back = self.cam_basis()
        eye = self.target + back * self.dist
        V = look_at(eye, self.target.copy(), up)
        near = max(self.dist - self.radius * 20, self.dist * 0.001)
        far = self.dist + self.radius * 20
        if self.ortho:
            hh = self.dist * math.tan(math.radians(self.fov / 2))
            P = ortho(-hh * w / h, hh * w / h, -hh, hh, -far, far)
        else:
            P = perspective(self.fov, w / h, near, far)
        return P, V

    # ---------------------------------------------------- GL
    def initializeGL(self):
        try:
            self.p_mesh = _compile(MESH_VS, MESH_FS, ["aPos", "aNor"])
            self.p_flat = _compile(FLAT_VS, FLAT_FS, ["aPos", "aCol"])
            self._mesh_vbo = gl.glGenBuffers(1)
            self._flat_vbo = gl.glGenBuffers(1)
            self._gl_ok = True
        except Exception as e:  # pragma: no cover
            self._gl_ok = False
            self.message = f"OpenGL-Fehler:\n{e}"
        gl.glClearColor(0.035, 0.05, 0.07, 1.0)

    def _upload(self):
        data = self._pending_upload
        self._pending_upload = None
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._mesh_vbo)
        if len(data):
            gl.glBufferData(gl.GL_ARRAY_BUFFER, data.nbytes, data, gl.GL_STATIC_DRAW)
        else:
            gl.glBufferData(gl.GL_ARRAY_BUFFER, 4, None, gl.GL_STATIC_DRAW)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        self._mesh_count = len(data)

    def _draw_flat(self, verts, cols, mode, mvp):
        if not len(verts):
            return
        data = np.hstack([np.asarray(verts, np.float32), np.asarray(cols, np.float32)])
        data = np.ascontiguousarray(data, dtype=np.float32)
        gl.glUseProgram(self.p_flat)
        gl.glUniformMatrix4fv(gl.glGetUniformLocation(self.p_flat, "uMVP"), 1, gl.GL_TRUE,
                              mvp.astype(np.float32))
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._flat_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, data.nbytes, data, gl.GL_STREAM_DRAW)
        gl.glEnableVertexAttribArray(0)
        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 28, ctypes.c_void_p(0))
        gl.glVertexAttribPointer(1, 4, gl.GL_FLOAT, False, 28, ctypes.c_void_p(12))
        gl.glDrawArrays(mode, 0, len(data))
        gl.glDisableVertexAttribArray(0)
        gl.glDisableVertexAttribArray(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)

    def paintGL(self):
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        if not self._gl_ok:
            self._paint_overlay(None, None)
            return
        if self._pending_upload is not None:
            self._upload()
        P, V = self.matrices()
        PV = P @ V
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glDepthFunc(gl.GL_LEQUAL)
        gl.glDisable(gl.GL_CULL_FACE)

        p = self.pivot()
        s = self.radius * 1.15

        # --- Welt-Referenz (fest) -------------------------------------
        if self._has_mesh and self.show_world:
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            verts, cols = [], []
            step = _nice_step(s * 2 / 10)
            n = int(math.ceil(s / step))
            g = (0.45, 0.55, 0.65, 0.16)
            for i in range(-n, n + 1):
                verts += [(i * step, -n * step, 0), (i * step, n * step, 0),
                          (-n * step, i * step, 0), (n * step, i * step, 0)]
                cols += [g] * 4
            ext = n * step
            gray = (0.55, 0.62, 0.70, 0.35)
            for a, b in (((-ext, 0, -ext), (ext, 0, -ext)), ((ext, 0, -ext), (ext, 0, ext)),
                         ((ext, 0, ext), (-ext, 0, ext)), ((-ext, 0, ext), (-ext, 0, -ext)),
                         ((0, -ext, -ext), (0, ext, -ext)), ((0, ext, -ext), (0, ext, ext)),
                         ((0, ext, ext), (0, -ext, ext)), ((0, -ext, ext), (0, -ext, -ext))):
                verts += [a, b]
                cols += [gray, gray]
            M = affine(t=p)
            gl.glDepthMask(gl.GL_FALSE)
            self._draw_flat(verts, cols, gl.GL_LINES, PV @ M)
            gl.glDepthMask(gl.GL_TRUE)
            gl.glDisable(gl.GL_BLEND)

        # --- Mesh ------------------------------------------------------
        if self._has_mesh and self._mesh_count:
            M = self.view_affine() @ self.total_affine() @ affine(t=self.c0)
            MV = V @ M
            gl.glUseProgram(self.p_mesh)
            gl.glUniformMatrix4fv(gl.glGetUniformLocation(self.p_mesh, "uMVP"), 1, gl.GL_TRUE,
                                  (P @ MV).astype(np.float32))
            gl.glUniformMatrix3fv(gl.glGetUniformLocation(self.p_mesh, "uNrm"), 1, gl.GL_TRUE,
                                  MV[:3, :3].astype(np.float32))
            gl.glUniform3f(gl.glGetUniformLocation(self.p_mesh, "uColor"), *self.mesh_color)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._mesh_vbo)
            gl.glEnableVertexAttribArray(0)
            gl.glEnableVertexAttribArray(1)
            gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(1.0, 1.0)
            for start in range(0, self._mesh_count, self.MAX_DRAW):
                cnt = min(self.MAX_DRAW, self._mesh_count - start)
                off = start * 24
                gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 24, ctypes.c_void_p(off))
                gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, False, 24, ctypes.c_void_p(off + 12))
                gl.glDrawArrays(gl.GL_TRIANGLES, 0, cnt)
            gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
            gl.glDisableVertexAttribArray(0)
            gl.glDisableVertexAttribArray(1)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)

        # --- Objektebenen / -achsen (drehen mit) -----------------------
        if self._has_mesh and (self.show_obj_planes or self.show_obj_axes):
            M = affine(t=p) @ affine(self.R_view)
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            if self.show_obj_planes:
                tv, tc, lv, lc = [], [], [], []
                planes = [  # (Ebene, Farbe, Ecken)
                    ("XY", C_Z, [(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)]),
                    ("XZ", C_Y, [(-s, 0, -s), (s, 0, -s), (s, 0, s), (-s, 0, s)]),
                    ("YZ", C_X, [(0, -s, -s), (0, s, -s), (0, s, s), (0, -s, s)]),
                ]
                for _, c, q in planes:
                    tv += [q[0], q[1], q[2], q[0], q[2], q[3]]
                    tc += [(*c, 0.10)] * 6
                    for i in range(4):
                        lv += [q[i], q[(i + 1) % 4]]
                        lc += [(*c, 0.75)] * 2
                gl.glDepthMask(gl.GL_FALSE)
                self._draw_flat(tv, tc, gl.GL_TRIANGLES, PV @ M)
                gl.glDepthMask(gl.GL_TRUE)
                self._draw_flat(lv, lc, gl.GL_LINES, PV @ M)
            if self.show_obj_axes:
                L = s * 1.25
                av = [(0, 0, 0), (L, 0, 0), (0, 0, 0), (0, L, 0), (0, 0, 0), (0, 0, L)]
                ac = [(*C_X, 1)] * 2 + [(*C_Y, 1)] * 2 + [(*C_Z, 1)] * 2
                gl.glDisable(gl.GL_DEPTH_TEST)
                gl.glLineWidth(2.5)
                self._draw_flat(av, ac, gl.GL_LINES, PV @ M)
                gl.glLineWidth(1.0)
                gl.glEnable(gl.GL_DEPTH_TEST)
            gl.glDisable(gl.GL_BLEND)

        # --- Achsenkreuz unten links (Welt) ---------------------------
        dpr = self.devicePixelRatioF()
        size = 110
        gl.glViewport(0, 0, int(size * dpr), int(size * dpr))
        gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        Vr = np.eye(4)
        Vr[:3, :3] = V[:3, :3]
        Pt = ortho(-1.4, 1.4, -1.4, 1.4, -5, 5)
        gl.glLineWidth(2.5)
        self._draw_flat([(0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0), (0, 0, 0), (0, 0, 1)],
                        [(*C_X, 1)] * 2 + [(*C_Y, 1)] * 2 + [(*C_Z, 1)] * 2,
                        gl.GL_LINES, Pt @ Vr)
        gl.glLineWidth(1.0)
        gl.glViewport(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        gl.glUseProgram(0)
        gl.glDisable(gl.GL_DEPTH_TEST)

        self._paint_overlay(PV, Vr)

    def _paint_overlay(self, PV, Vr):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if self.message:
            qp.setPen(QColor(125, 138, 153))
            f = QFont("Segoe UI", 13)
            qp.setFont(f)
            qp.drawText(0, 0, w, h, Qt.AlignCenter, self.message)
        if PV is not None:
            f = QFont("Consolas", 10)
            f.setBold(True)
            qp.setFont(f)
            # Beschriftung Achsenkreuz
            size = 110
            for vec, name, col in (((1.2, 0, 0), "X", C_X), ((0, 1.2, 0), "Y", C_Y), ((0, 0, 1.2), "Z", C_Z)):
                q = Vr[:3, :3] @ np.array(vec)
                sx = size / 2 + q[0] / 1.4 * size / 2
                sy = h - (size / 2 + q[1] / 1.4 * size / 2)
                qp.setPen(QColor.fromRgbF(*col))
                qp.drawText(QPointF(sx - 4, sy + 4), name)
            # Beschriftung Objektachsen
            if self._has_mesh and self.show_obj_axes:
                p = self.pivot()
                L = self.radius * 1.15 * 1.32
                for i, (name, col) in enumerate((("X", C_X), ("Y", C_Y), ("Z", C_Z))):
                    v = np.zeros(3)
                    v[i] = L
                    wp = p + self.R_view @ v
                    c = PV @ np.array([*wp, 1.0])
                    if c[3] <= 0:
                        continue
                    nx, ny = c[0] / c[3], c[1] / c[3]
                    qp.setPen(QColor.fromRgbF(*col))
                    qp.drawText(QPointF((nx + 1) / 2 * w - 4, (1 - ny) / 2 * h + 4), name)
            # Beschriftung Objektebenen
            if self._has_mesh and self.show_obj_planes:
                p = self.pivot()
                s = self.radius * 1.15
                f2 = QFont("Consolas", 9)
                qp.setFont(f2)
                for corner, name, col in (((s, s, 0), "XY", C_Z), ((s, 0, s), "XZ", C_Y), ((0, s, s), "YZ", C_X)):
                    wp = p + self.R_view @ np.array(corner, dtype=float)
                    c = PV @ np.array([*wp, 1.0])
                    if c[3] <= 0:
                        continue
                    nx, ny = c[0] / c[3], c[1] / c[3]
                    qp.setPen(QColor.fromRgbF(*col, 0.85))
                    qp.drawText(QPointF((nx + 1) / 2 * w + 4, (1 - ny) / 2 * h - 4), name)
            # Hinweis Steuerung
            qp.setPen(QColor(90, 104, 120))
            qp.setFont(QFont("Segoe UI", 8))
            qp.drawText(0, 0, w - 10, h - 6, Qt.AlignRight | Qt.AlignBottom,
                        "Links ziehen: Objekt drehen  ·  Strg+Links: um Blickachse  ·  "
                        "Rechts: Ansicht drehen  ·  Mitte/Umschalt+Rechts: verschieben  ·  Rad: Zoom")
        qp.end()

    # ---------------------------------------------------- Maus
    def mousePressEvent(self, e):
        self._last = e.position()
        self._btn = e.button()

    def mouseReleaseEvent(self, e):
        if self._btn == Qt.LeftButton:
            self.interactionFinished.emit()
        self._last = None
        self._btn = None

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        pos = e.position()
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        self._last = pos
        mods = e.modifiers()
        right, up, back = self.cam_basis()
        if self._btn == Qt.LeftButton and self._has_mesh:
            if mods & Qt.ControlModifier:
                R = rot_axis(back, -dx * 0.4)
            else:
                R = rot_axis(up, dx * 0.4) @ rot_axis(right, dy * 0.4)
            self.R_view = R @ self.R_view
            self.rotationChanged.emit()
        elif self._btn == Qt.MiddleButton or (self._btn == Qt.RightButton and mods & Qt.ShiftModifier):
            hh = self.dist * math.tan(math.radians(self.fov / 2))
            k = 2 * hh / max(1, self.height())
            self.target = self.target - right * dx * k + up * dy * k
        elif self._btn == Qt.RightButton:
            self.az -= dx * 0.4
            self.el = max(-90.0, min(90.0, self.el + dy * 0.4))
        self.update()

    def wheelEvent(self, e):
        d = e.angleDelta().y() / 120.0
        self.dist *= 0.87 ** d
        self.dist = max(self.radius * 0.02, min(self.radius * 200, self.dist))
        self.update()

    # ---------------------------------------------------- Drag & Drop
    def dragEnterEvent(self, e):
        if _stl_from_mime(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        p = _stl_from_mime(e.mimeData())
        if p:
            self.fileDropped.emit(p)


def _stl_from_mime(md):
    if md.hasUrls():
        for u in md.urls():
            if u.isLocalFile() and u.toLocalFile().lower().endswith(".stl"):
                return u.toLocalFile()
    return None


def _nice_step(x):
    if x <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 5, 10):
        if x <= m * e:
            return m * e
    return 10 * e
