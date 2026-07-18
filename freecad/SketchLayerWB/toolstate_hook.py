# SPDX-License-Identifier: MIT
"""Soft hook into Uppercut's active-tool highlight, when Uppercut is
installed. SketchLayer works unchanged when it is not: every import
failure or runtime error degrades to a no-op returning False, so the draw
session never depends on the (optional) sibling.

The call sites live in commands.py: ``tool_started`` at the end of
``_DrawSession.start`` (tool successfully armed), ``tool_finished`` in
``_DrawSession._teardown`` (the single funnel for commit, Enter-close, Esc
and the SoKeyboardEvent cancel path). The functions are pure and take an
injectable loader so the headless regression can drive them with a fake
toolstate (and with a blocked import) without FreeCADGui.
"""

#: The five draw commands, for callers that iterate; the command classes in
#: commands.py pass their own name literally.
COMMANDS = (
    "SketchLayer_Line",
    "SketchLayer_Rectangle",
    "SketchLayer_Circle",
    "SketchLayer_Polygon",
    "SketchLayer_Arc",
)


def _load_toolstate():
    """Uppercut's toolstate module, or None when Uppercut is not
    importable (not installed, or a broken install -- either way the hook
    must silently do nothing). The pre-rename SketchUI package is tried as
    a fallback for old installs."""
    try:
        from freecad.UppercutWB import toolstate
    except Exception:  # noqa: BLE001 - ImportError when Uppercut is absent
        try:
            from freecad.SketchUIWB import toolstate
        except Exception:  # noqa: BLE001 - pre-rename SketchUI install
            return None
    return toolstate


def tool_started(command_name, loader=None):
    """Mark ``command_name``'s toolbar button pressed. Returns True when a
    toolstate actually received the call, False on any absence/failure."""
    try:
        ts = (loader or _load_toolstate)()
    except Exception:  # noqa: BLE001 - a broken provider is "absent"
        return False
    if ts is None:
        return False
    try:
        ts.mark_active(command_name)
    except Exception:  # noqa: BLE001 - a broken Uppercut must not break us
        return False
    return True


def tool_finished(command_name, loader=None):
    """Clear ``command_name``'s pressed look. Same no-op contract as
    :func:`tool_started`."""
    try:
        ts = (loader or _load_toolstate)()
    except Exception:  # noqa: BLE001
        return False
    if ts is None:
        return False
    try:
        ts.mark_inactive(command_name)
    except Exception:  # noqa: BLE001
        return False
    return True
