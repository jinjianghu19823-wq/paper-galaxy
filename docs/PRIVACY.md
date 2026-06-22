# Privacy

Paper Galaxy is local-first by default.

- User documents stay local by default.
- There is no telemetry.
- There is no automatic upload.
- There is no account requirement.
- The app should be usable offline.
- Phase 2 stores extracted text and chunks locally in
  `.paper-galaxy/paper_galaxy.sqlite3` by default.
- Phase 4 stores compact extraction reports locally in SQLite and can write an
  optional local JSON sidecar. These reports omit full extracted text.
- Missing and unindexed records may preserve previously extracted local text and
  chunks so the local index can recover document history without rescanning
  unavailable content.
- Phase 3 starts a local server bound to `127.0.0.1` by default.
- The browser app communicates with the local backend only.
- Phase 3 static assets are served locally and do not reference CDNs, remote
  fonts, or external images.
- The browser app does not upload documents, collect telemetry, or call remote
  services.
- Phase 3.1 stores graph UI preferences and manually pinned node positions only
  in browser `localStorage`, keyed by local database identity and document IDs.
  These layout values are not uploaded and are not written to SQLite.
- Optional OCR runs locally only. It may require user-installed local OCR
  binaries such as Tesseract, but Paper Galaxy does not upload images,
  extracted text, OCR output, or extraction reports to a remote service.
- Choosing a non-loopback host may expose the app to other devices on the local
  network.
- `.paper-galaxy/` is local project state and is gitignored.
- Deleting `.paper-galaxy/` removes the local Paper Galaxy database and project
  metadata for that project.

Future cloud features, if any, must be opt-in. They should clearly explain what
data leaves the machine, where it goes, and how the user can disable or delete
that data.
