# SPDX-License-Identifier: MIT
"""Drawing-relative inference resolver -- the piece Draft's snapper does NOT
provide: SketchUp-style, colored, *relative-to-what-you-are-drawing*
inference (on-axis, parallel/perpendicular to the last segment, endpoint of
the path so you can close it), plus midpoint/center snapping with
SketchUp's cyan cue.

Object snapping proper (endpoint/midpoint/perpendicular *of existing model
geometry*, all 16 modes) is still delegated to ``FreeCADGui.Snapper`` in the
live GUI (see commands.py); this module adds a small, deterministic subset of
it -- midpoints of straight edges and centers of circular edges -- so the
colored midpoint/center cue also works headless and stays consistent with the
path-relative categories. Everything here is pure geometry, so it is
unit-tested headlessly.

``resolve()`` returns an :class:`Inference` (category + possibly axis-locked
point + an optional guide segment for the HUD to draw + an RGB color).
"""
import math

import FreeCAD as App

from . import geom

# Category constants (also used as HUD color keys).
FREE = "free"
ENDPOINT = "endpoint"        # near an existing path vertex (start = close)
MIDPOINT = "midpoint"        # on the midpoint of a segment (path or doc edge)
CENTER = "center"            # on the center of a circular document edge
ON_AXIS_U = "axis_u"         # aligned to plane U ("red" axis)
ON_AXIS_V = "axis_v"         # aligned to plane V ("green" axis)
PARALLEL = "parallel"        # parallel to previous segment
PERPENDICULAR = "perpendicular"

# SketchUp-like colors (0..1 RGB). U=red, V=green, parallel/perp=magenta,
# endpoint=green dot, midpoint=cyan (SketchUp's midpoint color), center=a
# distinct steel blue. Kept here so hud.py and any test can share them.
COLORS = {
    ENDPOINT: (0.10, 0.85, 0.10),
    MIDPOINT: (0.00, 0.85, 0.85),
    CENTER: (0.30, 0.55, 0.95),
    ON_AXIS_U: (0.90, 0.15, 0.15),
    ON_AXIS_V: (0.15, 0.80, 0.15),
    PARALLEL: (0.85, 0.15, 0.85),
    PERPENDICULAR: (0.85, 0.15, 0.85),
    FREE: (0.55, 0.55, 0.55),
}

TOOLTIPS = {
    ENDPOINT: "Endpoint",
    MIDPOINT: "Midpoint",
    CENTER: "Center",
    ON_AXIS_U: "On red axis",
    ON_AXIS_V: "On green axis",
    PARALLEL: "Parallel",
    PERPENDICULAR: "Perpendicular",
    FREE: "",
}


class Inference(object):
    def __init__(self, category, point, guide=None):
        self.category = category
        self.point = point            # possibly axis-corrected world point
        self.guide = guide            # (from_world, to_world) or None
        self.color = COLORS.get(category, COLORS[FREE])
        self.tooltip = TOOLTIPS.get(category, "")

    def __repr__(self):
        return "Inference(%s)" % self.category


def _axis_lock(plane, base, cursor, axis, tol_rad):
    """If the direction base->cursor is within ``tol_rad`` of +/-``axis``,
    return the cursor projected onto the axis line through ``base``; else
    None."""
    d = cursor.sub(base)
    if d.Length < 1e-9:
        return None
    dn = d * (1.0 / d.Length)
    a = axis * (1.0 / axis.Length)
    cosang = abs(dn.dot(a))
    if cosang >= math.cos(tol_rad):
        t = d.dot(a)               # signed distance along axis
        return base + a * t
    return None


def collect_doc_edges(doc):
    """All edges of the Part::Feature shapes in ``doc`` (flat list).

    Best-effort and never raises: objects without a usable shape are
    skipped. Used to seed midpoint/center snapping onto existing model
    geometry; the GUI session collects this once at tool start (per-cursor
    rescans would be wasted work -- the document cannot change while the
    modal draw tool owns the view).
    """
    edges = []
    for obj in getattr(doc, "Objects", None) or []:
        try:
            if not obj.isDerivedFrom("Part::Feature"):
                continue
            shape = obj.Shape
        except Exception:  # noqa: BLE001 - skip broken/foreign objects
            continue
        edges.extend(getattr(shape, "Edges", None) or [])
    return edges


def snap_candidates(points=(), doc_edges=None):
    """``(midpoints, centers)`` world-point lists for midpoint/center
    snapping.

    Midpoints: every segment of the in-progress path (consecutive committed
    vertices) plus the midpoint of every straight document edge (OCCT
    ``Line``/``LineSegment`` curve; the centre of mass of a straight edge is
    its midpoint). Centers: the centre of every circular document edge
    (full ``Circle`` or ``ArcOfCircle``). Anything else (B-splines,
    ellipses) yields no candidates -- v1 scope, matching the README's known
    gaps.
    """
    midpoints = []
    pts = [App.Vector(p) for p in points or []]
    for a, b in zip(pts, pts[1:]):
        midpoints.append((a + b) * 0.5)
    centers = []
    for edge in doc_edges or []:
        try:
            curve = edge.Curve
        except Exception:  # noqa: BLE001
            continue
        cname = curve.__class__.__name__
        if cname in ("Line", "LineSegment"):
            midpoints.append(edge.CenterOfMass)
        elif cname in ("Circle", "ArcOfCircle"):
            centers.append(App.Vector(curve.Center))
    return midpoints, centers


def _nearest_snap(points, doc_edges, cursor, radius):
    """The strongest midpoint/center candidate within ``radius`` of
    ``cursor``: ``(category, point)`` or None. The nearest candidate wins;
    midpoint is preferred on an exact tie."""
    midpoints, centers = snap_candidates(points, doc_edges)
    best = None  # (distance, rank, category, point); rank breaks ties
    for pt in midpoints:
        d = geom.distance(cursor, pt)
        if d <= radius and (best is None or (d, 0) < (best[0], best[1])):
            best = (d, 0, MIDPOINT, pt)
    for pt in centers:
        d = geom.distance(cursor, pt)
        if d <= radius and (best is None or (d, 1) < (best[0], best[1])):
            best = (d, 1, CENTER, pt)
    if best is None:
        return None
    return best[2], best[3]


def resolve(plane, points, cursor, tol_deg=6.0, endpoint_px_world=None,
            doc_edges=None, doc=None):
    """Compute the strongest inference for ``cursor`` given the in-progress
    path ``points`` (list of world vertices already placed) on ``plane``.

    Priority (SketchUp-like): endpoint(close) > midpoint/center > axis >
    parallel/perp > free.
    ``endpoint_px_world`` is the world-space radius under which the cursor is
    considered "on" an existing vertex (caller passes a value derived from a
    few screen pixels; tests pass an explicit number). The same radius gates
    midpoint/center snapping, so a midpoint only fires when the cursor is
    genuinely near it.

    ``doc_edges``: optional edges of existing model geometry (see
    :func:`snap_candidates`); ``doc``: optional document to scan instead
    (via :func:`collect_doc_edges`). Midpoints of the in-progress path's own
    segments are always candidates.
    """
    cursor = App.Vector(cursor)
    tol = math.radians(tol_deg)
    if doc_edges is None and doc is not None:
        doc_edges = collect_doc_edges(doc)

    # 1) endpoint / close-the-loop snapping to existing vertices.
    if points and endpoint_px_world:
        # Prefer the start point (closing the loop) over intermediate ones.
        ordered = [points[0]] + list(points[1:])
        for vtx in ordered:
            if geom.distance(cursor, vtx) <= endpoint_px_world:
                return Inference(ENDPOINT, App.Vector(vtx), guide=None)

    # 2) midpoint / center snapping (path segments + document edges).
    if endpoint_px_world:
        snap = _nearest_snap(points, doc_edges, cursor, endpoint_px_world)
        if snap is not None:
            category, pt = snap
            # Document-edge candidates can sit off the working plane; the
            # draw tools are plane-locked, so return the on-plane footprint
            # (committing a raw off-plane midpoint would break the line
            # tool's coplanar close and tilt circles/arcs out of the plane).
            return Inference(category, plane.project(pt), guide=None)

    if not points:
        return Inference(FREE, cursor)

    base = App.Vector(points[-1])

    # 3) working-plane axis inference (red = U, green = V).
    locked_u = _axis_lock(plane, base, cursor, plane.u, tol)
    locked_v = _axis_lock(plane, base, cursor, plane.v, tol)
    # If both fire (cursor almost on top of base), pick the nearer axis.
    if locked_u is not None and locked_v is not None:
        du = geom.distance(cursor, locked_u)
        dv = geom.distance(cursor, locked_v)
        if du <= dv:
            locked_v = None
        else:
            locked_u = None
    if locked_u is not None:
        far = base + plane.u * (plane.u.dot(locked_u.sub(base)))
        return Inference(ON_AXIS_U, locked_u, guide=(base, far))
    if locked_v is not None:
        far = base + plane.v * (plane.v.dot(locked_v.sub(base)))
        return Inference(ON_AXIS_V, locked_v, guide=(base, far))

    # 4) parallel / perpendicular to the previous drawn segment.
    if len(points) >= 2:
        prev = base.sub(App.Vector(points[-2]))
        if prev.Length > 1e-9:
            par = _axis_lock(plane, base, cursor, prev, tol)
            if par is not None:
                return Inference(PARALLEL, par, guide=(base, par))
            perp = plane.normal.cross(prev)
            perpn = _axis_lock(plane, base, cursor, perp, tol)
            if perpn is not None:
                return Inference(PERPENDICULAR, perpn, guide=(base, perpn))

    # 5) nothing inferred.
    return Inference(FREE, cursor)
