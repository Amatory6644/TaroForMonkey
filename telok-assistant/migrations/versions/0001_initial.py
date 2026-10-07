"""Frozen initial domain schema. DBOS manages its own schema."""

from alembic import op

revision = "0001"
down_revision = None

STATEMENTS = [
    "\nCREATE TABLE telok.audit (\n\tproject_id VARCHAR(64) NOT NULL, \n\tevent VARCHAR(100) NOT NULL, \n\tpayload JSON NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n",
    "CREATE INDEX ix_telok_audit_project_id ON telok.audit (project_id)",
    "\nCREATE TABLE telok.budget_buckets (\n\tspent NUMERIC(18, 6) NOT NULL, \n\treserved NUMERIC(18, 6) NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n",
    "\nCREATE TABLE telok.contexts (\n\tactor_id BIGINT NOT NULL, \n\tproject_id VARCHAR(64) NOT NULL, \n\tdata JSON NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (actor_id)\n)\n\n",
    "\nCREATE TABLE telok.projects (\n\towner_id BIGINT NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tdescription TEXT NOT NULL, \n\tbrand JSON NOT NULL, \n\tbrand_revision INTEGER NOT NULL, \n\tchannel_id VARCHAR(100) NOT NULL, \n\ttimezone VARCHAR(100) NOT NULL, \n\tpaused BOOLEAN NOT NULL, \n\tpolicy JSON NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n",
    "CREATE INDEX ix_telok_projects_owner_id ON telok.projects (owner_id)",
    "\nCREATE TABLE telok.telegram_receipts (\n\tupdate_id BIGINT NOT NULL, \n\tpayload JSON NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (update_id)\n)\n\n",
    "\nCREATE TABLE telok.assets (\n\tproject_id VARCHAR(64) NOT NULL, \n\tkey VARCHAR(300) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tmime VARCHAR(80) NOT NULL, \n\tsize INTEGER NOT NULL, \n\tinfo JSON NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (key), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id)\n)\n\n",
    "CREATE INDEX ix_telok_assets_project_id ON telok.assets (project_id)",
    "\nCREATE TABLE telok.content_items (\n\tproject_id VARCHAR(64) NOT NULL, \n\tcurrent_version_id VARCHAR(64) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id)\n)\n\n",
    "CREATE INDEX ix_telok_content_items_project_id ON telok.content_items (project_id)",
    "\nCREATE TABLE telok.evidence (\n\tproject_id VARCHAR(64) NOT NULL, \n\tclaim TEXT NOT NULL, \n\texcerpt TEXT NOT NULL, \n\turl TEXT NOT NULL, \n\tkind VARCHAR(40) NOT NULL, \n\tassessment VARCHAR(30) NOT NULL, \n\tvalid_until TIMESTAMP WITH TIME ZONE, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id)\n)\n\n",
    "CREATE INDEX ix_telok_evidence_project_id ON telok.evidence (project_id)",
    "\nCREATE TABLE telok.feedback (\n\tproject_id VARCHAR(64) NOT NULL, \n\tversion_id VARCHAR(64) NOT NULL, \n\tdecision VARCHAR(30) NOT NULL, \n\tnotes TEXT NOT NULL, \n\teditor_minutes DOUBLE PRECISION NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id)\n)\n\n",
    "CREATE INDEX ix_telok_feedback_project_id ON telok.feedback (project_id)",
    "\nCREATE TABLE telok.plans (\n\tproject_id VARCHAR(64) NOT NULL, \n\tslots JSON NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id)\n)\n\n",
    "CREATE INDEX ix_telok_plans_project_id ON telok.plans (project_id)",
    "\nCREATE TABLE telok.provider_attempts (\n\tproject_id VARCHAR(64) NOT NULL, \n\trequest_id VARCHAR(64) NOT NULL, \n\tprovider VARCHAR(80) NOT NULL, \n\treserved NUMERIC(18, 6) NOT NULL, \n\tcharged NUMERIC(18, 6) NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\tbucket_ids JSON NOT NULL, \n\tusage JSON NOT NULL, \n\tprovider_request_id VARCHAR(200) NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id)\n)\n\n",
    "CREATE INDEX ix_telok_provider_attempts_project_id ON telok.provider_attempts (project_id)",
    "CREATE INDEX ix_telok_provider_attempts_request_id ON telok.provider_attempts (request_id)",
    "\nCREATE TABLE telok.requests (\n\tproject_id VARCHAR(64) NOT NULL, \n\tkind VARCHAR(40) NOT NULL, \n\tpayload JSON NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\terror TEXT NOT NULL, \n\tworkflow_id VARCHAR(200) NOT NULL, \n\tresult JSON NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (workflow_id), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id)\n)\n\n",
    "CREATE INDEX ix_telok_requests_project_id ON telok.requests (project_id)",
    "\nCREATE TABLE telok.content_versions (\n\tproject_id VARCHAR(64) NOT NULL, \n\titem_id VARCHAR(64) NOT NULL, \n\trequest_id VARCHAR(64) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tbody TEXT NOT NULL, \n\tformat VARCHAR(30) NOT NULL, \n\tasset_ids JSON NOT NULL, \n\tbrief JSON NOT NULL, \n\tqa JSON NOT NULL, \n\tmanifest JSON NOT NULL, \n\tmanifest_hash VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\tdemo BOOLEAN NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (request_id), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id), \n\tFOREIGN KEY(item_id) REFERENCES telok.content_items (id), \n\tUNIQUE (item_id, revision)\n)\n\n",
    "CREATE INDEX ix_telok_content_versions_item_id ON telok.content_versions (item_id)",
    "CREATE INDEX ix_telok_content_versions_project_id ON telok.content_versions (project_id)",
    "\nCREATE TABLE telok.approvals (\n\tversion_id VARCHAR(64) NOT NULL, \n\tactor_id BIGINT NOT NULL, \n\tmanifest_hash VARCHAR(64) NOT NULL, \n\tchannel_id VARCHAR(100) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\taccepted_findings JSON NOT NULL, \n\tpolicy_revision INTEGER NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (version_id), \n\tFOREIGN KEY(version_id) REFERENCES telok.content_versions (id)\n)\n\n",
    "\nCREATE TABLE telok.publications (\n\tversion_id VARCHAR(64) NOT NULL, \n\tproject_id VARCHAR(64) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\tnot_before TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlatest_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tattempt_id VARCHAR(64) NOT NULL, \n\treceipt JSON NOT NULL, \n\terror TEXT NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (version_id), \n\tFOREIGN KEY(project_id) REFERENCES telok.projects (id), \n\tFOREIGN KEY(version_id) REFERENCES telok.content_versions (id)\n)\n\n",
    "CREATE INDEX ix_telok_publications_project_id ON telok.publications (project_id)",
    "\nCREATE TABLE telok.publication_attempts (\n\tpublication_id VARCHAR(64) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tstatus VARCHAR(40) NOT NULL, \n\treceipt JSON NOT NULL, \n\terror TEXT NOT NULL, \n\tid VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(publication_id) REFERENCES telok.publications (id)\n)\n\n",
    "CREATE INDEX ix_telok_publication_attempts_publication_id ON telok.publication_attempts (publication_id)",
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    for name in [
        "publication_attempts",
        "publications",
        "approvals",
        "content_versions",
        "requests",
        "provider_attempts",
        "plans",
        "feedback",
        "evidence",
        "content_items",
        "assets",
        "telegram_receipts",
        "projects",
        "contexts",
        "budget_buckets",
        "audit",
    ]:
        op.drop_table(name, schema="telok")
