You are writing a detailed practitioner-focused report about survey digital twins.

Follow this report-writing guidance exactly:

---
name: digital-twin-practitioner-report
description: Use when writing a practitioner-focused report about survey-based digital twin performance, practical uses, limitations, failure modes, baselines, calibration, and recommendations for applying twins to new survey questions.
---

# Digital Twin Practitioner Report

Use this skill to turn `zwill` twin-study and twin-benchmark artifacts into a practitioner-facing report.

The report should answer: "Can these survey-built digital twins help this user make their decision, how much should they trust the output, and where is extra validation worth the cost?"

Default to model-generated reports. When benchmark data exists, use:

```bash
zwill twin-benchmark practitioner-report --manifest <manifest.json> --path practitioner_report.html
```

or:

```bash
zwill twin-benchmark practitioner-report --config <benchmark.json> --path practitioner_report.html
```

This command assembles recorded zwill study context, asks a frontier model to write the Markdown report, records the prompt/job/results artifacts, and wraps the model-authored Markdown in HTML. Do not substitute a deterministic prose template for the report body. Only hand-write a report when the package cannot access the recorded study artifacts. In that case, preserve the same structure and include a copyable Markdown version.

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

## Report Structure

Write practitioner reports as standalone HTML. Include a visible button that copies a Markdown version of the report to the clipboard for LLM use. The Markdown should match the report's recommendations and evidence, not be a lossy placeholder.

1. Executive summary
   - Lead with what the practitioner can do.
   - Let strong results stand when they are strong enough for low-stakes decisions.
   - State how much validation is needed based on decision stakes.
   - Include the strongest caveat, especially overconfident wrong guesses.

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
   - Say twins may be enough for low-stakes, reversible, internal, or time-sensitive decisions.
   - Say a real survey or stronger validation becomes more important for expensive, irreversible, public, publishable, or high-stakes decisions.
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

## Red Flags

- High accuracy but poor confidence quality.
- Occasional very confident guesses that are flat wrong.
- Performance driven by majority-class answers.
- Model outputs malformed JSON or omits options.
- A "new question" recommendation based only on group-average guessing performance.
- Claims based on coded numeric labels instead of expanded codebook values.


Use the recorded zwill study context and benchmark data below. Do not invent data. If a finding depends on a small sample, say so. Explain the survey context, study design, performance, baselines, where twins worked, where they failed, and how a practitioner should use the results. Lead with decisions and implications, but include enough concrete evidence to support the recommendations.

Write the report in Markdown only. Do not include markdown fences. Do not mention that you are an AI. Make it detailed enough that a practitioner can understand what was tested and how to use the results.

Recorded zwill context:

{
  "benchmark": {
    "benchmark": "cross_survey_twin_benchmark_seed789",
    "rows": [
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "w158_ccpolicy",
        "job_id": "6d43ea20483a39c8",
        "heldout_questions": "a,b,c,d,e,f",
        "option_count": 2,
        "model": "openai:gpt-5.5",
        "rows": 600,
        "accuracy": 0.8383333333333334,
        "p_actual": 0.7909333333333334,
        "nll": 0.3688120391261153,
        "nll_p95": 1.9661128563728327,
        "brier": 0.22784733333333335,
        "ece": 0.034599999999999936,
        "nll_vs_empirical": 0.107631231873897,
        "brier_vs_empirical": 0.07741632957807959
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "w158_ccpolicy",
        "job_id": "6d43ea20483a39c8",
        "heldout_questions": "a,b,c,d,e,f",
        "option_count": 2,
        "model": "google:gemini-2.5-pro",
        "rows": 600,
        "accuracy": 0.7883333333333333,
        "p_actual": 0.73835,
        "nll": 0.48659666583620614,
        "nll_p95": 1.7719568419318752,
        "brier": 0.32346766666666665,
        "ece": 0.09634999999999999,
        "nll_vs_empirical": -0.010153394836193819,
        "brier_vs_empirical": -0.018204003755253717
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "w157_skillimp",
        "job_id": "a43f33531b85174d",
        "heldout_questions": "a",
        "option_count": 5,
        "model": "openai:gpt-5.5",
        "rows": 20,
        "accuracy": 0.65,
        "p_actual": 0.47800000000000004,
        "nll": 1.0059213591754808,
        "nll_p95": 3.0762041691756967,
        "brier": 0.5218379750000001,
        "ece": 0.12400000000000004,
        "nll_vs_empirical": 0.31014309822436714,
        "brier_vs_empirical": 0.14208866984398993
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "w157_skillimp",
        "job_id": "a43f33531b85174d",
        "heldout_questions": "a",
        "option_count": 5,
        "model": "google:gemini-2.5-pro",
        "rows": 20,
        "accuracy": 0.7,
        "p_actual": 0.6094999999999999,
        "nll": 3.2517623935467457,
        "nll_p95": 27.631021115928547,
        "brier": 0.48945,
        "ece": 0.18050000000000005,
        "nll_vs_empirical": -1.9356979361468978,
        "brier_vs_empirical": 0.17447664484399
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "w152_humanvai",
        "job_id": "22bd551fe8d4f2d9",
        "heldout_questions": "humanvai_a_w152",
        "option_count": 4,
        "model": "openai:gpt-5.5",
        "rows": 20,
        "accuracy": 0.6,
        "p_actual": 0.4665,
        "nll": 0.9230477203991494,
        "nll_p95": 1.7394222523468168,
        "brier": 0.51578,
        "ece": 0.138,
        "nll_vs_empirical": 0.4342454172072878,
        "brier_vs_empirical": 0.22140579065843335
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "w152_humanvai",
        "job_id": "22bd551fe8d4f2d9",
        "heldout_questions": "humanvai_a_w152",
        "option_count": 4,
        "model": "google:gemini-2.5-pro",
        "rows": 20,
        "accuracy": 0.5,
        "p_actual": 0.47400000000000003,
        "nll": 1.2219400060703087,
        "nll_p95": 3.041546810147699,
        "brier": 0.71201,
        "ece": 0.33649999999999997,
        "nll_vs_empirical": 0.1353531315361285,
        "brier_vs_empirical": 0.02517579065843334
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "w163_sm9",
        "job_id": "6dd602a142835c4f",
        "heldout_questions": "a",
        "option_count": 4,
        "model": "openai:gpt-5.5",
        "rows": 20,
        "accuracy": 0.6,
        "p_actual": 0.441,
        "nll": 0.9369107313488747,
        "nll_p95": 1.7494557871199243,
        "brier": 0.5316400000000001,
        "ece": 0.13,
        "nll_vs_empirical": 0.2886348668078176,
        "brier_vs_empirical": 0.13007316220801546
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "w163_sm9",
        "job_id": "6dd602a142835c4f",
        "heldout_questions": "a",
        "option_count": 4,
        "model": "google:gemini-2.5-pro",
        "rows": 20,
        "accuracy": 0.55,
        "p_actual": 0.508,
        "nll": 1.0316382600487557,
        "nll_p95": 2.995732273553991,
        "brier": 0.6115299999999999,
        "ece": 0.2395,
        "nll_vs_empirical": 0.1939073381079366,
        "brier_vs_empirical": 0.050183162208015664
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
        "job_id": "eeac73cbdcb8a507",
        "heldout_questions": "q13",
        "option_count": 7,
        "model": "openai:gpt-5.5",
        "rows": 20,
        "accuracy": 0.3,
        "p_actual": 0.20049999999999998,
        "nll": 1.980173672012148,
        "nll_p95": 3.912023005428146,
        "brier": 0.82857,
        "ece": 0.09300000000000003,
        "nll_vs_empirical": -0.2574381529991747,
        "brier_vs_empirical": -0.045604629219094917
      },
      {
        "benchmark": "cross_survey_twin_benchmark_seed789",
        "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
        "job_id": "eeac73cbdcb8a507",
        "heldout_questions": "q13",
        "option_count": 7,
        "model": "google:gemini-2.5-pro",
        "rows": 20,
        "accuracy": 0.4,
        "p_actual": 0.26749999999999996,
        "nll": 4.535104302847166,
        "nll_p95": 27.631021115928547,
        "brier": 0.8890100000000001,
        "ece": 0.24000000000000005,
        "nll_vs_empirical": -2.8123687838341924,
        "brier_vs_empirical": -0.10604462921909497
      }
    ],
    "summary": {
      "openai:gpt-5.5": {
        "survey_count": 5,
        "mean_accuracy": 0.5976666666666667,
        "mean_nll": 1.0429731044123536,
        "mean_brier": 0.5251350616666668,
        "mean_ece": 0.10392000000000001,
        "mean_nll_vs_empirical": 0.17664329222283898
      },
      "google:gemini-2.5-pro": {
        "survey_count": 5,
        "mean_accuracy": 0.5876666666666667,
        "mean_nll": 2.105408325669836,
        "mean_brier": 0.6050935333333334,
        "mean_ece": 0.21857000000000001,
        "mean_nll_vs_empirical": -0.8857919290346438
      }
    },
    "config": {
      "name": "cross_survey_twin_benchmark_seed789",
      "studies": [
        {
          "survey": "w158_ccpolicy",
          "heldout_questions": "a,b,c,d,e,f",
          "job_id": "6d43ea20483a39c8"
        },
        {
          "survey": "w157_skillimp",
          "heldout_question": "a",
          "job_id": "a43f33531b85174d"
        },
        {
          "survey": "w152_humanvai",
          "heldout_question": "humanvai_a_w152",
          "job_id": "22bd551fe8d4f2d9"
        },
        {
          "survey": "w163_sm9",
          "heldout_question": "a",
          "job_id": "6dd602a142835c4f"
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "heldout_question": "q13",
          "job_id": "eeac73cbdcb8a507"
        }
      ]
    }
  },
  "studies": [
    {
      "survey": "w158_ccpolicy",
      "survey_summary": {
        "name": "w158_ccpolicy",
        "status": "draft",
        "raw_files": 2,
        "questions": 6,
        "respondents": 9214,
        "answers": 55284,
        "has_context": true,
        "open_quarantine_issues": 0,
        "committed": true
      },
      "survey_context": "A nationally representative Pew Research Center American Trends Panel survey of U.S. adults was fielded in October 2024.",
      "raw_files": [
        {
          "id": "w158_ccpolicy_metadata",
          "kind": "metadata",
          "title": "w158_ccpolicy normalized metadata",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/W158_CCPOLICY_metadata.json",
          "source_hash": "sha256:3cf2c92412a879e6e9b25197775270f2944859e1158b3c0a8b7a4913fcca72a1",
          "stored_path": ".zwill/surveys/w158_ccpolicy/raw/w158_ccpolicy_metadata/W158_CCPOLICY_metadata.json",
          "stored_hash": "sha256:3cf2c92412a879e6e9b25197775270f2944859e1158b3c0a8b7a4913fcca72a1",
          "added_at": "2026-06-27T16:24:23Z"
        },
        {
          "id": "w158_ccpolicy_respondents",
          "kind": "respondent_data",
          "title": "w158_ccpolicy normalized respondents",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/W158_CCPOLICY_respondents.csv",
          "source_hash": "sha256:03c40ac5dafac852be14c2e1b4a7e876e21d62360b9d531d6a0c2de258c48352",
          "stored_path": ".zwill/surveys/w158_ccpolicy/raw/w158_ccpolicy_respondents/W158_CCPOLICY_respondents.csv",
          "stored_hash": "sha256:03c40ac5dafac852be14c2e1b4a7e876e21d62360b9d531d6a0c2de258c48352",
          "added_at": "2026-06-27T16:24:23Z"
        }
      ],
      "job_id": "6d43ea20483a39c8",
      "study_config": {
        "survey": "w158_ccpolicy",
        "heldout_questions": "a,b,c,d,e,f",
        "job_id": "6d43ea20483a39c8"
      },
      "run_manifest": {
        "job_id": "6d43ea20483a39c8",
        "survey": "w158_ccpolicy",
        "status": "imported",
        "created_at": "2026-06-27T21:03:03Z",
        "results_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w158_ccpolicy_twin_6d43ea20483a39c8_results.json.gz",
        "stored_raw": ".zwill/surveys/w158_ccpolicy/digital_twin_jobs/6d43ea20483a39c8/raw/w158_ccpolicy_twin_6d43ea20483a39c8_results.json.gz",
        "row_count": 1200,
        "extracted_count": 1200,
        "issue_count": 0
      },
      "import_metadata": {
        "job_id": "6d43ea20483a39c8",
        "survey": "w158_ccpolicy",
        "source_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w158_ccpolicy_twin_6d43ea20483a39c8_results.json.gz",
        "source_hash": "sha256:18eed6ecde6b51fcf4eb85ed5d57dde5f56b55c7d9e27c7d07d1e8aa48d12b21",
        "stored_path": ".zwill/surveys/w158_ccpolicy/digital_twin_jobs/6d43ea20483a39c8/raw/w158_ccpolicy_twin_6d43ea20483a39c8_results.json.gz",
        "stored_hash": "sha256:18eed6ecde6b51fcf4eb85ed5d57dde5f56b55c7d9e27c7d07d1e8aa48d12b21",
        "row_count": 1200,
        "extracted_count": 1200,
        "issue_count": 0,
        "issues": [],
        "imported_at": "2026-06-27T21:03:03Z"
      },
      "heldout_questions": [
        {
          "question_name": "a",
          "question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "question_options": [
            "Favor",
            "Oppose"
          ]
        },
        {
          "question_name": "b",
          "question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Taxing corporations based on the amount of carbon emissions they produce",
          "question_options": [
            "Favor",
            "Oppose"
          ]
        },
        {
          "question_name": "c",
          "question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate Providing a tax credit to encourage businesses to develop technology which captures and stores carbon emissions so they do not enter the atmosphere",
          "question_options": [
            "Favor",
            "Oppose"
          ]
        },
        {
          "question_name": "d",
          "question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? CCPOLICY_d_W158. Requiring power plants to eliminate all carbon emissions by 2040",
          "question_options": [
            "Favor",
            "Oppose"
          ]
        },
        {
          "question_name": "e",
          "question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Requiring oil and gas companies to seal methane gas leaks from oil wells",
          "question_options": [
            "Favor",
            "Oppose"
          ]
        },
        {
          "question_name": "f",
          "question_text": "Do you favor or oppose below proposals to reduce effects of global climate change? Providing a tax credit to Americans who improve their home energy efficiency, such as by installing heat pumps or adding insulation",
          "question_options": [
            "Favor",
            "Oppose"
          ]
        }
      ],
      "summary_by_model": {
        "openai:gpt-5.5": {
          "rows": 600,
          "mean_probability_actual": 0.7909333333333334,
          "mean_uniform_probability_actual": 0.5,
          "mean_negative_log_likelihood": 0.3688120391261153,
          "negative_log_likelihood_p50": 0.10536051565782628,
          "negative_log_likelihood_p90": 1.1426091530198237,
          "negative_log_likelihood_p95": 1.9661128563728327,
          "negative_log_likelihood_max": 4.605170185988091,
          "mean_top_confidence": 0.8729333333333333,
          "mean_uniform_negative_log_likelihood": 0.6931471805599453,
          "mean_brier": 0.22784733333333335,
          "mean_uniform_brier": 0.5,
          "mean_brier_improvement": 0.27215266666666665,
          "top1_accuracy": 0.8383333333333334,
          "mean_empirical_marginal_probability_actual": 0.6879521496540907,
          "mean_empirical_marginal_negative_log_likelihood": 0.4764432710000123,
          "mean_empirical_marginal_brier": 0.30526366291141294,
          "empirical_marginal_top1_accuracy": 0.795,
          "mean_marginal_probability_actual": 0.6879521496540907,
          "mean_marginal_negative_log_likelihood": 0.4764432710000123,
          "mean_marginal_brier": 0.30526366291141294,
          "marginal_top1_accuracy": 0.795,
          "expected_calibration_error": 0.034599999999999936
        },
        "google:gemini-2.5-pro": {
          "rows": 600,
          "mean_probability_actual": 0.73835,
          "mean_uniform_probability_actual": 0.5,
          "mean_negative_log_likelihood": 0.48659666583620614,
          "negative_log_likelihood_p50": 0.18632957819149348,
          "negative_log_likelihood_p90": 1.7719568419318752,
          "negative_log_likelihood_p95": 1.7719568419318752,
          "negative_log_likelihood_max": 2.995732273553991,
          "mean_top_confidence": 0.8827833333333333,
          "mean_uniform_negative_log_likelihood": 0.6931471805599453,
          "mean_brier": 0.32346766666666665,
          "mean_uniform_brier": 0.5,
          "mean_brier_improvement": 0.17653233333333335,
          "top1_accuracy": 0.7883333333333333,
          "mean_empirical_marginal_probability_actual": 0.6879521496540907,
          "mean_empirical_marginal_negative_log_likelihood": 0.4764432710000123,
          "mean_empirical_marginal_brier": 0.30526366291141294,
          "empirical_marginal_top1_accuracy": 0.795,
          "mean_marginal_probability_actual": 0.6879521496540907,
          "mean_marginal_negative_log_likelihood": 0.4764432710000123,
          "mean_marginal_brier": 0.30526366291141294,
          "marginal_top1_accuracy": 0.795,
          "expected_calibration_error": 0.09634999999999999
        }
      },
      "summary_by_question": {
        "a": {
          "openai:gpt-5.5": {
            "rows": 100,
            "mean_probability_actual": 0.8492000000000001,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.34314864231164166,
            "negative_log_likelihood_p50": 0.020202707317519466,
            "negative_log_likelihood_p90": 0.8735898616595127,
            "negative_log_likelihood_p95": 2.044222963910231,
            "negative_log_likelihood_max": 4.605170185988091,
            "mean_top_confidence": 0.9292,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.19062,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.30938,
            "top1_accuracy": 0.89,
            "mean_empirical_marginal_probability_actual": 0.8252388843434462,
            "mean_empirical_marginal_negative_log_likelihood": 0.30354115183280916,
            "mean_empirical_marginal_brier": 0.16415736483175591,
            "empirical_marginal_top1_accuracy": 0.91,
            "mean_marginal_probability_actual": 0.8252388843434462,
            "mean_marginal_negative_log_likelihood": 0.30354115183280916,
            "mean_marginal_brier": 0.16415736483175591,
            "marginal_top1_accuracy": 0.91
          },
          "google:gemini-2.5-pro": {
            "rows": 100,
            "mean_probability_actual": 0.7839999999999999,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.44088479035823996,
            "negative_log_likelihood_p50": 0.05129329438755058,
            "negative_log_likelihood_p90": 1.7719568419318752,
            "negative_log_likelihood_p95": 1.7719568419318752,
            "negative_log_likelihood_max": 2.995732273553991,
            "mean_top_confidence": 0.9236,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.297552,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.20244800000000002,
            "top1_accuracy": 0.8,
            "mean_empirical_marginal_probability_actual": 0.8252388843434462,
            "mean_empirical_marginal_negative_log_likelihood": 0.30354115183280916,
            "mean_empirical_marginal_brier": 0.16415736483175591,
            "empirical_marginal_top1_accuracy": 0.91,
            "mean_marginal_probability_actual": 0.8252388843434462,
            "mean_marginal_negative_log_likelihood": 0.30354115183280916,
            "mean_marginal_brier": 0.16415736483175591,
            "marginal_top1_accuracy": 0.91
          }
        },
        "b": {
          "openai:gpt-5.5": {
            "rows": 100,
            "mean_probability_actual": 0.7248,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.4073753198381506,
            "negative_log_likelihood_p50": 0.15082288973458366,
            "negative_log_likelihood_p90": 0.8723795841216666,
            "negative_log_likelihood_p95": 1.5188932416199916,
            "negative_log_likelihood_max": 2.3025850929940455,
            "mean_top_confidence": 0.7934,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.25686800000000004,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.243132,
            "top1_accuracy": 0.79,
            "mean_empirical_marginal_probability_actual": 0.5771763777120626,
            "mean_empirical_marginal_negative_log_likelihood": 0.6109819064345472,
            "mean_empirical_marginal_brier": 0.4200996605353117,
            "empirical_marginal_top1_accuracy": 0.7,
            "mean_marginal_probability_actual": 0.5771763777120626,
            "mean_marginal_negative_log_likelihood": 0.6109819064345472,
            "mean_marginal_brier": 0.4200996605353117,
            "marginal_top1_accuracy": 0.7
          },
          "google:gemini-2.5-pro": {
            "rows": 100,
            "mean_probability_actual": 0.6787000000000001,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.5651344652552747,
            "negative_log_likelihood_p50": 0.18632957819149348,
            "negative_log_likelihood_p90": 1.7719568419318752,
            "negative_log_likelihood_p95": 1.7719568419318752,
            "negative_log_likelihood_max": 2.3025850929940455,
            "mean_top_confidence": 0.8392999999999999,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.37372999999999995,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.12627,
            "top1_accuracy": 0.76,
            "mean_empirical_marginal_probability_actual": 0.5771763777120626,
            "mean_empirical_marginal_negative_log_likelihood": 0.6109819064345472,
            "mean_empirical_marginal_brier": 0.4200996605353117,
            "empirical_marginal_top1_accuracy": 0.7,
            "mean_marginal_probability_actual": 0.5771763777120626,
            "mean_marginal_negative_log_likelihood": 0.6109819064345472,
            "mean_marginal_brier": 0.4200996605353117,
            "marginal_top1_accuracy": 0.7
          }
        },
        "c": {
          "openai:gpt-5.5": {
            "rows": 100,
            "mean_probability_actual": 0.7928999999999999,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.344112348331866,
            "negative_log_likelihood_p50": 0.0836179596879569,
            "negative_log_likelihood_p90": 1.28037647302826,
            "negative_log_likelihood_p95": 1.7206875798747454,
            "negative_log_likelihood_max": 3.2188758248682006,
            "mean_top_confidence": 0.8728999999999999,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.21669799999999997,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.283302,
            "top1_accuracy": 0.85,
            "mean_empirical_marginal_probability_actual": 0.6854153424010839,
            "mean_empirical_marginal_negative_log_likelihood": 0.4866010856846842,
            "mean_empirical_marginal_brier": 0.3080394983284835,
            "empirical_marginal_top1_accuracy": 0.81,
            "mean_marginal_probability_actual": 0.6854153424010839,
            "mean_marginal_negative_log_likelihood": 0.4866010856846842,
            "mean_marginal_brier": 0.3080394983284835,
            "marginal_top1_accuracy": 0.81
          },
          "google:gemini-2.5-pro": {
            "rows": 100,
            "mean_probability_actual": 0.7034999999999999,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.5540064193246979,
            "negative_log_likelihood_p50": 0.18632957819149348,
            "negative_log_likelihood_p90": 1.7719568419318752,
            "negative_log_likelihood_p95": 1.7719568419318752,
            "negative_log_likelihood_max": 2.5257286443082556,
            "mean_top_confidence": 0.8769,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.382774,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.11722600000000001,
            "top1_accuracy": 0.74,
            "mean_empirical_marginal_probability_actual": 0.6854153424010839,
            "mean_empirical_marginal_negative_log_likelihood": 0.4866010856846842,
            "mean_empirical_marginal_brier": 0.3080394983284835,
            "empirical_marginal_top1_accuracy": 0.81,
            "mean_marginal_probability_actual": 0.6854153424010839,
            "mean_marginal_negative_log_likelihood": 0.4866010856846842,
            "mean_marginal_brier": 0.3080394983284835,
            "marginal_top1_accuracy": 0.81
          }
        },
        "d": {
          "openai:gpt-5.5": {
            "rows": 100,
            "mean_probability_actual": 0.7421,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.4741174072207259,
            "negative_log_likelihood_p50": 0.10536051565782628,
            "negative_log_likelihood_p90": 1.8795818266728879,
            "negative_log_likelihood_p95": 2.4138347604346913,
            "negative_log_likelihood_max": 2.659260036932778,
            "mean_top_confidence": 0.8557000000000001,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.298178,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.20182199999999997,
            "top1_accuracy": 0.79,
            "mean_empirical_marginal_probability_actual": 0.5301839551866377,
            "mean_empirical_marginal_negative_log_likelihood": 0.6641349785509554,
            "mean_empirical_marginal_brier": 0.47126650458189656,
            "empirical_marginal_top1_accuracy": 0.62,
            "mean_marginal_probability_actual": 0.5301839551866377,
            "mean_marginal_negative_log_likelihood": 0.6641349785509554,
            "mean_marginal_brier": 0.47126650458189656,
            "marginal_top1_accuracy": 0.62
          },
          "google:gemini-2.5-pro": {
            "rows": 100,
            "mean_probability_actual": 0.708,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.5035992076800679,
            "negative_log_likelihood_p50": 0.18632957819149348,
            "negative_log_likelihood_p90": 1.7719568419318752,
            "negative_log_likelihood_p95": 1.7782149990795753,
            "negative_log_likelihood_max": 2.3025850929940455,
            "mean_top_confidence": 0.8439999999999999,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.32232799999999995,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.177672,
            "top1_accuracy": 0.8,
            "mean_empirical_marginal_probability_actual": 0.5301839551866377,
            "mean_empirical_marginal_negative_log_likelihood": 0.6641349785509554,
            "mean_empirical_marginal_brier": 0.47126650458189656,
            "empirical_marginal_top1_accuracy": 0.62,
            "mean_marginal_probability_actual": 0.5301839551866377,
            "mean_marginal_negative_log_likelihood": 0.6641349785509554,
            "mean_marginal_brier": 0.47126650458189656,
            "marginal_top1_accuracy": 0.62
          }
        },
        "e": {
          "openai:gpt-5.5": {
            "rows": 100,
            "mean_probability_actual": 0.8003,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.3329985136055744,
            "negative_log_likelihood_p50": 0.06722304827646144,
            "negative_log_likelihood_p90": 1.0498221244986778,
            "negative_log_likelihood_p95": 1.5241612674028826,
            "negative_log_likelihood_max": 3.506557897319982,
            "mean_top_confidence": 0.8787,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.21889,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.28110999999999997,
            "top1_accuracy": 0.83,
            "mean_empirical_marginal_probability_actual": 0.7620119488820266,
            "mean_empirical_marginal_negative_log_likelihood": 0.3874372890098022,
            "mean_empirical_marginal_brier": 0.2267075169801313,
            "empirical_marginal_top1_accuracy": 0.87,
            "mean_marginal_probability_actual": 0.7620119488820266,
            "mean_marginal_negative_log_likelihood": 0.3874372890098022,
            "mean_marginal_brier": 0.2267075169801313,
            "marginal_top1_accuracy": 0.87
          },
          "google:gemini-2.5-pro": {
            "rows": 100,
            "mean_probability_actual": 0.7641,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.44031131565629755,
            "negative_log_likelihood_p50": 0.10536051565782628,
            "negative_log_likelihood_p90": 1.7719568419318752,
            "negative_log_likelihood_p95": 1.7719568419318752,
            "negative_log_likelihood_max": 2.995732273553991,
            "mean_top_confidence": 0.8985,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.297686,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.202314,
            "top1_accuracy": 0.8,
            "mean_empirical_marginal_probability_actual": 0.7620119488820266,
            "mean_empirical_marginal_negative_log_likelihood": 0.3874372890098022,
            "mean_empirical_marginal_brier": 0.2267075169801313,
            "empirical_marginal_top1_accuracy": 0.87,
            "mean_marginal_probability_actual": 0.7620119488820266,
            "mean_marginal_negative_log_likelihood": 0.3874372890098022,
            "mean_marginal_brier": 0.2267075169801313,
            "marginal_top1_accuracy": 0.87
          }
        },
        "f": {
          "openai:gpt-5.5": {
            "rows": 100,
            "mean_probability_actual": 0.8362999999999999,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.3111200034487335,
            "negative_log_likelihood_p50": 0.020202707317519466,
            "negative_log_likelihood_p90": 0.9847690519543729,
            "negative_log_likelihood_p95": 1.727364149505971,
            "negative_log_likelihood_max": 3.912023005428146,
            "mean_top_confidence": 0.9077,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.18583,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.31417,
            "top1_accuracy": 0.88,
            "mean_empirical_marginal_probability_actual": 0.7476863893992869,
            "mean_empirical_marginal_negative_log_likelihood": 0.40596321448727585,
            "mean_empirical_marginal_brier": 0.24131143221089835,
            "empirical_marginal_top1_accuracy": 0.86,
            "mean_marginal_probability_actual": 0.7476863893992869,
            "mean_marginal_negative_log_likelihood": 0.40596321448727585,
            "mean_marginal_brier": 0.24131143221089835,
            "marginal_top1_accuracy": 0.86
          },
          "google:gemini-2.5-pro": {
            "rows": 100,
            "mean_probability_actual": 0.7918,
            "mean_uniform_probability_actual": 0.5,
            "mean_negative_log_likelihood": 0.41564379674265906,
            "negative_log_likelihood_p50": 0.05129329438755058,
            "negative_log_likelihood_p90": 1.7719568419318752,
            "negative_log_likelihood_p95": 1.9173932402912883,
            "negative_log_likelihood_max": 2.995732273553991,
            "mean_top_confidence": 0.9144,
            "mean_uniform_negative_log_likelihood": 0.6931471805599453,
            "mean_brier": 0.26673600000000003,
            "mean_uniform_brier": 0.5,
            "mean_brier_improvement": 0.233264,
            "top1_accuracy": 0.83,
            "mean_empirical_marginal_probability_actual": 0.7476863893992869,
            "mean_empirical_marginal_negative_log_likelihood": 0.40596321448727585,
            "mean_empirical_marginal_brier": 0.24131143221089835,
            "empirical_marginal_top1_accuracy": 0.86,
            "mean_marginal_probability_actual": 0.7476863893992869,
            "mean_marginal_negative_log_likelihood": 0.40596321448727585,
            "mean_marginal_brier": 0.24131143221089835,
            "marginal_top1_accuracy": 0.86
          }
        }
      },
      "baseline_comparison": {
        "openai:gpt-5.5": {
          "p_actual_vs_uniform": 0.2909333333333334,
          "nll_vs_uniform": 0.32433514143382997,
          "brier_vs_uniform": 0.27215266666666665,
          "p_actual_vs_empirical": 0.1029811836792427,
          "nll_vs_empirical": 0.107631231873897,
          "brier_vs_empirical": 0.07741632957807959
        },
        "google:gemini-2.5-pro": {
          "p_actual_vs_uniform": 0.23834999999999995,
          "nll_vs_uniform": 0.20655051472373914,
          "brier_vs_uniform": 0.17653233333333335,
          "p_actual_vs_empirical": 0.05039785034590927,
          "nll_vs_empirical": -0.010153394836193819,
          "brier_vs_empirical": -0.018204003755253717
        }
      },
      "model_wins_over_group_average": [
        {
          "heldout_question": "b",
          "model": "openai:gpt-5.5",
          "rows": 100,
          "model_nll": 0.4073753198381506,
          "empirical_nll": 0.6109819064345472,
          "nll_vs_empirical": 0.20360658659639658
        },
        {
          "heldout_question": "d",
          "model": "openai:gpt-5.5",
          "rows": 100,
          "model_nll": 0.4741174072207259,
          "empirical_nll": 0.6641349785509554,
          "nll_vs_empirical": 0.1900175713302295
        },
        {
          "heldout_question": "d",
          "model": "google:gemini-2.5-pro",
          "rows": 100,
          "model_nll": 0.5035992076800679,
          "empirical_nll": 0.6641349785509554,
          "nll_vs_empirical": 0.16053577087088744
        },
        {
          "heldout_question": "c",
          "model": "openai:gpt-5.5",
          "rows": 100,
          "model_nll": 0.344112348331866,
          "empirical_nll": 0.4866010856846842,
          "nll_vs_empirical": 0.1424887373528182
        },
        {
          "heldout_question": "f",
          "model": "openai:gpt-5.5",
          "rows": 100,
          "model_nll": 0.3111200034487335,
          "empirical_nll": 0.40596321448727585,
          "nll_vs_empirical": 0.09484321103854237
        },
        {
          "heldout_question": "e",
          "model": "openai:gpt-5.5",
          "rows": 100,
          "model_nll": 0.3329985136055744,
          "empirical_nll": 0.3874372890098022,
          "nll_vs_empirical": 0.05443877540422776
        },
        {
          "heldout_question": "b",
          "model": "google:gemini-2.5-pro",
          "rows": 100,
          "model_nll": 0.5651344652552747,
          "empirical_nll": 0.6109819064345472,
          "nll_vs_empirical": 0.04584744117927253
        }
      ],
      "group_average_wins": [
        {
          "heldout_question": "a",
          "model": "google:gemini-2.5-pro",
          "rows": 100,
          "model_nll": 0.44088479035823996,
          "empirical_nll": 0.30354115183280916,
          "nll_vs_empirical": -0.1373436385254308
        },
        {
          "heldout_question": "c",
          "model": "google:gemini-2.5-pro",
          "rows": 100,
          "model_nll": 0.5540064193246979,
          "empirical_nll": 0.4866010856846842,
          "nll_vs_empirical": -0.06740533364001366
        },
        {
          "heldout_question": "e",
          "model": "google:gemini-2.5-pro",
          "rows": 100,
          "model_nll": 0.44031131565629755,
          "empirical_nll": 0.3874372890098022,
          "nll_vs_empirical": -0.052874026646495376
        },
        {
          "heldout_question": "a",
          "model": "openai:gpt-5.5",
          "rows": 100,
          "model_nll": 0.34314864231164166,
          "empirical_nll": 0.30354115183280916,
          "nll_vs_empirical": -0.0396074904788325
        },
        {
          "heldout_question": "f",
          "model": "google:gemini-2.5-pro",
          "rows": 100,
          "model_nll": 0.41564379674265906,
          "empirical_nll": 0.40596321448727585,
          "nll_vs_empirical": -0.009680582255383208
        }
      ],
      "overconfident_misses": [
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202001001583",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202400004271",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202201047943",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_201501207520",
          "heldout_question": "f",
          "heldout_question_text": "Do you favor or oppose below proposals to reduce effects of global climate change? Providing a tax credit to Americans who improve their home energy efficiency, such as by installing heat pumps or adding insulation",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202400013226",
          "heldout_question": "f",
          "heldout_question_text": "Do you favor or oppose below proposals to reduce effects of global climate change? Providing a tax credit to Americans who improve their home energy efficiency, such as by installing heat pumps or adding insulation",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202201035271",
          "heldout_question": "e",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Requiring oil and gas companies to seal methane gas leaks from oil wells",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.03,
          "negative_log_likelihood": 3.506557897319982,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202201016860",
          "heldout_question": "c",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate Providing a tax credit to encourage businesses to develop technology which captures and stores carbon emissions so they do not enter the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.04,
          "negative_log_likelihood": 3.2188758248682006,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202201047943",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202001001583",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202400004271",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        }
      ],
      "worst_misses": [
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202001001583",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202400004271",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202201047943",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_201501207520",
          "heldout_question": "f",
          "heldout_question_text": "Do you favor or oppose below proposals to reduce effects of global climate change? Providing a tax credit to Americans who improve their home energy efficiency, such as by installing heat pumps or adding insulation",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202400013226",
          "heldout_question": "f",
          "heldout_question_text": "Do you favor or oppose below proposals to reduce effects of global climate change? Providing a tax credit to Americans who improve their home energy efficiency, such as by installing heat pumps or adding insulation",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202201035271",
          "heldout_question": "e",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Requiring oil and gas companies to seal methane gas leaks from oil wells",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.03,
          "negative_log_likelihood": 3.506557897319982,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202201016860",
          "heldout_question": "c",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate Providing a tax credit to encourage businesses to develop technology which captures and stores carbon emissions so they do not enter the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.04,
          "negative_log_likelihood": 3.2188758248682006,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202201047943",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202001001583",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w158_ccpolicy",
          "job_id": "6d43ea20483a39c8",
          "respondent_id": "w158_ccpolicy_202400004271",
          "heldout_question": "a",
          "heldout_question_text": "Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere",
          "actual_answer": "Oppose",
          "predicted_option": "Favor",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        }
      ],
      "confusion": {
        "a::openai:gpt-5.5": {
          "Favor": {
            "Favor": 89,
            "Oppose": 2
          },
          "Oppose": {
            "Favor": 9
          }
        },
        "a::google:gemini-2.5-pro": {
          "Favor": {
            "Favor": 79,
            "Oppose": 12
          },
          "Oppose": {
            "Oppose": 1,
            "Favor": 8
          }
        },
        "b::openai:gpt-5.5": {
          "Favor": {
            "Favor": 58,
            "Oppose": 12
          },
          "Oppose": {
            "Oppose": 21,
            "Favor": 9
          }
        },
        "b::google:gemini-2.5-pro": {
          "Favor": {
            "Favor": 49,
            "Oppose": 21
          },
          "Oppose": {
            "Oppose": 27,
            "Favor": 3
          }
        },
        "c::openai:gpt-5.5": {
          "Favor": {
            "Favor": 76,
            "Oppose": 5
          },
          "Oppose": {
            "Favor": 10,
            "Oppose": 9
          }
        },
        "c::google:gemini-2.5-pro": {
          "Favor": {
            "Favor": 59,
            "Oppose": 22
          },
          "Oppose": {
            "Oppose": 15,
            "Favor": 4
          }
        },
        "d::openai:gpt-5.5": {
          "Oppose": {
            "Favor": 19,
            "Oppose": 19
          },
          "Favor": {
            "Favor": 60,
            "Oppose": 2
          }
        },
        "d::google:gemini-2.5-pro": {
          "Oppose": {
            "Oppose": 27,
            "Favor": 11
          },
          "Favor": {
            "Oppose": 9,
            "Favor": 53
          }
        },
        "e::openai:gpt-5.5": {
          "Favor": {
            "Favor": 75,
            "Oppose": 12
          },
          "Oppose": {
            "Favor": 5,
            "Oppose": 8
          }
        },
        "e::google:gemini-2.5-pro": {
          "Favor": {
            "Favor": 69,
            "Oppose": 18
          },
          "Oppose": {
            "Favor": 2,
            "Oppose": 11
          }
        },
        "f::openai:gpt-5.5": {
          "Favor": {
            "Favor": 85,
            "Oppose": 1
          },
          "Oppose": {
            "Favor": 11,
            "Oppose": 3
          }
        },
        "f::google:gemini-2.5-pro": {
          "Favor": {
            "Favor": 74,
            "Oppose": 12
          },
          "Oppose": {
            "Oppose": 9,
            "Favor": 5
          }
        }
      }
    },
    {
      "survey": "w157_skillimp",
      "survey_summary": {
        "name": "w157_skillimp",
        "status": "draft",
        "raw_files": 2,
        "questions": 9,
        "respondents": 5333,
        "answers": 47997,
        "has_context": true,
        "open_quarantine_issues": 0,
        "committed": true
      },
      "survey_context": "A nationally representative Pew Research Center American Trends Panel survey of U.S. adults was fielded in October 2024.",
      "raw_files": [
        {
          "id": "w157_skillimp_metadata",
          "kind": "metadata",
          "title": "w157_skillimp normalized metadata",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/W157_SKILLIMP_metadata.json",
          "source_hash": "sha256:45f0339aeaca8807c54c8edbdc1eb4925eb1106809de4966833ec5f71348ddfc",
          "stored_path": ".zwill/surveys/w157_skillimp/raw/w157_skillimp_metadata/W157_SKILLIMP_metadata.json",
          "stored_hash": "sha256:45f0339aeaca8807c54c8edbdc1eb4925eb1106809de4966833ec5f71348ddfc",
          "added_at": "2026-06-27T16:24:22Z"
        },
        {
          "id": "w157_skillimp_respondents",
          "kind": "respondent_data",
          "title": "w157_skillimp normalized respondents",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/W157_SKILLIMP_respondents.csv",
          "source_hash": "sha256:1d9a2ac083f31c57bb28a814303b83151f228b6b27bca0cfebab049d79a74aec",
          "stored_path": ".zwill/surveys/w157_skillimp/raw/w157_skillimp_respondents/W157_SKILLIMP_respondents.csv",
          "stored_hash": "sha256:1d9a2ac083f31c57bb28a814303b83151f228b6b27bca0cfebab049d79a74aec",
          "added_at": "2026-06-27T16:24:23Z"
        }
      ],
      "job_id": "a43f33531b85174d",
      "study_config": {
        "survey": "w157_skillimp",
        "heldout_question": "a",
        "job_id": "a43f33531b85174d"
      },
      "run_manifest": {
        "job_id": "a43f33531b85174d",
        "survey": "w157_skillimp",
        "status": "ok",
        "created_at": "2026-06-27T21:13:22Z",
        "job_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w157_skillimp_twin_a43f33531b85174d.edsl.json",
        "results_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w157_skillimp_twin_a43f33531b85174d_results.json.gz",
        "report_paths": {
          "html": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w157_skillimp_twin_a43f33531b85174d_report.html"
        },
        "heldout_questions": [
          "a"
        ],
        "context_question_count": 5,
        "sample_respondents": 20,
        "seed": 789,
        "complete_cases": true,
        "balance_actual": false,
        "stratify_actual": true,
        "scenario_count": 20,
        "result_count": 40,
        "extracted_count": 40,
        "issue_count": 0,
        "model_count": 2,
        "models": [
          "openai:gpt-5.5",
          "google:gemini-2.5-pro"
        ]
      },
      "import_metadata": {
        "job_id": "a43f33531b85174d",
        "survey": "w157_skillimp",
        "source_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w157_skillimp_twin_a43f33531b85174d_results.json.gz",
        "source_hash": "sha256:e605dc9783fb0bcd2eec751079993956ad9382fa37da4e364f08de2a22e87b89",
        "stored_path": ".zwill/surveys/w157_skillimp/digital_twin_jobs/a43f33531b85174d/raw/w157_skillimp_twin_a43f33531b85174d_results.json.gz",
        "stored_hash": "sha256:e605dc9783fb0bcd2eec751079993956ad9382fa37da4e364f08de2a22e87b89",
        "row_count": 40,
        "extracted_count": 40,
        "issue_count": 0,
        "issues": [],
        "imported_at": "2026-06-27T21:13:22Z"
      },
      "heldout_questions": [
        {
          "question_name": "a",
          "question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "question_options": [
            "Extremely important",
            "Very important",
            "Somewhat important",
            "Not too important",
            "Not at all important"
          ]
        }
      ],
      "summary_by_model": {
        "openai:gpt-5.5": {
          "rows": 20,
          "mean_probability_actual": 0.47800000000000004,
          "mean_uniform_probability_actual": 0.2,
          "mean_negative_log_likelihood": 1.0059213591754808,
          "negative_log_likelihood_p50": 0.6555934443165425,
          "negative_log_likelihood_p90": 1.445242335587,
          "negative_log_likelihood_p95": 3.0762041691756967,
          "negative_log_likelihood_max": 4.605170185988091,
          "mean_top_confidence": 0.604,
          "mean_uniform_negative_log_likelihood": 1.6094379124341003,
          "mean_brier": 0.5218379750000001,
          "mean_uniform_brier": 0.8000000000000002,
          "mean_brier_improvement": 0.27816202500000026,
          "top1_accuracy": 0.65,
          "mean_empirical_marginal_probability_actual": 0.35714480478500144,
          "mean_empirical_marginal_negative_log_likelihood": 1.316064457399848,
          "mean_empirical_marginal_brier": 0.66392664484399,
          "empirical_marginal_top1_accuracy": 0.45,
          "mean_marginal_probability_actual": 0.35714480478500144,
          "mean_marginal_negative_log_likelihood": 1.316064457399848,
          "mean_marginal_brier": 0.66392664484399,
          "marginal_top1_accuracy": 0.45,
          "expected_calibration_error": 0.12400000000000004
        },
        "google:gemini-2.5-pro": {
          "rows": 20,
          "mean_probability_actual": 0.6094999999999999,
          "mean_uniform_probability_actual": 0.2,
          "mean_negative_log_likelihood": 3.2517623935467457,
          "negative_log_likelihood_p50": 0.18632957819149348,
          "negative_log_likelihood_p90": 4.470510097990184,
          "negative_log_likelihood_p95": 27.631021115928547,
          "negative_log_likelihood_max": 27.631021115928547,
          "mean_top_confidence": 0.8185,
          "mean_uniform_negative_log_likelihood": 1.6094379124341003,
          "mean_brier": 0.48945,
          "mean_uniform_brier": 0.8000000000000002,
          "mean_brier_improvement": 0.3105500000000002,
          "top1_accuracy": 0.7,
          "mean_empirical_marginal_probability_actual": 0.35714480478500144,
          "mean_empirical_marginal_negative_log_likelihood": 1.316064457399848,
          "mean_empirical_marginal_brier": 0.66392664484399,
          "empirical_marginal_top1_accuracy": 0.45,
          "mean_marginal_probability_actual": 0.35714480478500144,
          "mean_marginal_negative_log_likelihood": 1.316064457399848,
          "mean_marginal_brier": 0.66392664484399,
          "marginal_top1_accuracy": 0.45,
          "expected_calibration_error": 0.18050000000000005
        }
      },
      "summary_by_question": {
        "a": {
          "openai:gpt-5.5": {
            "rows": 20,
            "mean_probability_actual": 0.47800000000000004,
            "mean_uniform_probability_actual": 0.2,
            "mean_negative_log_likelihood": 1.0059213591754808,
            "negative_log_likelihood_p50": 0.6555934443165425,
            "negative_log_likelihood_p90": 1.445242335587,
            "negative_log_likelihood_p95": 3.0762041691756967,
            "negative_log_likelihood_max": 4.605170185988091,
            "mean_top_confidence": 0.604,
            "mean_uniform_negative_log_likelihood": 1.6094379124341003,
            "mean_brier": 0.5218379750000001,
            "mean_uniform_brier": 0.8000000000000002,
            "mean_brier_improvement": 0.27816202500000026,
            "top1_accuracy": 0.65,
            "mean_empirical_marginal_probability_actual": 0.35714480478500144,
            "mean_empirical_marginal_negative_log_likelihood": 1.316064457399848,
            "mean_empirical_marginal_brier": 0.66392664484399,
            "empirical_marginal_top1_accuracy": 0.45,
            "mean_marginal_probability_actual": 0.35714480478500144,
            "mean_marginal_negative_log_likelihood": 1.316064457399848,
            "mean_marginal_brier": 0.66392664484399,
            "marginal_top1_accuracy": 0.45
          },
          "google:gemini-2.5-pro": {
            "rows": 20,
            "mean_probability_actual": 0.6094999999999999,
            "mean_uniform_probability_actual": 0.2,
            "mean_negative_log_likelihood": 3.2517623935467457,
            "negative_log_likelihood_p50": 0.18632957819149348,
            "negative_log_likelihood_p90": 4.470510097990184,
            "negative_log_likelihood_p95": 27.631021115928547,
            "negative_log_likelihood_max": 27.631021115928547,
            "mean_top_confidence": 0.8185,
            "mean_uniform_negative_log_likelihood": 1.6094379124341003,
            "mean_brier": 0.48945,
            "mean_uniform_brier": 0.8000000000000002,
            "mean_brier_improvement": 0.3105500000000002,
            "top1_accuracy": 0.7,
            "mean_empirical_marginal_probability_actual": 0.35714480478500144,
            "mean_empirical_marginal_negative_log_likelihood": 1.316064457399848,
            "mean_empirical_marginal_brier": 0.66392664484399,
            "empirical_marginal_top1_accuracy": 0.45,
            "mean_marginal_probability_actual": 0.35714480478500144,
            "mean_marginal_negative_log_likelihood": 1.316064457399848,
            "mean_marginal_brier": 0.66392664484399,
            "marginal_top1_accuracy": 0.45
          }
        }
      },
      "baseline_comparison": {
        "openai:gpt-5.5": {
          "p_actual_vs_uniform": 0.278,
          "nll_vs_uniform": 0.6035165532586195,
          "brier_vs_uniform": 0.2781620250000001,
          "p_actual_vs_empirical": 0.1208551952149986,
          "nll_vs_empirical": 0.31014309822436714,
          "brier_vs_empirical": 0.14208866984398993
        },
        "google:gemini-2.5-pro": {
          "p_actual_vs_uniform": 0.4094999999999999,
          "nll_vs_uniform": -1.6423244811126454,
          "brier_vs_uniform": 0.31055000000000016,
          "p_actual_vs_empirical": 0.2523551952149985,
          "nll_vs_empirical": -1.9356979361468978,
          "brier_vs_empirical": 0.17447664484399
        }
      },
      "model_wins_over_group_average": [
        {
          "heldout_question": "a",
          "model": "openai:gpt-5.5",
          "rows": 20,
          "model_nll": 1.0059213591754808,
          "empirical_nll": 1.316064457399848,
          "nll_vs_empirical": 0.31014309822436714
        }
      ],
      "group_average_wins": [
        {
          "heldout_question": "a",
          "model": "google:gemini-2.5-pro",
          "rows": 20,
          "model_nll": 3.2517623935467457,
          "empirical_nll": 1.316064457399848,
          "nll_vs_empirical": -1.9356979361468978
        }
      ],
      "overconfident_misses": [
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703106",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Very important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.15,
          "negative_log_likelihood": 1.8971199848858813,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703033",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Not too important",
          "predicted_option": "Very important",
          "probability_actual": 0.0,
          "negative_log_likelihood": 27.631021115928547,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703115",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Somewhat important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.0,
          "negative_log_likelihood": 27.631021115928547,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15702029",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Very important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.17,
          "negative_log_likelihood": 1.7719568419318752,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15700223",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Extremely important",
          "predicted_option": "Very important",
          "probability_actual": 0.17,
          "negative_log_likelihood": 1.7719568419318752,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703033",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Not too important",
          "predicted_option": "Very important",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15705233",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Not at all important",
          "predicted_option": "Not too important",
          "probability_actual": 0.25,
          "negative_log_likelihood": 1.3862943611198906,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703115",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Somewhat important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703576",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Very important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.28,
          "negative_log_likelihood": 1.2729656758128873,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15705233",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Not at all important",
          "predicted_option": "Not too important",
          "probability_actual": 0.28,
          "negative_log_likelihood": 1.2729656758128873,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        }
      ],
      "worst_misses": [
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703033",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Not too important",
          "predicted_option": "Very important",
          "probability_actual": 0.0,
          "negative_log_likelihood": 27.631021115928547,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703115",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Somewhat important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.0,
          "negative_log_likelihood": 27.631021115928547,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703033",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Not too important",
          "predicted_option": "Very important",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703115",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Somewhat important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703106",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Very important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.15,
          "negative_log_likelihood": 1.8971199848858813,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15702029",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Very important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.17,
          "negative_log_likelihood": 1.7719568419318752,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15700223",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Extremely important",
          "predicted_option": "Very important",
          "probability_actual": 0.17,
          "negative_log_likelihood": 1.7719568419318752,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15705233",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Not at all important",
          "predicted_option": "Not too important",
          "probability_actual": 0.25,
          "negative_log_likelihood": 1.3862943611198906,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15703576",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Very important",
          "predicted_option": "Extremely important",
          "probability_actual": 0.28,
          "negative_log_likelihood": 1.2729656758128873,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w157_skillimp",
          "job_id": "a43f33531b85174d",
          "respondent_id": "w157_skillimp_15705233",
          "heldout_question": "a",
          "heldout_question_text": "Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts",
          "actual_answer": "Not at all important",
          "predicted_option": "Not too important",
          "probability_actual": 0.28,
          "negative_log_likelihood": 1.2729656758128873,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        }
      ],
      "confusion": {
        "a::openai:gpt-5.5": {
          "Very important": {
            "Extremely important": 3,
            "Very important": 4
          },
          "Extremely important": {
            "Extremely important": 8,
            "Very important": 1
          },
          "Somewhat important": {
            "Somewhat important": 1,
            "Extremely important": 1
          },
          "Not too important": {
            "Very important": 1
          },
          "Not at all important": {
            "Not too important": 1
          }
        },
        "a::google:gemini-2.5-pro": {
          "Very important": {
            "Extremely important": 2,
            "Very important": 5
          },
          "Extremely important": {
            "Extremely important": 8,
            "Very important": 1
          },
          "Somewhat important": {
            "Somewhat important": 1,
            "Extremely important": 1
          },
          "Not too important": {
            "Very important": 1
          },
          "Not at all important": {
            "Not too important": 1
          }
        }
      }
    },
    {
      "survey": "w152_humanvai",
      "survey_summary": {
        "name": "w152_humanvai",
        "status": "draft",
        "raw_files": 2,
        "questions": 8,
        "respondents": 5318,
        "answers": 42544,
        "has_context": true,
        "open_quarantine_issues": 0,
        "committed": true
      },
      "survey_context": "Pew Research Center American Trends Panel W152",
      "raw_files": [
        {
          "id": "w152_humanvai_metadata",
          "kind": "metadata",
          "title": "w152_humanvai normalized metadata",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/W152_HUMANVAI_metadata.json",
          "source_hash": "sha256:c545329981f85679fdc5e26703129b7fa003e74fa7f37b135183372146b3c950",
          "stored_path": ".zwill/surveys/w152_humanvai/raw/w152_humanvai_metadata/W152_HUMANVAI_metadata.json",
          "stored_hash": "sha256:c545329981f85679fdc5e26703129b7fa003e74fa7f37b135183372146b3c950",
          "added_at": "2026-06-27T16:24:21Z"
        },
        {
          "id": "w152_humanvai_respondents",
          "kind": "respondent_data",
          "title": "w152_humanvai normalized respondents",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/W152_HUMANVAI_respondents.csv",
          "source_hash": "sha256:a77038bf95cf68bce9266c6f589d70635695f8fe57c1f30faab50b75aa617754",
          "stored_path": ".zwill/surveys/w152_humanvai/raw/w152_humanvai_respondents/W152_HUMANVAI_respondents.csv",
          "stored_hash": "sha256:a77038bf95cf68bce9266c6f589d70635695f8fe57c1f30faab50b75aa617754",
          "added_at": "2026-06-27T16:24:21Z"
        }
      ],
      "job_id": "22bd551fe8d4f2d9",
      "study_config": {
        "survey": "w152_humanvai",
        "heldout_question": "humanvai_a_w152",
        "job_id": "22bd551fe8d4f2d9"
      },
      "run_manifest": {
        "job_id": "22bd551fe8d4f2d9",
        "survey": "w152_humanvai",
        "status": "ok",
        "created_at": "2026-06-27T23:49:34Z",
        "job_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w152_humanvai_twin_22bd551fe8d4f2d9.edsl.json",
        "results_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w152_humanvai_twin_22bd551fe8d4f2d9_results.json.gz",
        "report_paths": {
          "html": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w152_humanvai_twin_22bd551fe8d4f2d9_report.html"
        },
        "heldout_questions": [
          "humanvai_a_w152"
        ],
        "context_question_count": 5,
        "sample_respondents": 20,
        "seed": 789,
        "complete_cases": true,
        "balance_actual": false,
        "stratify_actual": true,
        "scenario_count": 20,
        "result_count": 40,
        "extracted_count": 40,
        "issue_count": 0,
        "model_count": 2,
        "models": [
          "openai:gpt-5.5",
          "google:gemini-2.5-pro"
        ]
      },
      "import_metadata": {
        "job_id": "22bd551fe8d4f2d9",
        "survey": "w152_humanvai",
        "source_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w152_humanvai_twin_22bd551fe8d4f2d9_results.json.gz",
        "source_hash": "sha256:14f254290eb72bfd69a80a8a5cae4715d66ca33860af2fbbfc7ad0a0eab78e09",
        "stored_path": ".zwill/surveys/w152_humanvai/digital_twin_jobs/22bd551fe8d4f2d9/raw/w152_humanvai_twin_22bd551fe8d4f2d9_results.json.gz",
        "stored_hash": "sha256:14f254290eb72bfd69a80a8a5cae4715d66ca33860af2fbbfc7ad0a0eab78e09",
        "row_count": 40,
        "extracted_count": 40,
        "issue_count": 0,
        "issues": [],
        "imported_at": "2026-06-27T23:49:34Z"
      },
      "heldout_questions": [
        {
          "question_name": "humanvai_a_w152",
          "question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "question_options": [
            "AI would do this better",
            "AI would do this worse",
            "AI would do this about the same",
            "Not sure"
          ]
        }
      ],
      "summary_by_model": {
        "openai:gpt-5.5": {
          "rows": 20,
          "mean_probability_actual": 0.4665,
          "mean_uniform_probability_actual": 0.25,
          "mean_negative_log_likelihood": 0.9230477203991494,
          "negative_log_likelihood_p50": 0.8112615759913779,
          "negative_log_likelihood_p90": 1.7147984280919266,
          "negative_log_likelihood_p95": 1.7394222523468168,
          "negative_log_likelihood_max": 2.2072749131897207,
          "mean_top_confidence": 0.5700000000000001,
          "mean_uniform_negative_log_likelihood": 1.3862943611198906,
          "mean_brier": 0.51578,
          "mean_uniform_brier": 0.75,
          "mean_brier_improvement": 0.23422,
          "top1_accuracy": 0.6,
          "mean_empirical_marginal_probability_actual": 0.2646581995186982,
          "mean_empirical_marginal_negative_log_likelihood": 1.3572931376064372,
          "mean_empirical_marginal_brier": 0.7371857906584334,
          "empirical_marginal_top1_accuracy": 0.3,
          "mean_marginal_probability_actual": 0.2646581995186982,
          "mean_marginal_negative_log_likelihood": 1.3572931376064372,
          "mean_marginal_brier": 0.7371857906584334,
          "marginal_top1_accuracy": 0.3,
          "expected_calibration_error": 0.138
        },
        "google:gemini-2.5-pro": {
          "rows": 20,
          "mean_probability_actual": 0.47400000000000003,
          "mean_uniform_probability_actual": 0.25,
          "mean_negative_log_likelihood": 1.2219400060703087,
          "negative_log_likelihood_p50": 0.9859217655409089,
          "negative_log_likelihood_p90": 2.3718998110500413,
          "negative_log_likelihood_p95": 3.041546810147699,
          "negative_log_likelihood_max": 3.912023005428146,
          "mean_top_confidence": 0.7785,
          "mean_uniform_negative_log_likelihood": 1.3862943611198906,
          "mean_brier": 0.71201,
          "mean_uniform_brier": 0.75,
          "mean_brier_improvement": 0.037990000000000045,
          "top1_accuracy": 0.5,
          "mean_empirical_marginal_probability_actual": 0.2646581995186982,
          "mean_empirical_marginal_negative_log_likelihood": 1.3572931376064372,
          "mean_empirical_marginal_brier": 0.7371857906584334,
          "empirical_marginal_top1_accuracy": 0.3,
          "mean_marginal_probability_actual": 0.2646581995186982,
          "mean_marginal_negative_log_likelihood": 1.3572931376064372,
          "mean_marginal_brier": 0.7371857906584334,
          "marginal_top1_accuracy": 0.3,
          "expected_calibration_error": 0.33649999999999997
        }
      },
      "summary_by_question": {
        "humanvai_a_w152": {
          "openai:gpt-5.5": {
            "rows": 20,
            "mean_probability_actual": 0.4665,
            "mean_uniform_probability_actual": 0.25,
            "mean_negative_log_likelihood": 0.9230477203991494,
            "negative_log_likelihood_p50": 0.8112615759913779,
            "negative_log_likelihood_p90": 1.7147984280919266,
            "negative_log_likelihood_p95": 1.7394222523468168,
            "negative_log_likelihood_max": 2.2072749131897207,
            "mean_top_confidence": 0.5700000000000001,
            "mean_uniform_negative_log_likelihood": 1.3862943611198906,
            "mean_brier": 0.51578,
            "mean_uniform_brier": 0.75,
            "mean_brier_improvement": 0.23422,
            "top1_accuracy": 0.6,
            "mean_empirical_marginal_probability_actual": 0.2646581995186982,
            "mean_empirical_marginal_negative_log_likelihood": 1.3572931376064372,
            "mean_empirical_marginal_brier": 0.7371857906584334,
            "empirical_marginal_top1_accuracy": 0.3,
            "mean_marginal_probability_actual": 0.2646581995186982,
            "mean_marginal_negative_log_likelihood": 1.3572931376064372,
            "mean_marginal_brier": 0.7371857906584334,
            "marginal_top1_accuracy": 0.3
          },
          "google:gemini-2.5-pro": {
            "rows": 20,
            "mean_probability_actual": 0.47400000000000003,
            "mean_uniform_probability_actual": 0.25,
            "mean_negative_log_likelihood": 1.2219400060703087,
            "negative_log_likelihood_p50": 0.9859217655409089,
            "negative_log_likelihood_p90": 2.3718998110500413,
            "negative_log_likelihood_p95": 3.041546810147699,
            "negative_log_likelihood_max": 3.912023005428146,
            "mean_top_confidence": 0.7785,
            "mean_uniform_negative_log_likelihood": 1.3862943611198906,
            "mean_brier": 0.71201,
            "mean_uniform_brier": 0.75,
            "mean_brier_improvement": 0.037990000000000045,
            "top1_accuracy": 0.5,
            "mean_empirical_marginal_probability_actual": 0.2646581995186982,
            "mean_empirical_marginal_negative_log_likelihood": 1.3572931376064372,
            "mean_empirical_marginal_brier": 0.7371857906584334,
            "empirical_marginal_top1_accuracy": 0.3,
            "mean_marginal_probability_actual": 0.2646581995186982,
            "mean_marginal_negative_log_likelihood": 1.3572931376064372,
            "mean_marginal_brier": 0.7371857906584334,
            "marginal_top1_accuracy": 0.3
          }
        }
      },
      "baseline_comparison": {
        "openai:gpt-5.5": {
          "p_actual_vs_uniform": 0.21650000000000003,
          "nll_vs_uniform": 0.4632466407207412,
          "brier_vs_uniform": 0.23421999999999998,
          "p_actual_vs_empirical": 0.20184180048130185,
          "nll_vs_empirical": 0.4342454172072878,
          "brier_vs_empirical": 0.22140579065843335
        },
        "google:gemini-2.5-pro": {
          "p_actual_vs_uniform": 0.22400000000000003,
          "nll_vs_uniform": 0.16435435504958185,
          "brier_vs_uniform": 0.03798999999999997,
          "p_actual_vs_empirical": 0.20934180048130185,
          "nll_vs_empirical": 0.1353531315361285,
          "brier_vs_empirical": 0.02517579065843334
        }
      },
      "model_wins_over_group_average": [
        {
          "heldout_question": "humanvai_a_w152",
          "model": "openai:gpt-5.5",
          "rows": 20,
          "model_nll": 0.9230477203991494,
          "empirical_nll": 1.3572931376064372,
          "nll_vs_empirical": 0.4342454172072878
        },
        {
          "heldout_question": "humanvai_a_w152",
          "model": "google:gemini-2.5-pro",
          "rows": 20,
          "model_nll": 1.2219400060703087,
          "empirical_nll": 1.3572931376064372,
          "nll_vs_empirical": 0.1353531315361285
        }
      ],
      "group_average_wins": [],
      "overconfident_misses": [
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_674255",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this about the same",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202201024401",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "Not sure",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.1,
          "negative_log_likelihood": 2.3025850929940455,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202400022571",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this worse",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.1,
          "negative_log_likelihood": 2.3025850929940455,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202400021777",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this better",
          "predicted_option": "AI would do this worse",
          "probability_actual": 0.17,
          "negative_log_likelihood": 1.7719568419318752,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202201043598",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this better",
          "predicted_option": "Not sure",
          "probability_actual": 0.15,
          "negative_log_likelihood": 1.8971199848858813,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_674255",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this about the same",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.11,
          "negative_log_likelihood": 2.2072749131897207,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_201801086555",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this better",
          "predicted_option": "AI would do this worse",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_201901026691",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this about the same",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.22,
          "negative_log_likelihood": 1.5141277326297755,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_201801060845",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "Not sure",
          "predicted_option": "AI would do this worse",
          "probability_actual": 0.24,
          "negative_log_likelihood": 1.4271163556401458,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202001015632",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this worse",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.1,
          "negative_log_likelihood": 2.3025850929940455,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        }
      ],
      "worst_misses": [
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_674255",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this about the same",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_201801086555",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this better",
          "predicted_option": "AI would do this worse",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202001015632",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this worse",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.1,
          "negative_log_likelihood": 2.3025850929940455,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202201024401",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "Not sure",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.1,
          "negative_log_likelihood": 2.3025850929940455,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202400022571",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this worse",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.1,
          "negative_log_likelihood": 2.3025850929940455,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_674255",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this about the same",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.11,
          "negative_log_likelihood": 2.2072749131897207,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_201801142289",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this worse",
          "predicted_option": "Not sure",
          "probability_actual": 0.15,
          "negative_log_likelihood": 1.8971199848858813,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202201043598",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this better",
          "predicted_option": "Not sure",
          "probability_actual": 0.15,
          "negative_log_likelihood": 1.8971199848858813,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202400021777",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "AI would do this better",
          "predicted_option": "AI would do this worse",
          "probability_actual": 0.17,
          "negative_log_likelihood": 1.7719568419318752,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w152_humanvai",
          "job_id": "22bd551fe8d4f2d9",
          "respondent_id": "w152_humanvai_202201024401",
          "heldout_question": "humanvai_a_w152",
          "heldout_question_text": "Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis",
          "actual_answer": "Not sure",
          "predicted_option": "AI would do this better",
          "probability_actual": 0.18,
          "negative_log_likelihood": 1.7147984280919266,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        }
      ],
      "confusion": {
        "humanvai_a_w152::openai:gpt-5.5": {
          "AI would do this worse": {
            "AI would do this worse": 4,
            "AI would do this better": 2
          },
          "Not sure": {
            "AI would do this about the same": 1,
            "Not sure": 2,
            "AI would do this better": 1,
            "AI would do this worse": 1
          },
          "AI would do this better": {
            "AI would do this better": 4,
            "Not sure": 1,
            "AI would do this about the same": 1
          },
          "AI would do this about the same": {
            "AI would do this about the same": 2,
            "AI would do this better": 1
          }
        },
        "humanvai_a_w152::google:gemini-2.5-pro": {
          "AI would do this worse": {
            "AI would do this worse": 3,
            "AI would do this better": 2,
            "Not sure": 1
          },
          "Not sure": {
            "Not sure": 3,
            "AI would do this better": 1,
            "AI would do this worse": 1
          },
          "AI would do this better": {
            "AI would do this better": 3,
            "AI would do this worse": 2,
            "Not sure": 1
          },
          "AI would do this about the same": {
            "AI would do this about the same": 1,
            "AI would do this better": 2
          }
        }
      }
    },
    {
      "survey": "w163_sm9",
      "survey_summary": {
        "name": "w163_sm9",
        "status": "draft",
        "raw_files": 2,
        "questions": 5,
        "respondents": 5020,
        "answers": 25100,
        "has_context": true,
        "open_quarantine_issues": 0,
        "committed": true
      },
      "survey_context": "A nationally representative Pew Research Center American Trends Panel survey of U.S. adults was fielded in February 2025.",
      "raw_files": [
        {
          "id": "w163_sm9_metadata",
          "kind": "metadata",
          "title": "w163_sm9 normalized metadata",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/W163_SM9_metadata.json",
          "source_hash": "sha256:0b70ead79e190ffe7867698848445afc6e5c9032e8943ea6bfde657cf0ef4bfe",
          "stored_path": ".zwill/surveys/w163_sm9/raw/w163_sm9_metadata/W163_SM9_metadata.json",
          "stored_hash": "sha256:0b70ead79e190ffe7867698848445afc6e5c9032e8943ea6bfde657cf0ef4bfe",
          "added_at": "2026-06-27T16:24:24Z"
        },
        {
          "id": "w163_sm9_respondents",
          "kind": "respondent_data",
          "title": "w163_sm9 normalized respondents",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/W163_SM9_respondents.csv",
          "source_hash": "sha256:085bcfe1b49ea75907d028fbe4d98cb3d19627fab6422e1709ee616da58ea9dc",
          "stored_path": ".zwill/surveys/w163_sm9/raw/w163_sm9_respondents/W163_SM9_respondents.csv",
          "stored_hash": "sha256:085bcfe1b49ea75907d028fbe4d98cb3d19627fab6422e1709ee616da58ea9dc",
          "added_at": "2026-06-27T16:24:24Z"
        }
      ],
      "job_id": "6dd602a142835c4f",
      "study_config": {
        "survey": "w163_sm9",
        "heldout_question": "a",
        "job_id": "6dd602a142835c4f"
      },
      "run_manifest": {
        "job_id": "6dd602a142835c4f",
        "survey": "w163_sm9",
        "status": "ok",
        "created_at": "2026-06-28T00:34:11Z",
        "job_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w163_sm9_twin_6dd602a142835c4f.edsl.json",
        "results_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w163_sm9_twin_6dd602a142835c4f_results.json.gz",
        "report_paths": {
          "html": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w163_sm9_twin_6dd602a142835c4f_report.html"
        },
        "heldout_questions": [
          "a"
        ],
        "context_question_count": 4,
        "sample_respondents": 20,
        "seed": 789,
        "complete_cases": true,
        "balance_actual": false,
        "stratify_actual": true,
        "scenario_count": 20,
        "result_count": 40,
        "extracted_count": 40,
        "issue_count": 0,
        "model_count": 2,
        "models": [
          "openai:gpt-5.5",
          "google:gemini-2.5-pro"
        ]
      },
      "import_metadata": {
        "job_id": "6dd602a142835c4f",
        "survey": "w163_sm9",
        "source_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/w163_sm9_twin_6dd602a142835c4f_results.json.gz",
        "source_hash": "sha256:dec1a307d17310d63c29e0d2c0cfd88b143b3a287c3e7100389d3b525b03e386",
        "stored_path": ".zwill/surveys/w163_sm9/digital_twin_jobs/6dd602a142835c4f/raw/w163_sm9_twin_6dd602a142835c4f_results.json.gz",
        "stored_hash": "sha256:dec1a307d17310d63c29e0d2c0cfd88b143b3a287c3e7100389d3b525b03e386",
        "row_count": 40,
        "extracted_count": 40,
        "issue_count": 0,
        "issues": [],
        "imported_at": "2026-06-28T00:34:11Z"
      },
      "heldout_questions": [
        {
          "question_name": "a",
          "question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "question_options": [
            "Very well",
            "Somewhat well",
            "Not too well",
            "Not at all well"
          ]
        }
      ],
      "summary_by_model": {
        "openai:gpt-5.5": {
          "rows": 20,
          "mean_probability_actual": 0.441,
          "mean_uniform_probability_actual": 0.25,
          "mean_negative_log_likelihood": 0.9369107313488747,
          "negative_log_likelihood_p50": 0.6931471805599453,
          "negative_log_likelihood_p90": 1.5341948021759908,
          "negative_log_likelihood_p95": 1.7494557871199243,
          "negative_log_likelihood_max": 2.4079456086518722,
          "mean_top_confidence": 0.553,
          "mean_uniform_negative_log_likelihood": 1.3862943611198906,
          "mean_brier": 0.5316400000000001,
          "mean_uniform_brier": 0.75,
          "mean_brier_improvement": 0.21835999999999997,
          "top1_accuracy": 0.6,
          "mean_empirical_marginal_probability_actual": 0.3457441148368243,
          "mean_empirical_marginal_negative_log_likelihood": 1.2255455981566923,
          "mean_empirical_marginal_brier": 0.6617131622080156,
          "empirical_marginal_top1_accuracy": 0.5,
          "mean_marginal_probability_actual": 0.3457441148368243,
          "mean_marginal_negative_log_likelihood": 1.2255455981566923,
          "mean_marginal_brier": 0.6617131622080156,
          "marginal_top1_accuracy": 0.5,
          "expected_calibration_error": 0.13
        },
        "google:gemini-2.5-pro": {
          "rows": 20,
          "mean_probability_actual": 0.508,
          "mean_uniform_probability_actual": 0.25,
          "mean_negative_log_likelihood": 1.0316382600487557,
          "negative_log_likelihood_p50": 0.4772559723471764,
          "negative_log_likelihood_p90": 2.57272900723283,
          "negative_log_likelihood_p95": 2.995732273553991,
          "negative_log_likelihood_max": 2.995732273553991,
          "mean_top_confidence": 0.7545,
          "mean_uniform_negative_log_likelihood": 1.3862943611198906,
          "mean_brier": 0.6115299999999999,
          "mean_uniform_brier": 0.75,
          "mean_brier_improvement": 0.13847,
          "top1_accuracy": 0.55,
          "mean_empirical_marginal_probability_actual": 0.3457441148368243,
          "mean_empirical_marginal_negative_log_likelihood": 1.2255455981566923,
          "mean_empirical_marginal_brier": 0.6617131622080156,
          "empirical_marginal_top1_accuracy": 0.5,
          "mean_marginal_probability_actual": 0.3457441148368243,
          "mean_marginal_negative_log_likelihood": 1.2255455981566923,
          "mean_marginal_brier": 0.6617131622080156,
          "marginal_top1_accuracy": 0.5,
          "expected_calibration_error": 0.2395
        }
      },
      "summary_by_question": {
        "a": {
          "openai:gpt-5.5": {
            "rows": 20,
            "mean_probability_actual": 0.441,
            "mean_uniform_probability_actual": 0.25,
            "mean_negative_log_likelihood": 0.9369107313488747,
            "negative_log_likelihood_p50": 0.6931471805599453,
            "negative_log_likelihood_p90": 1.5341948021759908,
            "negative_log_likelihood_p95": 1.7494557871199243,
            "negative_log_likelihood_max": 2.4079456086518722,
            "mean_top_confidence": 0.553,
            "mean_uniform_negative_log_likelihood": 1.3862943611198906,
            "mean_brier": 0.5316400000000001,
            "mean_uniform_brier": 0.75,
            "mean_brier_improvement": 0.21835999999999997,
            "top1_accuracy": 0.6,
            "mean_empirical_marginal_probability_actual": 0.3457441148368243,
            "mean_empirical_marginal_negative_log_likelihood": 1.2255455981566923,
            "mean_empirical_marginal_brier": 0.6617131622080156,
            "empirical_marginal_top1_accuracy": 0.5,
            "mean_marginal_probability_actual": 0.3457441148368243,
            "mean_marginal_negative_log_likelihood": 1.2255455981566923,
            "mean_marginal_brier": 0.6617131622080156,
            "marginal_top1_accuracy": 0.5
          },
          "google:gemini-2.5-pro": {
            "rows": 20,
            "mean_probability_actual": 0.508,
            "mean_uniform_probability_actual": 0.25,
            "mean_negative_log_likelihood": 1.0316382600487557,
            "negative_log_likelihood_p50": 0.4772559723471764,
            "negative_log_likelihood_p90": 2.57272900723283,
            "negative_log_likelihood_p95": 2.995732273553991,
            "negative_log_likelihood_max": 2.995732273553991,
            "mean_top_confidence": 0.7545,
            "mean_uniform_negative_log_likelihood": 1.3862943611198906,
            "mean_brier": 0.6115299999999999,
            "mean_uniform_brier": 0.75,
            "mean_brier_improvement": 0.13847,
            "top1_accuracy": 0.55,
            "mean_empirical_marginal_probability_actual": 0.3457441148368243,
            "mean_empirical_marginal_negative_log_likelihood": 1.2255455981566923,
            "mean_empirical_marginal_brier": 0.6617131622080156,
            "empirical_marginal_top1_accuracy": 0.5,
            "mean_marginal_probability_actual": 0.3457441148368243,
            "mean_marginal_negative_log_likelihood": 1.2255455981566923,
            "mean_marginal_brier": 0.6617131622080156,
            "marginal_top1_accuracy": 0.5
          }
        }
      },
      "baseline_comparison": {
        "openai:gpt-5.5": {
          "p_actual_vs_uniform": 0.191,
          "nll_vs_uniform": 0.44938362977101587,
          "brier_vs_uniform": 0.2183599999999999,
          "p_actual_vs_empirical": 0.09525588516317568,
          "nll_vs_empirical": 0.2886348668078176,
          "brier_vs_empirical": 0.13007316220801546
        },
        "google:gemini-2.5-pro": {
          "p_actual_vs_uniform": 0.258,
          "nll_vs_uniform": 0.3546561010711349,
          "brier_vs_uniform": 0.1384700000000001,
          "p_actual_vs_empirical": 0.16225588516317568,
          "nll_vs_empirical": 0.1939073381079366,
          "brier_vs_empirical": 0.050183162208015664
        }
      },
      "model_wins_over_group_average": [
        {
          "heldout_question": "a",
          "model": "openai:gpt-5.5",
          "rows": 20,
          "model_nll": 0.9369107313488747,
          "empirical_nll": 1.2255455981566923,
          "nll_vs_empirical": 0.2886348668078176
        },
        {
          "heldout_question": "a",
          "model": "google:gemini-2.5-pro",
          "rows": 20,
          "model_nll": 1.0316382600487557,
          "empirical_nll": 1.2255455981566923,
          "nll_vs_empirical": 0.1939073381079366
        }
      ],
      "group_average_wins": [],
      "overconfident_misses": [
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400014211",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Very well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_201501160555",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Very well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.1,
          "negative_log_likelihood": 2.3025850929940455,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202201017548",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Somewhat well",
          "predicted_option": "Very well",
          "probability_actual": 0.15,
          "negative_log_likelihood": 1.8971199848858813,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202001020602",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Somewhat well",
          "predicted_option": "Very well",
          "probability_actual": 0.17,
          "negative_log_likelihood": 1.7719568419318752,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400014469",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Not too well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400006940",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Somewhat well",
          "predicted_option": "Not too well",
          "probability_actual": 0.08,
          "negative_log_likelihood": 2.5257286443082556,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202301000986",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Not too well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.3,
          "negative_log_likelihood": 1.2039728043259361,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202001020602",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Somewhat well",
          "predicted_option": "Very well",
          "probability_actual": 0.29,
          "negative_log_likelihood": 1.2378743560016174,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202101001885",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Not at all well",
          "predicted_option": "Not too well",
          "probability_actual": 0.35,
          "negative_log_likelihood": 1.0498221244986778,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_201501160555",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Very well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.18,
          "negative_log_likelihood": 1.7147984280919266,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        }
      ],
      "worst_misses": [
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400014469",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Not too well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400014211",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Very well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400006940",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Somewhat well",
          "predicted_option": "Not too well",
          "probability_actual": 0.08,
          "negative_log_likelihood": 2.5257286443082556,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400014469",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Not too well",
          "predicted_option": "Very well",
          "probability_actual": 0.09,
          "negative_log_likelihood": 2.4079456086518722,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_201501160555",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Very well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.1,
          "negative_log_likelihood": 2.3025850929940455,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202201017548",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Somewhat well",
          "predicted_option": "Very well",
          "probability_actual": 0.15,
          "negative_log_likelihood": 1.8971199848858813,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202001020602",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Somewhat well",
          "predicted_option": "Very well",
          "probability_actual": 0.17,
          "negative_log_likelihood": 1.7719568419318752,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_201501160555",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Very well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.18,
          "negative_log_likelihood": 1.7147984280919266,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400006940",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Somewhat well",
          "predicted_option": "Not too well",
          "probability_actual": 0.22,
          "negative_log_likelihood": 1.5141277326297755,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "w163_sm9",
          "job_id": "6dd602a142835c4f",
          "respondent_id": "w163_sm9_202400014211",
          "heldout_question": "a",
          "heldout_question_text": "How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups",
          "actual_answer": "Very well",
          "predicted_option": "Somewhat well",
          "probability_actual": 0.22,
          "negative_log_likelihood": 1.5141277326297755,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        }
      ],
      "confusion": {
        "a::openai:gpt-5.5": {
          "Not at all well": {
            "Not too well": 1,
            "Not at all well": 1
          },
          "Somewhat well": {
            "Somewhat well": 7,
            "Very well": 2,
            "Not too well": 1
          },
          "Not too well": {
            "Not too well": 2,
            "Somewhat well": 1,
            "Very well": 1
          },
          "Very well": {
            "Somewhat well": 2,
            "Very well": 2
          }
        },
        "a::google:gemini-2.5-pro": {
          "Not at all well": {
            "Not too well": 1,
            "Not at all well": 1
          },
          "Somewhat well": {
            "Somewhat well": 6,
            "Very well": 2,
            "Not too well": 2
          },
          "Not too well": {
            "Not too well": 2,
            "Somewhat well": 2
          },
          "Very well": {
            "Somewhat well": 2,
            "Very well": 2
          }
        }
      }
    },
    {
      "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
      "survey_summary": {
        "name": "dataverse_dnzt11_dnzt11_vignette_outcomes",
        "status": "draft",
        "raw_files": 2,
        "questions": 6,
        "respondents": 1199,
        "answers": 7194,
        "has_context": true,
        "open_quarantine_issues": 0,
        "committed": true
      },
      "survey_context": "U.S. online respondent survey experiment about local-government service failures and government downsizing. Respondents read two vignettes: one about trash/recycling not being picked up after Department of Public Works downsizing, and one about termination of vacuum leaf collection service in Clark County. Vignette arms varied whether no downsizing information, a chainsaw layoff approach, or a scalpel layoff approach was described, and whether a DPW employee or the DPW was identified as responsible. The downloaded replication package provides summarized condition labels, not full vignette prose",
      "raw_files": [
        {
          "id": "dataverse_dnzt11_dnzt11_vignette_outcomes_metadata",
          "kind": "metadata",
          "title": "dataverse_dnzt11_dnzt11_vignette_outcomes normalized metadata",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/DATAVERSE_DNZT11_DNZT11_VIGNETTE_OUTCOMES_metadata.json",
          "source_hash": "sha256:82f648c1943e6c1633a6048cdb98966bf5c71900483b29339608f10d7dfbeae1",
          "stored_path": ".zwill/surveys/dataverse_dnzt11_dnzt11_vignette_outcomes/raw/dataverse_dnzt11_dnzt11_vignette_outcomes_metadata/DATAVERSE_DNZT11_DNZT11_VIGNETTE_OUTCOMES_metadata.json",
          "stored_hash": "sha256:82f648c1943e6c1633a6048cdb98966bf5c71900483b29339608f10d7dfbeae1",
          "added_at": "2026-06-27T16:24:14Z"
        },
        {
          "id": "dataverse_dnzt11_dnzt11_vignette_outcomes_respondents",
          "kind": "respondent_data",
          "title": "dataverse_dnzt11_dnzt11_vignette_outcomes normalized respondents",
          "source_path": "/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized/DATAVERSE_DNZT11_DNZT11_VIGNETTE_OUTCOMES_respondents.csv",
          "source_hash": "sha256:ef87ce72d7e85399e7e47f16c0c6afd30be6aeea7dae7183ec1f38c90bd3fb48",
          "stored_path": ".zwill/surveys/dataverse_dnzt11_dnzt11_vignette_outcomes/raw/dataverse_dnzt11_dnzt11_vignette_outcomes_respondents/DATAVERSE_DNZT11_DNZT11_VIGNETTE_OUTCOMES_respondents.csv",
          "stored_hash": "sha256:ef87ce72d7e85399e7e47f16c0c6afd30be6aeea7dae7183ec1f38c90bd3fb48",
          "added_at": "2026-06-27T16:24:14Z"
        }
      ],
      "job_id": "eeac73cbdcb8a507",
      "study_config": {
        "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
        "heldout_question": "q13",
        "job_id": "eeac73cbdcb8a507"
      },
      "run_manifest": {
        "job_id": "eeac73cbdcb8a507",
        "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
        "status": "ok",
        "created_at": "2026-06-28T00:36:07Z",
        "job_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/dataverse_dnzt11_dnzt11_vignette_outcomes_twin_eeac73cbdcb8a507.edsl.json",
        "results_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/dataverse_dnzt11_dnzt11_vignette_outcomes_twin_eeac73cbdcb8a507_results.json.gz",
        "report_paths": {
          "html": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/dataverse_dnzt11_dnzt11_vignette_outcomes_twin_eeac73cbdcb8a507_report.html"
        },
        "heldout_questions": [
          "q13"
        ],
        "context_question_count": 5,
        "sample_respondents": 20,
        "seed": 789,
        "complete_cases": true,
        "balance_actual": false,
        "stratify_actual": true,
        "scenario_count": 20,
        "result_count": 40,
        "extracted_count": 40,
        "issue_count": 0,
        "model_count": 2,
        "models": [
          "openai:gpt-5.5",
          "google:gemini-2.5-pro"
        ]
      },
      "import_metadata": {
        "job_id": "eeac73cbdcb8a507",
        "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
        "source_path": "/Users/johnhorton/tools/ep/zwill/examples/llm_survey_priors/workdir/dataverse_dnzt11_dnzt11_vignette_outcomes_twin_eeac73cbdcb8a507_results.json.gz",
        "source_hash": "sha256:ec4cfbec02d1b77177bda09623cb85b80d135aec41a5a70091cce6639feaa022",
        "stored_path": ".zwill/surveys/dataverse_dnzt11_dnzt11_vignette_outcomes/digital_twin_jobs/eeac73cbdcb8a507/raw/dataverse_dnzt11_dnzt11_vignette_outcomes_twin_eeac73cbdcb8a507_results.json.gz",
        "stored_hash": "sha256:ec4cfbec02d1b77177bda09623cb85b80d135aec41a5a70091cce6639feaa022",
        "row_count": 40,
        "extracted_count": 40,
        "issue_count": 0,
        "issues": [],
        "imported_at": "2026-06-28T00:36:07Z"
      },
      "heldout_questions": [
        {
          "question_name": "q13",
          "question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "question_options": [
            "Strongly disagree",
            "Disagree",
            "Somewhat disagree",
            "Neither agree nor disagree",
            "Somewhat agree",
            "Agree",
            "Strongly agree"
          ]
        }
      ],
      "summary_by_model": {
        "openai:gpt-5.5": {
          "rows": 20,
          "mean_probability_actual": 0.20049999999999998,
          "mean_uniform_probability_actual": 0.14285714285714285,
          "mean_negative_log_likelihood": 1.980173672012148,
          "negative_log_likelihood_p50": 1.8775096283092405,
          "negative_log_likelihood_p90": 3.0873613467414076,
          "negative_log_likelihood_p95": 3.912023005428146,
          "negative_log_likelihood_max": 3.912023005428146,
          "mean_top_confidence": 0.35300000000000004,
          "mean_uniform_negative_log_likelihood": 1.9459101490553135,
          "mean_brier": 0.82857,
          "mean_uniform_brier": 0.8571428571428573,
          "mean_brier_improvement": 0.028572857142857355,
          "top1_accuracy": 0.3,
          "mean_empirical_marginal_probability_actual": 0.25596330275229356,
          "mean_empirical_marginal_negative_log_likelihood": 1.7227355190129732,
          "mean_empirical_marginal_brier": 0.7829653707809051,
          "empirical_marginal_top1_accuracy": 0.3,
          "mean_marginal_probability_actual": 0.25596330275229356,
          "mean_marginal_negative_log_likelihood": 1.7227355190129732,
          "mean_marginal_brier": 0.7829653707809051,
          "marginal_top1_accuracy": 0.3,
          "expected_calibration_error": 0.09300000000000003
        },
        "google:gemini-2.5-pro": {
          "rows": 20,
          "mean_probability_actual": 0.26749999999999996,
          "mean_uniform_probability_actual": 0.14285714285714285,
          "mean_negative_log_likelihood": 4.535104302847166,
          "negative_log_likelihood_p50": 1.2039728043259361,
          "negative_log_likelihood_p90": 6.90775527898217,
          "negative_log_likelihood_p95": 27.631021115928547,
          "negative_log_likelihood_max": 27.631021115928547,
          "mean_top_confidence": 0.562,
          "mean_uniform_negative_log_likelihood": 1.9459101490553135,
          "mean_brier": 0.8890100000000001,
          "mean_uniform_brier": 0.8571428571428573,
          "mean_brier_improvement": -0.03186714285714263,
          "top1_accuracy": 0.4,
          "mean_empirical_marginal_probability_actual": 0.25596330275229356,
          "mean_empirical_marginal_negative_log_likelihood": 1.7227355190129732,
          "mean_empirical_marginal_brier": 0.7829653707809051,
          "empirical_marginal_top1_accuracy": 0.3,
          "mean_marginal_probability_actual": 0.25596330275229356,
          "mean_marginal_negative_log_likelihood": 1.7227355190129732,
          "mean_marginal_brier": 0.7829653707809051,
          "marginal_top1_accuracy": 0.3,
          "expected_calibration_error": 0.24000000000000005
        }
      },
      "summary_by_question": {
        "q13": {
          "openai:gpt-5.5": {
            "rows": 20,
            "mean_probability_actual": 0.20049999999999998,
            "mean_uniform_probability_actual": 0.14285714285714285,
            "mean_negative_log_likelihood": 1.980173672012148,
            "negative_log_likelihood_p50": 1.8775096283092405,
            "negative_log_likelihood_p90": 3.0873613467414076,
            "negative_log_likelihood_p95": 3.912023005428146,
            "negative_log_likelihood_max": 3.912023005428146,
            "mean_top_confidence": 0.35300000000000004,
            "mean_uniform_negative_log_likelihood": 1.9459101490553135,
            "mean_brier": 0.82857,
            "mean_uniform_brier": 0.8571428571428573,
            "mean_brier_improvement": 0.028572857142857355,
            "top1_accuracy": 0.3,
            "mean_empirical_marginal_probability_actual": 0.25596330275229356,
            "mean_empirical_marginal_negative_log_likelihood": 1.7227355190129732,
            "mean_empirical_marginal_brier": 0.7829653707809051,
            "empirical_marginal_top1_accuracy": 0.3,
            "mean_marginal_probability_actual": 0.25596330275229356,
            "mean_marginal_negative_log_likelihood": 1.7227355190129732,
            "mean_marginal_brier": 0.7829653707809051,
            "marginal_top1_accuracy": 0.3
          },
          "google:gemini-2.5-pro": {
            "rows": 20,
            "mean_probability_actual": 0.26749999999999996,
            "mean_uniform_probability_actual": 0.14285714285714285,
            "mean_negative_log_likelihood": 4.535104302847166,
            "negative_log_likelihood_p50": 1.2039728043259361,
            "negative_log_likelihood_p90": 6.90775527898217,
            "negative_log_likelihood_p95": 27.631021115928547,
            "negative_log_likelihood_max": 27.631021115928547,
            "mean_top_confidence": 0.562,
            "mean_uniform_negative_log_likelihood": 1.9459101490553135,
            "mean_brier": 0.8890100000000001,
            "mean_uniform_brier": 0.8571428571428573,
            "mean_brier_improvement": -0.03186714285714263,
            "top1_accuracy": 0.4,
            "mean_empirical_marginal_probability_actual": 0.25596330275229356,
            "mean_empirical_marginal_negative_log_likelihood": 1.7227355190129732,
            "mean_empirical_marginal_brier": 0.7829653707809051,
            "empirical_marginal_top1_accuracy": 0.3,
            "mean_marginal_probability_actual": 0.25596330275229356,
            "mean_marginal_negative_log_likelihood": 1.7227355190129732,
            "mean_marginal_brier": 0.7829653707809051,
            "marginal_top1_accuracy": 0.3
          }
        }
      },
      "baseline_comparison": {
        "openai:gpt-5.5": {
          "p_actual_vs_uniform": 0.057642857142857135,
          "nll_vs_uniform": -0.034263522956834436,
          "brier_vs_uniform": 0.02857285714285729,
          "p_actual_vs_empirical": -0.05546330275229358,
          "nll_vs_empirical": -0.2574381529991747,
          "brier_vs_empirical": -0.045604629219094917
        },
        "google:gemini-2.5-pro": {
          "p_actual_vs_uniform": 0.12464285714285711,
          "nll_vs_uniform": -2.589194153791852,
          "brier_vs_uniform": -0.03186714285714276,
          "p_actual_vs_empirical": 0.011536697247706396,
          "nll_vs_empirical": -2.8123687838341924,
          "brier_vs_empirical": -0.10604462921909497
        }
      },
      "model_wins_over_group_average": [],
      "group_average_wins": [
        {
          "heldout_question": "q13",
          "model": "google:gemini-2.5-pro",
          "rows": 20,
          "model_nll": 4.535104302847166,
          "empirical_nll": 1.7227355190129732,
          "nll_vs_empirical": -2.8123687838341924
        },
        {
          "heldout_question": "q13",
          "model": "openai:gpt-5.5",
          "rows": 20,
          "model_nll": 1.980173672012148,
          "empirical_nll": 1.7227355190129732,
          "nll_vs_empirical": -0.2574381529991747
        }
      ],
      "overconfident_misses": [
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_1030",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Somewhat disagree",
          "predicted_option": "Neither agree nor disagree",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_106",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly agree",
          "predicted_option": "Strongly disagree",
          "probability_actual": 0.0,
          "negative_log_likelihood": 27.631021115928547,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_1097",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Neither agree nor disagree",
          "predicted_option": "Disagree",
          "probability_actual": 0.0,
          "negative_log_likelihood": 27.631021115928547,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_114",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Somewhat agree",
          "predicted_option": "Disagree",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_1015",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Agree",
          "predicted_option": "Strongly agree",
          "probability_actual": 0.3,
          "negative_log_likelihood": 1.2039728043259361,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_42",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly disagree",
          "predicted_option": "Somewhat agree",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_4",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly agree",
          "predicted_option": "Agree",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_1045",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Somewhat agree",
          "predicted_option": "Agree",
          "probability_actual": 0.3,
          "negative_log_likelihood": 1.2039728043259361,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_370",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Disagree",
          "predicted_option": "Agree",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_681",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Somewhat agree",
          "predicted_option": "Agree",
          "probability_actual": 0.3,
          "negative_log_likelihood": 1.2039728043259361,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        }
      ],
      "worst_misses": [
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_106",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly agree",
          "predicted_option": "Strongly disagree",
          "probability_actual": 0.0,
          "negative_log_likelihood": 27.631021115928547,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_1097",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Neither agree nor disagree",
          "predicted_option": "Disagree",
          "probability_actual": 0.0,
          "negative_log_likelihood": 27.631021115928547,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_42",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly disagree",
          "predicted_option": "Somewhat agree",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_687",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly agree",
          "predicted_option": "Somewhat agree",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_370",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Disagree",
          "predicted_option": "Agree",
          "probability_actual": 0.01,
          "negative_log_likelihood": 4.605170185988091,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_484",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Agree",
          "predicted_option": "Disagree",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_106",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly agree",
          "predicted_option": "Disagree",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_1030",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Somewhat disagree",
          "predicted_option": "Neither agree nor disagree",
          "probability_actual": 0.02,
          "negative_log_likelihood": 3.912023005428146,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_4",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly agree",
          "predicted_option": "Agree",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "google:gemini-2.5-pro",
          "raw_model_response": null
        },
        {
          "survey": "dataverse_dnzt11_dnzt11_vignette_outcomes",
          "job_id": "eeac73cbdcb8a507",
          "respondent_id": "dataverse_dnzt11_dnzt11_vignette_outcomes_1016",
          "heldout_question": "q13",
          "heldout_question_text": "Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.",
          "actual_answer": "Strongly agree",
          "predicted_option": "Neither agree nor disagree",
          "probability_actual": 0.05,
          "negative_log_likelihood": 2.995732273553991,
          "top1_correct": 0,
          "model": "openai:gpt-5.5",
          "raw_model_response": null
        }
      ],
      "confusion": {
        "q13::openai:gpt-5.5": {
          "Agree": {
            "Disagree": 2,
            "Agree": 2,
            "Somewhat agree": 2
          },
          "Strongly disagree": {
            "Neither agree nor disagree": 1
          },
          "Strongly agree": {
            "Somewhat agree": 1,
            "Strongly disagree": 1,
            "Disagree": 1,
            "Agree": 1,
            "Strongly agree": 1,
            "Neither agree nor disagree": 1
          },
          "Somewhat agree": {
            "Agree": 1,
            "Somewhat disagree": 1,
            "Somewhat agree": 2
          },
          "Disagree": {
            "Disagree": 1
          },
          "Neither agree nor disagree": {
            "Somewhat disagree": 1
          },
          "Somewhat disagree": {
            "Neither agree nor disagree": 1
          }
        },
        "q13::google:gemini-2.5-pro": {
          "Agree": {
            "Agree": 5,
            "Strongly agree": 1
          },
          "Strongly disagree": {
            "Somewhat agree": 1
          },
          "Strongly agree": {
            "Agree": 1,
            "Strongly agree": 2,
            "Strongly disagree": 1,
            "Somewhat agree": 2
          },
          "Somewhat agree": {
            "Agree": 2,
            "Somewhat agree": 1,
            "Disagree": 1
          },
          "Disagree": {
            "Agree": 1
          },
          "Neither agree nor disagree": {
            "Disagree": 1
          },
          "Somewhat disagree": {
            "Neither agree nor disagree": 1
          }
        }
      }
    }
  ],
  "notes": {
    "group_average_guessing": "The empirical marginal baseline: guessing from how the whole sample answered the held-out question. It is available for observed held-out questions but not for genuinely new questions.",
    "accuracy": "How often the twin's highest-probability answer matched the real respondent answer.",
    "confidence_quality": "Whether the model's confidence matched reality. Overconfident misses are especially important when using rankings or probability cutoffs."
  }
}
