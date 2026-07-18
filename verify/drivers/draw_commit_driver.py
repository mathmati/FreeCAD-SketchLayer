# DRAFT/UNVERIFIED — written 2026-07-18 by an agent WITHOUT FreeCAD available;
# never executed. mathmati must run with freecadcmd and adjust before committing.
# SPDX-License-Identifier: MIT
"""verify/drivers/draw_commit_driver.py -- GUI (Xvfb) registration + commit.

Run under a virtual X server from the repo root:

    xvfb-run -a -s "-screen 0 1280x1024x24" freecad verify/drivers/draw_commit_driver.py

What this is: the remaining GUI-only clauses of the SketchLayer README's
Verification section:

  1. "the workbench and all five commands auto-register with zero Report-View
     errors" -- imports the real workbench module, activates it, and asserts
     ``SketchLayer_Line`` / ``SketchLayer_Rectangle`` / ``SketchLayer_Circle``
     / ``SketchLayer_Polygon`` / ``SketchLayer_Arc`` appear in
     ``Gui.listCommands()``. NOTE: "zero Report-View errors" is NOT asserted
     programmatically (FreeCAD's Python layer exposes no console observer);
     eyeball the Report View once after activation -- flagged in
     verify/README.md as a manual step.
  2. "a closed rectangle commits a single planar face of the expected area"
     -- driven through the REAL registered command (``Gui.runCommand``,
     exactly what the toolbar button runs), with clicks simulated via the
     documented scripted fallback (``controller.add_point(...)`` -- the same
     method the SoMouseButtonEvent handler calls) and the ``W,H`` typed via
     GENUINELY SYNTHETIC Qt keyboard input (real QKeyEvents, ShortcutOverride
     + KeyPress, dispatched through the installed application event filter).
  3. Regenerates the README's committed-face screenshot
     (verify/out/face_result.png, same filename as docs/screenshots/).

Result is printed to stdout AND written to
``verify/out/draw_commit_driver.result.txt`` (grep that file in CI; GUI
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

from freecad.SketchLayerWB import commands as sl_commands  # noqa: E402

RECT_W, RECT_H = 30.0, 20.0  # typed as "30,20"


def pump(n=5):
    app = QtWidgets.QApplication.instance()
    for _ in range(n):
        app.processEvents()
    try:
        Gui.updateGui()
    except Exception:
        pass


def send_key(key, text=""):
    """Dispatch the ShortcutOverride + KeyPress pair a real keypress
    produces, through the application-level _KeyFilter."""
    mw = Gui.getMainWindow()
    override = QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, key,
                               QtCore.Qt.NoModifier, text)
    QtWidgets.QApplication.sendEvent(mw, override)
    press = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key,
                            QtCore.Qt.NoModifier, text)
    QtWidgets.QApplication.sendEvent(mw, press)
    pump()


def get_view(doc):
    gui_doc = Gui.getDocument(doc.Name)
    view = getattr(gui_doc, "ActiveView", None)
    if view is None:
        view = gui_doc.createView()
    return view


def check_registration():
    # Importing init_gui runs Gui.addWorkbench(...) at module scope -- the
    # same thing FreeCAD's addon loader does for an installed addon.
    from freecad.SketchLayerWB import init_gui  # noqa: F401
    Gui.activateWorkbench("SketchLayerWorkbench")  # runs Initialize() -> register()
    pump()
    names = list(Gui.listCommands())
    for cmd in ("SketchLayer_Line", "SketchLayer_Rectangle",
                "SketchLayer_Circle", "SketchLayer_Polygon",
                "SketchLayer_Arc"):
        assert cmd in names, "%s not registered (listCommands has %d entries)" \
            % (cmd, len(names))
    print("[ ok ] workbench + all five commands auto-register "
          "(MANUAL STEP: confirm the Report View showed zero errors during activation)")


def check_typed_rectangle(doc):
    Gui.runCommand("SketchLayer_Rectangle")  # exactly what the toolbar runs
    pump()
    session = sl_commands._RectangleCommand._session
    assert session is not None and session.controller is not None, \
        "command activation did not create a draw session"
    ctl = session.controller
    assert ctl.active, "draw session not active after command activation"

    # First corner via the documented scripted fallback (the same method the
    # SoMouseButtonEvent handler calls on a real click).
    assert ctl.add_point(App.Vector(0, 0, 0)) is None, \
        "first corner unexpectedly finished the rectangle"
    # Hover into the (+,+) quadrant so typed W,H extend that way.
    ctl.move_to(App.Vector(5, 5, 0))
    pump()

    # W,H via genuinely synthetic Qt keyboard input through the real filter.
    for ch in "30,20":
        key = {"0": QtCore.Qt.Key_0, "2": QtCore.Qt.Key_2, "3": QtCore.Qt.Key_3,
               ",": QtCore.Qt.Key_Comma}[ch]
        send_key(key, ch)
    assert ctl.typed_buffer == "30,20", \
        "typed_buffer is %r -- synthetic digits did not reach the controller" \
        % ctl.typed_buffer

    send_key(QtCore.Qt.Key_Return, "\r")
    obj = ctl.committed_object
    assert obj is not None, "typed rectangle did not commit: %s" % ctl.last_message
    assert obj.TypeId == "Part::Feature", "TypeId is %s" % obj.TypeId
    faces = getattr(obj.Shape, "Faces", [])
    assert len(faces) == 1, "expected a single face, Shape has %d" % len(faces)
    assert abs(obj.Shape.Area - RECT_W * RECT_H) < 1e-6, \
        "face area is %r, expected %r" % (obj.Shape.Area, RECT_W * RECT_H)
    assert obj.Shape.isValid(), "committed face is invalid"
    print("[ ok ] typed rectangle committed one planar face, area %.6g"
          % obj.Shape.Area)
    return obj


def main():
    os.makedirs(_OUT_DIR, exist_ok=True)
    check_registration()

    doc = App.newDocument("SketchLayerDrawVerify")
    App.setActiveDocument(doc.Name)
    try:
        Gui.ActiveDocument = Gui.getDocument(doc.Name)
    except Exception:
        pass
    view = get_view(doc)
    obj = check_typed_rectangle(doc)

    try:
        view.fitAll()
    except Exception:
        pass
    shot = os.path.join(_OUT_DIR, "face_result.png")
    pump()
    Gui.getMainWindow().grab().save(shot)
    print("[ ok ] committed face screenshot: %s" % shot)


if __name__ == "__main__":
    status, detail = "PASS", ""
    try:
        main()
    except Exception:  # noqa: BLE001
        status, detail = "FAIL", traceback.format_exc()
        print(detail)
    os.makedirs(_OUT_DIR, exist_ok=True)
    with open(os.path.join(_OUT_DIR, "draw_commit_driver.result.txt"), "w") as fh:
        fh.write("%s\n%s" % (status, detail))
    print("draw_commit_driver: %s" % status)
    QtCore.QTimer.singleShot(0, QtWidgets.QApplication.instance().quit)
    if status != "PASS":
        sys.exit(1)
