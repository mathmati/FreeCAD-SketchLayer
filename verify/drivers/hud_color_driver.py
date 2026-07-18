# DRAFT/UNVERIFIED — written 2026-07-18 by an agent WITHOUT FreeCAD available;
# never executed. mathmati must run with freecadcmd and adjust before committing.
# SPDX-License-Identifier: MIT
"""verify/drivers/hud_color_driver.py -- GUI (Xvfb) HUD color verification.

Run under a virtual X server from the repo root:

    xvfb-run -a -s "-screen 0 1280x1024x24" freecad verify/drivers/hud_color_driver.py

What this is: the "colored inference HUD renders the correct color per
category (verified at the pixel level -- red/green/magenta)" claim from the
SketchLayer README's Verification section. Each category is checked twice:

  LOGIC LEVEL (robust, deterministic): the HUD's Coin scene-graph fields
  carry exactly ``inference.COLORS[category]`` -- ``SoBaseColor.rgb`` on the
  guide line and point marker, and the right ``SoSwitch`` children on.

  PIXEL LEVEL (what the README promises, inherently environment-sensitive):
  the frame is rendered to a PNG with ``view.saveImage()`` and the most
  saturated pixel in a small window around the inference point (projected
  with ``view.getPointOnScreen``) must be dominated by the expected hue.
  Lighting/antialiasing/driver differences make this the part most likely
  to need tolerance tuning on a new machine -- see HUE_MIN_RATIO /
  SAT_WINDOW below. If a pixel check fails while its logic-level twin
  passes, the HUD colors are still logically correct; tune, don't panic.

Also regenerates the README's HUD screenshots into verify/out/
(hud_line_green.png = ON_AXIS_V, hud_rect_red.png = ON_AXIS_U in rect mode,
hud_parallel.png = PARALLEL) -- same filenames as docs/screenshots/.

Result is printed to stdout AND written to
``verify/out/hud_color_driver.result.txt`` (grep that file in CI; GUI
startup scripts do not reliably propagate process exit codes).
"""
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_OUT_DIR = os.path.join(_REPO_ROOT, "verify", "out")
try:
    import freecad
    freecad.__path__ = [os.path.join(_REPO_ROOT, "freecad")] + list(freecad.__path__)
except ImportError:
    sys.path.insert(0, _REPO_ROOT)

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtGui, QtWidgets  # noqa: E402

from freecad.SketchLayerWB import geom  # noqa: E402
from freecad.SketchLayerWB import inference as infer  # noqa: E402
from freecad.SketchLayerWB.draw_controller import (  # noqa: E402
    DrawController, MODE_LINE, MODE_RECT)

# Pixel-check tolerances -- the knobs to tune on a new machine/driver.
SAT_WINDOW = 8        # half-size of the search window around the marker pixel
HUE_MIN_RATIO = 1.4   # dominant channel must exceed each other channel x this
HUE_MIN_LEVEL = 60    # and be at least this bright (0-255)

V = App.Vector


def pump(n=5):
    """Flush QTimer-deferred scene-graph inserts (hud.py uses
    QTimer.singleShot(0, ...)) and repaint."""
    app = QtWidgets.QApplication.instance()
    for _ in range(n):
        app.processEvents()
    try:
        Gui.updateGui()
    except Exception:
        pass


def get_view(doc):
    gui_doc = Gui.getDocument(doc.Name)
    view = getattr(gui_doc, "ActiveView", None)
    if view is None:
        view = gui_doc.createView()
    return view


def field_rgb(mfcolor):
    """First color of an SoMFColor field as an (r, g, b) tuple of floats."""
    v = mfcolor[0]
    try:
        return (float(v[0]), float(v[1]), float(v[2]))
    except Exception:
        return tuple(float(c) for c in v.getValue())


def rgb_approx(a, b, tol=0.01):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def hue_ok(color, expected_rgb):
    """Hue-dominance test tolerant of lighting/antialiasing: the channels
    that dominate in the expected 0..1 color must dominate the pixel too."""
    r, g, b = color.red(), color.green(), color.blue()
    er, eg, eb = expected_rgb
    got = {"r": r, "g": g, "b": b}
    doms = [k for k, v in (("r", er), ("g", eg), ("b", eb)) if v >= 0.5]
    non = [k for k in "rgb" if k not in doms]
    for d in doms:
        if got[d] < HUE_MIN_LEVEL:
            return False
        for n in non:
            if got[d] < HUE_MIN_RATIO * max(got[n], 1):
                return False
    return True


def frame_scene(view, cx=7.0, cy=7.0, height=45.0):
    """Put an orthographic top camera over the scenario area so every marker
    projects inside the frame. fitAll cannot see the Coin overlay (the HUD is
    not a document object), so an explicit camera is required."""
    try:
        view.setCameraType("Orthographic")
    except Exception:
        pass
    cam = view.getCameraNode()
    cam.position.setValue(cx, cy, 50.0)
    cam.orientation.setValue(0.0, 0.0, 0.0, 1.0)  # identity: looking -Z, +Y up
    try:
        cam.height.setValue(height)  # SoOrthographicCamera view volume height
    except Exception:
        pass
    pump()


def pixel_check(view, world_point, expected_rgb, label):
    """Render at the widget's own size (so getPointOnScreen coords line up
    with the PNG), then hue-test the most saturated pixel near the marker."""
    frame_scene(view)
    frame = os.path.join(_OUT_DIR, "_hud_frame_%s.png" % label)
    pump()
    view.saveImage(frame)  # current widget size -- coordinate systems agree
    img = QtGui.QImage(frame)
    if img.isNull():
        raise AssertionError("could not read rendered frame %s" % frame)
    sx, sy = view.getPointOnScreen(world_point)
    # getPointOnScreen returns bottom-left-origin (OpenGL) coordinates;
    # QImage rows are top-down. Flip, or the search window lands mirrored.
    sy = img.height() - 1 - sy
    best = None
    for dy in range(-SAT_WINDOW, SAT_WINDOW + 1):
        for dx in range(-SAT_WINDOW, SAT_WINDOW + 1):
            x, y = int(sx) + dx, int(sy) + dy
            if 0 <= x < img.width() and 0 <= y < img.height():
                c = img.pixelColor(x, y)
                sat = max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue())
                if best is None or sat > best[0]:
                    best = (sat, c)
    if best is None:
        raise AssertionError("marker pixel (%s, %s) outside the frame" % (sx, sy))
    if not hue_ok(best[1], expected_rgb):
        raise AssertionError(
            "%s: pixel near marker is rgb(%d, %d, %d), expected hue of %s "
            "(tune SAT_WINDOW/HUE_MIN_RATIO if the scene lighting differs)"
            % (label, best[1].red(), best[1].green(), best[1].blue(), expected_rgb))


def run_scenario(doc, view, label, mode, points, cursor, expected_category,
                 screenshot=None):
    """Drive one inference scenario through the real DrawController (with a
    live view, so the real InferenceHUD is built) and check it both ways."""
    ctl = DrawController(doc, view=view)
    ctl.start(geom.Plane.xy(), mode, endpoint_world=1.0)
    pump()  # the HUD's scene-graph insert is QTimer-deferred
    for p in points:
        ctl.add_point(p)
        pump()
    inference = ctl.move_to(cursor)
    pump()
    assert inference is not None, "%s: move_to returned no inference" % label
    assert inference.category == expected_category, \
        "%s: category is %s, expected %s" % (label, inference.category, expected_category)
    expected = infer.COLORS[expected_category]

    # LOGIC LEVEL: the Coin fields carry exactly the category color.
    assert ctl.hud is not None, "%s: no HUD was created" % label
    assert ctl.hud.marker_switch.whichChild.getValue() == 0, "%s: marker switch off" % label
    assert rgb_approx(field_rgb(ctl.hud.marker_color.rgb), expected), \
        "%s: marker color %s != %s" % (label, field_rgb(ctl.hud.marker_color.rgb), expected)
    if inference.guide is not None:
        assert ctl.hud.guide_switch.whichChild.getValue() == 0, "%s: guide switch off" % label
        assert rgb_approx(field_rgb(ctl.hud.guide_color.rgb), expected), \
            "%s: guide color mismatch" % label
    print("[ ok ] %s: HUD fields carry %s (%s)" % (label, expected_category, expected))

    # PIXEL LEVEL: the rendered frame shows that hue at the inference point.
    pixel_check(view, inference.point, expected, label)
    print("[ ok ] %s: rendered pixel hue matches %s" % (label, expected_category))

    if screenshot:
        shot = os.path.join(_OUT_DIR, screenshot)
        pump()
        view.saveImage(shot, 900, 640, "Current")
        print("[ ok ] %s: screenshot %s" % (label, shot))

    ctl.cancel()
    pump()


def main():
    os.makedirs(_OUT_DIR, exist_ok=True)
    doc = App.newDocument("SketchLayerHudVerify")
    App.setActiveDocument(doc.Name)
    try:
        Gui.ActiveDocument = Gui.getDocument(doc.Name)
    except Exception:
        pass
    view = get_view(doc)
    try:
        view.viewTop()
        view.fitAll()
    except Exception:
        pass
    pump()

    # green: on the V axis, line mode (README screenshot hud_line_green.png)
    run_scenario(doc, view, "axis_v", MODE_LINE,
                 [V(0, 0, 0)], V(0.3, 10, 0),
                 infer.ON_AXIS_V, screenshot="hud_line_green.png")

    # red: on the U axis, rectangle mode (README screenshot hud_rect_red.png)
    run_scenario(doc, view, "axis_u", MODE_RECT,
                 [V(0, 0, 0)], V(10, 0.3, 0),
                 infer.ON_AXIS_U, screenshot="hud_rect_red.png")

    # magenta: parallel to the previous segment (screenshot hud_parallel.png)
    run_scenario(doc, view, "parallel", MODE_LINE,
                 [V(0, 0, 0), V(7, 7, 0)], V(14, 14.5, 0),
                 infer.PARALLEL, screenshot="hud_parallel.png")

    # green dot: endpoint/close-the-loop marker (logic + pixel, no doc shot)
    run_scenario(doc, view, "endpoint", MODE_LINE,
                 [V(0, 0, 0), V(10, 0, 0), V(10, 10, 0)], V(0.3, 0.2, 0),
                 infer.ENDPOINT)


if __name__ == "__main__":
    status, detail = "PASS", ""
    try:
        main()
    except Exception:  # noqa: BLE001
        status, detail = "FAIL", traceback.format_exc()
        print(detail)
    os.makedirs(_OUT_DIR, exist_ok=True)
    with open(os.path.join(_OUT_DIR, "hud_color_driver.result.txt"), "w") as fh:
        fh.write("%s\n%s" % (status, detail))
    print("hud_color_driver: %s" % status)
    QtCore.QTimer.singleShot(0, QtWidgets.QApplication.instance().quit)
    if status != "PASS":
        sys.exit(1)
