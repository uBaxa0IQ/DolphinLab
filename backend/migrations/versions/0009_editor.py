"""Durable project editor queue, independent of completed scientific runs."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "editor_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("source_key", sa.String(120), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "run_id"], ["runs.project_id", "runs.id"]),
        sa.UniqueConstraint("project_id", "source_key", name="uq_editor_source"),
    )
    op.create_index("ix_editor_jobs_project_id", "editor_jobs", ["project_id"])
    op.create_table(
        "editor_activation",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "INSERT INTO editor_activation (id, activated_at) VALUES ('pages', CURRENT_TIMESTAMP)"
    )


def downgrade():
    op.drop_table("editor_jobs")
    op.drop_table("editor_activation")
