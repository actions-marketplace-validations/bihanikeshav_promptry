"""Storage backends for promptry.

Default is SQLite. The BaseStorage interface lets you plug in your own.
Storage mode (sync/async/off/remote) is handled by get_storage().
"""
import threading

from promptry.storage.base import BaseStorage
from promptry.storage.sqlite import SQLiteStorage

# backwards compat
Storage = SQLiteStorage

_storage_instance: BaseStorage | None = None
# Guards singleton creation. Without it, threads racing on the first track()
# each build their own SQLiteStorage (separate connection + separate write
# lock), so their version-number increments collide on UNIQUE(name, version)
# and writes get silently dropped. One instance = one connection whose
# self._lock serializes every writer.
_storage_lock = threading.Lock()


def get_storage() -> BaseStorage:
    """Get the singleton storage instance based on the current config mode.

    - sync: direct SQLiteStorage (default)
    - async: SQLiteStorage wrapped in AsyncWriter (background thread)
    - remote: local SQLite + batched HTTP shipping to a remote endpoint
    - off: should not be called (track() short-circuits before this)
    """
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    with _storage_lock:
        # double-checked: another thread may have built it while we waited.
        if _storage_instance is not None:
            return _storage_instance

        from promptry.config import get_config

        config = get_config()

        if config.storage.mode == "remote":
            from promptry.storage.remote import RemoteStorage
            storage = RemoteStorage(
                endpoint=config.storage.endpoint,
                api_key=config.storage.api_key,
            )
        elif config.storage.mode == "async":
            from promptry.writer import AsyncWriter
            storage = AsyncWriter(SQLiteStorage())
        elif config.storage.mode == "postgres":
            # Opt-in scale tier (alpha). The default path below is untouched.
            from promptry.storage.postgres import PostgresStorage
            storage = PostgresStorage(dsn=config.storage.endpoint or None)
        else:
            storage = SQLiteStorage()

        _storage_instance = storage
        return storage


def reset_storage():
    """Close and discard the singleton storage instance.

    Also resets the prompt registry — it caches a reference to the
    storage instance, and without this reset, subsequent track() calls
    would operate on a closed database.
    """
    global _storage_instance
    if _storage_instance is not None:
        _storage_instance.close()
    _storage_instance = None

    # The registry caches our storage; drop its cache too so track()
    # picks up the fresh instance on next call.
    try:
        from promptry.registry import reset_registry
        reset_registry()
    except ImportError:
        pass


__all__ = ["BaseStorage", "SQLiteStorage", "Storage", "get_storage", "reset_storage"]
