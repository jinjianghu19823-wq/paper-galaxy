# API Sketch

## Status

Design-only. These endpoints are not implemented. Any future API must remain
behind an explicit opt-in cloud library boundary while local-first use continues
without an account.

All endpoints would require authenticated HTTPS in a future cloud service.
Payloads are sketches, not a committed contract.

## Auth

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/logout`
- `POST /auth/recovery/start`
- `POST /auth/recovery/complete`

## Devices

- `GET /devices`
- `POST /devices`
- `DELETE /devices/{device_id}`

Device records should include a public device key, display name, created time,
last seen time, and revoked time.

## Projects

- `GET /projects`
- `POST /projects`
- `GET /projects/{project_id}`
- `DELETE /projects/{project_id}`

## Backups

- `POST /projects/{project_id}/backups`
- `GET /projects/{project_id}/backups`
- `GET /projects/{project_id}/backups/{backup_id}`
- `DELETE /projects/{project_id}/backups/{backup_id}`

The upload body should be an encrypted blob or a pre-signed object-storage
upload flow. The manifest should not contain full extracted text.

## Manifests

- `GET /projects/{project_id}/manifest`
- `PUT /projects/{project_id}/manifest`

## Labels

- `GET /projects/{project_id}/labels`
- `PATCH /projects/{project_id}/labels`

## Map Runs

- `GET /projects/{project_id}/map-runs`
- `POST /projects/{project_id}/map-runs`
- `DELETE /projects/{project_id}/map-runs/{run_id}`

## Sync Cursors

- `GET /projects/{project_id}/sync?cursor=...`
- `POST /projects/{project_id}/sync/events`

## Export And Delete

- `POST /account/export`
- `GET /account/export/{export_id}`
- `DELETE /account`
- `DELETE /projects/{project_id}`
