"""Version research goals and link runs without rewriting historical snapshots."""

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def spec(question):
    return {
        "schema_version": 1,
        "question": question,
        "expected_outputs": [],
        "definitions": {},
        "constraints": [],
        "open_questions": [],
    }


def ident(kind, source):
    return str(uuid5(NAMESPACE_URL, f"dolphinlab:0003:{kind}:{source}"))


def upgrade():
    goals = op.create_table(
        "project_goals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("request_id", sa.String(36)),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "revision", name="uq_goal_revision"),
        sa.UniqueConstraint("project_id", "id", name="uq_goal_project_id"),
        sa.UniqueConstraint("project_id", "request_id", name="uq_goal_request"),
        sa.CheckConstraint("revision > 0", name="ck_goal_revision_positive"),
    )
    op.create_index("ix_project_goals_project_id", "project_goals", ["project_id"])
    iterations = op.create_table(
        "research_iterations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("goal_revision_id", sa.String(36), nullable=False),
        sa.Column("parent_iteration_id", sa.String(36)),
        sa.Column("legacy", sa.Boolean(), nullable=False),
        sa.Column("request_id", sa.String(36)),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "sequence", name="uq_iteration_sequence"),
        sa.UniqueConstraint("project_id", "id", name="uq_iteration_project_id"),
        sa.UniqueConstraint("project_id", "request_id", name="uq_iteration_request"),
        sa.CheckConstraint("sequence > 0", name="ck_iteration_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["project_id", "goal_revision_id"],
            ["project_goals.project_id", "project_goals.id"],
            name="fk_iteration_goal_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "parent_iteration_id"],
            ["research_iterations.project_id", "research_iterations.id"],
            name="fk_iteration_parent_project",
        ),
    )
    op.create_index("ix_research_iterations_project_id", "research_iterations", ["project_id"])
    op.add_column("runs", sa.Column("iteration_id", sa.String(36)))
    op.add_column("runs", sa.Column("goal_snapshot", sa.JSON()))
    op.add_column("runs", sa.Column("request_id", sa.String(36)))
    op.add_column("runs", sa.Column("request_hash", sa.String(64)))

    db = op.get_bind()
    projects = sa.table(
        "projects",
        sa.column("id"),
        sa.column("name"),
        sa.column("description"),
        sa.column("created"),
    )
    runs = sa.table(
        "runs",
        sa.column("id"),
        sa.column("project_id"),
        sa.column("task_description"),
        sa.column("created"),
        sa.column("iteration_id"),
        sa.column("goal_snapshot", sa.JSON()),
    )
    for project in db.execute(sa.select(projects).order_by(projects.c.id)).mappings():
        history = (
            db.execute(
                sa.select(runs)
                .where(runs.c.project_id == project["id"])
                .order_by(runs.c.created, runs.c.id)
            )
            .mappings()
            .all()
        )
        previous, last_question = None, None
        for number, run in enumerate(history, 1):
            # Run.task_description itself is deliberately left byte-for-byte unchanged.
            question = run["task_description"].strip() or project["name"]
            goal_id, iteration_id = ident("goal", run["id"]), ident("iteration", run["id"])
            snapshot = spec(question)
            db.execute(
                goals.insert().values(
                    id=goal_id,
                    project_id=project["id"],
                    revision=number,
                    spec=snapshot,
                    origin="legacy",
                    created=run["created"],
                )
            )
            db.execute(
                iterations.insert().values(
                    id=iteration_id,
                    project_id=project["id"],
                    sequence=number,
                    goal_revision_id=goal_id,
                    parent_iteration_id=previous,
                    legacy=True,
                    created=run["created"],
                )
            )
            db.execute(
                runs.update()
                .where(runs.c.id == run["id"])
                .values(
                    iteration_id=iteration_id,
                    goal_snapshot=snapshot,
                )
            )
            previous, last_question = iteration_id, question
        current_question = project["description"].strip() or project["name"]
        if current_question != last_question:
            db.execute(
                goals.insert().values(
                    id=ident("project", project["id"]),
                    project_id=project["id"],
                    revision=len(history) + 1,
                    spec=spec(current_question),
                    origin="project",
                    created=datetime.now(timezone.utc),
                )
            )

    op.alter_column("runs", "iteration_id", nullable=False)
    op.alter_column("runs", "goal_snapshot", nullable=False)
    op.create_index("ix_runs_iteration_id", "runs", ["iteration_id"])
    op.create_unique_constraint("uq_run_request", "runs", ["project_id", "request_id"])
    op.create_foreign_key(
        "fk_run_iteration_project",
        "runs",
        "research_iterations",
        ["project_id", "iteration_id"],
        ["project_id", "id"],
    )
    op.execute("""
        CREATE FUNCTION dolphinlab_goal_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Goal revisions are immutable; create a new revision'
                USING ERRCODE = '23514';
        END; $$
    """)
    op.execute("""
        CREATE TRIGGER goal_revision_immutable BEFORE UPDATE ON project_goals
        FOR EACH ROW EXECUTE FUNCTION dolphinlab_goal_immutable()
    """)


def downgrade():
    op.execute("DROP TRIGGER goal_revision_immutable ON project_goals")
    op.execute("DROP FUNCTION dolphinlab_goal_immutable()")
    op.drop_constraint("fk_run_iteration_project", "runs", type_="foreignkey")
    op.drop_constraint("uq_run_request", "runs", type_="unique")
    op.drop_index("ix_runs_iteration_id", table_name="runs")
    for name in ("request_hash", "request_id", "goal_snapshot", "iteration_id"):
        op.drop_column("runs", name)
    op.drop_table("research_iterations")
    op.drop_table("project_goals")
