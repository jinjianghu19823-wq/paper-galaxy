# Cloud Library Roadmap

## C0: Design Only

Current milestone. Write the product, architecture, privacy, API, data model,
roadmap, and threat model. Do not implement runtime cloud functionality. The
future cloud library remains opt-in and local-first users must not need an
account.

## C1: Encrypted Backup Vault

- Account and device model.
- Client-side encrypted backup upload.
- Backup list, restore, export, and delete.
- Clear privacy copy before enablement.
- Security review before release.

## C2: Metadata Sync

- Per-device sync cursors.
- Manual label sync.
- Saved map run manifest sync.
- Conflict detection and user-visible merge choices.
- Minimized server-readable metadata.

## C3: Optional Managed Compute

- Explicit per-project opt-in.
- Clear document upload warning.
- Hosted indexing/search/graph only after a separate architecture decision.
- Cost and abuse controls.

## C4: Team Or Collaboration

Only if explicitly requested in a later phase. Public sharing, teams, comments,
and collaboration dramatically change the privacy and abuse model.
