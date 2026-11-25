"""Normalização e classificação dos registros retornados pela API do SICONFI/FINBRA."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.revenue_classifier import classificar_por_codigo_nr, extrair_segmentos_dca


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text != "" else None


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Valor numérico inválido: {value!r}") from exc


def _format_numeric(value: Decimal | None) -> str | None:
    if value is None:
        return None
    # O tipo NUMERIC do BigQuery suporta até 9 casas decimais.
    return format(value.quantize(Decimal("0.000000001")).normalize(), "f")


def _parse_iso_date(value: Any) -> str | None:
    text = _clean_str(value)
    if not text:
        return None
    return text[:10]


def identificar_contas_folha_dca(records: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Identifica pares ``(coluna, cod_conta)`` que são folhas analíticas na árvore da DCA.

    Na DCA Anexo I-C, o SICONFI devolve simultaneamente contas sintéticas (pai) e
    contas analíticas (filho). Uma conta ``RO...`` é folha quando nenhuma outra conta
    da mesma coluna possui seus segmentos como prefixo estrito.
    """
    por_coluna: dict[str, dict[str, tuple[str, ...]]] = {}
    for row in records:
        coluna = _clean_str(row.get("coluna")) or ""
        cod_conta = _clean_str(row.get("cod_conta")) or ""
        segmentos = extrair_segmentos_dca(cod_conta)
        if segmentos is not None:
            por_coluna.setdefault(coluna, {})[cod_conta] = segmentos

    folhas: set[tuple[str, str]] = set()
    for coluna, mapa_seg in por_coluna.items():
        todos_segmentos = list(mapa_seg.values())
        for cod_conta, seg in mapa_seg.items():
            tamanho = len(seg)
            tem_filho = any(
                len(outro) > tamanho and outro[:tamanho] == seg
                for outro in todos_segmentos
            )
            if not tem_filho:
                folhas.add((coluna, cod_conta))

    # Na DCA Anexo I-C, a STN publica as intra-orçamentárias consolidadas em
    # "ReceitasIntraOrcamentarias" (sem abrir contas RO7/RO8). Caso não existam
    # contas filhas 7.x/8.x naquela coluna, marcamos essa linha como folha.
    for row in records:
        coluna = _clean_str(row.get("coluna")) or ""
        cod_conta = _clean_str(row.get("cod_conta")) or ""
        if cod_conta == "ReceitasIntraOrcamentarias":
            mapa_seg = por_coluna.get(coluna, {})
            tem_intra_analitica = any(seg[0] in {"7", "8"} for seg in mapa_seg.values())
            if not tem_intra_analitica:
                folhas.add((coluna, cod_conta))

    return folhas


def normalize_extrato_entregas(
    records: list[dict[str, Any]],
    ano_ref: int,
    id_ente: str,
    ingested_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Normaliza o extrato de entregas/homologações do SICONFI."""
    ts = (ingested_at or datetime.now(timezone.utc)).isoformat()
    result: list[dict[str, Any]] = []
    for raw in records:
        result.append(
            {
                "ano_ref": ano_ref,
                "id_ente": str(id_ente),
                "ingested_at": ts,
                "exercicio": _parse_int(raw.get("exercicio")),
                "cod_ibge": _clean_str(raw.get("cod_ibge")),
                "populacao": _parse_int(raw.get("populacao")),
                "instituicao": _clean_str(raw.get("instituicao")),
                "entregavel": _clean_str(raw.get("entregavel")),
                "periodo": _parse_int(raw.get("periodo")),
                "periodicidade": _clean_str(raw.get("periodicidade")),
                "status_relatorio": _clean_str(raw.get("status_relatorio")),
                "data_status": _clean_str(raw.get("data_status")),
                "forma_envio": _clean_str(raw.get("forma_envio")),
                "tipo_relatorio": _clean_str(raw.get("tipo_relatorio")),
            }
        )
    return result


def normalize_dca_receitas(
    records: list[dict[str, Any]],
    ano_ref: int,
    id_ente: str,
    ingested_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Normaliza o Anexo I-C da DCA (FINBRA anual), classificando receitas e marcando contas folha."""
    ts = (ingested_at or datetime.now(timezone.utc)).isoformat()
    folhas = identificar_contas_folha_dca(records)
    result: list[dict[str, Any]] = []

    for raw in records:
        coluna = _clean_str(raw.get("coluna")) or ""
        cod_conta = _clean_str(raw.get("cod_conta")) or ""
        conta = _clean_str(raw.get("conta")) or ""
        valor_dec = _parse_decimal(raw.get("valor")) or Decimal("0")

        eh_deducao = "DEDU" in coluna.upper()
        eh_folha = (coluna, cod_conta) in folhas

        # Classifica usando cod_conta (ex.: "RO1.1.1.2.50.0.0") e, se ausente, o texto de conta.
        classif = classificar_por_codigo_nr(cod_conta)
        if classif.codigo_nr is None:
            classif = classificar_por_codigo_nr(conta)

        if cod_conta == "ReceitasIntraOrcamentarias":
            grupo_gerencial = "Receitas Intra-Orçamentárias"
            origem_mcasp = "7.x/8.x - Receitas Intra-Orçamentárias"
            tipo_receita = "Receitas Intra-Orçamentárias (Consolidado FINBRA)"
            eh_intra = True
        else:
            grupo_gerencial = classif.grupo_gerencial
            origem_mcasp = classif.origem_mcasp
            tipo_receita = classif.tipo_receita
            eh_intra = classif.eh_intra_orcamentaria

        if eh_deducao:
            valor_bruto = Decimal("0")
            valor_deducao = abs(valor_dec)
            valor_liquido = -abs(valor_dec)
        else:
            valor_bruto = valor_dec
            valor_deducao = Decimal("0")
            valor_liquido = valor_dec

        result.append(
            {
                "ano_ref": ano_ref,
                "id_ente": str(id_ente),
                "ingested_at": ts,
                "exercicio": _parse_int(raw.get("exercicio")),
                "instituicao": _clean_str(raw.get("instituicao")),
                "uf": _clean_str(raw.get("uf")),
                "populacao": _parse_int(raw.get("populacao")),
                "anexo": _clean_str(raw.get("anexo")),
                "rotulo": _clean_str(raw.get("rotulo")),
                "coluna": coluna or None,
                "cod_conta": cod_conta or None,
                "conta": conta or None,
                "codigo_nr": classif.codigo_nr,
                "grupo_gerencial": grupo_gerencial,
                "origem_mcasp": origem_mcasp,
                "tipo_receita": tipo_receita,
                "eh_intra_orcamentaria": eh_intra,
                "eh_deducao": eh_deducao,
                "eh_conta_folha": eh_folha,
                "valor": _format_numeric(valor_dec),
                "valor_bruto": _format_numeric(valor_bruto),
                "valor_deducao": _format_numeric(valor_deducao),
                "valor_liquido": _format_numeric(valor_liquido),
            }
        )

    return result


def normalize_msc_receitas(
    records: list[dict[str, Any]],
    ano_ref: int,
    mes_ref: int,
    tipo_matriz: str,
    tipo_valor: str,
    id_ente: str,
    ingested_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Filtra e normaliza as contas de receita realizada (6212*) e deduções (6213*) da MSC Orçamentária.

    Convenção contábil PCASP:
    - ``6212*`` (Receita Realizada Bruta): natureza credora (``C`` soma, ``D`` estorna).
    - ``6213*`` (Deduções da Receita): natureza devedora (``D`` soma na dedução, ``C`` estorna).
    - ``valor_liquido`` = ``valor_bruto - valor_deducao``.
    """
    ts = (ingested_at or datetime.now(timezone.utc)).isoformat()
    result: list[dict[str, Any]] = []

    for raw in records:
        conta_contabil = _clean_str(raw.get("conta_contabil")) or ""
        if not (conta_contabil.startswith("6212") or conta_contabil.startswith("6213")):
            continue

        natureza_receita = _clean_str(raw.get("natureza_receita"))
        natureza_conta = (_clean_str(raw.get("natureza_conta")) or "").upper()
        valor_dec = _parse_decimal(raw.get("valor")) or Decimal("0")

        eh_deducao = conta_contabil.startswith("6213")
        if eh_deducao:
            sinal_deducao = Decimal("1") if natureza_conta == "D" else Decimal("-1")
            valor_bruto = Decimal("0")
            valor_deducao = valor_dec * sinal_deducao
            valor_liquido = -valor_deducao
        else:
            sinal_bruto = Decimal("1") if natureza_conta == "C" else Decimal("-1")
            valor_bruto = valor_dec * sinal_bruto
            valor_deducao = Decimal("0")
            valor_liquido = valor_bruto

        classif = classificar_por_codigo_nr(natureza_receita)

        result.append(
            {
                "ano_ref": ano_ref,
                "mes_ref": mes_ref,
                "tipo_matriz": tipo_matriz,
                "tipo_valor": tipo_valor,
                "id_ente": str(id_ente),
                "ingested_at": ts,
                "data_referencia": _parse_iso_date(raw.get("data_referencia")),
                "classe_conta": _parse_int(raw.get("classe_conta")),
                "conta_contabil": conta_contabil,
                "poder_orgao": _clean_str(raw.get("poder_orgao")),
                "ano_fonte_recursos": _clean_str(raw.get("ano_fonte_recursos")),
                "fonte_recursos": _clean_str(raw.get("fonte_recursos")),
                "complemento_fonte": _clean_str(raw.get("complemento_fonte")),
                "natureza_receita": natureza_receita,
                "natureza_conta": natureza_conta or None,
                "codigo_nr": classif.codigo_nr,
                "grupo_gerencial": classif.grupo_gerencial,
                "origem_mcasp": classif.origem_mcasp,
                "tipo_receita": classif.tipo_receita,
                "eh_intra_orcamentaria": classif.eh_intra_orcamentaria,
                "eh_deducao": eh_deducao,
                "valor_original": _format_numeric(valor_dec),
                "valor_bruto": _format_numeric(valor_bruto),
                "valor_deducao": _format_numeric(valor_deducao),
                "valor_liquido": _format_numeric(valor_liquido),
            }
        )

    return result


def normalize_rreo_anexo01(
    records: list[dict[str, Any]],
    ano_ref: int,
    bimestre_ref: int,
    id_ente: str,
    ingested_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Normaliza o Anexo 01 do RREO (Balanço Orçamentário bimestral)."""
    ts = (ingested_at or datetime.now(timezone.utc)).isoformat()
    result: list[dict[str, Any]] = []

    for raw in records:
        valor_dec = _parse_decimal(raw.get("valor"))
        result.append(
            {
                "ano_ref": ano_ref,
                "bimestre_ref": bimestre_ref,
                "id_ente": str(id_ente),
                "ingested_at": ts,
                "exercicio": _parse_int(raw.get("exercicio")),
                "demonstrativo": _clean_str(raw.get("demonstrativo")),
                "periodo": _parse_int(raw.get("periodo")),
                "periodicidade": _clean_str(raw.get("periodicidade")),
                "instituicao": _clean_str(raw.get("instituicao")),
                "uf": _clean_str(raw.get("uf")),
                "populacao": _parse_int(raw.get("populacao")),
                "anexo": _clean_str(raw.get("anexo")),
                "rotulo": _clean_str(raw.get("rotulo")),
                "coluna": _clean_str(raw.get("coluna")),
                "cod_conta": _clean_str(raw.get("cod_conta")),
                "conta": _clean_str(raw.get("conta")),
                "valor": _format_numeric(valor_dec),
            }
        )

    return result
