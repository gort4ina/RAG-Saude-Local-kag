import { HttpErrorResponse } from '@angular/common/http';

export const ERROR_MESSAGES: Record<string, string> = {
  ollama_unavailable:
    'O servico de modelos (Ollama) esta indisponivel. Verifique se o container esta rodando.',
  ollama_connection_error:
    'Nao foi possivel conectar ao servico de modelos. Confirme que o container "ollama" esta no ar.',
  ollama_timeout:
    'O modelo demorou demais para responder. Em CPU isso e comum. Considere ativar a GPU ou usar um modelo menor.',
  ollama_http_error:
    'O servico de modelos respondeu com erro HTTP. Consulte os logs do container "ollama".',
  ollama_overloaded:
    'O servico de modelos esta sobrecarregado ou sem memoria. Aguarde a requisicao anterior terminar.',
  ollama_empty_response:
    'O modelo retornou uma resposta vazia. Reformule a pergunta ou reduza o contexto.',
  stream_interrupted: 'A conexao com o modelo caiu durante a geracao. Tente novamente.',
  model_not_installed:
    'O modelo requisitado nao esta instalado no Ollama. Baixe-o com "ollama pull".',
  vector_store_unavailable:
    'O banco vetorial esta indisponivel. Confira o volume persistente e o log da API.',
  audit_unavailable:
    'A operação foi bloqueada porque a trilha de auditoria está indisponível.',
  embedding_model_mismatch:
    'A colecao foi indexada com outro modelo de embeddings. Use uma colecao nova ou volte o modelo anterior.',
  knowledge_base_empty:
    'Ainda nao ha documentos indexados. Adicione um documento antes de perguntar.',
  no_relevant_context:
    'A base indexada nao possui informacao suficiente para esta pergunta. Adicione uma fonte ou reformule.',
  unsupported_document: 'Formato de arquivo nao suportado. Use PDF, TXT ou Markdown.',
  document_too_large: 'O arquivo enviado ultrapassa o limite configurado.',
  payload_too_large: 'O conteudo enviado excede o tamanho maximo aceito pela API.',
  empty_document: 'O arquivo esta vazio ou nao possui texto extraivel.',
  scanned_pdf_requires_ocr:
    'Este PDF parece digitalizado (sem camada de texto). Aplique OCR antes de enviar.',
  ingestion_in_progress:
    'Este documento ja esta sendo indexado. Aguarde a conclusao antes de reenviar.',
  indexing_failed: 'Nao foi possivel indexar o documento. A base anterior foi preservada.',
  invalid_question: 'A pergunta precisa ter entre 3 e 2000 caracteres.',
  invalid_request: 'A requisicao possui campos invalidos.',
  network_error:
    'Nao foi possivel contatar o backend. Verifique se o container da API esta rodando.',
  stream_connection_error: 'A conexao foi interrompida durante a resposta. Tente novamente.',
  rate_limited: 'Muitas requisicoes em pouco tempo. Aguarde um instante e tente de novo.',
  forbidden: 'Sua conta nao tem permissao para esta operacao.',
  internal_error: 'Ocorreu um erro interno inesperado. Verifique os logs do backend.'
};

export function messageForCode(code: string, fallback: string): string {
  return ERROR_MESSAGES[code] ?? fallback;
}

export function humanizeError(response: HttpErrorResponse): {
  code: string;
  message: string;
} {
  const apiError = response.error as { code?: string; detail?: string } | undefined;
  const code = apiError?.code ?? '';
  if (code && ERROR_MESSAGES[code]) {
    return { code, message: ERROR_MESSAGES[code] };
  }
  if (response.status === 0) {
    return { code: 'network_error', message: ERROR_MESSAGES['network_error'] };
  }
  if (response.status === 429) {
    return { code: 'rate_limited', message: ERROR_MESSAGES['rate_limited'] };
  }
  if (response.status === 403) {
    return { code: 'forbidden', message: ERROR_MESSAGES['forbidden'] };
  }
  if (response.status === 504 || response.status === 408) {
    return { code: 'ollama_timeout', message: ERROR_MESSAGES['ollama_timeout'] };
  }
  if (apiError?.detail) {
    return { code: code || `http_${response.status}`, message: apiError.detail };
  }
  return {
    code: `http_${response.status}`,
    message: `Erro inesperado (${response.status}) ao consultar a base.`
  };
}
