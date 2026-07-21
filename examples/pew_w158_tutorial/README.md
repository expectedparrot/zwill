# Pew W158 climate-policy tutorial excerpt

This directory contains a fixed 100-respondent excerpt from Pew Research
Center's American Trends Panel Wave 158 survey of U.S. adults, fielded in
October 2024. The six questions ask whether respondents favor or oppose
different proposals intended to reduce the effects of climate change.

The excerpt is included for teaching, reproducibility, criticism, and
validation of survey-analysis methods. It contains deidentified respondent
IDs, survey weights, six normalized questions, and 600 normalized answers.
It is not a substitute for the complete source dataset or its documentation.
The records are the first 100 rows of the normalized source, so this is a fixed
convenience excerpt rather than a representative subsample. Survey weights do
not correct that selection.

The original normalized source used coded demographic metadata whose codebook
is not bundled here. Those fields were deliberately omitted rather than
presenting opaque codes to either tutorial readers or language models. The
remaining five climate-policy answers provide the permitted respondent context
when the sixth answer is held out.

Files:

- `questions.jsonl`: six codebook-expanded multiple-choice questions.
- `respondents.jsonl`: 100 deidentified IDs and survey weights.
- `answers.jsonl`: six answers per respondent.
- `source.md`: provenance and interpretation notes retained by zwill.
- `run_summary.json`: recorded one-shot, twin, XGBoost, bootstrap, leakage,
  and cost results from the completed tutorial run on 2026-07-21.

Source: Pew Research Center, American Trends Panel Wave 158. Pew Research
Center bears no responsibility for the analysis or interpretations presented
by this tutorial.

- Dataset: https://www.pewresearch.org/dataset/american-trends-panel-wave-158/
- Methodology: https://www.pewresearch.org/science/2024/12/09/climate-policies-methodology/
