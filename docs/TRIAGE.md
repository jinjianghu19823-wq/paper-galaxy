# Issue Triage Guide

Use lightweight labels so early public feedback can be sorted without asking
users to share private data.

## Suggested Labels

- `bug`: incorrect behavior or crash.
- `extraction`: parser, OCR, skipped file, or quality report issue.
- `graph-ui`: map layout, labels, interactions, inspector, or search UI issue.
- `privacy`: unclear data boundary, upload concern, or sensitive output concern.
- `documentation`: README, docs, examples, launch notes, FAQ, or troubleshooting.
- `packaging`: install, build, release, Python version, or dependency issue.
- `embeddings`: local model loading, vector build, semantic search, or hybrid similarity.
- `explainability`: cluster labels, pair explanations, terms, or evidence snippets.
- `good first issue`: small, scoped, beginner-friendly fix.
- `help wanted`: useful contribution that maintainers may not address immediately.
- `cloud-design`: design discussion for future opt-in cloud library work only.

## Privacy-Safe Triage Questions

- Can the issue be reproduced on `examples/tiny_corpus`?
- Can the reporter share a synthetic fixture instead of private papers?
- Which command failed, and what was the redacted error?
- Which OS, Python version, and install command were used?
- Did `paper-galaxy validate-project --project-dir .` report issues?

## Handling Sensitive Reports

Ask reporters not to upload `.paper-galaxy/`, SQLite files, backup bundles,
private paper text, extracted chunks, API keys, or secrets. If the report is
security-sensitive, move it to the private security reporting path.
