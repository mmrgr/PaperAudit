# Benchmark corpus

`smoke.json` is a small checked-in corpus for regression testing the
deterministic layer. `adversarial.json` adds twelve controlled positive/negative
cases for citation numbering, caption boundaries, numeric conflicts,
terminology definitions, and heading gaps. It is a CI gate: precision, recall,
and FPR must remain 1.0 on these cases. Both corpora use embedded
`DocumentIR` objects, so they run
without private manuscripts or external services:

```powershell
$env:PYTHONPATH = "src"
python -m paperrevamper.cli benchmark benchmarks/smoke.json --format markdown --fail-under 1
python -m paperrevamper.cli benchmark benchmarks/adversarial.json --format markdown --fail-under 1
```

For a real evaluation corpus, use local `path` entries and annotate each case
with `expected` objects.  An expected object requires `issue_type` and can add
`severity` and `block_ids`; anchored labels only match findings that share at
least one block anchor.  A case can add `expected_absent` issue types to make
the false positive rate (FPR) measurable; otherwise FPR is reported as `n/a`.
Keep the corpus de-identified and version its labels with the manuscript
snapshot.  Metrics are a regression signal, not a claim about generalization
from a small or single-venue sample.
