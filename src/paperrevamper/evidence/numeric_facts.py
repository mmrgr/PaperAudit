"""Conservative numeric fact extraction and consistency checks.

This module intentionally has no dependency on the DOCX layer.  It consumes
the small :class:`~paperrevamper.models.DocumentIR` object and keeps each parsed
fact anchored to the block that supplied it.  The rules are deliberately
strict: a number is useful only when a metric label or a statistical marker
nearby makes its meaning reasonably clear.

The first version covers the high-value cases that can be checked without a
statistical model: percentages and common measured quantities, ``+/-`` and
confidence intervals, ``p`` values, sample sizes, category totals in tables,
and an explicit ``significant``/``not significant`` claim next to a p value.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from paperrevamper.models import (
    Block,
    BlockKind,
    DocumentIR,
    Finding,
    IssueType,
    Severity,
    stable_id,
)


_NUM = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?|[-+]?\.\d+(?:[eE][-+]?\d+)?"
_NUMBER_RE = re.compile(_NUM)
_UNIT_RE = re.compile(
    r"(?:%|％|百分比|百分点(?:数)?|percentage\s*points?|pp\b|倍|times?|points?|point|"
    r"ms\b|毫秒|秒|s\b|kg\b|g\b|mg\b|cm\b|mm\b|mmhg\b|°c|℃|万|亿)",
    re.I,
)
_P_RE = re.compile(
    rf"(?<![A-Za-z])p\s*(?P<op><=|>=|<|>|=|：|:)\s*(?P<value>{_NUM})",
    re.I,
)
_P_BARE_RE = re.compile(rf"(?<![A-Za-z])p\s+(?P<value>{_NUM})", re.I)
_N_RE = re.compile(
    rf"(?<![A-Za-z])(?P<label>N|n)\s*(?:=|：|:|为|is|was)?\s*(?P<value>{_NUM})(?![A-Za-z])"
)
_N_CN_RE = re.compile(
    rf"(?:样本量|样本数|病例数|受试者(?:数|人数)?|人数)\s*(?:=|：|:|为|是)?\s*(?P<value>{_NUM})"
)
_CI_RE = re.compile(
    rf"(?P<level>\d{{1,3}})\s*%?\s*(?:CI|置信区间|confidence\s+interval)\s*"
    rf"[\[（(]?\s*(?P<low>{_NUM})\s*(?:[,，;；]|至|to|[-–—])\s*(?P<high>{_NUM})\s*[\]）)]?",
    re.I,
)
_PM_RE = re.compile(rf"(?P<left>{_NUM})\s*(?P<unit>[%％]|个百分点|points?|倍)?\s*(?:±|\+/-|\+\-+)\s*(?P<right>{_NUM})\s*(?P<right_unit>[%％]|个百分点|points?|倍)?", re.I)

# A bare number becomes a fact only if one of these labels is nearby.  This
# avoids treating years, page numbers, citation indices, and section numbers
# as measurements.
_METRIC_CUES = re.compile(
    r"(?:accuracy|precision|recall|f1(?:[- ]?score)?|sensitivity|specificity|"
    r"prevalence|incidence|rate|ratio|odds|hazard|risk|effect|mean|median|"
    r"average|score|value|level|concentration|weight|height|age|duration|"
    r"proportion|percentage|percent|change|difference|error|loss|yield|"
    r"accuracy|auc|rmse|mae|correlation|coefficient|效率|准确率|精确率|召回率|"
    r"灵敏度|特异度|患病率|发生率|比例|占比|比率|均值|平均值|中位数|得分|分数|"
    r"数值|值|水平|浓度|体重|身高|年龄|时长|持续时间|误差|损失|产量|差异|变化|"
    r"风险|优势比|比值比|相关系数|回归系数|效应量|显著性)",
    re.I,
)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_CITATION_ONLY_RE = re.compile(r"^\s*\[\s*\d+(?:\s*[,;，；]\s*\d+)*\s*\]\s*$")


@dataclass(frozen=True)
class NumericFact:
    """One conservative numeric statement anchored to a DocumentIR block."""

    metric: str
    value: float | None
    unit: str
    context: str
    source_block: str
    raw: str
    kind: str = "value"  # value | uncertainty | ci | p_value | sample_size
    operator: str = "="
    uncertainty: float | None = None
    interval: tuple[float, float] | None = None
    confidence_level: float | None = None

    @property
    def block_id(self) -> str:
        return self.source_block

    @property
    def source_block_id(self) -> str:
        return self.source_block


def extract_facts(doc: DocumentIR) -> list[NumericFact]:
    """Extract facts from paragraph and table blocks in document order."""

    facts: list[NumericFact] = []
    for block in doc.blocks:
        if block.is_bibliography:
            continue
        if block.kind in (BlockKind.PARAGRAPH, BlockKind.HEADING, BlockKind.CAPTION):
            facts.extend(_paragraph_facts(block))
        elif block.kind is BlockKind.TABLE or block.rows:
            facts.extend(_table_facts(block))
    return _dedupe_facts(facts)


# Friendly aliases for callers that prefer the longer name.
extract_numeric_facts = extract_facts
# Short alias retained for callers that treat evidence modules like parsers.
extract = extract_facts


def compare_facts(facts: Iterable[NumericFact] | DocumentIR) -> list[Finding]:
    """Return only high-confidence, directly evidenced numeric conflicts."""

    if isinstance(facts, DocumentIR):
        facts = extract_facts(facts)
    items = list(facts)
    out: list[Finding] = []
    out.extend(_compare_same_metric(items))
    out.extend(_compare_f1_relationship(items))
    out.extend(_compare_p_range(items))
    out.extend(_compare_p_significance(items))
    out.extend(_compare_table_totals(items))
    return _dedupe_findings(out)


def check(doc: DocumentIR) -> list[Finding]:
    """Compatibility entry point used by deterministic evidence runners."""

    return compare_facts(doc)


def _paragraph_facts(block: Block) -> list[NumericFact]:
    text = block.text or ""
    if not text.strip() or _CITATION_ONLY_RE.match(text):
        return []
    facts: list[NumericFact] = []
    covered: list[tuple[int, int]] = []

    # p values and sample sizes are explicit statistical markers and therefore
    # need no generic metric guess.
    for m in _P_RE.finditer(text):
        value = _to_float(m.group("value"))
        if value is None:
            continue
        raw = m.group(0)
        facts.append(_fact(block, "p-value", value, "p-value", raw, "p_value", m.group("op"), m.start(), m.end()))
        covered.append(m.span())
    for m in _P_BARE_RE.finditer(text):
        if any(a <= m.start() < b for a, b in covered):
            continue
        value = _to_float(m.group("value"))
        if value is None:
            continue
        facts.append(_fact(block, "p-value", value, "p-value", m.group(0), "p_value", "=", m.start(), m.end()))
        covered.append(m.span())
    for pattern in (_N_RE, _N_CN_RE):
        for m in pattern.finditer(text):
            value = _to_float(m.group("value"))
            if value is None or _looks_like_year(m.group("value")):
                continue
            facts.append(_fact(block, "sample size", value, "count", m.group(0), "sample_size", "=", m.start(), m.end()))
            covered.append(m.span())

    # Confidence intervals are kept as intervals; generic scanning must not
    # create duplicate low/high measurements from their endpoints.
    for m in _CI_RE.finditer(text):
        low, high = _to_float(m.group("low")), _to_float(m.group("high"))
        if low is None or high is None or high < low:
            continue
        metric = _metric_from_context(text, m.start()) or "estimate"
        raw = m.group(0)
        facts.append(
            _fact(block, metric, (low + high) / 2, "CI", raw, "ci", "=", m.start(), m.end(), interval=(low, high), confidence_level=_to_float(m.group("level")))
        )
        covered.append(m.span())

    # mean +/- uncertainty statements.
    for m in _PM_RE.finditer(text):
        start, end = m.span()
        if any(a <= start < b for a, b in covered):
            continue
        left, right = _to_float(m.group("left")), _to_float(m.group("right"))
        if left is None or right is None or (_looks_like_year(m.group("left")) and not m.group("unit")):
            continue
        metric = _metric_from_context(text, start)
        if not metric:
            continue
        unit = _normalize_unit(m.group("unit") or m.group("right_unit") or "")
        facts.append(_fact(block, metric, left, unit, m.group(0), "value", "=", start, end, uncertainty=right))
        covered.append(m.span())

    # Unit-bearing or labelled scalar measurements.  Numbers without a unit
    # are admitted only next to an unambiguous metric cue.
    for m in _NUMBER_RE.finditer(text):
        start, end = m.span()
        if any(a <= start < b for a, b in covered):
            continue
        if _inside_citation(text, start, end) or _looks_like_year(m.group(0)):
            continue
        value = _to_float(m.group(0))
        if value is None:
            continue
        tail = text[end : end + 18]
        unit_match = _UNIT_RE.match(tail.lstrip())
        unit = _normalize_unit(unit_match.group(0) if unit_match else "")
        metric = _metric_from_context(text, start)
        if not metric:
            continue
        if not unit and not _has_metric_cue(text, start):
            continue
        raw_end = end + (len(tail) - len(tail.lstrip()) + unit_match.end() if unit_match else 0)
        raw = text[start:raw_end]
        facts.append(_fact(block, metric, value, unit, raw, "value", "=", start, raw_end))
    return facts


def _table_facts(block: Block) -> list[NumericFact]:
    rows = block.rows or []
    if not rows:
        # Hand-built IRs sometimes provide only ``text`` for a table.  Parse it
        # conservatively as prose, retaining the table block anchor.
        return _paragraph_facts(Block(block.id, BlockKind.PARAGRAPH, block.text, block.section_path, block.style_name, block.heading_level, block.is_bibliography))
    facts: list[NumericFact] = []
    headers = [str(c).strip() for c in rows[0]]
    for row_index, row in enumerate(rows[1:], 1):
        label = str(row[0]).strip() if row else ""
        for col_index, cell in enumerate(row):
            cell_text = str(cell).strip()
            if not cell_text or _CITATION_ONLY_RE.match(cell_text):
                continue
            header = headers[col_index] if col_index < len(headers) else ""
            # Explicit p/n cells preserve their statistical kind.
            pm = _P_RE.search(cell_text)
            nm = _N_RE.search(cell_text) or _N_CN_RE.search(cell_text)
            if pm:
                val = _to_float(pm.group("value"))
                if val is not None:
                    facts.append(_fact(block, "p-value", val, "p-value", cell_text, "p_value", pm.group("op"), 0, len(cell_text), context=f"{label} {header}"))
                continue
            if nm:
                val = _to_float(nm.group("value"))
                if val is not None and not _looks_like_year(nm.group("value")):
                    facts.append(_fact(block, "sample size", val, "count", cell_text, "sample_size", "=", 0, len(cell_text), context=f"{label} {header}"))
                continue
            cm = _CI_RE.search(cell_text)
            if cm:
                low, high = _to_float(cm.group("low")), _to_float(cm.group("high"))
                if low is not None and high is not None and high >= low:
                    facts.append(_fact(block, _table_metric(label, header), (low + high) / 2, "CI", cell_text, "ci", "=", 0, len(cell_text), interval=(low, high), confidence_level=_to_float(cm.group("level")), context=f"{label} {header}"))
                continue
            scalar = _NUMBER_RE.search(cell_text)
            if not scalar or _looks_like_year(scalar.group(0)):
                continue
            value = _to_float(scalar.group(0))
            if value is None:
                continue
            unit_match = _UNIT_RE.search(cell_text[scalar.end() :])
            header_is_n = bool(re.fullmatch(r"\s*(?:n|N|sample\s*size|样本(?:量|数)|人数)\s*", header, re.I))
            unit = _normalize_unit(unit_match.group(0) if unit_match else (header if _UNIT_RE.search(header) else ""))
            metric = _table_metric(label, header)
            if not metric:
                continue
            kind = "sample_size" if header_is_n and metric != "table total" else "value"
            if kind == "sample_size":
                metric = "sample size"
                unit = "count"
            facts.append(_fact(block, metric, value, unit, cell_text, kind, "=", 0, len(cell_text), context=f"{label} {header}"))
    return facts


def _fact(block: Block, metric: str, value: float, unit: str, raw: str, kind: str, operator: str, start: int, end: int, *, uncertainty: float | None = None, interval: tuple[float, float] | None = None, confidence_level: float | None = None, context: str | None = None) -> NumericFact:
    if context is None:
        before = block.text[max(0, start - 70) : start]
        after = block.text[end : end + 70]
        context = _context_text(before, after, metric)
    return NumericFact(metric=_normalize_metric(metric), value=value, unit=unit, context=context, source_block=block.id, raw=raw, kind=kind, operator=operator, uncertainty=uncertainty, interval=interval, confidence_level=confidence_level)


def _metric_from_context(text: str, number_start: int) -> str:
    before = text[max(0, number_start - 90) : number_start]
    # Use the final clause so two measurements in one sentence retain their
    # local group labels instead of accidentally sharing the whole sentence.
    before = re.split(r"[。.!?；;\n]|(?<!\d)[,:：，](?!\d)", before)[-1]
    matches = list(_METRIC_CUES.finditer(before))
    if not matches:
        return ""
    cue = matches[-1]
    phrase = before[max(0, cue.start() - 36) : cue.end()]
    phrase = re.sub(r"(?:was|were|is|are|of|for|the|a|an|为|是|达|约|平均)\s+", " ", phrase, flags=re.I)
    phrase = re.sub(r"^[\s\(\[,:：，、-]+|[\s\)\],:：，、-]+$", "", phrase)
    # Keep a short label ending at the cue.  Group labels after the number are
    # placed into context separately.
    words = phrase.split()
    if len(words) > 6:
        phrase = " ".join(words[-6:])
    return _normalize_metric(phrase)


def _table_metric(label: str, header: str) -> str:
    label = re.sub(r"\s+", " ", label).strip()
    header = re.sub(r"\s+", " ", header).strip()
    if not label and not header:
        return ""
    if re.search(r"(?:total|合计|总计|sum)", label, re.I):
        return "table total"
    return _normalize_metric(" ".join(x for x in (label, header) if x))


def _context_text(before: str, after: str, metric: str) -> str:
    text = f"{before} {after}"
    text = re.sub(r"\s+", " ", text).strip(" ,，;；:：()（）[]")
    text = re.sub(r"\b(?:was|were|is|are|of|for|the|a|an|and|with|为|是|达|约)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def _normalize_metric(metric: str) -> str:
    metric = re.sub(r"\s+", " ", metric or "").strip(" ,，;；:：()（）[]")
    return metric.casefold()


def _normalize_unit(unit: str) -> str:
    value = (unit or "").strip().casefold()
    if value in {"％", "percent", "percentage"}:
        return "%"
    if value in {"points", "point", "percentage points", "个百分点", "百分点数"}:
        return "points"
    return value


def _compare_same_metric(facts: Sequence[NumericFact]) -> list[Finding]:
    groups: dict[tuple[str, str, str], list[NumericFact]] = defaultdict(list)
    for fact in facts:
        if fact.kind not in {"value", "p_value", "sample_size"} or fact.value is None:
            continue
        if fact.metric in {"p-value", "sample size", "table total"}:
            continue
        key = (fact.metric, _context_key(fact.context), fact.unit)
        groups[key].append(fact)
    out: list[Finding] = []
    for (metric, context, unit), group in groups.items():
        values = {round(float(f.value), 10) for f in group if f.value is not None}
        if len(values) < 2 or not _same_context_confident(group):
            continue
        quotes = [f.raw for f in group[:4]]
        blocks = list(dict.fromkeys(f.source_block for f in group))
        out.append(Finding(id=f"numeric-{stable_id(metric, context, unit, *sorted(map(str, values)))}", issue_type=IssueType.NUMERIC_INCONSISTENCY, severity=Severity.MAJOR, confidence=0.93, block_ids=blocks[:8], verbatim_quote=quotes[0], rationale=f"同一指标「{metric}」在相同上下文中出现不同取值：{' / '.join(quotes)}。", evidence_refs=quotes, checklist_id="pre-submission/numeric-facts", source="deterministic", needs_author_decision=True))
    return out


def _compare_f1_relationship(facts: Sequence[NumericFact]) -> list[Finding]:
    """Check an explicitly co-reported precision/recall/F1 triplet.

    This rule is intentionally narrow: all three values must be in one block,
    use the same unit and share the same local context.  It therefore avoids
    comparing scores from different models or tables merely because a page
    happens to contain the same metric names.
    """

    by_block: dict[str, list[NumericFact]] = defaultdict(list)
    for fact in facts:
        if fact.kind != "value" or fact.value is None or fact.unit not in {"%", ""}:
            continue
        metric = _metric_family(fact.metric)
        if metric in {"precision", "recall", "f1"}:
            by_block[fact.source_block].append(fact)
    out: list[Finding] = []
    for block_id, group in by_block.items():
        by_metric: dict[str, NumericFact] = {}
        for fact in group:
            by_metric.setdefault(_metric_family(fact.metric), fact)
        if set(by_metric) != {"precision", "recall", "f1"}:
            continue
        if not _f1_context_compatible(tuple(by_metric.values())):
            continue
        precision = _fraction_value(by_metric["precision"])
        recall = _fraction_value(by_metric["recall"])
        observed = _fraction_value(by_metric["f1"])
        if precision is None or recall is None or observed is None or precision + recall <= 0:
            continue
        expected = 2 * precision * recall / (precision + recall)
        if abs(expected - observed) <= 0.02:
            continue
        values = [by_metric[name].raw for name in ("precision", "recall", "f1")]
        out.append(
            Finding(
                id=f"stats-{stable_id(block_id, 'f1', *values)}",
                issue_type=IssueType.STATS_INCONSISTENCY,
                severity=Severity.MAJOR,
                confidence=0.94,
                block_ids=[block_id],
                verbatim_quote=by_metric["f1"].raw,
                rationale=f"同一段报告的 precision/recall 推导 F1 应约为 {expected * 100:.2f}%，但原文写为 {observed * 100:.2f}%。",
                evidence_refs=values,
                checklist_id="pre-submission/statistics",
                source="deterministic",
                needs_author_decision=True,
            )
        )
    return out


def _metric_family(metric: str) -> str:
    value = str(metric or "").casefold()
    if re.search(r"\bprecision\b|精确率|查准率", value):
        return "precision"
    if re.search(r"\brecall\b|召回率|查全率", value):
        return "recall"
    if re.search(r"\bf1(?:[- ]?score)?\b|f1分数|f1值", value):
        return "f1"
    return ""


def _fraction_value(fact: NumericFact) -> float | None:
    if fact.value is None:
        return None
    value = float(fact.value)
    if fact.unit == "%":
        value /= 100.0
    if not 0.0 <= value <= 1.0:
        return None
    return value


def _f1_context_compatible(facts: Sequence[NumericFact]) -> bool:
    """Require meaningful shared context without demanding identical spans."""

    token_sets = []
    for fact in facts:
        text = _context_key(fact.context)
        tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]{2,}", text))
        if not tokens:
            return False
        token_sets.append(tokens)
    shared = set.intersection(*token_sets)
    return len(shared) >= 2


def _compare_p_significance(facts: Sequence[NumericFact]) -> list[Finding]:
    by_block: dict[str, list[NumericFact]] = defaultdict(list)
    for fact in facts:
        if fact.kind == "p_value":
            by_block[fact.source_block].append(fact)
    out: list[Finding] = []
    for block_id, group in by_block.items():
        # The source text is unavailable on a fact by design, so use its raw
        # anchor and context. Context contains the nearby words from the block.
        for fact in group:
            p = fact.value
            if p is None:
                continue
            significant = _p_significance(fact)
            # Inequalities such as ``p > .01`` do not establish either side
            # of the conventional .05 threshold.  Stay silent in that case
            # instead of manufacturing a statistics contradiction.
            if significant is None:
                continue
            context = f"{fact.context} {fact.raw}".casefold()
            neg = bool(re.search(r"(?:not\s+significant|non[- ]?significant|不显著|未达显著|无显著)", context, re.I))
            pos = bool(re.search(r"(?:statistically\s+significant|significant|显著(?:性)?|有显著)", context, re.I)) and not neg
            contradiction = (significant and neg) or ((not significant) and pos)
            if not contradiction:
                continue
            claim = "不显著" if neg else "显著"
            out.append(Finding(id=f"stats-{stable_id(block_id, fact.raw, claim)}", issue_type=IssueType.STATS_INCONSISTENCY, severity=Severity.MAJOR, confidence=0.96, block_ids=[block_id], verbatim_quote=fact.raw, rationale=f"p 值 {fact.raw} 与同一段中的“{claim}”表述矛盾。", evidence_refs=[fact.raw, claim], checklist_id="pre-submission/statistics", source="deterministic", needs_author_decision=True))
    return out


def _compare_p_range(facts: Sequence[NumericFact]) -> list[Finding]:
    """Flag impossible p-values while keeping the source quote intact."""

    out: list[Finding] = []
    for fact in facts:
        if fact.kind != "p_value" or fact.value is None:
            continue
        if 0.0 <= float(fact.value) <= 1.0:
            continue
        out.append(
            Finding(
                id=f"stats-{stable_id(fact.source_block, fact.raw, 'p-range')}",
                issue_type=IssueType.STATS_INCONSISTENCY,
                severity=Severity.MAJOR,
                confidence=0.99,
                block_ids=[fact.source_block],
                verbatim_quote=fact.raw,
                rationale=f"p 值 {fact.raw} 超出 [0, 1] 合法范围。",
                evidence_refs=[fact.raw],
                checklist_id="pre-submission/statistics",
                source="deterministic",
                needs_author_decision=False,
            )
        )
    return out


def _p_significance(fact: NumericFact) -> bool | None:
    """Return a conservative significance interpretation for a p fact."""

    if fact.value is None:
        return None
    value = float(fact.value)
    op = fact.operator or "="
    if op in {"<", "<="}:
        if value > 0.05:
            return False
        return True if op == "<=" or value < 0.05 else None
    if op in {">", ">="}:
        if value < 0.05:
            return None
        return False
    return value < 0.05


def _compare_table_totals(facts: Sequence[NumericFact]) -> list[Finding]:
    by_block: dict[str, list[NumericFact]] = defaultdict(list)
    for fact in facts:
        by_block[fact.source_block].append(fact)
    out: list[Finding] = []
    for block_id, group in by_block.items():
        totals = [f for f in group if f.metric == "table total" and f.value is not None]
        ns = [f for f in group if f.kind == "sample_size" and f.value is not None]
        if not totals or not ns:
            continue
        # If a table has several total columns (for example ``n`` and ``%``),
        # compare only the total in the same count column.  When the header is
        # unavailable, a lone total remains safe to use.
        count_totals = [f for f in totals if re.search(r"(?:\bn\b|sample|样本|人数|count)", f.context, re.I)]
        if count_totals:
            totals = count_totals
        elif len(totals) > 1:
            continue
        # Prefer an explicitly labelled overall N.  Otherwise, the category
        # counts under an ``n`` column are the expected total.
        overall = [f for f in ns if re.search(r"(?:total|overall|合计|总计|总体|整体|all|overall)", f.context, re.I)]
        expected = overall[0] if overall else None
        expected_value = float(expected.value) if expected is not None else sum(float(f.value) for f in ns)
        for total in totals:
            if math.isclose(float(total.value), expected_value, rel_tol=0, abs_tol=1e-9):
                continue
            evidence = [total.raw] + ([expected.raw] if expected else [f.raw for f in ns[:6]])
            label = expected.raw if expected else f"各类别 n 之和={expected_value:g}"
            out.append(Finding(id=f"stats-{stable_id(block_id, 'total-vs-n', total.value, expected_value)}", issue_type=IssueType.STATS_INCONSISTENCY, severity=Severity.MAJOR, confidence=0.97, block_ids=[block_id], verbatim_quote=total.raw, rationale=f"表中总计 {total.value:g} 与样本量 n（{label}）不一致。", evidence_refs=evidence, checklist_id="pre-submission/statistics", source="deterministic", needs_author_decision=True))
    return out


def _same_context_confident(group: Sequence[NumericFact]) -> bool:
    # Contexts must share a meaningful token.  Empty contexts are intentionally
    # rejected because equal metric labels can legitimately differ by group.
    keys = [_context_key(f.context) for f in group]
    return bool(keys and keys[0] and all(k == keys[0] for k in keys))


def _context_key(context: str) -> str:
    value = re.sub(r"\b(?:19|20)\d{2}\b", " ", context.casefold())
    value = re.sub(r"\d+(?:\.\d+)?", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" ,，;；:：()（）[]")
    return value


def _inside_citation(text: str, start: int, end: int) -> bool:
    left = text.rfind("[", 0, start + 1)
    right = text.find("]", end)
    return left >= 0 and right >= 0 and not any(c.isalpha() for c in text[left + 1 : right])


def _has_metric_cue(text: str, start: int) -> bool:
    return bool(_METRIC_CUES.search(text[max(0, start - 90) : start]))


def _looks_like_year(raw: str) -> bool:
    try:
        value = int(float(raw.replace(",", "")))
    except (TypeError, ValueError):
        return False
    return 1900 <= value <= 2100 and re.fullmatch(r"\d{4}", raw.replace(",", "")) is not None


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def _dedupe_facts(facts: Iterable[NumericFact]) -> list[NumericFact]:
    out: list[NumericFact] = []
    seen: set[tuple] = set()
    for fact in facts:
        key = (fact.metric, fact.value, fact.unit, fact.context, fact.source_block, fact.kind, fact.raw)
        if key not in seen:
            seen.add(key)
            out.append(fact)
    return out


def _dedupe_findings(findings: Iterable[Finding]) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.id in seen:
            continue
        seen.add(finding.id)
        out.append(finding)
    return out


__all__ = ["NumericFact", "extract_facts", "extract_numeric_facts", "extract", "compare_facts", "check"]
