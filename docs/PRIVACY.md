# Privacy

Paper Galaxy is local-first by default.

- User documents stay local by default.
- There is no telemetry.
- There is no automatic upload.
- There is no account requirement.
- The app should be usable offline.
- Phase 2 stores extracted text and chunks locally in
  `.paper-galaxy/paper_galaxy.sqlite3` by default.
- Missing and unindexed records may preserve previously extracted local text and
  chunks so the local index can recover document history without rescanning
  unavailable content.
- `.paper-galaxy/` is local project state and is gitignored.
- Deleting `.paper-galaxy/` removes the local Paper Galaxy database and project
  metadata for that project.

Future cloud features, if any, must be opt-in. They should clearly explain what
data leaves the machine, where it goes, and how the user can disable or delete
that data.
