"""Postgres implementation of the research runtime's factual state and durable mail."""

import copy
import json
import time
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from sonder.infrastructure.database import (
    ResearchEvent,
    ResearchMail,
    ResearchObject,
    ResearchReceipt,
    Session,
)


class PostgresStore:
    def __init__(self, run_id, root):
        self.run_id, self.root = run_id, Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_error = None
        self.on_close = None

    def put(self, kind, ident, data):
        with Session.begin() as db:
            statement = insert(ResearchObject).values(
                run_id=self.run_id, kind=kind, object_id=ident, data=copy.deepcopy(data)
            )
            db.execute(
                statement.on_conflict_do_update(
                    index_elements=["run_id", "kind", "object_id"],
                    set_={"data": statement.excluded.data},
                )
            )

    def get(self, kind, ident):
        with Session() as db:
            value = db.scalar(
                select(ResearchObject.data).where(
                    ResearchObject.run_id == self.run_id,
                    ResearchObject.kind == kind,
                    ResearchObject.object_id == ident,
                )
            )
            if value is None:
                raise KeyError(f"Unknown {kind}: {ident}")
            return value

    def all(self, kind):
        with Session() as db:
            return list(
                db.scalars(
                    select(ResearchObject.data)
                    .where(
                        ResearchObject.run_id == self.run_id,
                        ResearchObject.kind == kind,
                    )
                    .order_by(ResearchObject.seq)
                )
            )

    def event(self, kind, data):
        timestamp = time.time()
        with Session.begin() as db:
            event = ResearchEvent(
                run_id=self.run_id,
                kind=kind,
                data=copy.deepcopy(data),
                created=timestamp,
            )
            db.add(event)
            db.flush()
            sequence = event.seq
        try:
            with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "seq": sequence,
                            "time": timestamp,
                            "kind": kind,
                            "data": data,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
        except OSError as error:
            self.log_error = type(error).__name__

    def sync_events(self):
        # PostgreSQL is authoritative; JSONL is only a best-effort export.
        pass

    def send(self, recipient, sender, body):
        if self.get("agent", recipient)["state"] == "closed":
            raise ValueError("Recipient is closed")
        with Session.begin() as db:
            mail = ResearchMail(
                run_id=self.run_id,
                recipient=recipient,
                sender=sender,
                body=body,
                created=time.time(),
            )
            db.add(mail)
            db.flush()
            ident = mail.seq
        self.event("message_sent", {"id": ident, "to": recipient, "from": sender, "text": body})
        return ident

    def pending(self, recipient):
        with Session() as db:
            return (
                db.scalar(
                    select(ResearchMail.seq)
                    .where(
                        ResearchMail.run_id == self.run_id,
                        ResearchMail.recipient == recipient,
                        ResearchMail.delivered.is_(False),
                    )
                    .limit(1)
                )
                is not None
            )

    def take_mail(self, agent, limit=12000):
        with Session.begin() as db:
            rows = db.scalars(
                select(ResearchMail)
                .where(
                    ResearchMail.run_id == self.run_id,
                    ResearchMail.recipient == agent["id"],
                    ResearchMail.delivered.is_(False),
                )
                .order_by(ResearchMail.seq)
                .with_for_update()
                .limit(50)
            ).all()
            chosen, size = [], 0
            for row in rows:
                if chosen and size + len(row.body) > limit:
                    break
                chosen.append(row)
                size += len(row.body)
            if not chosen:
                return 0
            agent["history"].append(
                {
                    "role": "user",
                    "content": "New addressed messages (chronological):\n"
                    + "\n".join(f"[{r.seq} from {r.sender}] {r.body}" for r in chosen),
                }
            )
            statement = insert(ResearchObject).values(
                run_id=self.run_id,
                kind="agent",
                object_id=agent["id"],
                data=copy.deepcopy(agent),
            )
            db.execute(
                statement.on_conflict_do_update(
                    index_elements=["run_id", "kind", "object_id"],
                    set_={"data": statement.excluded.data},
                )
            )
            ids = [row.seq for row in chosen]
            db.execute(update(ResearchMail).where(ResearchMail.seq.in_(ids)).values(delivered=True))
        self.event("mail_delivered", {"agent": agent["id"], "message_ids": ids})
        return len(chosen)

    def receipt(self, agent, call_id):
        with Session() as db:
            receipt = db.get(ResearchReceipt, (self.run_id, agent, call_id))
            return receipt.result if receipt else None

    def save_receipt(self, agent, call_id, result):
        with Session.begin() as db:
            statement = insert(ResearchReceipt).values(
                run_id=self.run_id,
                agent=agent,
                call_id=call_id,
                result=copy.deepcopy(result),
            )
            db.execute(statement.on_conflict_do_nothing())

    def recent_mail(self, recipients, limit=20):
        with Session() as db:
            rows = db.scalars(
                select(ResearchMail)
                .where(
                    ResearchMail.run_id == self.run_id,
                    ResearchMail.recipient.in_(recipients),
                )
                .order_by(ResearchMail.seq.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": r.seq,
                    "to": r.recipient,
                    "from": r.sender,
                    "text": r.body,
                    "created": r.created,
                    "delivered": r.delivered,
                }
                for r in reversed(rows)
            ]

    def retire_mail(self, recipient):
        with Session.begin() as db:
            db.execute(
                update(ResearchMail)
                .where(
                    ResearchMail.run_id == self.run_id,
                    ResearchMail.recipient == recipient,
                )
                .values(delivered=True)
            )

    def archives(self):
        with Session() as db:
            return [
                {"kind": "context_archive", **data}
                for data in db.scalars(
                    select(ResearchEvent.data)
                    .where(
                        ResearchEvent.run_id == self.run_id,
                        ResearchEvent.kind == "context_archive",
                    )
                    .order_by(ResearchEvent.seq)
                )
            ]

    def close(self):
        if self.on_close:
            self.on_close()
            self.on_close = None
