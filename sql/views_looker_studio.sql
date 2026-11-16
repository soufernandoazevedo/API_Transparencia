-- Substitua SEU_PROJETO pelo ID do seu projeto no Google Cloud
-- e execute no console do BigQuery após a primeira carga das tabelas.
--
-- Fonte oficial dos dados: Portal da Transparência de Santos Sp - http://santos-sp.portaltp.com.br/
-- Licença: CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/)

-- 1. Receitas consolidadas por mês, unidade gestora, categoria e fonte de recurso
CREATE OR REPLACE VIEW `SEU_PROJETO.transparencia_santos.vw_receitas_mensal` AS
SELECT
  ano_ref,
  mes_ref,
  DATE(ano_ref, mes_ref, 1) AS competencia,
  unidade_gestora,
  categoria,
  especie,
  fonte_recurso,
  receita,
  origem,
  SUM(valor_previsto) AS valor_previsto,
  SUM(valor_realizado) AS valor_realizado
FROM `SEU_PROJETO.transparencia_santos.receitas`
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9;

-- 2. Despesas unificadas por fase (Empenho, Liquidação, Pagamento) para Looker Studio
CREATE OR REPLACE VIEW `SEU_PROJETO.transparencia_santos.vw_despesas_fases_mensal` AS
SELECT
  '1 - Empenho' AS fase,
  ano_ref,
  mes_ref,
  DATE(ano_ref, mes_ref, 1) AS competencia,
  unidade_gestora,
  funcao,
  subfuncao,
  programa,
  grupo_despesa,
  elemento_despesa,
  fonte_recurso,
  SUM(valor) AS valor
FROM `SEU_PROJETO.transparencia_santos.empenhos`
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11

UNION ALL

SELECT
  '2 - Liquidação' AS fase,
  ano_ref,
  mes_ref,
  DATE(ano_ref, mes_ref, 1) AS competencia,
  unidade_gestora,
  funcao,
  subfuncao,
  programa,
  grupo_despesa,
  elemento_despesa,
  fonte_recurso,
  SUM(valor) AS valor
FROM `SEU_PROJETO.transparencia_santos.liquidacoes`
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11

UNION ALL

SELECT
  '3 - Pagamento' AS fase,
  ano_ref,
  mes_ref,
  DATE(ano_ref, mes_ref, 1) AS competencia,
  unidade_gestora,
  funcao,
  subfuncao,
  programa,
  grupo_despesa,
  elemento_despesa,
  fonte_recurso,
  SUM(valor) AS valor
FROM `SEU_PROJETO.transparencia_santos.pagamentos`
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11;

-- 3. Balanço mensal (Receita Realizada x Empenhado x Liquidado x Pago)
CREATE OR REPLACE VIEW `SEU_PROJETO.transparencia_santos.vw_balanco_mensal` AS
WITH rec AS (
  SELECT ano_ref, mes_ref, unidade_gestora, SUM(valor_realizado) AS receita_realizada
  FROM `SEU_PROJETO.transparencia_santos.receitas`
  GROUP BY 1, 2, 3
),
emp AS (
  SELECT ano_ref, mes_ref, unidade_gestora, SUM(valor) AS valor_empenhado
  FROM `SEU_PROJETO.transparencia_santos.empenhos`
  GROUP BY 1, 2, 3
),
liq AS (
  SELECT ano_ref, mes_ref, unidade_gestora, SUM(valor) AS valor_liquidado
  FROM `SEU_PROJETO.transparencia_santos.liquidacoes`
  GROUP BY 1, 2, 3
),
pag AS (
  SELECT ano_ref, mes_ref, unidade_gestora, SUM(valor) AS valor_pago
  FROM `SEU_PROJETO.transparencia_santos.pagamentos`
  GROUP BY 1, 2, 3
)
SELECT
  COALESCE(rec.ano_ref, emp.ano_ref, liq.ano_ref, pag.ano_ref) AS ano_ref,
  COALESCE(rec.mes_ref, emp.mes_ref, liq.mes_ref, pag.mes_ref) AS mes_ref,
  DATE(
    COALESCE(rec.ano_ref, emp.ano_ref, liq.ano_ref, pag.ano_ref),
    COALESCE(rec.mes_ref, emp.mes_ref, liq.mes_ref, pag.mes_ref),
    1
  ) AS competencia,
  COALESCE(rec.unidade_gestora, emp.unidade_gestora, liq.unidade_gestora, pag.unidade_gestora) AS unidade_gestora,
  COALESCE(rec.receita_realizada, 0) AS receita_realizada,
  COALESCE(emp.valor_empenhado, 0) AS valor_empenhado,
  COALESCE(liq.valor_liquidado, 0) AS valor_liquidado,
  COALESCE(pag.valor_pago, 0) AS valor_pago
FROM rec
FULL OUTER JOIN emp USING (ano_ref, mes_ref, unidade_gestora)
FULL OUTER JOIN liq USING (ano_ref, mes_ref, unidade_gestora)
FULL OUTER JOIN pag USING (ano_ref, mes_ref, unidade_gestora);

-- 4. Comparativo anual: Orçado x Realizado (Receitas e Despesas)
CREATE OR REPLACE VIEW `SEU_PROJETO.transparencia_santos.vw_orcamento_vs_realizado_anual` AS
WITH orc_rec AS (
  SELECT ano_ref, unidade_gestora, SUM(valor) AS receita_orcada
  FROM `SEU_PROJETO.transparencia_santos.orcamento_receitas`
  GROUP BY 1, 2
),
real_rec AS (
  SELECT ano_ref, unidade_gestora, SUM(valor_realizado) AS receita_realizada
  FROM `SEU_PROJETO.transparencia_santos.receitas`
  GROUP BY 1, 2
),
orc_desp AS (
  SELECT ano_ref, unidade_gestora, SUM(valor) AS despesa_orcada
  FROM `SEU_PROJETO.transparencia_santos.orcamento_despesas`
  GROUP BY 1, 2
),
emp AS (
  SELECT ano_ref, unidade_gestora, SUM(valor) AS valor_empenhado
  FROM `SEU_PROJETO.transparencia_santos.empenhos`
  GROUP BY 1, 2
),
liq AS (
  SELECT ano_ref, unidade_gestora, SUM(valor) AS valor_liquidado
  FROM `SEU_PROJETO.transparencia_santos.liquidacoes`
  GROUP BY 1, 2
),
pag AS (
  SELECT ano_ref, unidade_gestora, SUM(valor) AS valor_pago
  FROM `SEU_PROJETO.transparencia_santos.pagamentos`
  GROUP BY 1, 2
)
SELECT
  COALESCE(orc_rec.ano_ref, real_rec.ano_ref, orc_desp.ano_ref, emp.ano_ref, liq.ano_ref, pag.ano_ref) AS ano_ref,
  COALESCE(orc_rec.unidade_gestora, real_rec.unidade_gestora, orc_desp.unidade_gestora, emp.unidade_gestora, liq.unidade_gestora, pag.unidade_gestora) AS unidade_gestora,
  COALESCE(orc_rec.receita_orcada, 0) AS receita_orcada,
  COALESCE(real_rec.receita_realizada, 0) AS receita_realizada,
  COALESCE(orc_desp.despesa_orcada, 0) AS despesa_orcada,
  COALESCE(emp.valor_empenhado, 0) AS valor_empenhado,
  COALESCE(liq.valor_liquidado, 0) AS valor_liquidado,
  COALESCE(pag.valor_pago, 0) AS valor_pago
FROM orc_rec
FULL OUTER JOIN real_rec USING (ano_ref, unidade_gestora)
FULL OUTER JOIN orc_desp USING (ano_ref, unidade_gestora)
FULL OUTER JOIN emp USING (ano_ref, unidade_gestora)
FULL OUTER JOIN liq USING (ano_ref, unidade_gestora)
FULL OUTER JOIN pag USING (ano_ref, unidade_gestora);
