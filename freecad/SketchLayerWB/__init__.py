# SPDX-License-Identifier: MIT
"""SketchLayer -- SketchUp-style inline sketch + inference layer for FreeCAD.

This package draws line/rectangle geometry directly in the 3D view on the
working plane (or a hovered model face), with SketchUp-style colored
inference feedback and inline type-to-dimension, and emits a lightweight
planar face ready to be extruded (e.g. by the companion PushPull addon).

Module layout mirrors the PushPull addon's proven split so the core is
verifiable headlessly:

  geom.py           -- pure vector/plane math (no FreeCAD Gui, unit-testable)
  inference.py      -- drawing-relative inference resolver (pure, testable)
  facebuilder.py    -- turn drawn points into a real Part planar face
  draw_controller.py-- the click/move/type/close state machine (Gui-decoupled)
  hud.py            -- the colored Coin3D inference HUD (Gui only)
  vcb.py            -- the floating cursor-side value box (Qt only)
  commands.py       -- Gui.Command + SoEvent/Qt wiring (Gui only)
  init_gui.py       -- workbench registration
"""
