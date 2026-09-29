"""Proveniência das relações KAG: extractor, confidence, source_span, validação humana.

Amplia ``knowledge_relations`` para que cada aresta traga junto **como** foi
inferida (``extracted_by``), **quão certa** é (``confidence``), **onde** no
documento (``source_span``) e **quem/quando** validou (``validated_by``,
``validated_at``). Sem esses campos, promover uma extração automática para
"validada por humano" viraria adivinhação.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260918_0004"
down_revision = "20260917_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_relations") as batch:
        batch.add_column(
            sa.Column(
                "extracted_by",
                sa.String(40),
                nullable=False,
                server_default="dictionary",
            )
        )
        batch.add_column(
            sa.Column(
                "confidence",
                sa.Float,
                nullable=False,
                server_default="1.0",
            )
        )
        batch.add_column(sa.Column("source_span", sa.String(500), nullable=True))
        batch.add_column(sa.Column("validated_by", sa.String(120), nullable=True))
        batch.add_column(
            sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_relations") as batch:
        batch.drop_column("validated_at")
        batch.drop_column("validated_by")
        batch.drop_column("source_span")
        batch.drop_column("confidence")
        batch.drop_column("extracted_by")
