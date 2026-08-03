# Tinker Pew DIFF1 Fine-Tuning Experiment

This folder is a self-contained Tinker experiment for the Pew W154 DIFF1 example.
It uses only the prepared files under `../workdir/imports/`.

The experiment fine-tunes a LoRA adapter to predict held-out DIFF1 answers from a
respondent's other DIFF1 answers. The target answer is never included in the
prompt for that example.

## Setup

```bash
uv pip install tinker tinker-cookbook
export TINKER_API_KEY=...
```

The parent folder already has `.env` with `TINKER_API_KEY`; `run_tinker_sft.py`
will load it if the key is not already exported.

## Prepare Data

```bash
python tinker_experiment/prepare_tinker_data.py \
  --imports-dir workdir/imports \
  --out-dir tinker_experiment/runs/diff1_ce \
  --heldout-questions diff1_c,diff1_e \
  --train-respondents 1000 \
  --eval-respondents 300 \
  --seed 154
```

Outputs:

- `train_conversations.jsonl`: chat conversations for SFT.
- `eval_prompts.jsonl`: prompts, actual labels, options, and baseline labels.
- `manifest.json`: split counts, target questions, options, and baselines.

## Run Tinker SFT

```bash
python tinker_experiment/run_tinker_sft.py \
  --run-dir tinker_experiment/runs/diff1_ce \
  --model Qwen/Qwen3.5-4B \
  --renderer qwen3_5 \
  --lora-rank 16 \
  --learning-rate 0.0002 \
  --epochs 1 \
  --batch-size 16 \
  --max-eval-examples 200
```

The script writes:

- `training_metrics.jsonl`
- `predictions.jsonl`
- `run_summary.json`

## Score Results

```bash
python tinker_experiment/score_predictions.py \
  --run-dir tinker_experiment/runs/diff1_ce
```

This writes `score_report.json` with overall and per-question accuracy, plus
train-marginal and uniform baselines.

## Notes

- Respondent metadata in `workdir/imports/respondents.jsonl` is still coded, so
  this experiment deliberately excludes it. DIFF1 answer labels are already
  codebook-expanded human-readable labels.
- This is a supervised fine-tune, not a full digital twin validation bundle.
  It is useful for testing whether a tuned open model can learn respondent-level
  answer patterns in this five-item battery.
