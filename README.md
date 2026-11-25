# Transparência Santos (GPI - E&L) + SICONFI/FINBRA → BigQuery → Data Studio

Pipeline automatizado em Python para extrair dados financeiros de **Santos-SP** de duas fontes complementares:

1. **Portal da Transparência de Santos (sistema GPI - E&L):** Receitas, Despesas (`empenhos`, `liquidacoes`, `pagamentos`) e Orçamento (`orcamento_receitas`, `orcamento_despesas`).
2. **SICONFI / FINBRA (Secretaria do Tesouro Nacional — `id_ente=3548500`):** Declaração de Contas Anuais (`DCA-Anexo I-C` — FINBRA anual), Matriz de Saldos Contábeis Orçamentária (`MSC` mensal — contas `6212*` e `6213*`), Relatório Resumido da Execução Orçamentária (`RREO-Anexo 01` bimestral) e Extrato de Entregas.

O pipeline carrega ambas as bases de forma idempotente no **Google BigQuery** e oferece views prontas para **conciliar receitas arrecadadas (Bruta, Deduções e Líquida)** por tipo (IPTU, ISS, IR/IRRF, ITBI, Taxas, COSIP, FPM, ICMS, IPVA, FUNDEB etc.) e por grupo (**Tributárias e Patrimoniais**, **Contribuições**, **Transferências Correntes** etc.) no **Google Data Studio / Looker Studio**.

## Estrutura do projeto

- `src/config.py`: Definição declarativa dos 6 endpoints do Portal da Transparência de Santos (`json_execucao_receitas`, `json_empenhos`, `json_liquidacoes`, `json_pagamentos`, `json_orcamento_despesas`, `json_orcamento_receitas`) e variáveis de ambiente.
- `src/extractor.py`: Cliente HTTP paginado (`pagina` + `tamanho=1000`) com retentativas para o GPI - E&L.
- `src/transformer.py`: Limpeza de espaços extras, normalização de `ano_ref`/`mes_ref`, datas (`DATE`) e valores financeiros (`NUMERIC`).
- `src/siconfi_config.py`: Configurações e esquemas BigQuery dos 4 datasets do SICONFI (`siconfi_extrato_entregas`, `siconfi_dca_receitas`, `siconfi_msc_receitas`, `siconfi_rreo_anexo01`).
- `src/siconfi_extractor.py`: Cliente paginado da API SICONFI (`limit=5000`, `offset`, `hasMore`) respeitando o limite oficial de 1 req/s da STN.
- `src/siconfi_transformer.py`: Normalização dos payloads SICONFI, marcação de contas folha na DCA (evitando dupla contagem hierárquica) e aplicação de sinal contábil (`C`/`D`) nas contas `6212*` e `6213*` da MSC.
- `src/revenue_classifier.py`: Classificação padronizada de Natureza da Receita (MCASP/STN) funcionando tanto por código numérico (SICONFI) quanto por hierarquia textual (GPI - E&L).
- `src/bigquery_loader.py`: Criação automática do dataset/tabelas e substituição atômica dos períodos via tabela temporária de staging.
- `src/main.py`: Ponto de entrada unificado (`--source gpi|siconfi|all`; por padrão processa o **mês anterior** no fuso `America/Sao_Paulo`).
- `sql/views_looker_studio.sql`: Views gerais do GPI (`vw_receitas_mensal`, `vw_despesas_fases_mensal`, `vw_balanco_mensal`, `vw_orcamento_vs_realizado_anual`).
- `sql/views_conciliacao_siconfi.sql`: Views de conciliação **GPI (E&L) × SICONFI/FINBRA** (`vw_receitas_gpi_classificadas`, `vw_conciliacao_receitas_mensal`, `vw_conciliacao_receitas_anual_finbra`, `vw_conciliacao_receitas_bimestral_rreo`).
- `Dockerfile`: Imagem leve para execução como **Cloud Run Job**.

## 1. Teste local, download e carga histórica (2024, 2025, 2026)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Baixa e salva todos os 6 datasets do GPI (Santos) de 2024, 2025 e 2026 localmente em data/ (sem BigQuery):
python -m src.main --source gpi --years 2024 2025 2026 --output-dir data/gpi --local-only

# Baixa SICONFI/FINBRA (Extrato, DCA I-C, RREO Anexo 01 e MSC Orçamentária) de Santos (3548500) para 2024-2026:
python -m src.main --source siconfi --years 2024 2025 2026 --output-dir data/siconfi --local-only

# Ou envia ambas as fontes direto para o BigQuery (carga histórica 2024, 2025 e 2026):
export GCP_PROJECT_ID="SEU_PROJETO_GCP"
python -m src.main --source all --years 2024 2025 2026

# Atualização mensal pontual (ex.: setembro/2026):
python -m src.main --source all --year 2026 --month 9
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
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID},BQ_DATASET=transparencia_santos,BQ_LOCATION=${REGION},ETL_SOURCE=all" \
  --memory 1Gi \
  --task-timeout 1800s \
  --project "$PROJECT_ID"
```

4. **Execução manual inicial (e carga histórica, se desejar):**
```bash
# Executa o mês anterior automaticamente:
gcloud run jobs execute "$JOB_NAME" --region "$REGION" --project "$PROJECT_ID"

# Ou sobrescreve ano/mês para carga pontual (ex.: out/2026):
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

1. Após a primeira carga das tabelas, abra `sql/views_looker_studio.sql` e `sql/views_conciliacao_siconfi.sql`, substitua `SEU_PROJETO` pelo seu `PROJECT_ID` e execute no **BigQuery Console**.
2. No **Google Data Studio / Looker Studio**:
   - **Criar → Fonte de dados → BigQuery**.
   - Para **conciliação de receitas GPI × SICONFI**, conecte:
     - `vw_conciliacao_receitas_mensal` (comparação mensal GPI × MSC Orçamentária com Bruta, Deduções, Líquida e diferenças em R\$ e %);
     - `vw_conciliacao_receitas_anual_finbra` (fechamento anual GPI × DCA Anexo I-C / FINBRA);
     - `vw_conciliacao_receitas_bimestral_rreo` (conferência bimestral contra o RREO Anexo 01).
   - Utilize os filtros por `ano_ref`, `competencia`, `grupo_gerencial` (*Tributárias e Patrimoniais*, *Contribuições*, *Transferências Correntes*), `origem_mcasp`, `tipo_receita` (*IPTU*, *ISS / ISSQN*, *IR / IRRF*, *ITBI*, *Taxas*, etc.), `eh_intra_orcamentaria` e `status_conciliacao`.
   - Inclua no rodapé do relatório a citação oficial das fontes:
     `Fontes: Portal da Transparência de Santos-SP (http://santos-sp.portaltp.com.br/) e SICONFI/STN (https://siconfi.tesouro.gov.br/)`
   - Para receber o relatório todo mês: **Compartilhar → Agendar entrega**.

## Fonte dos Dados e Licença

- **Fonte oficial local:** [Portal da Transparência de Santos Sp](http://santos-sp.portaltp.com.br/) (`http://santos-sp.portaltp.com.br/`) — Licença [Creative Commons CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
- **Fonte oficial federal:** [SICONFI / Tesouro Nacional](https://siconfi.tesouro.gov.br/) (`https://apidatalake.tesouro.gov.br/docs/siconfi/`) — Licença Apache 2.0 / ODbL
- **Recomendações de uso:** Ao utilizar, redistribuir ou publicar análises e relatórios gerados a partir destes dados, cite as fontes oficiais acima e respeite a integridade e disponibilidade das APIs, bem como a legislação vigente sobre proteção de dados pessoais (LGPD).

## Próximas evoluções planejadas

- **Ciclo de vida do empenho (otimizado para Data Studio):** Estruturar em 2 camadas no BigQuery — tabela/view resumida (1 linha por empenho com totais empenhado, liquidado, pago e saldos) + detalhamento sob demanda das liquidações e pagamentos do empenho selecionado (usando clusterização por `empenho` e parâmetro no Looker Studio para evitar sobrecarga de volume).
- **Padrão de idioma:** Toda a documentação, docstrings e comentários do projeto devem ser mantidos em `pt-BR`.
