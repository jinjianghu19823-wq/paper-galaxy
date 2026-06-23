# Architecture

## Status

Design-only. No cloud architecture is implemented in the current codebase.

## Separation Boundary

The local Paper Galaxy runtime continues to own:

- extraction;
- SQLite indexing;
- local search;
- graph generation;
- cluster labels;
- local pair explanations;
- validation;
- backup export/import.

Future cloud code should live behind an explicit opt-in boundary and must not be
imported by normal local commands.

## Approach Comparison

### 1. Backup-Only Cloud Vault

The local app creates a backup bundle, encrypts it client-side, and uploads the
encrypted blob plus a minimal manifest.

Pros:

- simplest privacy story;
- easy disaster recovery;
- no live conflict resolution;
- source documents can remain outside the backup by default.

Cons:

- no live multi-device merge;
- recovery is bundle-level;
- large backups may cost more storage.

### 2. Metadata Sync

The local app syncs metadata such as project manifests, labels, saved map run
manifests, and sync events. Source documents remain local unless explicitly
backed up.

Pros:

- better multi-device continuity;
- smaller payloads;
- keeps most sensitive content local.

Cons:

- conflict resolution is required;
- metadata can still reveal research interests;
- vector and label leakage must be considered.

### 3. Full Managed Library

The server stores documents and runs indexing/search/graph operations.

Pros:

- easiest cross-device UX;
- can support hosted compute.

Cons:

- highest privacy risk;
- highest operational cost;
- requires abuse prevention, legal process handling, and stronger account
  security;
- not recommended as a first implementation.

## Recommended Staged Architecture

- C0: design only.
- C1: encrypted backup vault using object storage for encrypted blobs and a
  relational metadata store for manifests, devices, and audit events.
- C2: metadata sync with per-device cursors and explicit conflict records.
- C3: optional managed compute for users who knowingly upload selected projects.
- C4: team/collaboration only if a future task explicitly asks for it.

## Key Design Questions

- E2EE vs server-side encryption: E2EE protects against cloud operator access
  but complicates password reset and search.
- SQLite bundle sync: bundle-level backups are robust but coarse; row-level sync
  is more ergonomic but much harder to make correct.
- Device identity: each device should have a stable public identity and a
  revocation path.
- Vendor lock-in: backups and manifests should use documented formats.
