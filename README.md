# Transparência Santos → BigQuery → Data Studio

Pipeline automatizado em Python para extrair mensalmente **Receitas**, **Despesas** (`empenhos`, `liquidacoes` e `pagamentos`) e **Orçamento** (`orcamento_receitas` e `orcamento_despesas`) da API do Portal da Transparência de Santos-SP, carregar de forma idempotente no **Google BigQuery** e alimentar relatórios no **Google Data Studio**.

## Estrutura do projeto

- `src/config.py`: Definição declarativa dos 4 endpoints (`json_execucao_receitas`, `json_empenhos`, `json_liquidacoes`, `json_pagamentos`) e variáveis de ambiente.
- `src/extractor.py`: Cliente HTTP paginado (`pagina` + `tamanho=1000`) com retentativas.
- `src/transformer.py`: Limpeza de espaços extras, normalização de `ano_ref`/`mes_ref` numéricos, datas (`DATE`) e valores financeiros (`NUMERIC`).
- `src/bigquery_loader.py`: Criação automática do dataset/tabelas particionadas por mês e substituição atômica dos dados do mês via tabela de staging.
- `src/main.py`: Ponto de entrada (por padrão processa o **mês anterior** no fuso `America/Sao_Paulo`).
- `sql/views_looker_studio.sql`: Views prontas (`vw_receitas_mensal`, `vw_despesas_fases_mensal`, `vw_balanco_mensal`) para conectar no Looker Studio.
- `Dockerfile`: Imagem leve para execução como **Cloud Run Job**.

## 1. Teste local, download e carga histórica (2024, 2025, 2026)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Baixa e salva todos os 6 datasets de 2024, 2025 e 2026 localmente em data/ (sem BigQuery)
python -m src.main --years 2024 2025 2026 --output-dir data --local-only

# Ou envia direto para o BigQuery (carga histórica de anos fechados + 2026 até o momento):
export GCP_PROJECT_ID="SEU_PROJETO_GCP"
python -m src.main --years 2024 2025 2026

# Atualização mensal pontual (ex.: outubro/2026):
python -m src.main --year 2026 --month 10
```

## 2. Deploy na nuvem (Cloud Run Jobs + Cloud Scheduler)

Defina as variáveis do seu projeto GCP:

```bash
export PROJECT_ID="SEU_PROJETO_GCP"
export REGION="southamerica-east1"
export JOB_NAME="transparencia-santos-etl"
export SA_NAME="transparencia-etl-sa"
```

1. **Habilitar APIs necessárias:**
```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  bigquery.googleapis.com \
  --project "$PROJECT_ID"
```

2. **Criar Service Account com permissão no BigQuery e para disparar o Cloud Run Job:**
```bash
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Transparencia Santos ETL" \
  --project "$PROJECT_ID"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

3. **Build e deploy do Cloud Run Job:**
```bash
gcloud builds submit --tag "${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${JOB_NAME}:latest" --project "$PROJECT_ID"

gcloud run jobs create "$JOB_NAME" \
  --image "${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${JOB_NAME}:latest" \
  --region "$REGION" \
  --service-account "${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID},BQ_DATASET=transparencia_santos,BQ_LOCATION=${REGION}" \
  --memory 1Gi \
  --task-timeout 1800s \
  --project "$PROJECT_ID"
```

4. **Execução manual inicial (e carga histórica, se desejar):**
```bash
# Executa o mês anterior automaticamente:
gcloud run jobs execute "$JOB_NAME" --region "$REGION" --project "$PROJECT_ID"

# Ou sobrescreve ano/mês para carga histórica (ex: out/2026):
gcloud run jobs execute "$JOB_NAME" \
  --region "$REGION" \
  --update-env-vars "TARGET_YEAR=2026,TARGET_MONTH=10" \
  --project "$PROJECT_ID"
```

5. **Gatilho mensal com Cloud Scheduler (todo dia 5 às 06:00 BRT):**
```bash
gcloud scheduler jobs create http "${JOB_NAME}-mensal" \
  --location "$REGION" \
  --schedule "0 6 5 * *" \
  --time-zone "America/Sao_Paulo" \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
  --http-method POST \
  --oauth-service-account-email "${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project "$PROJECT_ID"
```

## 3. Views e conexão com o Data Studio

1. Após a primeira execução do job, abra `sql/views_looker_studio.sql`, substitua `SEU_PROJETO` pelo seu `PROJECT_ID` e execute no **BigQuery Console**.
2. No **Google Data Studio**:
   - **Criar → Fonte de dados → BigQuery**.
   - Selecione seu projeto → dataset `transparencia_santos` → `vw_balanco_mensal` (e/ou `vw_receitas_mensal` e `vw_despesas_fases_mensal`).
   - Monte os gráficos (série temporal por `competencia`, filtros por `unidade_gestora`, `funcao`, `fonte_recurso`, `fase`).
   - Inclua no rodapé do relatório a citação oficial da fonte recomendada pelo portal:
     `Fonte: Portal da Transparência de Santos Sp - http://santos-sp.portaltp.com.br/`
   - Para receber o relatório todo mês: **Compartilhar → Agendar entrega** (configure para todo dia 6, um dia após o Cloud Scheduler rodar).

## Fonte dos Dados e Licença

- **Fonte oficial:** [Portal da Transparência de Santos Sp](http://santos-sp.portaltp.com.br/) (`http://santos-sp.portaltp.com.br/`)
- **Licença:** [Creative Commons CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
- **Recomendações de uso:** Ao utilizar, redistribuir ou publicar análises e relatórios gerados a partir destes dados, cite a fonte oficial acima e respeite a integridade e disponibilidade da API, bem como a legislação vigente sobre proteção de dados pessoais (LGPD).

## Próximas evoluções planejadas

- **Ciclo de vida do empenho:** Criar view no BigQuery relacionando `empenhos` → `liquidacoes` → `pagamentos` pelas chaves (`empenho`, `liquidacao`, `unidade_gestora`) para acompanhar em um único lugar toda a vida útil de cada empenho (valor empenhado, liquidado, pago e saldos pendentes).
- **Suporte a novos portais:** Parametrizar múltiplos portais/municípios além de Santos-SP.
- **Padrão de idioma:** Toda a documentação, docstrings e comentários do projeto devem ser mantidos em `pt-BR`.
