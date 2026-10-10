"""Shared test configuration.

Critically, this isolates the SQLite database *before* any application module is
imported. The database path used to be a hard-coded module constant pointing at
``data/coach_notes.db``, so running the suite wrote into the learner's real
database: it inserted "⚠️ 错题强化: Test sentence" notes and odometer word rows,
which is what made ``user_gamification.words_typed`` drift away from
``COUNT(*) FROM typed_words_log``.

Every test now runs against a throwaway database. The schema is created by the
normal import-time initialisers as soon as ``conftest`` has set the variable,
so tests see a fully migrated, empty database.
"""

import atexit
import os
import shutil
import tempfile

_TEMP_DIR = tempfile.mkdtemp(prefix="coach-tests-")
os.environ["COACH_DB_PATH"] = os.path.join(_TEMP_DIR, "coach_notes.db")
# Belt and braces: make sure nothing in the suite can reach the real media or
# document stores either.
os.environ.setdefault("PYTHONHASHSEED", "0")

atexit.register(shutil.rmtree, _TEMP_DIR, True)
