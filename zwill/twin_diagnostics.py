from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from .twin_results import distribution_distance_metrics


def row_twin_label(row: dict[str, Any]) -> str:
    return str(row.get("twin_set_label") or row.get("model_label") or row.get("model") or "")


def row_probabilities(row: dict[str, Any]) -> dict[str, float]:
    return {str(option): float(probability) for option, probability in (row.get("probabilities") or {}).items()}


def categorical_distribution(values: list[Any]) -> dict[str, float]:
    counts = Counter(str(value) for value in values if value is not None)
    total = sum(counts.values())
    if not total:
        return {}
    return {option: count / total for option, count in sorted(counts.items())}


def average_probability_distribution(rows: list[dict[str, Any]]) -> dict[str, float]:
    totals: Counter[str] = Counter()
    for row in rows:
        for option, probability in row_probabilities(row).items():
            totals[option] += float(probability)
    if not rows:
        return {}
    return {option: totals[option] / len(rows) for option in sorted(totals)}


def cramers_v_from_joint(joint: dict[tuple[str, str], float]) -> float | None:
    if not joint:
        return None
    row_totals: Counter[str] = Counter()
    col_totals: Counter[str] = Counter()
    total = 0.0
    for (left, right), value in joint.items():
        value = float(value)
        row_totals[left] += value
        col_totals[right] += value
        total += value
    if total <= 0 or len(row_totals) < 2 or len(col_totals) < 2:
        return None
    chi2 = 0.0
    for left in row_totals:
        for right in col_totals:
            expected = row_totals[left] * col_totals[right] / total
            if expected <= 0:
                continue
            observed = float(joint.get((left, right), 0.0))
            chi2 += (observed - expected) ** 2 / expected
    denominator = total * min(len(row_totals) - 1, len(col_totals) - 1)
    if denominator <= 0:
        return None
    return math.sqrt(max(0.0, chi2 / denominator))


def bias_corrected_cramers_v(joint: dict[tuple[str, str], float], n: int) -> float | None:
    """Bergsma (2013) bias-corrected Cramer's V.

    Raw Cramer's V is inflated for high-cardinality variables -- a free-text field
    with near-unique values looks ~1.0 against everything. The correction subtracts
    the expected chi-square under independence, so a leakage audit does not fire on
    the cardinality artifact. `joint` holds proportions; `n` is the sample size.
    """
    if not joint or n < 2:
        return None
    phi2 = cramers_v_from_joint(joint)
    if phi2 is None:
        return None
    row_labels = {left for (left, _right) in joint}
    col_labels = {right for (_left, right) in joint}
    r, c = len(row_labels), len(col_labels)
    if r < 2 or c < 2:
        return None
    # cramers_v_from_joint returns V = sqrt(phi2 / min(r-1, c-1)); recover phi2.
    phi2_value = phi2 * phi2 * min(r - 1, c - 1)
    phi2_tilde = max(0.0, phi2_value - (r - 1) * (c - 1) / (n - 1))
    r_tilde = r - (r - 1) ** 2 / (n - 1)
    c_tilde = c - (c - 1) ** 2 / (n - 1)
    denominator = min(r_tilde - 1, c_tilde - 1)
    if denominator <= 0:
        return 0.0
    return math.sqrt(phi2_tilde / denominator)


def empirical_joint_distribution(left_rows: dict[str, dict[str, Any]], right_rows: dict[str, dict[str, Any]]) -> dict[tuple[str, str], float]:
    counts: Counter[tuple[str, str]] = Counter()
    for respondent_id in sorted(set(left_rows) & set(right_rows)):
        left = left_rows[respondent_id].get("actual_answer")
        right = right_rows[respondent_id].get("actual_answer")
        if left is None or right is None:
            continue
        counts[(str(left), str(right))] += 1
    total = sum(counts.values())
    if not total:
        return {}
    return {key: value / total for key, value in counts.items()}


def twin_implied_joint_distribution(left_rows: dict[str, dict[str, Any]], right_rows: dict[str, dict[str, Any]]) -> dict[tuple[str, str], float]:
    totals: Counter[tuple[str, str]] = Counter()
    respondent_ids = sorted(set(left_rows) & set(right_rows))
    for respondent_id in respondent_ids:
        left_probs = row_probabilities(left_rows[respondent_id])
        right_probs = row_probabilities(right_rows[respondent_id])
        for left, left_probability in left_probs.items():
            for right, right_probability in right_probs.items():
                totals[(left, right)] += left_probability * right_probability
    if not respondent_ids:
        return {}
    return {key: value / len(respondent_ids) for key, value in totals.items()}


def joint_l1(left: dict[tuple[str, str], float], right: dict[tuple[str, str], float]) -> float:
    keys = set(left) | set(right)
    return sum(abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0))) for key in keys)


def build_twin_joint_structure_diagnostics(rows: list[dict[str, Any]], *, min_pair_rows: int = 30, limit: int = 80) -> dict[str, Any]:
    by_model_question: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    question_text: dict[str, str] = {}
    for row in rows:
        label = row_twin_label(row)
        question = str(row.get("heldout_question") or "")
        respondent_id = str(row.get("respondent_id") or "")
        if not label or not question or not respondent_id:
            continue
        by_model_question[(label, question)][respondent_id] = row
        if row.get("heldout_question_text"):
            question_text[question] = str(row.get("heldout_question_text"))
    diagnostics = []
    for label in sorted({key[0] for key in by_model_question}):
        questions = sorted(question for model, question in by_model_question if model == label)
        for left_index, left_question in enumerate(questions):
            for right_question in questions[left_index + 1:]:
                left_rows = by_model_question[(label, left_question)]
                right_rows = by_model_question[(label, right_question)]
                respondents = sorted(set(left_rows) & set(right_rows))
                if len(respondents) < min_pair_rows:
                    continue
                empirical_joint = empirical_joint_distribution(left_rows, right_rows)
                twin_joint = twin_implied_joint_distribution(left_rows, right_rows)
                empirical_v = cramers_v_from_joint(empirical_joint)
                twin_v = cramers_v_from_joint(twin_joint)
                diagnostics.append(
                    {
                        "model_label": label,
                        "left_question": left_question,
                        "left_question_text": question_text.get(left_question, ""),
                        "right_question": right_question,
                        "right_question_text": question_text.get(right_question, ""),
                        "respondents": len(respondents),
                        "joint_l1": joint_l1(twin_joint, empirical_joint),
                        "empirical_cramers_v": empirical_v,
                        "twin_cramers_v": twin_v,
                        "cramers_v_error": abs(twin_v - empirical_v) if twin_v is not None and empirical_v is not None else None,
                        # Signed: negative means the twin-implied association is weaker
                        # than the empirical one (correlation attenuation / over-shrinkage).
                        "cramers_v_gap": (twin_v - empirical_v) if twin_v is not None and empirical_v is not None else None,
                        "warning": "sparse_pair" if len(respondents) < 100 else "",
                    }
                )
    diagnostics.sort(key=lambda item: (item.get("joint_l1", 0.0), item.get("cramers_v_error") or 0.0))
    return {
        "min_pair_rows": min_pair_rows,
        "pair_count": len(diagnostics),
        "rows": diagnostics[:limit],
        "omitted_count": max(0, len(diagnostics) - limit),
        "attenuation": twin_correlation_attenuation(diagnostics),
        "note": "Compares empirical crosstabs with twin-implied crosstabs built by aggregating each respondent's predicted probabilities across pairs of held-out questions.",
    }


def twin_correlation_attenuation(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise whether twin-implied associations are systematically weaker than empirical.

    Because the twin-implied joint is a product of each respondent's marginals, it can
    only reproduce cross-question association through individual heterogeneity, and tends
    to regress everyone toward a common distribution. When the twin's Cramer's V is
    systematically below the empirical V, that is correlation attenuation (over-shrinkage):
    a named, diagnosable failure mode, not just noise in the L1.
    """
    by_model: dict[str, list[float]] = defaultdict(list)
    for row in diagnostics:
        gap = row.get("cramers_v_gap")
        if gap is not None:
            by_model[str(row.get("model_label") or "")].append(float(gap))

    def verdict(gaps: list[float]) -> str:
        if not gaps:
            return "insufficient_pairs"
        mean_gap = sum(gaps) / len(gaps)
        share_attenuated = sum(1 for gap in gaps if gap < 0) / len(gaps)
        if mean_gap <= -0.05 and share_attenuated >= 0.6:
            return "attenuated"  # twin under-models cross-question correlation
        if mean_gap >= 0.05 and share_attenuated <= 0.4:
            return "overstated"  # twin over-states correlation
        return "matched"

    models = {}
    for label, gaps in by_model.items():
        models[label] = {
            "pairs": len(gaps),
            "mean_cramers_v_gap": sum(gaps) / len(gaps) if gaps else None,
            "share_attenuated": sum(1 for gap in gaps if gap < 0) / len(gaps) if gaps else None,
            "verdict": verdict(gaps),
        }
    all_gaps = [gap for gaps in by_model.values() for gap in gaps]
    return {
        "models": models,
        "overall": {
            "pairs": len(all_gaps),
            "mean_cramers_v_gap": sum(all_gaps) / len(all_gaps) if all_gaps else None,
            "share_attenuated": sum(1 for gap in all_gaps if gap < 0) / len(all_gaps) if all_gaps else None,
            "verdict": verdict(all_gaps),
        },
        "note": "twin Cramer's V minus empirical Cramer's V, averaged over question pairs. Negative = the twin under-models cross-question correlation (over-shrinkage toward a common distribution).",
    }


def observed_answer_segments(row: dict[str, Any]) -> list[dict[str, str]]:
    segments = []
    for observed in row.get("observed_answers") or []:
        question = observed.get("question_name")
        answer = observed.get("answer")
        if question and answer is not None:
            segments.append(
                {
                    "segment_question": str(question),
                    "segment_question_text": str(observed.get("question_text") or ""),
                    "segment_value": str(answer),
                }
            )
    return segments


def build_twin_subgroup_marginal_diagnostics(
    rows: list[dict[str, Any]],
    *,
    min_cell_rows: int = 30,
    max_segment_questions: int = 8,
    limit: int = 120,
) -> dict[str, Any]:
    segment_counts: Counter[str] = Counter()
    for row in rows:
        for segment in observed_answer_segments(row):
            segment_counts[segment["segment_question"]] += 1
    segment_questions = {
        question
        for question, _count in segment_counts.most_common(max_segment_questions)
    }
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    segment_text: dict[str, str] = {}
    for row in rows:
        label = row_twin_label(row)
        heldout = str(row.get("heldout_question") or "")
        if not label or not heldout:
            continue
        for segment in observed_answer_segments(row):
            segment_question = segment["segment_question"]
            if segment_question not in segment_questions or segment_question == heldout:
                continue
            segment_text.setdefault(segment_question, segment.get("segment_question_text", ""))
            grouped[(label, heldout, segment_question, segment["segment_value"])].append(row)
    diagnostics = []
    for (label, heldout, segment_question, segment_value), group_rows in grouped.items():
        if len(group_rows) < min_cell_rows:
            continue
        empirical = categorical_distribution([row.get("actual_answer") for row in group_rows])
        predicted = average_probability_distribution(group_rows)
        metrics = distribution_distance_metrics(predicted, empirical) if empirical and predicted else {}
        diagnostics.append(
            {
                "model_label": label,
                "heldout_question": heldout,
                "heldout_question_text": group_rows[0].get("heldout_question_text", ""),
                "segment_question": segment_question,
                "segment_question_text": segment_text.get(segment_question, ""),
                "segment_value": segment_value,
                "rows": len(group_rows),
                "empirical": empirical,
                "twin_implied": predicted,
                "warning": "small_cell" if len(group_rows) < 100 else "",
                **metrics,
            }
        )
    diagnostics.sort(key=lambda item: (-(item.get("l1") or 0.0), str(item.get("heldout_question")), str(item.get("segment_question"))))
    return {
        "min_cell_rows": min_cell_rows,
        "segment_questions_considered": sorted(segment_questions),
        "cell_count": len(diagnostics),
        "rows": diagnostics[:limit],
        "omitted_count": max(0, len(diagnostics) - limit),
        "note": "Scores held-out question marginals within observed context-answer segments. Segments come from answers available in the twin prompt, not from the held-out target itself.",
    }


def build_twin_conditional_consistency_diagnostics(
    rows: list[dict[str, Any]],
    *,
    min_cell_rows: int = 30,
    limit: int = 120,
) -> dict[str, Any]:
    by_model_question_respondent: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    question_text: dict[str, str] = {}
    for row in rows:
        label = row_twin_label(row)
        question = str(row.get("heldout_question") or "")
        respondent_id = str(row.get("respondent_id") or "")
        if not label or not question or not respondent_id:
            continue
        by_model_question_respondent[(label, question)][respondent_id] = row
        if row.get("heldout_question_text"):
            question_text[question] = str(row.get("heldout_question_text"))
    diagnostics = []
    for label in sorted({key[0] for key in by_model_question_respondent}):
        questions = sorted(question for model, question in by_model_question_respondent if model == label)
        for condition_question in questions:
            condition_rows = by_model_question_respondent[(label, condition_question)]
            condition_values = sorted({str(row.get("actual_answer")) for row in condition_rows.values() if row.get("actual_answer") is not None})
            for target_question in questions:
                if target_question == condition_question:
                    continue
                target_rows = by_model_question_respondent[(label, target_question)]
                shared = sorted(set(condition_rows) & set(target_rows))
                for condition_value in condition_values:
                    selected = [respondent_id for respondent_id in shared if str(condition_rows[respondent_id].get("actual_answer")) == condition_value]
                    if len(selected) < min_cell_rows:
                        continue
                    target_subset = [target_rows[respondent_id] for respondent_id in selected]
                    empirical = categorical_distribution([row.get("actual_answer") for row in target_subset])
                    predicted = average_probability_distribution(target_subset)
                    metrics = distribution_distance_metrics(predicted, empirical) if empirical and predicted else {}
                    diagnostics.append(
                        {
                            "model_label": label,
                            "condition_question": condition_question,
                            "condition_question_text": question_text.get(condition_question, ""),
                            "condition_value": condition_value,
                            "target_question": target_question,
                            "target_question_text": question_text.get(target_question, ""),
                            "rows": len(target_subset),
                            "empirical": empirical,
                            "twin_implied": predicted,
                            "warning": "small_cell" if len(target_subset) < 100 else "",
                            **metrics,
                        }
                    )
    diagnostics.sort(key=lambda item: (-(item.get("l1") or 0.0), str(item.get("condition_question")), str(item.get("target_question"))))
    return {
        "min_cell_rows": min_cell_rows,
        "cell_count": len(diagnostics),
        "rows": diagnostics[:limit],
        "omitted_count": max(0, len(diagnostics) - limit),
        "note": "Checks whether twin-implied target distributions remain coherent when conditioning on actual answers to other held-out questions.",
    }


def observed_pair_joint(
    answer_by_respondent: dict[str, dict[str, Any]],
    target_question: str,
    context_question: str,
) -> tuple[dict[tuple[str, str], float], int]:
    """Empirical joint of (target answer, context answer) over respondents who answered both."""
    counts: Counter[tuple[str, str]] = Counter()
    for answers in answer_by_respondent.values():
        target = answers.get(target_question)
        context = answers.get(context_question)
        if target is None or context is None:
            continue
        counts[(str(target), str(context))] += 1
    total = sum(counts.values())
    if not total:
        return {}, 0
    return {key: value / total for key, value in counts.items()}, total


def build_context_leakage_diagnostics(
    questions: list[dict[str, Any]],
    answer_by_respondent: dict[str, dict[str, Any]],
    target_questions: list[str],
    *,
    min_pair_rows: int = 30,
    warn_threshold: float = 0.7,
    limit: int = 200,
) -> dict[str, Any]:
    """Flag context questions that near-deterministically predict a target answer.

    The digital-twin validity threat is leakage: a context item whose value all but
    determines the held-out target lets the model "predict" the target by copying,
    not by modelling a respondent. This measures association strength (Cramer's V)
    between each target and every other question on the observed data, so leaky
    context surfaces as a number to review rather than relying on the analyst to
    catch it by eye.
    """
    question_names = [str(q["question_name"]) for q in questions]
    question_text = {str(q["question_name"]): str(q.get("question_text") or "") for q in questions}
    targets = [name for name in target_questions if name in set(question_names)]

    rows = []
    for target in targets:
        for context_question in question_names:
            if context_question == target:
                continue
            joint, n = observed_pair_joint(answer_by_respondent, target, context_question)
            if n < min_pair_rows:
                continue
            cramers_v = bias_corrected_cramers_v(joint, n)
            if cramers_v is None:
                continue
            target_cardinality = len({left for (left, _right) in joint})
            context_cardinality = len({right for (_left, right) in joint})
            rows.append(
                {
                    "target_question": target,
                    "target_question_text": question_text.get(target, ""),
                    "context_question": context_question,
                    "context_question_text": question_text.get(context_question, ""),
                    "cramers_v": cramers_v,
                    "respondents": n,
                    "target_distinct_answers": target_cardinality,
                    "context_distinct_answers": context_cardinality,
                    "warning": "possible_leakage" if cramers_v >= warn_threshold else "",
                }
            )
    rows.sort(key=lambda item: -item["cramers_v"])
    flagged = [row for row in rows if row["warning"]]
    return {
        "warn_threshold": warn_threshold,
        "min_pair_rows": min_pair_rows,
        "pair_count": len(rows),
        "flagged_count": len(flagged),
        "rows": rows[:limit],
        "omitted_count": max(0, len(rows) - limit),
        "note": "Bias-corrected (Bergsma) Cramer's V between each target and every other question on observed answers. High values mark context that may let a twin copy the target instead of modelling the respondent. The correction prevents high-cardinality (e.g. free-text) context from firing spuriously; check the distinct-answer counts when interpreting.",
    }
