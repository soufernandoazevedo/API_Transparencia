"""Extrator HTTP paginado para a API do Portal da Transparência de Santos."""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import AppConfig, EndpointConfig

logger = logging.getLogger(__name__)


def parse_api_payload(raw_text: str) -> list[dict[str, Any]]:
    """Faz o parsing direto de JSON ou desembrulha respostas XML <string>...</string> do ASP.NET ASMX."""
    text = raw_text.lstrip("\ufeff \t\r\n")
    if text.startswith("<"):
        root = ET.fromstring(text)
        inner = root.text or "[]"
        payload = json.loads(inner)
    else:
        payload = json.loads(text)

    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list from API, got {type(payload).__name__}")
    return payload


def build_session(user_agent: str) -> requests.Session:
    """Cria uma sessão requests configurada com retentativas e cabeçalhos padrão."""
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
    )
    return session


def fetch_period(
    endpoint: EndpointConfig,
    ano: int,
    mes: int | None,
    config: AppConfig,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Busca todas as páginas para um endpoint, ano e mês opcional informados."""
    own_session = session is None
    client = session or build_session(config.user_agent)
    url = f"{config.portal_base_url}/{endpoint.api_method}"

    all_rows: list[dict[str, Any]] = []
    pagina = 1
    tamanho = config.page_size
    period_label = f"{ano:04d}-{mes:02d}" if endpoint.requires_month and mes is not None else f"{ano:04d}"

    try:
        while True:
            params: dict[str, str] = {
                "ano": str(ano),
                "pagina": str(pagina),
                "tamanho": str(tamanho),
            }
            for key, value in endpoint.fixed_params:
                params[key] = value
            if endpoint.requires_month:
                params["mes"] = str(mes) if mes is not None else "00"

            logger.info(
                "Fetching %s (%s) page=%d size=%d",
                endpoint.name,
                period_label,
                pagina,
                tamanho,
            )
            resp = client.get(url, params=params, timeout=config.request_timeout_sec)
            resp.raise_for_status()

            payload = parse_api_payload(resp.text)

            all_rows.extend(payload)
            logger.info(
                "Fetched %d rows on page %d for %s (total=%d)",
                len(payload),
                pagina,
                endpoint.name,
                len(all_rows),
            )

            if len(payload) < tamanho:
                break
            pagina += 1
    finally:
        if own_session:
            client.close()

    return all_rows
