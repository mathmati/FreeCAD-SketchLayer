# SPDX-License-Identifier: MIT
"""Drawing-relative inference resolver -- the piece Draft's snapper does NOT
provide: SketchUp-style, colored, *relative-to-what-you-are-drawing*
inference (on-axis, parallel/perpendicular to the last segment, endpoint of
the path so you can close it).

This is deliberately NOT a re-implementation of Draft's object snapping
(endpoint/midpoint/perpendicular *of existing model geometry*). In the live
GUI, object snapping is delegated to ``FreeCADGui.Snapper`` (see commands.py)
so we do not reinvent Draft's 16 snap modes. What this module adds is the
inference that is relative to the current in-progress path and the working
plane's own axes -- which is exactly the SketchUp "feel" that is missing --
and it is pure geometry, so it is unit-tested headlessly.

``resolve()`` returns an :class:`Inference` (category + possibly axis-locked
point + an optional guide segment for the HUD to draw + an RGB color).
"""
import math

import FreeCAD as App

from . import geom

# Category constants (also used as HUD color keys).
FREE = "free"
ENDPOINT = "endpoint"        # near an existing path vertex (start = close)
ON_AXIS_U = "axis_u"         # aligned to plane U ("red" axis)
ON_AXIS_V = "axis_v"         # aligned to plane V ("green" axis)
PARALLEL = "parallel"        # parallel to previous segment
PERPENDICULAR = "perpendicular"

# SketchUp-like colors (0..1 RGB). U=red, V=green, parallel/perp=magenta,
# endpoint=green dot. Kept here so hud.py and any test can share them.
COLORS = {
    ENDPOINT: (0.10, 0.85, 0.10),
    ON_AXIS_U: (0.90, 0.15, 0.15),
    ON_AXIS_V: (0.15, 0.80, 0.15),
    PARALLEL: (0.85, 0.15, 0.85),
    PERPENDICULAR: (0.85, 0.15, 0.85),
    FREE: (0.55, 0.55, 0.55),
}

TOOLTIPS = {
    ENDPOINT: "Endpoint",
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


def resolve(plane, points, cursor, tol_deg=6.0, endpoint_px_world=None):
    """Compute the strongest inference for ``cursor`` given the in-progress
    path ``points`` (list of world vertices already placed) on ``plane``.

    Priority (SketchUp-like): endpoint(close) > axis > parallel/perp > free.
    ``endpoint_px_world`` is the world-space radius under which the cursor is
    considered "on" an existing vertex (caller passes a value derived from a
    few screen pixels; tests pass an explicit number).
    """
    cursor = App.Vector(cursor)
    tol = math.radians(tol_deg)

    # 1) endpoint / close-the-loop snapping to existing vertices.
    if points and endpoint_px_world:
        # Prefer the start point (closing the loop) over intermediate ones.
        ordered = [points[0]] + list(points[1:])
        for vtx in ordered:
            if geom.distance(cursor, vtx) <= endpoint_px_world:
                return Inference(ENDPOINT, App.Vector(vtx), guide=None)

    if not points:
        return Inference(FREE, cursor)

    base = App.Vector(points[-1])

    # 2) working-plane axis inference (red = U, green = V).
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

    # 3) parallel / perpendicular to the previous drawn segment.
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

    # 4) nothing inferred.
    return Inference(FREE, cursor)
