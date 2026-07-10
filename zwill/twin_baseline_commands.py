from __future__ import annotations

from .cli import *  # noqa: F403
from .twin import normalize_name_list
from .twin_baseline import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    MODEL_LABEL,
    Embedder,
    baseline_job_id,
    build_conditional_baseline_predictions,
    edsl_embedder,
    hashing_embedder,
    openai_embedder,
    sentence_transformers_available,
    sentence_transformers_embedder,
)

# How long to wait for a one-text health probe before deciding the Expected
# Parrot embeddings endpoint is unavailable and failing over to a local backend.
EP_PROBE_TIMEOUT_SECONDS = 20.0


def _probe_embedder(factory: Any, *, timeout: float) -> Embedder | None:
    """Return the embedder if a tiny probe embed succeeds within ``timeout``.

    Runs the probe on a daemon thread so a hung/slow endpoint fails over quickly
    (bounded by ``timeout``) instead of stalling the whole validation, and never
    blocks process exit. Returns None on error, timeout, or an empty result.
    """
    import threading

    try:
        embedder = factory()
    except Exception:  # pragma: no cover - construction rarely fails
        return None
    result: dict[str, Any] = {}

    def _run() -> None:
        try:
            result["vectors"] = embedder(["health check"])
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive() or "error" in result or not result.get("vectors"):
        return None
    return embedder


def resolve_baseline_embedder(args: Any, embedding_model: str) -> Embedder:
    """Pick the embedding backend for the conditional baseline.

    `--embedder auto` (the default) tries the Expected Parrot embeddings endpoint
    first (behind a bounded health probe, so an unavailable endpoint fails over in
    seconds instead of hanging the validation), then a direct OpenAI key, then a
    local sentence-transformers model, then a zero-dependency built-in lexical
    embedder that always works (weaker -- it leans on covariates). `edsl`,
    `openai`, `sentence-transformers`/`local`, and `hashing`/`lexical` force a
    backend (`edsl` with no failover).
    """
    choice = (getattr(args, "embedder", None) or "auto").lower()
    # A local sentence-transformers model uses its own default, not the OpenAI one.
    local_model = embedding_model if embedding_model != DEFAULT_EMBEDDING_MODEL else DEFAULT_LOCAL_EMBEDDING_MODEL
    if choice in {"sentence-transformers", "sentence_transformers", "sentence", "local", "st"}:
        return sentence_transformers_embedder(model=local_model)
    if choice in {"edsl", "expected-parrot", "ep", "remote"}:
        return edsl_embedder(model=embedding_model)
    if choice == "openai":
        return openai_embedder(model=embedding_model)
    if choice in {"hashing", "lexical", "builtin", "hash"}:
        return hashing_embedder()
    # auto -- Expected Parrot endpoint first, but health-probed so it can't hang.
    if os.environ.get("EXPECTED_PARROT_API_KEY"):
        remote = _probe_embedder(
            lambda: edsl_embedder(model=embedding_model), timeout=EP_PROBE_TIMEOUT_SECONDS
        )
        if remote is not None:
            return remote
        print(
            "warning: the Expected Parrot embeddings endpoint did not respond within "
            f"{EP_PROBE_TIMEOUT_SECONDS:.0f}s; falling back to a local embedder for the conditional baseline.",
            file=sys.stderr,
        )
    if os.environ.get("OPENAI_API_KEY"):
        return openai_embedder(model=embedding_model)
    if sentence_transformers_available():
        return sentence_transformers_embedder(model=local_model)
    print(
        "warning: no semantic embedding backend available; using the built-in lexical embedder "
        "for the conditional baseline (weaker — it leans on covariates). Install "
        "`zwill[local-embeddings]` or set OPENAI_API_KEY for a semantic baseline, or pass "
        "`--embedder sentence-transformers` once installed.",
        file=sys.stderr,
    )
    return hashing_embedder()


def selected_baseline_heldout_questions(args: Any, questions: list[dict[str, Any]]) -> list[str]:
    values = normalize_name_list(getattr(args, "heldout_question", None))
    values += normalize_name_list(getattr(args, "heldout_questions", None))
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
    active_embedder = embedder or resolve_baseline_embedder(args, embedding_model)

    job_id = args.job_id or baseline_job_id(args.survey, heldout_questions, respondent_ids, embedding_model)
    jdir = digital_twin_jobs_dir(sdir) / job_id
    if jdir.exists() and not getattr(args, "replace", False):
        raise ZwillError(
            "already_exists",
            f"Baseline predictions already exist for job id {job_id}.",
            hint="Use --replace to overwrite.",
        )

    # Respondent covariates (panel metadata) feed the baseline as features.
    metadata_by_respondent = {
        str(row["respondent_id"]): row["metadata"]
        for row in respondents
        if isinstance(row.get("metadata"), dict)
    }

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
        metadata_by_respondent=metadata_by_respondent,
        embedding_model=embedding_model,
        l2=float(getattr(args, "l2", 1.0) or 1.0),
    )
    # Carry each respondent's survey weight so population-level metrics and the
    # bootstrap weight the baseline the same way they weight the twin.
    weight_by_respondent = {
        str(row["respondent_id"]): float(row.get("weight", 1.0))
        for row in respondents
        if row.get("respondent_id") is not None
    }
    for row in rows:
        row["weight"] = weight_by_respondent.get(str(row.get("respondent_id")), 1.0)
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
        rewrite_jsonl(resolve_output_path(args.path), rows)

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
            "model_type": meta.get("model_type"),
            "covariate_features": meta.get("covariate_features"),
            "feature_dimension": meta.get("feature_dimension"),
            "skipped_no_actual": meta["skipped_no_actual"],
            "skipped_no_profile": meta["skipped_no_profile"],
        },
        next_steps=[
            f"zwill twin-results report --survey {args.survey} --job-id {job_id} --format html --path baseline_report.html",
        ],
    )
