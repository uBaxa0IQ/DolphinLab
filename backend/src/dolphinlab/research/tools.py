"""Concrete role-scoped operations: required fields are visible to the model."""

from copy import deepcopy

from dolphinlab.domain.pages import PagePublish, PageSave
from dolphinlab.domain.staffing import Handover

S = {"type": "string"}
N = {"type": "number"}
INTEGER = {"type": "integer"}
B = {"type": "boolean"}
STRINGS = {"type": "array", "items": S}
OBJECT = {"type": "object"}
WORKERS = {"ml_engineer", "data_engineer", "evaluator"}
MANAGERS = {"tech_lead", "team_lead"}


def spec(
    name, description, properties, required=(), roles=None, route=None, fixed=None, rename=None
):
    return {
        "schema": {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": list(required),
                    "additionalProperties": False,
                },
            },
        },
        "roles": set(roles) if roles else None,
        "route": route or name,
        "fixed": fixed or {},
        "rename": rename or {},
    }


JOB_OPTIONS = {
    "cpus": INTEGER,
    "ram_mb": INTEGER,
    "timeout": N,
    "wait_for_seconds": N,
    "kind": {"enum": ["research", "evaluation", "finalization"]},
}
CATALOG = [
    spec(
        "send_message",
        "Address an agent ID, team ID (routes to its lead), supervisor, or unique role in your team. assignment updates a subordinate's focus. finding records a reported conclusion. blocker alerts both leads; candidate_id blocks publication. References preserve evidence.",
        {
            "to": S,
            "text": S,
            "kind": {"enum": ["message", "assignment", "finding", "blocker"]},
            "references": STRINGS,
            "candidate_id": S,
            "next_action": S,
        },
        ["to", "text"],
    ),
    spec(
        "set_reasoning",
        "Choose low/medium/high for your next turns, with a reason. Use high for difficult scientific decisions; routine coordination can use medium. Compaction uses its separate light setting. Do not spend turns changing this unnecessarily.",
        {"effort": {"enum": ["low", "medium", "high"]}, "reason": S},
        ["effort", "reason"],
    ),
    spec(
        "read_file",
        "Read text by absolute path. Read code/CSV deliberately in pages; exact originals remain available.",
        {"path": S, "start_line": INTEGER, "lines": INTEGER},
        ["path"],
        route="files",
        fixed={"action": "read"},
    ),
    spec(
        "list_files",
        "List a task or artifact directory. Use absolute paths from current state.",
        {"path": S},
        ["path"],
        route="files",
        fixed={"action": "list"},
    ),
    spec(
        "write_file",
        "Write arbitrary Python or other text in your workspace. Managers delegate implementation.",
        {"path": S, "content": S},
        ["path", "content"],
        WORKERS,
        "files",
        {"action": "write"},
    ),
    spec(
        "append_file",
        "Append text/code in your workspace, e.g. to complete a long source file across calls.",
        {"path": S, "content": S},
        ["path", "content"],
        WORKERS,
        "files",
        {"action": "append"},
    ),
    spec(
        "replace_text",
        "Make a precise literal edit in your source_path (editable workspace file), not the job's frozen script. old_text must occur exactly expected_matches times (default 1). Read the source if it does not match. Useful for repairing a traceback and rerunning source_path.",
        {"path": S, "old_text": S, "new_text": S, "expected_matches": INTEGER},
        ["path", "old_text", "new_text"],
        WORKERS,
        "files",
        {"action": "replace"},
    ),
    spec(
        "run_python",
        "Run arbitrary Python in a managed process. Source is saved and snapshotted. JOB_DIR is private output storage; TASK_ROOT contains task data; RESEARCH_CPU is your thread budget. Short jobs return their result/logs directly (wait_for_seconds defaults to 10, range 0..30); longer jobs return a real job_id and wake you on completion. Decide your own ML methods using the allowed stack.",
        {"code": S, "purpose": S, **JOB_OPTIONS},
        ["code", "purpose"],
        WORKERS,
        "python_job",
        {"action": "run"},
    ),
    spec(
        "run_script",
        "Run your saved .py file in a private managed job. Source is frozen; outputs belong in JOB_DIR. Short jobs return their result directly; wait_for_seconds (default 10, 0..30) controls only inline observation, not execution timeout. Fix small errors with replace_text and rerun this path.",
        {"path": S, "purpose": S, "args": STRINGS, **JOB_OPTIONS},
        ["path", "purpose"],
        WORKERS,
        "python_job",
        {"action": "run"},
    ),
    spec(
        "job_status",
        "Read a real job_id returned by run_python/run_script, including its log tails. Wait for completion events rather than repeatedly polling.",
        {"job_id": S},
        ["job_id"],
        route="python_job",
        fixed={"action": "status"},
    ),
    spec(
        "cancel_job",
        "Cancel obsolete work owned by you or your team; tech lead can cancel across teams.",
        {"job_id": S},
        ["job_id"],
        route="python_job",
        fixed={"action": "cancel"},
    ),
    spec(
        "write_note",
        "Keep a concise durable working note. Personal hypotheses are not verified facts. Only team lead edits the team synthesis; tech lead edits global strategy.",
        {"text": S, "scope": {"enum": ["personal", "team", "global"]}},
        ["text"],
        route="memory",
        fixed={"action": "write"},
    ),
    spec(
        "read_memory",
        "Read personal/global notes or the shared team board.",
        {"scope": {"enum": ["personal", "team", "global"]}},
        route="memory",
        fixed={"action": "read"},
    ),
    spec(
        "publish_finding",
        "Append a finding with evidence. Mark hypotheses as such. share=true asks tech lead to route useful evidence to another team. Publication does not make a claim scientifically verified.",
        {
            "text": S,
            "references": STRINGS,
            "title": S,
            "share": B,
            "claim_status": {"enum": ["hypothesis", "reported"]},
        },
        ["text", "references"],
        WORKERS | {"team_lead"},
        "memory",
        {"action": "publish"},
    ),
    spec(
        "assess_finding",
        "Evaluator: confirm, refute or qualify a reported finding against evidence. Preserve the original claim and explain the inference; do not silently overwrite history.",
        {
            "finding_id": S,
            "status": {"enum": ["verified", "refuted", "needs_check"]},
            "text": S,
            "references": STRINGS,
        },
        ["finding_id", "status", "text", "references"],
        {"evaluator"},
        "memory",
        {"action": "assess"},
    ),
    spec(
        "share_finding",
        "Tech lead routes an immutable finding, with provenance, to a relevant team. Keep ownership of directions clear.",
        {"finding_id": S, "team_id": S, "guidance": S},
        ["finding_id", "team_id", "guidance"],
        {"tech_lead"},
        "memory",
        {"action": "share"},
    ),
    spec(
        "recall",
        "Retrieve factual records and archives without loading every transcript into working context.",
        {
            "topic": {
                "enum": [
                    "job",
                    "candidate",
                    "finding",
                    "team",
                    "team_journal",
                    "messages",
                    "archives",
                    "protocol",
                    "research_report",
                ]
            },
            "record_id": S,
            "offset": INTEGER,
            "limit": INTEGER,
        },
        ["topic"],
        route="memory",
        fixed={"action": "recall"},
    ),
    spec(
        "compact_context",
        "Close an old working episode into an evidence-linked summary. Original task, notes, artifacts and exact transcript remain available. Do not use summaries as proof.",
        {},
        route="memory",
        fixed={"action": "compact"},
    ),
    spec(
        "register_candidate",
        "ML engineer: freeze a ready prediction file and immediately hand it to evaluator. References should include the exact validation predictions/split, scorer and final inference code. allow_partial_source can salvage a file from a failed/interrupted job, but only independent review can approve it.",
        {
            "job_id": S,
            "submission_path": S,
            "findings": OBJECT,
            "references": STRINGS,
            "protocol_id": S,
            "allow_partial_source": B,
        },
        ["job_id", "submission_path"],
        {"ml_engineer"},
        "candidate",
        {"action": "register"},
    ),
    spec(
        "inspect_candidate",
        "Read the exact registered artifact, provenance, claims and reviewer state.",
        {"candidate_id": S},
        ["candidate_id"],
        route="candidate",
        fixed={"action": "inspect"},
    ),
    spec(
        "list_candidates",
        "List candidate records; a file at task root is not equivalent to reviewed publication.",
        {},
        route="candidate",
        fixed={"action": "list"},
    ),
    spec(
        "review_candidate",
        "Evaluator: record a verdict after your independent successful verification job. Check actual targets, row alignment, split-before-fit for learned transforms, and the exact final aggregate prediction function. Format/variance or claimed component scores do not establish predictive quality.",
        {
            "candidate_id": S,
            "job_id": S,
            "approved": B,
            "conclusion": S,
            "findings": OBJECT,
            "protocol_id": S,
            "comparable": B,
        },
        ["candidate_id", "job_id", "approved", "conclusion"],
        {"evaluator"},
        "candidate",
        {"action": "review"},
    ),
    spec(
        "flag_candidate",
        "Evaluator: immediately block a candidate once a correctness issue is found. Inform both leads now; further reproduction can continue afterwards.",
        {"candidate_id": S, "reason": S, "references": STRINGS},
        ["candidate_id", "reason", "references"],
        {"evaluator"},
        "candidate",
        {"action": "flag"},
    ),
    spec(
        "publish_candidate",
        "Tech lead publishes only a candidate already approved by evaluator. This operation cannot confer approval.",
        {"candidate_id": S},
        ["candidate_id"],
        {"tech_lead"},
        "candidate",
        {"action": "publish"},
    ),
    spec(
        "wait",
        "Suspend until an addressed message or job completion. Omit seconds for event-only waiting; supply a timer only for an actual decision checkpoint. Avoid status polling while work is running. Put last in the turn.",
        {"seconds": N},
    ),
    spec(
        "create_team",
        "Tech lead creates a four-role team: lead, ML engineer, data engineer, evaluator. Specify a research question, scope and exclusions; implementation methods remain open. budget_seconds allocates part of the remaining global time, not a fixed research/finalization split.",
        {
            "direction": S,
            "goal": S,
            "purpose": {"enum": ["delivery", "research"]},
            "scope": S,
            "out_of_scope": S,
            "deliverables": STRINGS,
            "budget_seconds": N,
            "resources": OBJECT,
        },
        ["direction", "goal"],
        {"tech_lead"},
        "team",
        {"action": "create"},
    ),
    spec(
        "inspect_team",
        "Read team charter, journal, focus, timing plan, work and findings.",
        {"team_id": S},
        ["team_id"],
        MANAGERS,
        "team",
        {"action": "inspect"},
    ),
    spec(
        "add_specialist",
        "Add an additional engineer with an independent assignment; the minimum team already exists. Reuse an existing specialist when its context is relevant.",
        {"team_id": S, "role": {"enum": ["ml_engineer", "data_engineer"]}, "assignment": S},
        ["team_id", "role", "assignment"],
        MANAGERS,
        "team",
        {"action": "staff"},
    ),
    spec(
        "reassign_team",
        "Tech lead changes the team's question while retaining relevant expertise and evidence. Revision changes are visible to all members.",
        {
            "team_id": S,
            "direction": S,
            "goal": S,
            "purpose": {"enum": ["delivery", "research"]},
            "scope": S,
            "out_of_scope": S,
            "deliverables": STRINGS,
        },
        ["team_id", "direction", "goal"],
        {"tech_lead"},
        "team",
        {"action": "reassign"},
    ),
    spec(
        "close_team",
        "Tech lead winds down or archives a direction. Graceful mode collects handover; immediate mode cancels work. Evidence referenced elsewhere is preserved.",
        {
            "team_id": S,
            "reason": S,
            "mode": {"enum": ["graceful", "immediate"]},
            "grace_seconds": N,
            "archive": B,
        },
        ["team_id", "reason"],
        {"tech_lead"},
        "team",
        {"action": "close"},
    ),
    spec(
        "restart_agent",
        "Lead starts a fresh episode for one subordinate, preserving jobs/evidence. Old instructions and interrupted calls are archived, not blindly replayed.",
        {"team_id": S, "agent_id": S, "assignment": S, "keep_note": B},
        ["team_id", "agent_id", "assignment"],
        MANAGERS,
        "team",
        {"action": "restart_agent"},
    ),
    spec(
        "allocate_team_time",
        "Tech lead assigns/extends a team's budget within the immutable global deadline. Does not cancel existing work automatically.",
        {"team_id": S, "seconds": N, "reason": S},
        ["team_id", "seconds", "reason"],
        {"tech_lead"},
        "timing",
        {"action": "allocate"},
    ),
    spec(
        "plan_team",
        "Team lead manages its own time: choose when to seek the first verified candidate, how much allocated time to reserve for checking/refit, and when to review progress. No fixed split is imposed. Change the plan as measured job durations change. phase=research/finalization explicitly overrides automatic use of your chosen reserve; auto follows it.",
        {
            "team_id": S,
            "reason": S,
            "first_result_in_seconds": N,
            "review_in_seconds": N,
            "finalization_reserve_seconds": N,
            "phase": {"enum": ["auto", "research", "finalization"]},
        },
        ["team_id", "reason"],
        {"team_lead"},
        "timing",
        {"action": "plan"},
    ),
    spec(
        "plan_run",
        "Tech lead chooses an optional global finalization reserve or enters finalization explicitly. Default is no preset split. Running work is cancelled only if requested. Local team timing belongs to team leads.",
        {"reason": S, "reserve_seconds": N, "finalize_now": B, "cancel_research": B},
        ["reason"],
        {"tech_lead"},
        "timing",
        {"action": "global"},
    ),
    spec(
        "plan_portfolio",
        "Tech lead records the current research strategy and next portfolio review. Compare independent questions, team reuse/new team, evidence, feasibility and resource cost. No fixed number of teams. A slow/failed experiment calls for replanning, not automatic finish.",
        {"strategy": S, "next_review_in_seconds": N},
        ["strategy", "next_review_in_seconds"],
        {"tech_lead"},
        "timing",
        {"action": "portfolio"},
    ),
    spec(
        "propose_evaluation",
        "Evaluator publishes an independently exercised assessment method. Split objects before fitting learned transforms; preserve alignment and compare exact deployed predictions. Check code/data flow rather than trusting a printed score. Earlier useful artifacts may stay provisional meanwhile.",
        {"description": S, "verification_job_id": S, "references": STRINGS},
        ["description", "verification_job_id", "references"],
        {"evaluator"},
        "research",
        {"action": "protocol"},
        {"description": "text", "verification_job_id": "job_id"},
    ),
    spec(
        "adopt_evaluation",
        "Tech lead adopts the evaluator's version for comparable research; a version label does not prove scientific correctness.",
        {"protocol_id": S},
        ["protocol_id"],
        {"tech_lead"},
        "research",
        {"action": "adopt"},
        {"protocol_id": "record_id"},
    ),
    spec(
        "inspect_protocol",
        "Read the agreed method and frozen assessment artifacts.",
        {"protocol_id": S},
        ["protocol_id"],
        route="research",
        fixed={"action": "inspect", "topic": "protocol"},
        rename={"protocol_id": "record_id"},
    ),
    spec(
        "report_research",
        "Optionally record a structured research conclusion with evidence. Ordinary finding/blocker messages are also journaled automatically.",
        {"question": S, "learned": S, "next_action": S, "references": STRINGS},
        ["question", "learned", "next_action", "references"],
        WORKERS | {"team_lead"},
        "research",
        {"action": "report"},
        {"learned": "text"},
    ),
    spec(
        "compare_candidates",
        "Check evaluator-confirmed comparability; do not rank incompatible scores or assume an ensemble beats its components.",
        {"candidate_ids": STRINGS},
        ["candidate_ids"],
        MANAGERS | {"evaluator"},
        "research",
        {"action": "compare"},
    ),
    spec(
        "finish",
        "End after reviewed publication, team handovers and closure. Explain why no useful feasible question remains, including known finalist caveats. Cannot stop active teams/jobs. Publishing a checkpoint alone is not a stopping reason; per-job timeouts can be changed.",
        {"reason": S},
        ["reason"],
        roles={"tech_lead"},
    ),
]


# Preserve the exact legacy catalog for resumed runs; new runs freeze revision 2.
LEGACY_CATALOG = deepcopy(CATALOG)
for item in CATALOG:
    name = item["schema"]["function"]["name"]
    if item["roles"] and "data_engineer" in item["roles"]:
        item["roles"].add("data_analyst")
        if name not in ("run_python", "run_script"):
            item["roles"].add("report_editor")
    if name == "create_team":
        item["schema"]["function"]["description"] = (
            "Choose explicit specialists for this research question. No predefined team is added. "
            "A single specialist reports to tech lead; coordinator=true creates a team lead. "
            "Include a reason for composition. Independent evaluators can be shared across directions."
        )
        params = item["schema"]["function"]["parameters"]
        params["properties"].update(
            {
                "members": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "role": {
                                "enum": [
                                    "ml_engineer",
                                    "data_engineer",
                                    "data_analyst",
                                    "evaluator",
                                    "report_editor",
                                ]
                            },
                            "assignment": S,
                        },
                        "required": ["role", "assignment"],
                    },
                },
                "coordinator": B,
                "reason": S,
            }
        )
        params["required"] += ["members", "reason"]
    if name == "add_specialist":
        item["schema"]["function"]["description"] = (
            "Add a missing capability with an explicit assignment and reason; no minimum staffing is assumed."
        )
        params = item["schema"]["function"]["parameters"]
        params["properties"]["role"]["enum"] = [
            "ml_engineer",
            "data_engineer",
            "data_analyst",
            "evaluator",
            "report_editor",
        ]
        params["properties"]["reason"] = S
        params["required"].append("reason")
    if name == "plan_team":
        item["roles"].add("tech_lead")
    if name == "recall":
        item["schema"]["function"]["parameters"]["properties"]["topic"]["enum"] += [
            "assignment",
            "assignment_revision",
            "agent_session",
            "role_profile",
            "staffing_change",
        ]
CATALOG += [
    spec(
        "complete_assignment",
        "Preserve structured handover for your assignment; requires no active own job. Does not verify results or end the run.",
        {"handover": Handover.model_json_schema()},
        ["handover"],
        route="staffing",
        fixed={"action": "complete"},
    ),
    spec(
        "retire_specialist",
        "Retire an idle specialist after its handover; release an agent slot and preserve archives. Cannot retire yourself, a lead or an assigned pending reviewer.",
        {"team_id": S, "agent_id": S, "reason": S},
        ["team_id", "agent_id", "reason"],
        MANAGERS,
        "team",
        {"action": "retire"},
    ),
    spec(
        "assign_reviewer",
        "Assign an independent active evaluator to an ML candidate, including another team's evaluator. Inspect reviewer_load; this grants no approval.",
        {"candidate_id": S, "reviewer_id": S, "reason": S},
        ["candidate_id", "reviewer_id", "reason"],
        MANAGERS,
        "staffing",
        {"action": "reviewer"},
    ),
]


V2_CATALOG = deepcopy(CATALOG)
CATALOG += [
    spec(
        "declare_delivery",
        "Freeze analysis/ml/mixed delivery criteria matching the original goal; does not execute work.",
        {"kind": {"enum": ["analysis", "ml", "mixed"]}, "criteria": STRINGS, "reason": S},
        ["kind", "criteria", "reason"],
        {"tech_lead"},
        "results",
        {"action": "declare"},
    ),
    spec(
        "register_result",
        "Freeze a bounded JSON dataset/table/metric/finding/chart/report from your successful job. Editor may assemble chart/report JSON in own workspace. Numeric payloads need selection, sample_size and units. Prepared table: columns [{name,label,type,unit}], rows; chart: type,table_id,x,y,x_label,y_label,caption; report: summary,blocks [{result_id,caption,required}],limitations. Read exact revisions instead of duplicating data.",
        {
            "kind": {"enum": ["dataset", "table", "metric", "finding", "chart", "report"]},
            "title": S,
            "path": S,
            "job_id": S,
            "dependencies": STRINGS,
            "previous_id": S,
        },
        ["kind", "title", "path"],
        {"data_analyst", "data_engineer", "ml_engineer", "evaluator", "report_editor"},
        "results",
        {"action": "register"},
    ),
    spec(
        "inspect_result",
        "Read exact result payload, frozen artifact hash, status and latest assessment.",
        {"result_id": S},
        ["result_id"],
        route="results",
        fixed={"action": "inspect"},
    ),
    spec(
        "list_results",
        "List bounded metadata for saved analytical revisions without loading raw data.",
        {},
        route="results",
        fixed={"action": "list"},
    ),
    spec(
        "assess_result",
        "Evaluator: freeze own successful verification job's JSON evidence for an exact result revision. Never self-approve. Verification JSON: result_id,sha256,status,checks [{name,passed,detail}],conclusion.",
        {"result_id": S, "job_id": S, "path": S},
        ["result_id", "job_id", "path"],
        {"evaluator"},
        "results",
        {"action": "assess"},
    ),
    spec(
        "publish_report",
        "Save a report page revision. Editor may save draft=true. Only tech lead publishes checked reports; outcome=inconclusive discloses supported data/method limitations. Required evidence must have independent assessments; draft is not final delivery.",
        {"report_id": S, "outcome": {"enum": ["complete", "inconclusive"]}, "draft": B},
        ["report_id", "outcome"],
        {"tech_lead", "report_editor"},
        "results",
        {"action": "publish"},
    ),
]


V3_CATALOG = deepcopy(CATALOG)
CATALOG += [
    spec(
        "search_knowledge",
        "Search document evidence in the Run's pinned versions; also attributed memory or historical result metadata",
        {
            "query": S,
            "kind": {"enum": ["documents", "memory", "results", "all"]},
            "include_historical": B,
            "limit": INTEGER,
        },
        ["query"],
        route="document_search",
    ),
    spec(
        "read_source",
        "Read an exact source, preserving its version/hash and citation; neighbors 0..2. Saved document evidence survives later deletion",
        {"source_id": S, "neighbors": INTEGER},
        ["source_id"],
        route="document_read",
    ),
]


V4_CATALOG = deepcopy(CATALOG)

CATALOG += [
    spec("list_pages", "List project/team pages and current draft/publication identifiers", {}),
    spec(
        "read_page",
        "Read a bounded draft, pins, user comments and publication state",
        {"page_id": S},
        ["page_id"],
    ),
]
for _name, _contract, _description in [
    (
        "save_page",
        PageSave,
        "Save a versioned draft of your page using exact result references; preserves user pins. New client_id per logical edit, expected_revision detects conflicts.",
    ),
    (
        "publish_page",
        PagePublish,
        "Publish an exact saved revision with independently accepted required evidence. Does not approve results or finish the research Run.",
    ),
]:
    _schema = _contract.model_json_schema()
    if _name == "publish_page":
        _schema["properties"]["page_id"] = S
    _schema["required"] = list(_schema["properties"])
    for _definition in _schema.get("$defs", {}).values():
        if "properties" in _definition:
            _definition["required"] = list(_definition["properties"])
    _tool = spec(_name, _description, {}, roles={"tech_lead", "team_lead", "report_editor"})
    _tool["schema"]["function"]["parameters"] = _schema
    CATALOG.append(_tool)


def tools_for(role, revision=5):
    catalog = (
        LEGACY_CATALOG
        if revision == 1
        else V2_CATALOG
        if revision == 2
        else V3_CATALOG
        if revision == 3
        else V4_CATALOG
        if revision == 4
        else CATALOG
    )
    return [
        deepcopy(item["schema"])
        for item in catalog
        if item["roles"] is None or role in item["roles"]
    ]


def validate_value(value, schema, path="arguments"):
    """Validate the bounded JSON-schema dialect used by this tool catalog."""
    import math

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: choose one of {schema['enum']}")
    kind = schema.get("type")
    checks = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value),
    }
    if kind and not checks.get(kind, False):
        raise ValueError(f"{path}: expected {kind}")
    if kind == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        extra = set(value) - set(properties)
        if missing or (extra and schema.get("additionalProperties") is False):
            raise ValueError(f"{path}: missing {sorted(missing)}, unexpected {sorted(extra)}")
        for name, item in value.items():
            if name in properties:
                validate_value(item, properties[name], path + "." + name)
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 100000):
            raise ValueError(f"{path}: invalid item count")
        for item in value:
            validate_value(item, schema.get("items", {}), path + "[]")
    if kind == "string" and not schema.get("minLength", 0) <= len(value) <= schema.get(
        "maxLength", 1000000
    ):
        raise ValueError(f"{path}: invalid string length")


def resolve_call(role, name, arguments, revision=5, schemas=None):
    catalog = (
        LEGACY_CATALOG
        if revision == 1
        else V2_CATALOG
        if revision == 2
        else V3_CATALOG
        if revision == 3
        else V4_CATALOG
        if revision == 4
        else CATALOG
    )
    item = next((i for i in catalog if i["schema"]["function"]["name"] == name), None)
    if item is None or (item["roles"] is not None and role not in item["roles"]):
        raise ValueError(f"{name} is unavailable to {role}; use your role's listed operations")
    schema = item["schema"]["function"]["parameters"]
    if schemas is not None:
        saved = next((s for s in schemas if s["function"]["name"] == name), None)
        if saved is None:
            raise ValueError("Tool is outside the frozen role profile")
        schema = saved["function"]["parameters"]
    validate_value(arguments, schema)
    args = {item["rename"].get(k, k): v for k, v in arguments.items()}
    args.update(item["fixed"])
    if name == "close_team" and args.pop("archive", False):
        args["action"] = "delete"
    return item["route"], args
