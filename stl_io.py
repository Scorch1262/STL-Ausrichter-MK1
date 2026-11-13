"""
STL-Ein-/Ausgabe für STL-Ausrichter-MK1.

Grundsätze
----------
* Dateien werden NIE komplett in den Speicher geladen, sondern in Blöcken
  gestreamt -> funktioniert auch mit STL-Dateien von mehreren GB.
* Beim Speichern bleibt die Struktur exakt erhalten:
    - Binär-STL: 80-Byte-Header, Dreiecksanzahl, Reihenfolge der Dreiecke,
      2-Byte-Attribut je Dreieck und evtl. angehängte Zusatzdaten werden
      1:1 übernommen. Nur Normalen und Eckpunkte werden gedreht.
      -> Dateigröße ist bytegenau identisch, keine Komprimierung.
    - ASCII-STL: Jede Zeile bleibt erhalten (Einrückung, Schlüsselwörter,
      Leerzeichen, Zeilenenden, solid-/endsolid-Namen). Nur die Zahlen hinter
      "facet normal" und "vertex" werden ersetzt, und zwar im selben
      Zahlenformat (gleiche Nachkommastellen / Exponentenschreibweise).
* Gerechnet wird in float64, gespeichert im Originalformat (float32 bei Binär).
* Drehungen um 90° werden exakt gerechnet (Matrix wird bereinigt), d. h. dabei
  entstehen keinerlei Rundungsfehler.
"""

from __future__ import annotations

import math
import os
import re
import shutil
from dataclasses import dataclass, field

import numpy as np

BIN_DTYPE = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
assert BIN_DTYPE.itemsize == 50

CHUNK_TRIS = 1_000_000          # Dreiecke pro Block beim Binär-Streaming
ASCII_CHUNK = 64 * 1024 * 1024  # Bytes pro Block beim ASCII-Einlesen


class Abgebrochen(Exception):
    """Wird ausgelöst, wenn der Benutzer einen Vorgang abbricht."""


@dataclass
class StlInfo:
    path: str
    fmt: str                      # "binary" | "ascii"
    file_size: int
    tri_count: int
    header: bytes = b""
    bbox_min: np.ndarray = field(default_factory=lambda: np.zeros(3))
    bbox_max: np.ndarray = field(default_factory=lambda: np.zeros(3))
    sample: np.ndarray | None = None   # (N,3,3) float32, Anzeige-Stichprobe
    stride: int = 1

    @property
    def center(self) -> np.ndarray:
        return (self.bbox_min + self.bbox_max) * 0.5


# --------------------------------------------------------------------------
# Format-Erkennung
# --------------------------------------------------------------------------
def detect_format(path: str) -> tuple[str, int]:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(84)
        probe = head + f.read(4096)
    if size >= 84:
        count = int.from_bytes(head[80:84], "little")
        expected = 84 + count * 50
        if expected == size:
            return "binary", count
    starts_solid = probe.lstrip().lower().startswith(b"solid")
    if starts_solid and (b"facet" in probe or b"endsolid" in probe or size < 300):
        return "ascii", -1
    if size >= 84:
        count = int.from_bytes(head[80:84], "little")
        if 84 + count * 50 <= size:
            # Binär mit angehängten Zusatzdaten (werden beim Speichern 1:1 kopiert)
            return "binary", count
    if starts_solid:
        return "ascii", -1
    raise ValueError("Unbekanntes oder beschädigtes STL-Format.")


# --------------------------------------------------------------------------
# Laden (Bounding-Box exakt über alle Dreiecke, Anzeige als Stichprobe)
# --------------------------------------------------------------------------
def load_stl(path: str, max_display_tris: int, progress=None, cancel=None) -> StlInfo:
    fmt, count = detect_format(path)
    if fmt == "binary":
        return _load_binary(path, count, max_display_tris, progress, cancel)
    return _load_ascii(path, max_display_tris, progress, cancel)


def _load_binary(path, count, max_display_tris, progress, cancel) -> StlInfo:
    size = os.path.getsize(path)
    info = StlInfo(path=path, fmt="binary", file_size=size, tri_count=count)
    stride = max(1, math.ceil(count / max(1, max_display_tris)))
    info.stride = stride
    chunk = max(stride, (min(CHUNK_TRIS, 2_000_000) // stride) * stride)

    vmin = np.full(3, np.inf)
    vmax = np.full(3, -np.inf)
    parts = []
    with open(path, "rb") as f:
        info.header = f.read(80)
        f.read(4)
        done = 0
        while done < count:
            if cancel and cancel():
                raise Abgebrochen()
            n = min(chunk, count - done)
            buf = np.fromfile(f, dtype=BIN_DTYPE, count=n)
            if len(buf) != n:
                raise ValueError("Datei ist kürzer als im Header angegeben.")
            v = buf["v"]
            flat = v.reshape(-1, 3)
            with np.errstate(invalid="ignore"):
                vmin = np.fmin(vmin, np.nanmin(flat, axis=0))
                vmax = np.fmax(vmax, np.nanmax(flat, axis=0))
            parts.append(np.ascontiguousarray(v[::stride]))
            done += n
            if progress:
                progress(int(done * 100 / max(1, count)), f"Lese Dreiecke … {done:,} / {count:,}".replace(",", "."))
    info.bbox_min, info.bbox_max = _fix_bbox(vmin, vmax)
    info.sample = np.concatenate(parts) if parts else np.zeros((0, 3, 3), np.float32)
    return info


_VERTEX_RE = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")


def _load_ascii(path, max_display_tris, progress, cancel) -> StlInfo:
    size = os.path.getsize(path)
    info = StlInfo(path=path, fmt="ascii", file_size=size, tri_count=0)
    vmin = np.full(3, np.inf)
    vmax = np.full(3, -np.inf)
    all_tris = []
    rest = b""
    read = 0
    with open(path, "rb") as f:
        info.header = f.readline()[:80].rstrip(b"\r\n")
        f.seek(0)
        while True:
            if cancel and cancel():
                raise Abgebrochen()
            block = f.read(ASCII_CHUNK)
            if not block:
                data, rest = rest, b""
            else:
                read += len(block)
                data = rest + block
                cut = data.rfind(b"\n")
                if cut < 0:
                    rest = data
                    continue
                data, rest = data[: cut + 1], data[cut + 1:]
            found = _VERTEX_RE.findall(data)
            if found:
                arr = np.array(found, dtype="S").astype(np.float64).reshape(-1, 3)
                with np.errstate(invalid="ignore"):
                    vmin = np.fmin(vmin, np.nanmin(arr, axis=0))
                    vmax = np.fmax(vmax, np.nanmax(arr, axis=0))
                all_tris.append(arr.astype(np.float32))
            if progress:
                progress(int(read * 100 / max(1, size)), "Lese ASCII-STL …")
            if not block:
                break
    verts = np.concatenate(all_tris) if all_tris else np.zeros((0, 3), np.float32)
    ntri = len(verts) // 3
    tris = verts[: ntri * 3].reshape(-1, 3, 3)
    info.tri_count = ntri
    stride = max(1, math.ceil(ntri / max(1, max_display_tris)))
    info.stride = stride
    info.sample = np.ascontiguousarray(tris[::stride])
    info.bbox_min, info.bbox_max = _fix_bbox(vmin, vmax)
    return info


def _fix_bbox(vmin, vmax):
    if not np.all(np.isfinite(vmin)) or not np.all(np.isfinite(vmax)):
        return np.zeros(3), np.zeros(3)
    return vmin.astype(np.float64), vmax.astype(np.float64)


# --------------------------------------------------------------------------
# Mathematik
# --------------------------------------------------------------------------
def clean_rotation(R: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """Setzt Werte, die praktisch 0 oder ±1 sind, exakt -> 90°-Drehungen verlustfrei."""
    R = np.array(R, dtype=np.float64)
    R[np.abs(R) < eps] = 0.0
    near_one = np.abs(np.abs(R) - 1.0) < eps
    R[near_one] = np.sign(R[near_one])
    return R


def is_identity(R, t=None, eps=1e-15) -> bool:
    ok = np.allclose(R, np.eye(3), atol=eps, rtol=0)
    if t is not None:
        ok = ok and np.allclose(t, 0.0, atol=eps, rtol=0)
    return bool(ok)


# --------------------------------------------------------------------------
# Exakte Maße nach Transformation (gestreamt über ALLE Dreiecke)
# --------------------------------------------------------------------------
def iter_triangles(path, cancel=None):
    """Liefert blockweise (n,3,3)-float64-Arrays aller Dreiecke und den Fortschritt 0..1."""
    fmt, count = detect_format(path)
    if fmt == "binary":
        with open(path, "rb") as f:
            f.seek(84)
            done = 0
            while done < count:
                if cancel and cancel():
                    raise Abgebrochen()
                n = min(CHUNK_TRIS, count - done)
                buf = np.fromfile(f, dtype=BIN_DTYPE, count=n)
                done += len(buf)
                yield buf["v"].astype(np.float64), done / max(1, count)
                if len(buf) < n:
                    break
    else:
        size = os.path.getsize(path)
        rest = b""
        read = 0
        carry = np.zeros((0, 3))
        with open(path, "rb") as f:
            while True:
                if cancel and cancel():
                    raise Abgebrochen()
                block = f.read(ASCII_CHUNK)
                if block:
                    read += len(block)
                    data = rest + block
                    cut = data.rfind(b"\n")
                    if cut < 0:
                        rest = data
                        continue
                    data, rest = data[: cut + 1], data[cut + 1:]
                else:
                    data, rest = rest, b""
                found = _VERTEX_RE.findall(data)
                if found:
                    arr = np.concatenate([carry, np.array(found, dtype="S").astype(np.float64).reshape(-1, 3)])
                    k = (len(arr) // 3) * 3
                    carry = arr[k:]
                    yield arr[:k].reshape(-1, 3, 3), read / max(1, size)
                if not block:
                    break


def compute_bounds(path, R, t, progress=None, cancel=None):
    """Exakte Begrenzungsbox und Volumenschwerpunkt nach v' = R·v + t."""
    R = np.asarray(R, float)
    t = np.asarray(t, float)
    vmin = np.full(3, np.inf)
    vmax = np.full(3, -np.inf)
    vol = 0.0
    csum = np.zeros(3)
    for tris, frac in iter_triangles(path, cancel):
        p = tris.reshape(-1, 3) @ R.T + t
        with np.errstate(invalid="ignore"):
            vmin = np.fmin(vmin, np.nanmin(p, axis=0))
            vmax = np.fmax(vmax, np.nanmax(p, axis=0))
        p = p.reshape(-1, 3, 3)
        # Schwerpunkt: Kegel/Tetraeder zum Ursprung (relativ zur Boxmitte für Genauigkeit)
        v0, v1, v2 = p[:, 0], p[:, 1], p[:, 2]
        dv = np.einsum("ij,ij->i", v0, np.cross(v1, v2)) / 6.0
        dv = np.nan_to_num(dv)
        vol += dv.sum()
        csum += (dv[:, None] * np.nan_to_num(v0 + v1 + v2)).sum(0) / 4.0
        if progress:
            progress(int(frac * 100), "Ermittle exakte Maße …")
    centroid = csum / vol if abs(vol) > 1e-30 else None
    return dict(min=vmin, max=vmax, center=(vmin + vmax) / 2, centroid=centroid, volume=abs(vol))


# --------------------------------------------------------------------------
# Speichern (gestreamt, Struktur/Größe unverändert)
# --------------------------------------------------------------------------
def save_transformed(src: str, dst: str, R: np.ndarray, t: np.ndarray,
                     progress=None, cancel=None) -> None:
    """Schreibt src nach dst mit v' = R·v + t, n' = R·n.

    Es wird zuerst in eine temporäre Datei neben dst geschrieben und diese
    danach atomar umbenannt -> auch Überschreiben der Quelldatei ist sicher.
    """
    R = clean_rotation(R)
    t = np.asarray(t, dtype=np.float64)
    fmt, count = detect_format(src)
    tmp = dst + ".teil"
    try:
        if fmt == "binary":
            _save_binary(src, tmp, count, R, t, progress, cancel)
        else:
            _save_ascii(src, tmp, R, t, progress, cancel)
        if os.path.getsize(tmp) != os.path.getsize(src) and fmt == "binary":
            raise IOError("Interner Fehler: Dateigröße weicht ab – Speichern abgebrochen.")
        os.replace(tmp, dst)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def _save_binary(src, tmp, count, R, t, progress, cancel):
    RT = R.T
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        fo.write(fi.read(84))  # Header + Anzahl unverändert
        done = 0
        while done < count:
            if cancel and cancel():
                raise Abgebrochen()
            n = min(CHUNK_TRIS, count - done)
            buf = np.fromfile(fi, dtype=BIN_DTYPE, count=n)
            if len(buf) != n:
                raise ValueError("Quelldatei ist kürzer als im Header angegeben.")
            v = buf["v"].reshape(-1, 3).astype(np.float64)
            buf["v"] = (v @ RT + t).astype("<f4").reshape(-1, 3, 3)
            nn = buf["n"].astype(np.float64)
            buf["n"] = (nn @ RT).astype("<f4")
            # Attribut-Bytes ("a") bleiben unangetastet
            buf.tofile(fo)
            done += n
            if progress:
                progress(int(done * 100 / max(1, count)), f"Schreibe Dreiecke … {done:,} / {count:,}".replace(",", "."))
        shutil.copyfileobj(fi, fo, 16 * 1024 * 1024)  # evtl. Zusatzdaten am Ende


_NUM_RE = re.compile(rb"^([+-]?)(\d*)(?:\.(\d*))?(?:([eEdD])([+-]?)(\d+))?$")
_LINE_RE = re.compile(rb"^(\s*)(facet\s+normal|vertex)(\s+)(\S+)(\s+)(\S+)(\s+)(\S+)(.*)$", re.I | re.S)


def format_like(tok: bytes, x: float) -> bytes:
    """Formatiert x im selben Stil wie das Original-Token tok."""
    m = _NUM_RE.match(tok)
    if not m:
        return b"%e" % x
    sign, _ip, frac, e, esign, edig = m.groups()
    dec = len(frac) if frac is not None else 0
    if x == 0.0:
        x = 0.0  # -0.0 vermeiden
    if e:
        s = b"%.*e" % (dec, x)
        mant, ex = s.split(b"e")
        exs, exd = ex[:1], ex[1:].lstrip(b"0") or b"0"
        exd = exd.rjust(len(edig), b"0")
        if not esign and exs == b"+":
            exs = b""
        if frac is not None and dec == 0:
            mant += b"."
        out = mant + e + exs + exd
    else:
        out = b"%.*f" % (dec, x)
        if frac is not None and dec == 0:
            out += b"."
    if sign == b"+" and not out.startswith(b"-"):
        out = b"+" + out
    return out


def _keep_length(seps, diff):
    """Gleicht Längenänderungen (z. B. ein neues Minuszeichen) über Leerraum aus,
    damit die Datei bytegenau gleich groß bleibt.

    seps = [Einrückung, Trenner1, Trenner2, Trenner3]. Gibt (seps, Rest) zurück;
    ein nicht ausgleichbarer Rest wird in den folgenden Zeilen ausgeglichen."""
    seps = list(seps)
    if diff < 0:
        seps[1] = seps[1] + b" " * (-diff)
        return seps, 0
    for i in (3, 2, 1, 0):
        while diff > 0 and len(seps[i]) > 1:
            seps[i] = seps[i][:-1]
            diff -= 1
    return seps, diff


def _save_ascii(src, tmp, R, t, progress, cancel):
    size = os.path.getsize(src)
    written = 0
    last_pct = -1
    debt = 0  # noch auszugleichende Bytes (für identische Dateigröße)
    with open(src, "rb") as fi, open(tmp, "wb", buffering=16 * 1024 * 1024) as fo:
        for i, line in enumerate(fi):
            written += len(line)
            body = line.rstrip(b"\r\n")
            ending = line[len(body):]
            m = _LINE_RE.match(body)
            if m:
                ind, kw, s1, a, s2, b, s3, c, tail = m.groups()
                try:
                    vec = np.array([float(a.replace(b"d", b"e").replace(b"D", b"e")),
                                    float(b.replace(b"d", b"e").replace(b"D", b"e")),
                                    float(c.replace(b"d", b"e").replace(b"D", b"e"))])
                except ValueError:
                    fo.write(line)
                    continue
                if kw.lower().startswith(b"vertex"):
                    r = R @ vec + t
                else:
                    r = R @ vec
                na, nb, nc = format_like(a, r[0]), format_like(b, r[1]), format_like(c, r[2])
                diff = (len(na) + len(nb) + len(nc)) - (len(a) + len(b) + len(c)) + debt
                seps, debt = _keep_length([ind, s1, s2, s3], diff)
                line = seps[0] + kw + seps[1] + na + seps[2] + nb + seps[3] + nc + tail + ending
            fo.write(line)
            if i % 200_000 == 0:
                if cancel and cancel():
                    raise Abgebrochen()
                pct = int(written * 100 / max(1, size))
                if progress and pct != last_pct:
                    last_pct = pct
                    progress(pct, "Schreibe ASCII-STL …")
    if progress:
        progress(100, "Fertig")
