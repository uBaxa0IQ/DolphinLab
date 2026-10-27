"""Protect frozen role profiles and original assignment revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("role_catalog", sa.JSON(), nullable=False, server_default="[]"))
    op.alter_column("runs", "role_catalog", server_default=None)
    op.execute("""
        CREATE FUNCTION protect_staffing_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.kind IN ('role_profile', 'assignment_revision') AND
               (OLD.data::jsonb IS DISTINCT FROM NEW.data::jsonb OR
                OLD.run_id IS DISTINCT FROM NEW.run_id OR
                OLD.kind IS DISTINCT FROM NEW.kind OR
                OLD.object_id IS DISTINCT FROM NEW.object_id) THEN
                RAISE EXCEPTION 'Frozen staffing revision is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER staffing_revision_immutable BEFORE UPDATE ON research_objects
        FOR EACH ROW EXECUTE FUNCTION protect_staffing_revision();
    """)


def downgrade():
    op.execute("DROP TRIGGER staffing_revision_immutable ON research_objects")
    op.execute("DROP FUNCTION protect_staffing_revision()")
    op.drop_column("runs", "role_catalog")
