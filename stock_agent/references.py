"""Source-backed reusable reference lifecycle for the Obsidian projection boundary.

References are deliberately separate from company/date-specific Evidence. This
module does not grant an LLM authority and does not mutate run/gate state; it
only resolves or builds deterministic prompt-prefix material that can be
recorded in the SQLite registry and projected to Obsidian.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from .models import canonical_hash, utc_now
from .vault import SecureVault, VaultBoundaryError, VaultConflictError


class ReferenceContractError(ValueError):
    """Raised when a reference cannot satisfy source/provenance contracts."""


@dataclass(frozen=True)
class ReferenceRequirement:
    reference_id: str
    version: str
    kind: str = "CANONICAL_REFERENCE"


@dataclass(frozen=True)
class ReferenceRecord:
    reference_id: str
    version: str
    status: str
    content_hash: str
    obsidian_path: str
    source_receipts: tuple[str, ...]
    validated_at: str
    supersedes: str | None
    kind: str
    content: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "version": self.version,
            "status": self.status,
            "content_hash": self.content_hash,
            "obsidian_path": self.obsidian_path,
            "source_receipts": list(self.source_receipts),
            "validated_at": self.validated_at,
            "supersedes": self.supersedes,
            "kind": self.kind,
            "content": self.content,
        }


class ReferenceRegistryProtocol(Protocol):
    def upsert_reference(self, record: ReferenceRecord) -> None: ...

    def get_active_reference(self, reference_id: str, version: str | None = None) -> ReferenceRecord | None: ...


def _canonical_content(value: Any) -> str:
    if isinstance(value, str):
        content = value.strip()
    else:
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if not content:
        raise ReferenceContractError("reference content must not be empty")
    return content


def _validate_component(value: str, label: str) -> str:
    text = str(value).strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text or ".." in text:
        raise ReferenceContractError(f"unsafe reference {label}")
    return text


def _validate_source_receipt(registry: ReferenceRegistryProtocol, receipt: str) -> None:
    """Bind generated-reference provenance to an existing authoritative row.

    A receipt string is not evidence by itself. Generated references may only
    cite persisted ACTIVE Evidence, SUCCEEDED StageResults, or persisted raw
    artifacts. This intentionally rejects legacy/free-form values such as
    ``receipt:anything`` or ``r:a``.
    """
    connection = getattr(registry, "connection", None)
    if connection is None:
        raise ReferenceContractError("reference registry cannot validate source receipts")
    kind, separator, identifier = str(receipt).partition(":")
    identifier = identifier.strip()
    if not separator or not identifier:
        raise ReferenceContractError(f"invalid reference source receipt: {receipt}")
    if kind == "evidence":
        row = connection.execute(
            "SELECT status FROM evidence WHERE evidence_id=?", (identifier,)
        ).fetchone()
        if row is None or str(row["status"]) != "ACTIVE":
            raise ReferenceContractError(f"reference Evidence receipt is not ACTIVE: {receipt}")
        return
    if kind == "stage-result":
        row = connection.execute(
            "SELECT status FROM stage_results WHERE result_id=?", (identifier,)
        ).fetchone()
        if row is None or str(row["status"]) != "SUCCEEDED":
            raise ReferenceContractError(f"reference StageResult receipt is not SUCCEEDED: {receipt}")
        return
    if kind == "raw-artifact":
        row = connection.execute(
            "SELECT artifact_id FROM raw_artifacts WHERE artifact_id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise ReferenceContractError(f"reference RawArtifact receipt does not exist: {receipt}")
        return
    raise ReferenceContractError(f"unsupported reference source receipt type: {kind}")


class ReferenceResolver:
    """Reuse only ACTIVE references matching the exact requested identity."""

    def __init__(self, registry: ReferenceRegistryProtocol) -> None:
        self.registry = registry

    def resolve(self, requirement: ReferenceRequirement) -> ReferenceRecord | None:
        if requirement.kind not in {"CANONICAL_REFERENCE", "GENERATED_REFERENCE"}:
            raise ReferenceContractError("dynamic evidence cannot be resolved as a reusable reference")
        record = self.registry.get_active_reference(requirement.reference_id, requirement.version)
        if record is None:
            return None
        if record.kind != requirement.kind or record.status != "ACTIVE":
            return None
        if record.content_hash != canonical_hash(record.content):
            raise ReferenceContractError("reference content hash mismatch")
        if not record.source_receipts:
            raise ReferenceContractError("active reference requires source receipts")
        if record.kind == "GENERATED_REFERENCE":
            for receipt in record.source_receipts:
                _validate_source_receipt(self.registry, receipt)
        return record


class ReferenceBuilder:
    """Create a reusable reference only from validated source receipts."""

    def __init__(self, registry: ReferenceRegistryProtocol, obsidian_root: str | Path) -> None:
        self.registry = registry
        try:
            self._vault = SecureVault(obsidian_root)
        except VaultBoundaryError as exc:
            raise ReferenceContractError(str(exc)) from exc
        self.obsidian_root = self._vault.root

    def build(
        self,
        requirement: ReferenceRequirement,
        source: Any,
        source_receipts: Iterable[str],
        *,
        supersedes: str | None = None,
    ) -> ReferenceRecord:
        if requirement.kind != "GENERATED_REFERENCE":
            raise ReferenceContractError("builder may only create GENERATED_REFERENCE records")
        _validate_component(requirement.reference_id, "id")
        _validate_component(requirement.version, "version")
        receipts = tuple(sorted({str(receipt).strip() for receipt in source_receipts if str(receipt).strip()}))
        if not receipts:
            raise ReferenceContractError("generated reference requires at least one source receipt")
        for receipt in receipts:
            _validate_source_receipt(self.registry, receipt)
        if isinstance(source, dict) and str(source.get("kind", "")).upper() == "DYNAMIC_EVIDENCE":
            raise ReferenceContractError("dynamic company/date-specific evidence cannot become reusable reference")
        content = _canonical_content(source)
        content_hash = canonical_hash(content)
        relative_path = Path("references") / f"{requirement.reference_id}__v{requirement.version}.md"
        body = "---\n" + json.dumps(
            {
                "reference_id": requirement.reference_id,
                "version": requirement.version,
                "kind": requirement.kind,
                "content_hash": content_hash,
                "source_receipts": list(receipts),
                "projection_only": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        ) + "\n---\n\n" + content + "\n"
        # A reference ID+version is immutable.  An identical retry is safe;
        # any differing existing note is either a user edit or a conflicting
        # export and must not be overwritten.
        try:
            target = self._vault.path(relative_path)
            if target.exists() and self._vault.read_text(relative_path) != body:
                raise ReferenceContractError("canonical reference note conflict; refusing overwrite")
            self._vault.write_text(relative_path, body)
            if self._vault.read_text(relative_path) != body:
                raise ReferenceContractError("reference projection status=PARTIAL; retry required")
        except (VaultBoundaryError, VaultConflictError) as exc:
            raise ReferenceContractError(str(exc)) from exc
        record = ReferenceRecord(
            reference_id=requirement.reference_id,
            version=requirement.version,
            status="ACTIVE",
            content_hash=content_hash,
            obsidian_path=str(relative_path).replace("\\", "/"),
            source_receipts=receipts,
            validated_at=utc_now(),
            supersedes=supersedes,
            kind=requirement.kind,
            content=content,
        )
        self.registry.upsert_reference(record)
        return record


@dataclass(frozen=True)
class ReferencePack:
    version: str
    entries: tuple[ReferenceRecord, ...]
    prefix: str
    content_hash: str
    cache_key: str


class ReferencePackCompiler:
    """Compile deterministic reference prefixes for prompt/cache reuse."""

    def compile(self, requirements: Iterable[ReferenceRequirement], resolver: ReferenceResolver, *, pack_version: str = "1") -> ReferencePack:
        resolved: list[ReferenceRecord] = []
        missing: list[str] = []
        for requirement in sorted(requirements, key=lambda item: (item.reference_id, item.version, item.kind)):
            record = resolver.resolve(requirement)
            if record is None:
                missing.append(f"{requirement.reference_id}@{requirement.version}")
            else:
                resolved.append(record)
        if missing:
            raise ReferenceContractError("missing ACTIVE references: " + ", ".join(missing))
        lines: list[str] = []
        for record in resolved:
            lines.extend((f"## {record.reference_id} v{record.version}", record.content, ""))
        prefix = "\n".join(lines).strip()
        manifest = {
            "pack_version": pack_version,
            "entries": [
                {"reference_id": record.reference_id, "version": record.version, "content_hash": record.content_hash}
                for record in resolved
            ],
        }
        return ReferencePack(
            version=pack_version,
            entries=tuple(resolved),
            prefix=prefix,
            content_hash=canonical_hash({"manifest": manifest, "prefix": prefix}),
            cache_key=canonical_hash(manifest),
        )
