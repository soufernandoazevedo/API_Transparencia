"""Classificação padronizada de receitas públicas (Ementário STN/MCASP) para Santos-SP.

Permite classificar receitas tanto pelo código numérico de Natureza da Receita
(usado no SICONFI — DCA, MSC Orçamentária e RREO) quanto pela hierarquia textual
do Portal da Transparência de Santos (GPI - E&L), produzindo sempre as mesmas
dimensões analíticas para conciliação no BigQuery e no Looker/Data Studio.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassificacaoReceita:
    """Resultado padronizado da classificação de uma receita orçamentária."""

    codigo_nr: str | None
    grupo_gerencial: str
    origem_mcasp: str
    tipo_receita: str
    eh_intra_orcamentaria: bool


_RE_CODIGO_NR_INICIO = re.compile(r"^(?:RO)?\s*([1278](?:\.?\d){3,})\b", re.IGNORECASE)
_RE_SEPARADOR_INICIAL = re.compile(r"^[\s\-–—]+")


def _normalizar_texto(texto: str | None) -> str:
    """Remove acentos, espaços extras, NBSP e prefixos '- ' para comparação segura."""
    if not texto:
        return ""
    limpo = texto.replace("\xa0", " ").strip()
    limpo = _RE_SEPARADOR_INICIAL.sub("", limpo)
    decomposto = unicodedata.normalize("NFKD", limpo)
    sem_acentos = "".join(ch for ch in decomposto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", sem_acentos).strip().upper()


def extrair_codigo_nr(valor: str | None) -> str | None:
    """Extrai o código de Natureza da Receita (8 dígitos sem pontos) de códigos SICONFI ou textos.

    Aceita apenas códigos iniciados pela categoria oficial (1, 2, 7 ou 8), opcionalmente
    precedidos por ``RO``, evitando falsos positivos com números de conta/lei no meio do texto.

    Exemplos aceitos:
    - ``"11125001"`` -> ``"11125001"``
    - ``"RO1.1.1.2.50.0.0"`` -> ``"11125000"``
    - ``"1.1.1.4.51.1.0 - Imposto sobre Serviços..."`` -> ``"11145110"``
    - ``"72150200000000 - Contribuição Patronal"`` -> ``"72150200"``
    """
    if not valor:
        return None
    texto = valor.strip()
    if not texto:
        return None

    cabecalho = texto.split(" - ", 1)[0].strip()
    match = _RE_CODIGO_NR_INICIO.match(cabecalho)
    if not match:
        return None

    digitos = match.group(1).replace(".", "")
    if len(digitos) >= 8:
        return digitos[:8]
    return digitos.ljust(8, "0")


def extrair_segmentos_dca(cod_conta: str | None) -> tuple[str, ...] | None:
    """Extrai os segmentos significativos de um ``cod_conta`` da DCA (ex.: ``RO1.1.1.2.50.0.0``).

    Remove apenas segmentos inteiramente zerados à direita (``"0"`` ou ``"00"``),
    preservando zeros que fazem parte de um código de 2 dígitos (como ``"50"`` do IPTU).
    Retorna ``None`` para contas textuais totalizadoras (como ``ReceitasExcetoIntraOrcamentarias``).
    """
    if not cod_conta:
        return None
    texto = cod_conta.strip()
    if not texto.upper().startswith("RO"):
        return None
    corpo = texto[2:].strip()
    partes = [p.strip() for p in corpo.split(".") if p.strip()]
    if not partes or not all(p.isdigit() for p in partes):
        return None
    while len(partes) > 1 and set(partes[-1]) == {"0"}:
        partes.pop()
    return tuple(partes)


def classificar_por_codigo_nr(codigo_nr_bruto: str | None) -> ClassificacaoReceita:
    """Classifica uma receita a partir do código de Natureza da Receita (STN/MCASP)."""
    codigo = extrair_codigo_nr(codigo_nr_bruto)
    if not codigo:
        return ClassificacaoReceita(
            codigo_nr=None,
            grupo_gerencial="Não Classificada",
            origem_mcasp="Não Classificada",
            tipo_receita="Não Classificada",
            eh_intra_orcamentaria=False,
        )

    categoria = codigo[0]
    eh_intra = categoria in {"7", "8"}

    # Mapeia categoria intra-orçamentária (7->1, 8->2) para classificar origem/tipo de forma homogênea.
    cat_base = "1" if categoria == "7" else ("2" if categoria == "8" else categoria)
    codigo_base = cat_base + codigo[1:]
    origem_2 = codigo_base[:2]
    especie_3 = codigo_base[:3]
    desdobr_4 = codigo_base[:4]
    desdobr_6 = codigo_base[:6]

    if cat_base == "2":
        origem_mcasp_mapa = {
            "21": "2.1 - Operações de Crédito",
            "22": "2.2 - Alienação de Bens",
            "23": "2.3 - Amortização de Empréstimos",
            "24": "2.4 - Transferências de Capital",
            "29": "2.9 - Outras Receitas de Capital",
        }
        tipo_cap_mapa = {
            "21": "Operações de Crédito",
            "22": "Alienação de Bens",
            "23": "Amortização de Empréstimos",
            "24": "Transferências de Capital",
            "29": "Outras Receitas de Capital",
        }
        return ClassificacaoReceita(
            codigo_nr=codigo,
            grupo_gerencial="Receitas de Capital",
            origem_mcasp=origem_mcasp_mapa.get(origem_2, "2.x - Receitas de Capital"),
            tipo_receita=tipo_cap_mapa.get(origem_2, "Receitas de Capital"),
            eh_intra_orcamentaria=eh_intra,
        )

    if origem_2 == "11":
        origem_mcasp = "1.1 - Impostos, Taxas e Contribuições de Melhoria"
        grupo = "Tributárias e Patrimoniais"
        if desdobr_6 == "111250":
            tipo = "IPTU"
        elif desdobr_6 == "111253":
            tipo = "ITBI"
        elif desdobr_6 == "111303":
            tipo = "IR / IRRF"
        elif desdobr_6 == "111451":
            tipo = "ISS / ISSQN"
        elif especie_3 == "111":
            tipo = "Outros Impostos"
        elif especie_3 == "112":
            tipo = "Taxas"
        elif especie_3 == "113":
            tipo = "Contribuição de Melhoria"
        else:
            tipo = "Outras Receitas Tributárias"
        return ClassificacaoReceita(codigo, grupo, origem_mcasp, tipo, eh_intra)

    if origem_2 == "13":
        origem_mcasp = "1.3 - Receita Patrimonial"
        grupo = "Tributárias e Patrimoniais"
        if especie_3 == "131":
            tipo = "Exploração do Patrimônio Imobiliário"
        elif especie_3 == "132":
            tipo = "Valores Mobiliários / Aplicações Financeiras"
        elif especie_3 == "133":
            tipo = "Concessões e Permissões"
        elif especie_3 == "134":
            tipo = "Exploração de Recursos Naturais / Royalties"
        else:
            tipo = "Outras Receitas Patrimoniais"
        return ClassificacaoReceita(codigo, grupo, origem_mcasp, tipo, eh_intra)

    if origem_2 == "12":
        origem_mcasp = "1.2 - Contribuições"
        grupo = "Contribuições"
        if especie_3 == "121":
            tipo = "Contribuições Sociais / RPPS"
        elif especie_3 == "124":
            tipo = "COSIP / Iluminação Pública"
        else:
            tipo = "Outras Contribuições"
        return ClassificacaoReceita(codigo, grupo, origem_mcasp, tipo, eh_intra)

    if origem_2 == "17":
        origem_mcasp = "1.7 - Transferências Correntes"
        grupo = "Transferências Correntes"
        if desdobr_6 == "171151":
            tipo = "FPM"
        elif desdobr_6 == "171152":
            tipo = "ITR"
        elif desdobr_4 == "1713":
            tipo = "Transferências SUS (União)"
        elif desdobr_4 == "1714":
            tipo = "Transferências FNDE / Educação"
        elif desdobr_4 == "1715":
            tipo = "Transferências FNAS / Assistência Social"
        elif desdobr_4 == "1712":
            tipo = "Royalties / Compensações Financeiras (União)"
        elif especie_3 == "171":
            tipo = "Outras Transferências da União"
        elif desdobr_6 == "172150":
            tipo = "Cota-Parte ICMS"
        elif desdobr_6 == "172151":
            tipo = "Cota-Parte IPVA"
        elif desdobr_6 == "172152":
            tipo = "Cota-Parte IPI-Exportação"
        elif desdobr_4 == "1722":
            tipo = "Royalties / Compensações Financeiras (Estado)"
        elif desdobr_4 == "1723":
            tipo = "Transferências SUS (Estado)"
        elif especie_3 == "172":
            tipo = "Outras Transferências do Estado"
        elif desdobr_6 == "175150" or desdobr_4 == "1751":
            tipo = "FUNDEB"
        elif especie_3 == "175":
            tipo = "Outras Transferências de Instituições Públicas"
        else:
            tipo = "Outras Transferências Correntes"
        return ClassificacaoReceita(codigo, grupo, origem_mcasp, tipo, eh_intra)

    if origem_2 == "16":
        return ClassificacaoReceita(
            codigo,
            "Outras Receitas Correntes",
            "1.6 - Receita de Serviços",
            "Receita de Serviços",
            eh_intra,
        )

    if origem_2 in {"14", "15", "19"}:
        origem_mapa = {
            "14": "1.4 - Receita Agropecuária",
            "15": "1.5 - Receita Industrial",
            "19": "1.9 - Outras Receitas Correntes",
        }
        return ClassificacaoReceita(
            codigo,
            "Outras Receitas Correntes",
            origem_mapa[origem_2],
            "Outras Receitas Correntes",
            eh_intra,
        )

    return ClassificacaoReceita(
        codigo_nr=codigo,
        grupo_gerencial="Não Classificada",
        origem_mcasp="Não Classificada",
        tipo_receita="Não Classificada",
        eh_intra_orcamentaria=eh_intra,
    )


def classificar_receita_gpi(
    categoria: str | None = None,
    conta_origem: str | None = None,
    especie: str | None = None,
    rubrica: str | None = None,
    alinea: str | None = None,
    subalinea: str | None = None,
) -> ClassificacaoReceita:
    """Classifica uma receita do Portal da Transparência de Santos (GPI - E&L).

    Tenta primeiro extrair um código numérico de Natureza da Receita (caso presente
    em algum campo, como ocorre em ``orcamento_receitas``). Quando os campos contêm
    apenas a descrição textual MCASP (como ocorre em ``json_execucao_receitas``),
    aplica a classificação hierárquica equivalente.
    """
    for candidato in (subalinea, alinea, rubrica, especie, conta_origem, categoria):
        codigo = extrair_codigo_nr(candidato)
        if codigo:
            return classificar_por_codigo_nr(codigo)

    cat = _normalizar_texto(categoria)
    orig = _normalizar_texto(conta_origem)
    esp = _normalizar_texto(especie)
    rub = _normalizar_texto(rubrica)
    ali = _normalizar_texto(alinea)
    sub = _normalizar_texto(subalinea)
    texto_completo = " | ".join(p for p in (cat, orig, esp, rub, ali, sub) if p)

    eh_intra = "INTRA" in cat or "INTRA" in orig or "INTRA" in esp

    if "CAPITAL" in cat or "ALIENACAO DE BENS" in orig or "OPERACOES DE CREDITO" in orig:
        if "TRANSFERENCIAS DE CAPITAL" in orig:
            return ClassificacaoReceita(None, "Receitas de Capital", "2.4 - Transferências de Capital", "Transferências de Capital", eh_intra)
        if "ALIENACAO DE BENS" in orig:
            return ClassificacaoReceita(None, "Receitas de Capital", "2.2 - Alienação de Bens", "Alienação de Bens", eh_intra)
        if "OPERACOES DE CREDITO" in orig:
            return ClassificacaoReceita(None, "Receitas de Capital", "2.1 - Operações de Crédito", "Operações de Crédito", eh_intra)
        if "AMORTIZACAO" in orig:
            return ClassificacaoReceita(None, "Receitas de Capital", "2.3 - Amortização de Empréstimos", "Amortização de Empréstimos", eh_intra)
        return ClassificacaoReceita(None, "Receitas de Capital", "2.9 - Outras Receitas de Capital", "Outras Receitas de Capital", eh_intra)

    if "IMPOSTOS, TAXAS E CONTRIBUICOES DE MELHORIA" in orig:
        origem_mcasp = "1.1 - Impostos, Taxas e Contribuições de Melhoria"
        grupo = "Tributárias e Patrimoniais"
        if "PREDIAL E TERRITORIAL URBANA" in texto_completo or "IPTU" in texto_completo:
            tipo = "IPTU"
        elif "TRANSMISSAO" in texto_completo and ("INTER VIVOS" in texto_completo or "ITBI" in texto_completo):
            tipo = "ITBI"
        elif "IMPOSTO SOBRE A RENDA" in texto_completo or "IRRF" in texto_completo:
            tipo = "IR / IRRF"
        elif "IMPOSTOS SOBRE SERVICOS" in texto_completo or "IMPOSTO SOBRE SERVICOS" in texto_completo or "ISSQN" in texto_completo:
            tipo = "ISS / ISSQN"
        elif "TAXAS" in esp or "TAXA" in rub:
            tipo = "Taxas"
        elif "CONTRIBUICAO DE MELHORIA" in esp or "MELHORIA" in rub:
            tipo = "Contribuição de Melhoria"
        elif "IMPOSTO" in esp:
            tipo = "Outros Impostos"
        else:
            tipo = "Outras Receitas Tributárias"
        return ClassificacaoReceita(None, grupo, origem_mcasp, tipo, eh_intra)

    if "PATRIMONIAL" in orig:
        origem_mcasp = "1.3 - Receita Patrimonial"
        grupo = "Tributárias e Patrimoniais"
        if "MOBILIARIOS" in esp or "DEPOSITOS BANCARIOS" in texto_completo:
            tipo = "Valores Mobiliários / Aplicações Financeiras"
        elif "IMOBILIARIO" in esp:
            tipo = "Exploração do Patrimônio Imobiliário"
        elif "CONCESS" in esp or "DELEGACAO" in esp:
            tipo = "Concessões e Permissões"
        elif "RECURSOS NATURAIS" in esp or "ROYALTIES" in texto_completo:
            tipo = "Exploração de Recursos Naturais / Royalties"
        else:
            tipo = "Outras Receitas Patrimoniais"
        return ClassificacaoReceita(None, grupo, origem_mcasp, tipo, eh_intra)

    if "CONTRIBUICOES" in orig:
        origem_mcasp = "1.2 - Contribuições"
        grupo = "Contribuições"
        if "ILUMINACAO PUBLICA" in texto_completo or "COSIP" in texto_completo:
            tipo = "COSIP / Iluminação Pública"
        elif "SOCIAIS" in esp or "PREVIDENC" in texto_completo or "PATRONAL" in texto_completo:
            tipo = "Contribuições Sociais / RPPS"
        else:
            tipo = "Outras Contribuições"
        return ClassificacaoReceita(None, grupo, origem_mcasp, tipo, eh_intra)

    if "TRANSFERENCIAS CORRENTES" in orig:
        origem_mcasp = "1.7 - Transferências Correntes"
        grupo = "Transferências Correntes"
        if "FUNDO DE PARTICIPACAO DOS MUNICIPIOS" in texto_completo or "FPM" in texto_completo:
            tipo = "FPM"
        elif "TERRITORIAL RURAL" in texto_completo or "ITR" in ali:
            tipo = "ITR"
        elif "FUNDO DE MANUTENCAO E DESENVOLVIMENTO DA EDUCACAO BASICA" in texto_completo or "FUNDEB" in texto_completo:
            tipo = "FUNDEB"
        elif "ICMS" in texto_completo:
            tipo = "Cota-Parte ICMS"
        elif "IPVA" in texto_completo:
            tipo = "Cota-Parte IPVA"
        elif "IPI" in texto_completo:
            tipo = "Cota-Parte IPI-Exportação"
        elif "SISTEMA UNICO DE SAUDE" in texto_completo or "SUS" in texto_completo:
            if "ESTADOS" in esp:
                tipo = "Transferências SUS (Estado)"
            else:
                tipo = "Transferências SUS (União)"
        elif "FNDE" in texto_completo or "SALARIO-EDUCACAO" in texto_completo or "PNAE" in texto_completo or "PNATE" in texto_completo:
            tipo = "Transferências FNDE / Educação"
        elif "FNAS" in texto_completo or "ASSISTENCIA SOCIAL" in texto_completo:
            tipo = "Transferências FNAS / Assistência Social"
        elif "ROYALTIES" in texto_completo or "COMPENSACAO FINANCEIRA" in texto_completo or "COMPENSACOES FINANCEIRAS" in texto_completo:
            if "ESTADOS" in esp:
                tipo = "Royalties / Compensações Financeiras (Estado)"
            else:
                tipo = "Royalties / Compensações Financeiras (União)"
        elif "UNIAO" in esp:
            tipo = "Outras Transferências da União"
        elif "ESTADOS" in esp:
            tipo = "Outras Transferências do Estado"
        elif "OUTRAS INSTITUICOES PUBLICAS" in esp:
            tipo = "Outras Transferências de Instituições Públicas"
        else:
            tipo = "Outras Transferências Correntes"
        return ClassificacaoReceita(None, grupo, origem_mcasp, tipo, eh_intra)

    if "SERVICOS" in orig:
        return ClassificacaoReceita(None, "Outras Receitas Correntes", "1.6 - Receita de Serviços", "Receita de Serviços", eh_intra)

    if "OUTRAS RECEITAS CORRENTES" in orig or "INDUSTRIAL" in orig or "AGROPECUARIA" in orig:
        return ClassificacaoReceita(None, "Outras Receitas Correntes", "1.9 - Outras Receitas Correntes", "Outras Receitas Correntes", eh_intra)

    return ClassificacaoReceita(
        codigo_nr=None,
        grupo_gerencial="Não Classificada",
        origem_mcasp="Não Classificada",
        tipo_receita="Não Classificada",
        eh_intra_orcamentaria=eh_intra,
    )
