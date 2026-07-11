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
        self.committed_object = None
        self.last_message = ""
        self.hud = None
        self.vcb = None

    # -- lifecycle -----------------------------------------------------
    def start(self, plane, mode=MODE_LINE, endpoint_world=1.0):
        self.reset()
        self.plane = plane
        self.mode = mode
        self.endpoint_world = float(endpoint_world)
        self.active = True
        if self.view is not None and hud_mod is not None:
            self.hud = hud_mod.InferenceHUD(self.view)
        if self.view is not None and vcb_mod is not None:
            self.vcb = vcb_mod.ValueBox()
        self._status(self._prompt())
        return True, "ok"

    # -- live cursor ---------------------------------------------------
    def move_to(self, world_point):
        """Update the live cursor; recompute inference; refresh HUD/VCB.
        Returns the resolved :class:`inference.Inference`."""
        if not self.active:
            return None
        self.cursor = self.plane.project(world_point)
        self.inference = infer.resolve(
            self.plane, self.points, self.cursor,
            endpoint_px_world=self.endpoint_world,
        )
        if self.hud is not None:
            self.hud.update(self.inference, self._band_points())
        if self.vcb is not None:
            self.vcb.set_text(self._live_dim_text())
        return self.inference

    def _band_points(self):
        """The rubber-band polyline to preview: the live rectangle's 4
        corners (rect mode, one corner placed) or the placed points plus the
        current effective cursor point (line mode)."""
        eff = self._effective_point()
        if self.mode == MODE_RECT and len(self.points) == 1 and eff is not None:
            corners = geom.rectangle_corners(self.plane, self.points[0], eff)
            return corners + [corners[0]]
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
        if ch in "0123456789.,xX* ":
            if ch == "." and self.typed_buffer.endswith("."):
                return
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
        """Enter: apply a typed dimension if present, else close/commit."""
        if not self.active:
            return None
        dims = _parse_dims(self.typed_buffer)
        if dims:
            return self._apply_typed(dims)
        # no typed value -> close/commit at cursor
        if self.mode == MODE_RECT and len(self.points) == 1 and self.cursor is not None:
            return self._finish_rectangle(self.points[0], self._effective_point())
        if self.mode == MODE_LINE and len(self.points) >= 3:
            return self.close_path()
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
        if self.mode == MODE_RECT and len(self.points) == 1:
            au, av = self.plane.to_local(self.points[0])
            cu, cv = self.plane.to_local(self._effective_point())
            return "%.3g x %.3g" % (abs(cu - au), abs(cv - av))
        base = self.points[-1]
        return "%.3g" % geom.distance(base, self._effective_point())

    def _prompt(self):
        if self.mode == MODE_RECT:
            if not self.points:
                return "SketchLayer Rectangle: click first corner (Esc cancels)."
            return ("SketchLayer Rectangle: click opposite corner, or type "
                    "W,H and press Enter.")
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
