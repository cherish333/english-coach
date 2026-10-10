"""Layout regression tests — measured with real browser layout.

These defects were found by measuring the rendered page in Chromium, not by
reading CSS:

* The typing toolbar's mode button inherited the 32px icon width, so its text
  label wrapped onto three lines (scrollHeight 78px in a 32px box) and spilled
  the toolbar out of its container.
* Fullscreen immersive mode stacked the sentence, translation and typing surface
  vertically; on a landscape monitor (height-constrained) the content needed
  891px in a 993px viewport and always scrolled, squeezing the typing surface to
  518px.
* `view-split` / `view-typing-only` / `view-notes-only` were toggled by JS but had
  no CSS, so the portrait panel tabs never redistributed space.
* Column widths were fixed at 320px/360px, ignoring the display size entirely.

Skipped when Chromium is unavailable.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS = PROJECT_ROOT / "tests" / "js" / "layout_harness.js"
PORTRAIT_HARNESS = PROJECT_ROOT / "tests" / "js" / "portrait_layout_harness.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or shutil.which("chromium") is None,
    reason="Chromium + Node are required for layout measurement tests",
)


def _measure(harness: Path, resolution: str) -> dict:
    proc = subprocess.run(
        ["node", str(harness), resolution],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=240,
    )
    assert proc.returncode == 0, f"layout harness failed:\n{proc.stdout}\n{proc.stderr}"
    line = [ln for ln in proc.stdout.strip().splitlines() if ln.strip().startswith("{")]
    assert line, f"no measurement produced:\n{proc.stdout}\n{proc.stderr}"
    data = json.loads(line[-1])
    if not data.get("available"):
        pytest.skip(f"layout measurement unavailable: {data.get('reason')}")
    return data


@pytest.fixture(scope="module")
def wide_1920():
    return _measure(HARNESS, "1920x1080")


@pytest.fixture(scope="module")
def wide_2560():
    return _measure(HARNESS, "2560x1440")


@pytest.fixture(scope="module")
def wide_4k():
    return _measure(HARNESS, "3840x2160")


@pytest.fixture(scope="module")
def portrait():
    return _measure(PORTRAIT_HARNESS, "1080x1920")


# ---------------------------------------------------------------------------
# The typing toolbar must not overflow its own row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", ["wide_1920", "wide_2560", "wide_4k"])
def test_typing_toolbar_does_not_overflow(fixture_name, request):
    """Regression: the mode button's label wrapped and stretched the toolbar.

    Content height exceeded the container height, so the toolbar visibly spilled
    out of the 32px row.
    """
    data = request.getfixturevalue(fixture_name)
    group = data["typingGroup"]
    assert group["scrollH"] <= group["h"] + 2, (
        f"toolbar content is {group['scrollH']}px tall inside a {group['h']}px box"
    )


@pytest.mark.parametrize("fixture_name", ["wide_1920", "wide_2560", "wide_4k"])
def test_no_typing_control_is_clipped(fixture_name, request):
    """Every visible control must fit inside the toolbar's width."""
    data = request.getfixturevalue(fixture_name)
    group = data["typingGroup"]
    visible = [c for c in data["typingControls"] if c["w"] > 0]
    assert visible, "no visible typing controls found"
    span = max(c["x"] + c["w"] for c in visible) - min(c["x"] for c in visible)
    assert span <= group["w"] + 2, (
        f"controls span {span}px inside a {group['w']}px toolbar — the rightmost are unreachable"
    )


def test_mode_button_label_is_not_squeezed(wide_1920):
    """The labelled mode button must be wider than a bare icon button."""
    buttons = {c["id"]: c for c in wide_1920["typingControls"]}
    mode = buttons.get("typing-mode-btn")
    assert mode and mode["w"] > 40, f"mode button is only {mode and mode['w']}px wide"


# ---------------------------------------------------------------------------
# Fullscreen immersive layout must fit, and give the typing surface the space
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", ["wide_1920", "wide_2560", "wide_4k"])
def test_fullscreen_does_not_overflow(fixture_name, request):
    """Regression: stacked layout needed more height than a landscape screen has."""
    data = request.getfixturevalue(fixture_name)
    fs = data["fullscreen"]
    assert fs["overflows"] is False, (
        f"fullscreen card scrolls: content {fs['card']['scrollH']}px vs "
        f"{fs['card']['clientH']}px viewport"
    )


@pytest.mark.parametrize("fixture_name", ["wide_1920", "wide_2560"])
def test_fullscreen_typing_surface_gets_most_of_the_height(fixture_name, request):
    """The typing surface is what the learner uses, so it must dominate."""
    data = request.getfixturevalue(fixture_name)
    fs = data["fullscreen"]
    viewport_h = data["viewport"]["h"]
    assert fs["typingDisplay"]["h"] >= viewport_h * 0.6, (
        f"typing surface is only {fs['typingDisplay']['h']}px of a {viewport_h}px screen"
    )


def test_fullscreen_reference_material_sits_beside_the_typing_area(wide_1920):
    """The sentence/translation belong in the left column, not above the typing."""
    fs = wide_1920["fullscreen"]
    # Side by side means the reference blocks are narrower than the card.
    card_w = fs["card"]["w"]
    assert fs["preview"]["w"] < card_w * 0.6, (
        "the sentence preview still spans the full card width — the two-column "
        "grid is not applied"
    )
    assert fs["typingDisplay"]["w"] < card_w * 0.8


def test_fullscreen_type_scales_down_on_short_screens(wide_1920):
    """A 1080p screen must not use the 4K type scale (which forced scrolling)."""
    fs = wide_1920["fullscreen"]
    size = float(fs["typingDisplay"]["font"].replace("px", ""))
    assert size <= 32, f"typing font is {size}px on a 993px-tall viewport"


# ---------------------------------------------------------------------------
# Responsive columns
# ---------------------------------------------------------------------------

def test_side_columns_grow_with_the_display(wide_1920, wide_4k):
    """Regression: columns were pinned at 320px/360px on every display."""
    left_small = wide_1920["colLeft"]["w"]
    left_big = wide_4k["colLeft"]["w"]
    right_small = wide_1920["colRight"]["w"]
    right_big = wide_4k["colRight"]["w"]
    assert left_big > left_small, f"left column did not scale ({left_small} -> {left_big})"
    assert right_big > right_small, f"right column did not scale ({right_small} -> {right_big})"


def test_columns_stay_within_usable_bounds(wide_4k):
    """Scaling must not let a side panel take over the workspace."""
    total = wide_4k["viewport"]["w"]
    left = wide_4k["colLeft"]["w"]
    right = wide_4k["colRight"]["w"]
    assert left <= 650 and right <= 850
    assert (left + right) < total * 0.45, "side columns crowd out the center"


# ---------------------------------------------------------------------------
# Portrait screen: reference panel yields space when typing lives there
# ---------------------------------------------------------------------------

def test_portrait_reference_panel_yields_space_for_typing(portrait):
    """Regression: the PDF stage stayed pinned at 58vh even while typing here."""
    assert portrait["errors"] == [], portrait["errors"]
    docked = portrait["typingDocked"]
    undocked = portrait["undocked"]
    assert docked["top"] < undocked["top"], (
        f"reference panel did not shrink ({undocked['top']} -> {docked['top']})"
    )
    assert docked["bottom"] > undocked["bottom"], (
        f"bottom panel did not gain space ({undocked['bottom']} -> {docked['bottom']})"
    )
    # The typing surface must end up with the majority of the freed height.
    assert docked["typingArea"] > undocked["typingArea"] * 1.5, (
        f"typing area grew only {undocked['typingArea']} -> {docked['typingArea']}"
    )


def test_portrait_view_modes_redistribute_space(portrait):
    """Regression: the four panel tabs toggled a class that had no CSS.

    Measured before the fix: every mode reported an identical typing area, so
    choosing "专注跟打" changed nothing at all.
    """
    modes = portrait["modes"]
    assert set(modes) == {"split", "typing", "sentences", "notes"}, modes

    split_area = modes["split"]["typingArea"]
    # 专注跟打 must substantially enlarge the typing surface, not just hide panels.
    assert modes["typing"]["typingArea"] > split_area * 1.4, (
        f"专注跟打 did not enlarge the typing surface meaningfuly: "
        f"{split_area} -> {modes['typing']['typingArea']}"
    )
    # 课文与句子 must give the height to the sentence list instead.
    assert modes["sentences"]["typingVisible"] is False, modes["sentences"]
    assert modes["sentences"]["sentencesVisible"] is True, modes["sentences"]
    # 智能板书 must show the notes panel and hide the typing surface.
    assert modes["notes"]["notesVisible"] is True, modes["notes"]
    assert modes["notes"]["typingVisible"] is False, modes["notes"]
    assert modes["notes"]["notesArea"] > 400, modes["notes"]


def test_portrait_typing_readable_on_a_tall_display(portrait):
    """The point of typing on a portrait screen is the larger type."""
    size = float(str(portrait["typingDocked"]["typingFont"]).replace("px", ""))
    assert size >= 24, f"portrait typing font is only {size}px on a 1833px-tall screen"


# ---------------------------------------------------------------------------
# Fullscreen toolbar (the reported "下面的 UI 显示有问题")
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", ["wide_1920", "wide_2560", "wide_4k"])
def test_no_fullscreen_control_overflows_its_button(fixture_name, request):
    """The reported bug: the labelled mode button inherited the 44px icon width.

    Its text then spilled ~46px outside its own box top-right, visually colliding
    with the neighbouring button ("对照跟打" rendered over the repeat control).
    Detected as scrollHeight exceeding the button's own height.
    """
    data = request.getfixturevalue(fixture_name)
    toolbar = data["fullscreen"]["toolbar"]
    assert toolbar is not None
    assert toolbar["overflowingLabels"] == [], (
        f"controls whose text overflows their box: {toolbar['overflowingLabels']}"
    )


@pytest.mark.parametrize("fixture_name", ["wide_1920", "wide_2560", "wide_4k"])
def test_fullscreen_toolbar_stays_on_one_row(fixture_name, request):
    """The toolbar must not wrap into a second row inside fullscreen mode.

    Regression: the labelled mode button overflowed its 44px box, and the fix for
    that (giving it its natural width) then pushed the row onto a second line.
    """
    data = request.getfixturevalue(fixture_name)
    toolbar = data["fullscreen"]["toolbar"]
    assert toolbar is not None
    # Nine icon buttons + the labelled mode button + the auto-focus label.
    assert toolbar["visibleControls"] >= 11, toolbar
    assert toolbar["rows"] == 1, (
        f"the fullscreen toolbar wrapped onto {toolbar['rows']} rows "
        f"(max controls on a row: {toolbar['maxPerRow']} of {toolbar['visibleControls']})"
    )


@pytest.mark.parametrize("fixture_name", ["wide_1920", "wide_2560", "wide_4k"])
def test_fullscreen_toolbar_is_inside_the_card(fixture_name, request):
    """Nothing may be pushed outside the visible fullscreen card."""
    data = request.getfixturevalue(fixture_name)
    toolbar = data["fullscreen"]["toolbar"]
    assert toolbar["insideCard"] is True, toolbar["debug"]
    assert toolbar["hintBarInside"] is True, toolbar["debug"]
