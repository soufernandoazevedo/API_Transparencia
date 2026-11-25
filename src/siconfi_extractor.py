"""Extrator HTTP paginado para a API de Dados Abertos do SICONFI/FINBRA (STN)."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

from src.extractor import build_session
from src.siconfi_config import SiconfiConfig

logger = logging.getLogger(__name__)


def _fetch_paginated(
    api_path: str,
    params: dict[str, str],
    config: SiconfiConfig,
    session: requests.Session | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Percorre todas as páginas de um endpoint SICONFI respeitando o limite de 1 req/s."""
    own_session = session is None
    client = session or build_session("Mozilla/5.0 (compatible; SantosTransparenciaETL/1.0)")
    url = f"{config.base_url}/{api_path}"

    all_items: list[dict[str, Any]] = []
    offset = 0
    limit = config.page_size

    try:
        while True:
            query_params = dict(params)
            query_params["limit"] = str(limit)
            query_params["offset"] = str(offset)

            logger.info("Consultando SICONFI /%s offset=%d limit=%d", api_path, offset, limit)
            resp = client.get(url, params=query_params, timeout=config.request_timeout_sec)
            resp.raise_for_status()

            body = resp.json()
            if not isinstance(body, dict):
                raise ValueError(f"Resposta inesperada do SICONFI (esperado dict, recebido {type(body).__name__})")

            items = body.get("items") or []
            if not isinstance(items, list):
                raise ValueError(f"Campo 'items' inválido na resposta do SICONFI: {type(items).__name__}")

            all_items.extend(items)
            logger.info(
                "Recebidos %d registros de /%s (acumulado=%d, hasMore=%s)",
                len(items),
                api_path,
                len(all_items),
                body.get("hasMore"),
            )

            # Respeita o limite oficial da STN de 1 requisição por segundo.
            if config.rate_limit_sec > 0:
                sleep_fn(config.rate_limit_sec)

            if not body.get("hasMore") or not items:
                break
            offset += limit
    finally:
        if own_session:
            client.close()

    return all_items


def fetch_extrato_entregas(
    ano: int,
    config: SiconfiConfig,
    session: requests.Session | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Busca o extrato de entregas/homologações do ente para o exercício informado."""
    return _fetch_paginated(
        api_path="extrato_entregas",
        params={"id_ente": config.id_ente, "an_referencia": str(ano)},
        config=config,
        session=session,
        sleep_fn=sleep_fn,
    )


def fetch_dca_receitas(
    ano: int,
    config: SiconfiConfig,
    session: requests.Session | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Busca o Anexo I-C da DCA (Receitas Orçamentárias — FINBRA anual)."""
    return _fetch_paginated(
        api_path="dca",
        params={
            "an_exercicio": str(ano),
            "no_anexo": "DCA-Anexo I-C",
            "id_ente": config.id_ente,
        },
        config=config,
        session=session,
        sleep_fn=sleep_fn,
    )


def fetch_rreo_anexo01(
    ano: int,
    bimestre: int,
    config: SiconfiConfig,
    session: requests.Session | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Busca o Anexo 01 do RREO (Balanço Orçamentário) para o bimestre informado (1 a 6)."""
    if not 1 <= bimestre <= 6:
        raise ValueError(f"Bimestre deve estar entre 1 e 6, recebido {bimestre}")
    return _fetch_paginated(
        api_path="rreo",
        params={
            "an_exercicio": str(ano),
            "nr_periodo": str(bimestre),
            "co_tipo_demonstrativo": "RREO",
            "no_anexo": "RREO-Anexo 01",
            "id_ente": config.id_ente,
        },
        config=config,
        session=session,
        sleep_fn=sleep_fn,
    )


def fetch_msc_receitas(
    ano: int,
    mes: int,
    config: SiconfiConfig,
    tipo_matriz: str = "MSCC",
    tipo_valor: str = "period_change",
    session: requests.Session | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Busca contas de classe 6 da MSC Orçamentária para um mês/matriz/tipo de valor.

    O filtro para contas de execução da receita (``6212*`` e ``6213*``) é aplicado na
    etapa de transformação para manter a separação entre extração e regra contábil.
    """
    if not 1 <= mes <= 12:
        raise ValueError(f"Mês deve estar entre 1 e 12, recebido {mes}")
    return _fetch_paginated(
        api_path="msc_orcamentaria",
        params={
            "id_ente": config.id_ente,
            "an_referencia": str(ano),
            "me_referencia": str(mes),
            "co_tipo_matriz": tipo_matriz,
            "classe_conta": "6",
            "id_tv": tipo_valor,
        },
        config=config,
        session=session,
        sleep_fn=sleep_fn,
    )
