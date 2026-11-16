"""Configuration and endpoint/schema definitions for Santos Transparency Portal ETL."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EndpointConfig:
    """Declarative specification for a Transparency Portal endpoint and its BigQuery table."""

    name: str
    api_method: str
    table_name: str
    string_fields: tuple[str, ...]
    numeric_fields: tuple[str, ...]
    date_fields: tuple[str, ...] = ("data",)
    requires_month: bool = True
    fixed_params: tuple[tuple[str, str], ...] = ()
    clustering_fields: tuple[str, ...] = ("ano_ref", "mes_ref", "unidade_gestora")

    @property
    def all_source_fields(self) -> tuple[str, ...]:
        return self.string_fields + self.date_fields + self.numeric_fields


ENDPOINTS: dict[str, EndpointConfig] = {
    "receitas": EndpointConfig(
        name="receitas",
        api_method="json_execucao_receitas",
        table_name="receitas",
        string_fields=(
            "ano",
            "mes",
            "unidade_gestora",
            "categoria",
            "conta_origem",
            "especie",
            "rubrica",
            "alinea",
            "subalinea",
            "fonte_recurso",
            "receita",
            "origem",
        ),
        numeric_fields=("valor_previsto", "valor_realizado"),
        requires_month=False,
        fixed_params=(("mes", "00"),),
    ),
    "empenhos": EndpointConfig(
        name="empenhos",
        api_method="json_empenhos",
        table_name="empenhos",
        string_fields=(
            "ano",
            "mes",
            "unidade_gestora",
            "especie",
            "empenho",
            "tipo_empenho",
            "elemento_despesa",
            "subtitulo",
            "funcao",
            "subfuncao",
            "programa",
            "fonte_recurso",
            "grupo_despesa",
            "documento_favorecido",
            "nome_favorecido",
        ),
        numeric_fields=("valor",),
    ),
    "liquidacoes": EndpointConfig(
        name="liquidacoes",
        api_method="json_liquidacoes",
        table_name="liquidacoes",
        string_fields=(
            "ano",
            "mes",
            "unidade_gestora",
            "especie",
            "empenho",
            "liquidacao",
            "tipo_liquidacao",
            "elemento_despesa",
            "subtitulo",
            "funcao",
            "subfuncao",
            "programa",
            "fonte_recurso",
            "grupo_despesa",
            "documento_favorecido",
            "nome_favorecido",
        ),
        numeric_fields=("valor",),
    ),
    "pagamentos": EndpointConfig(
        name="pagamentos",
        api_method="json_pagamentos",
        table_name="pagamentos",
        string_fields=(
            "ano",
            "mes",
            "unidade_gestora",
            "especie",
            "empenho",
            "liquidacao",
            "pagamento",
            "tipo_pagamento",
            "elemento_despesa",
            "subtitulo",
            "funcao",
            "subfuncao",
            "programa",
            "fonte_recurso",
            "grupo_despesa",
            "documento_favorecido",
            "nome_favorecido",
        ),
        numeric_fields=("valor",),
    ),
    "orcamento_despesas": EndpointConfig(
        name="orcamento_despesas",
        api_method="json_orcamento_despesas",
        table_name="orcamento_despesas",
        string_fields=(
            "ano",
            "unidade_gestora",
            "funcao",
            "subfuncao",
            "programa",
            "atividade",
            "fonte_recurso",
            "grupo_despesa",
            "elemento_despesa",
        ),
        numeric_fields=("valor",),
        date_fields=(),
        requires_month=False,
        clustering_fields=("ano_ref", "unidade_gestora"),
    ),
    "orcamento_receitas": EndpointConfig(
        name="orcamento_receitas",
        api_method="json_orcamento_receitas",
        table_name="orcamento_receitas",
        string_fields=(
            "ano",
            "unidade_gestora",
            "categoria",
            "origem",
            "especie",
            "rubrica",
            "alinea",
            "subalinea",
            "fonte_recurso",
        ),
        numeric_fields=("valor",),
        date_fields=(),
        requires_month=False,
        clustering_fields=("ano_ref", "unidade_gestora"),
    ),
}


@dataclass(frozen=True)
class AppConfig:
    """Runtime settings loaded from environment variables."""

    gcp_project_id: str = field(default_factory=lambda: os.getenv("GCP_PROJECT_ID", ""))
    bq_dataset: str = field(default_factory=lambda: os.getenv("BQ_DATASET", "transparencia_santos"))
    bq_location: str = field(default_factory=lambda: os.getenv("BQ_LOCATION", "southamerica-east1"))
    portal_base_url: str = field(
        default_factory=lambda: os.getenv(
            "PORTAL_BASE_URL",
            "https://santos-sp.portaltp.com.br/api/transparencia.asmx",
        ).rstrip("/")
    )
    page_size: int = field(default_factory=lambda: int(os.getenv("PAGE_SIZE", "1000")))
    request_timeout_sec: int = field(default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT_SEC", "60")))
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "HTTP_USER_AGENT",
            "Mozilla/5.0 (compatible; SantosTransparenciaETL/1.0)",
        )
    )
