"""Local Zotero attachment path resolution."""

from __future__ import annotations

from pathlib import Path

from paper_galaxy.zotero.models import AttachmentResolution, ZoteroAttachment

RESOLVED_STATUSES = {"resolved", "linked_outside_data_dir"}


def resolve_attachment_path(
    attachment: ZoteroAttachment,
    *,
    data_dir: Path | None = None,
) -> AttachmentResolution:
    """Resolve a Zotero local attachment path without copying or moving it."""

    if not attachment.path and not attachment.filename:
        return AttachmentResolution(
            status="no_local_file",
            message="Attachment has no local file path.",
        )
    raw_path = attachment.path or ""
    if raw_path.startswith("storage:"):
        filename = raw_path.split(":", 1)[1] or attachment.filename
        if not data_dir:
            return AttachmentResolution(
                status="missing",
                message="Pass --data-dir to resolve Zotero storage: attachments.",
            )
        if not filename:
            return AttachmentResolution(
                status="unsupported",
                message="Zotero storage attachment has no filename.",
            )
        candidate = data_dir.expanduser() / "storage" / attachment.key / filename
        return _existing_or_missing(candidate)

    if raw_path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            if not candidate.exists():
                return AttachmentResolution(status="missing", resolved_path=candidate)
            if data_dir and not _is_relative_to(candidate, data_dir.expanduser()):
                return AttachmentResolution(
                    status="linked_outside_data_dir",
                    resolved_path=candidate.resolve(),
                    message="Linked attachment is outside the Zotero data directory.",
                )
            return AttachmentResolution(
                status="resolved",
                resolved_path=candidate.resolve(),
            )
        if data_dir:
            return _existing_or_missing(data_dir.expanduser() / candidate)
        return AttachmentResolution(
            status="missing",
            message="Pass --data-dir to resolve relative Zotero attachment paths.",
        )

    if data_dir and attachment.filename:
        candidate = (
            data_dir.expanduser() / "storage" / attachment.key / attachment.filename
        )
        return _existing_or_missing(candidate)

    return AttachmentResolution(status="unsupported")


def _existing_or_missing(path: Path) -> AttachmentResolution:
    if path.exists():
        return AttachmentResolution(status="resolved", resolved_path=path.resolve())
    return AttachmentResolution(status="missing", resolved_path=path)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
