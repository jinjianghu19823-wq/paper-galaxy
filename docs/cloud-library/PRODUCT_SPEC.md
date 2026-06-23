# Product Spec

## Status

Not implemented. This is a future opt-in product design.

## Problem

Researchers may want encrypted off-device recovery or multi-device continuity
while keeping Paper Galaxy's local-first default. The cloud library should help
with backup and continuity without turning Paper Galaxy into an account-first
hosted app.

## Target Users

- Individual researchers with private paper libraries.
- Students who move between laptops and desktops.
- Users who want a recovery copy of local Paper Galaxy project state.
- Future self-hosters who want the same protocol under their own domain.

## Non-Goals

- No public sharing by default.
- No social features.
- No team workspace in the first cloud version.
- No cloud OCR or mandatory remote embeddings.
- No hosted indexing in the first version.
- No telemetry requirement.

## Core Capabilities

Stage C1:

- Create a personal account.
- Register trusted devices.
- Upload encrypted project backup bundles.
- Download and restore backup bundles.
- List backup manifests without exposing document contents.
- Delete backups.
- Export all account data.

Stage C2:

- Sync encrypted or minimized metadata changes.
- Sync manual cluster labels and saved map run manifests.
- Track sync cursors per device.
- Detect conflicts and ask the user before overwriting local state.

Stage C3:

- Optional managed compute only after a separate security and privacy review.
- Clear per-project opt-in.
- Explicit warnings for document upload and remote indexing.

## Success Criteria

- Local-only users see no cloud prompts during normal use.
- Enabling cloud requires an explicit user action.
- A user can restore a project from an encrypted backup.
- A user can export and delete cloud data.
- Public docs explain password reset and encryption tradeoffs plainly.
