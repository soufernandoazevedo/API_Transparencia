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

from src.bigquery_loader import replace_period_data, replace_siconfi_data
from src.config import ENDPOINTS, AppConfig
from src.extractor import build_session, fetch_period
from src.siconfi_config import SICONFI_DATASETS, SiconfiConfig
from src.siconfi_extractor import (
    fetch_dca_receitas,
    fetch_extrato_entregas,
    fetch_msc_receitas,
    fetch_rreo_anexo01,
)
from src.siconfi_transformer import (
    normalize_dca_receitas,
    normalize_extrato_entregas,
    normalize_msc_receitas,
    normalize_rreo_anexo01,
)
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
        description="Extrai dados do Portal da Transparência de Santos (GPI) e do SICONFI/FINBRA e carrega no BigQuery."
    )
    parser.add_argument(
        "--source",
        choices=("gpi", "siconfi", "all"),
        default=os.getenv("ETL_SOURCE", "gpi"),
        help="Fonte de dados a processar: gpi (Portal Santos), siconfi (STN/FINBRA) ou all (ambas). Padrão: gpi.",
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
        "--bimestre",
        type=int,
        default=int(os.getenv("TARGET_BIMESTRE")) if os.getenv("TARGET_BIMESTRE") else None,
        help="Bimestre específico (1 a 6) para o RREO do SICONFI (quando omitido, detecta automaticamente pelo extrato).",
    )
    parser.add_argument(
        "--endpoints",
        nargs="+",
        choices=list(ENDPOINTS.keys()),
        default=list(ENDPOINTS.keys()),
        help="Subconjunto de endpoints do GPI (Santos) a processar (padrão: todos os 6).",
    )
    parser.add_argument(
        "--siconfi-datasets",
        nargs="+",
        choices=list(SICONFI_DATASETS.keys()),
        default=list(SICONFI_DATASETS.keys()),
        help="Subconjunto de datasets do SICONFI a processar (padrão: todos os 4).",
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
    extra_suffix: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{year:04d}_{month:02d}" if month is not None else f"{year:04d}"
    if extra_suffix:
        suffix = f"{suffix}_{extra_suffix}"
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
    """Executa extração, normalização, exportação local opcional em JSON e carga no BigQuery (GPI Santos)."""
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


def _detectar_periodos_entregues(
    extrato_rows: list[dict],
) -> tuple[bool, list[int], list[int]]:
    """Extrai do extrato SICONFI se há DCA entregue e quais meses MSC / bimestres RREO existem."""
    tem_dca = False
    meses_msc: set[int] = set()
    bimestres_rreo: set[int] = set()

    for row in extrato_rows:
        entregavel = (row.get("entregavel") or "").upper()
        periodo = row.get("periodo")
        if not isinstance(periodo, int):
            continue
        if "DCA" in entregavel or "BALANÇO ANUAL" in entregavel:
            tem_dca = True
        elif "MSC AGREGADA" in entregavel and 1 <= periodo <= 12:
            meses_msc.add(periodo)
        elif "EXECUÇÃO ORÇAMENTÁRIA" in entregavel and 1 <= periodo <= 6:
            bimestres_rreo.add(periodo)

    return tem_dca, sorted(meses_msc), sorted(bimestres_rreo)


def run_siconfi_pipeline(
    years: list[int],
    month: int | None,
    bimestre: int | None,
    dataset_names: list[str],
    app_config: AppConfig,
    siconfi_config: SiconfiConfig,
    dry_run: bool = False,
    local_only: bool = False,
    output_dir: Path | None = None,
) -> dict[str, int]:
    """Executa extração, normalização, exportação local opcional e carga no BigQuery para o SICONFI/FINBRA."""
    if month is not None and not 1 <= month <= 12:
        raise ValueError(f"Mês deve estar entre 1 e 12, recebido {month}")
    if bimestre is not None and not 1 <= bimestre <= 6:
        raise ValueError(f"Bimestre deve estar entre 1 e 6, recebido {bimestre}")

    skip_bq = dry_run or local_only
    if not skip_bq and not app_config.gcp_project_id:
        raise ValueError("GCP_PROJECT_ID é obrigatório salvo quando --dry-run ou --local-only está ativo.")

    bq_client = None if skip_bq else bigquery.Client(project=app_config.gcp_project_id)
    results: dict[str, int] = {}

    def _persistir(
        dataset_name: str,
        rows: list[dict],
        key_values: dict,
        year: int,
        sub_month: int | None = None,
        extra_suffix: str | None = None,
    ) -> int:
        dataset_cfg = SICONFI_DATASETS[dataset_name]
        if output_dir is not None:
            _write_local_json(output_dir, dataset_cfg.table_name, year, sub_month, rows, extra_suffix=extra_suffix)
        if skip_bq:
            logger.info(
                "[%s] %s (%s): %d linhas normalizadas",
                "DRY RUN" if dry_run else "LOCAL ONLY",
                dataset_cfg.table_name,
                key_values,
                len(rows),
            )
            return len(rows)
        assert bq_client is not None
        return replace_siconfi_data(bq_client, app_config, dataset_cfg, rows, key_values)

    with build_session(app_config.user_agent) as session:
        for year in years:
            # Sempre consulta o extrato quando necessário para descobrir períodos já entregues sem desperdiçar chamadas.
            precisa_extrato = (
                "extrato_entregas" in dataset_names
                or ("dca_receitas" in dataset_names)
                or ("msc_receitas" in dataset_names and month is None)
                or ("rreo_anexo01" in dataset_names and bimestre is None)
            )
            extrato_norm: list[dict] = []
            tem_dca, meses_entregues, bimestres_entregues = True, list(range(1, 13)), list(range(1, 7))
            if precisa_extrato:
                raw_extrato = fetch_extrato_entregas(year, siconfi_config, session=session)
                extrato_norm = normalize_extrato_entregas(raw_extrato, year, siconfi_config.id_ente)
                tem_dca, meses_entregues, bimestres_entregues = _detectar_periodos_entregues(extrato_norm)
                if "extrato_entregas" in dataset_names:
                    results[f"siconfi_extrato_entregas:{year:04d}"] = _persistir(
                        "extrato_entregas",
                        extrato_norm,
                        {"ano_ref": year, "id_ente": siconfi_config.id_ente},
                        year,
                    )

            if "dca_receitas" in dataset_names:
                if tem_dca:
                    raw_dca = fetch_dca_receitas(year, siconfi_config, session=session)
                    dca_norm = normalize_dca_receitas(raw_dca, year, siconfi_config.id_ente)
                    results[f"siconfi_dca_receitas:{year:04d}"] = _persistir(
                        "dca_receitas",
                        dca_norm,
                        {"ano_ref": year, "id_ente": siconfi_config.id_ente},
                        year,
                    )
                else:
                    logger.info("DCA %04d ainda não entregue no SICONFI para ente %s; pulando.", year, siconfi_config.id_ente)
                    results[f"siconfi_dca_receitas:{year:04d}"] = 0

            if "rreo_anexo01" in dataset_names:
                bimestres_alvo = [bimestre] if bimestre is not None else bimestres_entregues
                for b in bimestres_alvo:
                    raw_rreo = fetch_rreo_anexo01(year, b, siconfi_config, session=session)
                    rreo_norm = normalize_rreo_anexo01(raw_rreo, year, b, siconfi_config.id_ente)
                    results[f"siconfi_rreo_anexo01:{year:04d}-B{b}"] = _persistir(
                        "rreo_anexo01",
                        rreo_norm,
                        {"ano_ref": year, "bimestre_ref": b, "id_ente": siconfi_config.id_ente},
                        year,
                        extra_suffix=f"B{b}",
                    )

            if "msc_receitas" in dataset_names:
                meses_alvo = [month] if month is not None else meses_entregues
                for m in meses_alvo:
                    for tipo_valor in ("period_change", "ending_balance"):
                        raw_msc = fetch_msc_receitas(
                            year,
                            m,
                            siconfi_config,
                            tipo_matriz="MSCC",
                            tipo_valor=tipo_valor,
                            session=session,
                        )
                        msc_norm = normalize_msc_receitas(
                            raw_msc,
                            ano_ref=year,
                            mes_ref=m,
                            tipo_matriz="MSCC",
                            tipo_valor=tipo_valor,
                            id_ente=siconfi_config.id_ente,
                        )
                        results[f"siconfi_msc_receitas:{year:04d}-{m:02d}:{tipo_valor}"] = _persistir(
                            "msc_receitas",
                            msc_norm,
                            {
                                "ano_ref": year,
                                "mes_ref": m,
                                "tipo_matriz": "MSCC",
                                "tipo_valor": tipo_valor,
                                "id_ente": siconfi_config.id_ente,
                            },
                            year,
                            sub_month=m,
                            extra_suffix=f"MSCC_{tipo_valor}",
                        )

    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.years and args.year is not None:
        logger.error("Use --year ou --years, não ambos simultaneamente.")
        return 2

    if args.years:
        years = args.years
        month = None
    else:
        default_year, default_month = resolve_previous_month()
        years = [args.year if args.year is not None else default_year]
        month = args.month if args.month is not None else default_month

    app_config = AppConfig()
    siconfi_config = SiconfiConfig()
    logger.info(
        "Iniciando ETL Santos (source=%s, years=%s, month=%s, bimestre=%s, dry_run=%s, local_only=%s)",
        args.source,
        years,
        month,
        args.bimestre,
        args.dry_run,
        args.local_only,
    )

    try:
        summary: dict[str, int] = {}
        if args.source in ("gpi", "all"):
            summary.update(
                run_pipeline(
                    years=years,
                    month=month,
                    endpoint_names=args.endpoints,
                    config=app_config,
                    dry_run=args.dry_run,
                    local_only=args.local_only,
                    output_dir=args.output_dir,
                )
            )
        if args.source in ("siconfi", "all"):
            summary.update(
                run_siconfi_pipeline(
                    years=years,
                    month=month,
                    bimestre=args.bimestre,
                    dataset_names=args.siconfi_datasets,
                    app_config=app_config,
                    siconfi_config=siconfi_config,
                    dry_run=args.dry_run,
                    local_only=args.local_only,
                    output_dir=args.output_dir,
                )
            )
        logger.info("ETL concluído com sucesso: %s", summary)
        return 0
    except Exception:
        logger.exception("Falha no ETL para source=%s years=%s month=%s", args.source, years, month)
        return 1


if __name__ == "__main__":
    sys.exit(main())
