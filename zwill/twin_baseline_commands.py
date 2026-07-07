from __future__ import annotations

from .cli import *  # noqa: F403
from .twin_baseline import (
    DEFAULT_EMBEDDING_MODEL,
    MODEL_LABEL,
    Embedder,
    baseline_job_id,
    build_conditional_baseline_predictions,
    openai_embedder,
)


def selected_baseline_heldout_questions(args: Any, questions: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    heldout_question = getattr(args, "heldout_question", None)
    if isinstance(heldout_question, list):
        values.extend(heldout_question)
    elif heldout_question:
        values.append(heldout_question)
    if getattr(args, "heldout_questions", None):
        values.extend(name.strip() for name in args.heldout_questions.split(",") if name.strip())
    if not values:
        raise ZwillError("invalid_input", "--heldout-question is required for the conditional baseline.")
    available = {question["question_name"] for question in questions}
    unknown = [name for name in values if name not in available]
    if unknown:
        raise ZwillError(
            "invalid_input",
            "Unknown held-out question for conditional baseline.",
            context={"unknown_questions": unknown},
        )
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def cmd_twin_baseline_run(args: argparse.Namespace, *, embedder: Embedder | None = None) -> dict[str, Any]:
    sdir = require_survey(args.survey)
    questions = read_jsonl(sdir / "questions.jsonl")
    respondents = read_jsonl(sdir / "respondents.jsonl")
    answer_rows = read_jsonl(sdir / "answers.jsonl")
    if not questions or not answer_rows:
        raise ZwillError("invalid_input", "Survey needs imported questions and answers before running the baseline.")

    answers_by_respondent: dict[str, dict[str, str]] = defaultdict(dict)
    for row in answer_rows:
        if row.get("answer") is None:
            continue
        answers_by_respondent[row["respondent_id"]][row["question"]] = row["answer"]

    heldout_questions = selected_baseline_heldout_questions(args, questions)

    restrict = getattr(args, "restrict_respondent_ids", None)
    if restrict:
        # Score the baseline on exactly these respondents (e.g. a twin job's set),
        # so a unified report compares every model on the same people.
        restrict_set = {str(rid) for rid in restrict}
        respondent_ids = sorted(rid for rid in answers_by_respondent if str(rid) in restrict_set)
    else:
        respondent_ids = [row["respondent_id"] for row in respondents] or sorted(answers_by_respondent)
        if getattr(args, "sample_respondents", None):
            rng = random.Random(getattr(args, "seed", None))
            pool = [rid for rid in respondent_ids if answers_by_respondent.get(rid)]
            rng.shuffle(pool)
            respondent_ids = sorted(pool[: args.sample_respondents])

    truth_path = sdir / "committed" / "truth_marginals.json"
    truth = read_json(truth_path, {}) if truth_path.exists() else {}

    embedding_model = getattr(args, "embedding_model", None) or DEFAULT_EMBEDDING_MODEL
    active_embedder = embedder or openai_embedder(model=embedding_model)

    job_id = args.job_id or baseline_job_id(args.survey, heldout_questions, respondent_ids, embedding_model)
    jdir = digital_twin_jobs_dir(sdir) / job_id
    if jdir.exists() and not getattr(args, "replace", False):
        raise ZwillError(
            "already_exists",
            f"Baseline predictions already exist for job id {job_id}.",
            hint="Use --replace to overwrite.",
        )

    imported_at = utc_now()
    rows, meta = build_conditional_baseline_predictions(
        survey=args.survey,
        questions=questions,
        answers_by_respondent=answers_by_respondent,
        respondent_ids=respondent_ids,
        heldout_questions=heldout_questions,
        truth=truth,
        embedder=active_embedder,
        job_id=job_id,
        imported_at=imported_at,
        embedding_model=embedding_model,
        l2=float(getattr(args, "l2", 1.0) or 1.0),
    )
    if not rows:
        raise ZwillError(
            "invalid_input",
            "Baseline produced no predictions.",
            context={"skipped_no_actual": meta["skipped_no_actual"], "skipped_no_profile": meta["skipped_no_profile"]},
        )

    predictions_path = digital_twin_predictions_path(sdir)
    existing = [row for row in read_jsonl(predictions_path) if row.get("job_id") != job_id]
    rewrite_jsonl(predictions_path, existing + rows)

    jdir.mkdir(parents=True, exist_ok=True)
    write_json(jdir / "import.json", {**meta, "survey": args.survey, "imported_at": imported_at})
    upsert_twin_run_manifest(
        sdir,
        {
            "job_id": job_id,
            "survey": args.survey,
            "status": "imported",
            "created_at": imported_at,
            "model_label": MODEL_LABEL,
            "kind": "conditional-baseline",
        },
    )

    if getattr(args, "path", None):
        rewrite_jsonl(Path(args.path), rows)

    return envelope(
        "zwill twin-baseline run",
        "ok",
        {
            "job_id": job_id,
            "model_label": MODEL_LABEL,
            "embedding_model": embedding_model,
            "prediction_rows": meta["prediction_rows"],
            "training_rows": meta["training_rows"],
            "scored_questions": meta["heldout_questions"],
            "unscored_questions": meta["unscored_questions"],
            "feature_weights_by_question": meta["feature_weights_by_question"],
            "skipped_no_actual": meta["skipped_no_actual"],
            "skipped_no_profile": meta["skipped_no_profile"],
        },
        next_steps=[
            f"zwill twin-results report --survey {args.survey} --job-id {job_id} --format html --path baseline_report.html",
        ],
    )
