"""Ponto de entrada de CLI e Cloud Run Job para o ETL do Portal da Transparência de Santos."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from src.bigquery_loader import replace_period_data
from src.config import ENDPOINTS, AppConfig
from src.extractor import build_session, fetch_period
from src.transformer import normalize_batch

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def resolve_previous_month(now: datetime | None = None) -> tuple[int, int]:
    """Retorna (ano, mês) correspondentes ao mês civil anterior no fuso horário de São Paulo."""
    ref = now or datetime.now(ZoneInfo("America/Sao_Paulo"))
    if ref.month == 1:
        return ref.year - 1, 12
    return ref.year, ref.month - 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extrai dados do Portal da Transparência de Santos e carrega no BigQuery."
    )
    parser.add_argument(
        "--year",
        type=int,
        default=int(os.getenv("TARGET_YEAR")) if os.getenv("TARGET_YEAR") else None,
        help="Ano de exercício alvo (padrão: ano do mês anterior).",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=None,
        help="Um ou mais anos completos para carga histórica (ex.: --years 2024 2025 2026).",
    )
    parser.add_argument(
        "--month",
        type=int,
        default=int(os.getenv("TARGET_MONTH")) if os.getenv("TARGET_MONTH") else None,
        help="Mês de exercício alvo de 1 a 12 (padrão: mês anterior; omitido quando --years é usado).",
    )
    parser.add_argument(
        "--endpoints",
        nargs="+",
        choices=list(ENDPOINTS.keys()),
        default=list(ENDPOINTS.keys()),
        help="Subconjunto de endpoints a processar (padrão: todos os 6).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Diretório opcional para salvar localmente os arquivos JSON normalizados.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Ignora a carga no BigQuery (útil com --output-dir para downloads locais).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extrai e normaliza sem gravar no BigQuery.",
    )
    return parser.parse_args(argv)


def _write_local_json(
    output_dir: Path,
    endpoint_name: str,
    year: int,
    month: int | None,
    rows: list[dict],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{year:04d}_{month:02d}" if month is not None else f"{year:04d}"
    out_path = output_dir / f"{endpoint_name}_{suffix}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    logger.info("Saved %d rows to %s", len(rows), out_path)
    return out_path


def run_pipeline(
    years: list[int],
    month: int | None,
    endpoint_names: list[str],
    config: AppConfig,
    dry_run: bool = False,
    local_only: bool = False,
    output_dir: Path | None = None,
) -> dict[str, int]:
    """Executa extração, normalização, exportação local opcional em JSON e carga no BigQuery."""
    if month is not None and not 1 <= month <= 12:
        raise ValueError(f"Month must be between 1 and 12, got {month}")

    skip_bq = dry_run or local_only
    if not skip_bq and not config.gcp_project_id:
        raise ValueError("GCP_PROJECT_ID environment variable is required unless --dry-run or --local-only is set.")

    bq_client = None if skip_bq else bigquery.Client(project=config.gcp_project_id)
    results: dict[str, int] = {}

    with build_session(config.user_agent) as session:
        for year in years:
            for name in endpoint_names:
                endpoint = ENDPOINTS[name]
                target_month = month if endpoint.requires_month else None
                period_label = (
                    f"{year:04d}-{target_month:02d}"
                    if target_month is not None
                    else f"{year:04d}"
                )

                raw_rows = fetch_period(endpoint, year, target_month, config, session=session)
                normalized_rows = normalize_batch(raw_rows, endpoint, year, target_month)

                if output_dir is not None:
                    _write_local_json(output_dir, name, year, target_month, normalized_rows)

                key = f"{name}:{period_label}"
                if skip_bq:
                    logger.info(
                        "[%s] %s (%s): %d normalized rows",
                        "DRY RUN" if dry_run else "LOCAL ONLY",
                        name,
                        period_label,
                        len(normalized_rows),
                    )
                    results[key] = len(normalized_rows)
                else:
                    assert bq_client is not None
                    loaded = replace_period_data(
                        bq_client, config, endpoint, normalized_rows, year, target_month
                    )
                    results[key] = loaded

    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.years and args.year is not None:
        logger.error("Use either --year or --years, not both.")
        return 2

    if args.years:
        years = args.years
        month = None
    else:
        default_year, default_month = resolve_previous_month()
        years = [args.year if args.year is not None else default_year]
        month = args.month if args.month is not None else default_month

    config = AppConfig()
    logger.info(
        "Starting Santos Transparency ETL for years=%s month=%s (endpoints=%s, dry_run=%s, local_only=%s)",
        years,
        month,
        ",".join(args.endpoints),
        args.dry_run,
        args.local_only,
    )

    try:
        summary = run_pipeline(
            years=years,
            month=month,
            endpoint_names=args.endpoints,
            config=config,
            dry_run=args.dry_run,
            local_only=args.local_only,
            output_dir=args.output_dir,
        )
        logger.info("ETL completed successfully: %s", summary)
        return 0
    except Exception:
        logger.exception("ETL failed for years=%s month=%s", years, month)
        return 1


if __name__ == "__main__":
    sys.exit(main())
