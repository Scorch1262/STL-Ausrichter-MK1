"""„Auf Fläche legen“ – nach dem Vorbild von Bambu Studio / PrusaSlicer.

Ablauf wie im Original (GLGizmoFlatten::update_planes):
1. Konvexe Hülle des Objekts bilden.
2. Benachbarte Hüllflächen mit (fast) gleicher Normale zu einer ebenen
   Kandidatenfläche zusammenfassen.
3. Polygon jeder Kandidatenfläche = 2D-Hülle ihrer Punkte in der Ebene.
4. Zu kleine Flächen und sehr spitze Splitter verwerfen, die größten
   254 Flächen behalten.

Erweiterung: Die angeklickte Fläche kann auf jede der drei Ebenen
XY / XZ / YZ gelegt werden (jeweils in beide Richtungen), optional mit
Ausrichtung der längsten Kante an einer Achse und mit „Folge-Ebene“
(die zweite Fläche dreht nur noch um die bereits festgelegte Achse).
"""

from __future__ import annotations

import math

import numpy as np

MAX_PLANES = 254
HULL_CHUNK = 1_000_000

# (Bezeichnung, Zielrichtung der Flächennormale, Ebene)
TARGETS = [
    ("XY – Fläche nach unten (−Z)", (0, 0, -1)),
    ("XY – Fläche nach oben (+Z)", (0, 0, 1)),
    ("XZ – Fläche nach vorne (−Y)", (0, -1, 0)),
    ("XZ – Fläche nach hinten (+Y)", (0, 1, 0)),
    ("YZ – Fläche nach links (−X)", (-1, 0, 0)),
    ("YZ – Fläche nach rechts (+X)", (1, 0, 0)),
]


def _hull(points):
    from scipy.spatial import ConvexHull
    pts = np.ascontiguousarray(points, dtype=np.float64)
    # doppelte Eckpunkte entfernen (jeder Punkt kommt im STL ca. 6x vor)
    pts = np.unique(pts.view(np.dtype((np.void, 24))).ravel()).view(np.float64).reshape(-1, 3)
    # Hülle der Hüllen: große Punktmengen blockweise vorreduzieren
    if len(pts) > 2 * HULL_CHUNK:
        keep = []
        for i in range(0, len(pts), HULL_CHUNK):
            sub = pts[i:i + HULL_CHUNK]
            if len(sub) >= 4:
                try:
                    h = ConvexHull(sub)
                    keep.append(sub[h.vertices])
                except Exception:
                    keep.append(sub)
            else:
                keep.append(sub)
        pts = np.concatenate(keep)
    return ConvexHull(pts), pts


def _hull2d(p2):
    from scipy.spatial import ConvexHull
    if len(p2) < 3:
        return None
    try:
        h = ConvexHull(p2)
    except Exception:
        return None
    return p2[h.vertices]  # gegen den Uhrzeigersinn


def _min_width(poly2):
    """Kleinste Breite eines konvexen Polygons (Messschieber-Verfahren)."""
    best = np.inf
    for i in range(len(poly2)):
        e = poly2[(i + 1) % len(poly2)] - poly2[i]
        L = np.hypot(*e)
        if L < 1e-300:
            continue
        nrm = np.array([-e[1], e[0]]) / L
        dist = (poly2 - poly2[i]) @ nrm
        best = min(best, float(np.max(np.abs(dist))))
    return best


def _plane_basis(n):
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    return u, v


def _bfs_seed(members, eq, neigh, areas, cos_tol, dist_tol):
    """Wie im Original: Gruppen wachsen von der größten Fläche aus, verglichen
    wird immer mit der Normale der Startfläche (kein „Abdriften“ über Rundungen)."""
    allowed = np.zeros(len(eq), dtype=bool)
    allowed[members] = True
    visited = ~allowed
    out = []
    for seed in members[np.argsort(-areas[members])]:
        if visited[seed]:
            continue
        n0, d0 = eq[seed, :3], eq[seed, 3]
        stack = [seed]
        visited[seed] = True
        group = []
        while stack:
            f = stack.pop()
            group.append(f)
            for g in neigh[f]:
                if g < 0 or visited[g]:
                    continue
                if np.dot(eq[g, :3], n0) >= cos_tol and abs(eq[g, 3] - d0) <= dist_tol:
                    visited[g] = True
                    stack.append(g)
        out.append(np.array(group))
    return out


def _group_facets(eq, neigh, areas, cos_tol, dist_tol, min_area):
    """Schnelle, weitgehend vektorisierte Gruppierung der Hüllflächen.

    1. Zusammenhangskomponenten über Nachbarn mit fast gleicher Normale.
    2. Ist die Komponente wirklich eben (alle Normalen innerhalb der
       Toleranz zur mittleren Normale) -> eine Fläche.
    3. Sonst (fein unterteilte Rundung) Startflächen-Verfahren wie im
       Original; bei sehr großen gekrümmten Bereichen Normalen-Raster."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    F = len(eq)
    i = np.repeat(np.arange(F), neigh.shape[1])
    j = neigh.ravel()
    m = (j >= 0) & (i < j)
    i, j = i[m], j[m]
    ok = (np.einsum("ij,ij->i", eq[i, :3], eq[j, :3]) >= cos_tol) & (np.abs(eq[i, 3] - eq[j, 3]) <= dist_tol)
    i, j = i[ok], j[ok]
    g = coo_matrix((np.ones(len(i), dtype=np.int8), (i, j)), shape=(F, F))
    _, lab = connected_components(g, directed=False)
    comp_area = np.bincount(lab, weights=areas)
    groups = []
    order = np.argsort(lab, kind="stable")
    bounds = np.searchsorted(lab[order], np.arange(len(comp_area) + 1))
    for c in np.nonzero(comp_area >= min_area)[0]:
        members = order[bounds[c]:bounds[c + 1]]
        if len(members) == 1:
            groups.append(members)
            continue
        nm = (eq[members, :3] * areas[members, None]).sum(0)
        nm /= np.linalg.norm(nm) + 1e-300
        if np.min(eq[members, :3] @ nm) >= cos_tol:
            groups.append(members)
        elif len(members) <= 200_000:
            groups.extend(_bfs_seed(members, eq, neigh, areas, cos_tol, dist_tol))
        else:
            step = max(math.acos(cos_tol), 1e-4)
            key = np.round(eq[members, :3] / step).astype(np.int64)
            _, inv = np.unique(key, axis=0, return_inverse=True)
            inv = inv.ravel()
            ba = np.bincount(inv, weights=areas[members])
            for b in np.nonzero(ba >= min_area)[0]:
                groups.append(members[inv == b])
    return groups


def compute_candidates(points, tol_deg=1.0, progress=None):
    """points: (N,3) Weltkoordinaten. Gibt Liste von Kandidaten zurück:
    dict(normal, offset, poly (K,3), area, center)."""
    if progress:
        progress(5, "Bilde konvexe Hülle …")
    hull, pts = _hull(points)
    eq = hull.equations            # (F,4): n·x + d = 0, n zeigt nach außen
    simp = hull.simplices
    neigh = hull.neighbors
    F = len(simp)
    tri = pts[simp]
    areas = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    ext = pts.max(0) - pts.min(0)
    size = float(np.linalg.norm(ext)) or 1.0
    bb_faces = sorted([ext[0] * ext[1], ext[0] * ext[2], ext[1] * ext[2]])
    min_area = max(0.01 * bb_faces[0], 1e-6 * size * size)
    cos_tol = math.cos(math.radians(tol_deg))
    dist_tol = 1e-3 * size

    if progress:
        progress(40, "Fasse ebene Bereiche zusammen …")
    groups = _group_facets(eq, neigh, areas, cos_tol, dist_tol, min_area)
    cands = []
    for group in groups:
        if areas[group].sum() < min_area:
            continue
        # gewichtete Normale der Gruppe
        n = (eq[group, :3] * areas[group, None]).sum(0)
        n /= np.linalg.norm(n)
        vidx = np.unique(simp[group].ravel())
        p = pts[vidx]
        off = float(np.max(p @ n))          # Ebene liegt außen an der Hülle
        u, v = _plane_basis(n)
        p2 = np.stack([p @ u, p @ v], axis=1)
        poly2 = _hull2d(p2)
        if poly2 is None:
            continue
        x, y = poly2[:, 0], poly2[:, 1]
        area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        if area < min_area:
            continue
        # schmale Streifen verwerfen (z. B. Facetten-Streifen eines Zylinders)
        if _min_width(poly2) < 0.01 * size:
            continue
        # sehr spitze Splitter verwerfen (Innenwinkel < 10°, wie im Original)
        if len(poly2) <= 4:
            ang_ok = True
            for i in range(len(poly2)):
                a = poly2[i - 1] - poly2[i]
                b = poly2[(i + 1) % len(poly2)] - poly2[i]
                c = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-30)
                if c > math.cos(math.radians(10)):
                    ang_ok = False
                    break
            if not ang_ok:
                continue
        poly3 = poly2[:, :1] * u + poly2[:, 1:2] * v + n * off
        cands.append(dict(normal=n, offset=off, poly=poly3, area=float(area),
                          center=poly3.mean(0)))
    cands.sort(key=lambda c: -c["area"])
    if progress:
        progress(100, f"{min(len(cands), MAX_PLANES)} ebene Flächen gefunden.")
    return cands[:MAX_PLANES]


def refine_normal(points, cand, size):
    """Verfeinert die Normale über alle Punkte, die auf der Fläche liegen
    (Ebenen-Ausgleich). Bei CAD-Dateien liegen die Eckpunkte exakt in der
    Ebene -> exakte Normale, auch wenn die Anzeige ausgedünnt ist."""
    n = cand["normal"]
    off = cand["offset"]
    tol = max(1e-5 * size, 1e-9)
    d = points @ n - off
    sel = points[np.abs(d) <= tol]
    if len(sel) < 3:
        return n
    if len(sel) > 200_000:
        sel = sel[:: len(sel) // 200_000]
    c = sel.mean(0)
    try:
        _, s, vt = np.linalg.svd(sel - c, full_matrices=False)
    except np.linalg.LinAlgError:
        return n
    if s[1] < 1e-12:  # alle Punkte auf einer Linie
        return n
    m = vt[2]
    if np.dot(m, n) < 0:
        m = -m
    if np.dot(m, n) < math.cos(math.radians(3)):  # Sicherheitsnetz
        return n
    return m / np.linalg.norm(m)


def _rot(axis, ang):
    from viewer import rot_axis
    return rot_axis(axis, math.degrees(ang))


def rotation_onto(n, d):
    """Kleinste Drehung, die n auf d dreht."""
    n = n / np.linalg.norm(n)
    d = np.asarray(d, float)
    c = float(np.clip(np.dot(n, d), -1, 1))
    axis = np.cross(n, d)
    s = np.linalg.norm(axis)
    if s < 1e-12:
        if c > 0:
            return np.eye(3)
        # 180°: beliebige senkrechte Achse
        a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
        axis = np.cross(n, a)
        return _rot(axis, math.pi)
    return _rot(axis / s, math.atan2(s, c))


def edge_alignment(poly, d):
    """Drehung um d, die die längste Kante des Polygons auf die nächstliegende
    Weltachse in der Zielebene dreht (kleinster Winkel)."""
    d = np.asarray(d, float)
    edges = np.roll(poly, -1, axis=0) - poly
    lens = np.linalg.norm(edges, axis=1)
    e = edges[int(np.argmax(lens))]
    e = e - np.dot(e, d) * d
    if np.linalg.norm(e) < 1e-12:
        return np.eye(3)
    e /= np.linalg.norm(e)
    axes = [np.eye(3)[i] for i in range(3) if abs(d[i]) < 0.5]
    best = None
    for a in axes:
        for sgn in (1, -1):
            t = sgn * a
            ang = math.atan2(np.dot(np.cross(e, t), d), np.dot(e, t))
            if best is None or abs(ang) < abs(best):
                best = ang
    return _rot(d, best)


def lay_rotation(n, poly, d, lock, align_edge):
    """Berechnet die Drehung für „Auf Fläche legen“.

    lock: Liste bereits festgelegter Zielrichtungen (Folge-Ebene) oder [].
    Gibt (R, neuer_lock, beschreibung) zurück."""
    d = np.asarray(d, float)
    if lock and len(lock) == 1:
        L = np.asarray(lock[0], float)
        if abs(np.dot(d, L)) < 1e-9 and abs(np.dot(n, L)) < math.sin(math.radians(20)):
            # nur um die bereits festgelegte Achse drehen -> erste Ausrichtung bleibt
            npj = n - np.dot(n, L) * L
            npj /= np.linalg.norm(npj)
            ang = math.atan2(np.dot(np.cross(npj, d), L), np.dot(npj, d))
            return _rot(L, ang), [tuple(L), tuple(d)], "Folge-Ebene (erste Ausrichtung beibehalten)"
    R = rotation_onto(n, d)
    if align_edge and poly is not None:
        P = (poly - poly.mean(0)) @ R.T
        R = edge_alignment(P, d) @ R
    return R, [tuple(d)], "Fläche gelegt"
