"""Extend immutability to analytical revisions and evidence manifests."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def install(kinds):
    op.execute(f"""
        CREATE OR REPLACE FUNCTION protect_staffing_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.kind IN ({kinds}) AND
               (OLD.data::jsonb IS DISTINCT FROM NEW.data::jsonb OR
                OLD.run_id IS DISTINCT FROM NEW.run_id OR
                OLD.kind IS DISTINCT FROM NEW.kind OR OLD.object_id IS DISTINCT FROM NEW.object_id) THEN
                RAISE EXCEPTION 'Frozen research revision is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
    """)


def upgrade():
    install(
        "'role_profile','assignment_revision','artifact','result_revision','assessment','page_revision','delivery_contract'"
    )


def downgrade():
    install("'role_profile','assignment_revision'")
