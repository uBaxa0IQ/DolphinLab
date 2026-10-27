"""Immutable analytical revisions, evidence, review and report publication."""

import hashlib
import json
import time
from pathlib import Path

from dolphinlab.common.serialization import uid
from dolphinlab.domain.results import PAYLOADS, VerificationData


class ResultRegistry:
    def __init__(self, runtime):
        self.rt, self.store = runtime, runtime.store

    def contract(self):
        records = self.store.all("delivery_contract")
        return (
            records[0]
            if records
            else {"kind": self.rt.config.goal_spec.get("research_type", "ml"), "criteria": []}
        )

    def declare(self, agent, args):
        if agent["role"] != "tech_lead":
            raise ValueError("Only tech lead declares delivery criteria")
        fixed = self.rt.config.goal_spec.get("research_type", "ml")
        if fixed != "auto" and fixed != args["kind"]:
            raise ValueError("Delivery kind contradicts the frozen goal")
        if (
            not args["criteria"]
            or len(args["criteria"]) > 20
            or any(not c.strip() or len(c) > 1000 for c in args["criteria"])
        ):
            raise ValueError("Declare 1..20 concrete delivery criteria")
        record = {
            "id": "current",
            "kind": args["kind"],
            "criteria": args["criteria"],
            "reason": args["reason"],
        }
        old = self.store.all("delivery_contract")
        if old and old[0] != record:
            raise ValueError("Delivery contract is frozen; a scope change needs a new iteration")
        self.store.put("delivery_contract", "current", record)
        return record

    def artifact(self, agent, path, job_id=None, *, presentation=False):
        path = self.rt.readable(path)
        job = self.store.get("job", job_id) if job_id else None
        if job:
            if job["owner"] != agent["id"] or job["state"] != "succeeded":
                raise ValueError("Cite your own successful source job")
            allowed = Path(job["folder"]).resolve()
        elif presentation and agent["role"] == "report_editor":
            allowed = Path(agent["workspace"]).resolve()
        else:
            raise ValueError("A scientific result needs an executed source job")
        if not path.is_relative_to(allowed) or not path.is_file() or path.is_symlink():
            raise ValueError("Artifact must belong to the cited job or your presentation workspace")
        if not 0 < path.stat().st_size <= 1024 * 1024:
            raise ValueError("Prepare a JSON view of at most 1 MiB; do not send the raw dataset")
        ident = uid("artifact")
        target = self.rt.config.work_dir / "results" / ident / "payload.json"
        target.parent.mkdir(parents=True)
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Source exceeds the prepared-view limit")
        original_hash = hashlib.sha256(raw).hexdigest()
        target.write_bytes(raw)
        with path.open("rb") as stream:
            current = stream.read(1024 * 1024 + 1)
        if hashlib.sha256(current).hexdigest() != original_hash:
            raise ValueError("Source changed while freezing the artifact")
        payload = json.loads(
            target.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON")),
        )
        record = {
            "id": ident,
            "path": str(target),
            "sha256": original_hash,
            "size": target.stat().st_size,
            "source_path": str(path),
            "job_id": job_id,
            "author": agent["id"],
            "created": time.time(),
        }
        self.store.put("artifact", ident, record)
        return record, payload

    def get(self, ident):
        return self.store.get("result_revision", ident)

    def latest_assessment(self, ident):
        records = [a for a in self.store.all("assessment") if a["result_id"] == ident]
        return records[-1] if records else None

    def artifact_valid(self, ident):
        artifact = self.store.get("artifact", ident)
        path = Path(artifact["path"])
        if not path.is_file() or path.is_symlink() or path.stat().st_size != artifact["size"]:
            return False
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        return (
            len(raw) == artifact["size"] and hashlib.sha256(raw).hexdigest() == artifact["sha256"]
        )

    def status(self, result, *, files=True):
        if files and not self.artifact_valid(result["artifact_id"]):
            return "broken"
        assessment = self.latest_assessment(result["id"])
        if assessment and files:
            if not self.artifact_valid(assessment["artifact_id"]):
                return "broken"
        return assessment["status"] if assessment else "reported"

    def closure(self, ident, *, required=False, seen=None):
        seen = set() if seen is None else seen
        if ident in seen:
            return seen
        if len(seen) >= 100:
            raise ValueError("Result dependency graph is too large")
        result = self.get(ident)
        seen.add(ident)
        for dep in result["required_dependencies" if required else "dependencies"]:
            self.closure(dep, required=required, seen=seen)
        return seen

    def register(self, agent, args):
        kind = args["kind"]
        if not 1 <= len(args["title"].strip()) <= 300:
            raise ValueError("Result title must contain 1..300 characters")
        if kind not in PAYLOADS:
            raise ValueError("Unsupported analytical result kind")
        if agent["role"] == "report_editor" and kind not in ("chart", "report"):
            raise ValueError(
                "Editor can assemble charts/reports, not originate scientific measurements"
            )
        artifact, payload = self.artifact(
            agent, args["path"], args.get("job_id"), presentation=kind in ("chart", "report")
        )
        payload = PAYLOADS[kind].model_validate(payload).model_dump(mode="json")
        explicit = list(dict.fromkeys(args.get("dependencies", [])))
        required, deps = list(explicit), list(explicit)
        if kind == "chart":
            table = self.get(payload["table_id"])
            if table["kind"] not in ("table", "dataset"):
                raise ValueError("A chart needs a fixed prepared table revision")
            columns = {c["name"]: c for c in table["payload"]["columns"]}
            if (
                payload["x"] not in columns
                or payload["y"] not in columns
                or columns[payload["y"]]["type"] != "number"
            ):
                raise ValueError("Chart fields must exist and y must be numeric")
            if payload["type"] == "scatter" and columns[payload["x"]]["type"] != "number":
                raise ValueError("Scatter requires a numeric x axis")
            required.append(table["id"])
            deps.append(table["id"])
        if kind == "report":
            if not any(b["required"] for b in payload["blocks"]):
                raise ValueError("A report needs at least one required evidence block")
            for block in payload["blocks"]:
                if self.get(block["result_id"])["kind"] == "report":
                    raise ValueError("Nested reports are not supported")
                deps.append(block["result_id"])
                if block["required"]:
                    required.append(block["result_id"])
        if len(deps) > 30:
            raise ValueError("Too many dependencies")
        for ident in deps:
            self.get(ident)
        previous = self.get(args["previous_id"]) if args.get("previous_id") else None
        if previous and (previous["author"] != agent["id"] or previous["kind"] != kind):
            raise ValueError("Create a revision of your own result of the same kind")
        if previous and any(
            r["result_id"] == previous["result_id"] and r["revision"] > previous["revision"]
            for r in self.store.all("result_revision")
        ):
            raise ValueError("Base revision is stale; inspect the latest result")
        ident = uid("result")
        result = {
            "id": ident,
            "result_id": previous["result_id"] if previous else ident,
            "revision": previous["revision"] + 1 if previous else 1,
            "previous_id": previous["id"] if previous else None,
            "kind": kind,
            "title": args["title"],
            "author": agent["id"],
            "team_id": agent["team_id"],
            "artifact_id": artifact["id"],
            "sha256": artifact["sha256"],
            "job_id": args.get("job_id"),
            "assignment_id": agent.get("assignment_id"),
            "scope": {**self.rt.config.scope, "run_id": self.store.run_id},
            "input_versions": self.rt.config.input_versions,
            "payload": payload,
            "dependencies": list(dict.fromkeys(deps)),
            "required_dependencies": list(dict.fromkeys(required)),
            "created": time.time(),
        }
        if args.get("job_id"):
            source_job = self.store.get("job", args["job_id"])
            result["source_job"] = {
                k: source_job.get(k)
                for k in ("id", "script", "folder", "source_fingerprint", "metadata")
            }
        self.store.put("result_revision", ident, result)
        self.closure(ident)
        self.rt.notify(
            agent.get("supervisor") or self.rt.root["id"],
            "Result ready for independent review: " + ident,
            agent["id"],
        )
        return self.summary(result)

    def assess(self, agent, args):
        if agent["role"] != "evaluator":
            raise ValueError("Only evaluator records independent assessments")
        result = self.get(args["result_id"])
        for ident in self.closure(result["id"]):
            dependency = self.get(ident)
            source = self.store.get("job", dependency["job_id"]) if dependency["job_id"] else None
            if dependency["author"] == agent["id"] or (source and source["owner"] == agent["id"]):
                raise ValueError(
                    "Cannot independently review your own result, source job or dependent evidence"
                )
        job = self.store.get("job", args["job_id"])
        if job["submitted"] < result["created"]:
            raise ValueError("Verification must execute after this exact result revision exists")
        artifact, payload = self.artifact(agent, args["path"], args["job_id"])
        verification = VerificationData.model_validate(payload).model_dump(mode="json")
        if verification["result_id"] != result["id"] or verification["sha256"] != result["sha256"]:
            raise ValueError("Verification must cite the exact result ID and artifact hash")
        if not self.artifact_valid(result["artifact_id"]):
            raise ValueError("Result artifact no longer matches its frozen hash")
        record = {
            "id": uid("assessment"),
            **verification,
            "reviewer": agent["id"],
            "job_id": job["id"],
            "artifact_id": artifact["id"],
            "created": time.time(),
        }
        self.store.put("assessment", record["id"], record)
        current = self.current()
        if (
            current
            and result["id"] in self.closure(current["report_id"], required=True)
            and record["status"] != "verified"
        ):
            self.store.put(
                "report_publication",
                "current",
                {"revoked_page_id": current["page_id"], "reason": "A required assessment changed"},
            )
        self.rt.notify(
            self.rt.root["id"], "Assessment: " + result["id"] + " " + record["status"], agent["id"]
        )
        return record

    def publish(self, agent, args):
        report = self.get(args["report_id"])
        if report["kind"] != "report":
            raise ValueError("Choose a report revision")
        draft = args.get("draft", False)
        if not draft and agent["role"] != "tech_lead":
            raise ValueError("Editor prepares drafts; tech lead publishes checked delivery")
        contract = self.contract()
        if not draft and (contract["kind"] == "auto" or not contract["criteria"]):
            raise ValueError("Declare a delivery contract before publication")
        closure = self.closure(report["id"], required=True)
        accepted = {"verified"} if args["outcome"] == "complete" else {"verified", "inconclusive"}
        statuses = {ident: self.status(self.get(ident)) for ident in self.closure(report["id"])}
        if not draft and any(statuses[ident] not in accepted for ident in closure):
            raise ValueError("Required report evidence is unverified, refuted or broken")
        assessment = self.latest_assessment(report["id"])
        if not draft and not set(contract["criteria"]) <= {c["name"] for c in assessment["checks"]}:
            raise ValueError("Report assessment must explicitly address every delivery criterion")
        page = {
            "id": uid("page"),
            "report_id": report["id"],
            "title": report["title"],
            "author": agent["id"],
            "draft": draft,
            "outcome": args["outcome"],
            "criteria": contract["criteria"],
            "result_ids": sorted(statuses),
            "statuses": statuses,
            "assessments": {
                ident: (self.latest_assessment(ident) or {}).get("id") for ident in statuses
            },
            "created": time.time(),
        }
        self.store.put("page_revision", page["id"], page)
        if not draft:
            self.store.put(
                "report_publication",
                "current",
                {
                    "kind": "report",
                    "page_id": page["id"],
                    "report_id": report["id"],
                    "sha256": report["sha256"],
                    "outcome": args["outcome"],
                },
            )
        return page

    def current(self):
        records = self.store.all("report_publication")
        return records[0] if records and records[0].get("page_id") else None

    def validate_publication(self):
        current = self.current()
        if not current:
            return None
        page = self.store.get("page_revision", current["page_id"])
        accepted = {"verified"} if page["outcome"] == "complete" else {"verified", "inconclusive"}
        if any(
            self.status(self.get(ident)) not in accepted
            for ident in self.closure(page["report_id"], required=True)
        ):
            self.store.put(
                "report_publication",
                "current",
                {"revoked_page_id": page["id"], "reason": "Evidence is no longer valid"},
            )
            return None
        return current

    def summary(self, result):
        return {
            k: result.get(k)
            for k in (
                "id",
                "result_id",
                "revision",
                "kind",
                "title",
                "author",
                "team_id",
                "job_id",
                "sha256",
                "artifact_id",
                "created",
                "previous_id",
            )
        }

    def handle(self, agent, action, args):
        with self.store.transaction():
            if action == "declare":
                return self.declare(agent, args)
            if action == "register":
                return self.register(agent, args)
            if action == "assess":
                return self.assess(agent, args)
            if action == "publish":
                return self.publish(agent, args)
            if action == "inspect":
                result = self.get(args["result_id"])
                return {
                    **result,
                    "status": self.status(result),
                    "assessment": self.latest_assessment(result["id"]),
                }
            return [
                {**self.summary(r), "status": self.status(r)}
                for r in self.store.all("result_revision")
            ][-100:]
