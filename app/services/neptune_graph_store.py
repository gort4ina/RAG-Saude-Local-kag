"""Provedor AWS Neptune (openCypher) — stub opcional.

Este módulo NÃO importa SDKs da AWS e NÃO lê chaves do código. Ele existe
para:

1. Reservar o contrato ``GraphStore`` para produção futura.
2. Falhar de forma clara se alguém ativar ``KAG_GRAPH_BACKEND=neptune``
   sem configurar o endpoint — o ambiente local continua funcionando com
   ``postgres``.

Quando a integração real for escrita, este arquivo deve:

- Abrir HTTPS contra ``NEPTUNE_ENDPOINT`` (cluster Neptune Serverless ou
  provisionado) com openCypher HTTP (``/openCypher``) ou Gremlin.
- Usar IAM / SigV4 via variáveis de ambiente ou role da instância —
  nunca hardcode de ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``.
- Mapear os mesmos kinds/relações de ``graph_store.ENTITY_KINDS``.
"""

from __future__ import annotations

from app.errors import RagError
from app.services.metadata import RegulatoryMetadata


class NeptuneNotConfiguredError(RagError):
    """Neptune foi escolhido como backend, mas o endpoint não está pronto."""

    code = "neptune_not_configured"
    http_status = 503
    user_message = (
        "O backend de grafo Neptune está selecionado, mas ainda não está "
        "configurado neste ambiente. Use KAG_GRAPH_BACKEND=postgres para "
        "desenvolvimento local, ou defina NEPTUNE_ENDPOINT."
    )


class NeptuneNotImplementedError(RagError):
    """Stub: a integração openCypher ainda não foi implementada."""

    code = "neptune_not_implemented"
    http_status = 501
    user_message = (
        "A integração com AWS Neptune ainda não está disponível. "
        "Mantenha KAG_GRAPH_BACKEND=postgres para uso local."
    )


class NeptuneGraphStore:
    """Stub do GraphStore para Neptune. Não executa consultas reais."""

    def __init__(
        self,
        *,
        endpoint: str = "",
        port: int = 8182,
        use_iam: bool = True,
        region: str = "",
    ) -> None:
        self.endpoint = (endpoint or "").strip()
        self.port = port
        self.use_iam = use_iam
        self.region = (region or "").strip()
        if not self.endpoint:
            raise NeptuneNotConfiguredError()

    def _not_ready(self) -> None:
        # Endpoint presente, mas a implementação openCypher ainda não existe.
        raise NeptuneNotImplementedError()

    async def sync_document(
        self, tenant_id: str, metadata: RegulatoryMetadata, text: str
    ) -> int:
        self._not_ready()
        return 0

    async def forget_document(self, tenant_id: str, document_id: str) -> int:
        self._not_ready()
        return 0

    async def search_context(
        self, tenant_id: str, question: str, limit: int = 8
    ) -> list:
        self._not_ready()
        return []

    async def list_relations(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list:
        self._not_ready()
        return []

    async def set_validation_status(
        self,
        tenant_id: str,
        relation_id: str,
        *,
        status: str,
        validated_by: str,
    ):
        self._not_ready()
        return None
