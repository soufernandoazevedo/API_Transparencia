"""Configurações e esquemas dos conjuntos de dados da API do SICONFI/FINBRA (STN)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SiconfiConfig:
    """Configurações de execução da API de Dados Abertos do SICONFI."""

    base_url: str = field(
        default_factory=lambda: os.getenv(
            "SICONFI_BASE_URL",
            "https://apidatalake.tesouro.gov.br/ords/siconfi/tt",
        ).rstrip("/")
    )
    id_ente: str = field(default_factory=lambda: os.getenv("SICONFI_ID_ENTE", "3548500"))
    page_size: int = field(default_factory=lambda: int(os.getenv("SICONFI_PAGE_SIZE", "5000")))
    rate_limit_sec: float = field(
        default_factory=lambda: float(os.getenv("SICONFI_RATE_LIMIT_SEC", "1.05"))
    )
    request_timeout_sec: int = field(
        default_factory=lambda: int(os.getenv("SICONFI_REQUEST_TIMEOUT_SEC", "90"))
    )


@dataclass(frozen=True)
class SiconfiDatasetConfig:
    """Especificação declarativa de um conjunto de dados do SICONFI no BigQuery."""

    name: str
    api_path: str
    table_name: str
    # Tuplas (nome_coluna, tipo_bq, modo_bq)
    schema_fields: tuple[tuple[str, str, str], ...]
    clustering_fields: tuple[str, ...]
    # Colunas usadas no WHERE do DELETE atômico para garantir idempotência.
    delete_key_fields: tuple[str, ...]


SICONFI_DATASETS: dict[str, SiconfiDatasetConfig] = {
    "extrato_entregas": SiconfiDatasetConfig(
        name="extrato_entregas",
        api_path="extrato_entregas",
        table_name="siconfi_extrato_entregas",
        schema_fields=(
            ("ano_ref", "INT64", "REQUIRED"),
            ("id_ente", "STRING", "REQUIRED"),
            ("ingested_at", "TIMESTAMP", "REQUIRED"),
            ("exercicio", "INT64", "NULLABLE"),
            ("cod_ibge", "STRING", "NULLABLE"),
            ("populacao", "INT64", "NULLABLE"),
            ("instituicao", "STRING", "NULLABLE"),
            ("entregavel", "STRING", "NULLABLE"),
            ("periodo", "INT64", "NULLABLE"),
            ("periodicidade", "STRING", "NULLABLE"),
            ("status_relatorio", "STRING", "NULLABLE"),
            ("data_status", "STRING", "NULLABLE"),
            ("forma_envio", "STRING", "NULLABLE"),
            ("tipo_relatorio", "STRING", "NULLABLE"),
        ),
        clustering_fields=("ano_ref", "id_ente", "entregavel"),
        delete_key_fields=("ano_ref", "id_ente"),
    ),
    "dca_receitas": SiconfiDatasetConfig(
        name="dca_receitas",
        api_path="dca",
        table_name="siconfi_dca_receitas",
        schema_fields=(
            ("ano_ref", "INT64", "REQUIRED"),
            ("id_ente", "STRING", "REQUIRED"),
            ("ingested_at", "TIMESTAMP", "REQUIRED"),
            ("exercicio", "INT64", "NULLABLE"),
            ("instituicao", "STRING", "NULLABLE"),
            ("uf", "STRING", "NULLABLE"),
            ("populacao", "INT64", "NULLABLE"),
            ("anexo", "STRING", "NULLABLE"),
            ("rotulo", "STRING", "NULLABLE"),
            ("coluna", "STRING", "NULLABLE"),
            ("cod_conta", "STRING", "NULLABLE"),
            ("conta", "STRING", "NULLABLE"),
            ("codigo_nr", "STRING", "NULLABLE"),
            ("grupo_gerencial", "STRING", "NULLABLE"),
            ("origem_mcasp", "STRING", "NULLABLE"),
            ("tipo_receita", "STRING", "NULLABLE"),
            ("eh_intra_orcamentaria", "BOOL", "NULLABLE"),
            ("eh_deducao", "BOOL", "NULLABLE"),
            ("eh_conta_folha", "BOOL", "NULLABLE"),
            ("valor", "NUMERIC", "NULLABLE"),
            ("valor_bruto", "NUMERIC", "NULLABLE"),
            ("valor_deducao", "NUMERIC", "NULLABLE"),
            ("valor_liquido", "NUMERIC", "NULLABLE"),
        ),
        clustering_fields=("ano_ref", "grupo_gerencial", "tipo_receita"),
        delete_key_fields=("ano_ref", "id_ente"),
    ),
    "msc_receitas": SiconfiDatasetConfig(
        name="msc_receitas",
        api_path="msc_orcamentaria",
        table_name="siconfi_msc_receitas",
        schema_fields=(
            ("ano_ref", "INT64", "REQUIRED"),
            ("mes_ref", "INT64", "REQUIRED"),
            ("tipo_matriz", "STRING", "REQUIRED"),
            ("tipo_valor", "STRING", "REQUIRED"),
            ("id_ente", "STRING", "REQUIRED"),
            ("ingested_at", "TIMESTAMP", "REQUIRED"),
            ("data_referencia", "DATE", "NULLABLE"),
            ("classe_conta", "INT64", "NULLABLE"),
            ("conta_contabil", "STRING", "NULLABLE"),
            ("poder_orgao", "STRING", "NULLABLE"),
            ("ano_fonte_recursos", "STRING", "NULLABLE"),
            ("fonte_recursos", "STRING", "NULLABLE"),
            ("complemento_fonte", "STRING", "NULLABLE"),
            ("natureza_receita", "STRING", "NULLABLE"),
            ("natureza_conta", "STRING", "NULLABLE"),
            ("codigo_nr", "STRING", "NULLABLE"),
            ("grupo_gerencial", "STRING", "NULLABLE"),
            ("origem_mcasp", "STRING", "NULLABLE"),
            ("tipo_receita", "STRING", "NULLABLE"),
            ("eh_intra_orcamentaria", "BOOL", "NULLABLE"),
            ("eh_deducao", "BOOL", "NULLABLE"),
            ("valor_original", "NUMERIC", "NULLABLE"),
            ("valor_bruto", "NUMERIC", "NULLABLE"),
            ("valor_deducao", "NUMERIC", "NULLABLE"),
            ("valor_liquido", "NUMERIC", "NULLABLE"),
        ),
        clustering_fields=("ano_ref", "mes_ref", "grupo_gerencial", "tipo_receita"),
        delete_key_fields=("ano_ref", "mes_ref", "tipo_matriz", "tipo_valor", "id_ente"),
    ),
    "rreo_anexo01": SiconfiDatasetConfig(
        name="rreo_anexo01",
        api_path="rreo",
        table_name="siconfi_rreo_anexo01",
        schema_fields=(
            ("ano_ref", "INT64", "REQUIRED"),
            ("bimestre_ref", "INT64", "REQUIRED"),
            ("id_ente", "STRING", "REQUIRED"),
            ("ingested_at", "TIMESTAMP", "REQUIRED"),
            ("exercicio", "INT64", "NULLABLE"),
            ("demonstrativo", "STRING", "NULLABLE"),
            ("periodo", "INT64", "NULLABLE"),
            ("periodicidade", "STRING", "NULLABLE"),
            ("instituicao", "STRING", "NULLABLE"),
            ("uf", "STRING", "NULLABLE"),
            ("populacao", "INT64", "NULLABLE"),
            ("anexo", "STRING", "NULLABLE"),
            ("rotulo", "STRING", "NULLABLE"),
            ("coluna", "STRING", "NULLABLE"),
            ("cod_conta", "STRING", "NULLABLE"),
            ("conta", "STRING", "NULLABLE"),
            ("valor", "NUMERIC", "NULLABLE"),
        ),
        clustering_fields=("ano_ref", "bimestre_ref", "cod_conta"),
        delete_key_fields=("ano_ref", "bimestre_ref", "id_ente"),
    ),
}
