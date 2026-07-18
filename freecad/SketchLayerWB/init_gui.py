# SPDX-License-Identifier: MIT
"""Workbench registration for the SketchLayer addon.

Auto-discovered by FreeCAD's Modern-layout addon loader from
Mod/<addon>/freecad/SketchLayerWB/. No network access or expensive work at
import/startup -- the tools only act on explicit user command activation.
"""
import os

import FreeCADGui as Gui


class SketchLayerWorkbench(Gui.Workbench):
    MenuText = "SketchLayer"
    ToolTip = ("SketchUp-style inline drawing with colored inference cues and "
               "type-to-dimension; makes faces ready to Push/Pull")
    Icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "Resources", "Icons", "sketchlayer.svg")

    def Initialize(self):
        from . import commands
        commands.register()
        tools = ["SketchLayer_Line", "SketchLayer_Rectangle",
                 "SketchLayer_Circle", "SketchLayer_Polygon",
                 "SketchLayer_Arc"]
        self.appendToolbar("SketchLayer", tools)
        self.appendMenu("SketchLayer", tools)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"  # exact string, mandatory, do not change


Gui.addWorkbench(SketchLayerWorkbench())
