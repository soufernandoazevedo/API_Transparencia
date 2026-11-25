-- Substitua SEU_PROJETO pelo ID do seu projeto no Google Cloud
-- e execute no console do BigQuery após carregar as tabelas do GPI e do SICONFI.
--
-- Fontes oficiais dos dados:
-- - Portal da Transparência de Santos-SP (GPI - E&L): http://santos-sp.portaltp.com.br/
-- - SICONFI / FINBRA (Secretaria do Tesouro Nacional): https://siconfi.tesouro.gov.br/

-- ============================================================================
-- 1. Receitas Orçamentárias do GPI (E&L) classificadas no padrão MCASP/STN
--    - Separa Bruta, Deduções e Líquida para comparação direta com o SICONFI.
--    - Exclui receitas Extra-Orçamentárias (que não compõem o balanço orçamentário).
--    - Deduplicação inteligente orientada por dia/unidade gestora:
--      quando >= 20% das chaves naturais do dia vêm repetidas (case-insensitive,
--      sinalizando recarga duplicada/triplicada na API do Portal), mantém apenas
--      1 linha confiável por chave; em dias limpos (< 20%), preserva todas as linhas.
-- ============================================================================
CREATE OR REPLACE VIEW `SEU_PROJETO.transparencia_santos.vw_receitas_gpi_classificadas` AS
WITH base_bruta AS (
  SELECT
    ano_ref,
    mes_ref,
    data,
    DATE(ano_ref, mes_ref, 1) AS competencia,
    unidade_gestora,
    COALESCE(TRIM(fonte_recurso), '') AS fonte_recurso,
    receita,
    origem,
    -- Remove acentos e prefixos "- " para padronizar a classificação textual e a chave natural.
    UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(categoria, ''), NFD), r'\pM|^[\s\-–—]+', '')) AS cat_norm,
    UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(conta_origem, ''), NFD), r'\pM|^[\s\-–—]+', '')) AS orig_norm,
    UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(especie, ''), NFD), r'\pM|^[\s\-–—]+', '')) AS esp_norm,
    UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(rubrica, ''), NFD), r'\pM|^[\s\-–—]+', '')) AS rub_norm,
    UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(alinea, ''), NFD), r'\pM|^[\s\-–—]+', '')) AS ali_norm,
    UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(subalinea, ''), NFD), r'\pM|^[\s\-–—]+', '')) AS sub_norm,
    valor_previsto,
    valor_realizado
  FROM `SEU_PROJETO.transparencia_santos.receitas`
  WHERE COALESCE(TRIM(receita), '') != 'Extra Orçamentário'
),
numerada AS (
  SELECT
    *,
    COUNT(*) OVER (
      PARTITION BY
        ano_ref, mes_ref, data,
        TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(unidade_gestora, ''), NFD), r'\pM', '')), r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(NORMALIZE(fonte_recurso, NFD), r'\pM', '')), r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(receita, ''), NFD), r'\pM', '')), r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(origem, ''), NFD), r'\pM', '')), r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(cat_norm, r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(orig_norm, r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(esp_norm, r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(rub_norm, r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(ali_norm, r'[^A-Z0-9]+', ' ')),
        valor_previsto, valor_realizado
    ) AS qtd_copias_api,
    ROW_NUMBER() OVER (
      PARTITION BY
        ano_ref, mes_ref, data,
        TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(unidade_gestora, ''), NFD), r'\pM', '')), r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(NORMALIZE(fonte_recurso, NFD), r'\pM', '')), r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(receita, ''), NFD), r'\pM', '')), r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(UPPER(REGEXP_REPLACE(NORMALIZE(COALESCE(origem, ''), NFD), r'\pM', '')), r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(cat_norm, r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(orig_norm, r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(esp_norm, r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(rub_norm, r'[^A-Z0-9]+', ' ')),
        TRIM(REGEXP_REPLACE(ali_norm, r'[^A-Z0-9]+', ' ')),
        valor_previsto, valor_realizado
      ORDER BY IF(sub_norm != '', 0, 1), data
    ) AS rn_chave
  FROM base_bruta
),
diagnostico_dia AS (
  SELECT
    ano_ref,
    data,
    unidade_gestora,
    SAFE_DIVIDE(
      COUNTIF(rn_chave = 1 AND qtd_copias_api > 1),
      COUNTIF(rn_chave = 1)
    ) >= 0.20 AS dia_com_duplicidade_api
  FROM numerada
  GROUP BY 1, 2, 3
),
base AS (
  SELECT
    n.*,
    COALESCE(d.dia_com_duplicidade_api, FALSE) AS dia_com_duplicidade_api
  FROM numerada n
  LEFT JOIN diagnostico_dia d
    ON n.ano_ref = d.ano_ref
   AND n.data = d.data
   AND COALESCE(n.unidade_gestora, '') = COALESCE(d.unidade_gestora, '')
  WHERE NOT COALESCE(d.dia_com_duplicidade_api, FALSE)
     OR n.rn_chave = 1
),
enriquecida AS (
  SELECT
    *,
    CONCAT(cat_norm, ' | ', orig_norm, ' | ', esp_norm, ' | ', rub_norm, ' | ', ali_norm, ' | ', sub_norm) AS texto_completo,
    (CONTAINS_SUBSTR(cat_norm, 'INTRA') OR CONTAINS_SUBSTR(orig_norm, 'INTRA') OR CONTAINS_SUBSTR(esp_norm, 'INTRA')) AS eh_intra_orcamentaria
  FROM base
)
SELECT
  ano_ref,
  mes_ref,
  competencia,
  unidade_gestora,
  eh_intra_orcamentaria,
  dia_com_duplicidade_api,
  qtd_copias_api,

  -- Grupo gerencial (Tributárias e Patrimoniais reunidas conforme demanda)
  CASE
    WHEN CONTAINS_SUBSTR(cat_norm, 'CAPITAL') OR CONTAINS_SUBSTR(orig_norm, 'ALIENACAO DE BENS') OR CONTAINS_SUBSTR(orig_norm, 'OPERACOES DE CREDITO')
      THEN 'Receitas de Capital'
    WHEN CONTAINS_SUBSTR(orig_norm, 'IMPOSTOS, TAXAS E CONTRIBUICOES DE MELHORIA') OR CONTAINS_SUBSTR(orig_norm, 'PATRIMONIAL')
      THEN 'Tributárias e Patrimoniais'
    WHEN CONTAINS_SUBSTR(orig_norm, 'CONTRIBUICOES')
      THEN 'Contribuições'
    WHEN CONTAINS_SUBSTR(orig_norm, 'TRANSFERENCIAS CORRENTES')
      THEN 'Transferências Correntes'
    WHEN CONTAINS_SUBSTR(orig_norm, 'SERVICOS') OR CONTAINS_SUBSTR(orig_norm, 'OUTRAS RECEITAS CORRENTES') OR CONTAINS_SUBSTR(orig_norm, 'INDUSTRIAL') OR CONTAINS_SUBSTR(orig_norm, 'AGROPECUARIA')
      THEN 'Outras Receitas Correntes'
    ELSE 'Não Classificada'
  END AS grupo_gerencial,

  -- Origem oficial MCASP (mantém Tributária e Patrimonial separadas para detalhamento)
  CASE
    WHEN CONTAINS_SUBSTR(orig_norm, 'TRANSFERENCIAS DE CAPITAL') THEN '2.4 - Transferências de Capital'
    WHEN CONTAINS_SUBSTR(orig_norm, 'ALIENACAO DE BENS') THEN '2.2 - Alienação de Bens'
    WHEN CONTAINS_SUBSTR(orig_norm, 'OPERACOES DE CREDITO') THEN '2.1 - Operações de Crédito'
    WHEN CONTAINS_SUBSTR(orig_norm, 'AMORTIZACAO') THEN '2.3 - Amortização de Empréstimos'
    WHEN CONTAINS_SUBSTR(cat_norm, 'CAPITAL') THEN '2.9 - Outras Receitas de Capital'
    WHEN CONTAINS_SUBSTR(orig_norm, 'IMPOSTOS, TAXAS E CONTRIBUICOES DE MELHORIA') THEN '1.1 - Impostos, Taxas e Contribuições de Melhoria'
    WHEN CONTAINS_SUBSTR(orig_norm, 'CONTRIBUICOES') THEN '1.2 - Contribuições'
    WHEN CONTAINS_SUBSTR(orig_norm, 'PATRIMONIAL') THEN '1.3 - Receita Patrimonial'
    WHEN CONTAINS_SUBSTR(orig_norm, 'SERVICOS') THEN '1.6 - Receita de Serviços'
    WHEN CONTAINS_SUBSTR(orig_norm, 'TRANSFERENCIAS CORRENTES') THEN '1.7 - Transferências Correntes'
    WHEN CONTAINS_SUBSTR(orig_norm, 'OUTRAS RECEITAS CORRENTES') OR CONTAINS_SUBSTR(orig_norm, 'INDUSTRIAL') OR CONTAINS_SUBSTR(orig_norm, 'AGROPECUARIA')
      THEN '1.9 - Outras Receitas Correntes'
    ELSE 'Não Classificada'
  END AS origem_mcasp,

  -- Tipo de receita detalhado (IPTU, ISS, IR/IRRF, ITBI, Taxas, COSIP, FPM, ICMS, IPVA, FUNDEB, etc.)
  CASE
    WHEN CONTAINS_SUBSTR(orig_norm, 'TRANSFERENCIAS DE CAPITAL') THEN 'Transferências de Capital'
    WHEN CONTAINS_SUBSTR(orig_norm, 'ALIENACAO DE BENS') THEN 'Alienação de Bens'
    WHEN CONTAINS_SUBSTR(orig_norm, 'OPERACOES DE CREDITO') THEN 'Operações de Crédito'
    WHEN CONTAINS_SUBSTR(orig_norm, 'AMORTIZACAO') THEN 'Amortização de Empréstimos'
    WHEN CONTAINS_SUBSTR(cat_norm, 'CAPITAL') THEN 'Outras Receitas de Capital'

    WHEN CONTAINS_SUBSTR(orig_norm, 'IMPOSTOS, TAXAS E CONTRIBUICOES DE MELHORIA') THEN
      CASE
        WHEN CONTAINS_SUBSTR(texto_completo, 'PREDIAL E TERRITORIAL URBANA') OR CONTAINS_SUBSTR(texto_completo, 'IPTU') THEN 'IPTU'
        WHEN CONTAINS_SUBSTR(texto_completo, 'TRANSMISSAO') AND (CONTAINS_SUBSTR(texto_completo, 'INTER VIVOS') OR CONTAINS_SUBSTR(texto_completo, 'ITBI')) THEN 'ITBI'
        WHEN CONTAINS_SUBSTR(texto_completo, 'IMPOSTO SOBRE A RENDA') OR CONTAINS_SUBSTR(texto_completo, 'IRRF') THEN 'IR / IRRF'
        WHEN CONTAINS_SUBSTR(texto_completo, 'IMPOSTOS SOBRE SERVICOS') OR CONTAINS_SUBSTR(texto_completo, 'IMPOSTO SOBRE SERVICOS') OR CONTAINS_SUBSTR(texto_completo, 'ISSQN') THEN 'ISS / ISSQN'
        WHEN CONTAINS_SUBSTR(esp_norm, 'TAXAS') OR CONTAINS_SUBSTR(rub_norm, 'TAXA') THEN 'Taxas'
        WHEN CONTAINS_SUBSTR(esp_norm, 'CONTRIBUICAO DE MELHORIA') OR CONTAINS_SUBSTR(rub_norm, 'MELHORIA') THEN 'Contribuição de Melhoria'
        WHEN CONTAINS_SUBSTR(esp_norm, 'IMPOSTO') THEN 'Outros Impostos'
        ELSE 'Outras Receitas Tributárias'
      END

    WHEN CONTAINS_SUBSTR(orig_norm, 'PATRIMONIAL') THEN
      CASE
        WHEN CONTAINS_SUBSTR(esp_norm, 'MOBILIARIOS') OR CONTAINS_SUBSTR(texto_completo, 'DEPOSITOS BANCARIOS') THEN 'Valores Mobiliários / Aplicações Financeiras'
        WHEN CONTAINS_SUBSTR(esp_norm, 'IMOBILIARIO') THEN 'Exploração do Patrimônio Imobiliário'
        WHEN CONTAINS_SUBSTR(esp_norm, 'CONCESS') OR CONTAINS_SUBSTR(esp_norm, 'DELEGACAO') THEN 'Concessões e Permissões'
        WHEN CONTAINS_SUBSTR(esp_norm, 'RECURSOS NATURAIS') OR CONTAINS_SUBSTR(texto_completo, 'ROYALTIES') THEN 'Exploração de Recursos Naturais / Royalties'
        ELSE 'Outras Receitas Patrimoniais'
      END

    WHEN CONTAINS_SUBSTR(orig_norm, 'CONTRIBUICOES') THEN
      CASE
        WHEN CONTAINS_SUBSTR(texto_completo, 'ILUMINACAO PUBLICA') OR CONTAINS_SUBSTR(texto_completo, 'COSIP') THEN 'COSIP / Iluminação Pública'
        WHEN CONTAINS_SUBSTR(esp_norm, 'SOCIAIS') OR CONTAINS_SUBSTR(texto_completo, 'PREVIDENC') OR CONTAINS_SUBSTR(texto_completo, 'PATRONAL') THEN 'Contribuições Sociais / RPPS'
        ELSE 'Outras Contribuições'
      END

    WHEN CONTAINS_SUBSTR(orig_norm, 'TRANSFERENCIAS CORRENTES') THEN
      CASE
        WHEN CONTAINS_SUBSTR(texto_completo, 'FUNDO DE PARTICIPACAO DOS MUNICIPIOS') OR CONTAINS_SUBSTR(texto_completo, 'FPM') THEN 'FPM'
        WHEN CONTAINS_SUBSTR(texto_completo, 'TERRITORIAL RURAL') OR CONTAINS_SUBSTR(ali_norm, 'ITR') THEN 'ITR'
        WHEN CONTAINS_SUBSTR(texto_completo, 'FUNDO DE MANUTENCAO E DESENVOLVIMENTO DA EDUCACAO BASICA') OR CONTAINS_SUBSTR(texto_completo, 'FUNDEB') THEN 'FUNDEB'
        WHEN CONTAINS_SUBSTR(texto_completo, 'ICMS') THEN 'Cota-Parte ICMS'
        WHEN CONTAINS_SUBSTR(texto_completo, 'IPVA') THEN 'Cota-Parte IPVA'
        WHEN CONTAINS_SUBSTR(texto_completo, 'IPI') THEN 'Cota-Parte IPI-Exportação'
        WHEN CONTAINS_SUBSTR(texto_completo, 'SISTEMA UNICO DE SAUDE') OR CONTAINS_SUBSTR(texto_completo, 'SUS') THEN
          IF(CONTAINS_SUBSTR(esp_norm, 'ESTADOS'), 'Transferências SUS (Estado)', 'Transferências SUS (União)')
        WHEN CONTAINS_SUBSTR(texto_completo, 'FNDE') OR CONTAINS_SUBSTR(texto_completo, 'SALARIO-EDUCACAO') OR CONTAINS_SUBSTR(texto_completo, 'PNAE') OR CONTAINS_SUBSTR(texto_completo, 'PNATE') THEN 'Transferências FNDE / Educação'
        WHEN CONTAINS_SUBSTR(texto_completo, 'FNAS') OR CONTAINS_SUBSTR(texto_completo, 'ASSISTENCIA SOCIAL') THEN 'Transferências FNAS / Assistência Social'
        WHEN CONTAINS_SUBSTR(texto_completo, 'ROYALTIES') OR CONTAINS_SUBSTR(texto_completo, 'COMPENSACAO FINANCEIRA') OR CONTAINS_SUBSTR(texto_completo, 'COMPENSACOES FINANCEIRAS') THEN
          IF(CONTAINS_SUBSTR(esp_norm, 'ESTADOS'), 'Royalties / Compensações Financeiras (Estado)', 'Royalties / Compensações Financeiras (União)')
        WHEN CONTAINS_SUBSTR(esp_norm, 'UNIAO') THEN 'Outras Transferências da União'
        WHEN CONTAINS_SUBSTR(esp_norm, 'ESTADOS') THEN 'Outras Transferências do Estado'
        WHEN CONTAINS_SUBSTR(esp_norm, 'OUTRAS INSTITUICOES PUBLICAS') THEN 'Outras Transferências de Instituições Públicas'
        ELSE 'Outras Transferências Correntes'
      END

    WHEN CONTAINS_SUBSTR(orig_norm, 'SERVICOS') THEN 'Receita de Serviços'
    WHEN CONTAINS_SUBSTR(orig_norm, 'OUTRAS RECEITAS CORRENTES') OR CONTAINS_SUBSTR(orig_norm, 'INDUSTRIAL') OR CONTAINS_SUBSTR(orig_norm, 'AGROPECUARIA') THEN 'Outras Receitas Correntes'
    ELSE 'Não Classificada'
  END AS tipo_receita,

  -- Regra GPI Santos: "Arrecadação" negativa representa deduções (ex.: FUNDEB);
  -- "Anulação de Arrecadação" representa estorno da arrecadação bruta.
  CASE
    WHEN origem = 'Arrecadação' AND valor_realizado >= 0 THEN valor_realizado
    WHEN origem = 'Anulação de Arrecadação' THEN valor_realizado
    ELSE CAST(0 AS NUMERIC)
  END AS valor_bruto,

  CASE
    WHEN origem = 'Arrecadação' AND valor_realizado < 0 THEN ABS(valor_realizado)
    ELSE CAST(0 AS NUMERIC)
  END AS valor_deducao,

  valor_realizado AS valor_liquido
FROM enriquecida;


-- ============================================================================
-- 2. Conciliação MENSAL: GPI (E&L) x SICONFI MSC Orçamentária (period_change)
--    Permite acompanhar 2024, 2025 e 2026 mês a mês com Bruta, Deduções e Líquida.
-- ============================================================================
CREATE OR REPLACE VIEW `SEU_PROJETO.transparencia_santos.vw_conciliacao_receitas_mensal` AS
WITH gpi AS (
  SELECT
    ano_ref,
    mes_ref,
    grupo_gerencial,
    origem_mcasp,
    tipo_receita,
    eh_intra_orcamentaria,
    SUM(valor_bruto) AS gpi_valor_bruto,
    SUM(valor_deducao) AS gpi_valor_deducao,
    SUM(valor_liquido) AS gpi_valor_liquido
  FROM `SEU_PROJETO.transparencia_santos.vw_receitas_gpi_classificadas`
  GROUP BY 1, 2, 3, 4, 5, 6
),
siconfi AS (
  SELECT
    ano_ref,
    mes_ref,
    grupo_gerencial,
    origem_mcasp,
    tipo_receita,
    eh_intra_orcamentaria,
    SUM(valor_bruto) AS siconfi_valor_bruto,
    SUM(valor_deducao) AS siconfi_valor_deducao,
    SUM(valor_liquido) AS siconfi_valor_liquido
  FROM `SEU_PROJETO.transparencia_santos.siconfi_msc_receitas`
  WHERE tipo_matriz = 'MSCC'
    AND tipo_valor = 'period_change'
  GROUP BY 1, 2, 3, 4, 5, 6
),
entregas_msc AS (
  SELECT DISTINCT
    ano_ref,
    periodo AS mes_ref
  FROM `SEU_PROJETO.transparencia_santos.siconfi_extrato_entregas`
  WHERE UPPER(COALESCE(entregavel, '')) LIKE '%MSC AGREGADA%'
),
unido AS (
  SELECT
    COALESCE(g.ano_ref, s.ano_ref) AS ano_ref,
    COALESCE(g.mes_ref, s.mes_ref) AS mes_ref,
    DATE(COALESCE(g.ano_ref, s.ano_ref), COALESCE(g.mes_ref, s.mes_ref), 1) AS competencia,
    COALESCE(g.grupo_gerencial, s.grupo_gerencial) AS grupo_gerencial,
    COALESCE(g.origem_mcasp, s.origem_mcasp) AS origem_mcasp,
    COALESCE(g.tipo_receita, s.tipo_receita) AS tipo_receita,
    COALESCE(g.eh_intra_orcamentaria, s.eh_intra_orcamentaria) AS eh_intra_orcamentaria,
    COALESCE(g.gpi_valor_bruto, CAST(0 AS NUMERIC)) AS gpi_valor_bruto,
    COALESCE(g.gpi_valor_deducao, CAST(0 AS NUMERIC)) AS gpi_valor_deducao,
    COALESCE(g.gpi_valor_liquido, CAST(0 AS NUMERIC)) AS gpi_valor_liquido,
    COALESCE(s.siconfi_valor_bruto, CAST(0 AS NUMERIC)) AS siconfi_valor_bruto,
    COALESCE(s.siconfi_valor_deducao, CAST(0 AS NUMERIC)) AS siconfi_valor_deducao,
    COALESCE(s.siconfi_valor_liquido, CAST(0 AS NUMERIC)) AS siconfi_valor_liquido
  FROM gpi g
  FULL OUTER JOIN siconfi s
    USING (ano_ref, mes_ref, grupo_gerencial, origem_mcasp, tipo_receita, eh_intra_orcamentaria)
)
SELECT
  u.*,
  (u.gpi_valor_bruto - u.siconfi_valor_bruto) AS diferenca_bruta_rs,
  SAFE_DIVIDE(u.gpi_valor_bruto - u.siconfi_valor_bruto, NULLIF(u.siconfi_valor_bruto, 0)) AS diferenca_bruta_pct,
  (u.gpi_valor_deducao - u.siconfi_valor_deducao) AS diferenca_deducao_rs,
  SAFE_DIVIDE(u.gpi_valor_deducao - u.siconfi_valor_deducao, NULLIF(u.siconfi_valor_deducao, 0)) AS diferenca_deducao_pct,
  (u.gpi_valor_liquido - u.siconfi_valor_liquido) AS diferenca_liquida_rs,
  SAFE_DIVIDE(u.gpi_valor_liquido - u.siconfi_valor_liquido, NULLIF(u.siconfi_valor_liquido, 0)) AS diferenca_liquida_pct,
  CASE
    WHEN e.mes_ref IS NULL THEN 'Aguardando entrega SICONFI'
    WHEN ABS(u.gpi_valor_liquido - u.siconfi_valor_liquido) <= 0.05 THEN 'Conciliado'
    WHEN u.siconfi_valor_bruto = 0 AND u.siconfi_valor_deducao = 0 THEN 'Somente GPI'
    WHEN u.gpi_valor_bruto = 0 AND u.gpi_valor_deducao = 0 THEN 'Somente SICONFI'
    ELSE 'Divergência'
  END AS status_conciliacao
FROM unido u
LEFT JOIN entregas_msc e
  ON u.ano_ref = e.ano_ref AND u.mes_ref = e.mes_ref;


-- ============================================================================
-- 3. Conciliação ANUAL (FINBRA): GPI (E&L) x SICONFI DCA Anexo I-C
--    Usa apenas eh_conta_folha = TRUE na DCA para evitar dupla contagem da árvore.
-- ============================================================================
CREATE OR REPLACE VIEW `SEU_PROJETO.transparencia_santos.vw_conciliacao_receitas_anual_finbra` AS
WITH gpi AS (
  SELECT
    ano_ref,
    grupo_gerencial,
    origem_mcasp,
    tipo_receita,
    eh_intra_orcamentaria,
    SUM(valor_bruto) AS gpi_valor_bruto,
    SUM(valor_deducao) AS gpi_valor_deducao,
    SUM(valor_liquido) AS gpi_valor_liquido
  FROM `SEU_PROJETO.transparencia_santos.vw_receitas_gpi_classificadas`
  GROUP BY 1, 2, 3, 4, 5
),
dca AS (
  SELECT
    ano_ref,
    grupo_gerencial,
    origem_mcasp,
    tipo_receita,
    eh_intra_orcamentaria,
    SUM(valor_bruto) AS finbra_valor_bruto,
    SUM(valor_deducao) AS finbra_valor_deducao,
    SUM(valor_liquido) AS finbra_valor_liquido
  FROM `SEU_PROJETO.transparencia_santos.siconfi_dca_receitas`
  WHERE eh_conta_folha = TRUE
  GROUP BY 1, 2, 3, 4, 5
),
unido AS (
  SELECT
    COALESCE(g.ano_ref, d.ano_ref) AS ano_ref,
    COALESCE(g.grupo_gerencial, d.grupo_gerencial) AS grupo_gerencial,
    COALESCE(g.origem_mcasp, d.origem_mcasp) AS origem_mcasp,
    COALESCE(g.tipo_receita, d.tipo_receita) AS tipo_receita,
    COALESCE(g.eh_intra_orcamentaria, d.eh_intra_orcamentaria) AS eh_intra_orcamentaria,
    COALESCE(g.gpi_valor_bruto, CAST(0 AS NUMERIC)) AS gpi_valor_bruto,
    COALESCE(g.gpi_valor_deducao, CAST(0 AS NUMERIC)) AS gpi_valor_deducao,
    COALESCE(g.gpi_valor_liquido, CAST(0 AS NUMERIC)) AS gpi_valor_liquido,
    COALESCE(d.finbra_valor_bruto, CAST(0 AS NUMERIC)) AS finbra_valor_bruto,
    COALESCE(d.finbra_valor_deducao, CAST(0 AS NUMERIC)) AS finbra_valor_deducao,
    COALESCE(d.finbra_valor_liquido, CAST(0 AS NUMERIC)) AS finbra_valor_liquido
  FROM gpi g
  FULL OUTER JOIN dca d
    USING (ano_ref, grupo_gerencial, origem_mcasp, tipo_receita, eh_intra_orcamentaria)
)
SELECT
  u.*,
  (u.gpi_valor_bruto - u.finbra_valor_bruto) AS diferenca_bruta_rs,
  SAFE_DIVIDE(u.gpi_valor_bruto - u.finbra_valor_bruto, NULLIF(u.finbra_valor_bruto, 0)) AS diferenca_bruta_pct,
  (u.gpi_valor_deducao - u.finbra_valor_deducao) AS diferenca_deducao_rs,
  SAFE_DIVIDE(u.gpi_valor_deducao - u.finbra_valor_deducao, NULLIF(u.finbra_valor_deducao, 0)) AS diferenca_deducao_pct,
  (u.gpi_valor_liquido - u.finbra_valor_liquido) AS diferenca_liquida_rs,
  SAFE_DIVIDE(u.gpi_valor_liquido - u.finbra_valor_liquido, NULLIF(u.finbra_valor_liquido, 0)) AS diferenca_liquida_pct,
  CASE
    WHEN ABS(u.gpi_valor_liquido - u.finbra_valor_liquido) <= 0.05 THEN 'Conciliado'
    WHEN u.finbra_valor_bruto = 0 AND u.finbra_valor_deducao = 0 THEN 'Somente GPI'
    WHEN u.gpi_valor_bruto = 0 AND u.gpi_valor_deducao = 0 THEN 'Somente FINBRA'
    ELSE 'Divergência'
  END AS status_conciliacao
FROM unido u;


-- ============================================================================
-- 4. Conferência BIMESTRAL: RREO Anexo 01 (Balanço Orçamentário oficial)
--    Pivota Previsão Inicial, Previsão Atualizada, No Bimestre e Até o Bimestre.
-- ============================================================================
CREATE OR REPLACE VIEW `SEU_PROJETO.transparencia_santos.vw_conciliacao_receitas_bimestral_rreo` AS
SELECT
  ano_ref,
  bimestre_ref,
  cod_conta,
  conta,
  MAX(CASE WHEN coluna = 'PREVISÃO INICIAL' THEN valor END) AS previsao_inicial,
  MAX(CASE WHEN coluna = 'PREVISÃO ATUALIZADA (a)' THEN valor END) AS previsao_atualizada,
  MAX(CASE WHEN coluna = 'No Bimestre (b)' THEN valor END) AS realizada_no_bimestre,
  MAX(CASE WHEN coluna = 'Até o Bimestre (c)' THEN valor END) AS realizada_ate_bimestre,
  MAX(CASE WHEN coluna = 'SALDO (a-c)' THEN valor END) AS saldo_a_realizar
FROM `SEU_PROJETO.transparencia_santos.siconfi_rreo_anexo01`
WHERE coluna IN (
  'PREVISÃO INICIAL',
  'PREVISÃO ATUALIZADA (a)',
  'No Bimestre (b)',
  'Até o Bimestre (c)',
  'SALDO (a-c)'
)
GROUP BY 1, 2, 3, 4;
