"""
Import rooting — the hazard that makes every other test lie.

src/frontend/main.py imports its siblings absolutely (`from frontend.widgets
import ...`) and ships as `python src\\frontend\\main.py`, so src/ is the
package root. A harness that instead does `import src.frontend.main` loads a
SECOND copy of the module tree. Nothing raises; but the two copies have
distinct class objects, so `isinstance(x, PulseDialog)` silently returns
False and code that works at runtime appears broken under test.

That cost a real debugging cycle: PulseApp.resizeEvent's
`isinstance(active, PulseDialog) -> refit_dialog(...)` guard was reported
as a failure when it was fine — only the test's object graph was wrong.
"""
from __future__ import annotations

import sys


def test_app_is_importable_as_frontend_not_src_frontend():
    import frontend.main            # noqa: F401
    assert "frontend.main" in sys.modules


def test_no_duplicate_module_tree_is_loaded():
    """If both spellings are present, class identity is already broken."""
    duplicates = sorted(
        name for name in sys.modules
        if name.startswith("src.frontend") or name.startswith("src.utils"))
    assert not duplicates, (
        "a second copy of the app modules is loaded under 'src.*': "
        f"{duplicates}. Root sys.path at src/ and import 'frontend.*'.")


def test_class_identity_holds_across_modules():
    """The exact identity PulseApp.resizeEvent depends on."""
    import frontend.main as main_mod
    from frontend.widgets import PulseDialog
    assert main_mod.PulseDialog is PulseDialog


def test_dialog_subclasses_are_recognised_as_pulse_dialogs():
    from frontend.widgets import PulseDialog, ShortcutSheetDialog
    assert issubclass(ShortcutSheetDialog, PulseDialog)
