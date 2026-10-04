"""Contas no SQLite de verdade, com o hash de senha real (werkzeug), e o limite de tentativas entre threads."""

import threading

import pytest

from propagandas import db
from src.domain.contas import CredenciaisInvalidas, ServicoDeContas
from src.domain.tentativas import LimiteDeTentativas
from src.infrastructure.senhas import SenhasWerkzeug
from src.infrastructure.sqlite import RepositorioDeContasSQLite


@pytest.fixture
def contas(app):
    conexao = db.conectar(app.config["BANCO"])
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Lanchonete', 'lanchonete')")
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (3, 'Padaria', 'padaria')")
    yield ServicoDeContas(RepositorioDeContasSQLite(conexao), SenhasWerkzeug()), conexao
    conexao.close()


def test_senha_nunca_fica_gravada(contas):
    servico, conexao = contas
    servico.criar(2, "joao", "senha-do-joao", "garcom")
    senha_hash = conexao.execute("SELECT senha_hash FROM usuarios WHERE usuario = 'joao'").fetchone()[0]
    assert "senha-do-joao" not in senha_hash and senha_hash.count("$") >= 2   # método$sal$hash
    assert servico.entrar("joao", "senha-do-joao", None, "1.1.1.1").usuario == "joao"
    with pytest.raises(CredenciaisInvalidas):
        servico.entrar("joao", "SENHA-DO-JOAO", None, "1.1.1.1")


def test_loja_pelo_codigo_e_situacao_da_empresa(contas):
    servico, conexao = contas
    servico.criar(2, "joao", "mesma-senha-1", "garcom")
    servico.criar(3, "joao", "mesma-senha-1", "caixa")
    assert servico.entrar("joao", "mesma-senha-1", " Padaria ", "1.1.1.1").empresa_id == 3   # código sem diferenciar maiúsculas
    with conexao:
        conexao.execute("UPDATE empresas SET ativa = 0, motivo_suspensao = 'inadimplencia' WHERE id = 2")
    assert servico.entrar("joao", "mesma-senha-1", "lanchonete", "1.1.1.1").acesso == "so_pagamento"


def test_papel_novo_tira_a_permissao_de_fechar_contas(contas):
    servico, conexao = contas
    admin = servico.criar(2, "dono", "senha-do-dono", "admin")
    garcom = servico.criar(2, "joao", "senha-do-joao", "garcom")
    servico.alternar_fecha_conta(2, garcom)
    servico.editar(2, admin, garcom, "garcom", "", {"comanda"})
    assert conexao.execute("SELECT fecha_conta FROM usuarios WHERE id = ?", (garcom,)).fetchone()[0] == 1
    servico.editar(2, admin, garcom, "caixa", "", {"comanda"})
    assert tuple(conexao.execute("SELECT papel, fecha_conta FROM usuarios WHERE id = ?", (garcom,)).fetchone()) == ("caixa", 0)


def test_trocar_senha_derruba_as_sessoes(contas):
    servico, conexao = contas
    joao = servico.criar(2, "joao", "senha-do-joao", "garcom")
    antes = conexao.execute("SELECT token_sessao FROM usuarios WHERE id = ?", (joao,)).fetchone()[0]
    servico.trocar_senha(joao, "senha-nova-joao")
    assert conexao.execute("SELECT token_sessao FROM usuarios WHERE id = ?", (joao,)).fetchone()[0] != antes
    assert servico.entrar("joao", "senha-nova-joao", None, "1.1.1.1")


def test_limite_de_tentativas_conta_certo_com_varias_threads():
    limite = LimiteDeTentativas(100, 60)
    largada = threading.Barrier(16)

    def errar():
        largada.wait()
        for _ in range(50):
            limite.errou("6.6.6.6")

    threads = [threading.Thread(target=errar) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(limite._erros["6.6.6.6"]) == 800 and limite.bloqueado("6.6.6.6")
    limite.acertou("6.6.6.6")
    assert not limite.bloqueado("6.6.6.6")
