"""Normalização de registros provenientes dos payloads do Portal da Transparência de Santos."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any
import unicodedata

from src.config import EndpointConfig


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text != "" else None


def _parse_iso_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Trata formatos como "2026-11-03T00:00:00" ou "2026-11-03"
    return text[:10]


def _parse_numeric(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        dec = Decimal(str(value).strip())
        # O tipo NUMERIC do BigQuery suporta até 9 casas decimais.
        return format(dec.quantize(Decimal("0.000000001")).normalize(), "f")
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc


def normalize_record(
    raw: dict[str, Any],
    endpoint: EndpointConfig,
    ano_ref: int,
    mes_ref: int | None = None,
    ingested_at: datetime | None = None,
) -> dict[str, Any]:
    """Normaliza um único registro da API de acordo com o esquema do endpoint."""
    ts = ingested_at or datetime.now(timezone.utc)
    normalized: dict[str, Any] = {
        "ano_ref": ano_ref,
        "ingested_at": ts.isoformat(),
    }
    if mes_ref is not None:
        normalized["mes_ref"] = mes_ref
    elif "mes" in endpoint.string_fields and raw.get("mes") is not None:
        raw_mes = str(raw["mes"]).strip().split()[0]
        if raw_mes.isdigit():
            normalized["mes_ref"] = int(raw_mes)

    for field_name in endpoint.string_fields:
        normalized[field_name] = _clean_str(raw.get(field_name))

    for field_name in endpoint.date_fields:
        normalized[field_name] = _parse_iso_date(raw.get(field_name))

    for field_name in endpoint.numeric_fields:
        normalized[field_name] = _parse_numeric(raw.get(field_name))

    return normalized


def normalize_batch(
    records: list[dict[str, Any]],
    endpoint: EndpointConfig,
    ano_ref: int,
    mes_ref: int | None = None,
) -> list[dict[str, Any]]:
    """Normaliza todos os registros de um lote com um timestamp de ingestão compartilhado."""
    ts = datetime.now(timezone.utc)
    return [normalize_record(row, endpoint, ano_ref, mes_ref, ts) for row in records]


_CAMPOS_CHAVE_RECEITA_GPI: tuple[str, ...] = (
    "ano_ref",
    "mes_ref",
    "unidade_gestora",
    "data",
    "categoria",
    "conta_origem",
    "especie",
    "rubrica",
    "alinea",
    "fonte_recurso",
    "receita",
    "origem",
    "valor_previsto",
    "valor_realizado",
)


def _normalizar_chave_texto(valor: Any) -> Any:
    """Remove acentos, pontuação divergente ('-' vs '–' vs '?') e espaços múltiplos."""
    if not isinstance(valor, str):
        return valor
    decomposto = unicodedata.normalize("NFKD", valor.replace("\xa0", " "))
    sem_acentos = "".join(ch for ch in decomposto if not unicodedata.combining(ch)).upper()
    return re.sub(r"[^A-Z0-9]+", " ", sem_acentos).strip()


def _chave_natural_receita_gpi(row: dict[str, Any]) -> tuple[Any, ...]:
    """Gera chave natural normalizada (sem ``subalinea``) para agrupar cópias de um lançamento.

    ``subalinea`` fica de fora porque as cópias em CAIXA ALTA da API do Portal de Santos
    vêm sem subalínea; os lançamentos reais são distinguidos pela cópia em caixa mista.
    """
    return tuple(_normalizar_chave_texto(row.get(campo)) for campo in _CAMPOS_CHAVE_RECEITA_GPI)


def _linha_em_caixa_mista(row: dict[str, Any]) -> bool:
    """Retorna True se a linha traz a cópia em caixa mista (ou sem letras), que é a cópia 1:1 do lançamento real."""
    texto = "".join(str(row.get(c) or "") for c in ("especie", "rubrica", "alinea", "subalinea"))
    letras = [ch for ch in texto if ch.isalpha()]
    if not letras:
        return True
    return any(ch.islower() for ch in letras)


def deduplicar_receitas_gpi(
    records: list[dict[str, Any]],
    limiar_participacao_moda: float = 0.5,
) -> list[dict[str, Any]]:
    """Remove cópias clonadas da API do GPI preservando lançamentos legítimos repetidos.

    Diagnóstico: em jan–mai/2026 a API devolve cada lançamento 3× (1 cópia em caixa mista
    + 2 em CAIXA ALTA, sem subalínea). Regras:

    1. Toda linha em caixa mista é mantida (cada lançamento real tem exatamente 1 cópia mista,
       então pares legítimos idênticos no mesmo dia não são perdidos).
    2. Cópias CAIXA ALTA são descartadas quando a mesma chave possui cópia mista.
    3. Chaves só em CAIXA ALTA mantêm ``ceil(n / fator_clone_dia)``, em que ``fator_clone_dia``
       é a moda de cópias CAIXA ALTA por chave no dia/UG (se ela cobre ao menos
       ``limiar_participacao_moda`` das chaves; senão 1 = dia limpo, preserva tudo).
    """
    por_chave: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        por_chave[_chave_natural_receita_gpi(row)].append(row)

    # Distribuição de cópias CAIXA ALTA por (ano, data, UG).
    dist_dia: dict[tuple[Any, Any, Any], Counter] = defaultdict(Counter)
    for rows in por_chave.values():
        n_maiusc = sum(1 for r in rows if not _linha_em_caixa_mista(r))
        if n_maiusc:
            r0 = rows[0]
            dist_dia[(r0.get("ano_ref"), r0.get("data"), _normalizar_chave_texto(r0.get("unidade_gestora")))][n_maiusc] += 1

    fator_dia: dict[tuple[Any, Any, Any], int] = {}
    for chave_dia, cont in dist_dia.items():
        total = sum(cont.values())
        moda, q = sorted(cont.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        fator_dia[chave_dia] = moda if q / total >= limiar_participacao_moda else 1

    resultado: list[dict[str, Any]] = []
    for rows in por_chave.values():
        mistas = [r for r in rows if _linha_em_caixa_mista(r)]
        maiusc = [r for r in rows if not _linha_em_caixa_mista(r)]
        resultado.extend(mistas)
        if maiusc and not mistas:
            r0 = maiusc[0]
            fator = fator_dia.get((r0.get("ano_ref"), r0.get("data"), _normalizar_chave_texto(r0.get("unidade_gestora"))), 1)
            resultado.extend(maiusc[: -(-len(maiusc) // fator)])
    return resultado
