"""Hintergrund-Aufträge in einem EIGENEN Prozess (STEP-Umwandlung, Vereinfachung).

Wie im STL-STEP-Konverter-MK1: Die Umwandlung kann bei großen Netzen
mehrere GB Arbeitsspeicher belegen. Als eigener Prozess wird der Speicher
danach garantiert freigegeben, ein Absturz (z. B. Out-of-Memory) betrifft
nur den Auftrag – nicht das Programmfenster – und "Abbrechen" ist jederzeit
möglich (Prozess wird beendet).

Liegt eine Ausrichtung vor, wird zuerst eine ausgerichtete Zwischen-STL
(verlustfrei, siehe stl_io) in einen Temp-Ordner geschrieben und diese dann
umgewandelt bzw. vereinfacht.
"""

from __future__ import annotations

import os
import traceback


def _transform_first(src, R, t, put, span, workdir):
    """Gibt den Pfad der umzuwandelnden STL zurück (immer im Arbeitsordner,
    damit Zwischendateien des Konverters nicht neben der Quelldatei landen)."""
    import stl_io
    tmp = os.path.join(workdir, "eingabe.stl")
    if stl_io.is_identity(R, t, eps=0):
        try:
            os.link(src, tmp)          # Hardlink: kostet keinen Platz/keine Zeit
            return tmp, False
        except OSError:
            return src, False          # anderes Laufwerk -> Original direkt nutzen
    a, b = span
    stl_io.save_transformed(
        src, tmp, R, t,
        progress=lambda p, m: put(("progress", a + int(p * (b - a) / 100), "Ausrichtung anwenden: " + m)))
    return tmp, True


MAX_FACES_FOR_SPLIT = 20_000_000


def simplify_file(inp, out, percent, smoothing, cb):
    """Vereinfacht eine STL robust und schreibt das Ergebnis als Binär-STL.

    Unterschied zur reinen trimesh-Dezimierung: Das Netz wird in einzelne
    Körper zerlegt und jeder Körper für sich vereinfacht. Würde ein Körper
    dabei zerstört (verschwindet oder ändert seine Abmessungen um mehr als
    1 %), bleibt er unverändert. Hintergrund: Bei mehreren Körpern in einer
    Datei löscht die Quadric-Dezimierung sonst gern kleine, grob
    triangulierte Teile (z. B. ebene CAD-Flächen) komplett.
    Gibt (Dreiecke vorher, nachher, Punkte vorher, nachher) zurück."""
    import numpy as np
    import trimesh
    cb(2, "Lese Netz ein …")
    mesh = trimesh.load(inp, force="mesh")
    fb, vb = len(mesh.faces), len(mesh.vertices)
    if fb == 0:
        raise ValueError("Die STL-Datei enthält keine Dreiecke.")
    if smoothing > 0:
        cb(15, f"Glättung ({smoothing}×) …")
        trimesh.smoothing.filter_taubin(mesh, iterations=smoothing)
    if percent < 100:
        if fb <= MAX_FACES_FOR_SPLIT:
            cb(25, "Zerlege in einzelne Körper …")
            bodies = mesh.split(only_watertight=False)
            if len(bodies) == 0:
                bodies = [mesh]
        else:
            bodies = [mesh]
        out_parts, kept = [], 0
        for i, b in enumerate(bodies):
            target = max(4, int(len(b.faces) * percent / 100))
            try:
                r = b.simplify_quadric_decimation(face_count=target)
            except Exception:
                r = None
            ok = r is not None and len(r.faces) > 0
            if ok:
                e0, e1 = b.extents, r.extents
                ok = bool(np.all(np.abs(e1 - e0) <= 0.01 * max(float(e0.max()), 1e-12)))
            if ok:
                out_parts.append(r)
            else:
                out_parts.append(b)
                kept += 1
            if i % 20 == 0 or i == len(bodies) - 1:
                cb(30 + int(55 * (i + 1) / len(bodies)),
                   f"Vereinfache Körper {i + 1} / {len(bodies)} auf {percent} % …")
        mesh = out_parts[0] if len(out_parts) == 1 else trimesh.util.concatenate(out_parts)
        if kept:
            cb(86, f"{kept} Körper unverändert gelassen (wären sonst zerstört worden).")
    cb(90, "Schreibe STL …")
    mesh.export(out, file_type="stl")
    cb(100, "Fertig.")
    return fb, len(mesh.faces), vb, len(mesh.vertices)


def step_job(src, dst, R, t, settings_dict, workdir, queue):
    put = queue.put
    inp = src
    try:
        from converter import ConversionSettings, ConversionError, convert_stl_to_step
        inp, aligned = _transform_first(src, R, t, put, (0, 10), workdir)
        off = 10 if aligned else 0
        settings = ConversionSettings(**settings_dict)
        if settings.decimate_percent < 100 or settings.smoothing_iterations > 0:
            pre = os.path.join(workdir, "vereinfacht.stl")
            a = off
            simplify_file(inp, pre, settings.decimate_percent, settings.smoothing_iterations,
                          lambda p, m: put(("progress", a + int(p * 20 / 100), "Vorab-Vereinfachung: " + m)))
            _cleanup(inp)
            inp = pre
            off = a + 20
            settings.decimate_percent = 100
            settings.smoothing_iterations = 0

        def cb(p, m):
            put(("progress", off + int(p * (100 - off) / 100), m))

        part = dst + ".teil"
        res = convert_stl_to_step(inp, part, preview_path=None, settings=settings, progress_cb=cb)
        os.replace(part, dst)
        put(("done", {
            "path": dst,
            "is_solid": res.is_solid,
            "faces_before": res.face_count_before,
            "faces_after": res.face_count_after,
            "volume": res.volume,
            "shapes": [(s.kind, round(s.radius, 3), s.replaced) for s in res.detected_shapes],
        }))
    except Exception as e:  # noqa
        traceback.print_exc()
        put(("error", str(e)))
    finally:
        _cleanup(inp)


def simplify_job(src, dst, R, t, settings_dict, workdir, queue):
    put = queue.put
    inp = src
    try:
        inp, aligned = _transform_first(src, R, t, put, (0, 15), workdir)
        off = 15 if aligned else 0

        def cb(p, m):
            put(("progress", off + int(p * (100 - off) / 100), m))

        part = dst + ".teil.stl"
        fb, fa, vb, va = simplify_file(inp, part, settings_dict["decimate_percent"],
                                       settings_dict["smoothing_iterations"], cb)
        os.replace(part, dst)
        put(("done", {"path": dst, "faces_before": fb, "faces_after": fa,
                      "verts_before": vb, "verts_after": va}))
    except Exception as e:  # noqa
        traceback.print_exc()
        put(("error", str(e)))
    finally:
        _cleanup(inp)


def _cleanup(inp):
    # converter legt "<eingabe>_pre.stl" an – Zwischendatei wieder entfernen
    # (der Arbeitsordner selbst wird vom Hauptprogramm gelöscht)
    p = inp + "_pre.stl"
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass
