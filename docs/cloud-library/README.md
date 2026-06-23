# Personal Cloud Library

[English](README.md) | [简体中文](README.zh-CN.md)

This directory describes a future opt-in personal cloud library for Paper
Galaxy. It is design-only. No cloud runtime, hosted backend, account system,
sync worker, storage SDK, payment code, or telemetry is implemented by these
docs.

Cloud sync is not implemented in this repository.

## Principles

- Local-first remains the default.
- A user can use Paper Galaxy without an account.
- No upload happens unless the user explicitly enables a cloud library feature.
- Local app code and cloud sync code must be clearly separated.
- Source documents are never shared publicly by default.
- Export and deletion controls are required before any public cloud release.
- Self-hosting should remain a first-class design consideration.

## Candidate Approaches

1. Backup-only cloud vault: upload encrypted project backup bundles. This is
   the safest first implementation because it avoids live merge logic.
2. Metadata sync: sync project manifests, labels, map runs, and selected
   metadata while source documents remain local unless explicitly backed up.
3. Full managed library: hosted indexing, search, graph generation, and managed
   storage. This has the highest privacy and operational risk and should not be
   the first cloud release.

The recommended path is C1 backup vault first, then C2 metadata sync after
security review and user testing.

## Next Review Target

C1 encrypted backup vault is the next design review target. It remains
design-only until a separate explicit implementation phase is requested. Any
future implementation must preserve local-first use without an account and must
include clear export/delete controls before public release.
