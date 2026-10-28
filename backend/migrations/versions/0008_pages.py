"""Immutable research page drafts, publications and user feedback."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def base_columns():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("page_id", sa.String(36), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    ]


def scope_constraint():
    return sa.ForeignKeyConstraint(
        ["project_id", "page_id"], ["project_pages.project_id", "project_pages.id"]
    )


def upgrade():
    op.create_unique_constraint("uq_run_project_id", "runs", ["project_id", "id"])
    op.create_table(
        "project_pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("page_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("run_id", sa.String(36)),
        sa.Column("team_id", sa.String(120)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("feedback_revision", sa.Integer(), nullable=False),
        sa.Column("user_state", sa.JSON(), nullable=False),
        sa.Column("published_id", sa.String(36)),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "page_key", name="uq_page_key"),
        sa.UniqueConstraint("project_id", "id", name="uq_page_project"),
        sa.ForeignKeyConstraint(["project_id", "run_id"], ["runs.project_id", "runs.id"]),
    )
    op.create_table(
        "page_revisions",
        *base_columns(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("author", sa.JSON(), nullable=False),
        sa.Column("feedback_revision", sa.Integer(), nullable=False),
        scope_constraint(),
        sa.UniqueConstraint("page_id", "revision", name="uq_page_revision"),
        sa.UniqueConstraint("project_id", "page_id", "id", name="uq_page_revision_scope"),
        sa.UniqueConstraint("project_id", "request_id", name="uq_page_save_request"),
    )
    op.create_table(
        "page_releases",
        *base_columns(),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("user_state", sa.JSON(), nullable=False),
        sa.Column("author", sa.JSON(), nullable=False),
        scope_constraint(),
        sa.ForeignKeyConstraint(
            ["project_id", "page_id", "revision_id"],
            ["page_revisions.project_id", "page_revisions.page_id", "page_revisions.id"],
        ),
        sa.UniqueConstraint("project_id", "page_id", "id", name="uq_page_release_scope"),
        sa.UniqueConstraint("project_id", "request_id", name="uq_page_publish_request"),
    )
    op.create_foreign_key(
        "fk_page_current_release",
        "project_pages",
        "page_releases",
        ["project_id", "id", "published_id"],
        ["project_id", "page_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "page_feedback",
        *base_columns(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("change", sa.JSON(), nullable=False),
        sa.Column("user_state", sa.JSON(), nullable=False),
        scope_constraint(),
        sa.UniqueConstraint("page_id", "revision", name="uq_page_feedback_revision"),
        sa.UniqueConstraint("project_id", "request_id", name="uq_page_feedback_request"),
    )
    op.create_table(
        "page_refresh_cursor",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
    )
    for table in ("project_pages", "page_revisions", "page_releases", "page_feedback"):
        op.create_index("ix_" + table + "_project_id", table, ["project_id"])
        if table != "project_pages":
            op.create_index("ix_" + table + "_page_id", table, ["page_id"])
    op.execute("""
        CREATE FUNCTION protect_page_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Page history is immutable' USING ERRCODE = '23514';
        END $$;
    """)
    for table in ("page_revisions", "page_releases", "page_feedback"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_page_revision()"
        )


def downgrade():
    op.drop_constraint("fk_page_current_release", "project_pages")
    for table in (
        "page_refresh_cursor",
        "page_feedback",
        "page_releases",
        "page_revisions",
        "project_pages",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION protect_page_revision()")
    op.drop_constraint("uq_run_project_id", "runs")
