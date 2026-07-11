# SPDX-License-Identifier: MIT
"""Turn a set of drawn coplanar points into a real FreeCAD shape.

The v1 output contract is a STANDALONE planar face -- a plain
``Part::Feature`` whose ``Shape`` is a single ``Part.Face`` -- exactly the
"loose face" a SketchUp user draws and then pushes/pulls into a solid. It
carries no toponaming baggage and no constrained Sketch object; the
companion PushPull addon's ``Part::Extrude`` path consumes it directly.

Kept import-light (only FreeCAD + Part) so it runs under plain freecadcmd.
"""
import FreeCAD as App
import Part

from . import geom


class BuildError(Exception):
    """Raised when the drawn points cannot form a valid planar face/wire."""


def _closed_world_points(points):
    pts = [App.Vector(p) for p in points]
    if len(pts) >= 2 and geom.distance(pts[0], pts[-1]) <= 1e-6:
        pts = pts[:-1]            # drop an explicit duplicate closing vertex
    return pts


def make_face_shape(points):
    """Build a single planar ``Part.Face`` from >=3 coplanar world points.
    Returns the Part.Face; raises :class:`BuildError` on degenerate input."""
    pts = _closed_world_points(points)
    if len(pts) < 3:
        raise BuildError("Need at least 3 points to make a face.")
    if not geom.points_coplanar(pts):
        raise BuildError("Drawn points are not coplanar; cannot make a flat face.")
    # close the loop for the polygon
    loop = pts + [pts[0]]
    try:
        wire = Part.makePolygon([App.Vector(p) for p in loop])
        face = Part.Face(wire)
    except Exception as exc:  # noqa: BLE001 - surface a friendly message
        raise BuildError("Could not build a face from these points: %s" % exc)
    if not face.isValid() or face.Area <= 1e-9:
        raise BuildError("Resulting face is invalid or has zero area.")
    return face


def make_wire_shape(points, closed):
    """Build an (open or closed) ``Part.Wire`` from >=2 points -- used when
    the path is not (yet) a closed face."""
    pts = _closed_world_points(points) if closed else [App.Vector(p) for p in points]
    if len(pts) < 2:
        raise BuildError("Need at least 2 points to make a wire.")
    loop = pts + [pts[0]] if closed else pts
    try:
        return Part.makePolygon([App.Vector(p) for p in loop])
    except Exception as exc:  # noqa: BLE001
        raise BuildError("Could not build a wire: %s" % exc)


def add_face_object(doc, points, name="SketchLayerFace"):
    """Create and return a ``Part::Feature`` document object holding the
    planar face built from ``points``. Caller is responsible for
    ``doc.recompute()`` if it wants the shape realized immediately (this
    function does recompute so the returned object's Shape is populated)."""
    face = make_face_shape(points)
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = face
    doc.recompute()
    return obj


def add_wire_object(doc, points, closed, name="SketchLayerWire"):
    wire = make_wire_shape(points, closed)
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = wire
    doc.recompute()
    return obj
