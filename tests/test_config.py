import os

import pytest

from src.config import arquivo as arquivo_config


def escrever(tmp_path, texto, bom=False):
    caminho = tmp_path / "configuracao.env"
    caminho.write_text(("﻿" if bom else "") + texto, encoding="utf-8")
    return str(caminho)


def test_le_valores_com_aspas_comentarios_e_cifrao(tmp_path):
    caminho = escrever(tmp_path, """
# comentário
NOME_PLATAFORMA='Rede Pão Quente'
ASAAS_API_KEY='$aact_hmlg_abc'
PORTA=8080   # comentário no fim
export FUSO_HORARIO=America/Manaus
SMTP_HOST=
""", bom=True)  # Bloco de Notas do Windows salva com BOM
    assert arquivo_config.ler(caminho) == {
        "NOME_PLATAFORMA": "Rede Pão Quente",
        "ASAAS_API_KEY": "$aact_hmlg_abc",
        "PORTA": "8080",
        "FUSO_HORARIO": "America/Manaus",
        "SMTP_HOST": "",
    }


def test_variavel_de_ambiente_tem_prioridade_e_vazio_e_ignorado(tmp_path, monkeypatch):
    caminho = escrever(tmp_path, "PORTA=8080\nNOME_PLATAFORMA=Do arquivo\nSMTP_HOST=\n")
    monkeypatch.setenv("PORTA", "9000")
    monkeypatch.delenv("NOME_PLATAFORMA", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert arquivo_config.carregar(caminho) == caminho
    assert os.environ["PORTA"] == "9000"
    assert os.environ["NOME_PLATAFORMA"] == "Do arquivo"
    assert "SMTP_HOST" not in os.environ
    monkeypatch.delenv("NOME_PLATAFORMA")


def test_linha_invalida_mostra_onde_esta_o_erro(tmp_path):
    caminho = escrever(tmp_path, "PORTA=5000\nisso não é configuração\n")
    with pytest.raises(ValueError, match="linha 2"):
        arquivo_config.ler(caminho)


def test_sem_arquivo_nao_faz_nada(tmp_path):
    assert arquivo_config.carregar(str(tmp_path / "nao-existe.env")) is None


def test_exemplo_do_repositorio_e_valido():
    exemplo = os.path.join(arquivo_config.PASTA_PROJETO, "configuracao.env.exemplo")
    valores = arquivo_config.ler(exemplo)
    assert valores["ASAAS_AMBIENTE"] == "sandbox" and valores["NOME_PLATAFORMA"] == "Painel de Propagandas"
