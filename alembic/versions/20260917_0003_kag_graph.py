"""KAG knowledge graph with traceable, tenant-scoped relations."""
from alembic import op
import sqlalchemy as sa

revision = "20260917_0003"
down_revision = "20260829_0002"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("knowledge_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "kind", "name", name="uq_knowledge_entity"),
    )
    op.create_index("ix_knowledge_entities_tenant_id", "knowledge_entities", ["tenant_id"])
    op.create_table("knowledge_relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("source_entity_id", sa.String(36), sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(80), nullable=False),
        sa.Column("target_entity_id", sa.String(36), sa.ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_document_id", sa.String(64), nullable=False),
        sa.Column("source_reference", sa.String(300)),
        sa.Column("validation_status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "source_entity_id", "relation", "target_entity_id", "source_document_id", name="uq_knowledge_relation"),
    )
    op.create_index("ix_knowledge_relations_tenant_id", "knowledge_relations", ["tenant_id"])
    op.create_index("ix_knowledge_relation_tenant_source", "knowledge_relations", ["tenant_id", "source_entity_id"])
    op.create_index("ix_knowledge_relation_tenant_target", "knowledge_relations", ["tenant_id", "target_entity_id"])
    op.create_index("ix_knowledge_relations_source_document_id", "knowledge_relations", ["source_document_id"])

def downgrade() -> None:
    op.drop_table("knowledge_relations")
    op.drop_table("knowledge_entities")
