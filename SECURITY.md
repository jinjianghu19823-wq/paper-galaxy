# Security Policy

Paper Galaxy currently has no formal supported release window.

## Reporting

Please report security issues privately to the repository owner before opening a
public issue. Include the affected command, input type, and whether local files,
SQLite data, browser localStorage, or backup bundles are involved.

## Local Data Boundary

Paper Galaxy is designed to run locally. It should not upload documents, send
telemetry, load remote frontend assets, or load remote plugins by default.
Backup bundles can contain the local SQLite database, so treat them as sensitive
project data.
