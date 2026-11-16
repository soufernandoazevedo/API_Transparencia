"""Unit tests for Santos Transparency Portal ETL."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

# Allow running unit tests without installing external dependencies locally.
if "google.cloud.bigquery" not in sys.modules:
    google_mod = sys.modules.setdefault("google", types.ModuleType("google"))
    cloud_mod = types.ModuleType("google.cloud")
    bq_mod = types.ModuleType("google.cloud.bigquery")
    bq_mod.Client = MagicMock()
    google_mod.cloud = cloud_mod
    cloud_mod.bigquery = bq_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bq_mod

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_mod = types.ModuleType("requests")
    requests_mod.Session = MagicMock()
    adapters_mod = types.ModuleType("requests.adapters")
    adapters_mod.HTTPAdapter = MagicMock()
    urllib3_mod = sys.modules.setdefault("urllib3", types.ModuleType("urllib3"))
    util_mod = types.ModuleType("urllib3.util")
    retry_mod = types.ModuleType("urllib3.util.retry")
    retry_mod.Retry = MagicMock()
    sys.modules["requests"] = requests_mod
    sys.modules["requests.adapters"] = adapters_mod
    sys.modules["urllib3.util"] = util_mod
    sys.modules["urllib3.util.retry"] = retry_mod

from src.config import ENDPOINTS, AppConfig
from src.extractor import fetch_period
from src.main import resolve_previous_month
from src.transformer import normalize_batch


class TestTransformer(unittest.TestCase):
    def test_normalizes_receitas_strips_spaces_and_formats_numeric(self) -> None:
        sample = [
            {
                "ano": "2026",
                "mes": "9",
                "unidade_gestora": "CAIXA DE ASSIST. AO SERV.PÚBL. MUNIC. DE SANTOS",
                "data": "2026-09-01T00:00:00",
                "categoria": " IRRF - TERCEIROS PJ",
                "conta_origem": " IRRF - TERCEIROS PJ",
                "especie": " IRRF - TERCEIROS PJ",
                "rubrica": " IRRF - TERCEIROS PJ",
                "alinea": " IRRF - TERCEIROS PJ",
                "subalinea": " IRRF - TERCEIROS PJ",
                "fonte_recurso": "1100000 - GERAL",
                "receita": "Extra Orçamentário",
                "origem": "Liquidação/Desconto",
                "valor_previsto": 0.000000000,
                "valor_realizado": 2105.510000000,
            }
        ]
        rows = normalize_batch(sample, ENDPOINTS["receitas"], ano_ref=2026, mes_ref=9)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["ano_ref"], 2026)
        self.assertEqual(row["mes_ref"], 9)
        self.assertEqual(row["categoria"], "IRRF - TERCEIROS PJ")
        self.assertEqual(row["data"], "2026-09-01")
        self.assertEqual(row["valor_previsto"], "0")
        self.assertEqual(row["valor_realizado"], "2105.51")

    def test_normalizes_pagamentos_with_null_subtitulo_and_grupo(self) -> None:
        sample = [
            {
                "ano": "2026",
                "mes": "11 - Novembro",
                "unidade_gestora": "PREFEITURA MUNICIPAL DE SANTOS",
                "data": "2026-11-03T00:00:00",
                "especie": "Original",
                "empenho": "00014456/2026",
                "liquidacao": "00035716/2026",
                "pagamento": "00065536/2026",
                "tipo_pagamento": "Extra Orçamentário",
                "elemento_despesa": "218830104209 - IRRF",
                "subtitulo": None,
                "funcao": "12 - EDUCAÇÃO",
                "subfuncao": "361 - ENSINO FUNDAMENTAL",
                "programa": "0020 - EDUCACAO BASICA",
                "fonte_recurso": "2200021 - PREMIO",
                "grupo_despesa": None,
                "documento_favorecido": "58.200.015/0001-83",
                "nome_favorecido": "MUNICÍPIO DE SANTOS ",
                "valor": 7.130000000,
            }
        ]
        rows = normalize_batch(sample, ENDPOINTS["pagamentos"], ano_ref=2026, mes_ref=11)
        row = rows[0]
        self.assertEqual(row["ano_ref"], 2026)
        self.assertEqual(row["mes_ref"], 11)
        self.assertIsNone(row["subtitulo"])
        self.assertIsNone(row["grupo_despesa"])
        self.assertEqual(row["nome_favorecido"], "MUNICÍPIO DE SANTOS")
        self.assertEqual(row["valor"], "7.13")

    def test_normalizes_annual_orcamento_despesas_without_mes_ref(self) -> None:
        sample = [
            {
                "ano": "2026",
                "unidade_gestora": "CAIXA DE ASSIST. AO SERV.PÚBL. MUNIC. DE SANTOS",
                "funcao": "04 - ADMINISTRAÇÃO",
                "subfuncao": "122 - ADMINISTRAÇÃO GERAL",
                "programa": "0091 - GESTÃO E ASSISTENCIA A SAÚDE",
                "atividade": "2510 - ASSISTENCIA MEDICA E HOSPITALAR",
                "fonte_recurso": "04-1100000 - GERAL",
                "grupo_despesa": "330000000000 - OUTRAS DESPESAS CORRENTES",
                "elemento_despesa": "33909100 - SENTENÇAS JUDICIAIS",
                "valor": 300000.000000000,
            }
        ]
        rows = normalize_batch(sample, ENDPOINTS["orcamento_despesas"], ano_ref=2026)
        row = rows[0]
        self.assertEqual(row["ano_ref"], 2026)
        self.assertNotIn("mes_ref", row)
        self.assertNotIn("data", row)
        self.assertEqual(row["valor"], "300000")

    def test_normalizes_annual_orcamento_receitas_without_mes_ref(self) -> None:
        sample = [
            {
                "ano": "2026",
                "unidade_gestora": "INSTITUTO DE PREVIDENCIA SOCIAL DOS SERVIDORES PUBLICOS MUNICIPAIS DE SANTOS",
                "categoria": "70000000000000 - Receitas Correntes - Intra OFSS",
                "origem": "72000000000000 - Contribuições - Intra OFSS",
                "especie": "72100000000000 - Contribuições Sociais - Intra OFSS",
                "rubrica": "72150000000000 - Contribuições",
                "alinea": "72150200000000 - Contribuição Patronal",
                "subalinea": "72150200000000 - Contribuição Patronal",
                "fonte_recurso": "6050000 - RECURSOS VINCULADOS",
                "valor": 9000.000000000,
            }
        ]
        rows = normalize_batch(sample, ENDPOINTS["orcamento_receitas"], ano_ref=2026)
        row = rows[0]
        self.assertEqual(row["ano_ref"], 2026)
        self.assertNotIn("mes_ref", row)
        self.assertEqual(row["valor"], "9000")


class TestExtractorPagination(unittest.TestCase):
    def test_stops_when_page_smaller_than_page_size(self) -> None:
        config = AppConfig(page_size=2)
        session = MagicMock()

        resp1 = MagicMock()
        resp1.json.return_value = [{"id": 1}, {"id": 2}]
        resp2 = MagicMock()
        resp2.json.return_value = [{"id": 3}]
        session.get.side_effect = [resp1, resp2]

        rows = fetch_period(ENDPOINTS["empenhos"], 2026, 11, config, session=session)
        self.assertEqual(len(rows), 3)
        self.assertEqual(session.get.call_count, 2)

    def test_annual_endpoint_omits_mes_query_param(self) -> None:
        config = AppConfig(page_size=1000)
        session = MagicMock()

        resp = MagicMock()
        resp.json.return_value = [{"ano": "2026"}]
        session.get.return_value = resp

        rows = fetch_period(ENDPOINTS["orcamento_despesas"], 2026, None, config, session=session)
        self.assertEqual(len(rows), 1)
        _, kwargs = session.get.call_args
        self.assertNotIn("mes", kwargs["params"])
        self.assertEqual(kwargs["params"]["ano"], "2026")


class TestPreviousMonth(unittest.TestCase):
    def test_january_rolls_back_to_december_previous_year(self) -> None:
        dt = datetime(2027, 1, 5, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
        self.assertEqual(resolve_previous_month(dt), (2026, 12))

    def test_november_rolls_back_to_october(self) -> None:
        dt = datetime(2026, 11, 16, 1, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
        self.assertEqual(resolve_previous_month(dt), (2026, 10))


if __name__ == "__main__":
    unittest.main()
