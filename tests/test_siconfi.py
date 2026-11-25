"""Testes unitários da integração SICONFI/FINBRA e classificação de receitas."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Permite executar os testes unitários sem instalar dependências externas localmente.
if "google.cloud.bigquery" not in sys.modules:
    google_mod = sys.modules.setdefault("google", types.ModuleType("google"))
    cloud_mod = types.ModuleType("google.cloud")
    bq_mod = types.ModuleType("google.cloud.bigquery")
    bq_mod.Client = MagicMock()
    google_mod.cloud = cloud_mod
    cloud_mod.bigquery = bq_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bq_mod

from src.config import AppConfig
from src.main import _detectar_periodos_entregues, run_siconfi_pipeline
from src.revenue_classifier import (
    classificar_por_codigo_nr,
    classificar_receita_gpi,
    extrair_codigo_nr,
    extrair_segmentos_dca,
)
from src.siconfi_config import SiconfiConfig
from src.siconfi_extractor import fetch_dca_receitas, fetch_msc_receitas
from src.siconfi_transformer import (
    identificar_contas_folha_dca,
    normalize_dca_receitas,
    normalize_msc_receitas,
    normalize_rreo_anexo01,
)


class TestClassificadorReceitas(unittest.TestCase):
    def test_extrai_codigo_nr_de_diversos_formatos(self) -> None:
        self.assertEqual(extrair_codigo_nr("11125001"), "11125001")
        self.assertEqual(extrair_codigo_nr("RO1.1.1.2.50.0.0"), "11125000")
        self.assertEqual(extrair_codigo_nr("1.1.1.4.51.1.0 - ISSQN"), "11145110")
        self.assertEqual(extrair_codigo_nr("72150200000000 - Contribuição Patronal"), "72150200")
        self.assertIsNone(extrair_codigo_nr("ReceitasExcetoIntraOrcamentarias"))

    def test_extrai_segmentos_dca_preservando_zeros_significativos(self) -> None:
        self.assertEqual(extrair_segmentos_dca("RO1.1.1.2.00.0.0"), ("1", "1", "1", "2"))
        self.assertEqual(extrair_segmentos_dca("RO1.1.1.2.50.0.0"), ("1", "1", "1", "2", "50"))
        self.assertEqual(extrair_segmentos_dca("RO1.1.1.2.53.0.0"), ("1", "1", "1", "2", "53"))
        self.assertIsNone(extrair_segmentos_dca("ReceitasExcetoIntraOrcamentarias"))

    def test_classifica_principais_receitas_por_codigo_nr(self) -> None:
        casos = [
            ("11125001", "Tributárias e Patrimoniais", "1.1 - Impostos, Taxas e Contribuições de Melhoria", "IPTU", False),
            ("11125301", "Tributárias e Patrimoniais", "1.1 - Impostos, Taxas e Contribuições de Melhoria", "ITBI", False),
            ("11130311", "Tributárias e Patrimoniais", "1.1 - Impostos, Taxas e Contribuições de Melhoria", "IR / IRRF", False),
            ("11145111", "Tributárias e Patrimoniais", "1.1 - Impostos, Taxas e Contribuições de Melhoria", "ISS / ISSQN", False),
            ("11220101", "Tributárias e Patrimoniais", "1.1 - Impostos, Taxas e Contribuições de Melhoria", "Taxas", False),
            ("13210101", "Tributárias e Patrimoniais", "1.3 - Receita Patrimonial", "Valores Mobiliários / Aplicações Financeiras", False),
            ("12150111", "Contribuições", "1.2 - Contribuições", "Contribuições Sociais / RPPS", False),
            ("12415001", "Contribuições", "1.2 - Contribuições", "COSIP / Iluminação Pública", False),
            ("17115111", "Transferências Correntes", "1.7 - Transferências Correntes", "FPM", False),
            ("17215001", "Transferências Correntes", "1.7 - Transferências Correntes", "Cota-Parte ICMS", False),
            ("17215101", "Transferências Correntes", "1.7 - Transferências Correntes", "Cota-Parte IPVA", False),
            ("17515001", "Transferências Correntes", "1.7 - Transferências Correntes", "FUNDEB", False),
            ("72150211", "Contribuições", "1.2 - Contribuições", "Contribuições Sociais / RPPS", True),
        ]
        for codigo, grupo, origem, tipo, intra in casos:
            res = classificar_por_codigo_nr(codigo)
            self.assertEqual((res.grupo_gerencial, res.origem_mcasp, res.tipo_receita, res.eh_intra_orcamentaria), (grupo, origem, tipo, intra), msg=codigo)

    def test_classifica_receita_textual_gpi_santos(self) -> None:
        iptu = classificar_receita_gpi(
            categoria=" - RECEITAS CORRENTES",
            conta_origem=" - IMPOSTOS, TAXAS E CONTRIBUIÇÕES DE MELHORIA",
            especie=" - IMPOSTOS",
            rubrica=" - IMPOSTOS SOBRE O PATRIMÔNIO",
            alinea=" - Imposto sobre a Propriedade Predial e Territorial Urbana",
        )
        self.assertEqual(iptu.grupo_gerencial, "Tributárias e Patrimoniais")
        self.assertEqual(iptu.tipo_receita, "IPTU")
        self.assertFalse(iptu.eh_intra_orcamentaria)

        iss = classificar_receita_gpi(
            categoria=" - RECEITAS CORRENTES",
            conta_origem=" - IMPOSTOS, TAXAS E CONTRIBUIÇÕES DE MELHORIA",
            especie=" - IMPOSTOS",
            rubrica=" - Impostos sobre a Produção e Circulação de Mercadorias e Serviços",
            alinea=" - Impostos sobre Serviços",
        )
        self.assertEqual(iss.tipo_receita, "ISS / ISSQN")

        fundeb = classificar_receita_gpi(
            categoria=" - RECEITAS CORRENTES",
            conta_origem=" - TRANSFERÊNCIAS CORRENTES",
            especie=" - TRANSFERÊNCIAS DE OUTRAS INSTITUIÇÕES PÚBLICAS",
            rubrica=" - Transferências de Recursos do Fundo de Manutenção e Desenvolvimento da Educação Básica e de Valoriza",
            alinea=" - Transferências de Recursos do Fundo de Manutenção e Desenvolvimento da Educação Básica e de Valoriza",
        )
        self.assertEqual(fundeb.grupo_gerencial, "Transferências Correntes")
        self.assertEqual(fundeb.tipo_receita, "FUNDEB")

        intra = classificar_receita_gpi(
            categoria=" - Receitas Correntes - Intra OFSS",
            conta_origem=" - Transferências Correntes - Intra OFSS",
            especie=" - Outras Transferências Correntes - Intra OFSS",
        )
        self.assertTrue(intra.eh_intra_orcamentaria)
        self.assertEqual(intra.grupo_gerencial, "Transferências Correntes")


class TestSiconfiTransformer(unittest.TestCase):
    def test_identifica_contas_folha_dca_sem_dupla_contagem(self) -> None:
        amostra = [
            {"coluna": "Receitas Brutas Realizadas", "cod_conta": "ReceitasExcetoIntraOrcamentarias", "conta": "Total", "valor": 825.0},
            {"coluna": "Receitas Brutas Realizadas", "cod_conta": "ReceitasIntraOrcamentarias", "conta": "RECEITAS (INTRA-ORÇAMENTÁRIAS) (II)", "valor": 500.0},
            {"coluna": "Receitas Brutas Realizadas", "cod_conta": "RO1.1.1.2.00.0.0", "conta": "Patrimônio", "valor": 825.0},
            {"coluna": "Receitas Brutas Realizadas", "cod_conta": "RO1.1.1.2.50.0.0", "conta": "IPTU", "valor": 710.0},
            {"coluna": "Receitas Brutas Realizadas", "cod_conta": "RO1.1.1.2.53.0.0", "conta": "ITBI", "valor": 115.0},
            {"coluna": "Deduções - FUNDEB", "cod_conta": "RO1.7.1.1.51.0.0", "conta": "FPM", "valor": 50.0},
        ]
        folhas = identificar_contas_folha_dca(amostra)
        self.assertIn(("Receitas Brutas Realizadas", "RO1.1.1.2.50.0.0"), folhas)
        self.assertIn(("Receitas Brutas Realizadas", "RO1.1.1.2.53.0.0"), folhas)
        self.assertIn(("Receitas Brutas Realizadas", "ReceitasIntraOrcamentarias"), folhas)
        self.assertNotIn(("Receitas Brutas Realizadas", "RO1.1.1.2.00.0.0"), folhas)
        self.assertNotIn(("Receitas Brutas Realizadas", "ReceitasExcetoIntraOrcamentarias"), folhas)

        norm = normalize_dca_receitas(amostra, ano_ref=2025, id_ente="3548500")
        folhas_bruta = [r for r in norm if r["eh_conta_folha"] and not r["eh_deducao"]]
        self.assertEqual(len(folhas_bruta), 3)
        intra_row = [r for r in norm if r["cod_conta"] == "ReceitasIntraOrcamentarias"][0]
        self.assertTrue(intra_row["eh_intra_orcamentaria"])
        self.assertEqual(intra_row["grupo_gerencial"], "Receitas Intra-Orçamentárias")
        deducao = [r for r in norm if r["eh_deducao"]][0]
        self.assertEqual(deducao["valor_bruto"], "0")
        self.assertEqual(deducao["valor_deducao"], "50")
        self.assertEqual(deducao["valor_liquido"], "-50")

    def test_normaliza_msc_receitas_filtra_6212_6213_e_aplica_sinal_contabil(self) -> None:
        amostra = [
            # Conta de despesa (deve ser ignorada)
            {"conta_contabil": "622130100", "natureza_receita": None, "natureza_conta": "C", "valor": 999.0},
            # Receita realizada bruta (C soma)
            {"conta_contabil": "621200000", "poder_orgao": "10131", "natureza_receita": "11125001", "natureza_conta": "C", "valor": 1000.50, "data_referencia": "2025-01-31T00:00:00Z"},
            # Estorno de receita realizada bruta (D subtrai da bruta)
            {"conta_contabil": "621200000", "poder_orgao": "10131", "natureza_receita": "11125001", "natureza_conta": "D", "valor": 100.50, "data_referencia": "2025-01-31T00:00:00Z"},
            # Dedução FUNDEB (D soma na dedução e reduz líquida)
            {"conta_contabil": "621310100", "poder_orgao": "10131", "natureza_receita": "17215001", "natureza_conta": "D", "valor": 200.00, "data_referencia": "2025-01-31T00:00:00Z"},
        ]
        rows = normalize_msc_receitas(
            amostra,
            ano_ref=2025,
            mes_ref=1,
            tipo_matriz="MSCC",
            tipo_valor="period_change",
            id_ente="3548500",
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["tipo_receita"], "IPTU")
        self.assertEqual(rows[0]["valor_bruto"], "1000.5")
        self.assertEqual(rows[0]["valor_deducao"], "0")
        self.assertEqual(rows[0]["valor_liquido"], "1000.5")
        self.assertEqual(rows[0]["data_referencia"], "2025-01-31")

        # Estorno na 6212
        self.assertEqual(rows[1]["valor_bruto"], "-100.5")
        self.assertEqual(rows[1]["valor_liquido"], "-100.5")

        # Dedução na 6213
        self.assertTrue(rows[2]["eh_deducao"])
        self.assertEqual(rows[2]["tipo_receita"], "Cota-Parte ICMS")
        self.assertEqual(rows[2]["valor_bruto"], "0")
        self.assertEqual(rows[2]["valor_deducao"], "200")
        self.assertEqual(rows[2]["valor_liquido"], "-200")

    def test_normaliza_rreo_anexo01(self) -> None:
        amostra = [
            {
                "exercicio": 2025,
                "demonstrativo": "RREO",
                "periodo": 6,
                "periodicidade": "B",
                "instituicao": "Prefeitura Municipal de Santos - SP",
                "uf": "SP",
                "populacao": 414029,
                "anexo": "RREO-Anexo 01",
                "rotulo": "Padrão",
                "coluna": "Até o Bimestre (c)",
                "cod_conta": "ReceitasCorrentes",
                "conta": "RECEITAS CORRENTES",
                "valor": 5207943887.09,
            }
        ]
        rows = normalize_rreo_anexo01(amostra, ano_ref=2025, bimestre_ref=6, id_ente="3548500")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bimestre_ref"], 6)
        self.assertEqual(rows[0]["valor"], "5207943887.09")


class TestSiconfiExtractorEPipeline(unittest.TestCase):
    def test_paginacao_com_has_more_e_rate_limit(self) -> None:
        cfg = SiconfiConfig(id_ente="3548500", page_size=2, rate_limit_sec=1.05)
        session = MagicMock()
        r1 = MagicMock()
        r1.json.return_value = {"items": [{"a": 1}, {"a": 2}], "hasMore": True}
        r2 = MagicMock()
        r2.json.return_value = {"items": [{"a": 3}], "hasMore": False}
        session.get.side_effect = [r1, r2]
        sleeps: list[float] = []

        rows = fetch_msc_receitas(2025, 1, cfg, session=session, sleep_fn=sleeps.append)
        self.assertEqual(len(rows), 3)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(sleeps, [1.05, 1.05])
        _, kwargs2 = session.get.call_args_list[1]
        self.assertEqual(kwargs2["params"]["offset"], "2")

    def test_detecta_periodos_entregues_no_extrato(self) -> None:
        extrato = [
            {"entregavel": "Balanço Anual (DCA)", "periodo": 1},
            {"entregavel": "MSC Agregada", "periodo": 1},
            {"entregavel": "MSC Agregada", "periodo": 2},
            {"entregavel": "Relatório Resumido de Execução Orçamentária", "periodo": 1},
        ]
        tem_dca, meses, bimestres = _detectar_periodos_entregues(extrato)
        self.assertTrue(tem_dca)
        self.assertEqual(meses, [1, 2])
        self.assertEqual(bimestres, [1])

    def test_pipeline_siconfi_local_only_salva_arquivos_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch(
                    "src.main.fetch_extrato_entregas",
                    return_value=[{"exercicio": 2025, "entregavel": "Balanço Anual (DCA)", "periodo": 1}],
                ),
                patch(
                    "src.main.fetch_dca_receitas",
                    return_value=[{"exercicio": 2025, "coluna": "Receitas Brutas Realizadas", "cod_conta": "RO1.1.1.2.50.0.0", "conta": "IPTU", "valor": 100.0}],
                ),
            ):
                summary = run_siconfi_pipeline(
                    years=[2025],
                    month=None,
                    bimestre=None,
                    dataset_names=["extrato_entregas", "dca_receitas"],
                    app_config=AppConfig(),
                    siconfi_config=SiconfiConfig(),
                    local_only=True,
                    output_dir=Path(tmpdir),
                )
            self.assertEqual(summary["siconfi_extrato_entregas:2025"], 1)
            self.assertEqual(summary["siconfi_dca_receitas:2025"], 1)
            self.assertTrue((Path(tmpdir) / "siconfi_dca_receitas_2025.json").exists())


if __name__ == "__main__":
    unittest.main()
