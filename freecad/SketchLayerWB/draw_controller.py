# SPDX-License-Identifier: MIT
"""DrawController: the click / move / type / close state machine for
SketchLayer, deliberately decoupled from Coin/pivy/Qt so it can be driven

  1. from commands.py's real SoEvent/Qt callbacks in the live GUI, and
  2. directly, by method call, from a headless (freecadcmd) regression --
     "the user moved the cursor to P" is ``move_to(P)``, "clicked" is
     ``add_point()``, "typed 12 and pressed Enter" is
     ``type_char('1'); type_char('2'); key_return()``.

No document object is created until the path closes / the rectangle's second
corner is set / Enter commits -- every ``move_to`` only recomputes the
in-memory inference and (in GUI mode) nudges the Coin HUD + the floating
value box. This mirrors the PushPull controller's "cheap per tick, commit
once" design.
"""
import FreeCAD as App

from . import facebuilder
from . import geom
from . import inference as infer

try:  # GUI-only helpers; absent under headless import.
    from . import hud as hud_mod
except Exception:  # pragma: no cover
    hud_mod = None
try:
    from . import vcb as vcb_mod
except Exception:  # pragma: no cover
    vcb_mod = None

MODE_LINE = "line"
MODE_RECT = "rect"
MODE_CIRCLE = "circle"
MODE_POLYGON = "polygon"
MODE_ARC = "arc"

#: default polygon side count (SketchUp's default too)
DEFAULT_POLY_SIDES = 6
MIN_POLY_SIDES = 3
MAX_POLY_SIDES = 999


def _parse_dims(buffer):
    """Parse a VCB buffer into a list of floats. Accepts '12', '12,8',
    '12x8', '12*8', '12 8'. Returns [] on empty/garbage."""
    if not buffer:
        return []
    norm = buffer.replace("x", " ").replace("X", " ").replace("*", " ").replace(",", " ")
    out = []
    for tok in norm.split():
        try:
            out.append(float(tok))
        except ValueError:
            return []
    return out


def _parse_sides(buffer):
    """Parse a SketchUp-style polygon sides buffer ('8s') into an int, or
    None when the buffer is not exactly ``<digits>s``."""
    if not buffer:
        return None
    text = buffer.strip().lower()
    if not text.endswith("s"):
        return None
    digits = text[:-1]
    if not digits.isdigit():
        return None
    return int(digits)


class DrawController(object):
    #: cursor movement (screen px) below which a click is a "place point"
    #: rather than the tail of a drag -- matches PushPull's threshold idiom.
    CLICK_PIXEL_THRESHOLD = 4

    def __init__(self, doc, view=None):
        self.doc = doc
        self.view = view
        self.reset()

    def reset(self):
        self.active = False
        self.mode = MODE_LINE
        self.plane = geom.Plane.xy()
        self.points = []            # committed world vertices
        self.cursor = None          # raw live cursor world point
        self.inference = None       # last Inference for cursor
        self.typed_buffer = ""
        self.endpoint_world = 1.0    # world radius counted as "on a vertex"
        self.poly_sides = DEFAULT_POLY_SIDES
        self.committed_object = None
        self.last_message = ""
        self.hud = None
        self.vcb = None
        self.axis_lock = None       # None, "u" or "v" (SketchUp arrow keys)
        self.doc_edges = []         # edges for midpoint/center snapping

    # -- lifecycle -----------------------------------------------------
    def start(self, plane, mode=MODE_LINE, endpoint_world=1.0, doc_edges=None):
        self.reset()
        self.plane = plane
        self.mode = mode
        self.endpoint_world = float(endpoint_world)
        self.doc_edges = list(doc_edges) if doc_edges else []
        self.active = True
        if self.view is not None and hud_mod is not None:
            self.hud = hud_mod.InferenceHUD(self.view)
        if self.view is not None and vcb_mod is not None:
            self.vcb = vcb_mod.ValueBox()
        self._status(self._prompt())
        return True, "ok"

    # -- arrow-key axis lock (SketchUp) ---------------------------------
    def set_axis_lock(self, axis_or_none):
        """Force the effective cursor onto the plane U or V axis through the
        last placed point (SketchUp's arrow-key lock). ``axis_or_none`` is
        "u", "v" or None (unlock). Returns the new lock state."""
        if axis_or_none not in (None, "u", "v"):
            raise ValueError("axis lock must be None, 'u' or 'v', got %r"
                             % (axis_or_none,))
        self.axis_lock = axis_or_none
        if axis_or_none == "u":
            self._status("SketchLayer: locked to the red (U) axis "
                         "(Left/Right again to unlock).")
        elif axis_or_none == "v":
            self._status("SketchLayer: locked to the green (V) axis "
                         "(Up/Down again to unlock).")
        elif self.active:
            self._status(self._prompt())
        # recompute the inference under the new lock state
        if self.active and self.cursor is not None:
            self.move_to(self.cursor)
        return self.axis_lock

    def toggle_axis_lock(self, axis):
        """SketchUp arrow semantics: pressing the same axis key again
        unlocks; pressing the other axis' key switches the lock."""
        if axis not in ("u", "v"):
            raise ValueError("axis must be 'u' or 'v', got %r" % (axis,))
        return self.set_axis_lock(None if self.axis_lock == axis else axis)

    def _locked_inference(self):
        """The forced ON_AXIS_* inference while an axis lock holds, or None
        when no lock applies (no lock, or no base point to lock through)."""
        if self.axis_lock not in ("u", "v") or not self.points:
            return None
        axis = self.plane.u if self.axis_lock == "u" else self.plane.v
        base = App.Vector(self.points[-1])
        t = self.cursor.sub(base).dot(axis)
        locked = base + axis * t
        category = infer.ON_AXIS_U if self.axis_lock == "u" else infer.ON_AXIS_V
        return infer.Inference(category, locked, guide=(base, locked))

    # -- live cursor ---------------------------------------------------
    def move_to(self, world_point):
        """Update the live cursor; recompute inference; refresh HUD/VCB.
        Returns the resolved :class:`inference.Inference`."""
        if not self.active:
            return None
        self.cursor = self.plane.project(world_point)
        locked = self._locked_inference()
        if locked is not None:
            # an axis lock constrains the pick absolutely (SketchUp): no
            # endpoint/midpoint/parallel arbitration while it holds.
            self.inference = locked
        else:
            self.inference = infer.resolve(
                self.plane, self.points, self.cursor,
                endpoint_px_world=self.endpoint_world,
                doc_edges=self.doc_edges,
            )
        if self.hud is not None:
            self.hud.update(self.inference, self._band_points())
        if self.vcb is not None:
            self.vcb.set_text(self._live_dim_text())
        return self.inference

    def _band_points(self):
        """The rubber-band polyline to preview: the live rectangle's 4
        corners (rect mode, one corner placed), a polyline approximation of
        the live circle/polygon (center placed), the arc through the placed
        points and the cursor (arc mode, two points placed), or the placed
        points plus the current effective cursor point (line mode)."""
        eff = self._effective_point()
        if self.mode == MODE_RECT and len(self.points) == 1 and eff is not None:
            corners = geom.rectangle_corners(self.plane, self.points[0], eff)
            return corners + [corners[0]]
        if self.mode == MODE_CIRCLE and len(self.points) == 1 and eff is not None:
            radius = geom.distance(self.points[0], eff)
            if radius < 1e-9:
                return []
            return geom.circle_band_points(self.plane, self.points[0], radius)
        if self.mode == MODE_POLYGON and len(self.points) == 1 and eff is not None:
            if geom.distance(self.points[0], eff) < 1e-9:
                return []
            corners = geom.regular_polygon_corners(
                self.plane, self.points[0], eff, self.poly_sides)
            return corners + [corners[0]]
        if self.mode == MODE_ARC and len(self.points) == 2 and eff is not None:
            band = geom.arc_band_points(self.points[0], self.points[1], eff)
            if band is not None:
                return band
            # collinear so far: plain polyline until the end point bends
            return [self.points[0], self.points[1], eff]
        band = list(self.points)
        if eff is not None:
            band = band + [eff]
        return band

    def _effective_point(self):
        """The point a click would place: inference-adjusted if one fired,
        else the raw projected cursor."""
        if self.inference is not None and self.inference.category != infer.FREE:
            return self.inference.point
        return self.cursor

    # -- click / place -------------------------------------------------
    def add_point(self, world_point=None):
        """Place a vertex at the current effective point (or an explicit
        world point). May finish the drawing (rect 2nd corner / closing the
        loop), returning the created object; otherwise returns None."""
        if not self.active:
            return None
        if world_point is not None:
            self.move_to(world_point)
        pt = self._effective_point()
        if pt is None:
            return None

        if self.mode == MODE_RECT:
            self.points.append(App.Vector(pt))
            if len(self.points) >= 2:
                return self._finish_rectangle(self.points[0], self.points[1])
            self._status(self._prompt())
            return None

        if self.mode in (MODE_CIRCLE, MODE_POLYGON):
            if self.points and geom.distance(self.points[0], pt) < 1e-9:
                # the pick snapped exactly onto the center (easy to do: the
                # center is an endpoint snap target); refuse and stay alive,
                # same idiom as the arc's collinear refusal
                what = "Circle" if self.mode == MODE_CIRCLE else "Polygon"
                self._status(
                    "SketchLayer %s: radius is zero (the pick snapped onto "
                    "the center); click farther out or type a radius (Esc "
                    "cancels)." % what)
                return None
            self.points.append(App.Vector(pt))
            if len(self.points) >= 2:
                if self.mode == MODE_CIRCLE:
                    return self._finish_circle(
                        self.points[0], geom.distance(self.points[0], pt))
                return self._finish_polygon(self.points[0], pt)
            self.typed_buffer = ""
            self._status(self._prompt())
            return None

        if self.mode == MODE_ARC:
            if len(self.points) >= 2:
                # third point: refuse a collinear pick instead of committing
                # a shape; the tool stays alive for another try (or Esc).
                if geom.circle_through_3pt(
                        self.points[0], self.points[1], pt) is None:
                    self._status(
                        "SketchLayer Arc: points are collinear; click an end "
                        "point off the line (Esc cancels).")
                    return None
                return self._finish_arc(self.points[0], self.points[1], pt)
            self.points.append(App.Vector(pt))
            self.typed_buffer = ""
            self._status(self._prompt())
            return None

        # line / polyline
        if self.points and self.inference is not None and \
                self.inference.category == infer.ENDPOINT and \
                geom.distance(pt, self.points[0]) <= self.endpoint_world and \
                len(self.points) >= 3:
            return self.close_path()
        self.points.append(App.Vector(pt))
        self.typed_buffer = ""
        self._status(self._prompt())
        return None

    # -- typed precision (VCB) ----------------------------------------
    def type_char(self, ch):
        if not self.active:
            return
        allowed = "0123456789.,xX* "
        if self.mode == MODE_POLYGON:
            allowed += "sS"  # SketchUp-style sides count: '8s'
        if ch in allowed:
            if ch == "." and self.typed_buffer.endswith("."):
                return
            if ch in "sS":
                if "s" in self.typed_buffer.lower():
                    return
                ch = "s"
            self.typed_buffer += ch
        else:
            return
        if self.vcb is not None:
            self.vcb.set_text(self.typed_buffer)

    def key_backspace(self):
        if self.active and self.typed_buffer:
            self.typed_buffer = self.typed_buffer[:-1]
            if self.vcb is not None:
                self.vcb.set_text(self.typed_buffer or self._live_dim_text())

    def key_return(self):
        """Enter: apply a typed dimension (or polygon sides) if present,
        else close/commit at the cursor."""
        if not self.active:
            return None
        if self.mode == MODE_POLYGON and "s" in self.typed_buffer.lower():
            sides = _parse_sides(self.typed_buffer)
            if sides is None:
                # a buffer with 's' that is not '<n>s' is a rejected sides
                # change, never a commit-at-cursor
                self._status("SketchLayer Polygon: type sides as <n>s (e.g. "
                             "8s); still %d." % self.poly_sides)
                self.typed_buffer = ""
                if self.vcb is not None:
                    self.vcb.set_text(self._live_dim_text())
                return None
            return self._apply_sides(sides)
        dims = _parse_dims(self.typed_buffer)
        if dims:
            return self._apply_typed(dims)
        # no typed value -> close/commit at cursor
        if self.mode == MODE_RECT and len(self.points) == 1 and self.cursor is not None:
            return self._finish_rectangle(self.points[0], self._effective_point())
        if self.mode in (MODE_CIRCLE, MODE_POLYGON) and len(self.points) == 1 \
                and self.cursor is not None:
            return self.add_point()
        if self.mode == MODE_LINE and len(self.points) >= 3:
            return self.close_path()
        return None

    def _apply_sides(self, sides):
        """SketchUp-style '<n>s' typed mid-tool: change the polygon side
        count without committing."""
        if not MIN_POLY_SIDES <= sides <= MAX_POLY_SIDES:
            self._status("SketchLayer Polygon: sides must be between %d and "
                         "%d (still %d)." % (
                             MIN_POLY_SIDES, MAX_POLY_SIDES, self.poly_sides))
            self.typed_buffer = ""
            if self.vcb is not None:
                self.vcb.set_text(self._live_dim_text())
            return None
        self.poly_sides = sides
        self.typed_buffer = ""
        self._status(self._prompt())
        # refresh the preview band with the new side count
        if self.cursor is not None:
            self.move_to(self.cursor)
        return None

    def _apply_typed(self, dims):
        if self.mode == MODE_RECT:
            if not self.points:
                return None
            a = self.points[0]
            if len(dims) == 1:
                w = h = dims[0]
            else:
                w, h = dims[0], dims[1]
            # direction signs follow the current cursor quadrant if known
            su = sv = 1.0
            if self.cursor is not None:
                cu, cv = self.plane.to_local(self.cursor)
                au, av = self.plane.to_local(a)
                su = 1.0 if (cu - au) >= 0 else -1.0
                sv = 1.0 if (cv - av) >= 0 else -1.0
            au, av = self.plane.to_local(a)
            b = self.plane.to_world(au + su * abs(w), av + sv * abs(h))
            return self._finish_rectangle(a, b)
        if self.mode in (MODE_CIRCLE, MODE_POLYGON):
            # typed radius: exact value, no cursor-distance rounding
            if not self.points:
                return None
            center = self.points[0]
            radius = abs(dims[0])
            if self.mode == MODE_CIRCLE:
                return self._finish_circle(center, radius)
            direction = self._radius_direction(center)
            return self._finish_polygon(center, center + direction * radius)
        if self.mode == MODE_ARC:
            # A 3-point arc has no single typed dimension. Refuse instead of
            # falling through to the line branch below, which would append a
            # phantom vertex the arc commit then ignores (and whose segment
            # corrupts the parallel/perpendicular inference for the real
            # end-point click).
            self._status("SketchLayer Arc: no typed dimension here; click "
                         "the three points (Esc cancels).")
            self.typed_buffer = ""
            if self.vcb is not None:
                self.vcb.set_text(self._live_dim_text())
            return None
        # line: typed length along the current direction from the last point
        if not self.points:
            return None
        base = self.points[-1]
        direction = self._current_direction()
        if direction is None:
            return None
        length = dims[0]
        newpt = base + direction * length
        self.points.append(App.Vector(newpt))
        self.typed_buffer = ""
        self._status(self._prompt())
        return None

    def _current_direction(self):
        """Unit direction the next line segment would go (inference-locked
        if an inference fired, else toward the raw cursor)."""
        if not self.points or self.cursor is None:
            return None
        base = self.points[-1]
        tgt = self._effective_point()
        d = App.Vector(tgt).sub(base)
        if d.Length < 1e-9:
            return None
        return d * (1.0 / d.Length)

    def _radius_direction(self, center):
        """Unit direction from the center toward the effective cursor, used
        to place a polygon vertex for a typed circumradius. Falls back to
        the plane U axis when the cursor sits on the center."""
        tgt = self._effective_point()
        if tgt is not None:
            d = App.Vector(tgt).sub(center)
            if d.Length >= 1e-9:
                return d * (1.0 / d.Length)
        return self.plane.u

    # -- build / finish ------------------------------------------------
    def close_path(self):
        try:
            obj = facebuilder.add_face_object(self.doc, self.points)
        except facebuilder.BuildError as exc:
            self.last_message = "SketchLayer: %s" % exc
            self._teardown()
            return None
        self.committed_object = obj
        self.last_message = "SketchLayer: created face '%s' (area %.3g)." % (
            obj.Name, obj.Shape.Area)
        self._teardown()
        return obj

    def _finish_rectangle(self, corner_a, corner_b):
        corners = geom.rectangle_corners(self.plane, corner_a, corner_b)
        try:
            obj = facebuilder.add_face_object(self.doc, corners)
        except facebuilder.BuildError as exc:
            self.last_message = "SketchLayer: %s" % exc
            self._teardown()
            return None
        self.committed_object = obj
        self.last_message = "SketchLayer: created rectangle '%s' (area %.3g)." % (
            obj.Name, obj.Shape.Area)
        self._teardown()
        return obj

    def _finish_circle(self, center, radius):
        try:
            obj = facebuilder.add_circle_face_object(
                self.doc, center, radius, self.plane.normal)
        except facebuilder.BuildError as exc:
            self.last_message = "SketchLayer: %s" % exc
            self._teardown()
            return None
        self.committed_object = obj
        self.last_message = "SketchLayer: created circle '%s' (radius %.3g)." % (
            obj.Name, radius)
        self._teardown()
        return obj

    def _finish_polygon(self, center, radius_point):
        corners = geom.regular_polygon_corners(
            self.plane, center, radius_point, self.poly_sides)
        try:
            obj = facebuilder.add_face_object(
                self.doc, corners, name="SketchLayerPolygon")
        except facebuilder.BuildError as exc:
            self.last_message = "SketchLayer: %s" % exc
            self._teardown()
            return None
        self.committed_object = obj
        self.last_message = ("SketchLayer: created polygon '%s' (%d sides, "
                             "area %.3g)." % (
                                 obj.Name, self.poly_sides, obj.Shape.Area))
        self._teardown()
        return obj

    def _finish_arc(self, p1, p2, p3):
        try:
            obj = facebuilder.add_arc_object(self.doc, p1, p2, p3)
        except facebuilder.BuildError as exc:
            self.last_message = "SketchLayer: %s" % exc
            self._teardown()
            return None
        self.committed_object = obj
        self.last_message = ("SketchLayer: created arc '%s' (length %.3g; an "
                             "open edge, not a face)." % (
                                 obj.Name, obj.Shape.Length))
        self._teardown()
        return obj

    def cancel(self):
        self.last_message = "SketchLayer: cancelled."
        self._teardown()

    def _teardown(self):
        if self.hud is not None:
            self.hud.remove()
            self.hud = None
        if self.vcb is not None:
            self.vcb.hide()
            self.vcb = None
        self.active = False
        self._status(self.last_message)

    # -- readout helpers -----------------------------------------------
    def _live_dim_text(self):
        if self.typed_buffer:
            return self.typed_buffer
        if not self.points or self.cursor is None:
            return ""
        eff = self._effective_point()
        if self.mode == MODE_RECT and len(self.points) == 1:
            au, av = self.plane.to_local(self.points[0])
            cu, cv = self.plane.to_local(eff)
            return "%.3g x %.3g" % (abs(cu - au), abs(cv - av))
        if self.mode == MODE_CIRCLE and len(self.points) == 1:
            return "%.3g" % geom.distance(self.points[0], eff)
        if self.mode == MODE_POLYGON and len(self.points) == 1:
            return "%.3g (%ds)" % (
                geom.distance(self.points[0], eff), self.poly_sides)
        if self.mode == MODE_ARC:
            if len(self.points) == 2 and eff is not None:
                circ = geom.circle_through_3pt(
                    self.points[0], self.points[1], eff)
                if circ is not None:
                    return "R %.3g" % circ[1]
                return ""
            if len(self.points) < 2:
                return "%.3g" % geom.distance(self.points[-1], eff)
        base = self.points[-1]
        return "%.3g" % geom.distance(base, eff)

    def _prompt(self):
        if self.mode == MODE_RECT:
            if not self.points:
                return "SketchLayer Rectangle: click first corner (Esc cancels)."
            return ("SketchLayer Rectangle: click opposite corner, or type "
                    "W,H and press Enter.")
        if self.mode == MODE_CIRCLE:
            if not self.points:
                return "SketchLayer Circle: click the center (Esc cancels)."
            return ("SketchLayer Circle: click a point on the circle, or "
                    "type a radius and press Enter.")
        if self.mode == MODE_POLYGON:
            if not self.points:
                return ("SketchLayer Polygon: click the center (Esc cancels). "
                        "Type <n>s for sides, now %d." % self.poly_sides)
            return ("SketchLayer Polygon: click a vertex, or type a "
                    "circumradius and press Enter. (%d sides)" % self.poly_sides)
        if self.mode == MODE_ARC:
            n = len(self.points)
            if n == 0:
                return "SketchLayer Arc: click the start point (Esc cancels)."
            if n == 1:
                return "SketchLayer Arc: click a second point on the curve."
            return ("SketchLayer Arc: click the end point. (An arc is an "
                    "edge, not a face.)")
        n = len(self.points)
        if n == 0:
            return "SketchLayer Line: click start point (Esc cancels)."
        if n < 3:
            return ("SketchLayer Line: click next point, or type a length and "
                    "Enter. (%d placed)" % n)
        return ("SketchLayer Line: click the start point to close into a face, "
                "or Enter to close. (%d placed)" % n)

    def _status(self, msg):
        self.last_message = msg or self.last_message
        if self.view is None:
            return
        try:
            import FreeCADGui as Gui
            Gui.getMainWindow().statusBar().showMessage(msg, 4000)
        except Exception:
            pass
