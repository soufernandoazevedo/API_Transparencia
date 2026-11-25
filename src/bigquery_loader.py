"""Carregador idempotente no BigQuery para dados do Portal da Transparência de Santos."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from google.cloud import bigquery

from src.config import AppConfig, EndpointConfig
from src.siconfi_config import SiconfiDatasetConfig

logger = logging.getLogger(__name__)


def build_bq_schema(endpoint: EndpointConfig) -> list[bigquery.SchemaField]:
    """Gera o esquema da tabela no BigQuery a partir de um EndpointConfig."""
    fields: list[bigquery.SchemaField] = [
        bigquery.SchemaField("ano_ref", "INT64", mode="REQUIRED"),
    ]
    if endpoint.requires_month or "mes" in endpoint.string_fields:
        fields.append(bigquery.SchemaField("mes_ref", "INT64", mode="NULLABLE" if not endpoint.requires_month else "REQUIRED"))
    fields.append(bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"))

    for name in endpoint.string_fields:
        fields.append(bigquery.SchemaField(name, "STRING", mode="NULLABLE"))
    for name in endpoint.date_fields:
        fields.append(bigquery.SchemaField(name, "DATE", mode="NULLABLE"))
    for name in endpoint.numeric_fields:
        fields.append(bigquery.SchemaField(name, "NUMERIC", mode="NULLABLE"))
    return fields


def ensure_dataset_and_table(
    client: bigquery.Client,
    config: AppConfig,
    endpoint: EndpointConfig,
) -> bigquery.TableReference:
    """Cria o dataset e a tabela de destino particionada/clusterizada caso ainda não existam."""
    dataset_ref = bigquery.DatasetReference(config.gcp_project_id, config.bq_dataset)
    dataset = bigquery.Dataset(dataset_ref)
    dataset.location = config.bq_location
    client.create_dataset(dataset, exists_ok=True)

    table_ref = dataset_ref.table(endpoint.table_name)
    table = bigquery.Table(table_ref, schema=build_bq_schema(endpoint))
    if "data" in endpoint.date_fields:
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.MONTH,
            field="data",
        )
    table.clustering_fields = list(endpoint.clustering_fields)
    client.create_table(table, exists_ok=True)
    return table_ref


def _period_filter(
    endpoint: EndpointConfig,
    ano_ref: int,
    mes_ref: int | None,
) -> tuple[str, list[bigquery.ScalarQueryParameter], str]:
    params = [bigquery.ScalarQueryParameter("ano_ref", "INT64", ano_ref)]
    if endpoint.requires_month and mes_ref is not None:
        params.append(bigquery.ScalarQueryParameter("mes_ref", "INT64", mes_ref))
        return "ano_ref = @ano_ref AND mes_ref = @mes_ref", params, f"{ano_ref:04d}-{mes_ref:02d}"
    return "ano_ref = @ano_ref", params, f"{ano_ref:04d}"


def replace_period_data(
    client: bigquery.Client,
    config: AppConfig,
    endpoint: EndpointConfig,
    rows: list[dict[str, Any]],
    ano_ref: int,
    mes_ref: int | None = None,
) -> int:
    """Substitui atomicamente todas as linhas do período alvo por meio de uma tabela temporária de staging."""
    target_ref = ensure_dataset_and_table(client, config, endpoint)
    target_fqn = f"`{target_ref.project}.{target_ref.dataset_id}.{target_ref.table_id}`"
    where_clause, query_params, period_label = _period_filter(endpoint, ano_ref, mes_ref)

    if not rows:
        delete_sql = f"DELETE FROM {target_fqn} WHERE {where_clause}"
        client.query(
            delete_sql,
            job_config=bigquery.QueryJobConfig(query_parameters=query_params),
        ).result()
        logger.info("No rows returned; cleared %s for %s", endpoint.table_name, period_label)
        return 0

    schema = build_bq_schema(endpoint)
    staging_name = f"_stg_{endpoint.table_name}_{period_label.replace('-', '_')}_{uuid.uuid4().hex[:8]}"
    staging_ref = bigquery.DatasetReference(config.gcp_project_id, config.bq_dataset).table(staging_name)
    staging_fqn = f"`{staging_ref.project}.{staging_ref.dataset_id}.{staging_ref.table_id}`"

    load_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    try:
        logger.info("Loading %d rows into staging table %s", len(rows), staging_name)
        load_job = client.load_table_from_json(rows, staging_ref, job_config=load_config)
        load_job.result()

        swap_sql = f"""
        BEGIN TRANSACTION;
          DELETE FROM {target_fqn}
          WHERE {where_clause};

          INSERT INTO {target_fqn}
          SELECT * FROM {staging_fqn};
        COMMIT TRANSACTION;
        """
        client.query(
            swap_sql,
            job_config=bigquery.QueryJobConfig(query_parameters=query_params),
        ).result()
        logger.info(
            "Replaced %s for %s with %d rows",
            endpoint.table_name,
            period_label,
            len(rows),
        )
    finally:
        client.delete_table(staging_ref, not_found_ok=True)

    return len(rows)


def build_siconfi_bq_schema(dataset: SiconfiDatasetConfig) -> list[bigquery.SchemaField]:
    """Gera o esquema da tabela no BigQuery a partir de um SiconfiDatasetConfig."""
    return [
        bigquery.SchemaField(name, field_type, mode=mode)
        for name, field_type, mode in dataset.schema_fields
    ]


def ensure_siconfi_table(
    client: bigquery.Client,
    config: AppConfig,
    dataset_cfg: SiconfiDatasetConfig,
) -> bigquery.TableReference:
    """Cria o dataset e a tabela clusterizada do SICONFI caso ainda não existam."""
    dataset_ref = bigquery.DatasetReference(config.gcp_project_id, config.bq_dataset)
    bq_dataset = bigquery.Dataset(dataset_ref)
    bq_dataset.location = config.bq_location
    client.create_dataset(bq_dataset, exists_ok=True)

    table_ref = dataset_ref.table(dataset_cfg.table_name)
    table = bigquery.Table(table_ref, schema=build_siconfi_bq_schema(dataset_cfg))
    table.clustering_fields = list(dataset_cfg.clustering_fields)
    client.create_table(table, exists_ok=True)
    return table_ref


def replace_siconfi_data(
    client: bigquery.Client,
    config: AppConfig,
    dataset_cfg: SiconfiDatasetConfig,
    rows: list[dict[str, Any]],
    key_values: dict[str, Any],
) -> int:
    """Substitui atomicamente os registros do SICONFI para as chaves informadas via staging."""
    target_ref = ensure_siconfi_table(client, config, dataset_cfg)
    target_fqn = f"`{target_ref.project}.{target_ref.dataset_id}.{target_ref.table_id}`"

    schema_types = {name: field_type for name, field_type, _ in dataset_cfg.schema_fields}
    clauses: list[str] = []
    query_params: list[bigquery.ScalarQueryParameter] = []
    for key_field in dataset_cfg.delete_key_fields:
        if key_field not in key_values:
            raise ValueError(f"Chave obrigatória '{key_field}' ausente para {dataset_cfg.table_name}")
        clauses.append(f"{key_field} = @{key_field}")
        query_params.append(
            bigquery.ScalarQueryParameter(key_field, schema_types[key_field], key_values[key_field])
        )
    where_clause = " AND ".join(clauses)
    label = "_".join(str(key_values[k]) for k in dataset_cfg.delete_key_fields)

    if not rows:
        delete_sql = f"DELETE FROM {target_fqn} WHERE {where_clause}"
        client.query(
            delete_sql,
            job_config=bigquery.QueryJobConfig(query_parameters=query_params),
        ).result()
        logger.info("Sem linhas retornadas; limpeza executada em %s para %s", dataset_cfg.table_name, label)
        return 0

    schema = build_siconfi_bq_schema(dataset_cfg)
    staging_name = f"_stg_{dataset_cfg.table_name}_{uuid.uuid4().hex[:8]}"
    staging_ref = bigquery.DatasetReference(config.gcp_project_id, config.bq_dataset).table(staging_name)
    staging_fqn = f"`{staging_ref.project}.{staging_ref.dataset_id}.{staging_ref.table_id}`"

    load_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    try:
        logger.info("Carregando %d linhas na staging %s", len(rows), staging_name)
        load_job = client.load_table_from_json(rows, staging_ref, job_config=load_config)
        load_job.result()

        swap_sql = f"""
        BEGIN TRANSACTION;
          DELETE FROM {target_fqn}
          WHERE {where_clause};

          INSERT INTO {target_fqn}
          SELECT * FROM {staging_fqn};
        COMMIT TRANSACTION;
        """
        client.query(
            swap_sql,
            job_config=bigquery.QueryJobConfig(query_parameters=query_params),
        ).result()
        logger.info("Substituído %s (%s) com %d linhas", dataset_cfg.table_name, label, len(rows))
    finally:
        client.delete_table(staging_ref, not_found_ok=True)

    return len(rows)
