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
    """Gera chave natural normalizada para identificar cópias redundantes em receitas do GPI.

    Omite ``subalinea`` da chave porque cargas duplicadas da API do Portal de Santos
    emitem cópias idênticas alternando ``subalinea=None`` e ``subalinea`` preenchida,
    além de variações de caixa, espaços duplos e hífens ('-' vs '–').
    """
    return tuple(_normalizar_chave_texto(row.get(campo)) for campo in _CAMPOS_CHAVE_RECEITA_GPI)


def deduplicar_receitas_gpi(
    records: list[dict[str, Any]],
    limiar_taxa_duplicidade: float = 0.20,
) -> list[dict[str, Any]]:
    """Remove cópias redundantes de dias afetados por carga duplicada na API do GPI.

    Para cada grupo ``(ano_ref, data, unidade_gestora)``, calcula a proporção de chaves
    naturais repetidas (case-insensitive). Quando a taxa atinge ``limiar_taxa_duplicidade``
    (indicando duplicação/triplicação de carga na API), mantém apenas a linha mais completa
    (priorizando a cópia com ``subalinea`` preenchida); em dias limpos, preserva tudo.
    """
    por_dia_ug: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        chave_dia = (row.get("ano_ref"), row.get("data"), row.get("unidade_gestora"))
        por_dia_ug[chave_dia].append(row)

    resultado: list[dict[str, Any]] = []
    for rows in por_dia_ug.values():
        contagem = Counter(_chave_natural_receita_gpi(r) for r in rows)
        total_chaves = len(contagem)
        chaves_repetidas = sum(1 for qtd in contagem.values() if qtd > 1)
        taxa = chaves_repetidas / total_chaves if total_chaves else 0.0

        if taxa >= limiar_taxa_duplicidade:
            escolhidas: dict[tuple[Any, ...], dict[str, Any]] = {}
            for r in rows:
                ch = _chave_natural_receita_gpi(r)
                anterior = escolhidas.get(ch)
                if anterior is None or (not anterior.get("subalinea") and r.get("subalinea")):
                    escolhidas[ch] = r
            resultado.extend(escolhidas.values())
        else:
            resultado.extend(rows)

    return resultado
