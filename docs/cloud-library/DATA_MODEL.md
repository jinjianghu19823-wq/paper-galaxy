# Data Model

## Status

Design-only. No tables or migrations are implemented. Any future cloud data
model must support opt-in use only and preserve local-first operation without an
account.

## Entities

### user

- `id`
- `email_hash`
- `created_at`
- `status`
- `encryption_mode`

### device

- `id`
- `user_id`
- `display_name`
- `public_key`
- `created_at`
- `last_seen_at`
- `revoked_at`

### project

- `id`
- `user_id`
- `local_project_fingerprint`
- `display_name`
- `created_at`
- `updated_at`
- `deleted_at`

### backup_bundle

- `id`
- `project_id`
- `created_by_device_id`
- `object_key`
- `size_bytes`
- `checksum_sha256`
- `encryption_metadata`
- `created_at`
- `deleted_at`

### manifest

- `project_id`
- `version`
- `encrypted_payload`
- `server_readable_summary`
- `updated_at`

### sync_event

- `id`
- `project_id`
- `device_id`
- `sequence`
- `event_type`
- `encrypted_payload`
- `created_at`

### encrypted_blob

- `id`
- `owner_type`
- `owner_id`
- `object_key`
- `checksum_sha256`
- `size_bytes`
- `encryption_metadata`

### audit_event

- `id`
- `user_id`
- `device_id`
- `event_type`
- `ip_prefix`
- `created_at`
- `metadata`

## Notes

Avoid storing full extracted text, vectors, or source documents in
server-readable form unless a future user explicitly opts into a managed
library mode. Metadata should be minimized because paths, labels, and vector
summaries can reveal research interests.
