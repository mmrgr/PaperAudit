# Changelog

## 0.2.0 — 2026-10-06

- Added native PDF and GROBID TEI ingestion with page/bounding-box anchors.
- Added citation integrity auditing, local text/TEI evidence retrieval, and provenance reporting.
- Added numeric fact checks, privacy preflight, checklist registry, Finding Graph, and benchmark runner.
- Added persistent panel jobs, safe revision gates, explicit model review runtime, and Windows DPAPI key protection.
- Added deterministic model-runner checkpoints with plan/profile/output hashes and `--fresh` reset.
- Added a 12-case adversarial benchmark corpus, gate-rejected accounting, maximum-cardinality scoring, and self-contained corpus layout.
- Tightened cross-reference evidence quotes and preserved structured DOCX XML through save/reopen regression checks.
- Propagated evidence confidence into citation support triage so explicitly weak passages cannot become supported through lexical overlap alone.
- Added a two-position, two-model panel adjudicator with auditable `confirmed / contested / refuted / unverifiable` output and panel state display.
- Added opt-in provider-backed `run-panel` orchestration with per-model/position artifacts, response hashes, prompt-boundary isolation, resumable checkpoints, and control-panel launch support.
- Added read-only `plan-revision` / `/api/revision/plan` EditProposal previews with expected-old-text locks, per-block operations, conflict graphs, persisted `revision.plan.json`, and readable `revision.diff` artifacts.
- Added config-driven venue profiles with `list-venues`, `prepare --venue`, explicit required-section gates, and persisted `venue.profile.json` snapshots; the bundled profile is clearly marked as a template.
