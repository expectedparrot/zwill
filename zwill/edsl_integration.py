from __future__ import annotations

from .cli import *  # noqa: F403
from .costs import estimate_job_cost_summary, results_cost_summary
from .twin import normalize_name_list


def _cli():
    from . import cli

    return cli


def load_edsl_classes() -> tuple[Any, Any, Any, Any]:
    os.environ.setdefault("EDSL_LOG_DIR", str((ROOT / "edsl_logs").resolve()))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from edsl import Agent, AgentList, Question, Survey
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "Could not import EDSL.",
            hint="Install EDSL or make sure ~/tools/ep/edsl is available in this Python environment.",
        ) from exc
    return Agent, AgentList, Question, Survey


def load_edsl_job_classes() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    os.environ.setdefault("EDSL_LOG_DIR", str((ROOT / "edsl_logs").resolve()))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from edsl import Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "Could not import EDSL job dependencies.",
            hint="Install EDSL or make sure ~/tools/ep/edsl is available in this Python environment.",
        ) from exc
    return Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey


def load_edsl_agent_study_classes() -> tuple[Any, Any, Any, Any, Any, Any]:
    os.environ.setdefault("EDSL_LOG_DIR", str((ROOT / "edsl_logs").resolve()))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from edsl import AgentList, Jobs, Model, ModelList, Question, Survey
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "Could not import EDSL agent-study dependencies.",
            hint="Install EDSL or make sure ~/tools/ep/edsl is available in this Python environment.",
        ) from exc
    return AgentList, Jobs, Model, ModelList, Question, Survey


def load_edsl_runner_classes() -> tuple[Any, Any]:
    os.environ.setdefault("EDSL_LOG_DIR", str((ROOT / "edsl_logs").resolve()))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from edsl import Jobs
            from edsl.jobs.data_structures import RunParameters
    except ImportError as exc:
        raise ZwillError(
            "missing_dependency",
            "Could not import EDSL runner dependencies.",
            hint="Install EDSL or make sure ~/tools/ep/edsl is available in this Python environment.",
        ) from exc
    return Jobs, RunParameters


def edsl_question_from_zwill(question: dict[str, Any], Question: Any) -> Any:
    kwargs = {
        "question_name": question["question_name"],
        "question_text": question["question_text"],
    }
    if question.get("question_options"):
        kwargs["question_options"] = question["question_options"]
    try:
        return Question(question["question_type"], **kwargs)
    except Exception as exc:
        if question.get("question_type") in {"rank", "ranking"}:
            try:
                from edsl import QuestionRank

                return QuestionRank(**kwargs)
            except Exception:
                pass
        raise ZwillError(
            "edsl_export_failed",
            f"Could not convert question {question['question_name']} to EDSL.",
            context={
                "question_name": question.get("question_name"),
                "question_type": question.get("question_type"),
                "error": str(exc),
            },
        ) from exc


def build_edsl_survey_dict(survey_name: str) -> dict[str, Any]:
    sdir = require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    _, _, Question, Survey = _cli().load_edsl_classes()
    survey = Survey()
    rank_tasks = detect_rank_tasks(questions)
    if not rank_tasks:
        rank_task_map: dict[str, dict[str, Any]] = {}
        for question in questions:
            if question.get("question_type") != "rank_item" or not question.get("rank_task_id"):
                continue
            task_id = str(question["rank_task_id"])
            rank_task_map.setdefault(
                task_id,
                {
                    "rank_task_id": task_id,
                    "rank_task_text": question.get("rank_task_text") or question.get("question_text") or task_id,
                    "rank_direction": question.get("rank_direction") or "1_is_best",
                    "source_question_names": [],
                    "items": [],
                },
            )
            rank_task_map[task_id]["source_question_names"].append(question["question_name"])
            rank_task_map[task_id]["items"].append(
                {
                    "item_id": question["question_name"],
                    "label": question.get("rank_item_label") or question.get("question_text") or question["question_name"],
                }
            )
        rank_tasks = list(rank_task_map.values())
    rank_item_names = {name for task in rank_tasks for name in task.get("source_question_names", [])}
    for question in synthetic_rank_questions(rank_tasks):
        survey.add_question(edsl_question_from_zwill(question, Question))
    for question in questions:
        if question.get("question_name") in rank_item_names or question.get("question_type") == "rank_item":
            continue
        survey.add_question(edsl_question_from_zwill(question, Question))
    return survey.to_dict()


def selected_question_names(args: argparse.Namespace, questions: list[dict[str, Any]]) -> list[str]:
    available = [question["question_name"] for question in questions]
    # Accept both a comma-separated string and a JSON list (e.g. plan-driven
    # `context_questions`) for the singular and plural selectors.
    requested = normalize_name_list(args.question) + normalize_name_list(args.questions)
    selected: list[str] = requested if requested else available[:]

    excluded = set(args.exclude_question or [])
    selected = [name for name in selected if name not in excluded]
    unknown = [name for name in selected if name not in available]
    if unknown:
        raise ZwillError(
            "invalid_input",
            "Unknown question selected for EDSL export.",
            context={"unknown_questions": unknown, "available_questions": available},
            hint="Use question names from `zwill survey show` or the survey questions.jsonl file.",
        )
    return selected


def selected_agent_material_kinds(args: argparse.Namespace) -> set[str]:
    return set(normalize_tags(getattr(args, "agent_material_kind", None)))


def selected_agent_material_tags(args: argparse.Namespace) -> set[str]:
    return set(normalize_tags(getattr(args, "agent_material_tag", None)))


def select_agent_material(
    sdir: Path,
    respondent_ids: list[str] | None,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    respondent_set = set(respondent_ids) if respondent_ids is not None else None
    kinds = selected_agent_material_kinds(args)
    tags = selected_agent_material_tags(args)
    selected = []
    for row in agent_material_rows(sdir):
        if respondent_set is not None and row.get("respondent_id") not in respondent_set:
            continue
        if kinds and row.get("kind") not in kinds:
            continue
        row_tags = set(row.get("tags", []))
        if tags and not row_tags.intersection(tags):
            continue
        selected.append(row)
    return selected


def format_agent_material(materials: list[dict[str, Any]], max_chars: int | None = None) -> str:
    if not materials:
        return "No non-survey agent material provided."
    blocks = []
    for material in materials:
        heading = material.get("title") or material.get("kind") or "Agent material"
        kind = material.get("kind")
        body = material.get("body_markdown", "").strip()
        if kind:
            blocks.append(f"### {heading} ({kind})\n{body}")
        else:
            blocks.append(f"### {heading}\n{body}")
    text = "\n\n".join(blocks).strip()
    if max_chars is not None and max_chars >= 0 and len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n\n[Truncated to max agent material characters.]"
    return text


def twin_material_paths(args: argparse.Namespace) -> list[str]:
    values = getattr(args, "twin_material", None) or []
    return [str(value) for value in values]


def normalize_twin_material_row(row: dict[str, Any], source_path: str, index: int) -> dict[str, Any]:
    material_id = row.get("material_id") or row.get("id") or f"{Path(source_path).stem}_{index:04d}"
    question = row.get("question") or row.get("heldout_question") or row.get("heldout_question_name") or row.get("source_question_name")
    body = row.get("body_markdown") or row.get("markdown") or row.get("text") or row.get("body") or ""
    probabilities = row.get("probabilities")
    if not body and isinstance(probabilities, dict):
        body = "Probabilities:\n" + "\n".join(f"- {option}: {probability}" for option, probability in probabilities.items())
    return {
        "material_id": str(material_id),
        "title": str(row.get("title") or row.get("kind") or material_id),
        "kind": str(row.get("kind") or "supplemental"),
        "body_markdown": str(body).strip(),
        "survey": row.get("survey"),
        "question": question,
        "respondent_id": row.get("respondent_id"),
        "source_path": source_path,
        "metadata": row.get("metadata", {}),
    }


def load_twin_material(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_path in twin_material_paths(args):
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            raise ZwillError("not_found", f"Twin material path does not exist: {raw_path}.")
        if path.suffix == ".jsonl":
            for index, row in enumerate(read_jsonl(path), start=1):
                if not isinstance(row, dict):
                    raise ZwillError("invalid_input", f"Twin material JSONL rows must be objects: {raw_path}.")
                rows.append(normalize_twin_material_row(row, raw_path, index))
        elif path.suffix == ".json":
            data = read_json(path, None)
            records = data if isinstance(data, list) else data.get("materials", [data]) if isinstance(data, dict) else None
            if not isinstance(records, list):
                raise ZwillError("invalid_input", f"Twin material JSON must be an object, list, or object with materials: {raw_path}.")
            for index, row in enumerate(records, start=1):
                if not isinstance(row, dict):
                    raise ZwillError("invalid_input", f"Twin material JSON records must be objects: {raw_path}.")
                rows.append(normalize_twin_material_row(row, raw_path, index))
        else:
            rows.append(
                normalize_twin_material_row(
                    {
                        "material_id": path.stem,
                        "title": path.stem.replace("_", " "),
                        "kind": "markdown",
                        "body_markdown": path.read_text().strip(),
                    },
                    raw_path,
                    1,
                )
            )
    return [row for row in rows if row.get("body_markdown")]


def matching_twin_material(
    materials: list[dict[str, Any]],
    *,
    survey_name: str,
    heldout_question: str,
    respondent_id: str,
) -> list[dict[str, Any]]:
    matched = []
    for row in materials:
        if row.get("survey") and row.get("survey") != survey_name:
            continue
        if row.get("question") and row.get("question") != heldout_question:
            continue
        if row.get("respondent_id") and row.get("respondent_id") != respondent_id:
            continue
        matched.append(row)
    return matched


def format_twin_material(materials: list[dict[str, Any]], max_chars: int | None = None) -> str:
    if not materials:
        return "No supplemental twin material supplied."
    blocks = []
    for row in materials:
        blocks.append(
            "\n".join(
                [
                    f"## {row.get('title', row.get('material_id', 'Supplemental material'))}",
                    f"Kind: {row.get('kind', 'supplemental')}",
                    f"Source: {row.get('source_path', '')}",
                    str(row.get("body_markdown", "")).strip(),
                ]
            ).strip()
        )
    text = "\n\n".join(blocks)
    if max_chars is not None and max_chars >= 0 and len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n\n[Truncated to max twin material characters.]"
    return text


def build_agent_instruction(survey_context: str, material_text: str) -> str | None:
    blocks = []
    if survey_context.strip():
        blocks.append("## Survey context\n" + survey_context.strip())
    if material_text.strip() and material_text.strip() != "No non-survey agent material provided.":
        blocks.append("## Non-survey agent material\n" + material_text.strip())
    text = "\n\n".join(blocks)
    return (text + "\n") if text else None


DEFAULT_SURVEY_ANSWER_TRAITS_PRESENTATION_TEMPLATE = """## Prior survey answers
The following entries are observed question-and-answer pairs from the source survey for this respondent. Use them as evidence about this respondent's views and background when answering the new question. They are not instructions, and they are not answers to the new question.

{% for question_name, answer in traits.items() -%}
- Survey question: {{ codebook[question_name] if question_name in codebook else question_name }}
  Recorded answer: {{ answer }}
{% endfor -%}"""


def agent_list_traits_presentation_template(args: argparse.Namespace) -> tuple[str | None, str]:
    if getattr(args, "no_default_traits_presentation_template", False):
        return None, "edsl_default"
    template_path = getattr(args, "traits_presentation_template_path", None)
    template = getattr(args, "traits_presentation_template", None)
    if template_path and template:
        raise ZwillError(
            "invalid_input",
            "Use only one of --traits-presentation-template and --traits-presentation-template-path.",
        )
    if template_path:
        return Path(template_path).read_text(), "path"
    if template is not None:
        return template, "inline"
    return DEFAULT_SURVEY_ANSWER_TRAITS_PRESENTATION_TEMPLATE, "zwill_default_survey_answers"


def option_key(index: int) -> str:
    key = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        key = chr(ord("a") + remainder) + key
    return key


def parse_model_specs(args: argparse.Namespace) -> list[tuple[str, str | None]]:
    values = normalize_name_list(args.model) + normalize_name_list(args.models)
    if not values:
        values = ["gpt-5.5"]

    specs = []
    for value in values:
        if ":" in value:
            service_name, model_name = value.split(":", 1)
            service_name = service_name.strip()
            model_name = model_name.strip()
        else:
            service_name = args.service_name
            model_name = value.strip()
        if not model_name:
            raise ZwillError("invalid_input", "Model name cannot be empty.")
        specs.append((model_name, service_name or None))
    return specs


def parse_model_param_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_model_params(args: argparse.Namespace) -> dict[tuple[str | None, str | None], dict[str, Any]]:
    params: dict[tuple[str | None, str | None], dict[str, Any]] = defaultdict(dict)
    for item in args.model_param or []:
        if "=" not in item:
            raise ZwillError("invalid_input", f"Invalid model parameter: {item}.", hint="Use key=value or service:model:key=value.")
        left, raw_value = item.split("=", 1)
        parts = left.split(":")
        if len(parts) == 1:
            service_name = None
            model_name = None
            key = parts[0]
        elif len(parts) == 3:
            service_name, model_name, key = parts
            service_name = service_name or None
            model_name = model_name or None
        else:
            raise ZwillError(
                "invalid_input",
                f"Invalid model parameter target: {left}.",
                hint="Use key=value for all models or service:model:key=value for one model.",
            )
        if not key:
            raise ZwillError("invalid_input", f"Invalid model parameter: {item}.", hint="Parameter key cannot be empty.")
        params[(service_name, model_name)][key] = parse_model_param_value(raw_value)
    return params


def model_kwargs_for(
    model_name: str,
    service_name: str | None,
    model_params: dict[tuple[str | None, str | None], dict[str, Any]],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    kwargs.update(model_params.get((None, None), {}))
    kwargs.update(model_params.get((None, model_name), {}))
    kwargs.update(model_params.get((service_name, model_name), {}))
    return kwargs


def model_label(service_name: str | None, model_name: str | None) -> str:
    if service_name and model_name:
        return f"{service_name}:{model_name}"
    return str(model_name or "")



def build_edsl_agent_list_dict(survey_name: str, args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    selected = selected_question_names(args, questions)
    selected_set = set(selected)
    codebook = {
        question["question_name"]: question["question_text"]
        for question in questions
        if question["question_name"] in selected_set
    }

    respondent_ids = [row["respondent_id"] for row in read_jsonl(sdir / "respondents.jsonl")]
    if not respondent_ids:
        respondent_ids = sorted({row["respondent_id"] for row in read_jsonl(sdir / "answers.jsonl")})
    if args.limit is not None:
        respondent_ids = respondent_ids[: args.limit]

    traits_by_respondent = {
        respondent_id: {question_name: None for question_name in selected}
        for respondent_id in respondent_ids
    }
    for answer in read_jsonl(sdir / "answers.jsonl"):
        respondent_id = answer["respondent_id"]
        question_name = answer["question"]
        if respondent_id not in traits_by_respondent or question_name not in selected_set:
            continue
        traits_by_respondent[respondent_id][question_name] = answer.get("answer")

    include_material = getattr(args, "include_agent_material", False)
    include_survey_context = getattr(args, "include_survey_context", False)
    context_text = survey_context_text(sdir) if include_survey_context else ""
    max_chars = getattr(args, "max_agent_material_chars", None)
    traits_template, traits_template_source = agent_list_traits_presentation_template(args)
    instructions_by_respondent: dict[str, str | None] = {}
    for respondent_id in respondent_ids:
        material_text = ""
        if include_material:
            materials = select_agent_material(sdir, [respondent_id], args)
            material_text = format_agent_material(materials, max_chars)
        instructions_by_respondent[respondent_id] = build_agent_instruction(context_text, material_text)

    Agent, AgentList, _, _ = _cli().load_edsl_classes()
    agents = []
    for respondent_id in respondent_ids:
        kwargs = {
            "name": respondent_id,
            "traits": traits_by_respondent[respondent_id],
            "codebook": codebook,
        }
        if instructions_by_respondent[respondent_id]:
            kwargs["instruction"] = instructions_by_respondent[respondent_id]
        if traits_template is not None:
            kwargs["traits_presentation_template"] = traits_template
        agents.append(Agent(**kwargs))
    agent_list = AgentList(agents)
    data = agent_list.to_dict()

    # Keep the shared trait codebook at AgentList level. EDSL can rehydrate this
    # format and apply the codebook to agents during AgentList.from_dict.
    data["codebook"] = codebook
    if traits_template is not None:
        data["traits_presentation_template"] = traits_template
    for agent in data.get("agent_list", []):
        agent.pop("codebook", None)
        if traits_template is not None and agent.get("traits_presentation_template") == traits_template:
            agent.pop("traits_presentation_template", None)
    data["zwill"] = {
        "survey": survey_name,
        "selected_questions": selected,
        "include_survey_context": include_survey_context,
        "include_agent_material": include_material,
        "agent_material_kinds": sorted(selected_agent_material_kinds(args)),
        "agent_material_tags": sorted(selected_agent_material_tags(args)),
        "max_agent_material_chars": max_chars,
        "traits_presentation_template_source": traits_template_source,
        "agent_count": len(respondent_ids),
    }
    return data


def inspect_agent_list_dict(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("edsl_class_name") != "AgentList":
        raise ZwillError("invalid_input", "Expected an EDSL AgentList serialization.")
    agents = data.get("agent_list", [])
    if not isinstance(agents, list):
        raise ZwillError("invalid_input", "Expected an EDSL AgentList serialization with an agent_list array.")
    codebook = data.get("codebook", {})
    shared_traits_template = data.get("traits_presentation_template")
    trait_keys: Counter = Counter()
    agents_with_instruction = 0
    instruction_chars = []
    sample_agents = []
    for agent in agents:
        traits = agent.get("traits", {}) if isinstance(agent, dict) else {}
        for key in traits:
            trait_keys[key] += 1
        instruction = agent.get("instruction") if isinstance(agent, dict) else None
        if instruction:
            agents_with_instruction += 1
            instruction_chars.append(len(instruction))
        if len(sample_agents) < 3:
            sample_agents.append(
                {
                    "name": agent.get("name"),
                    "trait_count": len(traits),
                    "trait_keys": list(traits)[:10],
                    "has_instruction": bool(instruction),
                    "instruction_chars": len(instruction or ""),
                }
            )
    return {
        "agent_count": len(agents),
        "trait_keys": sorted(trait_keys),
        "trait_counts": dict(sorted(trait_keys.items())),
        "codebook_keys": sorted(codebook),
        "has_traits_presentation_template": bool(shared_traits_template),
        "traits_presentation_template_chars": len(shared_traits_template or ""),
        "traits_presentation_template_preview": (shared_traits_template or "")[:240],
        "agents_with_instruction": agents_with_instruction,
        "mean_instruction_chars": (
            sum(instruction_chars) / len(instruction_chars)
            if instruction_chars
            else 0.0
        ),
        "zwill": data.get("zwill", {}),
        "sample_agents": sample_agents,
    }


def cmd_agent_list_inspect(args: argparse.Namespace) -> dict[str, Any]:
    data = read_json_or_gzip(Path(args.path))
    summary = inspect_agent_list_dict(data)
    if args.format == "json":
        print_json(envelope("zwill agent-list inspect", "ok", summary))
    else:
        table = Table(title="EDSL AgentList")
        table.add_column("metric")
        table.add_column("value")
        table.add_row("agents", str(summary["agent_count"]))
        table.add_row("trait keys", ", ".join(summary["trait_keys"]) or "(none)")
        table.add_row("traits template", "yes" if summary["has_traits_presentation_template"] else "no")
        table.add_row("traits template chars", str(summary["traits_presentation_template_chars"]))
        table.add_row("agents with instruction", str(summary["agents_with_instruction"]))
        table.add_row("mean instruction chars", f"{summary['mean_instruction_chars']:.1f}")
        Console().print(table)
        if summary["sample_agents"]:
            sample_table = Table(title="sample agents")
            for column in ["name", "trait_count", "trait_keys", "has_instruction", "instruction_chars"]:
                sample_table.add_column(column)
            for row in summary["sample_agents"]:
                sample_table.add_row(
                    str(row["name"]),
                    str(row["trait_count"]),
                    ", ".join(row["trait_keys"]),
                    str(row["has_instruction"]),
                    str(row["instruction_chars"]),
                )
            Console().print(sample_table)
    return envelope("zwill agent-list inspect", "ok", summary)


def load_question_spec_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.question_path:
        data = read_json(Path(args.question_path), {})
        if data.get("edsl_class_name") == "QuestionBase":
            return {
                "question_name": data["question_name"],
                "question_type": data["question_type"],
                "question_text": data["question_text"],
                "question_options": data.get("question_options", []),
            }
        return data
    return {
        "question_name": args.question_name,
        "question_type": args.question_type,
        "question_text": args.question_text,
        "question_options": args.question_option or [],
    }


def agent_study_job_id_from_job(job: dict[str, Any]) -> str:
    payload = {
        "survey": job.get("survey", {}),
        "agents": job.get("agents", []),
        "models": job.get("models", []),
        "scenarios": job.get("scenarios", []),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def agent_study_job_id_from_results(results: dict[str, Any]) -> str:
    rows = []
    for row in results.get("data", []):
        question_to_attributes = row.get("question_to_attributes", {})
        rows.append(
            {
                "agent": row.get("agent", {}),
                "scenario": row.get("scenario", {}),
                "model": row.get("model", {}),
                "question_to_attributes": question_to_attributes,
                "answer_keys": sorted((row.get("answer") or {}).keys()),
            }
        )
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_edsl_agent_study_job_dict(args: argparse.Namespace) -> dict[str, Any]:
    agent_list_dict = read_json_or_gzip(Path(args.agent_list))
    agent_list_summary = inspect_agent_list_dict(agent_list_dict)
    question_spec = load_question_spec_from_args(args)
    required = ["question_name", "question_type", "question_text"]
    missing = [key for key in required if not question_spec.get(key)]
    if missing:
        raise ZwillError("invalid_input", "Agent-study question is missing required fields.", context={"missing": missing})

    AgentList, Jobs, Model, ModelList, Question, Survey = _cli().load_edsl_agent_study_classes()
    agent_list = AgentList.from_dict(agent_list_dict)
    question = edsl_question_from_zwill(question_spec, Question)
    model_params = parse_model_params(args)
    job = Jobs(
        survey=Survey(questions=[question]),
        agents=agent_list,
        models=ModelList(
            [
                Model(
                    model_name=model_name,
                    service_name=service_name,
                    **model_kwargs_for(model_name, service_name, model_params),
                )
                for model_name, service_name in parse_model_specs(args)
            ]
        ),
    )
    data = job.to_dict()
    data["zwill"] = {
        "agent_study_job_id": agent_study_job_id_from_job(data),
        "agent_list_path": args.agent_list,
        "question_name": question_spec["question_name"],
        "agent_count": agent_list_summary["agent_count"],
        "agent_list": agent_list_dict.get("zwill", {}),
    }
    return data


def emit_raw_export(command: str, args: argparse.Namespace, output: str, data: dict[str, Any]) -> None:
    """Shared output contract for raw JSON-job exports.

    With --path the file is the artifact and stdout carries a clean, parseable
    envelope (metadata, not a second copy of the whole job). Without --path,
    stdout is the artifact (pipe-friendly). --quiet suppresses stdout entirely.
    """
    quiet = getattr(args, "quiet", False)
    if args.path:
        path = Path(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n")
        if not quiet:
            print_json(envelope(command, "ok", {"path": str(path), **data}))
    elif not quiet:
        print(output)


def cmd_agent_study_export(args: argparse.Namespace) -> None:
    export_dict = build_edsl_agent_study_job_dict(args)
    output = json.dumps(export_dict, indent=2)
    emit_raw_export("zwill agent-study export", args, output, {})



def probability_job_builder_deps() -> ProbabilityJobBuilderDeps:
    return ProbabilityJobBuilderDeps(
        require_survey=require_survey,
        selected_question_names=selected_question_names,
        context_path=context_path,
        load_edsl_job_classes=load_edsl_job_classes,
        option_key=option_key,
        parse_model_params=parse_model_params,
        parse_model_specs=parse_model_specs,
        model_kwargs_for=model_kwargs_for,
    )


def build_edsl_probability_job_dict(survey_name: str, args: argparse.Namespace) -> dict[str, Any]:
    return build_edsl_probability_job_dict_impl(survey_name, args, probability_job_builder_deps())


def respondent_selection(args: argparse.Namespace, all_respondent_ids: list[str]) -> list[str]:
    # Accept both a comma-separated string and a JSON list (e.g. plan-driven
    # `respondents`) for the singular and plural selectors.
    requested = normalize_name_list(getattr(args, "respondent", None)) + normalize_name_list(
        getattr(args, "respondents", None)
    )
    selected: list[str] = requested if requested else all_respondent_ids[:]
    unknown = [respondent_id for respondent_id in selected if respondent_id not in all_respondent_ids]
    if unknown:
        raise ZwillError(
            "invalid_input",
            "Unknown respondent selected for digital twin export.",
            context={"unknown_respondents": unknown},
        )
    if (
        getattr(args, "sample_respondents", None) is not None
        and not getattr(args, "balance_actual", False)
        and not getattr(args, "stratify_actual", False)
    ):
        if args.sample_respondents < 0:
            raise ZwillError("invalid_input", "--sample-respondents must be non-negative.")
        rng = random.Random(args.seed)
        selected = rng.sample(selected, min(args.sample_respondents, len(selected)))
    if args.limit_respondents is not None:
        selected = selected[: args.limit_respondents]
    return selected


def digital_twin_job_builder_deps() -> DigitalTwinJobBuilderDeps:
    return DigitalTwinJobBuilderDeps(
        require_survey=require_survey,
        selected_question_names=selected_question_names,
        respondent_selection=respondent_selection,
        context_question_options=context_question_options,
        context_path=context_path,
        load_edsl_job_classes=load_edsl_job_classes,
        load_twin_material=load_twin_material,
        selected_agent_material_kinds=selected_agent_material_kinds,
        selected_agent_material_tags=selected_agent_material_tags,
        select_agent_material=select_agent_material,
        format_agent_material=format_agent_material,
        matching_twin_material=matching_twin_material,
        format_twin_material=format_twin_material,
        twin_material_paths=twin_material_paths,
        option_key=option_key,
        parse_model_params=parse_model_params,
        parse_model_specs=parse_model_specs,
        model_kwargs_for=model_kwargs_for,
    )


def build_edsl_digital_twin_job_dict(survey_name: str, args: argparse.Namespace) -> dict[str, Any]:
    return build_edsl_digital_twin_job_dict_impl(survey_name, args, digital_twin_job_builder_deps())


def rank_utility_question_text() -> str:
    return """You are acting as a digital twin for one survey respondent.

Survey name:
{{ survey_name }}

Survey context:
{{ survey_context }}

Respondent id:
{{ respondent_id }}

Non-survey agent construction material:
{{ agent_material_text }}

Supplemental twin material:
{{ twin_material_text }}

Observed answers from this respondent:
{{ observed_answers_text }}

Held-out rank task:
{{ rank_task_text }}

Rank direction:
{{ rank_direction_text }}

Items to score:
{{ rank_items_text }}

Estimate this respondent's latent appeal or utility score for each item on a 0-100 scale.

Use the full scale:
- 0 means no appeal to this respondent.
- 50 means neutral/moderate appeal.
- 100 means strongest appeal among this kind of item.

Scores may be close or tied if the respondent would see items as similarly appealing.
The implied ranking is obtained by sorting items from highest score to lowest score.

Return only valid JSON. Do not include markdown fences, prose, or comments.

The JSON must have exactly this shape:
{
  "scores": {
    "item_id_1": 78,
    "item_id_2": 72
  },
  "confidence": 0.64,
  "notes": "Brief respondent-level explanation."
}"""


def build_edsl_rank_utility_twin_job_dict(survey_name: str, args: argparse.Namespace) -> dict[str, Any]:
    sdir = require_survey(survey_name)
    questions = read_jsonl(sdir / "questions.jsonl")
    question_by_name = {question["question_name"]: question for question in questions}
    tasks = detect_rank_tasks(questions)
    try:
        selected_tasks = selected_rank_tasks(args, tasks)
    except ValueError as exc:
        detail = str(exc)
        context = None
        if detail.startswith("{"):
            try:
                context = json.loads(detail)
            except json.JSONDecodeError:
                context = None
        raise ZwillError(
            "invalid_input",
            "Unknown or missing rank task selection.",
            context=context or {"error": detail, "available_rank_tasks": [task["rank_task_id"] for task in tasks]},
            hint="Use --rank-task-id, or pass one item-level source question with --heldout-question.",
        ) from exc
    if not selected_tasks:
        raise ZwillError("invalid_input", "No rank tasks selected.")

    context_args = type("ContextArgs", (), {})()
    context_args.question = args.context_question
    context_args.questions = args.context_questions
    context_args.exclude_question = args.exclude_context_question or []
    context_question_names = selected_question_names(context_args, questions)
    all_rank_item_names = {name for task in selected_tasks for name in task.get("source_question_names", [])}
    context_question_names = [name for name in context_question_names if name not in all_rank_item_names]
    context_priority_by_question = {
        str(question["question_name"]): float(question["context_priority"])
        for question in questions
        if question.get("context_priority") is not None
    }

    all_respondent_ids = [row["respondent_id"] for row in read_jsonl(sdir / "respondents.jsonl")]
    if not all_respondent_ids:
        all_respondent_ids = sorted({row["respondent_id"] for row in read_jsonl(sdir / "answers.jsonl")})
    respondent_ids = respondent_selection(args, all_respondent_ids)
    answer_by_respondent: dict[str, dict[str, str]] = defaultdict(dict)
    for answer in read_jsonl(sdir / "answers.jsonl"):
        if answer.get("answer") is None:
            continue
        answer_by_respondent[answer["respondent_id"]][answer["question"]] = answer["answer"]
    counts_by_question = answer_commonness_by_question(answer_by_respondent)
    if args.complete_cases:
        required = set(context_question_names) | all_rank_item_names
        respondent_ids = [respondent_id for respondent_id in respondent_ids if required.issubset(answer_by_respondent.get(respondent_id, {}))]

    context_file = context_path(sdir)
    context_text = context_file.read_text().strip() if context_file.exists() else ""
    Jobs, Model, ModelList, QuestionFreeText, Scenario, ScenarioList, Survey = _cli().load_edsl_job_classes()
    rank_question = QuestionFreeText(question_name=args.job_question_name, question_text=rank_utility_question_text())
    all_twin_material = load_twin_material(args)
    scenarios = []
    skipped_missing = []
    prompt_variant = getattr(args, "prompt_variant", "raw") or "raw"
    for task in selected_tasks:
        task_item_ids = [item["item_id"] for item in task["items"]]
        for respondent_id in respondent_ids:
            respondent_answers = answer_by_respondent.get(respondent_id, {})
            actual_ranks = {}
            missing_items = []
            for item_id in task_item_ids:
                raw_rank = respondent_answers.get(item_id)
                if raw_rank is None:
                    missing_items.append(item_id)
                    continue
                match = re.match(r"^(\d+)", str(raw_rank).strip())
                if not match:
                    missing_items.append(item_id)
                    continue
                actual_ranks[item_id] = int(match.group(1))
            if missing_items and not getattr(args, "allow_missing_actual", False):
                skipped_missing.append({"respondent_id": respondent_id, "rank_task_id": task["rank_task_id"], "missing_items": missing_items})
                continue
            target_context = [name for name in context_question_names if name not in task_item_ids]
            selected_context = select_context_questions(
                respondent_answers, target_context, "", args.context_question_count, context_priority_by_question
            )
            observed_answers = [
                {
                    "question_name": question_name,
                    "question_text": expand_question_text_fields(question_by_name[question_name]["question_text"], respondent_answers, question_by_name),
                    "question_options": context_question_options(question_by_name[question_name]),
                    "answer": respondent_answers[question_name],
                }
                for question_name in selected_context
            ]
            observed_lines = []
            for observed in observed_answers:
                observed_lines.append(
                    "\n".join(
                        [
                            f"Question: {observed['question_name']}",
                            f"Text: {observed['question_text']}",
                            "Options: " + "; ".join(observed["question_options"]),
                            f"Respondent answered: {observed['answer']}",
                            *(
                                [answer_commonness_text(observed["question_name"], observed["answer"], counts_by_question)]
                                if prompt_variant == "answer-commonness-confidence"
                                else []
                            ),
                        ]
                    )
                )
            agent_material = select_agent_material(sdir, [respondent_id], args) if getattr(args, "include_agent_material", False) else []
            twin_material = matching_twin_material(all_twin_material, survey_name=survey_name, heldout_question=task["rank_task_id"], respondent_id=respondent_id)
            rank_items_text = "\n".join(f"{item['item_id']}: {item['label']}" for item in task["items"])
            scenarios.append(
                Scenario(
                    {
                        "survey_name": survey_name,
                        "survey_context": context_text,
                        "respondent_id": respondent_id,
                        "rank_task_id": task["rank_task_id"],
                        "rank_task_text": task["rank_task_text"],
                        "rank_direction": task["rank_direction"],
                        "rank_direction_text": "1 is the most preferred/appealing item; larger ranks are lower.",
                        "rank_items": task["items"],
                        "rank_items_text": rank_items_text,
                        "actual_ranks": actual_ranks,
                        "observed_answers": observed_answers,
                        "observed_answers_text": "\n\n".join(observed_lines) if observed_lines else "No observed answers provided.",
                        "agent_material": agent_material,
                        "agent_material_text": format_agent_material(agent_material, getattr(args, "max_agent_material_chars", None)),
                        "twin_material": twin_material,
                        "twin_material_text": format_twin_material(twin_material, getattr(args, "max_twin_material_chars", None)),
                        "leakage_exclusions": task_item_ids,
                    }
                )
            )
    if not scenarios:
        raise ZwillError(
            "invalid_input",
            "No rank utility twin scenarios could be built.",
            context={"skipped_missing": skipped_missing[:10], "skipped_count": len(skipped_missing)},
        )
    model_params = parse_model_params(args)
    job = Jobs(
        survey=Survey(questions=[rank_question]),
        scenarios=ScenarioList(scenarios),
        models=ModelList(
            [
                Model(model_name=model_name, service_name=service_name, **model_kwargs_for(model_name, service_name, model_params))
                for model_name, service_name in parse_model_specs(args)
            ]
        ),
    )
    data = job.to_dict()
    data["zwill"] = {
        "rank_utility_twin_job_id": rank_job_id_from_job(data),
        "rank_task_ids": [task["rank_task_id"] for task in selected_tasks],
        "rank_task_count": len(selected_tasks),
        "rank_item_count": sum(len(task["items"]) for task in selected_tasks),
        "context_question_count": args.context_question_count,
        "sample_respondents": args.sample_respondents,
        "seed": args.seed,
        "complete_cases": args.complete_cases,
        "include_agent_material": getattr(args, "include_agent_material", False),
        "twin_material_paths": twin_material_paths(args),
        "twin_material_count": len(all_twin_material),
        "allow_missing_actual": getattr(args, "allow_missing_actual", False),
        "prompt_variant": prompt_variant,
        "scenario_count": len(scenarios),
        "skipped_missing_rank_item_count": len(skipped_missing),
    }
    return data



def cmd_edsl_run(args: argparse.Namespace) -> dict[str, Any]:
    job_path = Path(args.job)
    if not job_path.exists():
        raise ZwillError("not_found", f"EDSL job file does not exist: {args.job}.")
    job_dict = read_json_or_gzip(job_path)
    if not isinstance(job_dict, dict) or job_dict.get("edsl_class_name") != "Jobs":
        raise ZwillError("invalid_input", "Expected an EDSL Jobs serialization.")

    env_path = Path(args.env_path) if getattr(args, "env_path", None) else None
    loaded_env = load_local_env(env_path)
    Jobs, RunParameters = _cli().load_edsl_runner_classes()
    job = Jobs.from_dict(job_dict)
    approved_validation_plan = (job_dict.get("zwill") or {}).get("approved_validation_plan")
    if isinstance(approved_validation_plan, dict):
        count_check = approved_validation_plan.get("export_count_check")
        if isinstance(count_check, dict) and count_check.get("requires_reapproval") and not getattr(args, "allow_count_delta", False):
            raise ZwillError(
                "approval_required",
                "Exported validation job prediction count differs from the approved plan.",
                context=count_check,
                hint="Review the exported count, re-approve the plan, or pass --allow-count-delta for an explicit debug run.",
            )
    run_parameters = {}
    if args.n is not None:
        run_parameters["n"] = args.n
    if args.progress_bar:
        run_parameters["progress_bar"] = True
    if args.fresh:
        run_parameters["fresh"] = True
    if args.stop_on_exception:
        run_parameters["stop_on_exception"] = True
    if args.check_api_keys:
        run_parameters["check_api_keys"] = True
    if args.verbose is not None:
        run_parameters["verbose"] = args.verbose
    if args.print_exceptions is not None:
        run_parameters["print_exceptions"] = args.print_exceptions
    if args.offload_execution:
        run_parameters["offload_execution"] = True
    if args.use_api_proxy:
        run_parameters["use_api_proxy"] = True
    for item in args.run_param or []:
        if "=" not in item:
            raise ZwillError("invalid_input", f"Invalid run parameter: {item}.", hint="Use key=value.")
        key, value = item.split("=", 1)
        if key not in RunParameters.__dataclass_fields__:
            raise ZwillError(
                "invalid_input",
                f"Unknown EDSL run parameter: {key}.",
                context={"available_parameters": sorted(RunParameters.__dataclass_fields__)},
            )
        run_parameters[key] = parse_model_param_value(value)

    output_path = Path(args.path)
    if args.dry_run:
        return envelope(
            "zwill edsl-run",
            "ok",
            {
                "job_path": str(job_path),
                "results_path": str(output_path),
                "dry_run": True,
                "estimated_cost": estimate_job_cost_summary(job),
                "scenario_count": len(job.scenarios),
                "model_count": len(job.models),
                "question_count": len(job.survey.questions),
                "probability_job_id": job_dict.get("zwill", {}).get("probability_job_id"),
                "digital_twin_job_id": job_dict.get("zwill", {}).get("digital_twin_job_id"),
                "rank_utility_twin_job_id": job_dict.get("zwill", {}).get("rank_utility_twin_job_id"),
                "agent_study_job_id": job_dict.get("zwill", {}).get("agent_study_job_id"),
                "practitioner_report_id": job_dict.get("zwill", {}).get("practitioner_report_id"),
                "run_parameters": run_parameters,
                "loaded_env": loaded_env,
            },
        )

    results = job.run(**run_parameters) if run_parameters else job.run()
    if results is None:
        raise ZwillError("edsl_run_failed", "EDSL job did not return a Results object.")
    results_dict = results.to_dict()
    if job_dict.get("zwill"):
        results_dict["zwill"] = job_dict["zwill"]
    write_json_or_gzip(output_path, results_dict)
    return envelope(
        "zwill edsl-run",
        "ok",
        {
            "job_path": str(job_path),
            "results_path": str(output_path),
            "result_count": len(results_dict.get("data", [])),
            "cost": results_cost_summary(results_dict),
            "probability_job_id": results_dict.get("zwill", {}).get("probability_job_id"),
            "digital_twin_job_id": results_dict.get("zwill", {}).get("digital_twin_job_id"),
            "rank_utility_twin_job_id": results_dict.get("zwill", {}).get("rank_utility_twin_job_id"),
            "agent_study_job_id": results_dict.get("zwill", {}).get("agent_study_job_id"),
            "practitioner_report_id": results_dict.get("zwill", {}).get("practitioner_report_id"),
            "run_parameters": run_parameters,
            "loaded_env": loaded_env,
        },
        next_steps=[
            (
                f"zwill twin-benchmark practitioner-report-import --path {output_path}"
                if results_dict.get("zwill", {}).get("practitioner_report_id")
                else f"zwill twin-results import --survey <survey> --path {output_path}"
                if results_dict.get("zwill", {}).get("digital_twin_job_id")
                else f"zwill twin-results import --survey <survey> --path {output_path}"
                if results_dict.get("zwill", {}).get("rank_utility_twin_job_id")
                else f"zwill prob-results import --survey <survey> --path {output_path}"
                if results_dict.get("zwill", {}).get("probability_job_id")
                else f"zwill agent-study import --path {output_path}"
                if results_dict.get("zwill", {}).get("agent_study_job_id")
                else f"zwill prob-results import --survey <survey> --path {output_path}"
            )
        ],
    )


def cmd_edsl_export(args: argparse.Namespace) -> None:
    if args.target == "survey":
        export_dict = build_edsl_survey_dict(args.survey)
    elif args.target == "agent-list":
        export_dict = build_edsl_agent_list_dict(args.survey, args)
    elif args.target == "probability-job":
        export_dict = build_edsl_probability_job_dict(args.survey, args)
    elif args.target == "rank-utility-twin-job":
        approved_plan = require_twin_plan_approval(args, command="zwill edsl-export --target rank-utility-twin-job")
        export_dict = build_edsl_rank_utility_twin_job_dict(args.survey, args)
        if approved_plan:
            export_dict.setdefault("zwill", {})["approved_validation_plan"] = approved_plan
    else:
        approved_plan = require_twin_plan_approval(args, command="zwill edsl-export --target twin-probability-job")
        export_dict = build_edsl_digital_twin_job_dict(args.survey, args)
        if approved_plan:
            export_dict.setdefault("zwill", {})["approved_validation_plan"] = approved_plan
    output = json.dumps(export_dict, indent=2)
    zwill_meta = export_dict.get("zwill") if isinstance(export_dict.get("zwill"), dict) else {}
    scenario_count = zwill_meta.get("scenario_count")
    if scenario_count is None:
        scenario_count = len(export_dict.get("scenarios", []) or []) or None
    emit_raw_export(
        "zwill edsl-export",
        args,
        output,
        {
            "target": args.target,
            "survey": args.survey,
            "scenario_count": scenario_count,
            "model_count": len(export_dict.get("models", []) or []) or None,
        },
    )
