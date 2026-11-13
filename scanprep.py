"""Scan-Aufbereitung vor der STEP-Umwandlung (STL-Ausrichter-MK1).

3D-Scans unterscheiden sich stark von CAD-Exporten:
* viele lose Fragmente / „Schwebeteilchen“ (Staub, Reflexionen, Tischreste)
  -> wurden bisher alle als eigene Körper umgewandelt (z. B. 482 Körper),
* Löcher, nicht-mannigfaltige Kanten, Selbstüberschneidungen,
* Rauschen: eigentlich ebene Flächen bestehen aus Tausenden leicht
  verkippten Dreiecken -> im STEP wird jedes Dreieck eine eigene Fläche,
* sehr viele Dreiecke (oft Millionen).

Ablauf:
 1. Netz bereinigen (doppelte/entartete Dreiecke)
 2. Fragmente entfernen (nur größter Körper bzw. kleine Teile verwerfen)
 3. Auf eine sinnvolle Dreieckszahl vereinfachen (Detailstufe)
 4. Reparieren: Löcher schließen, mannigfaltig + wasserdicht (MeshFix)
 5. Optional glätten (Taubin, volumenerhaltend)
 6. Ebene Bereiche erkennen und „begradigen“: alle Punkte einer im Rahmen
    der Scan-Toleranz ebenen Fläche werden exakt in die ausgeglichene Ebene
    gelegt, Punkte an Kanten zwischen zwei/drei Ebenen exakt auf die
    Schnittkante. Dadurch fasst die STEP-Umwandlung jede solche Fläche zu
    EINER echten ebenen CAD-Fläche zusammen.
"""

from __future__ import annotations

import math

import numpy as np


def _cb(cb, p, m):
    if cb:
        cb(int(p), m)


def prepare_scan(inp, out, *, fragments="largest", max_faces=100_000, repair=True,
                 smoothing=0, flatten=True, cylinders=True, tolerance=0.0, cb=None) -> dict:
    """Bereitet einen Scan auf und schreibt das Ergebnis als STL nach out.

    fragments: "largest" | "small" | "all"
    max_faces: Ziel-Dreiecksanzahl (0 = nicht vereinfachen)
    tolerance: Scan-Toleranz in Modelleinheiten (0 = automatisch)
    Gibt eine Statistik (dict) zurück.
    """
    import trimesh
    stats = {}
    _cb(cb, 1, "Lese Scan ein …")
    mesh = trimesh.load(inp, force="mesh", process=True)
    stats["faces_in"] = len(mesh.faces)

    _cb(cb, 6, "Bereinige Netz …")
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()

    # --- 2. Fragmente --------------------------------------------------------
    _cb(cb, 10, "Suche lose Fragmente …")
    parts = mesh.split(only_watertight=False)
    stats["bodies_in"] = max(1, len(parts))
    if len(parts) > 1 and fragments != "all":
        areas = np.array([p.area for p in parts])
        if fragments == "largest":
            keep = [parts[int(np.argmax(areas))]]
        else:  # "small": Teile < 5 % der größten Fläche verwerfen
            keep = [p for p, a in zip(parts, areas) if a >= 0.05 * areas.max()]
        mesh = trimesh.util.concatenate(keep) if len(keep) > 1 else keep[0]
        stats["bodies_removed"] = len(parts) - len(keep)
    else:
        stats["bodies_removed"] = 0
    stats["bodies_out"] = stats["bodies_in"] - stats["bodies_removed"]

    # --- 3. Detailstufe -------------------------------------------------------
    if max_faces and len(mesh.faces) > max_faces:
        _cb(cb, 20, f"Vereinfache {len(mesh.faces):,} → {max_faces:,} Dreiecke …".replace(",", "."))
        mesh = mesh.simplify_quadric_decimation(face_count=int(max_faces))

    # --- 4. Reparatur ---------------------------------------------------------
    if repair:
        mesh, info = _meshfix(mesh, cb, 35, keep_all=(fragments != "largest"))
        stats.update(info)

    # --- 5. Glättung ----------------------------------------------------------
    if smoothing > 0:
        _cb(cb, 50, f"Glätte Scan-Rauschen ({smoothing}×) …")
        trimesh.smoothing.filter_taubin(mesh, iterations=int(smoothing))

    # --- 6. Ebenen begradigen ---------------------------------------------------
    diag = float(np.linalg.norm(mesh.extents)) or 1.0
    tol = tolerance if tolerance and tolerance > 0 else _auto_tolerance(mesh, diag)
    stats["tolerance"] = tol
    cyl_fits = []
    if cylinders:
        # Rundungen ZUERST erkennen, damit deren Facetten nicht als schmale
        # „Ebenen-Streifen“ begradigt werden
        _cb(cb, 56, "Erkenne Bohrungen, Zapfen und Rundungen …")
        e = np.zeros((0, 3))
        cyl_fits = snap_cylinders(mesh, e, np.zeros(0), tol, cb, detect_only=True)
    if flatten:
        _cb(cb, 60, "Erkenne ebene Bereiche …")
        excl = np.unique(np.concatenate([f[3] for f in cyl_fits])) if cyl_fits else None
        n_planes, moved = flatten_planes(mesh, tol, cb, exclude=excl)
        stats["planes"] = n_planes
        stats["plane_area_share"] = round(getattr(flatten_planes, "last_coverage", 0.0), 3)
        stats["vertices_snapped"] = moved
        if n_planes:
            # Umgeklappte/entartete Dreiecke in den Ebenen entfernen und die
            # Lücken neu schließen (sonst ungültige, sich selbst überschneidende
            # STEP-Flächen)
            N, D = flatten_planes.last_planes
            # durch das Begradigen zusammengefallene Punkte vereinen
            if cyl_fits:
                stats["cylinders"] = [round(r, 4) for r in snap_cylinders(mesh, N, D, tol, cb, given=cyl_fits)]
            mesh.merge_vertices()
            mesh.update_faces(mesh.nondegenerate_faces())
            mesh.remove_unreferenced_vertices()
            if cyl_fits:
                if stats["cylinders"]:
                    # Selbstüberschneidungen nach dem Einpassen sicher entfernen
                    mesh, _ = _meshfix(mesh, cb, 87, keep_all=(fragments != "largest"), force=True)
            removed = 0
            for _round in range(8):
                stats["spikes_snapped"] = stats.get("spikes_snapped", 0) + snap_enclosed(mesh, N, D)
                bad = find_folds(mesh, N, D)
                if not len(bad):
                    break
                _cb(cb, 88, f"Entferne {len(bad)} umgeklappte Dreiecke …")
                if __import__("os").environ.get("STLA_DEBUG"):
                    print("fold round", _round, len(bad), flush=True)
                vt = np.unique(mesh.faces[bad])
                ring = np.isin(mesh.faces, vt).any(axis=1)
                removed += int(ring.sum())
                mesh.update_faces(~ring)
                mesh.remove_unreferenced_vertices()
                mesh, _ = _meshfix(mesh, cb, 90, keep_all=(fragments != "largest"), force=True)
            stats["folds_fixed"] = removed
            mesh.update_faces(mesh.nondegenerate_faces())
            mesh.remove_unreferenced_vertices()
            if repair and not mesh.is_watertight:
                mesh, _ = _meshfix(mesh, cb, 90, keep_all=(fragments != "largest"))

    if cyl_fits and "cylinders" not in stats:
        # keine Ebenen begradigt -> Rundungen hier einpassen
        stats["cylinders"] = [round(r, 4) for r in
                              snap_cylinders(mesh, np.zeros((0, 3)), np.zeros(0), tol, cb, given=cyl_fits)]
        if repair:
            mesh, _ = _meshfix(mesh, cb, 90, keep_all=(fragments != "largest"), force=True)

    if not mesh.is_winding_consistent:
        _cb(cb, 94, "Richte Facetten einheitlich aus …")
        trimesh.repair.fix_normals(mesh, multibody=True)

    _cb(cb, 97, "Schreibe aufbereiteten Scan …")
    mesh.export(out, file_type="stl")
    stats["faces_out"] = len(mesh.faces)
    stats["watertight"] = bool(mesh.is_watertight)
    _cb(cb, 100, "Scan aufbereitet.")
    return stats


def _meshfix(mesh, cb, pct, keep_all, force=False):
    import trimesh
    info = {}
    try:
        import pymeshfix
    except Exception:
        return mesh, info
    if not force and mesh.is_watertight and mesh.is_winding_consistent:
        return mesh, info
    _cb(cb, pct, "Repariere Netz: Löcher schließen, Überschneidungen entfernen …")
    try:
        parts = mesh.split(only_watertight=False) if keep_all else [mesh]
        fixed = []
        for p in parts:
            mf = pymeshfix.MeshFix(np.asarray(p.vertices, dtype=np.float64),
                                   np.asarray(p.faces, dtype=np.int32))
            mf.repair(joincomp=False, remove_smallest_components=not keep_all)
            if len(mf.faces):
                fixed.append(trimesh.Trimesh(mf.points, mf.faces, process=True))
        if fixed:
            new = fixed[0] if len(fixed) == 1 else trimesh.util.concatenate(fixed)
            info["holes_closed"] = True
            return new, info
    except Exception:
        import traceback
        traceback.print_exc()
    return mesh, info


def _auto_tolerance(mesh, diag):
    """Schätzt das Scan-Rauschen: Abweichung der Punkte von der lokalen
    Tangentialebene (über Nachbarflächen), robust per Median."""
    try:
        adj = mesh.face_adjacency
        if len(adj) > 200_000:
            adj = adj[np.random.default_rng(0).choice(len(adj), 200_000, replace=False)]
        n = mesh.face_normals
        c = mesh.triangles_center
        # nur fast gleich ausgerichtete Nachbarn: echte Kanten sind kein Rauschen
        flat = np.einsum("ij,ij->i", n[adj[:, 0]], n[adj[:, 1]]) > math.cos(math.radians(15))
        if flat.sum() < 50:
            return 2e-4 * diag
        a = adj[flat]
        d = np.abs(np.einsum("ij,ij->i", c[a[:, 1]] - c[a[:, 0]], n[a[:, 0]]))
        noise = float(np.median(d))
        # Toleranz = gut das Doppelte des typischen Rauschens, begrenzt
        return float(np.clip(7.5 * noise, 2e-4 * diag, 5e-3 * diag))
    except Exception:
        return 1e-3 * diag


def _grow(seed, n, d, region, nb, normals, V, F, tol, cos_max):
    seen = {seed}
    stack = [seed]
    out = [seed]
    while stack:
        f = stack.pop()
        for g in nb[f]:
            if g in seen or region[g] != -1:
                continue
            seen.add(g)
            if normals[g] @ n < cos_max:
                continue
            if np.max(np.abs(V[F[g]] @ n - d)) > tol:
                continue
            out.append(g)
            stack.append(g)
    return np.array(out)


def _fit_plane(pts, ref):
    c = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    n = vt[2] if np.dot(vt[2], ref) >= 0 else -vt[2]
    return n, float(n @ c)


def _merge_and_absorb(region, planes, V, F, normals, nb, tol, cos_max):
    """1. Bereiche derselben Ebene zusammenfassen (z. B. eine Oberseite, die
    durch Rauschen oder einen Aufbau in mehrere Stücke zerfallen ist).
    2. Angrenzende, noch freie Dreiecke aufnehmen, die zur endgültigen
    Ebene passen (Randbereiche)."""
    k = len(planes)
    parent = list(range(k))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    cos_merge = math.cos(math.radians(3.0))
    N = np.array([p[0] for p in planes])
    D = np.array([p[1] for p in planes])
    for i in range(k):
        for j in range(i + 1, k):
            if N[i] @ N[j] >= cos_merge and abs(D[i] - D[j]) <= tol:
                parent[find(j)] = find(i)
    roots = sorted({find(i) for i in range(k)})
    remap = {r: idx for idx, r in enumerate(roots)}
    lut = np.array([remap[find(i)] for i in range(k)])
    region = np.where(region >= 0, lut[np.maximum(region, 0)], region)
    new_planes = []
    for idx in range(len(roots)):
        mem = np.nonzero(region == idx)[0]
        pts = V[np.unique(F[mem])]
        if len(pts) > 60000:
            pts = pts[:: len(pts) // 60000]
        new_planes.append(_fit_plane(pts, N[roots[idx]]))
    # Randbereiche aufnehmen
    NP = np.array([p[0] for p in new_planes])
    DP = np.array([p[1] for p in new_planes])
    frontier = [f for f in np.nonzero(region >= 0)[0].tolist()]
    while frontier:
        nxt = []
        for f in frontier:
            r = region[f]
            for g in nb[f]:
                if region[g] != -1:
                    continue
                if normals[g] @ NP[r] < cos_max:
                    continue
                if np.max(np.abs(V[F[g]] @ NP[r] - DP[r])) > 1.5 * tol:
                    continue
                region[g] = r
                nxt.append(g)
        frontier = nxt
    # Nicht angrenzende, aber eindeutig in einer Ebene liegende Dreiecke
    # (z. B. durch Rauschen abgetrennte Inseln) ebenfalls aufnehmen
    free = np.nonzero(region == -1)[0]
    if len(free):
        dist = np.abs(np.einsum("fkj,pj->fpk", V[F[free]], NP) - DP[None, :, None]).max(axis=2)
        ang = normals[free] @ NP.T
        ok = (dist <= tol) & (ang >= cos_max)
        best = np.where(ok, dist, np.inf).argmin(axis=1)
        has = ok.any(axis=1)
        region[free[has]] = best[has]
    return region, list(zip(NP, DP))


def flatten_planes(mesh, tol, cb=None, max_angle_deg=12.0, min_area_frac=0.002, exclude=None):
    """Erkennt ebene Bereiche und legt deren Punkte exakt in die Ebene.
    Gibt (Anzahl Ebenen, Anzahl verschobener Punkte) zurück."""
    F = mesh.faces
    V = mesh.vertices.copy()
    nf = len(F)
    if nf == 0:
        return 0, 0
    normals = mesh.face_normals
    centers = mesh.triangles_center
    areas = mesh.area_faces
    total_area = float(areas.sum())
    cos_max = math.cos(math.radians(max_angle_deg))

    # Nachbarschaft je Fläche
    adj = mesh.face_adjacency
    nb = [[] for _ in range(nf)]
    for a, b in adj.tolist():
        nb[a].append(b)
        nb[b].append(a)

    # Startflächen: lokal möglichst eben (Normale ähnlich zu allen Nachbarn)
    smooth = np.ones(nf)
    na, nb_ = normals[adj[:, 0]], normals[adj[:, 1]]
    dots = np.einsum("ij,ij->i", na, nb_)
    np.minimum.at(smooth, adj[:, 0], dots)
    np.minimum.at(smooth, adj[:, 1], dots)
    order = np.lexsort((-areas, -np.round(smooth, 3)))

    region = np.full(nf, -1, dtype=np.int64)
    if exclude is not None:
        region[exclude] = -3          # Zylinderflächen: nicht als Ebene verwenden
    planes = []
    rid = 0
    min_area = min_area_frac * total_area
    for seed in order:
        if region[seed] != -1 or smooth[seed] < cos_max:
            continue
        # Startebene aus der Umgebung der Startfläche (2 Nachbarringe)
        ring = {seed}
        for _ in range(2):
            ring |= {g for f in list(ring) for g in nb[f] if region[g] == -1}
        n0, d0 = _fit_plane(V[np.unique(F[list(ring)])], normals[seed])
        members = [seed]
        for _it in range(3):   # wachsen -> Ebene neu anpassen -> erneut wachsen
            members = _grow(seed, n0, d0, region, nb, normals, V, F, tol, cos_max)
            pts = V[np.unique(F[members])]
            if len(pts) > 60000:
                pts = pts[:: len(pts) // 60000]
            n_new, d_new = _fit_plane(pts, n0)
            if n_new @ n0 > math.cos(math.radians(0.05)) and abs(d_new - d0) < 0.05 * tol:
                n0, d0 = n_new, d_new
                break
            n0, d0 = n_new, d_new
        members = _grow(seed, n0, d0, region, nb, normals, V, F, tol, cos_max)
        region[members] = rid
        members = np.array(members)
        area = float(areas[members].sum())
        if area < min_area or len(members) < 6:
            region[members] = -2   # zu klein: nicht als Ebene behandeln
            continue
        # Schmale Streifen auf gekrümmten Flächen (Zylinder, Freiform) sind
        # keine Ebenen: dort setzt sich die Fläche am Rand fast überall
        # glatt fort, bei echten Ebenen folgt meist eine Kante.
        bnd = [normals[g] @ n0 for f in members.tolist() for g in nb[f] if region[g] != rid]
        if bnd and np.mean(np.array(bnd) > math.cos(math.radians(25))) > 0.85:
            region[members] = -2
            continue
        pts = V[np.unique(F[members])]
        c = pts.mean(0)
        _, sv, vt = np.linalg.svd(pts - c, full_matrices=False)
        nn = vt[2] if np.dot(vt[2], n0) >= 0 else -vt[2]
        dev = np.abs((pts - c) @ nn)
        if np.percentile(dev, 95) > tol:   # doch nicht eben genug
            region[members] = -2
            continue
        planes.append((nn, float(nn @ c)))
        rid += 1
        if cb and rid % 10 == 0:
            cb(60 + min(25, rid // 10), f"Erkenne ebene Bereiche … {rid} gefunden")
    region[region == -2] = -1
    if planes:
        region, planes = _merge_and_absorb(region, planes, V, F, normals, nb, tol, cos_max)
    flatten_planes.last_coverage = float(areas[region >= 0].sum() / total_area) if total_area else 0.0
    if not planes:
        return 0, 0

    # Punkte zuordnen: zu welchen Ebenen gehört jeder Punkt? Zusätzlich
    # „Kanten-Fang“: Ein Punkt, der nahe an der Ebene eines benachbarten
    # Bereichs liegt, wird auch dieser zugeordnet. So werden die schrägen
    # Übergangsdreiecke an Kanten aufgelöst und die Kanten werden scharf.
    N = np.array([p[0] for p in planes])
    D = np.array([p[1] for p in planes])
    edge_len = float(np.mean(mesh.edges_unique_length)) if len(mesh.edges_unique) else tol
    snap = 2.0 * tol
    vfaces = mesh.vertex_faces          # (nv, k), -1 = leer
    vreg = np.where(vfaces >= 0, region[np.maximum(vfaces, 0)], -1)
    moved = 0
    newV = V.copy()
    lam = 1e-9
    limit = 3.0 * tol
    assign = {}
    has_reg = (vreg >= 0).any(axis=1)
    vnb = mesh.vertex_neighbors
    # Kandidaten: Punkte mit Ebenen-Dreieck in sich selbst oder einem Nachbarpunkt
    cand_v = [v for v in range(len(V)) if has_reg[v] or any(has_reg[w] for w in vnb[v])]
    for v in cand_v:
        x0 = V[v]
        regs = set(vreg[v][vreg[v] >= 0].tolist())
        for w in vnb[v]:
            if has_reg[w]:
                regs.update(vreg[w][vreg[w] >= 0].tolist())
        rs = [r for r in regs if abs(N[r] @ x0 - D[r]) <= snap]
        if not rs:
            continue
        if len(rs) == 1:
            n = N[rs[0]]
            x = x0 - (n @ x0 - D[rs[0]]) * n
        else:
            # Punkt auf Schnittkante/-ecke: kleinste Verschiebung, die alle Ebenen erfüllt
            A = N[rs]
            b = D[rs]
            M = A.T @ A + lam * np.eye(3)
            x = np.linalg.solve(M, A.T @ b + lam * x0)
            if np.linalg.norm(x - x0) > limit:
                r0 = min(rs, key=lambda r: abs(N[r] @ x0 - D[r]))
                n = N[r0]
                x = x0 - (n @ x0 - D[r0]) * n
        if np.linalg.norm(x - x0) <= limit:
            newV[v] = x
            moved += 1
            assign[v] = rs if len(rs) <= 2 else None   # Ecken (>=3 Ebenen) bleiben fest
    newV = _relax_in_planes(mesh, newV, assign, N, D)
    mesh.vertices = newV
    flatten_planes.last_planes = (N, D)
    return len(planes), moved


def snap_enclosed(mesh, N, D, max_iter=6):
    """Punkte, deren Nachbarn ALLE in derselben Ebene liegen, die selbst aber
    (noch) nicht darin liegen – Rausch-Spitzen oder Punkte aus Lochfüllungen –
    werden ebenfalls in die Ebene gelegt. Sonst entstehen in der STEP-Fläche
    viele kleine Löcher/Inseln (bis hin zu ungültigen Flächen)."""
    diag = float(np.linalg.norm(mesh.extents)) or 1.0
    eps = 1e-7 * diag
    total = 0
    for _ in range(max_iter):
        V = mesh.vertices.copy()
        dist = np.abs(V @ N.T - D[None, :])          # (nv, P)
        on = dist < eps
        on_any = on.any(axis=1)
        vnb = mesh.vertex_neighbors
        changed = 0
        for v in np.nonzero(~on_any)[0].tolist():
            nbs = vnb[v]
            if len(nbs) < 3:
                continue
            common = on[nbs].all(axis=0)
            if common.any():
                p = int(np.argmax(common))
                V[v] = V[v] - (N[p] @ V[v] - D[p]) * N[p]
                changed += 1
        if not changed:
            break
        mesh.vertices = V
        total += changed
    return total


def _cyl_fit(points, normals):
    nn = normals[np.linalg.norm(normals, axis=1) > 0.5]
    if len(nn) < 3:
        return None
    _, _, vt = np.linalg.svd(nn, full_matrices=False)
    axis = vt[-1] / np.linalg.norm(vt[-1])
    u = np.cross(axis, [1.0, 0, 0] if abs(axis[0]) < 0.9 else [0, 1.0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    c0 = points.mean(axis=0)
    rel = points - c0
    x, y = rel @ u, rel @ v
    sol, *_ = np.linalg.lstsq(np.column_stack([x, y, np.ones_like(x)]), x * x + y * y, rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    r2 = sol[2] + cx * cx + cy * cy
    if not r2 > 0:
        return None
    return c0 + cx * u + cy * v, axis, float(np.sqrt(r2))


def snap_cylinders(mesh, N, D, tol, cb=None, crease_deg=25.0, detect_only=False, given=None):
    """Zylinderbereiche (Bohrungen, Zapfen, Rundungen) erkennen und deren
    Punkte exakt auf den ausgeglichenen Zylinder legen: exakte Durchmesser
    und glatte Rundungen statt verrauschter Facetten.
    Gibt eine Liste der gefundenen Radien zurück."""
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components
    snap_cylinders.last = []
    V = mesh.vertices.copy()
    F = mesh.faces
    diag = float(np.linalg.norm(mesh.extents)) or 1.0
    eps = 1e-7 * diag
    on_plane = (np.abs(V @ N.T - D[None, :]) < eps).any(axis=1) if len(N) else np.zeros(len(V), bool)
    if given is not None:
        return _apply_cylinders(mesh, V, F, given, on_plane, tol, cb)
    free = ~on_plane[F].all(axis=1)
    adj = mesh.face_adjacency
    ang = mesh.face_adjacency_angles
    ok = free[adj[:, 0]] & free[adj[:, 1]] & (ang < math.radians(crease_deg))
    a = adj[ok]
    nf = len(F)
    g = sp.coo_matrix((np.ones(len(a)), (a[:, 0], a[:, 1])), shape=(nf, nf))
    _, lab = connected_components(g, directed=False)
    areas = mesh.area_faces
    total = float(areas.sum())
    normals = mesh.face_normals
    fits = []   # (center, axis, R, face_indices)
    comp_area = np.bincount(lab, weights=areas * free)
    for c in np.nonzero(comp_area >= 0.002 * total)[0]:
        fm = np.nonzero((lab == c) & free)[0]
        if len(fm) < 30:
            continue
        pts = V[F[fm]].reshape(-1, 3)
        nrm = np.repeat(normals[fm], 3, axis=0)
        fit = _cyl_fit(pts, nrm)
        for _ in range(2):
            if fit is None:
                break
            cen, ax, R = fit
            rel = pts - cen
            rad = np.linalg.norm(rel - np.outer(rel @ ax, ax), axis=1)
            keep = np.abs(rad - R) < 3 * tol
            if keep.sum() < 30 or keep.all():
                break
            fit = _cyl_fit(pts[keep], nrm[keep])
        if fit is None:
            continue
        cen, ax, R = fit
        if R > 0.75 * diag or R < 2 * tol:
            continue
        fits.append([cen, ax, R, fm])
    # Teile desselben Zylinders (z. B. durch Rauschen getrennte Bohrungshälften)
    # zusammenfassen und gemeinsam neu anpassen
    merged = []
    for f in fits:
        for m in merged:
            if (abs(abs(f[1] @ m[1]) - 1) < 0.01 and abs(f[2] - m[2]) < 0.05 * max(f[2], m[2])
                    and np.linalg.norm(np.cross(f[0] - m[0], m[1])) < 0.1 * m[2]):
                m[3] = np.concatenate([m[3], f[3]])
                break
        else:
            merged.append(list(f))
    good = []
    for cen, ax, R, fm in merged:
        pts = V[F[fm]].reshape(-1, 3)
        nrm = np.repeat(normals[fm], 3, axis=0)
        fit = _cyl_fit(pts, nrm) or (cen, ax, R)
        cen, ax, R = fit
        rel = pts - cen
        axial = rel @ ax
        radv = rel - np.outer(axial, ax)
        rad = np.linalg.norm(radv, axis=1)
        if np.mean(np.abs(rad - R) < 1.5 * tol) < 0.9:
            continue
        u = np.cross(ax, [1.0, 0, 0] if abs(ax[0]) < 0.9 else [0, 1.0, 0])
        u /= np.linalg.norm(u)
        w = np.cross(ax, u)
        th = np.sort(np.arctan2(radv @ w, radv @ u))
        gaps = np.diff(np.r_[th, th[0] + 2 * np.pi])
        if 2 * np.pi - gaps.max() < math.radians(45):
            continue
        good.append((cen, ax, R, fm))
    if detect_only:
        return good
    return _apply_cylinders(mesh, V, F, good, on_plane, tol, cb)


def _apply_cylinders(mesh, V, F, good, on_plane, tol, cb):
    radii = []
    for cen, ax, R, fm in good:
        vids = np.unique(F[fm])
        vids = vids[~on_plane[vids]]
        r = V[vids] - cen
        al = r @ ax
        rv = r - np.outer(al, ax)
        ln = np.linalg.norm(rv, axis=1)
        okv = (ln > 1e-12) & (np.abs(ln - R) <= 2.5 * tol)   # nur Rauschen, keine Ausreißer
        V[vids[okv]] = cen + np.outer(al[okv], ax) + rv[okv] / ln[okv, None] * R
        radii.append(R)
        if cb:
            cb(86, f"Rundung eingepasst: Radius {R:.3f}")
    mesh.vertices = V
    return radii


def find_folds(mesh, N, D):
    """Findet in den begradigten Ebenen umgeklappte oder entartete Dreiecke
    (würden in der STEP-Datei sich selbst überschneidende Flächen ergeben)."""
    V = mesh.vertices
    F = mesh.faces
    diag = float(np.linalg.norm(mesh.extents)) or 1.0
    eps = 1e-7 * diag
    cr = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    area2 = np.linalg.norm(cr, axis=1)
    e = np.stack([V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 1]], V[F[:, 0]] - V[F[:, 2]]], axis=1)
    longest2 = (e ** 2).sum(axis=2).max(axis=1)
    bad = np.zeros(len(F), dtype=bool)
    for n, d in zip(N, D):
        on = np.abs(V @ n - d) < eps
        fm = on[F].all(axis=1)
        if not fm.any():
            continue
        bad |= fm & ((cr @ n) <= 0.05 * area2 + 1e-18 * diag * diag)
        # sehr schmale Splitter (kippen beim Speichern als float32 leicht um)
        bad |= fm & (area2 < 0.02 * longest2)
    bad |= area2 < 1e-14 * diag * diag
    return np.nonzero(bad)[0]


def _relax_in_planes(mesh, X, assign, N, D, iterations=6):
    """Rauschen in der Ebene glätten: Nach dem Projizieren können winzige
    Dreiecke „umklappen“ (überlappen). Punkte werden deshalb innerhalb
    ihrer Ebene (bzw. entlang ihrer Kante) zum Mittel ihrer Nachbarn
    verschoben – das entwirrt die Dreiecke, ohne die Form zu ändern."""
    import scipy.sparse as sp
    e = mesh.edges_unique
    nv = len(X)
    A = sp.coo_matrix((np.ones(2 * len(e)), (np.r_[e[:, 0], e[:, 1]], np.r_[e[:, 1], e[:, 0]])),
                      shape=(nv, nv)).tocsr()
    deg = np.asarray(A.sum(axis=1)).ravel()
    deg[deg == 0] = 1
    # nur Punkte im Inneren einer Ebene (alle Nachbarn in derselben Ebene);
    # Kanten- und Randpunkte bleiben, wo sie sind (keine Formänderung)
    vnb = mesh.vertex_neighbors
    def interior(v, r):
        return all(assign.get(w) is not None and len(assign[w]) >= 1 and r[0] in assign[w] for w in vnb[v])
    one_v = np.array([v for v, r in assign.items() if r is not None and len(r) == 1 and interior(v, r)],
                     dtype=np.int64)
    one_r = np.array([assign[v][0] for v in one_v.tolist()], dtype=np.int64)
    two = []   # Kantenpunkte werden nicht verschoben (sonst „wandern“ Ecken)
    two_v = np.array([v for v, _ in two], dtype=np.int64)
    if len(two):
        n1 = N[[r[0] for _, r in two]]
        n2 = N[[r[1] for _, r in two]]
        dirs = np.cross(n1, n2)
        ln = np.linalg.norm(dirs, axis=1, keepdims=True)
        okd = ln.ravel() > 1e-6
        dirs = dirs / np.where(ln > 1e-6, ln, 1)
    X = X.copy()
    for _ in range(iterations):
        avg = (A @ X) / deg[:, None]
        if len(one_v):
            nn = N[one_r]
            a = avg[one_v]
            X[one_v] = a - ((a * nn).sum(1) - D[one_r])[:, None] * nn
        if len(two_v):
            base = X[two_v]
            a = avg[two_v]
            t = ((a - base) * dirs).sum(1)
            X[two_v[okd]] = (base + t[:, None] * dirs)[okd]
    return X
