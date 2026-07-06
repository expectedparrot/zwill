#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PEW_WORKDIR="${ZWILL_PEW_WORKDIR:-$REPO_ROOT/examples/pew_w154_diff1/workdir}"
OUTDIR="${ZWILL_PEW_AGENT_STUDY_DIR:-$PEW_WORKDIR/agent_study_leadership}"
ZWILL_BIN="${ZWILL_BIN:-zwill}"

mkdir -p "$OUTDIR"
cd "$PEW_WORKDIR"

export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

zwill_cmd() {
  if [[ -n "${ZWILL_PYTHON:-}" ]]; then
    "$ZWILL_PYTHON" -m zwill.cli "$@"
  else
    "$ZWILL_BIN" "$@"
  fi
}

run_step() {
  local stem="$1"
  shift
  "$@" > "$OUTDIR/${stem}.stdout.txt" 2> "$OUTDIR/${stem}.stderr.txt"
}

AGENT_LIST="$OUTDIR/pew_w154_diff1_agent_list.edsl.json"
AGENT_LIST_INSPECT="$OUTDIR/pew_w154_diff1_agent_list.inspect.json"
MC_JOB="$OUTDIR/pew_w154_diff1_agent_study_leadership_job.edsl.json"
MC_RESULTS="$OUTDIR/pew_w154_diff1_agent_study_leadership_results.json.gz"
FT_JOB="$OUTDIR/pew_w154_diff1_agent_study_gender_roles_job.edsl.json"
FT_RESULTS="$OUTDIR/pew_w154_diff1_agent_study_gender_roles_results.json.gz"
MC_IMPORT_LOG="$OUTDIR/pew_w154_diff1_agent_study_leadership_import.json"
FT_IMPORT_LOG="$OUTDIR/pew_w154_diff1_agent_study_gender_roles_import.json"
REPORT_JSON="$OUTDIR/pew_w154_diff1_agent_study_report.json"
REPORT_HTML="$OUTDIR/pew_w154_diff1_agent_study_report.html"

if [[ "${ZWILL_PEW_SKIP_IMPORT:-0}" != "1" ]]; then
  run_step 00_pew_workflow \
    zwill_cmd workflow pew-demo --fresh --no-edsl --workdir "$PEW_WORKDIR"
else
  printf 'Skipped by ZWILL_PEW_SKIP_IMPORT=1\n' > "$OUTDIR/00_pew_workflow.stdout.txt"
  : > "$OUTDIR/00_pew_workflow.stderr.txt"
fi

run_step 01_agent_list_export \
  zwill_cmd edsl-export \
  --survey pew_w154_diff1 \
  --target agent-list \
  --questions diff1_a,diff1_b,diff1_c,diff1_d,diff1_e \
  --limit 30 \
  --include-survey-context \
  --path "$AGENT_LIST"

run_step 02_agent_list_inspect \
  zwill_cmd agent-list inspect \
  --path "$AGENT_LIST" \
  --format json
cp "$OUTDIR/02_agent_list_inspect.stdout.txt" "$AGENT_LIST_INSPECT"

run_step 03_agent_study_export_leadership \
  zwill_cmd agent-study export \
  --agent-list "$AGENT_LIST" \
  --question-name gender_political_leadership_similarity \
  --question-type multiple_choice \
  --question-text "In general, when it comes to being effective leaders in politics, are men and women basically similar or basically different?" \
  --question-option "Men and women are basically similar" \
  --question-option "Men and women are basically different" \
  --model openai:gpt-5.5 \
  --model-param temperature=0 \
  --path "$MC_JOB"

run_step 04_agent_study_export_gender_roles \
  zwill_cmd agent-study export \
  --agent-list "$AGENT_LIST" \
  --question-name gender_roles_views \
  --question-type free_text \
  --question-text "Given this respondent's prior answers, briefly describe this respondent's likely views on gender roles in society. Mention the evidence from their prior survey answers." \
  --model openai:gpt-5.5 \
  --model-param temperature=0 \
  --path "$FT_JOB"

if [[ "${ZWILL_EXAMPLE_DRY_RUN:-0}" == "1" ]]; then
  run_step 05_edsl_run_leadership_dry_run \
    zwill_cmd edsl-run --job "$MC_JOB" --path "$MC_RESULTS" --dry-run
  run_step 06_edsl_run_gender_roles_dry_run \
    zwill_cmd edsl-run --job "$FT_JOB" --path "$FT_RESULTS" --dry-run
  echo "Dry run complete. Jobs written to $MC_JOB and $FT_JOB"
  exit 0
fi

run_step 05_edsl_run_leadership \
  zwill_cmd edsl-run --job "$MC_JOB" --path "$MC_RESULTS"
run_step 06_agent_study_import_leadership \
  zwill_cmd agent-study import --path "$MC_RESULTS" --replace
cp "$OUTDIR/06_agent_study_import_leadership.stdout.txt" "$MC_IMPORT_LOG"

run_step 07_edsl_run_gender_roles \
  zwill_cmd edsl-run --job "$FT_JOB" --path "$FT_RESULTS"
run_step 08_agent_study_import_gender_roles \
  zwill_cmd agent-study import --path "$FT_RESULTS" --replace
cp "$OUTDIR/08_agent_study_import_gender_roles.stdout.txt" "$FT_IMPORT_LOG"

run_step 09_agent_study_report \
  zwill_cmd agent-study report \
  --format json \
  --path "$REPORT_JSON"

python3 "$REPO_ROOT/examples/pew_w154_diff1/agent_study_report.py" \
  --agent-list-inspect "$AGENT_LIST_INSPECT" \
  --leadership-job "$MC_JOB" \
  --gender-roles-job "$FT_JOB" \
  --report-json "$REPORT_JSON" \
  --output "$REPORT_HTML" \
  --workdir "$PEW_WORKDIR" \
  --artifacts-dir "$OUTDIR"
