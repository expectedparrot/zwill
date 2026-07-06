#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SOURCE_DIR="${ZWILL_PEW_SOURCE_DIR:-/Users/johnhorton/tools/ep/capabilities/packages/llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized}"
WORKDIR="${ZWILL_EXAMPLE_DIR:-$REPO_ROOT/examples/pew_w154_diff1/workdir}"
IMPORT_DIR="$WORKDIR/imports"

export PATH="$(python3 -c 'import sysconfig; print(sysconfig.get_path("scripts"))'):$PATH"

rm -rf "$WORKDIR/.zwill" "$IMPORT_DIR"
mkdir -p "$WORKDIR"

python3 "$REPO_ROOT/examples/pew_w154_diff1/prepare_imports.py" \
  --source-dir "$SOURCE_DIR" \
  --out-dir "$IMPORT_DIR"

cd "$WORKDIR"

zwill init
zwill survey create --name pew_w154_diff1
zwill context add --survey pew_w154_diff1 --path "$REPO_ROOT/examples/pew_w154_diff1/context.md"
zwill raw add --survey pew_w154_diff1 --id w154_diff1_metadata --path "$SOURCE_DIR/W154_DIFF1_metadata.json" --kind metadata --title "Pew W154 DIFF1 Normalized Metadata"
zwill raw add --survey pew_w154_diff1 --id w154_diff1_respondents --path "$SOURCE_DIR/W154_DIFF1_respondents.csv" --kind respondent_data --title "Pew W154 DIFF1 Normalized Respondents"
zwill question import --survey pew_w154_diff1 --path "$IMPORT_DIR/questions.jsonl"
zwill respondent import --survey pew_w154_diff1 --path "$IMPORT_DIR/respondents.jsonl"
zwill answer import --survey pew_w154_diff1 --path "$IMPORT_DIR/answers.jsonl"
zwill commit --survey pew_w154_diff1
zwill status
zwill table --survey pew_w154_diff1 --limit 12
zwill edsl-export --survey pew_w154_diff1 --path "$WORKDIR/pew_w154_diff1.edsl.json" >/dev/null
echo "EDSL export: $WORKDIR/pew_w154_diff1.edsl.json"
zwill edsl-export --survey pew_w154_diff1 --target probability-job --model openai:gpt-5.5 --model google:gemini-2.5-pro --model-param google:gemini-2.5-pro:max_tokens=8192 --model-param google:gemini-2.5-pro:thinking_budget=4096 --model-param google:gemini-2.5-pro:temperature=0 --path "$WORKDIR/pew_w154_diff1_probability_job.edsl.json" >/dev/null
echo "EDSL probability job export: $WORKDIR/pew_w154_diff1_probability_job.edsl.json"
