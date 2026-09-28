"""Repository-level regression test for the completed LocalEmu migration.

The local development requirement is intentionally implemented with LocalEmu.
This test prevents the retired emulator name from being accidentally reintroduced
into source, infrastructure, scripts or documentation during later edits.
"""

from pathlib import Path


def test_retired_emulator_is_not_referenced_anywhere_in_repository():
    root = Path(__file__).resolve().parents[1]
    forbidden = ("local" + "stack").lower()
    ignored_parts = {".git", ".venv", ".localemu-venv", "__pycache__", ".pytest_cache"}

    offenders = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored_parts for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, OSError):
            continue
        if forbidden in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == []
