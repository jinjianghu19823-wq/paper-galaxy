# Threat Model

## Status

Design-only. This threat model describes a possible future opt-in cloud library.

## Threats

### Malicious Server

The server may attempt to read backups, manifests, labels, vectors, or source
document content. Mitigation: client-side encryption for backup blobs, minimized
server-readable metadata, and documented field boundaries.

### Stolen Token

An attacker with an access token may list or delete data. Mitigation: short-lived
tokens, refresh-token rotation, device revocation, and audit events.

### Compromised Device

A compromised laptop can access local documents and cloud keys. Mitigation:
device revocation, local OS security guidance, and explicit warnings that cloud
encryption cannot protect data already decrypted on the device.

### Leaked Backup

An object-storage leak may expose encrypted blobs. Mitigation: strong
client-side encryption, checksums, key rotation design, and deletion lifecycle.

### Malicious Plugin

Future plugins could exfiltrate data. Mitigation: keep remote plugin loading out
of the default product, document plugin boundaries, and require a separate
security design before third-party plugin execution.

### Inference From Embeddings Or Vectors

Vectors can leak semantic information. Mitigation: do not upload vectors by
default, treat vectors as sensitive, and document any managed-compute opt-in.

### Account Takeover

An attacker may change account credentials or create new devices. Mitigation:
MFA support, device notifications, audit logs, and recovery controls.

### Cloud Operator Access

Operators may access server-side data. Mitigation: prefer E2EE for backup
contents and keep server-readable metadata minimal.

### Legal Request

Hosted data may be subject to legal process. Mitigation: minimize retained
server-readable data and document jurisdictional risk.

### Deletion Guarantees

Deleting data from object storage, backups, and logs can be incomplete.
Mitigation: define retention windows, expose deletion status, and avoid keeping
unnecessary replicas.
