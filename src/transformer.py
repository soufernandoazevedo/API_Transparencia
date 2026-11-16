"""Record normalization for Santos Transparency Portal payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

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
    # Handles formats like "2026-11-03T00:00:00" or "2026-11-03"
    return text[:10]


def _parse_numeric(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        dec = Decimal(str(value).strip())
        # BigQuery NUMERIC supports up to 9 fractional digits.
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
    """Normalize a single API record according to the endpoint schema."""
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
    """Normalize all records in a batch with a shared ingestion timestamp."""
    ts = datetime.now(timezone.utc)
    return [normalize_record(row, endpoint, ano_ref, mes_ref, ts) for row in records]
