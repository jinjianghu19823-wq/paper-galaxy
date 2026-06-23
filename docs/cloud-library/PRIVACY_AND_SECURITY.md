# Privacy And Security

## Status

Not implemented. These requirements apply to a future opt-in cloud library.

## Privacy Commitments

- Local-first remains the default.
- No account is required for local Paper Galaxy use.
- No upload happens unless the user explicitly enables cloud library features.
- Source documents are not shared publicly by default.
- Users can export and delete cloud data.
- Documentation must explain which data leaves the device before enablement.

## Encryption

Two models are plausible:

1. End-to-end encryption: the server stores encrypted blobs and cannot decrypt
   backup contents. This improves privacy but makes password reset and server
   search difficult.
2. Server-side encrypted storage: easier recovery and operations, but the
   operator can technically access decrypted data in some workflows.

C1 should prefer client-side encryption for backup blobs and avoid hosted search.
If E2EE is used, password reset cannot recover data unless the user keeps a
recovery key or another trusted device can re-encrypt project keys.

## Sensitive Data Types

- Extracted text and chunks.
- Source document metadata.
- Dense vectors and vector indexes.
- Manual labels and map runs.
- Backup manifests.
- Sync events and device names.

Even metadata can reveal research interests. Minimize server-readable metadata
and document every field.

## Controls

- Explicit enablement per project.
- Clear backup contents preview.
- Device revocation.
- Export all cloud data.
- Delete backups and account data.
- Audit log visible to the user.
- Rate limits and abuse prevention.
