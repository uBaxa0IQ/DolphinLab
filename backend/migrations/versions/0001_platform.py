"""Initial frozen PostgreSQL schema. LangGraph owns its separate checkpoint migrations."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = [
    "\n"
    "CREATE TABLE projects (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tname VARCHAR(120) NOT NULL, \n"
    "\tdescription TEXT NOT NULL, \n"
    "\tstatus VARCHAR(24) NOT NULL, \n"
    "\tcreated TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (id)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE tool_receipts (\n"
    "\tsession_id VARCHAR(120) NOT NULL, \n"
    "\tcall_id VARCHAR(120) NOT NULL, \n"
    "\tstate VARCHAR(24) NOT NULL, \n"
    "\tresult JSON, \n"
    "\tcreated TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (session_id, call_id)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE workspace_settings (\n"
    "\tid VARCHAR(20) NOT NULL, \n"
    "\tdisplay_name VARCHAR(80) NOT NULL, \n"
    "\tbase_url VARCHAR(1000) NOT NULL, \n"
    "\tmodel VARCHAR(200) NOT NULL, \n"
    "\tencrypted_api_key TEXT, \n"
    "\tpricing JSON NOT NULL, \n"
    "\tPRIMARY KEY (id)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE input_files (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tproject_id VARCHAR(36) NOT NULL, \n"
    "\tname VARCHAR(240) NOT NULL, \n"
    "\tstored_name VARCHAR(60) NOT NULL, \n"
    "\tsize INTEGER NOT NULL, \n"
    "\tsha256 VARCHAR(64) NOT NULL, \n"
    "\tcreated TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tFOREIGN KEY(project_id) REFERENCES projects (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_input_files_project_id ON input_files (project_id)",
    "\n"
    "CREATE TABLE messages (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tproject_id VARCHAR(36) NOT NULL, \n"
    "\trole VARCHAR(20) NOT NULL, \n"
    "\tcontent TEXT NOT NULL, \n"
    "\tdirective BOOLEAN NOT NULL, \n"
    "\tstatus VARCHAR(24) NOT NULL, \n"
    "\tcreated TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\terror TEXT, \n"
    "\tPRIMARY KEY (id), \n"
    "\tFOREIGN KEY(project_id) REFERENCES projects (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_messages_project_id ON messages (project_id)",
    "\n"
    "CREATE TABLE project_events (\n"
    "\tseq SERIAL NOT NULL, \n"
    "\tproject_id VARCHAR(36) NOT NULL, \n"
    "\tkind VARCHAR(40) NOT NULL, \n"
    "\tpayload JSON NOT NULL, \n"
    "\tcreated TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (seq), \n"
    "\tFOREIGN KEY(project_id) REFERENCES projects (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_project_events_project_id ON project_events (project_id)",
    "\n"
    "CREATE TABLE project_policies (\n"
    "\tproject_id VARCHAR(36) NOT NULL, \n"
    "\trevision INTEGER NOT NULL, \n"
    "\tpolicy JSON NOT NULL, \n"
    "\tPRIMARY KEY (project_id), \n"
    "\tFOREIGN KEY(project_id) REFERENCES projects (id)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE runs (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tproject_id VARCHAR(36) NOT NULL, \n"
    "\tstatus VARCHAR(24) NOT NULL, \n"
    "\trequested_action VARCHAR(20) NOT NULL, \n"
    "\tpolicy JSON NOT NULL, \n"
    "\tinputs JSON NOT NULL, \n"
    "\tconnection JSON NOT NULL, \n"
    "\ttask_description TEXT NOT NULL, \n"
    "\telapsed_seconds FLOAT NOT NULL, \n"
    "\tusage JSON NOT NULL, \n"
    "\tsnapshot JSON NOT NULL, \n"
    "\tpublication JSON, \n"
    "\terror TEXT, \n"
    "\tcreated TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tupdated TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tpaused_at TIMESTAMP WITH TIME ZONE, \n"
    "\tPRIMARY KEY (id), \n"
    "\tFOREIGN KEY(project_id) REFERENCES projects (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_runs_project_id ON runs (project_id)",
    "CREATE INDEX ix_runs_status ON runs (status)",
    "\n"
    "CREATE TABLE directives (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tproject_id VARCHAR(36) NOT NULL, \n"
    "\tmessage_id VARCHAR(36) NOT NULL, \n"
    "\tstate VARCHAR(24) NOT NULL, \n"
    "\tcreated TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tcontent TEXT NOT NULL, \n"
    "\tdecision TEXT NOT NULL, \n"
    "\trun_id VARCHAR(36), \n"
    "\tPRIMARY KEY (id), \n"
    "\tFOREIGN KEY(project_id) REFERENCES projects (id), \n"
    "\tUNIQUE (message_id), \n"
    "\tFOREIGN KEY(message_id) REFERENCES messages (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_directives_project_id ON directives (project_id)",
    "\n"
    "CREATE TABLE research_events (\n"
    "\tseq SERIAL NOT NULL, \n"
    "\trun_id VARCHAR(36) NOT NULL, \n"
    "\tkind VARCHAR(80) NOT NULL, \n"
    "\tdata JSON NOT NULL, \n"
    "\tcreated FLOAT NOT NULL, \n"
    "\tPRIMARY KEY (seq), \n"
    "\tFOREIGN KEY(run_id) REFERENCES runs (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_research_events_run_id ON research_events (run_id)",
    "\n"
    "CREATE TABLE research_mail (\n"
    "\tseq SERIAL NOT NULL, \n"
    "\trun_id VARCHAR(36) NOT NULL, \n"
    "\trecipient VARCHAR(120) NOT NULL, \n"
    "\tsender VARCHAR(120) NOT NULL, \n"
    "\tbody TEXT NOT NULL, \n"
    "\tdelivered BOOLEAN NOT NULL, \n"
    "\tcreated FLOAT NOT NULL, \n"
    "\tPRIMARY KEY (seq), \n"
    "\tFOREIGN KEY(run_id) REFERENCES runs (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_research_mail_run_id ON research_mail (run_id)",
    "\n"
    "CREATE TABLE research_objects (\n"
    "\tseq SERIAL NOT NULL, \n"
    "\trun_id VARCHAR(36) NOT NULL, \n"
    "\tkind VARCHAR(40) NOT NULL, \n"
    "\tobject_id VARCHAR(120) NOT NULL, \n"
    "\tdata JSON NOT NULL, \n"
    "\tPRIMARY KEY (seq), \n"
    "\tUNIQUE (run_id, kind, object_id), \n"
    "\tFOREIGN KEY(run_id) REFERENCES runs (id)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_research_objects_run_id ON research_objects (run_id)",
    "\n"
    "CREATE TABLE research_receipts (\n"
    "\trun_id VARCHAR(36) NOT NULL, \n"
    "\tagent VARCHAR(120) NOT NULL, \n"
    "\tcall_id VARCHAR(160) NOT NULL, \n"
    "\tresult JSON NOT NULL, \n"
    "\tPRIMARY KEY (run_id, agent, call_id), \n"
    "\tFOREIGN KEY(run_id) REFERENCES runs (id)\n"
    ")\n"
    "\n",
]
TABLES = [
    "projects",
    "tool_receipts",
    "workspace_settings",
    "input_files",
    "messages",
    "project_events",
    "project_policies",
    "runs",
    "directives",
    "research_events",
    "research_mail",
    "research_objects",
    "research_receipts",
]


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    for name in reversed(TABLES):
        op.drop_table(name)
