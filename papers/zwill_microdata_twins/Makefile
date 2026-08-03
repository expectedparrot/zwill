SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DELETE_ON_ERROR:

# -----------------------------------------------------------------------------
# Study configuration
# -----------------------------------------------------------------------------

PAPER          := paper
WORK           := $(CURDIR)/workdir
PIPE           := $(WORK)/.pipeline
MODEL          := openai:gpt-5.5
EMBEDDING_MODEL := text-embedding-3-small
SEED           := 20260730
EDSL_ROOT      ?= $(abspath $(CURDIR)/../../../edsl)
EP             ?= env PYTHONPATH=$(EDSL_ROOT) python -m edsl
ENV_FILE       := $(CURDIR)/../../.env
REMOTE_FLAG    := $(if $(filter 1,$(ALLOW_REMOTE)),--execute,)
REVIEWER       := $(if $(APPROVED_BY),$(APPROVED_BY),johnhorton)
BYQ_WORKFLOW   := $(WORK)/workflow/by-question
MODEL_WORKFLOW := $(WORK)/workflow/model-comparison

GSS_RELEASE    := 3a
GSS_ROOT       := $(WORK)/sources/gss2024
GSS_ARCHIVE    := $(GSS_ROOT)/2024_stata.zip
GSS_DATA       := $(GSS_ROOT)/raw/2024/GSS2024.dta
GSS_SPEC       := config/gss_2024_batteries.json
GSS_EXTRACTED  := $(GSS_ROOT)/extracted
GSS_INGESTION  := $(GSS_ROOT)/ingestion.json
GSS_WORKFLOW   := $(WORK)/workflow/gss2024_validation
GSS_JOBS       := $(GSS_WORKFLOW)/jobs
GSS_RESULTS    := $(WORK)/results/gss2024_eligible200
GSS_AUDITS     := $(WORK)/reports/gss2024/audits
GSS_VALIDATION := $(WORK)/reports/gss2024/validation
GSS_SUMMARY    := $(WORK)/cross_survey/gss2024
GSS_COSTS      := $(GSS_SUMMARY)/costs.json
GSS_ONE_SHOT_JOBS := $(WORK)/jobs/gss2024_one_shot
GSS_ONE_SHOT_RESULTS := $(WORK)/results
GSS_PROBABILITY_JOBS := $(WORK)/reports/gss2024/probability-jobs.json
GSS_SURVEYS    := \
	gss2024_confidence_in_institutions \
	gss2024_socializing_frequency \
	gss2024_abortion_circumstances_standard_form \
	gss2024_government_responsibility \
	gss2024_national_spending_standard_form
GSS_COMMITTED  := $(GSS_SURVEYS:%=$(PIPE)/gss/committed/%.stamp)

OPENICPSR_186241_ARCHIVE ?= $(HOME)/Downloads/186241-V1.zip
OPENICPSR_186241_ROOT    := $(WORK)/sources/openicpsr_186241
OPENICPSR_186241_RAW     := $(OPENICPSR_186241_ROOT)/raw
OPENICPSR_186241_SPEC    := config/openicpsr_186241_kalro_batteries.json
OPENICPSR_186241_EXTRACTED := $(OPENICPSR_186241_ROOT)/extracted
OPENICPSR_186241_CONVERTED := $(OPENICPSR_186241_ROOT)/converted
OPENICPSR_186241_KALRO   := $(OPENICPSR_186241_RAW)/KALRO_baseline_raw.csv
OPENICPSR_186241_PROJECT := kalro_pilot
OPENICPSR_186241_PROJECT_DIR := $(WORK)/.zwill/projects/$(OPENICPSR_186241_PROJECT)
OPENICPSR_186241_PILOT_JOBS := $(WORK)/jobs/openicpsr_186241/kalro_pilot_jobs.ep
OPENICPSR_186241_PILOT_RESULTS := $(WORK)/results/openicpsr_186241/kalro_pilot_results.ep
OPENICPSR_186241_PILOT_IMPORT := $(WORK)/reports/openicpsr_186241/kalro_pilot_import.json
OPENICPSR_186241_PILOT_VALIDATION := $(WORK)/zwill_work/reports/openicpsr_186241/kalro_pilot_validation
OPENICPSR_186241_STUDY   := config/openicpsr_186241_kalro_study.json
OPENICPSR_186241_FULL    := openicpsr_186241/full
OPENICPSR_186241_WORKFLOW := $(WORK)/workflow/$(OPENICPSR_186241_FULL)
OPENICPSR_186241_JOBS    := $(OPENICPSR_186241_WORKFLOW)/jobs
OPENICPSR_186241_RESULTS := $(WORK)/results/$(OPENICPSR_186241_FULL)
OPENICPSR_186241_ONE_SHOT_JOBS := $(WORK)/jobs/openicpsr_186241/full_one_shot
OPENICPSR_186241_ONE_SHOT_RESULTS := $(WORK)/results
OPENICPSR_186241_PROBABILITY_JOBS := $(WORK)/reports/openicpsr_186241/full_probability_jobs.json
OPENICPSR_186241_AUDITS := $(WORK)/zwill_work/reports/openicpsr_186241/full_audits
OPENICPSR_186241_VALIDATION := $(WORK)/zwill_work/reports/openicpsr_186241/full_validation
OPENICPSR_186241_SUMMARY := $(WORK)/zwill_work/reports/openicpsr_186241/full_summary
OPENICPSR_186241_COSTS := $(WORK)/zwill_work/reports/openicpsr_186241/full_costs.json

PRIMARY_PLANS := \
	w132_spiressent1_validation_v1 \
	w141_provider_validation_v1 \
	w150_smuse_validation_v1 \
	w152_humanvai_validation_v1 \
	w154_diff1_validation_v1 \
	w157_skillimp_validation_v1 \
	w158_ccpolicy_validation_v1 \
	w163_sm9_validation_v1 \
	asian2022_23_originbattery_validation_v1 \
	gallup_remote_covid_gallup_remote_covid_validation_v1 \
	gallup_wellbeing_gallup_wellbeing_validation_v1 \
	dataverse_478tlg_korea_mobility_fairness_validation_v1 \
	dataverse_6rk7rd_brazil_digital_credit_validation_v1 \
	dataverse_dnzt11_dnzt11_vignette_outcomes_validation_v1 \
	dataverse_iq6ebz_iq6ebz_2024_democracy_norms_validation_v1

# One independent probability job per battery. A newly appended survey creates
# one new export/result/import chain and does not invalidate completed batteries.
ONE_SHOT_SURVEYS := \
	w132_spiressent1 \
	w141_provider \
	w150_smuse \
	w152_humanvai \
	w154_diff1 \
	w157_skillimp \
	w158_ccpolicy \
	w163_sm9 \
	asian2022_23_originbattery \
	gallup_remote_covid_gallup_remote_covid \
	gallup_wellbeing_gallup_wellbeing \
	dataverse_478tlg_korea_mobility_fairness \
	dataverse_6rk7rd_brazil_digital_credit \
	dataverse_dnzt11_dnzt11_vignette_outcomes \
	dataverse_iq6ebz_iq6ebz_2024_democracy_norms

ONE_SHOT_EXPORTED := $(ONE_SHOT_SURVEYS:%=$(PIPE)/one-shot/exported/%.stamp)
ONE_SHOT_INFERRED := $(ONE_SHOT_SURVEYS:%=$(PIPE)/one-shot/inferred/%.stamp)
ONE_SHOT_IMPORTED := $(ONE_SHOT_SURVEYS:%=$(PIPE)/one-shot/imported/%.stamp)
ONE_SHOT_REPORTED := $(ONE_SHOT_SURVEYS:%=$(PIPE)/one-shot/reported/%.stamp)

CORRECTIVE_PLAN := gallup_remote_covid_q6rec_leakage_fix_v1
CORRECTIVE_SURVEY := gallup_remote_covid_gallup_remote_covid

APPROVED  := $(PRIMARY_PLANS:%=$(PIPE)/approved/%.stamp)
EXPORTED  := $(PRIMARY_PLANS:%=$(PIPE)/exported/%.stamp)
INFERRED  := $(PRIMARY_PLANS:%=$(PIPE)/inferred/%.stamp)
IMPORTED  := $(PRIMARY_PLANS:%=$(PIPE)/imported/%.stamp)
AUDITED   := $(PRIMARY_PLANS:%=$(PIPE)/audited/%.stamp)
VALIDATED := $(PRIMARY_PLANS:%=$(PIPE)/validated/%.stamp)
REPORTED  := $(PRIMARY_PLANS:%=$(PIPE)/reported/%.stamp)

TEX := TEXMFVAR=$(CURDIR)/output/texcache \
	TEXMFCACHE=$(CURDIR)/output/texcache \
	lualatex -interaction=nonstopmode -halt-on-error

.PHONY: all paper offline approve export infer import audit validate reports \
	by-question-plans by-question-infer by-question-import by-question-audit \
	by-question-validate model-plans model-infer model-import model-audit \
	model-validate one-shot-export one-shot-infer one-shot-import \
	one-shot-reports one-shot-validate one-shot-summary study-summaries semantic-proximity costs \
	summary live reproduce corrective check clean help gss-extract gss-ingest gss-plans \
	gss-approve gss-export gss-infer gss-import gss-audit gss-validate gss-summary \
	gss-one-shot-export gss-one-shot-infer gss-one-shot-import gss-costs \
	openicpsr-186241-extract openicpsr-186241-check openicpsr-186241-ingest \
	openicpsr-186241-pilot-export openicpsr-186241-pilot-infer \
	openicpsr-186241-pilot-import openicpsr-186241-pilot-validate \
	openicpsr-186241-full-plans openicpsr-186241-full-approve \
	openicpsr-186241-full-export openicpsr-186241-full-infer \
	openicpsr-186241-full-import openicpsr-186241-full-one-shot-export \
	openicpsr-186241-full-one-shot-infer openicpsr-186241-full-one-shot-import \
	openicpsr-186241-full-audit openicpsr-186241-full-validate \
	openicpsr-186241-full-summary openicpsr-186241-full-costs \
	openicpsr-186241-full

# Safe default: build from cached validation artifacts. It never calls ep run.
all: paper
paper: output/$(PAPER).pdf

help:
	@echo 'make offline'
	@echo '    Convert normalized sources, ingest with zwill, and create draft plans.'
	@echo 'make live ALLOW_REMOTE=1 APPROVED_BY="Name"'
	@echo '    Approve, export, run, import, audit, validate, and render all plans.'
	@echo 'make reproduce ALLOW_REMOTE=1 APPROVED_BY="Name"'
	@echo '    Run the complete source-to-PDF pipeline.'
	@echo 'make all'
	@echo '    Rebuild tables, plots, and PDF from cached validation bundles.'

# -----------------------------------------------------------------------------
# 1. Normalized source files -> committed zwill surveys and draft plans
# -----------------------------------------------------------------------------

$(PIPE)/offline.stamp: build.py config/surveys.json config/covariate_codebook.json ../../zwill/matrix_ingest.py \
	../../zwill/study_commands.py
	mkdir -p $(PIPE)
	test -f $(WORK)/study_manifest.json || \
		(cd ../.. && python papers/zwill_microdata_twins/build.py)
	touch $@

offline: $(PIPE)/offline.stamp

# -----------------------------------------------------------------------------
# 1b. Official 2024 GSS Release 3a -> five committed zwill batteries
# -----------------------------------------------------------------------------

$(GSS_ARCHIVE):
	mkdir -p $(@D)
	curl -L --fail --retry 3 --output $@ \
		https://gss.norc.org/content/dam/gss/get-the-data/documents/stata/2024_stata.zip
	echo '107c11ce811c004207844bd2d23265ec32370e27b72d965c630fe849fc971b6f  $@' | shasum -a 256 -c -

$(GSS_DATA): $(GSS_ARCHIVE)
	mkdir -p $(GSS_ROOT)/raw
	unzip -o $(GSS_ARCHIVE) -d $(GSS_ROOT)/raw
	test -f $@
	touch $@

$(GSS_EXTRACTED)/manifest.json: $(GSS_DATA) $(GSS_SPEC) ../../zwill/stata_extract.py
	zwill survey extract-stata --data $(GSS_DATA) --spec $(GSS_SPEC) --out $(GSS_EXTRACTED)

gss-extract: $(GSS_EXTRACTED)/manifest.json

$(GSS_INGESTION): | $(GSS_EXTRACTED)/manifest.json $(PIPE)/offline.stamp
	cd $(WORK) && zwill study init --path $@
	cd $(WORK) && zwill study set-matrix-defaults --manifest $@ \
		--question-name-template '{battery}_{item}'
	cd $(WORK) && zwill study add-matrix --manifest $@ \
		--survey gss2024_confidence_in_institutions \
		--source-id GSS2024_CONFIDENCE_IN_INSTITUTIONS \
		--metadata $(GSS_EXTRACTED)/gss2024_confidence_in_institutions_metadata.json \
		--respondents $(GSS_EXTRACTED)/gss2024_confidence_in_institutions_respondents.csv
	cd $(WORK) && zwill study add-matrix --manifest $@ \
		--survey gss2024_socializing_frequency \
		--source-id GSS2024_SOCIALIZING_FREQUENCY \
		--metadata $(GSS_EXTRACTED)/gss2024_socializing_frequency_metadata.json \
		--respondents $(GSS_EXTRACTED)/gss2024_socializing_frequency_respondents.csv
	cd $(WORK) && zwill study add-matrix --manifest $@ \
		--survey gss2024_abortion_circumstances_standard_form \
		--source-id GSS2024_ABORTION_CIRCUMSTANCES_STANDARD_FORM \
		--metadata $(GSS_EXTRACTED)/gss2024_abortion_circumstances_standard_form_metadata.json \
		--respondents $(GSS_EXTRACTED)/gss2024_abortion_circumstances_standard_form_respondents.csv
	cd $(WORK) && zwill study add-matrix --manifest $@ \
		--survey gss2024_government_responsibility \
		--source-id GSS2024_GOVERNMENT_RESPONSIBILITY \
		--metadata $(GSS_EXTRACTED)/gss2024_government_responsibility_metadata.json \
		--respondents $(GSS_EXTRACTED)/gss2024_government_responsibility_respondents.csv
	cd $(WORK) && zwill study add-matrix --manifest $@ \
		--survey gss2024_national_spending_standard_form \
		--source-id GSS2024_NATIONAL_SPENDING_STANDARD_FORM \
		--metadata $(GSS_EXTRACTED)/gss2024_national_spending_standard_form_metadata.json \
		--respondents $(GSS_EXTRACTED)/gss2024_national_spending_standard_form_respondents.csv
	cd $(WORK) && zwill study check --manifest $@
	cd $(WORK) && zwill study ingest --manifest $@ --out $(GSS_ROOT)/imports --apply

$(PIPE)/gss/committed/%.stamp: $(GSS_INGESTION)
	mkdir -p $(@D)
	cd $(WORK) && zwill commit --survey $*
	cd $(WORK) && zwill survey report --survey $* --format html \
		--path reports/$*/survey-profile.html
	cd $(WORK) && zwill next --survey $*
	touch $@

gss-ingest: $(GSS_COMMITTED)

# -----------------------------------------------------------------------------
# 1c. AEA/openICPSR E186241V1 -> reviewed KALRO baseline matrices
# -----------------------------------------------------------------------------

$(OPENICPSR_186241_ROOT)/raw.stamp: $(OPENICPSR_186241_ARCHIVE)
	mkdir -p $(OPENICPSR_186241_RAW)
	echo '51f50f7eaab336de9c8b0850aebaa35e36362fd8541a2bd9a53a44f365bbb7cd  $<' | shasum -a 256 -c -
	unzip -j -o $< \
		'Replication Package 2024/data/raw/KALRO/KALRO_baseline_raw.csv' \
		'Replication Package 2024/materials/KALRO/KALRO_Baseline.pdf' \
		'Replication Package 2024/README.pdf' \
		-d $(OPENICPSR_186241_RAW)
	test -f $(OPENICPSR_186241_KALRO)
	test -f $(OPENICPSR_186241_RAW)/KALRO_Baseline.pdf
	test -f $(OPENICPSR_186241_RAW)/README.pdf
	touch $@

$(OPENICPSR_186241_EXTRACTED)/manifest.json: \
		$(OPENICPSR_186241_ROOT)/raw.stamp $(OPENICPSR_186241_SPEC) ../../zwill/csv_extract.py
	zwill survey extract-csv \
		--data $(OPENICPSR_186241_KALRO) \
		--spec $(OPENICPSR_186241_SPEC) \
		--out $(OPENICPSR_186241_EXTRACTED)

openicpsr-186241-extract: $(OPENICPSR_186241_EXTRACTED)/manifest.json

$(PIPE)/openicpsr-186241/checked.stamp: $(OPENICPSR_186241_EXTRACTED)/manifest.json
	zwill survey ingest-matrix --survey kalro_input_awareness \
		--metadata $(OPENICPSR_186241_EXTRACTED)/kalro_input_awareness_metadata.json \
		--respondents $(OPENICPSR_186241_EXTRACTED)/kalro_input_awareness_respondents.csv \
		--out $(OPENICPSR_186241_CONVERTED)/kalro_input_awareness
	zwill survey ingest-matrix --survey kalro_input_use \
		--metadata $(OPENICPSR_186241_EXTRACTED)/kalro_input_use_metadata.json \
		--respondents $(OPENICPSR_186241_EXTRACTED)/kalro_input_use_respondents.csv \
		--out $(OPENICPSR_186241_CONVERTED)/kalro_input_use
	zwill survey ingest-matrix --survey kalro_farming_practices \
		--metadata $(OPENICPSR_186241_EXTRACTED)/kalro_farming_practices_metadata.json \
		--respondents $(OPENICPSR_186241_EXTRACTED)/kalro_farming_practices_respondents.csv \
		--out $(OPENICPSR_186241_CONVERTED)/kalro_farming_practices
	mkdir -p $(@D)
	touch $@

openicpsr-186241-check: $(PIPE)/openicpsr-186241/checked.stamp

$(OPENICPSR_186241_PROJECT_DIR)/project.json: $(PIPE)/openicpsr-186241/checked.stamp
	cd $(WORK) && zwill project create $(OPENICPSR_186241_PROJECT) \
		--title 'KALRO pilot validation'

$(PIPE)/openicpsr-186241/ingested.stamp: $(OPENICPSR_186241_PROJECT_DIR)/project.json
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill survey ingest-matrix --survey kalro_input_awareness \
		--metadata $(OPENICPSR_186241_EXTRACTED)/kalro_input_awareness_metadata.json \
		--respondents $(OPENICPSR_186241_EXTRACTED)/kalro_input_awareness_respondents.csv \
		--out $(OPENICPSR_186241_CONVERTED)/kalro_input_awareness --apply
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill survey ingest-matrix --survey kalro_input_use \
		--metadata $(OPENICPSR_186241_EXTRACTED)/kalro_input_use_metadata.json \
		--respondents $(OPENICPSR_186241_EXTRACTED)/kalro_input_use_respondents.csv \
		--out $(OPENICPSR_186241_CONVERTED)/kalro_input_use --apply
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill survey ingest-matrix --survey kalro_farming_practices \
		--metadata $(OPENICPSR_186241_EXTRACTED)/kalro_farming_practices_metadata.json \
		--respondents $(OPENICPSR_186241_EXTRACTED)/kalro_farming_practices_respondents.csv \
		--out $(OPENICPSR_186241_CONVERTED)/kalro_farming_practices --apply
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill commit --survey kalro_input_awareness
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill commit --survey kalro_input_use
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill commit --survey kalro_farming_practices
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill survey report --survey kalro_farming_practices --format html \
		--path reports/openicpsr_186241/kalro_farming_practices.html
	mkdir -p $(@D)
	touch $@

openicpsr-186241-ingest: $(PIPE)/openicpsr-186241/ingested.stamp

$(OPENICPSR_186241_PILOT_JOBS): $(PIPE)/openicpsr-186241/ingested.stamp
	mkdir -p $(@D)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill edsl build --survey kalro_farming_practices \
		--target twin-probability-job \
		--heldout-question g10e_used_crop_rotation \
		--context-question-count 8 --sample-respondents 5 --seed $(SEED) \
		--complete-cases --model $(MODEL) --allow-unapproved --path $@

openicpsr-186241-pilot-export: $(OPENICPSR_186241_PILOT_JOBS)

$(OPENICPSR_186241_PILOT_RESULTS): $(OPENICPSR_186241_PILOT_JOBS)
	test "$(ALLOW_REMOTE)" = "1"
	mkdir -p $(@D)
	set -a; source $(ENV_FILE); set +a; \
		ep run --jobs $< --background --wait --timeout 600 --output $@

openicpsr-186241-pilot-infer: $(OPENICPSR_186241_PILOT_RESULTS)

$(OPENICPSR_186241_PILOT_IMPORT): $(OPENICPSR_186241_PILOT_RESULTS)
	mkdir -p $(@D)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill twin-results import --survey kalro_farming_practices \
		--input-path results/openicpsr_186241/kalro_pilot_results.ep \
		--jobs jobs/openicpsr_186241/kalro_pilot_jobs.ep --replace > $@

openicpsr-186241-pilot-import: $(OPENICPSR_186241_PILOT_IMPORT)

$(PIPE)/openicpsr-186241/pilot-validated.stamp: $(OPENICPSR_186241_PILOT_IMPORT)
	job_id=$$(jq -r .data.job_id $<); \
		cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill twin-results run-report --survey kalro_farming_practices \
		--job-id $$job_id --format html \
		--path reports/openicpsr_186241/kalro_pilot_run.html
	job_id=$$(jq -r .data.job_id $<); \
		cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill twin-validate --survey kalro_farming_practices --job-id $$job_id \
		--out reports/openicpsr_186241/kalro_pilot_validation \
		--require-baseline --embedder hashing --n-boot 200 --seed $(SEED)
	mkdir -p $(@D)
	touch $@

openicpsr-186241-pilot-validate: $(PIPE)/openicpsr-186241/pilot-validated.stamp

$(OPENICPSR_186241_JOBS)/run-manifest.json: $(PIPE)/openicpsr-186241/ingested.stamp $(OPENICPSR_186241_STUDY)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill twin-approach add --survey kalro_input_awareness \
		--approach-id battery_context --name 'Held-out battery context' \
		--description 'Leave one question out; use the other battery responses and readable profile metadata.' \
		--complete-cases --context-question-count 8 --model $(MODEL)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill twin-approach add --survey kalro_input_use \
		--approach-id battery_context --name 'Held-out battery context' \
		--description 'Leave one question out; use the other battery responses and readable profile metadata.' \
		--complete-cases --context-question-count 8 --model $(MODEL)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill twin-approach add --survey kalro_farming_practices \
		--approach-id battery_context --name 'Held-out battery context' \
		--description 'Leave one question out; use up to eight other battery responses and readable profile metadata.' \
		--complete-cases --context-question-count 8 --model $(MODEL)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill study prepare --manifest ../$(OPENICPSR_186241_STUDY) \
		--mode by-question --out $(OPENICPSR_186241_WORKFLOW) \
		--plan-suffix validation_v1 \
		--results-dir $(OPENICPSR_186241_RESULTS) \
		--approved-by "$(REVIEWER)" \
		--note 'Approved full 20-question KALRO leave-one-out validation: 4,000 predictions.'

openicpsr-186241-full-plans openicpsr-186241-full-approve: $(OPENICPSR_186241_JOBS)/run-manifest.json
openicpsr-186241-full-export: $(OPENICPSR_186241_JOBS)/run-manifest.json

$(PIPE)/openicpsr-186241/full-inferred.stamp: $(OPENICPSR_186241_JOBS)/run-manifest.json
	export EXPECTED_PARROT_API_KEY="$$(sed -n 's/^EXPECTED_PARROT_API_KEY[[:space:]]*=[[:space:]]*//p' $(ENV_FILE))"; \
		$(EP) run-manifest $(OPENICPSR_186241_JOBS)/run-manifest.json --parallel 3 $(REMOTE_FLAG)
	test "$$(find $(OPENICPSR_186241_RESULTS) -name '*_results.ep' | wc -l | tr -d ' ')" = 20
	mkdir -p $(@D)
	touch $@

openicpsr-186241-full-infer: $(PIPE)/openicpsr-186241/full-inferred.stamp

$(OPENICPSR_186241_ONE_SHOT_JOBS)/run-manifest.json: $(PIPE)/openicpsr-186241/ingested.stamp $(OPENICPSR_186241_STUDY)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill study one-shot-build --manifest ../$(OPENICPSR_186241_STUDY) \
		--out $(OPENICPSR_186241_ONE_SHOT_JOBS) --model $(MODEL)

openicpsr-186241-full-one-shot-export: $(OPENICPSR_186241_ONE_SHOT_JOBS)/run-manifest.json

$(PIPE)/openicpsr-186241/full-one-shot-inferred.stamp: $(OPENICPSR_186241_ONE_SHOT_JOBS)/run-manifest.json
	export EXPECTED_PARROT_API_KEY="$$(sed -n 's/^EXPECTED_PARROT_API_KEY[[:space:]]*=[[:space:]]*//p' $(ENV_FILE))"; \
		$(EP) run-manifest $(OPENICPSR_186241_ONE_SHOT_JOBS)/run-manifest.json \
		--result-base $(WORK) --parallel 3 $(REMOTE_FLAG)
	test "$$(find $(OPENICPSR_186241_ONE_SHOT_RESULTS) -path '*/kalro_*/*one_shot_results.ep' | wc -l | tr -d ' ')" = 3
	mkdir -p $(@D)
	touch $@

openicpsr-186241-full-one-shot-infer: $(PIPE)/openicpsr-186241/full-one-shot-inferred.stamp

$(OPENICPSR_186241_PROBABILITY_JOBS): $(PIPE)/openicpsr-186241/full-one-shot-inferred.stamp
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill study one-shot-import \
		--manifest $(OPENICPSR_186241_ONE_SHOT_JOBS)/run-manifest.json \
		--results-dir $(OPENICPSR_186241_ONE_SHOT_RESULTS) \
		--path $(OPENICPSR_186241_PROBABILITY_JOBS) --replace

openicpsr-186241-full-one-shot-import: $(OPENICPSR_186241_PROBABILITY_JOBS)

$(PIPE)/openicpsr-186241/full-validated.stamp: $(PIPE)/openicpsr-186241/full-inferred.stamp $(OPENICPSR_186241_PROBABILITY_JOBS)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill study finish --manifest $(OPENICPSR_186241_JOBS)/run-manifest.json \
		--results-dir $(OPENICPSR_186241_RESULTS) --replace \
		--audit-out $(OPENICPSR_186241_AUDITS) \
		--out '$(OPENICPSR_186241_VALIDATION)/{survey}' \
		--embedder edsl --embedding-model $(EMBEDDING_MODEL) \
		--env-path $(ENV_FILE) --seed $(SEED) \
		--probability-jobs $(OPENICPSR_186241_PROBABILITY_JOBS)
	mkdir -p $(@D)
	touch $@

openicpsr-186241-full-import openicpsr-186241-full-audit openicpsr-186241-full-validate: $(PIPE)/openicpsr-186241/full-validated.stamp

$(OPENICPSR_186241_SUMMARY)/summary.json: $(PIPE)/openicpsr-186241/full-validated.stamp $(OPENICPSR_186241_STUDY)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill study summarize --manifest ../$(OPENICPSR_186241_STUDY) \
		--bundle-template '$(OPENICPSR_186241_VALIDATION)/{survey}' \
		--out $(OPENICPSR_186241_SUMMARY)
	cd $(WORK) && ZWILL_PROJECT=$(OPENICPSR_186241_PROJECT) \
		zwill study plot --summary-dir $(OPENICPSR_186241_SUMMARY) \
		--out $(OPENICPSR_186241_SUMMARY)/plots --model $(MODEL)

openicpsr-186241-full-summary: $(OPENICPSR_186241_SUMMARY)/summary.json

$(OPENICPSR_186241_COSTS): $(PIPE)/openicpsr-186241/full-inferred.stamp $(PIPE)/openicpsr-186241/full-one-shot-inferred.stamp
	cd $(WORK) && zwill study costs \
		--result 'results/openicpsr_186241/full/**/*_results.ep' \
		--result 'results/kalro_*/one_shot_results.ep' \
		--path $(OPENICPSR_186241_COSTS)

openicpsr-186241-full-costs: $(OPENICPSR_186241_COSTS)

openicpsr-186241-full: openicpsr-186241-full-summary openicpsr-186241-full-costs

$(GSS_JOBS)/run-manifest.json: config/gss_2024_study.json $(GSS_COMMITTED)
	cd $(WORK) && zwill twin-approach add --survey gss2024_confidence_in_institutions \
		--approach-id battery_context --name 'Held-out battery context' \
		--description 'Leave one question out; use up to eight other battery responses and readable profile metadata.' \
		--complete-cases --context-question-count 8 --model $(MODEL)
	cd $(WORK) && zwill twin-approach add --survey gss2024_socializing_frequency \
		--approach-id battery_context --name 'Held-out battery context' \
		--description 'Leave one question out; use the other battery responses and readable profile metadata.' \
		--complete-cases --context-question-count 8 --model $(MODEL)
	cd $(WORK) && zwill twin-approach add --survey gss2024_abortion_circumstances_standard_form \
		--approach-id battery_context --name 'Held-out battery context' \
		--description 'Leave one question out; use the other battery responses and readable profile metadata.' \
		--complete-cases --context-question-count 8 --model $(MODEL)
	cd $(WORK) && zwill twin-approach add --survey gss2024_government_responsibility \
		--approach-id battery_context --name 'Held-out battery context' \
		--description 'Leave one question out; use the other battery responses and readable profile metadata.' \
		--complete-cases --context-question-count 8 --model $(MODEL)
	cd $(WORK) && zwill twin-approach add --survey gss2024_national_spending_standard_form \
		--approach-id battery_context --name 'Held-out battery context' \
		--description 'Leave one question out; use up to eight other battery responses and readable profile metadata.' \
		--complete-cases --context-question-count 8 --model $(MODEL)
	cd $(WORK) && zwill study prepare --manifest ../config/gss_2024_study.json \
		--mode by-question --out $(GSS_WORKFLOW) --results-dir $(GSS_RESULTS) \
		--plan-suffix validation_v1 \
		--approved-by "$(REVIEWER)" \
		--note 'Approved 38-question GSS leave-one-out validation: 7,600 predictions.'

gss-plans gss-approve: $(GSS_JOBS)/run-manifest.json
gss-export: $(GSS_JOBS)/run-manifest.json

$(PIPE)/gss/inferred.stamp: $(GSS_JOBS)/run-manifest.json
	$(EP) run-manifest $(GSS_JOBS)/run-manifest.json --parallel 3 $(REMOTE_FLAG)
	test "$$(find $(GSS_RESULTS) -name '*_results.ep' | wc -l | tr -d ' ')" = 38
	mkdir -p $(@D)
	touch $@

gss-infer: $(PIPE)/gss/inferred.stamp

$(GSS_ONE_SHOT_JOBS)/run-manifest.json: $(GSS_COMMITTED) config/gss_2024_study.json
	cd $(WORK) && zwill study one-shot-build --manifest ../config/gss_2024_study.json \
		--out $(GSS_ONE_SHOT_JOBS) --model $(MODEL)

gss-one-shot-export: $(GSS_ONE_SHOT_JOBS)/run-manifest.json

$(PIPE)/gss/one-shot-inferred.stamp: $(GSS_ONE_SHOT_JOBS)/run-manifest.json
	$(EP) run-manifest $(GSS_ONE_SHOT_JOBS)/run-manifest.json \
		--result-base $(WORK) --parallel 3 $(REMOTE_FLAG)
	test "$$(find $(GSS_ONE_SHOT_RESULTS) -path '*/gss2024_*/*one_shot_results.ep' | wc -l | tr -d ' ')" = 5
	mkdir -p $(@D)
	touch $@

gss-one-shot-infer: $(PIPE)/gss/one-shot-inferred.stamp

$(GSS_PROBABILITY_JOBS): $(PIPE)/gss/one-shot-inferred.stamp
	cd $(WORK) && zwill study one-shot-import \
		--manifest $(GSS_ONE_SHOT_JOBS)/run-manifest.json \
		--results-dir $(GSS_ONE_SHOT_RESULTS) --path $(GSS_PROBABILITY_JOBS) --replace

gss-one-shot-import: $(GSS_PROBABILITY_JOBS)

$(PIPE)/gss/validated.stamp: $(PIPE)/gss/inferred.stamp $(GSS_PROBABILITY_JOBS)
	cd $(WORK) && zwill study finish --manifest $(GSS_JOBS)/run-manifest.json \
		--results-dir $(GSS_RESULTS) --replace --audit-out $(GSS_AUDITS) \
		--out '$(GSS_VALIDATION)/{survey}' --embedder edsl \
		--embedding-model $(EMBEDDING_MODEL) --env-path $(ENV_FILE) --seed $(SEED) \
		--probability-jobs $(GSS_PROBABILITY_JOBS)
	touch $@

gss-import gss-audit gss-validate: $(PIPE)/gss/validated.stamp

$(GSS_SUMMARY)/summary.json: $(PIPE)/gss/validated.stamp config/gss_2024_study.json
	cd $(WORK) && zwill study summarize --manifest ../config/gss_2024_study.json \
		--bundle-template '$(GSS_VALIDATION)/{survey}' --out $(GSS_SUMMARY)
	cd $(WORK) && zwill study plot --summary-dir $(GSS_SUMMARY) \
		--out $(GSS_SUMMARY)/plots --model $(MODEL)

gss-summary: $(GSS_SUMMARY)/summary.json

$(GSS_COSTS): $(PIPE)/gss/inferred.stamp $(PIPE)/gss/one-shot-inferred.stamp
	cd $(WORK) && zwill study costs \
		--result 'results/gss2024_eligible200/**/*_results.ep' \
		--result 'results/gss2024_*/one_shot_results.ep' --path $(GSS_COSTS)

gss-costs: $(GSS_COSTS)

# -----------------------------------------------------------------------------
# 2. One explicit, resumable chain for every primary plan
# -----------------------------------------------------------------------------

$(PIPE)/approved/%.stamp: $(PIPE)/offline.stamp
	mkdir -p $(@D)
	cd $(WORK) && jq -e '.approved == true' plans/$*.json >/dev/null || \
		{ test -n "$(APPROVED_BY)" && \
		  zwill twin-experiment approve --input-path plans/$*.json \
		    --approved-by "$(APPROVED_BY)" --note "Approved reproducible paper run"; }
	cd $(WORK) && zwill next --survey "$$(jq -r .survey plans/$*.json)" >/dev/null
	touch $@

$(PIPE)/exported/%.stamp: $(PIPE)/approved/%.stamp
	mkdir -p $(@D)
	cd $(WORK) && test -f jobs/$*/manifest.json || \
		zwill twin-experiment export-plan \
		  --input-path plans/$*.json --output-dir jobs/$*
	cd $(WORK) && zwill next --survey "$$(jq -r .survey plans/$*.json)" >/dev/null
	touch $@

# Each registered plan has one exported .ep package. The assertion makes that
# design assumption visible and prevents silently ignoring an added arm.
$(PIPE)/inferred/%.stamp: $(PIPE)/exported/%.stamp
	mkdir -p $(@D) $(WORK)/results/$*
	cd $(WORK) && test "$$(jq '.exports | length' jobs/$*/manifest.json)" -eq 1
	cd $(WORK) && job="$$(jq -r '.exports[0].job_path' jobs/$*/manifest.json)"; \
		result="results/$*/$$(basename "$$job" _jobs.ep)_results.ep"; \
		test -f "$$result" || \
		  { test "$(ALLOW_REMOTE)" = 1 && ep run "$$job" --output "$$result"; }
	touch $@

$(PIPE)/imported/%.stamp: $(PIPE)/inferred/%.stamp
	mkdir -p $(@D)
	cd $(WORK) && zwill twin-experiment import-plan-results \
		--manifest jobs/$*/manifest.json --results-dir results/$* --replace >/dev/null
	cd $(WORK) && zwill next --survey "$$(jq -r .survey plans/$*.json)" >/dev/null
	touch $@

$(PIPE)/audited/%.stamp: $(PIPE)/imported/%.stamp
	mkdir -p $(@D)
	cd $(WORK) && survey="$$(jq -r .survey plans/$*.json)"; \
		job_id="$$(jq -r '.exports[0].job_id' jobs/$*/manifest.json)"; \
		report="reports/$$survey/audit/run-$$job_id.html"; \
		test -f "$$report" || \
		  zwill twin-results run-report --survey "$$survey" --job-id "$$job_id" \
		    --format html --path "$$report"
	cd $(WORK) && zwill next --survey "$$(jq -r .survey plans/$*.json)" >/dev/null
	touch $@

$(PIPE)/validated/%.stamp: $(PIPE)/audited/%.stamp
	mkdir -p $(@D)
	cd $(WORK) && survey="$$(jq -r .survey plans/$*.json)"; \
		job_id="$$(jq -r '.exports[0].job_id' jobs/$*/manifest.json)"; \
		bundle="zwill_work/reports/$$survey/validation-exact/manifest.json"; \
		test -f "$$bundle" || \
		  zwill twin-validate --survey "$$survey" --jobs "$$job_id" \
		    --out "reports/$$survey/validation-exact" --require-baseline \
		    --embedder edsl --embedding-model $(EMBEDDING_MODEL) \
		    --env-path ../../../.env --seed $(SEED)
	cd $(WORK) && zwill next --survey "$$(jq -r .survey plans/$*.json)" >/dev/null
	touch $@

$(PIPE)/reported/%.stamp: $(PIPE)/validated/%.stamp
	mkdir -p $(@D)
	cd $(WORK) && survey="$$(jq -r .survey plans/$*.json)"; \
		job_id="$$(jq -r '.exports[0].job_id' jobs/$*/manifest.json)"; \
		index="zwill_work/reports/$$survey/final/index.html"; \
		test -f "$$index" || \
		  { zwill report build --survey "$$survey" --jobs "$$job_id" \
		      --path "reports/$$survey/final" --seed $(SEED); \
		    zwill report render --survey "$$survey" --jobs "$$job_id" \
		      --path "reports/$$survey/final" --seed $(SEED); }
	cd $(WORK) && zwill next --survey "$$(jq -r .survey plans/$*.json)" >/dev/null
	touch $@

approve: $(APPROVED)
export: $(EXPORTED)
infer: $(INFERRED)
import: $(IMPORTED)
audit: $(AUDITED)
validate: $(VALIDATED)
reports: $(REPORTED)

# -----------------------------------------------------------------------------
# 3. Predeclared 200-call Gallup leakage correction
# -----------------------------------------------------------------------------

$(PIPE)/corrective-plan.stamp: $(PIPE)/offline.stamp
	cd $(WORK) && test -f plans/$(CORRECTIVE_PLAN).json || \
		test ! -f zwill_work/plans/$(CORRECTIVE_PLAN).json || \
		cp zwill_work/plans/$(CORRECTIVE_PLAN).json plans/$(CORRECTIVE_PLAN).json
	cd $(WORK) && test -f plans/$(CORRECTIVE_PLAN).json || \
		zwill twin-approach add --survey $(CORRECTIVE_SURVEY) \
		  --approach-id battery_context_leakage_fixed \
		  --name "Battery context with target-specific leakage exclusion" \
		  --description "Exclude q18rec when q6rec is the target." \
		  --complete-cases --context-question-count 8 --model $(MODEL) \
		  --leakage-exclusion gallup_remote_covid_q6rec:gallup_remote_covid_q18rec
	cd $(WORK) && test -f plans/$(CORRECTIVE_PLAN).json || \
		zwill twin-experiment init-plan --survey $(CORRECTIVE_SURVEY) \
		  --plan-id $(CORRECTIVE_PLAN) --heldout-question gallup_remote_covid_q6rec \
		  --approach-id battery_context_leakage_fixed --sample-respondents 200 \
		  --seed $(SEED) --context-question-count 8 --model $(MODEL) \
		  --leakage-exclusion gallup_remote_covid_q6rec:gallup_remote_covid_q18rec \
		  --primary-metric nll_vs_empirical --path plans/$(CORRECTIVE_PLAN).json
	cd $(WORK) && zwill twin-experiment validate \
		--input-path plans/$(CORRECTIVE_PLAN).json >/dev/null
	touch $@

# Approval, export, inference, and import use the same plan chain as the primary
# runs. Audit and validation use distinct output paths so they cannot overwrite
# the primary Gallup report.
$(PIPE)/approved/$(CORRECTIVE_PLAN).stamp: $(PIPE)/corrective-plan.stamp

$(PIPE)/corrective.stamp: $(PIPE)/imported/$(CORRECTIVE_PLAN).stamp
	cd $(WORK) && job_id="$$(jq -r '.exports[0].job_id' \
		jobs/$(CORRECTIVE_PLAN)/manifest.json)"; \
		zwill twin-results run-report --survey $(CORRECTIVE_SURVEY) \
		  --job-id "$$job_id" --format html \
		  --path "reports/$(CORRECTIVE_SURVEY)/audit/run-corrective-$$job_id.html"; \
		zwill twin-validate --survey $(CORRECTIVE_SURVEY) --jobs "$$job_id" \
		  --out "reports/$(CORRECTIVE_SURVEY)/validation-leakage-fixed-matched" \
		  --require-baseline --embedder edsl \
		  --embedding-model $(EMBEDDING_MODEL) --env-path ../../../.env --seed $(SEED)
	cd $(WORK) && zwill next --survey $(CORRECTIVE_SURVEY) >/dev/null
	touch $@

corrective: $(PIPE)/corrective.stamp

# -----------------------------------------------------------------------------
# 4. One-target-at-a-time companion design (69 independently approved plans)
# -----------------------------------------------------------------------------

$(PIPE)/by-question-plans.stamp: $(PIPE)/corrective-plan.stamp
	cd $(WORK) && zwill study prepare --manifest study_manifest.json \
		--mode by-question --out $(BYQ_WORKFLOW) --results-dir $(WORK)/results/by_question \
		--approved-by "$(REVIEWER)" --note 'Approved reproducible per-question paper benchmark.' \
		--approach-id battery_context --sample-respondents 200 --seed $(SEED) \
		--context-question-count 8 --model $(MODEL)
	touch $@

$(PIPE)/by-question-infer.stamp: $(PIPE)/by-question-plans.stamp
	$(EP) run-manifest $(BYQ_WORKFLOW)/jobs/run-manifest.json --parallel 3 $(REMOTE_FLAG)
	touch $@

$(PIPE)/by-question-validate.stamp: $(PIPE)/by-question-infer.stamp
	cd $(WORK) && zwill study finish --manifest $(BYQ_WORKFLOW)/jobs/run-manifest.json \
		--results-dir results/by_question --replace \
		--audit-out $(WORK)/zwill_work/reports/by-question-audits \
		--out '$(WORK)/zwill_work/reports/{survey}/validation-by-question-exact' \
		--embedder edsl --embedding-model $(EMBEDDING_MODEL) \
		--env-path $(ENV_FILE) --seed $(SEED)
	touch $@

by-question-plans: $(PIPE)/by-question-plans.stamp
by-question-infer: $(PIPE)/by-question-infer.stamp
by-question-import by-question-audit: $(PIPE)/by-question-validate.stamp
by-question-validate: $(PIPE)/by-question-validate.stamp

$(PIPE)/model-plans.stamp: $(PIPE)/by-question-plans.stamp
	cd $(WORK) && zwill study prepare --manifest study_manifest.json \
		--comparison frontier_models --out $(MODEL_WORKFLOW) \
		--results-dir $(WORK)/results/model_comparison_v2 \
		--approved-by "$(REVIEWER)" --note 'Approved matched multi-model paper comparison.'
	touch $@

$(PIPE)/model-infer.stamp: $(PIPE)/model-plans.stamp
	$(EP) run-manifest $(MODEL_WORKFLOW)/jobs/run-manifest.json --parallel 3 $(REMOTE_FLAG)
	touch $@

$(PIPE)/model-validate.stamp: $(PIPE)/model-infer.stamp
	cd $(WORK) && zwill study finish --manifest $(MODEL_WORKFLOW)/jobs/run-manifest.json \
		--results-dir results/model_comparison_v2 --replace \
		--audit-out $(WORK)/zwill_work/reports/model-comparison-audits \
		--include-manifest $(BYQ_WORKFLOW)/jobs/run-manifest.json \
		--out '$(WORK)/zwill_work/reports/{survey}/validation-model-comparison' \
		--embedder edsl --embedding-model $(EMBEDDING_MODEL) \
		--env-path $(ENV_FILE) --seed $(SEED)
	touch $@

model-plans: $(PIPE)/model-plans.stamp
model-infer: $(PIPE)/model-infer.stamp
model-import model-audit: $(PIPE)/model-validate.stamp
model-validate: $(PIPE)/model-validate.stamp

# -----------------------------------------------------------------------------
# 5. Same-model, no-persona one-shot marginals (one resumable job per battery)
# -----------------------------------------------------------------------------

$(PIPE)/one-shot/jobs.stamp: $(PIPE)/offline.stamp
	mkdir -p $(@D) $(WORK)/jobs/one-shot
	cd $(WORK) && zwill study one-shot-build --manifest study_manifest.json \
		--out '$(WORK)/jobs/one-shot' --model $(MODEL) >/dev/null
	touch $@

$(PIPE)/one-shot/exported/%.stamp: $(PIPE)/one-shot/jobs.stamp
	test -f $(WORK)/jobs/one-shot/$*/one_shot_jobs.ep
	mkdir -p $(@D)
	touch $@

$(PIPE)/one-shot/inferred/%.stamp: $(PIPE)/one-shot/exported/%.stamp
	mkdir -p $(@D) $(WORK)/results/one-shot/$* $(WORK)/remote_jobs/one-shot
	cd $(WORK) && test -f results/one-shot/$*/one_shot_results.ep || \
		jq -e '.data.meta.remote_job.job_uuid' remote_jobs/one-shot/$*.json >/dev/null 2>&1 || \
		{ test "$(ALLOW_REMOTE)" = 1 && \
		  set -a && source $(ENV_FILE) && set +a && \
		  $(EP) run --jobs jobs/one-shot/$*/one_shot_jobs.ep --background \
		    > remote_jobs/one-shot/$*.json; }
	cd $(WORK) && while test ! -f results/one-shot/$*/one_shot_results.ep; do \
		job_uuid="$$(jq -r '.data.meta.remote_job.job_uuid' remote_jobs/one-shot/$*.json)"; \
		status="$$( set -a; source $(ENV_FILE); set +a; \
		  $(EP) jobs status "$$job_uuid" | jq -r .data.status )"; \
		if test "$$status" = completed; then \
		  set -a; source $(ENV_FILE); set +a; \
		  $(EP) jobs results "$$job_uuid" \
		    --output results/one-shot/$*/one_shot_results.ep; \
		elif test "$$status" = failed -o "$$status" = cancelled; then \
		  echo "Remote one-shot job $$job_uuid ended with $$status" >&2; exit 1; \
		else sleep 10; fi; \
	done
	touch $@

$(PIPE)/one-shot/imported-all.stamp: $(ONE_SHOT_INFERRED)
	mkdir -p $(@D) $(WORK)/reports/one-shot
	cd $(WORK) && zwill study one-shot-import \
		--manifest jobs/one-shot/run-manifest.json --results-dir results/one-shot \
		--path '$(WORK)/reports/one-shot/probability-jobs.json' --replace >/dev/null
	touch $@

$(PIPE)/one-shot/imported/%.stamp: $(PIPE)/one-shot/imported-all.stamp
	jq -e --arg survey '$*' '.[$$survey]' $(WORK)/reports/one-shot/probability-jobs.json >/dev/null
	mkdir -p $(@D)
	touch $@

$(PIPE)/one-shot/reported/%.stamp: $(PIPE)/one-shot/imported/%.stamp
	mkdir -p $(@D)
	cd $(WORK) && job_id="$$(jq -r --arg survey '$*' '.[$$survey]' reports/one-shot/probability-jobs.json)"; \
		zwill prob-results report --survey $* --job-id "$$job_id" \
		  --format json --path reports/one-shot/$*/marginals.json >/dev/null; \
		zwill prob-results report --survey $* --job-id "$$job_id" \
		  --format csv --path reports/one-shot/$*/marginals.csv >/dev/null; \
		zwill prob-results report --survey $* --job-id "$$job_id" \
		  --format svg --path reports/one-shot/$*/marginals.svg >/dev/null
	cd $(WORK) && zwill next --survey $* >/dev/null
	touch $@

one-shot-export: $(ONE_SHOT_EXPORTED)
one-shot-infer: $(ONE_SHOT_INFERRED)
one-shot-import: $(ONE_SHOT_IMPORTED)
one-shot-reports: $(ONE_SHOT_REPORTED)

# Re-score the imported one-shot distribution on the exact respondent-question
# cases used by the twins. This adds a derived baseline arm and respondent-
# bootstrap intervals; it does not make any new language-model calls.
$(PIPE)/one-shot/validated.stamp: $(ONE_SHOT_IMPORTED) $(PIPE)/by-question-import.stamp
	mkdir -p $(@D)
	cd $(WORK) && zwill study validate --manifest jobs/by_question/run-manifest.json \
		--probability-jobs reports/one-shot/probability-jobs.json \
		--out '$(WORK)/zwill_work/reports/{survey}/validation-by-question-exact' \
		--embedder edsl --embedding-model $(EMBEDDING_MODEL) \
		--env-path ../../../.env --seed $(SEED) >/dev/null
	touch $@

one-shot-validate: $(PIPE)/one-shot/validated.stamp

# Cross-battery wrangling is a zwill feature. The paper scripts consume these
# tidy, versioned exports and retain only presentation-specific plotting/LaTeX.
$(PIPE)/study-summaries.stamp: $(VALIDATED) $(PIPE)/one-shot/validated.stamp \
	$(PIPE)/model-validate.stamp
	mkdir -p $(@D)
	cd $(WORK) && zwill study summarize --manifest study_manifest.json \
		--bundle-template 'zwill_work/reports/{survey}/validation-exact' \
		--out '$(WORK)/study/simultaneous'
	cd $(WORK) && zwill study summarize --manifest study_manifest.json \
		--bundle-template 'zwill_work/reports/{survey}/validation-by-question-exact' \
		--out '$(WORK)/study/by-question'
	cd $(WORK) && zwill study summarize --manifest study_manifest.json \
		--bundle-template 'zwill_work/reports/{survey}/validation-model-comparison' \
		--out '$(WORK)/study/model-comparison' --allow-missing
	cd $(WORK) && zwill study plot --summary-dir '$(WORK)/study/by-question' \
		--out '$(WORK)/study/plots' --model $(MODEL)
	touch $@

study-summaries: $(PIPE)/study-summaries.stamp

$(PIPE)/semantic-proximity.stamp: $(PIPE)/study-summaries.stamp $(PIPE)/by-question-import.stamp \
	$(GSS_SUMMARY)/summary.json
	mkdir -p $(@D)
	test "$(ALLOW_REMOTE)" = 1
	cd $(WORK) && zwill study semantic-proximity \
		--manifest jobs/by_question/run-manifest.json \
		--manifest jobs/gss2024_eligible200/run-manifest.json \
		--questions '$(WORK)/study/by-question/questions.csv' \
		--questions '$(GSS_SUMMARY)/questions.csv' \
		--out '$(WORK)/study/semantic-proximity' --model $(MODEL) \
		--embedder edsl --embedding-model $(EMBEDDING_MODEL) \
		--env-path ../../../.env --seed $(SEED) --n-boot 10000
	touch $@

semantic-proximity: $(PIPE)/semantic-proximity.stamp

$(PIPE)/costs.stamp: $(ONE_SHOT_INFERRED) $(PIPE)/model-infer.stamp \
	$(PIPE)/by-question-infer.stamp
	cd $(WORK) && zwill study costs --manifest study_manifest.json \
		--path '$(WORK)/study/costs.json'
	cd $(WORK) && zwill study costs --result 'results/one-shot/**/*_results.ep' \
		--path '$(WORK)/study/one-shot-costs.json'
	touch $@

costs: $(PIPE)/costs.stamp

$(PIPE)/one-shot/summary.stamp: analysis/summarize_one_shot.py $(ONE_SHOT_REPORTED) \
	$(PIPE)/study-summaries.stamp
	mkdir -p $(@D) output/figures writeup/generated /tmp/zwill-mpl
	MPLCONFIGDIR=/tmp/zwill-mpl python analysis/summarize_one_shot.py
	touch $@

one-shot-summary: $(PIPE)/one-shot/summary.stamp

# -----------------------------------------------------------------------------
# 6. Deterministic tables, plots, and manuscript
# -----------------------------------------------------------------------------

$(PIPE)/summary.stamp: analysis/summarize_validation.py $(PIPE)/study-summaries.stamp \
	$(PIPE)/semantic-proximity.stamp $(PIPE)/costs.stamp $(GSS_COSTS) \
	$(OPENICPSR_186241_SUMMARY)/summary.json $(OPENICPSR_186241_COSTS)
	mkdir -p $(PIPE) output/figures writeup/generated /tmp/zwill-mpl
	MPLCONFIGDIR=/tmp/zwill-mpl python analysis/summarize_validation.py
	touch $@

summary: $(PIPE)/summary.stamp
live: reports corrective by-question-validate model-validate one-shot-reports one-shot-validate study-summaries costs

writeup/generated/cohort.tex: $(PIPE)/offline.stamp
	test -f $@

writeup/generated/validation.tex writeup/generated/model_comparison.tex \
writeup/generated/exclusions.tex writeup/generated/gss_extension.tex \
writeup/generated/kalro_extension.tex \
writeup/generated/results_macros.tex output/figures/nll_change.pdf \
	output/figures/marginal_fit.pdf output/figures/benchmark_ladder.pdf \
	output/figures/calibration_comparison.pdf output/figures/model_comparison.pdf \
	output/figures/semantic_proximity.pdf output/figures/gss_extension.pdf \
	output/figures/kalro_extension.pdf: \
	$(PIPE)/summary.stamp
	test -f $@

writeup/generated/one_shot_table.tex writeup/generated/one_shot_macros.tex \
	output/figures/one_shot_marginal_fit.pdf \
	output/figures/one_shot_nll_lift.pdf: $(PIPE)/one-shot/summary.stamp
	test -f $@

output/$(PAPER).pdf: writeup/$(PAPER).tex ../../docs/assets/zwill-package.png \
	writeup/generated/cohort.tex \
	writeup/generated/validation.tex writeup/generated/model_comparison.tex \
	writeup/generated/exclusions.tex writeup/generated/gss_extension.tex \
	writeup/generated/kalro_extension.tex \
	writeup/generated/results_macros.tex \
	writeup/generated/one_shot_table.tex writeup/generated/one_shot_macros.tex \
	writeup/references.bib output/figures/nll_change.pdf \
	output/figures/marginal_fit.pdf output/figures/benchmark_ladder.pdf \
	output/figures/calibration_comparison.pdf output/figures/model_comparison.pdf \
	output/figures/semantic_proximity.pdf output/figures/gss_extension.pdf \
	output/figures/kalro_extension.pdf \
	output/figures/one_shot_marginal_fit.pdf output/figures/one_shot_nll_lift.pdf
	mkdir -p output output/texcache
	cd writeup && $(TEX) $(PAPER).tex
	cd writeup && bibtex $(PAPER)
	cd writeup && $(TEX) $(PAPER).tex
	cd writeup && $(TEX) $(PAPER).tex
	cp writeup/$(PAPER).pdf $@

# Run summary again after the corrective result exists, then rebuild the PDF.
reproduce: live
	MPLCONFIGDIR=/tmp/zwill-mpl python analysis/summarize_validation.py
	MPLCONFIGDIR=/tmp/zwill-mpl python analysis/summarize_one_shot.py
	rm -f output/$(PAPER).pdf
	$(MAKE) --no-print-directory paper

check: $(PIPE)/offline.stamp
	$(MAKE) --no-print-directory -n reproduce \
		ALLOW_REMOTE=1 APPROVED_BY=reproducer >/dev/null
	jq -e '.total_planned_predictions == 13800' \
		$(WORK)/study_manifest.json >/dev/null
	@echo "Make graph and 13,800-call primary design check passed."

clean:
	rm -f writeup/$(PAPER).aux writeup/$(PAPER).log writeup/$(PAPER).out \
		writeup/$(PAPER).pdf output/$(PAPER).pdf
