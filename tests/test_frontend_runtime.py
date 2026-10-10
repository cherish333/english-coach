"""Executable front-end integration tests.

The rest of the suite asserts against app.js *as text*, which cannot catch a
runtime error inside an event handler. That gap shipped a real regression: a
refactor removed the `let correctCount = 0;` declaration from
`handleTypingInput` while keeping `correctCount++` and its use in the progress
label, so **every keystroke threw a ReferenceError and the typing area appeared
frozen**.

These tests load index.html + app.js into a real DOM (jsdom) and drive actual
keyboard events, so a broken handler fails the suite instead of failing silently
in the browser.

They are skipped when Node or the vendored jsdom are unavailable; the JS
dependencies live in tests/js (installed with `npm install` inside that folder).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JS_DIR = PROJECT_ROOT / "tests" / "js"
HARNESS = JS_DIR / "typing_harness.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (JS_DIR / "node_modules" / "jsdom").is_dir(),
    reason="Node + vendored jsdom are required for DOM integration tests "
           "(run `npm install` inside tests/js)",
)


@pytest.fixture(scope="module")
def harness_result():
    """Run the node harness once and return its JSON verdict."""
    proc = subprocess.run(
        ["node", str(HARNESS)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"front-end harness failed (exit {proc.returncode})\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    # The harness prints a single JSON document on the last line.
    last = [line for line in proc.stdout.strip().splitlines() if line.strip().startswith("{")]
    assert last, f"harness produced no JSON verdict:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(last[-1])


def test_app_js_loads_without_errors(harness_result):
    """A load-time exception would leave the whole UI dead."""
    assert harness_result["loadErrors"] == [], harness_result["loadErrors"]
    assert harness_result["errorsDuringTyping"] == [], harness_result["errorsDuringTyping"]


def test_typing_area_accepts_input(harness_result):
    """The exact reported symptom: characters must appear as you type."""
    typing = harness_result["copyMode"]
    assert typing["buffer"] == typing["target"], (
        f"typed buffer is {typing['buffer']!r}, expected {typing['target']!r}"
    )
    assert typing["visibleAfter"] == typing["target"], (
        "the rendered text did not keep up with typing"
    )
    assert typing["completed"] is True, "sentence never registered as completed"
    assert typing["progressText"].startswith("进度: "), typing["progressText"]
    # The progress counter must advance, not stay at 0/len.
    assert not typing["progressText"].startswith("进度: 0 /"), (
        f"progress counter is stuck: {typing['progressText']}"
    )


def test_backspace_edits_the_buffer(harness_result):
    """Backspace must work even after the sentence already completed.

    It used to be swallowed by the `isTypingCompleted` early return, so a typo in
    the final character could not be corrected without clearing the whole card.
    """
    back = harness_result["backspace"]
    assert back["buffer"] == back["target"], back
    assert len(back["buffer"]) == len(back["before"]) - 1, back


def test_wrong_character_is_marked_and_does_not_complete(harness_result):
    wrong = harness_result["wrongChar"]
    assert wrong["completed"] is False, "a mistyped sentence must not count as complete"
    assert wrong["hasWrongSpan"] is True, "the mistyped character was not flagged"


def test_retrieval_modes_hide_the_answer_while_typing(harness_result):
    for mode in ("cloze", "recall", "dictation"):
        data = harness_result[mode]
        assert data["beforeTyping"] != data["target"], f"{mode} revealed the full sentence"
        # What was typed must be visible...
        assert data["visibleAfter"].startswith("I th"), data
        # ...and the rest must still be hidden.
        assert "think" not in data["visibleAfter"], f"{mode} leaked the hidden word"
        assert data["buffer"] == "I th", data


def test_copy_mode_keeps_showing_the_text(harness_result):
    assert harness_result["copyVisibleInitially"] == harness_result["copyMode"]["target"]


def test_practice_mode_cycling_and_persistence(harness_result):
    cycle = harness_result["modeCycle"]
    assert cycle["order"] == ["cloze", "recall", "dictation", "copy"], cycle
    assert cycle["stored"] in ("copy", "cloze", "recall", "dictation"), cycle


def test_translation_is_hidden_outside_copy_mode(harness_result):
    display = harness_result["translationDisplay"]
    assert display["copy"] != "none", "the translation should be visible in copy mode"
    assert display["recall"] == "none", "the translation gives the answer away in recall mode"
    assert display["dictation"] == "none"


# ---------------------------------------------------------------------------
# Static safety net: catch undeclared identifiers without needing a DOM.
# ---------------------------------------------------------------------------

def _handler_body(source: str, name: str) -> str:
    """Return the body of a top-level `function name(...) { ... }`."""
    import re

    match = re.search(rf"^function {re.escape(name)}\([^)]*\)\s*\{{", source, re.MULTILINE)
    assert match, f"handler {name} not found in app.js"
    start = match.end()
    depth = 1
    for idx in range(start, len(source)):
        if source[idx] == "{":
            depth += 1
        elif source[idx] == "}":
            depth -= 1
            if depth == 0:
                return source[start:idx]
    raise AssertionError(f"unbalanced braces in {name}")


def test_incremented_identifiers_are_declared():
    """Every `x++` inside the typing handler must have a matching declaration.

    Removing `let correctCount = 0;` while keeping `correctCount++` made the
    typing area throw a ReferenceError on every keystroke — the field appeared
    frozen. This check catches that class of edit without needing a browser.
    """
    import re

    source = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    body = _handler_body(source, "handleTypingInput")

    incremented = set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\+\+", body))
    incremented |= set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\+=", body))
    assert incremented, "no counters found — did the handler change shape?"

    undeclared = []
    for name in incremented:
        if re.search(rf"\b(let|const|var)\s+{re.escape(name)}\b", body):
            continue
        # Could be a module-level binding assigned elsewhere.
        if re.search(rf"^\s*(let|const|var)\s+{re.escape(name)}\b", source, re.MULTILINE):
            continue
        if re.search(rf"^\s*{re.escape(name)}\s*=", body, re.MULTILINE):
            continue
        undeclared.append(name)

    assert not undeclared, (
        f"handleTypingInput increments {undeclared} without declaring it; "
        "this throws a ReferenceError on every keystroke"
    )
