"""Paginated HTTP extractor for the Santos Transparency Portal API."""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import AppConfig, EndpointConfig

logger = logging.getLogger(__name__)


def build_session(user_agent: str) -> requests.Session:
    """Create a requests Session configured with retries and standard headers."""
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
    """Fetch all pages for a given endpoint, year, and optional month."""
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
            if endpoint.requires_month:
                if mes is None:
                    raise ValueError(f"Endpoint {endpoint.name} requires 'mes'.")
                params["mes"] = str(mes)

            logger.info(
                "Fetching %s (%s) page=%d size=%d",
                endpoint.name,
                period_label,
                pagina,
                tamanho,
            )
            resp = client.get(url, params=params, timeout=config.request_timeout_sec)
            resp.raise_for_status()

            payload = resp.json()
            if not isinstance(payload, list):
                raise ValueError(
                    f"Expected JSON list from {url}, got {type(payload).__name__}"
                )

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
