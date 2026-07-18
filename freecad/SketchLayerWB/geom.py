# SPDX-License-Identifier: MIT
"""Pure vector/plane geometry for SketchLayer.

No FreeCADGui, no Coin, no Qt -- only FreeCAD.Vector math, so every function
here is exercisable under plain ``freecadcmd`` in the headless regression.

A "plane" throughout this module is a simple immutable basis:
    origin : App.Vector   -- a point on the plane
    u, v   : App.Vector   -- orthonormal in-plane axes ("red"/"green")
    normal : App.Vector   -- u x v ("blue")
Local plane coordinates (pu, pv) map to world as origin + pu*u + pv*v.
"""
import math

import FreeCAD as App


class Plane(object):
    """An orthonormal drawing plane (origin + u,v,normal)."""

    def __init__(self, origin, u, v):
        # NB: use only App.Vector operators (+, -, *) and .cross/.dot, never
        # the in-place-mutating .multiply()/.scale() methods.
        self.origin = App.Vector(origin)
        uu = App.Vector(u)
        self.u = uu * (1.0 / uu.Length)
        # Re-orthonormalize v against u (Gram-Schmidt) so callers can pass a
        # rough second axis (e.g. straight from a face) without skew.
        vv = App.Vector(v)
        vv = vv - self.u * vv.dot(self.u)
        self.v = vv * (1.0 / vv.Length)
        self.normal = self.u.cross(self.v)

    @staticmethod
    def xy():
        return Plane((0, 0, 0), (1, 0, 0), (0, 1, 0))

    def to_local(self, point):
        """World point -> (pu, pv) in-plane coordinates (projected)."""
        d = App.Vector(point).sub(self.origin)
        return d.dot(self.u), d.dot(self.v)

    def to_world(self, pu, pv):
        return self.origin + self.u * pu + self.v * pv

    def project(self, point):
        """Orthogonally project a world point onto the plane."""
        pu, pv = self.to_local(point)
        return self.to_world(pu, pv)


def distance(a, b):
    return App.Vector(a).sub(App.Vector(b)).Length


def ray_plane_intersection(plane, ray_origin, ray_dir):
    """World intersection of a pick ray with ``plane`` (or None if parallel).
    ``ray_dir`` need not be normalized."""
    ro = App.Vector(ray_origin)
    rd = App.Vector(ray_dir)
    denom = rd.dot(plane.normal)
    if abs(denom) < 1e-12:
        return None
    t = plane.origin.sub(ro).dot(plane.normal) / denom
    return ro + rd * t


def plane_from_face(face):
    """Build a :class:`Plane` from a planar OCCT face: origin at its centre
    of mass, U/V from the face's own parametric axes, normal outward. Returns
    None if the face is not planar."""
    try:
        surf = face.Surface
        if surf.__class__.__name__ != "Plane":
            return None
        n = face.normalAt(0, 0)
        if str(face.Orientation) == "Reversed":
            n = n * -1.0
        # a stable in-plane U axis: project global X, fall back to global Y
        gx = App.Vector(1, 0, 0)
        u = gx - n * gx.dot(n)
        if u.Length < 1e-6:
            gy = App.Vector(0, 1, 0)
            u = gy - n * gy.dot(n)
        origin = face.CenterOfMass
        # v = n x u so that Plane's computed normal (u x v) equals n.
        return Plane(origin, u, n.cross(u))
    except Exception:
        return None


def points_coplanar(points, tol=1e-6):
    """True if all points lie on a common plane (or there are < 4)."""
    pts = [App.Vector(p) for p in points]
    if len(pts) < 4:
        return True
    o = pts[0]
    # find two independent in-plane directions
    e1 = None
    for p in pts[1:]:
        d = p.sub(o)
        if d.Length > tol:
            e1 = d.normalize()
            break
    if e1 is None:
        return True
    n = None
    for p in pts[1:]:
        d = p.sub(o)
        c = e1.cross(d)
        if c.Length > tol:
            n = c.normalize()
            break
    if n is None:
        return True  # all colinear
    return all(abs(p.sub(o).dot(n)) <= 1e-4 for p in pts)


def polygon_is_closed(points, tol=1e-6):
    return len(points) >= 3 and distance(points[0], points[-1]) <= tol


def rectangle_corners(plane, corner_a, corner_b):
    """Given two opposite corners (world points, snapped onto ``plane``),
    return the 4 world corners of the axis-aligned (in plane u/v) rectangle,
    counter-clockwise, closed loop NOT included (4 distinct corners)."""
    ua, va = plane.to_local(corner_a)
    ub, vb = plane.to_local(corner_b)
    return [
        plane.to_world(ua, va),
        plane.to_world(ub, va),
        plane.to_world(ub, vb),
        plane.to_world(ua, vb),
    ]


def circle_through_3pt(p1, p2, p3):
    """Circumcircle of three world points. Returns
    ``(center, radius, normal)`` or None when the points are coincident or
    collinear (no finite circle passes through them)."""
    a = App.Vector(p1)
    u = App.Vector(p2).sub(a)
    v = App.Vector(p3).sub(a)
    if u.Length < 1e-12 or v.Length < 1e-12:
        return None
    n = u.cross(v)
    # collinear when the parallelogram area vanishes relative to the sides
    if n.Length <= 1e-12 * max(u.Length, v.Length) ** 2:
        return None
    # circumcenter of the triangle a, a+u, a+v
    center = a + (v.cross(n) * (u.Length ** 2) + n.cross(u) * (v.Length ** 2)) \
        * (1.0 / (2.0 * n.Length ** 2))
    return center, distance(center, a), n * (1.0 / n.Length)


def regular_polygon_corners(plane, center, radius_point, sides):
    """World corners of the regular N-gon on ``plane`` with circumradius
    ``|center -> radius_point|`` and one vertex at ``radius_point``."""
    cu, cv = plane.to_local(center)
    pu, pv = plane.to_local(radius_point)
    du, dv = pu - cu, pv - cv
    radius = math.hypot(du, dv)
    a0 = math.atan2(dv, du)
    return [
        plane.to_world(
            cu + radius * math.cos(a0 + 2.0 * math.pi * k / sides),
            cv + radius * math.sin(a0 + 2.0 * math.pi * k / sides))
        for k in range(sides)
    ]


def circle_band_points(plane, center, radius, segments=48):
    """Closed polyline approximation of a circle (``segments`` + 1 points),
    for the HUD rubber band only -- the committed shape is a true circle."""
    cu, cv = plane.to_local(center)
    return [
        plane.to_world(
            cu + radius * math.cos(2.0 * math.pi * k / segments),
            cv + radius * math.sin(2.0 * math.pi * k / segments))
        for k in range(segments + 1)
    ]


def arc_band_points(p1, p2, p3, segments=32):
    """Polyline approximation of the arc from ``p1`` through ``p2`` to
    ``p3`` (``segments`` + 1 points), or None when the three points define
    no circle (collinear). For the HUD rubber band only."""
    circ = circle_through_3pt(p1, p2, p3)
    if circ is None:
        return None
    center, radius, normal = circ
    e1 = App.Vector(p1).sub(center)
    e1 = e1 * (1.0 / e1.Length)
    e2 = normal.cross(e1)

    def angle_of(p):
        d = App.Vector(p).sub(center)
        a = math.atan2(d.dot(e2), d.dot(e1))
        return a if a >= 0 else a + 2.0 * math.pi

    a2 = angle_of(p2)
    a3 = angle_of(p3)
    # sweep from p1 (angle 0) to p3 passing through p2
    sweep = a3 if a2 <= a3 else a3 - 2.0 * math.pi
    return [
        center + e1 * (radius * math.cos(sweep * k / segments))
        + e2 * (radius * math.sin(sweep * k / segments))
        for k in range(segments + 1)
    ]


def signed_angle(plane, from_dir, to_dir):
    """Signed angle (radians, -pi..pi) from ``from_dir`` to ``to_dir``
    measured in the plane (positive = u->v sense)."""
    fu, fv = from_dir.dot(plane.u), from_dir.dot(plane.v)
    tu, tv = to_dir.dot(plane.u), to_dir.dot(plane.v)
    a0 = math.atan2(fv, fu)
    a1 = math.atan2(tv, tu)
    d = a1 - a0
    while d > math.pi:
        d -= 2 * math.pi
    while d < -math.pi:
        d += 2 * math.pi
    return d
