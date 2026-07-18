# SPDX-License-Identifier: MIT
"""verify/headless_regression.py -- SketchLayer headless regression (freecadcmd).

Run from the repo root:

    freecadcmd verify/headless_regression.py

Exit code 0 and a final "46/46 checks pass" line when green.

What this is: the headless half of the SketchLayer README's "Verification"
section -- "a headless (freecadcmd) regression exercises the inference
resolver, face builder, and the full draw state machine including typed
dimensions and the coplanarity guard". It drives the Gui-decoupled
``DrawController`` by method call, the same object the mouse/keyboard
callbacks drive: "the user moved the cursor to P" is ``move_to(P)``,
"clicked" is ``add_point()``, "typed 12,8 and pressed Enter" is
``type_char(...)`` + ``key_return()``, "pressed the Right arrow" is
``set_axis_lock("u")`` / ``toggle_axis_lock("u")``.

What is deliberately NOT here (it is headless-infeasible, see the GUI drivers
in verify/drivers/ and verify/README.md for the mapping):

  * the pixel-level HUD color checks (needs a rendered 3D view -> Xvfb GUI,
    drivers/hud_color_driver.py);
  * workbench/command auto-registration (needs FreeCADGui ->
    drivers/draw_commit_driver.py);
  * Draft Snapper integration (needs the GUI snapper; not claimed headless).

Checks (one shared document; order matters):
   inference resolver  1-10   axis/parallel/perpendicular/endpoint/free,
                              priority rules, axis-lock positions, colors
   face builder        11-15  area, degenerate + non-coplanar guards,
                              duplicate closing vertex, document object
   draw state machine  16-24  rectangle by clicks, typed W,H, polyline close
                              by endpoint click + by Enter, typed line length,
                              cancel, controller-level BuildError path,
                              typed-buffer rules, dimension parser
   circle              25-29  drag radius exact (area pi r^2, planar), typed
                              radius exact, zero-radius refusal, cancel, live
                              band preview ring
   polygon             30-34  default hexagon (6 vertices, exact area),
                              typed '8s' mid-tool, typed circumradius, sides
                              buffer rules, cancel
   arc                 35-36  3-point arc center/radius/on-curve/length
                              exact (edge, not a face), collinear refusal +
                              cancel
   midpoint/center     37-40  segment midpoint fires (cyan, beats the axis),
                              no fire off-midpoint, circle-edge center snap
                              (explicit edges + doc scan), endpoint priority
   axis lock           41-44  U/V projection of an off-axis cursor, toggle +
                              unlock semantics, typed length along the lock
   toolstate hook      45-46  the Uppercut highlight hook no-ops cleanly when
                              freecad.UppercutWB is not importable (import
                              blocked in sys.modules; the draw controller is
                              untouched either way), and fires mark_active /
                              mark_inactive through a fake toolstate; static
                              xref of the hook call sites in commands.py
"""
import math
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
try:
    import freecad  # FreeCAD's own namespace package (present under freecadcmd)
    freecad.__path__ = [os.path.join(_REPO_ROOT, "freecad")] + list(freecad.__path__)
except ImportError:  # extremely defensive: fall back to plain sys.path
    sys.path.insert(0, _REPO_ROOT)

# freecadcmd imports installed Mod addons at startup; if a SketchLayerWB copy
# is installed, freecad.SketchLayerWB is then already in sys.modules and the
# repo path prepend above would be ignored. Drop the cached package so the
# checks always run against THIS checkout.
for _mod in list(sys.modules):
    if _mod == "freecad.SketchLayerWB" or _mod.startswith("freecad.SketchLayerWB."):
        del sys.modules[_mod]

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from freecad.SketchLayerWB import geom  # noqa: E402
from freecad.SketchLayerWB import inference as infer  # noqa: E402
from freecad.SketchLayerWB import facebuilder  # noqa: E402
from freecad.SketchLayerWB import draw_controller  # noqa: E402
from freecad.SketchLayerWB import toolstate_hook  # noqa: E402

DrawController = draw_controller.DrawController
MODE_LINE = draw_controller.MODE_LINE
MODE_RECT = draw_controller.MODE_RECT
MODE_CIRCLE = draw_controller.MODE_CIRCLE
MODE_POLYGON = draw_controller.MODE_POLYGON
MODE_ARC = draw_controller.MODE_ARC

EXPECTED_CHECKS = 46
V = App.Vector

_checks = []


def check(name):
    def deco(fn):
        _checks.append((name, fn))
        return fn
    return deco


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def approx(a, b, tol, msg):
    if abs(a - b) > tol:
        raise AssertionError("%s (got %r, want %r +/- %r)" % (msg, a, b, tol))


def vec_approx(a, b, tol, msg):
    approx(geom.distance(a, b), 0.0, tol, msg)


def is_planar_face(face):
    """PushPullWB-style planar validation: the face's underlying surface is
    a plane. (FreeCAD 1.1.1's Part.Face has no isPlanar(); checking the
    Surface type is the idiom geom.plane_from_face already uses.)"""
    try:
        return face.Surface.__class__.__name__ == "Plane"
    except Exception:  # noqa: BLE001
        return False


class Fixture(object):
    def __init__(self):
        self.doc = App.newDocument("SketchLayerVerify")
        self.plane = geom.Plane.xy()


# --- 1-10: inference resolver ----------------------------------------------
@check("inference: no points yet -> FREE (gray, no lock)")
def c01(fx):
    inf = infer.resolve(fx.plane, [], V(5, 5, 0))
    ok(inf.category == infer.FREE, "category is %s" % inf.category)
    ok(inf.color == infer.COLORS[infer.FREE], "FREE color mismatch")
    vec_approx(inf.point, V(5, 5, 0), 1e-9, "FREE point should pass through")


@check("inference: near plane U axis -> ON_AXIS_U, locked onto the axis, red")
def c02(fx):
    inf = infer.resolve(fx.plane, [V(0, 0, 0)], V(10, 0.3, 0))
    ok(inf.category == infer.ON_AXIS_U, "category is %s" % inf.category)
    vec_approx(inf.point, V(10, 0, 0), 1e-6, "point not axis-locked")
    ok(inf.color == infer.COLORS[infer.ON_AXIS_U], "axis_u color mismatch")
    ok(inf.guide is not None, "axis inference should carry a guide segment")
    vec_approx(inf.guide[0], V(0, 0, 0), 1e-9, "guide starts at the base point")


@check("inference: near plane V axis -> ON_AXIS_V, locked onto the axis, green")
def c03(fx):
    inf = infer.resolve(fx.plane, [V(0, 0, 0)], V(0.3, 10, 0))
    ok(inf.category == infer.ON_AXIS_V, "category is %s" % inf.category)
    vec_approx(inf.point, V(0, 10, 0), 1e-6, "point not axis-locked")
    ok(inf.color == infer.COLORS[infer.ON_AXIS_V], "axis_v color mismatch")


@check("inference: outside the 6-degree cone -> FREE")
def c04(fx):
    inf = infer.resolve(fx.plane, [V(0, 0, 0)], V(10, 5, 0))  # ~26.6 degrees
    ok(inf.category == infer.FREE, "category is %s" % inf.category)


@check("inference: continuing the last segment -> PARALLEL (magenta)")
def c05(fx):
    inf = infer.resolve(fx.plane, [V(0, 0, 0), V(7, 7, 0)], V(14, 14.5, 0))
    ok(inf.category == infer.PARALLEL, "category is %s" % inf.category)
    ok(inf.color == infer.COLORS[infer.PARALLEL], "parallel color mismatch")


@check("inference: at right angles to the last segment -> PERPENDICULAR")
def c06(fx):
    inf = infer.resolve(fx.plane, [V(0, 0, 0), V(7, 7, 0)],
                        V(-0.071, 14.071, 0))  # base + 10 * (-u+v)/sqrt(2)
    ok(inf.category == infer.PERPENDICULAR, "category is %s" % inf.category)
    ok(inf.color == infer.COLORS[infer.PERPENDICULAR], "perp color mismatch")


@check("inference: near the start point -> ENDPOINT (green dot, no guide)")
def c07(fx):
    pts = [V(0, 0, 0), V(10, 0, 0), V(10, 10, 0)]
    inf = infer.resolve(fx.plane, pts, V(0.3, 0.2, 0), endpoint_px_world=1.0)
    ok(inf.category == infer.ENDPOINT, "category is %s" % inf.category)
    vec_approx(inf.point, pts[0], 1e-9, "endpoint should snap to the vertex")
    ok(inf.color == infer.COLORS[infer.ENDPOINT], "endpoint color mismatch")
    ok(inf.guide is None, "endpoint inference carries no guide")


@check("inference: ENDPOINT beats an axis lock (priority order)")
def c08(fx):
    # Cursor is both on the +U axis from the base AND within the endpoint
    # radius of the last vertex -- endpoint must win.
    inf = infer.resolve(fx.plane, [V(0, 0, 0), V(5, 0, 0)], V(5.2, 0.1, 0),
                        endpoint_px_world=1.0)
    ok(inf.category == infer.ENDPOINT, "category is %s" % inf.category)


@check("inference: both axes fire near the base -> nearer axis wins")
def c09(fx):
    # With the default 6-degree cone two perpendicular axes can never fire
    # together; a wide tolerance is needed to reach the "both fire" branch.
    inf = infer.resolve(fx.plane, [V(0, 0, 0)], V(0.2, 0.1, 0), tol_deg=64.0)
    ok(inf.category == infer.ON_AXIS_U, "category is %s" % inf.category)
    approx(inf.point.x, 0.2, 1e-9, "locked point keeps the U coordinate")
    approx(inf.point.y, 0.0, 1e-9, "locked point projected onto the U axis")


@check("inference: axis guide runs base -> projection along the axis")
def c10(fx):
    base = V(0, 0, 0)
    inf = infer.resolve(fx.plane, [base], V(10, 0.3, 0))
    a, b = inf.guide
    vec_approx(a, base, 1e-9, "guide origin")
    ok(abs(b.sub(base).cross(fx.plane.u).Length) < 1e-9,
       "guide end is not collinear with the U axis")


# --- 11-15: face builder -----------------------------------------------------
@check("facebuilder: 10x10 square -> valid Part.Face of area 100")
def c11(fx):
    face = facebuilder.make_face_shape(
        [V(0, 0, 0), V(10, 0, 0), V(10, 10, 0), V(0, 10, 0)])
    ok(face.isValid(), "face is invalid")
    approx(face.Area, 100.0, 1e-6, "face area")


@check("facebuilder: fewer than 3 points -> BuildError")
def c12(fx):
    try:
        facebuilder.make_face_shape([V(0, 0, 0), V(5, 0, 0)])
    except facebuilder.BuildError as exc:
        ok("at least 3" in str(exc), "unexpected message: %s" % exc)
        return
    raise AssertionError("2 points did not raise BuildError")


@check("facebuilder: the coplanarity guard rejects non-coplanar points")
def c13(fx):
    try:
        facebuilder.make_face_shape(
            [V(0, 0, 0), V(10, 0, 0), V(10, 10, 3), V(0, 10, 0)])
    except facebuilder.BuildError as exc:
        ok("coplanar" in str(exc).lower(), "unexpected message: %s" % exc)
        return
    raise AssertionError("non-coplanar points did not raise BuildError")


@check("facebuilder: an explicit duplicate closing vertex is tolerated")
def c14(fx):
    pts = [V(0, 0, 0), V(10, 0, 0), V(10, 10, 0), V(0, 10, 0), V(0, 0, 0)]
    face = facebuilder.make_face_shape(pts)
    approx(face.Area, 100.0, 1e-6, "face area with duplicate closer")


@check("facebuilder: add_face_object creates a Part::Feature in the document")
def c15(fx):
    before = len(fx.doc.Objects)
    obj = facebuilder.add_face_object(
        fx.doc, [V(0, 0, 0), V(10, 0, 0), V(10, 10, 0), V(0, 10, 0)])
    ok(len(fx.doc.Objects) == before + 1, "no object added")
    ok(obj.TypeId == "Part::Feature", "TypeId is %s" % obj.TypeId)
    approx(obj.Shape.Area, 100.0, 1e-6, "object face area")


# --- 16-24: draw state machine ----------------------------------------------
@check("draw: rectangle by two clicks -> face of the expected area (40)")
def c16(fx):
    ctl = DrawController(fx.doc)  # view=None -> headless, no HUD/VCB
    ctl.start(fx.plane, MODE_RECT)
    ok(ctl.active, "controller not active after start")
    ok(ctl.add_point(V(0, 0, 0)) is None, "first corner should not finish")
    ctl.move_to(V(8, 5, 0))
    obj = ctl.add_point()
    ok(obj is not None, "second corner did not commit: %s" % ctl.last_message)
    ok(obj.TypeId == "Part::Feature", "TypeId is %s" % obj.TypeId)
    approx(obj.Shape.Area, 40.0, 1e-6, "rectangle area")
    ok(not ctl.active, "controller still active after commit")
    ok(ctl.committed_object == obj, "committed_object not set")


@check("draw: typed 'W,H' + Enter -> rectangle of exact area (96)")
def c17(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_RECT)
    ctl.add_point(V(0, 0, 0))
    ctl.move_to(V(5, 5, 0))  # sets the (+,+) quadrant for the typed dims
    for ch in "12,8":
        ctl.type_char(ch)
    ok(ctl.typed_buffer == "12,8", "buffer is %r" % ctl.typed_buffer)
    obj = ctl.key_return()
    ok(obj is not None, "typed rect did not commit: %s" % ctl.last_message)
    approx(obj.Shape.Area, 96.0, 1e-6, "typed rectangle area")


@check("draw: polyline closes into a face by clicking the start point")
def c18(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE, endpoint_world=1.0)
    ctl.add_point(V(0, 0, 0))
    ctl.add_point(V(10, 0, 0))
    ctl.add_point(V(10, 10, 0))
    ctl.add_point(V(0, 10, 0))
    inf = ctl.move_to(V(0.2, 0.1, 0))
    ok(inf is not None and inf.category == infer.ENDPOINT,
       "expected ENDPOINT inference, got %s" % inf)
    obj = ctl.add_point()
    ok(obj is not None, "endpoint click did not close: %s" % ctl.last_message)
    approx(obj.Shape.Area, 100.0, 1e-6, "closed square area")
    ok(not ctl.active, "controller still active after close")


@check("draw: typed length places the next vertex exactly along the direction")
def c19(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.add_point(V(0, 0, 0))
    ctl.move_to(V(6, 0.2, 0))  # inside the U-axis cone -> direction +X
    ctl.type_char("1")
    ctl.type_char("5")
    before = len(fx.doc.Objects)
    ok(ctl.key_return() is None, "typed length must not finish the drawing")
    ok(len(ctl.points) == 2, "expected 2 points, got %d" % len(ctl.points))
    vec_approx(ctl.points[-1], V(15, 0, 0), 1e-9, "typed-length vertex")
    ok(len(fx.doc.Objects) == before, "typed length created a document object")
    ok(ctl.typed_buffer == "", "typed buffer not cleared after apply")


@check("draw: Enter with an open polyline closes it into a face (area 50)")
def c20(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.add_point(V(0, 0, 0))
    ctl.add_point(V(10, 0, 0))
    ctl.add_point(V(10, 10, 0))
    obj = ctl.key_return()  # empty buffer -> close at 3+ points
    ok(obj is not None, "Enter did not close the polyline: %s" % ctl.last_message)
    approx(obj.Shape.Area, 50.0, 1e-6, "triangle area")


@check("draw: Esc cancel mid-draw leaves the document untouched")
def c21(fx):
    before = len(fx.doc.Objects)
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_RECT)
    ctl.add_point(V(2, 2, 0))
    ctl.cancel()
    ok(not ctl.active, "controller still active after cancel")
    ok(ctl.committed_object is None, "cancel produced a committed object")
    ok(len(fx.doc.Objects) == before, "document changed after cancel")


@check("draw: a degenerate path surfaces a friendly message, not a crash")
def c22(fx):
    before = len(fx.doc.Objects)
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.points = [V(0, 0, 0), V(5, 0, 0)]  # simulate two placed vertices
    ok(ctl.close_path() is None, "degenerate path should not commit")
    ok("SketchLayer:" in ctl.last_message,
       "expected a friendly message, got %r" % ctl.last_message)
    ok(not ctl.active, "controller still active after failed close")
    ok(len(fx.doc.Objects) == before, "document changed after failed close")


@check("draw: typed-buffer rules (reject letters, no double dot, backspace)")
def c23(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.type_char("a")
    ok(ctl.typed_buffer == "", "letter accepted into buffer")
    for ch in "1.5":
        ctl.type_char(ch)
    ok(ctl.typed_buffer == "1.5", "buffer is %r" % ctl.typed_buffer)
    # draw_controller only blocks a dot when the buffer ENDS with one --
    # "1.5" + "." is accepted as "1.5.", a further "." is dropped.
    ctl.type_char(".")
    ok(ctl.typed_buffer == "1.5.", "buffer is %r" % ctl.typed_buffer)
    ctl.type_char(".")
    ok(ctl.typed_buffer == "1.5.", "consecutive dot not ignored: %r"
       % ctl.typed_buffer)
    ctl.key_backspace()
    ok(ctl.typed_buffer == "1.5", "backspace gave %r" % ctl.typed_buffer)
    ctl.cancel()


@check("draw: the dimension parser accepts '12,8' '12x8' '12*8' '12 8'")
def c24(fx):
    parse = draw_controller._parse_dims
    for text in ("12,8", "12x8", "12X8", "12*8", "12 8"):
        ok(parse(text) == [12.0, 8.0], "parse(%r) -> %r" % (text, parse(text)))
    ok(parse("12") == [12.0], "single value parse failed")
    ok(parse("abc") == [], "garbage should parse to []")
    ok(parse("") == [], "empty should parse to []")


# --- 25-29: circle --------------------------------------------------------------
@check("circle: center + drag -> planar face, radius exact, area pi r^2")
def c25(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_CIRCLE)
    ok(ctl.add_point(V(0, 0, 0)) is None, "center click should not finish")
    ctl.move_to(V(6, 8, 0))  # off-axis, no inference: radius exactly 10
    obj = ctl.add_point()
    ok(obj is not None, "second click did not commit: %s" % ctl.last_message)
    ok(obj.TypeId == "Part::Feature", "TypeId is %s" % obj.TypeId)
    ok(len(obj.Shape.Faces) == 1, "expected one face, got %d"
       % len(obj.Shape.Faces))
    approx(obj.Shape.Edges[0].Curve.Radius, 10.0, 1e-9, "circle radius")
    rel = abs(obj.Shape.Area - math.pi * 100.0) / (math.pi * 100.0)
    ok(rel <= 1e-6, "area off by %.3g (rel)" % rel)
    ok(is_planar_face(obj.Shape.Faces[0]), "circle face is not planar")
    ok(not ctl.active, "controller still active after commit")


@check("circle: typed radius + Enter -> exact radius, planar face")
def c26(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_CIRCLE)
    ctl.add_point(V(10, 10, 0))
    ctl.move_to(V(30, 12, 0))  # live radius ~20.05, typed value must win
    for ch in "25":
        ctl.type_char(ch)
    obj = ctl.key_return()
    ok(obj is not None, "typed circle did not commit: %s" % ctl.last_message)
    approx(obj.Shape.Edges[0].Curve.Radius, 25.0, 1e-9, "typed radius")
    rel = abs(obj.Shape.Area - math.pi * 625.0) / (math.pi * 625.0)
    ok(rel <= 1e-6, "area off by %.3g (rel)" % rel)
    ok(is_planar_face(obj.Shape.Faces[0]), "typed circle face is not planar")


@check("circle: a zero-radius second click refuses with a friendly message")
def c27(fx):
    before = len(fx.doc.Objects)
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_CIRCLE, endpoint_world=1.0)
    ctl.add_point(V(5, 5, 0))
    obj = ctl.add_point(V(5, 5, 0))  # endpoint snap onto the center: r = 0
    ok(obj is None, "zero-radius circle committed")
    ok("radius" in ctl.last_message.lower(),
       "unexpected message: %r" % ctl.last_message)
    ok(not ctl.active, "controller still active after failed commit")
    ok(len(fx.doc.Objects) == before, "document changed after failed commit")


@check("circle: Esc cancel after the center leaves the document untouched")
def c28(fx):
    before = len(fx.doc.Objects)
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_CIRCLE)
    ctl.add_point(V(3, 3, 0))
    ctl.move_to(V(9, 3, 0))
    ctl.cancel()
    ok(not ctl.active, "controller still active after cancel")
    ok(ctl.committed_object is None, "cancel produced a committed object")
    ok(len(fx.doc.Objects) == before, "document changed after cancel")


@check("circle: the live band is a closed ring at the cursor radius")
def c29(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_CIRCLE)
    ctl.add_point(V(0, 0, 0))
    ctl.move_to(V(6, 8, 0))
    band = ctl._band_points()
    ok(len(band) == 49, "expected 48-segment closed ring, got %d points"
       % len(band))
    for p in band:
        approx(geom.distance(p, V(0, 0, 0)), 10.0, 1e-6,
               "band point off the circle")
    vec_approx(band[0], band[-1], 1e-9, "band ring not closed")
    ctl.cancel()


# --- 30-34: polygon -------------------------------------------------------------
@check("polygon: default 6 sides -> hexagon face, exact regular area")
def c30(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_POLYGON)
    ok(ctl.poly_sides == 6, "default sides is %d" % ctl.poly_sides)
    ctl.add_point(V(0, 0, 0))
    ctl.move_to(V(6, 8, 0))  # circumradius 10, one vertex at (6,8,0)
    obj = ctl.add_point()
    ok(obj is not None, "polygon did not commit: %s" % ctl.last_message)
    ok(len(obj.Shape.Vertexes) == 6, "expected 6 vertices, got %d"
       % len(obj.Shape.Vertexes))
    want = 3.0 * math.sqrt(3.0) / 2.0 * 100.0
    rel = abs(obj.Shape.Area - want) / want
    ok(rel <= 1e-6, "hexagon area off by %.3g (rel)" % rel)
    ok(is_planar_face(obj.Shape.Faces[0]), "polygon face is not planar")
    ok(any(geom.distance(v.Point, V(6, 8, 0)) < 1e-6
           for v in obj.Shape.Vertexes), "no vertex at the drag point")


@check("polygon: typed '8s' mid-tool switches to an octagon before commit")
def c31(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_POLYGON)
    ctl.add_point(V(0, 0, 0))
    ctl.type_char("8")
    ctl.type_char("s")
    ok(ctl.typed_buffer == "8s", "buffer is %r" % ctl.typed_buffer)
    before = len(fx.doc.Objects)
    ok(ctl.key_return() is None, "a sides change must not commit")
    ok(ctl.active, "controller not active after sides change")
    ok(ctl.poly_sides == 8, "sides is %d" % ctl.poly_sides)
    ok(ctl.typed_buffer == "", "typed buffer not cleared")
    ok(len(fx.doc.Objects) == before, "sides change created an object")
    ctl.move_to(V(10, 0, 0))  # on-axis inference: radius exactly 10
    obj = ctl.add_point()
    ok(obj is not None, "octagon did not commit: %s" % ctl.last_message)
    ok(len(obj.Shape.Vertexes) == 8, "expected 8 vertices, got %d"
       % len(obj.Shape.Vertexes))
    want = 4.0 * 100.0 * math.sin(math.pi / 4.0)  # (n/2) r^2 sin(2 pi/n)
    rel = abs(obj.Shape.Area - want) / want
    ok(rel <= 1e-6, "octagon area off by %.3g (rel)" % rel)
    ok(is_planar_face(obj.Shape.Faces[0]), "octagon face is not planar")


@check("polygon: typed circumradius + Enter -> every vertex at that radius")
def c32(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_POLYGON)
    ctl.add_point(V(0, 0, 0))
    ctl.move_to(V(10, 0, 0))  # vertex direction +U
    for ch in "20":
        ctl.type_char(ch)
    obj = ctl.key_return()
    ok(obj is not None, "typed polygon did not commit: %s" % ctl.last_message)
    ok(len(obj.Shape.Vertexes) == 6, "expected 6 vertices, got %d"
       % len(obj.Shape.Vertexes))
    for v in obj.Shape.Vertexes:
        approx(geom.distance(v.Point, V(0, 0, 0)), 20.0, 1e-9,
               "vertex off the circumradius")
    ok(is_planar_face(obj.Shape.Faces[0]), "typed polygon face is not planar")


@check("polygon: sides buffer rules ('s' line-mode, bad counts, bare 's')")
def c33(fx):
    parse = draw_controller._parse_sides
    ok(parse("8s") == 8 and parse("8S") == 8, "parse_sides basic")
    for bad in ("", "8", "xs", "s", "8ss"):
        ok(parse(bad) is None, "parse_sides(%r) should be None" % bad)
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.type_char("s")
    ok(ctl.typed_buffer == "", "'s' accepted outside polygon mode")
    ctl.cancel()
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_POLYGON)
    ctl.add_point(V(0, 0, 0))
    for ch in "2s":
        ctl.type_char(ch)
    ok(ctl.key_return() is None, "a refused sides change must not commit")
    ok(ctl.poly_sides == 6, "sides changed to %d" % ctl.poly_sides)
    ok("between 3 and 999" in ctl.last_message,
       "unexpected message: %r" % ctl.last_message)
    ctl.type_char("s")
    ok(ctl.key_return() is None, "bare 's' must not commit")
    ok(ctl.active and ctl.poly_sides == 6, "bare 's' changed the tool state")
    ctl.cancel()


@check("polygon: Esc cancel after the center leaves the document untouched")
def c34(fx):
    before = len(fx.doc.Objects)
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_POLYGON)
    ctl.add_point(V(3, 3, 0))
    ctl.move_to(V(9, 3, 0))
    ctl.cancel()
    ok(not ctl.active, "controller still active after cancel")
    ok(len(fx.doc.Objects) == before, "document changed after cancel")


# --- 35-36: arc -------------------------------------------------------------------
@check("arc: 3 points -> open edge, center/radius/on-curve/length exact")
def c35(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_ARC)
    ctl.add_point(V(10, 0, 0))
    ctl.add_point(V(6, 8, 0))
    ctl.move_to(V(-10, 0, 0))
    obj = ctl.add_point()
    ok(obj is not None, "arc did not commit: %s" % ctl.last_message)
    ok(obj.TypeId == "Part::Feature", "TypeId is %s" % obj.TypeId)
    ok(len(obj.Shape.Faces) == 0, "an arc must not make a face")
    ok(len(obj.Shape.Edges) == 1, "expected one edge, got %d"
       % len(obj.Shape.Edges))
    edge = obj.Shape.Edges[0]
    # FreeCAD 1.1.1's Part.Arc(p1,p2,p3).toShape() reports the underlying
    # curve as a trimmed Circle (Draft-style ArcOfCircle is the other known
    # representation); the exact center/radius/length checks below are the
    # substance either way.
    ok(edge.Curve.__class__.__name__ in ("Circle", "ArcOfCircle"),
       "curve is %s" % edge.Curve.__class__.__name__)
    vec_approx(edge.Curve.Center, V(0, 0, 0), 1e-6, "arc center")
    approx(edge.Curve.Radius, 10.0, 1e-9, "arc radius")
    rel = abs(edge.Length - math.pi * 10.0) / (math.pi * 10.0)
    ok(rel <= 1e-6, "arc length off by %.3g (rel)" % rel)
    ok(edge.distToShape(Part.Vertex(V(6, 8, 0)))[0] <= 1e-6,
       "second point is not on the curve")
    vec_approx(edge.Vertexes[0].Point, V(10, 0, 0), 1e-6, "arc start")
    vec_approx(edge.Vertexes[-1].Point, V(-10, 0, 0), 1e-6, "arc end")
    ok(not ctl.active, "controller still active after commit")


@check("arc: collinear third point refuses (tool stays alive); cancel is clean")
def c36(fx):
    before = len(fx.doc.Objects)
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_ARC)
    ctl.add_point(V(0, 0, 0))
    ctl.add_point(V(5, 0, 0))
    ctl.move_to(V(10, 0, 0))
    ok(len(ctl._band_points()) == 3, "collinear band should fall back to a "
       "polyline, got %d points" % len(ctl._band_points()))
    ok(ctl.add_point() is None, "collinear arc committed")
    ok(ctl.active, "collinear refusal should keep the tool alive")
    ok("collinear" in ctl.last_message.lower(),
       "unexpected message: %r" % ctl.last_message)
    ok(len(fx.doc.Objects) == before, "document changed after refusal")
    ctl.cancel()
    ok(not ctl.active, "controller still active after cancel")
    ok(ctl.committed_object is None, "cancel produced a committed object")
    ok(len(fx.doc.Objects) == before, "document changed after cancel")


# --- 37-40: midpoint / center snapping ------------------------------------------
@check("inference: midpoint of a path segment fires (cyan), beating the axis")
def c37(fx):
    pts = [V(0, 0, 0), V(10, 0, 0)]
    # (5,0.2) sits inside the U-axis cone from the base AND within the snap
    # radius of the segment midpoint (5,0): midpoint must win (priority).
    inf = infer.resolve(fx.plane, pts, V(5, 0.2, 0), endpoint_px_world=1.0)
    ok(inf.category == infer.MIDPOINT, "category is %s" % inf.category)
    vec_approx(inf.point, V(5, 0, 0), 1e-9, "midpoint snap point")
    ok(inf.color == infer.COLORS[infer.MIDPOINT], "midpoint color mismatch")
    ok(inf.tooltip == "Midpoint", "tooltip is %r" % inf.tooltip)


@check("inference: no midpoint fires away from any midpoint (still FREE)")
def c38(fx):
    pts = [V(0, 0, 0), V(10, 0, 0)]
    # 2 mm from the midpoint: outside the snap radius; also outside every
    # axis/parallel cone, so the resolver must fall through to FREE.
    inf = infer.resolve(fx.plane, pts, V(5, 2, 0), endpoint_px_world=1.0)
    ok(inf.category == infer.FREE, "category is %s" % inf.category)
    vec_approx(inf.point, V(5, 2, 0), 1e-9, "FREE point should pass through")


@check("inference: center of a circular document edge snaps (steel blue)")
def c39(fx):
    circle_edge = Part.makeCircle(10.0, V(100, 100, 0))
    inf = infer.resolve(fx.plane, [V(0, 0, 0)], V(100.2, 100.1, 0),
                        endpoint_px_world=1.0, doc_edges=[circle_edge])
    ok(inf.category == infer.CENTER, "category is %s" % inf.category)
    vec_approx(inf.point, V(100, 100, 0), 1e-9, "center snap point")
    ok(inf.color == infer.COLORS[infer.CENTER], "center color mismatch")
    ok(inf.tooltip == "Center", "tooltip is %r" % inf.tooltip)
    # same via the doc-scan path: a Part::Feature circle in the document
    obj = fx.doc.addObject("Part::Feature", "SnapCircle")
    obj.Shape = Part.makeCircle(25.0, V(150, 150, 0))
    edges = infer.collect_doc_edges(fx.doc)
    ok(any(e.Curve.__class__.__name__ == "Circle" for e in edges),
       "collect_doc_edges missed the circle edge")
    inf = infer.resolve(fx.plane, [V(0, 0, 0)], V(149.8, 150.1, 0),
                        endpoint_px_world=1.0, doc=fx.doc)
    ok(inf.category == infer.CENTER, "doc-scan category is %s" % inf.category)
    vec_approx(inf.point, V(150, 150, 0), 1e-9, "doc-scan center snap point")


@check("inference: ENDPOINT beats a midpoint when both are in range")
def c40(fx):
    # straight document edge whose midpoint coincides with a path vertex
    doc_edge = Part.makeLine(V(9, 0, 0), V(11, 0, 0))  # midpoint (10,0,0)
    pts = [V(0, 0, 0), V(10, 0, 0)]
    inf = infer.resolve(fx.plane, pts, V(10.2, 0.1, 0),
                        endpoint_px_world=1.0, doc_edges=[doc_edge])
    ok(inf.category == infer.ENDPOINT, "category is %s" % inf.category)
    vec_approx(inf.point, V(10, 0, 0), 1e-9, "endpoint snap point")
    # and the same document-edge midpoint fires when no vertex competes,
    # here against an axis lock the cursor also qualifies for
    inf = infer.resolve(fx.plane, [V(0, 0, 0)], V(9.8, 0.1, 0),
                        endpoint_px_world=1.0, doc_edges=[doc_edge])
    ok(inf.category == infer.MIDPOINT, "category is %s" % inf.category)
    vec_approx(inf.point, V(10, 0, 0), 1e-9, "doc-edge midpoint snap point")


# --- 41-44: arrow-key axis lock ---------------------------------------------------
@check("axis lock: U projects an off-axis cursor onto the U line through the base")
def c41(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.add_point(V(0, 0, 0))
    ok(ctl.axis_lock is None, "lock active before any arrow key")
    ctl.set_axis_lock("u")
    inf = ctl.move_to(V(7, 5, 0))
    ok(inf.category == infer.ON_AXIS_U, "category is %s" % inf.category)
    vec_approx(inf.point, V(7, 0, 0), 1e-9, "cursor not projected onto U")
    vec_approx(ctl._effective_point(), V(7, 0, 0), 1e-9,
               "effective point off the U line")
    ok(inf.guide is not None, "locked inference should carry the axis guide")
    ctl.cancel()


@check("axis lock: V projects an off-axis cursor onto the V line through the base")
def c42(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.add_point(V(2, 2, 0))
    ctl.set_axis_lock("v")
    inf = ctl.move_to(V(9, 7, 0))
    ok(inf.category == infer.ON_AXIS_V, "category is %s" % inf.category)
    vec_approx(inf.point, V(2, 7, 0), 1e-9, "cursor not projected onto V")
    vec_approx(ctl._effective_point(), V(2, 7, 0), 1e-9,
               "effective point off the V line")
    ctl.cancel()


@check("axis lock: toggle and unlock restore free inference")
def c43(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.add_point(V(0, 0, 0))
    ctl.toggle_axis_lock("u")
    ok(ctl.axis_lock == "u", "toggle did not lock U, got %r" % ctl.axis_lock)
    ctl.toggle_axis_lock("u")
    ok(ctl.axis_lock is None, "second toggle did not unlock")
    ctl.toggle_axis_lock("v")
    ok(ctl.axis_lock == "v", "toggle did not lock V, got %r" % ctl.axis_lock)
    ctl.toggle_axis_lock("u")
    ok(ctl.axis_lock == "u", "switching axes failed, got %r" % ctl.axis_lock)
    ctl.set_axis_lock(None)
    inf = ctl.move_to(V(6, 5, 0))  # ~40 degrees: off every inference cone
    ok(inf.category == infer.FREE, "category is %s after unlock" % inf.category)
    vec_approx(inf.point, V(6, 5, 0), 1e-9,
               "unlock should pass the cursor through")
    ctl.cancel()


@check("axis lock: typed length commits the exact distance along the locked axis")
def c44(fx):
    ctl = DrawController(fx.doc)
    ctl.start(fx.plane, MODE_LINE)
    ctl.add_point(V(1, 1, 0))
    ctl.set_axis_lock("v")
    ctl.move_to(V(9, 4, 0))  # effective point (1,4,0): direction +V
    ctl.type_char("1")
    ctl.type_char("2")
    before = len(fx.doc.Objects)
    ok(ctl.key_return() is None, "typed length must not finish the drawing")
    ok(len(ctl.points) == 2, "expected 2 points, got %d" % len(ctl.points))
    vec_approx(ctl.points[-1], V(1, 13, 0), 1e-9,
               "typed-length vertex off the locked axis")
    ok(len(fx.doc.Objects) == before, "typed length created a document object")
    ok(ctl.typed_buffer == "", "typed buffer not cleared")
    ctl.cancel()


# --- 45-46: Uppercut active-tool highlight soft hook -----------------------------
@check("toolstate hook: no-ops cleanly when Uppercut is not importable")
def c45(fx):
    # Simulate "Uppercut not installed": block both package names in
    # sys.modules so 'from freecad.UppercutWB import toolstate' (and the
    # pre-rename 'freecad.SketchUIWB' fallback) raises ImportError.
    blocked = {"freecad.UppercutWB": None, "freecad.UppercutWB.toolstate": None,
               "freecad.SketchUIWB": None, "freecad.SketchUIWB.toolstate": None}
    saved = {name: sys.modules.get(name) for name in blocked}
    try:
        sys.modules.update(blocked)
        ok(toolstate_hook._load_toolstate() is None,
           "blocked import should yield no toolstate")
        ok(toolstate_hook.tool_started("SketchLayer_Line") is False,
           "started should no-op to False without Uppercut")
        ok(toolstate_hook.tool_finished("SketchLayer_Line") is False,
           "finished should no-op to False without Uppercut")
        # ... and the draw path is byte-for-byte the old behavior: a full
        # rectangle commit with the import still blocked
        ctl = DrawController(fx.doc)
        ctl.start(fx.plane, MODE_RECT)
        ctl.add_point(V(0, 0, 0))
        ctl.move_to(V(8, 5, 0))
        obj = ctl.add_point()
        ok(obj is not None and abs(obj.Shape.Area - 40.0) < 1e-6,
           "draw behavior changed with Uppercut blocked")
        ctl.cancel()
    finally:
        for name, prior in saved.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
    # a loader that raises (broken install) or whose module raises at call
    # time degrades the same way, never propagates
    def raising_loader():
        raise ImportError("No module named 'freecad.UppercutWB'")
    ok(toolstate_hook.tool_started("SketchLayer_Line", loader=raising_loader)
       is False, "raising loader should no-op")
    ok(toolstate_hook.tool_finished("SketchLayer_Line", loader=raising_loader)
       is False, "raising loader should no-op")

    class BrokenTS(object):
        def mark_active(self, name):
            raise RuntimeError("half-broken Uppercut")

        def mark_inactive(self, name):
            raise RuntimeError("half-broken Uppercut")

    ok(toolstate_hook.tool_started("SketchLayer_Line", loader=lambda: BrokenTS())
       is False, "a raising mark_active must not propagate")
    ok(toolstate_hook.tool_finished("SketchLayer_Line", loader=lambda: BrokenTS())
       is False, "a raising mark_inactive must not propagate")


@check("toolstate hook: fires mark_active/mark_inactive; wired in commands.py")
def c46(fx):
    class FakeTS(object):
        def __init__(self):
            self.calls = []

        def mark_active(self, name):
            self.calls.append(("active", name))

        def mark_inactive(self, name):
            self.calls.append(("inactive", name))

    ok(toolstate_hook.COMMANDS == (
        "SketchLayer_Line", "SketchLayer_Rectangle", "SketchLayer_Circle",
        "SketchLayer_Polygon", "SketchLayer_Arc"),
       "hook command list = %r" % (toolstate_hook.COMMANDS,))
    fake = FakeTS()
    for name in toolstate_hook.COMMANDS:
        ok(toolstate_hook.tool_started(name, loader=lambda: fake) is True,
           "started(%s) did not reach the toolstate" % name)
        ok(toolstate_hook.tool_finished(name, loader=lambda: fake) is True,
           "finished(%s) did not reach the toolstate" % name)
    want = []
    for name in toolstate_hook.COMMANDS:
        want += [("active", name), ("inactive", name)]
    ok(fake.calls == want, "calls = %r" % (fake.calls,))
    # static xref: the session calls the hook on start and on teardown,
    # and every command passes its own name
    src_path = os.path.join(_REPO_ROOT, "freecad", "SketchLayerWB", "commands.py")
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    ok("from . import toolstate_hook" in src, "hook import missing")
    ok("toolstate_hook.tool_started(self.command_name)" in src,
       "tool_started call missing")
    ok("toolstate_hook.tool_finished(self.command_name)" in src,
       "tool_finished call missing")
    start = src.index("def start(self):")
    pick_plane = src.index("def _pick_plane(self):")
    ts = src.index("toolstate_hook.tool_started(self.command_name)")
    ok(start < ts < pick_plane, "tool_started is not inside start()")
    teardown = src.index("def _teardown(self):")
    keyfilter = src.index("class _KeyFilter")
    tf = src.index("toolstate_hook.tool_finished(self.command_name)")
    ok(teardown < tf < keyfilter, "tool_finished is not inside _teardown()")
    for mode, name in (("MODE_LINE", "SketchLayer_Line"),
                       ("MODE_RECT", "SketchLayer_Rectangle"),
                       ("MODE_CIRCLE", "SketchLayer_Circle"),
                       ("MODE_POLYGON", "SketchLayer_Polygon"),
                       ("MODE_ARC", "SketchLayer_Arc")):
        needle = '_DrawSession(%s, "%s")' % (mode, name)
        ok(needle in src, "%r missing from commands.py" % needle)


def main():
    fx = Fixture()
    passed = 0
    failures = []
    for idx, (name, fn) in enumerate(_checks, 1):
        try:
            fn(fx)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures.append((idx, name, exc))
            print("[FAIL %2d] %s" % (idx, name))
            traceback.print_exc()
        else:
            passed += 1
            print("[ ok  %2d] %s" % (idx, name))
    total = passed + len(failures)
    print("-" * 64)
    print("%d/%d checks pass" % (passed, total))
    if total != EXPECTED_CHECKS:
        print("WARNING: ran %d checks, expected %d -- update EXPECTED_CHECKS"
              % (total, EXPECTED_CHECKS))
    if failures:
        print("FAILURES:")
        for idx, name, exc in failures:
            print("  %2d. %s: %s" % (idx, name, exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
