#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKDIR="${ZWILL_AGENT_LIST_EXAMPLE_DIR:-$REPO_ROOT/examples/hello_world/workdir/agent_list_study}"

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
zwill_cmd survey create --name agent_list_hello
zwill_cmd context set --survey agent_list_hello --text "A one-respondent hello-world test for exporting an EDSL AgentList and asking a new question."
zwill_cmd question add \
  --survey agent_list_hello \
  --question-name favorite_color_blue \
  --question-type multiple_choice \
  --question-text "Is this respondent's favorite color blue?" \
  --question-option "Yes" \
  --question-option "No"
zwill_cmd respondent add --survey agent_list_hello --respondent-id r001
zwill_cmd answer add --survey agent_list_hello --respondent-id r001 --question favorite_color_blue --answer "Yes"
zwill_cmd agent-material add \
  --survey agent_list_hello \
  --respondent-id r001 \
  --kind profile \
  --title "Favorite color profile" \
  --text "The respondent's favorite color is blue. If asked whether their favorite color is blue, they would answer Yes." \
  --tag preference
zwill_cmd commit --survey agent_list_hello

zwill_cmd edsl build \
  --survey agent_list_hello \
  --target agent-list \
  --questions favorite_color_blue \
  --include-survey-context \
  --include-agent-material \
  --agent-material-kind profile \
  --path agent_list.ep
zwill_cmd agent-list inspect --input-path agent_list.ep

zwill_cmd agent-study export \
  --agent-list agent_list.ep \
  --question-name ask_favorite_color_blue \
  --question-type multiple_choice \
  --question-text "Given your profile and prior answers, is your favorite color blue?" \
  --question-option "Yes" \
  --question-option "No" \
  --model openai:gpt-5.5 \
  --path agent_study_jobs.ep
if [[ "${ZWILL_EXAMPLE_DRY_RUN:-0}" != "1" ]]; then
  ep run agent_study_jobs.ep --output agent_study_results.ep
  zwill_cmd agent-study import --input-path agent_study_results.ep --replace
  zwill_cmd agent-study list
  zwill_cmd agent-study report --format table
fi
