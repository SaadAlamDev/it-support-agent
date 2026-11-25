"""Couple disposable application and checkpoint state without deleting existing data."""

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


@contextmanager
def demo_storage(settings):
    if not settings.reset_demo_on_start:
        yield settings, None
        return

    # This mode is explicitly restricted to cloud/ephemeral in Settings. Each
    # process gets a fresh pair, including WAL files, seeds, and quota counters.
    # Never delete/reuse a configured local or persistent database to reset it.
    parent = settings.database_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="servicedesk-demo-", dir=parent) as directory:
        root = Path(directory)
        runtime = settings.model_copy(
            update={
                "database_path": root / "support.db",
                "checkpoint_path": root / "checkpoints.db",
            }
        )
        yield runtime, uuid4().hex
