"""Project-scoped versioned documents, ingestion leases and lexical retrieval."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "project_memory", sa.Column("search_text", sa.Text(), nullable=False, server_default="")
    )
    connection = op.get_bind()
    for row in connection.execute(sa.text("SELECT id, text FROM project_memory")).mappings().all():
        connection.execute(
            sa.text("UPDATE project_memory SET search_text=:normalized WHERE id=:id"),
            {"id": row["id"], "normalized": row["text"].casefold()},
        )
    op.alter_column("project_memory", "search_text", server_default=None)
    op.add_column(
        "project_memory", sa.Column("document_refs", sa.JSON(), nullable=False, server_default="[]")
    )
    op.alter_column("project_memory", "document_refs", server_default=None)
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "id", name="uq_document_project_id"),
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("pipeline", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("lease_id", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "document_id"], ["documents.project_id", "documents.id"]
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_document_version_project_id"),
        sa.UniqueConstraint("document_id", "revision", name="uq_document_version_revision"),
        sa.UniqueConstraint("project_id", "request_id", name="uq_document_version_request"),
        sa.CheckConstraint("revision > 0", name="ck_document_revision_positive"),
        sa.CheckConstraint(
            "state IN ('queued','processing','ready','failed','deleted')", name="ck_document_state"
        ),
    )
    for col in ("project_id", "document_id", "state"):
        op.create_index("ix_document_versions_" + col, "document_versions", [col])
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column(
            "search_vector",
            TSVECTOR(),
            sa.Computed(
                "to_tsvector('russian'::regconfig, search_text) || to_tsvector('english'::regconfig, search_text) || to_tsvector('simple'::regconfig, search_text)",
                persisted=True,
            ),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "version_id"], ["document_versions.project_id", "document_versions.id"]
        ),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_chunk_ordinal"),
    )
    op.create_index(
        "ix_document_chunk_search", "document_chunks", ["search_vector"], postgresql_using="gin"
    )
    for col in ("project_id", "version_id"):
        op.create_index("ix_document_chunks_" + col, "document_chunks", [col])
    op.execute("""
        CREATE FUNCTION protect_document_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'document_chunks' THEN
                RAISE EXCEPTION 'Document chunks are immutable' USING ERRCODE = '23514';
            ELSIF (OLD.project_id, OLD.document_id, OLD.revision, OLD.name, OLD.sha256,
                   OLD.size, OLD.request_id, OLD.request_hash, OLD.pipeline)
               IS DISTINCT FROM (NEW.project_id, NEW.document_id, NEW.revision, NEW.name,
                   NEW.sha256, NEW.size, NEW.request_id, NEW.request_hash, NEW.pipeline) THEN
                RAISE EXCEPTION 'Document version is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER document_chunk_immutable BEFORE UPDATE ON document_chunks
            FOR EACH ROW EXECUTE FUNCTION protect_document_evidence();
        CREATE TRIGGER document_version_immutable BEFORE UPDATE ON document_versions
            FOR EACH ROW EXECUTE FUNCTION protect_document_evidence();
        CREATE FUNCTION protect_document_run_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.kind = 'document_evidence' AND
               (OLD.data::jsonb IS DISTINCT FROM NEW.data::jsonb OR
                OLD.run_id IS DISTINCT FROM NEW.run_id OR OLD.kind IS DISTINCT FROM NEW.kind OR
                OLD.object_id IS DISTINCT FROM NEW.object_id) THEN
                RAISE EXCEPTION 'Frozen document evidence is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER document_run_evidence_immutable BEFORE UPDATE ON research_objects
            FOR EACH ROW EXECUTE FUNCTION protect_document_run_evidence();
    """)


def downgrade():
    op.drop_column("project_memory", "search_text")
    op.drop_column("project_memory", "document_refs")
    op.execute("DROP TRIGGER document_run_evidence_immutable ON research_objects")
    op.execute("DROP FUNCTION protect_document_run_evidence()")
    op.drop_table("document_chunks")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.execute("DROP FUNCTION protect_document_evidence()")
