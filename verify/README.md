<!-- DRAFT/UNVERIFIED — written 2026-07-18 by an agent WITHOUT FreeCAD available; never executed. mathmati must run with freecadcmd and adjust before committing. -->
> **DRAFT — for mathmati to review and post manually.**
> Target: `verify/` directory of <https://github.com/mathmati/FreeCAD-SketchLayer>
> Status: **DRAFT/UNVERIFIED** — written 2026-07-18 by an agent without FreeCAD
> on the machine; **never executed**. Run everything below, fix what breaks,
> and only then commit.

# SketchLayer `verify/`: how to run

This tree is the verification package the SketchLayer README's
**"Verification"** section describes, split by what is and isn't
headless-feasible:

| README claim | Where it's checked |
|---|---|
| headless (`freecadcmd`) regression: inference resolver, face builder, full draw state machine incl. typed dimensions + coplanarity guard, midpoint/center snapping, arrow-key axis lock | `headless_regression.py` (44 checks) |
| HUD renders the **correct color per category, pixel level** (red/green/magenta) | `drivers/hud_color_driver.py` (needs GUI under Xvfb) |
| a closed rectangle commits a single planar face of the expected area | headless checks 16–17 **and** `drivers/draw_commit_driver.py` (via the real registered command) |
| workbench + all five commands auto-register with zero Report-View errors | `drivers/draw_commit_driver.py` (registration asserted; **"zero errors" is a manual eyeball step**, see below) |
| live drawing driven through the Gui-decoupled `DrawController` | everywhere: all scripts drive `DrawController`/the real commands |

## Requirements

- FreeCAD 1.1+ (`freecadcmd` and `freecad` on PATH)
- For the GUI drivers: Linux with `xvfb-run`. On Windows, run them under a
  real desktop session instead (a window will flash open).

## Run

```bash
# headless regression
freecadcmd verify/headless_regression.py
echo $?   # 0 when green; final line prints "44/44 checks pass"

# GUI drivers (virtual framebuffer)
xvfb-run -a -s "-screen 0 1280x1024x24" freecad verify/drivers/hud_color_driver.py
xvfb-run -a -s "-screen 0 1280x1024x24" freecad verify/drivers/draw_commit_driver.py
```

GUI drivers print `PASS`/`FAIL`, drop screenshots in `verify/out/` (the same
filenames as `docs/screenshots/`: `hud_line_green.png`, `hud_rect_red.png`,
`hud_parallel.png`, `face_result.png`, so regenerated shots can be copied
there), and write `verify/out/<driver>.result.txt`. **Use the result file in
CI**: process exit codes from a GUI startup script are not reliable.

## What is headless-infeasible, and how it's substituted

- **Pixel-level color checks cannot run under `freecadcmd`** (no 3D view, no
  renderer). The headless script therefore asserts the *logic-level* twin:
  every inference category resolves with the exact
  `inference.COLORS[category]` value. The actual pixel check lives in
  `drivers/hud_color_driver.py`, which does BOTH the Coin scene-graph field
  assertion and the rendered-frame hue sample.
- **Draft `Snapper` integration** needs the GUI snapper and is not covered by
  any script (the README only claims best-effort reuse, so nothing to
  verify).
- **"Zero Report-View errors"** is not asserted programmatically: FreeCAD's
  Python layer exposes no console-message observer. `draw_commit_driver.py`
  asserts registration (the five command names in `Gui.listCommands()` after
  workbench activation) and prints a reminder to eyeball the Report View
  once. *Flagged: the README wording implies this was verified; the draft
  can only automate the registration half.*

## Known-fragile spots to adjust before committing

- **Pixel hue thresholds** (`SAT_WINDOW`, `HUE_MIN_RATIO`, `HUE_MIN_LEVEL`
  at the top of `hud_color_driver.py`) depend on scene lighting, GPU/driver
  antialiasing, and the Coin light model. If a pixel check fails while its
  logic-level twin passes, tune those knobs; the HUD colors are still
  logically right.
- **HUD insert is `QTimer`-deferred** (`hud.py`); the drivers `pump()`
  (`processEvents`) before asserting. Increase the pump count if a HUD
  assertion fires spuriously.
- **Pixel sampling renders at the widget's own size** so
  `view.getPointOnScreen()` coordinates line up with the saved PNG; the
  pretty 900×640 screenshots are rendered separately.
- **Digit-key race:** typed dims go through the real application-level
  `_KeyFilter` with ShortcutOverride + KeyPress pairs. If digit handling
  ever regresses, `draw_commit_driver.py`'s `typed_buffer` assertion fails
  first.
