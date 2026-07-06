#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKDIR="${ZWILL_EXAMPLE_DIR:-$(mktemp -d)}"

cd "$WORKDIR"
export PATH="$(python3 -c 'import sysconfig; print(sysconfig.get_path("scripts"))'):$PATH"

zwill init
zwill survey create --name hello_world
zwill raw add --survey hello_world --id questionnaire --path "$REPO_ROOT/examples/hello_world/raw/questionnaire.md" --kind questionnaire --title "Hello World Questionnaire"
zwill question add --survey hello_world --question-name favorite_color --question-type multiple_choice --question-text "Which color do you like best?" --question-option red --question-option blue --question-option green --role survey_item --source-raw questionnaire --source-note "Single hello-world test question."
zwill respondent add --survey hello_world --respondent-id r001 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r002 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r003 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r004 --weight 1.0 --metadata "sample_source=demo"
zwill respondent add --survey hello_world --respondent-id r005 --weight 1.0 --metadata "sample_source=demo"
zwill answer add --survey hello_world --respondent-id r001 --question favorite_color --answer red
zwill answer add --survey hello_world --respondent-id r002 --question favorite_color --answer blue
zwill answer add --survey hello_world --respondent-id r003 --question favorite_color --answer green
zwill answer add --survey hello_world --respondent-id r004 --question favorite_color --answer blue
zwill answer add --survey hello_world --respondent-id r005 --question favorite_color --answer red
zwill table --survey hello_world
