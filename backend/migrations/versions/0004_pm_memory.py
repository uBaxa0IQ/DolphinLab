"""Project conversation, durable PM actions and evidence-linked working memory."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "messages", sa.Column("intent", sa.String(20), nullable=False, server_default="question")
    )
    op.add_column(
        "messages", sa.Column("context_binding", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column("messages", sa.Column("reply_to_id", sa.String(36)))
    op.create_unique_constraint("uq_message_project_id", "messages", ["project_id", "id"])
    op.add_column("directives", sa.Column("iteration_id", sa.String(36)))
    op.add_column(
        "runs", sa.Column("project_context", sa.JSON(), nullable=False, server_default="{}")
    )
    op.execute("UPDATE messages SET intent = 'instruction' WHERE directive = true")
    op.execute("""UPDATE directives SET iteration_id = runs.iteration_id
                  FROM runs WHERE directives.run_id = runs.id""")
    op.execute("""UPDATE directives SET state = 'needs_target'
                  WHERE run_id IS NULL AND state IN ('received','queued_for_tech_lead','awaiting_pm')""")
    op.create_table(
        "pm_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "id", name="uq_pm_action_project_id"),
        sa.ForeignKeyConstraint(
            ["project_id", "message_id"],
            ["messages.project_id", "messages.id"],
            name="fk_pm_action_message_project",
        ),
    )
    op.create_table(
        "project_memory",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("goal_revision_id", sa.String(36)),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_quote", sa.Text()),
        sa.Column("source_action_id", sa.String(36)),
        sa.Column("input_ids", sa.JSON(), nullable=False),
        sa.Column("supersedes_id", sa.String(36)),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "id", name="uq_memory_project_id"),
        sa.ForeignKeyConstraint(
            ["project_id", "message_id"],
            ["messages.project_id", "messages.id"],
            name="fk_memory_message_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "goal_revision_id"],
            ["project_goals.project_id", "project_goals.id"],
            name="fk_memory_goal_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_action_id"],
            ["pm_actions.project_id", "pm_actions.id"],
            name="fk_memory_action_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["project_memory.project_id", "project_memory.id"],
            name="fk_memory_supersedes_project",
        ),
    )
    op.create_table(
        "context_manifests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "conversation_accounts",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("usage", sa.JSON(), nullable=False),
    )
    for table, columns in (
        ("pm_actions", ("project_id", "message_id")),
        ("project_memory", ("project_id",)),
        ("context_manifests", ("project_id", "message_id")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.alter_column("messages", "intent", server_default=None)
    op.alter_column("messages", "context_binding", server_default=None)
    op.alter_column("runs", "project_context", server_default=None)


def downgrade():
    for table in ("conversation_accounts", "context_manifests", "project_memory", "pm_actions"):
        op.drop_table(table)
    op.drop_column("runs", "project_context")
    op.drop_column("directives", "iteration_id")
    op.drop_constraint("uq_message_project_id", "messages", type_="unique")
    for column in ("reply_to_id", "context_binding", "intent"):
        op.drop_column("messages", column)
