#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKDIR="${ZWILL_TWIN_PLAN_EXAMPLE_DIR:-$REPO_ROOT/examples/hello_world/workdir/twin_plan_lifecycle}"

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"
export PATH="$(python3 -c 'import sysconfig; print(sysconfig.get_path("scripts"))'):$PATH"

zwill_cmd() {
  if [[ -n "${ZWILL_PYTHON:-}" ]]; then
    "$ZWILL_PYTHON" -m zwill.cli "$@"
  else
    zwill "$@"
  fi
}

zwill_cmd init
zwill_cmd survey create --name hello_twin_plan
zwill_cmd context set \
  --survey hello_twin_plan \
  --text "A toy validation survey for demonstrating digital twin plan export, status, results import, and artifact bundling."

zwill_cmd question add \
  --survey hello_twin_plan \
  --question-name favorite_color \
  --question-type multiple_choice \
  --question-text "Which color do you like best?" \
  --question-option red \
  --question-option blue \
  --question-option green

zwill_cmd question add \
  --survey hello_twin_plan \
  --question-name likes_blue \
  --question-type multiple_choice \
  --question-text "Do you like the color blue?" \
  --question-option "Yes" \
  --question-option "No"

for respondent_id in r001 r002 r003 r004 r005; do
  zwill_cmd respondent add --survey hello_twin_plan --respondent-id "$respondent_id"
done

zwill_cmd answer add --survey hello_twin_plan --respondent-id r001 --question favorite_color --answer blue
zwill_cmd answer add --survey hello_twin_plan --respondent-id r001 --question likes_blue --answer "Yes"
zwill_cmd answer add --survey hello_twin_plan --respondent-id r002 --question favorite_color --answer red
zwill_cmd answer add --survey hello_twin_plan --respondent-id r002 --question likes_blue --answer "No"
zwill_cmd answer add --survey hello_twin_plan --respondent-id r003 --question favorite_color --answer green
zwill_cmd answer add --survey hello_twin_plan --respondent-id r003 --question likes_blue --answer "No"
zwill_cmd answer add --survey hello_twin_plan --respondent-id r004 --question favorite_color --answer blue
zwill_cmd answer add --survey hello_twin_plan --respondent-id r004 --question likes_blue --answer "Yes"
zwill_cmd answer add --survey hello_twin_plan --respondent-id r005 --question favorite_color --answer red
zwill_cmd answer add --survey hello_twin_plan --respondent-id r005 --question likes_blue --answer "No"

zwill_cmd commit --survey hello_twin_plan

zwill_cmd twin-approach add \
  --survey hello_twin_plan \
  --approach-id survey_answers_only \
  --name "Prior survey answers only" \
  --description "Use the respondent's favorite-color answer to infer whether they like blue." \
  --context-questions favorite_color \
  --model openai:gpt-5.5

zwill_cmd twin-approach add \
  --survey hello_twin_plan \
  --approach-id survey_answers_plus_prior \
  --name "Prior survey answers plus one-shot prior" \
  --description "Use the respondent's favorite-color answer plus a toy one-shot population prior." \
  --context-questions favorite_color \
  --twin-material "$REPO_ROOT/examples/hello_world/twin_plan_prior.jsonl" \
  --model openai:gpt-5.5

zwill_cmd twin-experiment export-plan \
  --path "$REPO_ROOT/examples/hello_world/twin_plan.json" \
  --output-dir jobs

zwill_cmd twin-experiment plan-status --survey hello_twin_plan --plan-id hello_twin_plan

if [[ "${ZWILL_EXAMPLE_SYNTHETIC_RESULTS:-0}" == "1" ]]; then
  mkdir -p synthetic_results
  python3 - <<'PY'
import json
from pathlib import Path

manifest = json.loads(Path("jobs/manifest.json").read_text())
for export in manifest["exports"]:
    job = json.loads(Path(export["job_path"]).read_text())
    rows = []
    for scenario in job["scenarios"]:
        favorite = None
        for observed in scenario.get("observed_answers", []):
            if observed.get("question_name") == "favorite_color":
                favorite = observed.get("answer")
        likes_blue = favorite == "blue"
        if export["approach_id"] == "survey_answers_plus_prior":
            probabilities = [0.92, 0.08] if likes_blue else [0.18, 0.82]
            notes = "Synthetic no-API result: used favorite_color plus the one-shot prior."
        else:
            probabilities = [0.85, 0.15] if likes_blue else [0.25, 0.75]
            notes = "Synthetic no-API result: used favorite_color only."
        for model in job.get("models", []):
            rows.append(
                {
                    "scenario": scenario,
                    "model": model,
                    "answer": {
                        "response_probabilities": json.dumps(
                            {"probabilities": probabilities, "notes": notes}
                        )
                    },
                }
            )
    payload = {
        "edsl_class_name": "Results",
        "zwill": {"digital_twin_job_id": export["job_id"]},
        "data": rows,
    }
    path = Path("synthetic_results") / f"{export['approach_id']}_results.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
PY
  zwill_cmd twin-experiment import-plan-results --manifest jobs/manifest.json --results-dir synthetic_results --replace
  zwill_cmd twin-experiment plan-status --survey hello_twin_plan --plan-id hello_twin_plan
  zwill_cmd twin-experiment bundle \
    --survey hello_twin_plan \
    --plan-id hello_twin_plan \
    --metric nll \
    --model openai:gpt-5.5 \
    --output-dir bundle \
    --report-export
elif [[ "${ZWILL_EXAMPLE_RUN:-0}" == "1" ]]; then
  mkdir -p results
  python3 - <<'PY' | while read -r job_path results_path; do
import json
from pathlib import Path

manifest = json.loads(Path("jobs/manifest.json").read_text())
for export in manifest["exports"]:
    print(export["job_path"], f"results/{export['approach_id']}_results.json.gz")
PY
    zwill_cmd edsl-run --job "$job_path" --path "$results_path"
  done
  zwill_cmd twin-experiment import-plan-results --manifest jobs/manifest.json --results-dir results --replace
  zwill_cmd twin-experiment bundle \
    --survey hello_twin_plan \
    --plan-id hello_twin_plan \
    --metric nll \
    --model openai:gpt-5.5 \
    --output-dir bundle \
    --report-export
else
  echo
  echo "Export-only run complete."
  echo "Set ZWILL_EXAMPLE_SYNTHETIC_RESULTS=1 to generate no-API Results, import them, and build the bundle."
  echo "Set ZWILL_EXAMPLE_RUN=1 to run real EDSL jobs, import Results, and build the bundle."
fi
