# SPDX-License-Identifier: MIT
"""Gui.Commands for SketchLayer: Line/polyline, Rectangle, Circle, Polygon
and Arc tools, all driving the shared
:class:`draw_controller.DrawController`.

Wiring mirrors the PushPull addon (proven idiom):
  * a dict-style ``"SoEvent"`` view callback for mouse move / click, and
  * an application-level Qt event filter for typed digits / Enter / Esc
    (needed because FreeCAD binds bare digit keys to view shortcuts that
    would otherwise eat the keystrokes -- same reason documented in
    PushPull's commands.py).

Two pieces of FreeCAD machinery are reused rather than reinvented:
  * **object snapping** -- ``FreeCADGui.Snapper`` is consulted for snaps to
    real model geometry (with ``noTracker=True`` so only our colored HUD is
    drawn, not Draft's monochrome glyph); the deterministic ray/plane
    intersection is the fallback when it does not fire.
  * **draw-on-face** -- if a planar face is selected when the tool starts,
    the drawing plane is aligned to it (``geom.plane_from_face``).
"""
import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from . import geom
from . import inference as infer
from . import toolstate_hook
from .draw_controller import (
    DrawController, MODE_ARC, MODE_CIRCLE, MODE_LINE, MODE_POLYGON, MODE_RECT)

_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "Resources", "Icons")


def _icon(name):
    return os.path.join(_ICON_DIR, name)


_TYPE_KEYS = {
    QtCore.Qt.Key_0: "0", QtCore.Qt.Key_1: "1", QtCore.Qt.Key_2: "2",
    QtCore.Qt.Key_3: "3", QtCore.Qt.Key_4: "4", QtCore.Qt.Key_5: "5",
    QtCore.Qt.Key_6: "6", QtCore.Qt.Key_7: "7", QtCore.Qt.Key_8: "8",
    QtCore.Qt.Key_9: "9", QtCore.Qt.Key_Period: ".", QtCore.Qt.Key_Comma: ",",
    QtCore.Qt.Key_X: "x", QtCore.Qt.Key_Asterisk: "*",
    QtCore.Qt.Key_S: "s",  # polygon sides: '8s' (ignored by other modes)
}

#: SketchUp arrow-key axis lock: Right/Left lock the plane U ("red") axis,
#: Up/Down lock V ("green"); the controller toggles on repeat presses.
_AXIS_LOCK_KEYS = {
    QtCore.Qt.Key_Right: "u", QtCore.Qt.Key_Left: "u",
    QtCore.Qt.Key_Up: "v", QtCore.Qt.Key_Down: "v",
}


def _is_bare_letter_or_space(event):
    """True for an unmodified A-Z key or Space.

    While a draw session owns the view, bare letters and Space are reserved
    for the tool: accepting their ShortcutOverride stops application-level
    accelerators (SketchUI's single-letter shortcuts like L/R/C/A/G, or any
    other addon's) from firing mid-draw and stealing the session, and the
    KeyPress is then consumed so nothing else reacts. Modifier combos
    (Ctrl+S and friends) are NOT touched.
    """
    if event.modifiers() != QtCore.Qt.NoModifier:
        return False
    key = event.key()
    return QtCore.Qt.Key_A <= key <= QtCore.Qt.Key_Z or key == QtCore.Qt.Key_Space


class _DrawSession(object):
    """One click/move/type/close drawing session for a given mode.

    ``command_name`` is the FreeCAD command that owns the session; it is
    only used for the (optional) SketchUI active-tool highlight hook --
    a no-op when SketchUI is not installed."""

    def __init__(self, mode, command_name=None):
        self.mode = mode
        self.command_name = command_name
        self._view = None
        self._sg_callback = None
        self._key_filter = None
        self.controller = None

    def start(self):
        doc = App.ActiveDocument
        if doc is None:
            doc = App.newDocument()
        self._view = Gui.ActiveDocument.ActiveView
        plane = self._pick_plane()
        endpoint_world = self._endpoint_world(plane)
        self.controller = DrawController(doc, view=self._view)
        self.controller.start(
            plane, self.mode, endpoint_world=endpoint_world,
            doc_edges=infer.collect_doc_edges(doc))
        self._sg_callback = self._view.addEventCallback("SoEvent", self._on_event)
        self._install_key_filter()
        if self.command_name:
            toolstate_hook.tool_started(self.command_name)

    # -- drawing plane --------------------------------------------------
    def _pick_plane(self):
        """Draw-on-face: if a single planar face is selected, use it; else
        default to the global XY plane (v1)."""
        try:
            sel = Gui.Selection.getSelectionEx()
            if len(sel) == 1 and len(sel[0].SubObjects) == 1:
                sub = sel[0].SubObjects[0]
                if sub.__class__.__name__ == "Face":
                    p = geom.plane_from_face(sub)
                    if p is not None:
                        return p
        except Exception:
            pass
        return geom.Plane.xy()

    def _endpoint_world(self, plane):
        """A world-space radius (~10 screen px at the view centre) under
        which the cursor counts as 'on' an existing vertex, so the size of
        the close-the-loop hot zone tracks zoom."""
        try:
            c = plane.origin
            s = self._view.getPointOnScreen(c)
            p2 = self._view.getPoint(s[0] + 10, s[1])
            return max(geom.distance(c, plane.project(p2)), 1e-3)
        except Exception:
            return 1.0

    # -- keyboard (Qt) --------------------------------------------------
    def _install_key_filter(self):
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        self._key_filter = _KeyFilter(self)
        app.installEventFilter(self._key_filter)

    def _remove_key_filter(self):
        if self._key_filter is not None:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.removeEventFilter(self._key_filter)
            self._key_filter = None

    def wants_key(self, event):
        if self.controller is None or not self.controller.active:
            return False
        if event.key() in _AXIS_LOCK_KEYS or _is_bare_letter_or_space(event):
            return True
        return event.key() in _TYPE_KEYS or event.key() in (
            QtCore.Qt.Key_Escape, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter,
            QtCore.Qt.Key_Backspace)

    def handle_key(self, event):
        if self.controller is None or not self.controller.active:
            return False
        key = event.key()
        if key == QtCore.Qt.Key_Escape:
            self.controller.cancel()
            self._teardown()
            return True
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            obj = self.controller.key_return()
            if obj is not None or not self.controller.active:
                self._teardown()
            return True
        if key == QtCore.Qt.Key_Backspace:
            self.controller.key_backspace()
            return True
        if key in _AXIS_LOCK_KEYS and \
                event.modifiers() == QtCore.Qt.NoModifier:
            self.controller.toggle_axis_lock(_AXIS_LOCK_KEYS[key])
            return True
        if key in _TYPE_KEYS:
            self.controller.type_char(_TYPE_KEYS[key])
            return True
        if _is_bare_letter_or_space(event):
            # reserved while drawing (see _is_bare_letter_or_space): consume
            # so no accelerator or view shortcut can react mid-draw.
            return True
        return False

    # -- mouse (SoEvent) ------------------------------------------------
    def _on_event(self, arg):
        etype = arg.get("Type")
        if etype == "SoLocation2Event":
            self._on_move(arg)
        elif etype == "SoMouseButtonEvent":
            self._on_button(arg)
        elif etype == "SoKeyboardEvent" and arg.get("Key") == "ESCAPE":
            self.controller.cancel()
            self._teardown()

    def _on_move(self, arg):
        if self.controller is None or not self.controller.active:
            return
        pos = arg.get("Position")
        if pos is None:
            return
        pt = self._plane_point(pos)
        if pt is not None:
            self.controller.move_to(pt)
        # keep the floating value box by the cursor
        if self.controller.vcb is not None:
            gp = QtGui.QCursor.pos()
            self.controller.vcb.move_to_global(gp.x(), gp.y())

    def _on_button(self, arg):
        if arg.get("Button") != "BUTTON1" or arg.get("State") != "DOWN":
            return
        if self.controller is None or not self.controller.active:
            return
        obj = self.controller.add_point()
        if obj is not None or not self.controller.active:
            self._teardown()

    # -- cursor -> 3D point on the drawing plane ------------------------
    def _plane_point(self, pos):
        # 1) reuse Draft's Snapper for snaps to real model geometry.
        snapped = self._snapper_point(pos)
        if snapped is not None:
            return snapped
        # 2) deterministic fallback: intersect the pick ray with the plane.
        ro, rd = self._pick_ray(pos)
        if ro is None:
            return None
        return geom.ray_plane_intersection(self.controller.plane, ro, rd)

    def _snapper_point(self, pos):
        try:
            snapper = getattr(Gui, "Snapper", None)
            if snapper is None:
                return None
            last = self.controller.points[-1] if self.controller.points else None
            p = snapper.snap(pos, lastpoint=last, active=True, noTracker=True)
            info = getattr(snapper, "snapInfo", None)
            # only trust a snap that latched onto a real component (vertex/
            # edge/face) -- otherwise it is just the grid, and our own
            # ray/plane intersection is the cleaner source.
            if p is not None and info and info.get("Component"):
                return self.controller.plane.project(p)
        except Exception:
            return None
        return None

    def _pick_ray(self, pos):
        try:
            view = self._view
            pt = view.getPoint(pos[0], pos[1])
            if view.getCameraType() == "Perspective":
                cam = view.getCameraNode()
                c = cam.getField("position").getValue()
                cam_pos = App.Vector(c[0], c[1], c[2])
                return cam_pos, pt.sub(cam_pos)
            return pt, view.getViewDirection()
        except Exception:
            return None, None

    def _teardown(self):
        if self._view is not None and self._sg_callback is not None:
            try:
                self._view.removeEventCallback("SoEvent", self._sg_callback)
            except Exception:
                pass
            self._sg_callback = None
        self._remove_key_filter()
        if self.command_name:
            toolstate_hook.tool_finished(self.command_name)


class _KeyFilter(QtCore.QObject):
    def __init__(self, session):
        super().__init__()
        self._session = session

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QtCore.QEvent.ShortcutOverride:
            if self._session.wants_key(event):
                event.accept()
            return False
        if etype == QtCore.QEvent.KeyPress:
            if self._session.handle_key(event):
                return True
        return False


class _RectangleCommand(object):
    _session = None

    def GetResources(self):
        return {"MenuText": "Rectangle (inference)",
                "ToolTip": ("Draw a rectangle on the working plane (or a "
                            "selected planar face) with colored inference "
                            "cues; type W,H for exact size. Makes a face "
                            "ready to Push/Pull."),
                "Pixmap": _icon("sketchlayer_rect.svg")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        _RectangleCommand._session = _DrawSession(MODE_RECT, "SketchLayer_Rectangle")
        _RectangleCommand._session.start()


class _LineCommand(object):
    _session = None

    def GetResources(self):
        return {"MenuText": "Line (inference)",
                "ToolTip": ("Draw a polyline on the working plane (or a "
                            "selected planar face) with colored inference "
                            "cues; click the start point (or Enter) to close "
                            "into a face ready to Push/Pull."),
                "Pixmap": _icon("sketchlayer_line.svg")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        _LineCommand._session = _DrawSession(MODE_LINE, "SketchLayer_Line")
        _LineCommand._session.start()


class _CircleCommand(object):
    _session = None

    def GetResources(self):
        return {"MenuText": "Circle (inference)",
                "ToolTip": ("Draw a circle on the working plane (or a "
                            "selected planar face): click the center, then "
                            "drag or type a radius. Makes a face ready to "
                            "Push/Pull."),
                "Pixmap": _icon("sketchlayer_circle.svg")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        _CircleCommand._session = _DrawSession(MODE_CIRCLE, "SketchLayer_Circle")
        _CircleCommand._session.start()


class _PolygonCommand(object):
    _session = None

    def GetResources(self):
        return {"MenuText": "Polygon (inference)",
                "ToolTip": ("Draw a regular polygon: click the center, then "
                            "drag or type a circumradius. Type <n>s mid-tool "
                            "(e.g. 8s) to change the side count (default 6). "
                            "Makes a face ready to Push/Pull."),
                "Pixmap": _icon("sketchlayer_polygon.svg")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        _PolygonCommand._session = _DrawSession(MODE_POLYGON, "SketchLayer_Polygon")
        _PolygonCommand._session.start()


class _ArcCommand(object):
    _session = None

    def GetResources(self):
        return {"MenuText": "Arc 3-point (inference)",
                "ToolTip": ("Draw a 3-point arc: click the start, a second "
                            "point on the curve, then the end. Commits an "
                            "open edge, not a face (same as SketchUp)."),
                "Pixmap": _icon("sketchlayer_arc.svg")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        _ArcCommand._session = _DrawSession(MODE_ARC, "SketchLayer_Arc")
        _ArcCommand._session.start()


def register():
    Gui.addCommand("SketchLayer_Rectangle", _RectangleCommand())
    Gui.addCommand("SketchLayer_Line", _LineCommand())
    Gui.addCommand("SketchLayer_Circle", _CircleCommand())
    Gui.addCommand("SketchLayer_Polygon", _PolygonCommand())
    Gui.addCommand("SketchLayer_Arc", _ArcCommand())
