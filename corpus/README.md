# Corpus layout

This directory is reserved for versioned, de-identified manuscript fixtures.

- `seeded/`: controlled documents with one known defect injected at a time.
- `gold/`: manually adjudicated documents and labels.
- `adversarial/`: prompt-injection, malformed-format, and false-positive cases.
- `format/`: DOCX/PDF/TEI fidelity fixtures such as hyperlinks, equations,
  footnotes, comments, tracked changes, hidden text, tables, and fields.

Private manuscripts do not belong in this repository. Add a manifest and gold
labels beside every fixture, then reference local files from a benchmark
corpus. The checked-in `benchmarks/*.json` files use embedded `DocumentIR`
cases so CI remains reproducible without sensitive documents.
