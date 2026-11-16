"""CLI and Cloud Run Job entrypoint for Santos Transparency Portal ETL."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
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
    """Return (year, month) for the previous calendar month in Sao Paulo time."""
    ref = now or datetime.now(ZoneInfo("America/Sao_Paulo"))
    if ref.month == 1:
        return ref.year - 1, 12
    return ref.year, ref.month - 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Santos Transparency Portal data and load into BigQuery."
    )
    parser.add_argument(
        "--year",
        type=int,
        default=int(os.getenv("TARGET_YEAR")) if os.getenv("TARGET_YEAR") else None,
        help="Target exercise year (defaults to previous month's year).",
    )
    parser.add_argument(
        "--month",
        type=int,
        default=int(os.getenv("TARGET_MONTH")) if os.getenv("TARGET_MONTH") else None,
        help="Target exercise month 1-12 (defaults to previous month).",
    )
    parser.add_argument(
        "--endpoints",
        nargs="+",
        choices=list(ENDPOINTS.keys()),
        default=list(ENDPOINTS.keys()),
        help="Subset of endpoints to process (default: all 4).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and normalize without writing to BigQuery.",
    )
    return parser.parse_args(argv)


def run_pipeline(
    year: int,
    month: int,
    endpoint_names: list[str],
    config: AppConfig,
    dry_run: bool = False,
) -> dict[str, int]:
    """Run extraction, normalization, and BigQuery load for all requested endpoints."""
    if not 1 <= month <= 12:
        raise ValueError(f"Month must be between 1 and 12, got {month}")

    if not dry_run and not config.gcp_project_id:
        raise ValueError("GCP_PROJECT_ID environment variable is required unless --dry-run is set.")

    bq_client = None if dry_run else bigquery.Client(project=config.gcp_project_id)
    results: dict[str, int] = {}

    with build_session(config.user_agent) as session:
        for name in endpoint_names:
            endpoint = ENDPOINTS[name]
            target_month = month if endpoint.requires_month else None
            raw_rows = fetch_period(endpoint, year, target_month, config, session=session)
            normalized_rows = normalize_batch(raw_rows, endpoint, year, target_month)

            if dry_run:
                period_label = f"{year:04d}-{month:02d}" if endpoint.requires_month else f"{year:04d}"
                logger.info(
                    "[DRY RUN] %s (%s): %d normalized rows",
                    name,
                    period_label,
                    len(normalized_rows),
                )
                results[name] = len(normalized_rows)
            else:
                assert bq_client is not None
                loaded = replace_period_data(
                    bq_client, config, endpoint, normalized_rows, year, target_month
                )
                results[name] = loaded

    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    default_year, default_month = resolve_previous_month()
    year = args.year if args.year is not None else default_year
    month = args.month if args.month is not None else default_month

    config = AppConfig()
    logger.info(
        "Starting Santos Transparency ETL for %04d-%02d (endpoints=%s, dry_run=%s)",
        year,
        month,
        ",".join(args.endpoints),
        args.dry_run,
    )

    try:
        summary = run_pipeline(year, month, args.endpoints, config, dry_run=args.dry_run)
        logger.info("ETL completed successfully: %s", summary)
        return 0
    except Exception:
        logger.exception("ETL failed for %04d-%02d", year, month)
        return 1


if __name__ == "__main__":
    sys.exit(main())
