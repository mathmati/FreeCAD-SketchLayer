# SPDX-License-Identifier: MIT
"""InferenceHUD: the colored Coin3D overlay that gives SketchLayer its
SketchUp "feel" -- the piece Draft's monochrome snap glyph does not provide.

Three cheap, persistent scene-graph pieces, updated (field-set only, no
graph rebuild) on every cursor tick, mirroring PushPull's tracker discipline
(deferred insert/remove via QTimer.singleShot so it never mutates the graph
mid-Coin-traversal):

  * a **rubber band** polyline from the already-placed points to the live
    effective point (neutral gray);
  * a **colored inference guide** dotted line (red = on U/"red" axis, green =
    on V/"green" axis, magenta = parallel/perpendicular);
  * a **colored point marker** at the inference point, in the category color
    (green endpoint, red/green axis, magenta parallel/perp).

Colors come from inference.COLORS so the HUD and any test agree.
"""
import pivy.coin as coin
import FreeCADGui as Gui
from PySide import QtCore

from . import inference as infer

BAND_COLOR = (0.55, 0.55, 0.55)
MARKER_SIZE = 14.0
GUIDE_WIDTH = 2.0
GUIDE_PATTERN = 0x0F0F  # dotted


def _defer(func, *args):
    QtCore.QTimer.singleShot(0, lambda: func(*args))


class InferenceHUD(object):
    def __init__(self, view):
        self.view = view

        root = coin.SoSeparator()

        # --- rubber band ---
        band = coin.SoSeparator()
        self.band_color = coin.SoBaseColor()
        self.band_color.rgb = BAND_COLOR
        bstyle = coin.SoDrawStyle()
        bstyle.lineWidth = 1.5
        self.band_coords = coin.SoCoordinate3()
        self.band_lines = coin.SoLineSet()
        band.addChild(self.band_color)
        band.addChild(bstyle)
        band.addChild(self.band_coords)
        band.addChild(self.band_lines)
        self.band_switch = coin.SoSwitch()
        self.band_switch.addChild(band)
        self.band_switch.whichChild = -1
        root.addChild(self.band_switch)

        # --- colored inference guide line ---
        guide = coin.SoSeparator()
        self.guide_color = coin.SoBaseColor()
        gstyle = coin.SoDrawStyle()
        gstyle.lineWidth = GUIDE_WIDTH
        gstyle.linePattern = GUIDE_PATTERN
        self.guide_coords = coin.SoCoordinate3()
        self.guide_lines = coin.SoLineSet()
        guide.addChild(self.guide_color)
        guide.addChild(gstyle)
        guide.addChild(self.guide_coords)
        guide.addChild(self.guide_lines)
        self.guide_switch = coin.SoSwitch()
        self.guide_switch.addChild(guide)
        self.guide_switch.whichChild = -1
        root.addChild(self.guide_switch)

        # --- colored point marker ---
        marker = coin.SoSeparator()
        self.marker_color = coin.SoBaseColor()
        mstyle = coin.SoDrawStyle()
        mstyle.pointSize = MARKER_SIZE
        self.marker_coords = coin.SoCoordinate3()
        marker.addChild(self.marker_color)
        marker.addChild(mstyle)
        marker.addChild(self.marker_coords)
        marker.addChild(coin.SoPointSet())
        self.marker_switch = coin.SoSwitch()
        self.marker_switch.addChild(marker)
        self.marker_switch.whichChild = -1
        root.addChild(self.marker_switch)

        self.root = coin.SoSwitch()
        self.root.setName("SketchLayerHUD")
        self.root.addChild(root)
        self.root.whichChild = 0
        _defer(self._insert)

    # -- scene graph attach/detach -------------------------------------
    def _scene_graph(self):
        try:
            return self.view.getSceneGraph()
        except Exception:
            return None

    def _insert(self):
        sg = self._scene_graph()
        if sg is not None and self.root is not None:
            sg.addChild(self.root)

    def remove(self):
        root = self.root
        self.root = None
        if root is not None:
            _defer(self._detach, root)

    def _detach(self, root):
        sg = self._scene_graph()
        if sg is not None and sg.findChild(root) >= 0:
            sg.removeChild(root)

    # -- per-tick update (cheap: field-set only) -----------------------
    def update(self, inference, band_points):
        """``band_points`` is the full rubber-band polyline the controller
        wants drawn (line = placed points + cursor; rectangle = the 4 live
        corners). The colored guide + marker come from ``inference``."""
        if self.root is None:
            return

        band_pts = [tuple(p) for p in (band_points or [])]
        if len(band_pts) >= 2:
            self.band_coords.point.setValues(0, len(band_pts), band_pts)
            self.band_lines.numVertices.setValue(len(band_pts))
            self.band_switch.whichChild = 0
        else:
            self.band_switch.whichChild = -1

        # colored guide line for the active inference
        if inference is not None and inference.guide is not None:
            a, b = inference.guide
            self.guide_color.rgb = inference.color
            self.guide_coords.point.setValues(0, 2, [tuple(a), tuple(b)])
            self.guide_lines.numVertices.setValue(2)
            self.guide_switch.whichChild = 0
        else:
            self.guide_switch.whichChild = -1

        # colored point marker at the inference point
        if inference is not None and inference.category != infer.FREE and \
                inference.point is not None:
            self.marker_color.rgb = inference.color
            self.marker_coords.point.setValues(0, 1, [tuple(inference.point)])
            self.marker_switch.whichChild = 0
        else:
            self.marker_switch.whichChild = -1
