#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKDIR="${ZWILL_AGENT_MATERIAL_EXAMPLE_DIR:-$REPO_ROOT/examples/hello_world/workdir/agent_material}"

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
zwill_cmd survey create --name agent_material_hello
zwill_cmd context set --survey agent_material_hello --text "A one-respondent hello-world test for whether non-survey agent material changes digital twin predictions."
zwill_cmd question add \
  --survey agent_material_hello \
  --question-name favorite_color_blue \
  --question-type multiple_choice \
  --question-text "Is this respondent's favorite color blue?" \
  --question-option "Yes" \
  --question-option "No"
zwill_cmd respondent add --survey agent_material_hello --respondent-id r001
zwill_cmd answer add --survey agent_material_hello --respondent-id r001 --question favorite_color_blue --answer "Yes"
zwill_cmd agent-material add \
  --survey agent_material_hello \
  --respondent-id r001 \
  --kind profile \
  --title "Favorite color profile" \
  --text "The respondent's favorite color is blue. If asked whether their favorite color is blue, they would answer Yes." \
  --tag preference
zwill_cmd commit --survey agent_material_hello

zwill_cmd edsl build \
  --survey agent_material_hello \
  --target twin-probability-job \
  --allow-unapproved \
  --heldout-question favorite_color_blue \
  --respondent r001 \
  --context-question-count 0 \
  --model openai:gpt-5.5 \
  --path without_material_jobs.ep
if [[ "${ZWILL_EXAMPLE_DRY_RUN:-0}" != "1" ]]; then
  ep run without_material_jobs.ep --output without_material_results.ep
  zwill_cmd twin-results import --survey agent_material_hello --input-path without_material_results.ep --replace
fi

zwill_cmd edsl build \
  --survey agent_material_hello \
  --target twin-probability-job \
  --allow-unapproved \
  --heldout-question favorite_color_blue \
  --respondent r001 \
  --context-question-count 0 \
  --include-agent-material \
  --agent-material-kind profile \
  --model openai:gpt-5.5 \
  --path with_material_jobs.ep
if [[ "${ZWILL_EXAMPLE_DRY_RUN:-0}" != "1" ]]; then
  ep run with_material_jobs.ep --output with_material_results.ep
  zwill_cmd twin-results import --survey agent_material_hello --input-path with_material_results.ep --replace

  zwill_cmd twin-study list --survey agent_material_hello --format json
fi
