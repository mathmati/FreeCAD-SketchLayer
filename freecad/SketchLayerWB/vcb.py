# SPDX-License-Identifier: MIT
"""ValueBox: the floating, cursor-side "measurements box" (SketchUp's VCB).

Display-only: the actual keystrokes are captured by the command's
application-level Qt key filter (see commands.py) exactly as PushPull does,
so this widget never needs focus and there is no focus-stealing fight with
the 3D view. It just shows the live dimension / the value being typed, near
the cursor.

A plain frameless ``QLabel`` child of FreeCAD's main window, positioned in
global coordinates. Kept defensive: any Qt failure degrades to a no-op (the
status-bar readout still carries the same text), never a crash mid-draw.
"""
from PySide import QtCore, QtGui, QtWidgets

_STYLE = (
    "QLabel { background: rgba(30,30,30,220); color: #f0f0f0; "
    "border: 1px solid #ffcc00; border-radius: 3px; padding: 2px 6px; "
    "font-family: monospace; font-size: 12px; }"
)


def _main_window():
    try:
        import FreeCADGui as Gui
        return Gui.getMainWindow()
    except Exception:
        return None


class ValueBox(object):
    def __init__(self):
        self.label = None
        mw = _main_window()
        if mw is None:
            return
        try:
            self.label = QtWidgets.QLabel(mw)
            self.label.setStyleSheet(_STYLE)
            self.label.setWindowFlags(
                QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
            self.label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self.label.hide()
        except Exception:
            self.label = None

    def set_text(self, text):
        if self.label is None:
            return
        try:
            if text:
                self.label.setText(text)
                self.label.adjustSize()
                self.label.show()
                self.label.raise_()
            else:
                self.label.hide()
        except Exception:
            pass

    def move_to_global(self, x, y):
        """Reposition the box a little up-and-right of a global screen point
        (typically the cursor), like SketchUp's VCB following the mouse."""
        if self.label is None:
            return
        try:
            self.label.move(int(x) + 16, int(y) + 16)
        except Exception:
            pass

    def hide(self):
        if self.label is None:
            return
        try:
            self.label.hide()
            self.label.deleteLater()
        except Exception:
            pass
        self.label = None
