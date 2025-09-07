# Módulo: `cloud_function_trigger_scraper`

## 1. Visão Geral

Esta Cloud Function é o ponto de partida da pipeline de processamento de dados da web na nova arquitetura orientada a eventos. Sua principal responsabilidade é detectar a criação de novos documentos na coleção `monitor_results` do Firestore e acionar o micro-serviço `scraper_newspaper3k` para que ele realize o scraping do conteúdo da URL recém-descoberta.

Este componente substitui a necessidade de um Cloud Scheduler que varria a coleção em busca de novos links, tornando o início do processo de coleta quase instantâneo. Adicionalmente, a função registra cada execução na coleção `system_logs` para garantir a observabilidade da pipeline.

---


## 2. Detalhes Técnicos / Pilha Tecnológica

-   **Ambiente de Execução:** Google Cloud Functions (1ª geração)
-   **Runtime:** Python 3.12+
-   **Framework:** [Google Cloud Functions Framework](https://github.com/GoogleCloudPlatform/functions-framework-python)
-   **Dependências Principais:**
    -   `functions-framework`: Para o boilerplate e execução da função.
    -   `requests`: Para realizar chamadas HTTP para o serviço de scraping.
    -   `google-auth` e `google-oauth2`: Para gerar um token de identidade (ID Token) e autenticar a chamada para o serviço `scraper_newspaper3k`, que é um serviço privado no Cloud Run.
    -   `firebase-admin`: Para se conectar ao Firestore e escrever na coleção `system_logs`.

---


## 3. Gatilho (Trigger)

-   **Tipo:** Firestore Trigger (Nativo de 1ª Geração)
-   **Evento:** `providers/cloud.firestore/eventTypes/document.create`
-   **Recurso:** `projects/{project_id}/databases/(default)/documents/monitor_results/{doc_id}`

Isso significa que a função é executada automaticamente sempre que um novo documento é adicionado à coleção `monitor_results`.

---


## 4. Variáveis de Ambiente

Para operar corretamente, a função requer a seguinte variável de ambiente:

| Variável              | Descrição                                                                                                | Exemplo                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `SCRAPER_SERVICE_URL` | A URL completa do serviço `scraper_newspaper3k` implantado no Google Cloud Run. A função usará essa URL como *audience* para gerar o token de autenticação e como alvo para a chamada HTTP. | `https://scraper-newspaper3k-abcdef-uc.a.run.app` |

---


## 5. Lógica de Execução

1.  A função é ativada por um evento de criação de documento no Firestore.
2.  Ela extrai o `doc_id` do documento recém-criado a partir dos metadados do evento.
3.  Um novo documento é criado na coleção `system_logs` com o status `processing` para registrar o início da execução.
4.  Utilizando a biblioteca `google-oauth2`, a função obtém as credenciais do ambiente de execução e gera um **ID Token** JWT. A audiência (`aud`) deste token é definida como a `SCRAPER_SERVICE_URL`, autorizando a função a invocar especificamente aquele serviço.
5.  A função monta a URL do endpoint alvo: `{SCRAPER_SERVICE_URL}/scrape/by-doc-id/{doc_id}`.
6.  Uma requisição `POST` é enviada para a URL alvo, com o ID Token no cabeçalho `Authorization: Bearer <token>`.
7.  Em caso de sucesso, o documento de log em `system_logs` é atualizado para `success`.
8.  Em caso de falha (erro de rede, status HTTP 4xx/5xx, ou qualquer outra exceção), o log é atualizado para `failed`, e os detalhes do erro são registrados. A exceção é então propagada para que o Google Cloud possa gerenciar a política de retentativas da função.

---


## 6. Modelo de Dados (`system_logs`)

A função cria e atualiza um documento na coleção `system_logs` com a seguinte estrutura:

```json
{
  "run_id": "string (uuid4)",
  "module": "trigger-scraper",
  "target_doc_id": "string",
  "start_time": "timestamp",
  "end_time": "timestamp",
  "status": "string ('processing', 'success', 'failed')",
  "details": "string",
  "error_details": "string (opcional)"
}
```

---


## 7. Permissões de IAM (Identity and Access Management)

A conta de serviço associada a esta Cloud Function precisa ter as seguintes permissões:

-   **Role 1:** `Cloud Run Invoker` (`roles/run.invoker`)
    -   **No Recurso:** No serviço `scraper_newspaper3k` do Cloud Run.
-   **Role 2:** `Cloud Datastore User` (`roles/datastore.user`)
    -   **No Recurso:** No projeto GCP (para permitir escrita na coleção `system_logs` do Firestore).

---


## 8. Relação com Outros Módulos

-   **Origem do Evento:** A função é acionada por documentos criados pelo `backend` na coleção `monitor_results`.
-   **Destino da Ação:** A função invoca o endpoint `POST /scrape/by-doc-id/{doc_id}` no serviço `scraper_newspaper3k`.
-   **Próximo Passo na Pipeline:** Após o `scraper_newspaper3k` concluir seu trabalho e atualizar o status do documento para `scraper_ok`, a Cloud Function `trigger-nlp-web` será acionada.

---

## 9. Notas de Implementação e Histórico

Durante a implementação inicial, foram encontrados os seguintes problemas e aplicadas as seguintes soluções:

1.  **Problema:** O gatilho do Firestore para a Cloud Function de 2ª Geração (baseado em Eventarc) não era acionado, apesar de todas as configurações e permissões estarem corretas. A falha era silenciosa, sem erros nos logs.
2.  **Solução:** A função de 2ª Geração foi substituída por uma de 1ª Geração (`trigger-scraper-v1`), que utiliza um mecanismo de gatilho nativo e mais direto com o Firestore. Esta abordagem contornou a falha do Eventarc e o gatilho passou a funcionar.

3.  **Problema:** Após a ativação do gatilho, a função falhava ao tentar gerar o token de autenticação para invocar o serviço de scraper, com o erro `'Request' object has no attribute 'key_id'`.
4.  **Solução:** O código de geração de token foi refatorado para usar o método `google.oauth2.id_token.fetch_id_token`, que é a abordagem recomendada e mais robusta para este ambiente, resolvendo o problema de autenticação.

A versão estável e em produção utiliza a função de 1ª Geração `trigger-scraper-v1`.

---

## 10. Exemplo de Comando de Deploy (1ª Geração)

Execute o comando a partir da raiz do projeto. Lembre-se de substituir `"URL_DO_SEU_SERVICO_SCRAPER"` pela URL real do seu serviço no Cloud Run.

```bash
gcloud functions deploy trigger-scraper-v1 \
  --no-gen2 \
  --runtime=python312 \
  --trigger-event="providers/cloud.firestore/eventTypes/document.create" \
  --trigger-resource="projects/monitora-parlamentar-elmar/databases/(default)/documents/monitor_results/{docId}" \
  --source=cloud_function_trigger_scraper \
  --entry-point=trigger_scraper \
  --region=us-central1 \
  --set-env-vars SCRAPER_SERVICE_URL="URL_DO_SEU_SERVICO_SCRAPER"
```





