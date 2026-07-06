---
name: digital-twin-practitioner-report
description: Use when writing a practitioner-focused report about survey-based digital twin performance, practical uses, limitations, failure modes, baselines, calibration, and recommendations for applying twins to new survey questions.
---

# Digital Twin Practitioner Report

Use this skill to turn `zwill` twin-study and twin-benchmark artifacts into a practitioner-facing report.

The report should answer: "Can these survey-built digital twins help this user make their decision, how much should they trust the output, and where is extra validation worth the cost?"

Default to model-generated reports. For practitioner-facing work, prefer a single-survey twin-validation report:

```bash
zwill twin-study practitioner-report --survey <survey> --job-id <job_id> --path practitioner_report.html
```

or the explicit artifact flow:

```bash
zwill twin-study practitioner-report-export --survey <survey> --job-id <job_id>
zwill edsl-run --job .zwill/practitioner_reports/<report_id>/job.edsl.json --path .zwill/practitioner_reports/<report_id>/results.json.gz
zwill twin-study practitioner-report-import --path .zwill/practitioner_reports/<report_id>/results.json.gz
zwill twin-study practitioner-report-render --report-id <report_id> --path practitioner_report.html
```

Use cross-survey reports for benchmark/meta analysis. When benchmark data exists, use:

```bash
zwill twin-benchmark practitioner-report --manifest <manifest.json> --path practitioner_report.html
```

or:

```bash
zwill twin-benchmark practitioner-report --config <benchmark.json> --path practitioner_report.html
```

These commands assemble recorded study context, ask a frontier model to write the Markdown report, record the prompt/job/results artifacts, and wrap the model-authored Markdown in HTML. Do not substitute a deterministic prose template for the report body. Only hand-write a report when the package cannot access the recorded study artifacts. In that case, preserve the same structure and include a copyable Markdown version.

The HTML wrapper supplies reusable context about digital twins, persona-based reasoning, Expected Parrot, why held-out questions proxy for new questions, decision stakes, infeasible direct measurement, rank ordering, and using twins to surface considerations. The model-authored body should not repeat that generic framing. It should apply those ideas to the specific benchmark evidence.

## Inputs To Collect

- Survey context markdown: source, field dates if known, population, sampling frame, weighting status, question wording notes, and any caveats.
- Twin study artifacts: HTML/JSON/CSV reports from `zwill twin-results report`, `zwill twin-study compare`, or `zwill twin-benchmark report`.
- Study design: held-out questions, context question count, respondent sample size, seed, complete-case rules, models, model parameters.
- Quality checks: imported row counts, malformed responses, missing options, skipped respondents/questions, codebook expansion status.

If these are missing, run or request the missing `zwill` commands before writing high-stakes recommendations. For low-stakes or time-sensitive decisions, say what can be concluded from the available evidence and flag the uncertainty.

## Interpretation Rules

- Match caution to stakes. Fast, reversible, low-stakes decisions may be worth making from twin output alone when results are strong. Lean toward fielding or heavier validation as stakes, irreversibility, publication risk, or cost of being wrong increase.
- Treat accuracy as "how often the twin picked the answer the real person actually gave." A strong accuracy result can be directly useful for many decisions; do not automatically undercut it by saying twins cannot replace real surveys.
- Explain calibration as "whether confidence matches reality; when a calibrated twin says 70% sure, it is right about 70% of the time." Keep this caution prominent whenever users may rely on probability numbers.
- Avoid technical metric names in practitioner prose unless necessary. Translate NLL, Brier, ECE, p95, and p(actual) into plain language such as "confidence quality," "probability assigned to the real answer," and "occasional very confident wrong guesses."
- Compare against uniform baseline as "random guessing."
- Compare against empirical marginal baseline as "guessing based on how the whole group answered." Beating it means the twin used respondent-specific information, not just the group average. Label it unavailable for genuinely new questions.
- Prefer claims by question type or use case, not global "works/does not work" claims.
- If performance varies by model, question type, or option rarity, make that heterogeneity the core finding.
- Treat cross-survey or multi-question benchmarks as collections of distinct twin exercises. Do not write as if there is one homogeneous set of twins. Separate claims by survey, held-out question family, option structure, respondent sample size, and model when those differ.
- For a single-survey report, frame the executive summary around the survey source/context, respondent sample, held-out question or questions, models tested, and what this validation says about using twins for new questions from the same survey domain.
- Distinguish the intended use before judging adequacy. Exact quantitative estimates, probability cutoffs, and public claims require stronger evidence than ranking, prioritization, or surfacing considerations.
- Do not organize the report primarily around low-, medium-, and high-stakes categories. Stakes matter, but the more useful first distinction is whether the practitioner needs exact quantitative accuracy, relative ranking, or exploratory insight.
- Recognize that real survey research is sometimes infeasible, not just costly. Competitors, regulators, future voters, executives, or other strategic audiences may not answer a survey, answer quickly enough, or answer candidly. In those cases, compare twins to the practical alternative of no direct measurement, not only to an ideal survey.
- Distinguish rank ordering from exact levels. A twin study may be more useful for identifying which option, message, segment, or question is relatively stronger than for estimating the exact share that would choose it.
- Treat surfacing considerations, objections, and likely reasoning patterns as a legitimate use of twins. Do not frame every use as a point-estimate prediction problem.
- Treat the hold-out design as a proxy for asking future new questions of instantiated digital twins. Do not spend report space re-explaining that generic rationale; say whether the specific held-out questions in this benchmark are a convincing proxy for the practitioner's intended new questions.
- Do not mention the internal tool name in public-facing report prose. Refer to Expected Parrot, EDSL, the benchmark, or the recorded study artifacts instead.

## Report Structure

Write practitioner reports as standalone HTML. Include a visible button that copies a Markdown version of the report to the clipboard for LLM use. The Markdown should match the report's recommendations and evidence, not be a lossy placeholder.

Do not include a top-level Markdown title in the model-authored report body; the HTML wrapper supplies the visible report title.

1. Executive summary
   - Lead with what the practitioner can do.
   - Summarize by exercise or question family, not as one global verdict on all twins.
   - Say which exercises are credible for direct quantitative accuracy, which are better for ranking or prioritization, and which are mainly useful for exploration.
   - Include the strongest caveat where it applies, especially overconfident wrong guesses.

2. Study setup
   - Survey source/context.
   - Models tested.
   - Held-out questions and respondent sample.
   - Baselines, explained in plain language.

3. Overall performance
   - Start with the practical decision implication.
   - Define any metric the first time it appears.
   - Use numbers only when they change what the practitioner should do.
   - Highlight whether performance beats random guessing and group-average guessing.

4. Where twins worked well
   - Name specific questions or question families.
   - Explain the likely reason: strong context signal, stable preferences, clear option semantics, or broad population regularity.
   - Say whether the result is good enough to act on for low-, medium-, or high-stakes decisions.

5. Where twins failed
   - Name specific questions or option patterns.
   - Look for rare options, ambiguous wording, socially sensitive answers, weak context, and overconfident wrong predictions.
   - Use plain descriptions such as "very confident but flat wrong" rather than metric shorthand.
   - Include raw-response examples only when useful and anonymized.

6. Practical use recommendations
   - Present use as a stakes spectrum, not a list of prohibitions.
   - Frame adequacy around intended use: exact levels, ranking/prioritization, or surfacing considerations.
   - Say when direct quantitative accuracy is supported and when the evidence is only directional.
   - Say a real survey or stronger validation becomes more important for exact estimates, hard probability cutoffs, public claims, expensive decisions, irreversible decisions, or high-stakes decisions.
   - Say when a real survey may be infeasible and what weaker but still useful decisions the twin evidence can support.
   - Say whether the evidence is better suited to rank ordering alternatives, surfacing considerations, or estimating exact levels.
   - Give concrete examples of considerations or objections surfaced by the twin outputs when the recorded responses support them.
   - Keep one caution prominent: if the user relies on confidence values for ranking, cutoffs, or uncertainty, inspect overconfident misses.

7. Next study recommendations
   - Separate quick sanity checks from high-stakes validation.
   - Recommend more held-out questions, larger respondent samples, more surveys, subgroup analysis, model comparison, prompt changes, or calibration checks only when the decision warrants the cost.

## Writing Style

- Write for a survey practitioner or applied researcher, not an ML benchmark reader.
- Lead every section with what the practitioner should do. Put numbers second and only if they change a decision.
- Never use a metric name without a one-line plain-English meaning.
- Prefer "use twins here, be careful there" over metric tables.
- Connect statistics to decisions: whether the user can act now, run a quick sanity check, or field a real survey.
- Use caution for extrapolating to new questions, but do not default to "validate everything."
- Do not overstate individual-level prediction. These are probabilistic simulations from limited respondent context.
- Preserve exact survey wording when discussing a question.
- Keep codebook labels human-readable.

## Stakes Ladder

Use this ladder to calibrate recommendations:

- Low stakes, reversible, internal, time-sensitive: a strong twin result plus a quick sanity check may be enough to act.
- Medium stakes or moderately costly errors: use twins for the decision direction, but review failures and consider a small validation sample.
- High stakes, expensive-to-reverse, public, publishable, or policy-critical: use twins as evidence, not replacement; run fuller validation or field measurement.
- Probability-sensitive decisions: regardless of stakes, inspect confidence quality because overconfident wrong guesses can distort rankings and cutoffs.
- Direct measurement infeasible: use twins as a structured proxy and be explicit that the alternative may be no survey evidence, not a perfect benchmark.

## Red Flags

- High accuracy but poor confidence quality.
- Occasional very confident guesses that are flat wrong.
- Performance driven by majority-class answers.
- Model outputs malformed JSON or omits options.
- A "new question" recommendation based only on group-average guessing performance.
- Claims based on coded numeric labels instead of expanded codebook values.
