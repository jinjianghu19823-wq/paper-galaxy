# Personal Cloud Library Design

The personal cloud library is a future, opt-in design only. It is not
implemented in the current Paper Galaxy runtime.

Paper Galaxy remains local-first by default:

- no account is required today;
- no documents are uploaded by default;
- local indexing, search, map generation, labels, backups, and validation remain
  useful without cloud services;
- any future cloud feature must have explicit enablement, export controls, and
  deletion controls.

Detailed design package:

- [Overview](cloud-library/README.md)
- [Product spec](cloud-library/PRODUCT_SPEC.md)
- [Architecture](cloud-library/ARCHITECTURE.md)
- [Privacy and security](cloud-library/PRIVACY_AND_SECURITY.md)
- [API sketch](cloud-library/API_SKETCH.md)
- [Data model](cloud-library/DATA_MODEL.md)
- [Roadmap](cloud-library/ROADMAP.md)
- [Threat model](cloud-library/THREAT_MODEL.md)

Recommended staged path:

1. C0: design only.
2. C1: encrypted backup vault.
3. C2: metadata sync.
4. C3: optional managed compute.
5. C4: team or collaboration features only if explicitly requested later.
