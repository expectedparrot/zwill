#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="${SCRIPT_DIR}/workdir"
SURVEY="${ZWILL_SURVEY:-w158_ccpolicy}"
HELDOUT="${ZWILL_HELDOUT:-a}"
SAMPLE="${ZWILL_SAMPLE:-10}"
SEED="${ZWILL_SEED:-123}"

cd "${WORKDIR}"

zwill status
zwill table --survey "${SURVEY}" --limit 5

COMMON_ARGS=(
  --survey "${SURVEY}"
  --heldout-question "${HELDOUT}"
  --context-question-count 5
  --sample-respondents "${SAMPLE}"
  --seed "${SEED}"
  --complete-cases
  --stratify-actual
  --model openai:gpt-5.5
  --model google:gemini-2.5-pro
  --model-param google:gemini-2.5-pro:max_tokens=8192
  --model-param google:gemini-2.5-pro:thinking_budget=4096
  --model-param google:gemini-2.5-pro:temperature=0
  --output-dir "${WORKDIR}"
  --job-path "${WORKDIR}/${SURVEY}_small_twin_jobs.ep"
  --results-path "${WORKDIR}/${SURVEY}_small_twin_results.ep"
  --report-json "${WORKDIR}/${SURVEY}_small_twin_report.json"
  --report-csv "${WORKDIR}/${SURVEY}_small_twin_report.csv"
  --replace
)

zwill twin-study build "${COMMON_ARGS[@]}"

if [[ "${ZWILL_RUN_EDSL:-0}" == "1" ]]; then
  ep run "${WORKDIR}/${SURVEY}_small_twin_jobs.ep" --output "${WORKDIR}/${SURVEY}_small_twin_results.ep"
  zwill twin-results import --survey "${SURVEY}" --input-path "${WORKDIR}/${SURVEY}_small_twin_results.ep" --replace
  zwill twin-results report --survey "${SURVEY}" --format html --path "${WORKDIR}/${SURVEY}_small_twin_report.html"
fi
