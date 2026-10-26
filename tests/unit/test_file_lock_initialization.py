"""A shared lock must not write an initialization byte before acquiring ownership."""

from pathlib import Path

from codex_ai_os.infrastructure.files import execution_lock_active, file_lock


def test_lock_acquires_empty_file_without_unprotected_initialization(tmp_path, monkeypatch):
    target = tmp_path / "shared.lock"
    original = Path.open

    class GuardedFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, data):
            raise PermissionError("another owner may already hold the initialization byte")

    def open_file(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        return GuardedFile(handle) if path == target and args == ("a+b",) else handle

    monkeypatch.setattr(Path, "open", open_file)
    with file_lock(target):
        assert execution_lock_active(target)
    assert not execution_lock_active(target)
