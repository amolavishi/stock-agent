"""Fail-closed filesystem boundary for Obsidian projection writes.

The vault is a projection sink, not an authority source.  Every path is
relative to one pinned, non-reparse root.  Writes use compare-and-swap style
conflict checks plus a same-directory temporary file so a user edit is never
silently replaced.
"""
from __future__ import annotations

import hashlib
import os
import stat
import uuid
from pathlib import Path, PurePath


class VaultBoundaryError(RuntimeError):
    """A path cannot be proven to remain inside the configured vault."""


class VaultConflictError(VaultBoundaryError):
    """The target changed or contains user-managed content."""


class VaultIntegrityError(VaultBoundaryError):
    """A write may have partially completed and must be retried/inspected."""


def content_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _lexists(path: Path) -> bool:
    return os.path.lexists(str(path))


def _is_reparse_or_symlink(path: Path) -> bool:
    """Detect POSIX symlinks and Windows junction/reparse points."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


class SecureVault:
    """Pinned-root, no-link filesystem access with atomic verified writes."""

    def __init__(self, root: str | Path) -> None:
        configured = Path(root).expanduser()
        if _lexists(configured) and _is_reparse_or_symlink(configured):
            raise VaultBoundaryError("vault root cannot be a symlink or junction")
        configured.mkdir(parents=True, exist_ok=True)
        if _is_reparse_or_symlink(configured):
            raise VaultBoundaryError("vault root became a symlink or junction")
        self.root = configured.resolve(strict=True)
        root_stat = self.root.stat()
        if not stat.S_ISDIR(root_stat.st_mode):
            raise VaultBoundaryError("vault root is not a directory")
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)

    def _assert_root_identity(self) -> None:
        if not _lexists(self.root) or _is_reparse_or_symlink(self.root):
            raise VaultBoundaryError("vault root identity is no longer safe")
        current = self.root.stat()
        if (current.st_dev, current.st_ino) != self._root_identity:
            raise VaultBoundaryError("vault root changed during operation")

    @staticmethod
    def _relative_path(relative: str | Path) -> Path:
        value = Path(relative)
        pure = PurePath(value)
        if value.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise VaultBoundaryError("vault path must be a safe relative path")
        return value

    def _ensure_parent(self, relative: Path) -> Path:
        self._assert_root_identity()
        parent = self.root
        for part in relative.parent.parts:
            candidate = parent / part
            if _lexists(candidate):
                if _is_reparse_or_symlink(candidate):
                    raise VaultBoundaryError("vault path contains a symlink or junction")
                info = candidate.stat()
                if not stat.S_ISDIR(info.st_mode):
                    raise VaultBoundaryError("vault parent component is not a directory")
            else:
                candidate.mkdir()
                if _is_reparse_or_symlink(candidate):
                    raise VaultBoundaryError("vault parent became a symlink or junction")
            resolved = candidate.resolve(strict=True)
            if resolved != self.root and self.root not in resolved.parents:
                raise VaultBoundaryError("vault path escaped configured root")
            parent = candidate
        self._assert_root_identity()
        return parent

    def path(self, relative: str | Path) -> Path:
        safe = self._relative_path(relative)
        parent = self._ensure_parent(safe)
        target = parent / safe.name
        if _lexists(target):
            if _is_reparse_or_symlink(target):
                raise VaultBoundaryError("vault target is a symlink or junction")
            if not stat.S_ISREG(target.stat().st_mode):
                raise VaultBoundaryError("vault target is not a regular file")
        return target

    def read_text(self, relative: str | Path) -> str:
        target = self.path(relative)
        if not _lexists(target):
            raise FileNotFoundError(str(target))
        before = target.stat()
        value = target.read_text(encoding="utf-8")
        self._assert_root_identity()
        after = target.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise VaultConflictError("vault target changed while being read")
        return value

    def write_text(self, relative: str | Path, value: str, *, expected_existing_hash: str | None = None) -> Path:
        safe = self._relative_path(relative)
        target = self.path(safe)
        existing: str | None = None
        if _lexists(target):
            existing = self.read_text(safe)
            if existing == value:
                return target
            if expected_existing_hash is None or content_digest(existing) != expected_existing_hash:
                raise VaultConflictError("vault note changed; refusing overwrite")
        elif expected_existing_hash is not None:
            raise VaultConflictError("vault note disappeared before compare-and-swap")

        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(str(temporary), flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                descriptor = None
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())

            # Revalidate every boundary immediately before the atomic swap.
            self._ensure_parent(safe)
            target = self.path(safe)
            if existing is None:
                if _lexists(target):
                    raise VaultConflictError("vault note appeared during write")
            else:
                current = self.read_text(safe)
                if content_digest(current) != expected_existing_hash:
                    raise VaultConflictError("vault note changed during write")
            if _is_reparse_or_symlink(temporary):
                raise VaultBoundaryError("vault temporary file became a link")
            os.replace(temporary, target)
            self._assert_root_identity()
            if self.read_text(safe) != value:
                raise VaultIntegrityError("vault write verification failed; status=PARTIAL")
            return target
        except VaultBoundaryError:
            raise
        except OSError as exc:
            raise VaultIntegrityError("vault write failed; status=FAILED; retryable=true") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if _lexists(temporary) and not _is_reparse_or_symlink(temporary):
                try:
                    temporary.unlink()
                except OSError:
                    pass
