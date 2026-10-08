"""Aliases, feedback, chunks pgvector, name_normalized e RLS.

Revision ID: 20261007_0005
Revises: 20260918_0004
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20261007_0005"
down_revision = "20260918_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_entities") as batch:
        batch.add_column(
            sa.Column("name_normalized", sa.String(255), nullable=False, server_default="")
        )
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE knowledge_entities SET name_normalized = lower(name)"))
    try:
        with op.batch_alter_table("knowledge_entities") as batch:
            batch.drop_constraint("uq_knowledge_entity", type_="unique")
    except Exception:
        pass
    with op.batch_alter_table("knowledge_entities") as batch:
        batch.create_unique_constraint(
            "uq_knowledge_entity_normalized",
            ["tenant_id", "kind", "name_normalized"],
        )
        batch.create_index(
            "ix_knowledge_entity_normalized",
            ["tenant_id", "name_normalized"],
        )

    op.create_table(
        "knowledge_entity_aliases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "entity_id",
            sa.String(36),
            sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("alias_normalized", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "alias_normalized", name="uq_knowledge_entity_alias"),
    )
    op.create_index("ix_knowledge_entity_aliases_tenant", "knowledge_entity_aliases", ["tenant_id"])
    op.create_index("ix_knowledge_entity_aliases_entity", "knowledge_entity_aliases", ["entity_id"])

    op.create_table(
        "chat_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("useful", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_feedback_tenant_created", "chat_feedback", ["tenant_id", "created_at"])
    op.create_index("ix_chat_feedback_request_id", "chat_feedback", ["request_id"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(200), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("embedding_model", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chunks_tenant_doc", "knowledge_chunks", ["tenant_id", "document_id"])
    op.create_index("ix_chunks_tenant_model", "knowledge_chunks", ["tenant_id", "embedding_model"])

    if conn.dialect.name == "postgresql":
        for table in (
            "audit_events",
            "knowledge_entities",
            "knowledge_relations",
            "knowledge_entity_aliases",
            "chat_feedback",
            "knowledge_chunks",
        ):
            op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            op.execute(
                sa.text(
                    f"""
                    CREATE POLICY tenant_isolation ON {table}
                    USING (
                        current_setting('app.rls_bypass', true) = '1'
                        OR tenant_id = current_setting('app.tenant_id', true)
                    )
                    """
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        for table in (
            "audit_events",
            "knowledge_entities",
            "knowledge_relations",
            "knowledge_entity_aliases",
            "chat_feedback",
            "knowledge_chunks",
        ):
            op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.drop_table("knowledge_chunks")
    op.drop_table("chat_feedback")
    op.drop_table("knowledge_entity_aliases")
    with op.batch_alter_table("knowledge_entities") as batch:
        batch.drop_constraint("uq_knowledge_entity_normalized", type_="unique")
        batch.drop_index("ix_knowledge_entity_normalized")
        batch.drop_column("name_normalized")
