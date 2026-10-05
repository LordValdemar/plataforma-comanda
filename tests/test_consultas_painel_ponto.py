"""Consultas das telas do Painel e do ponto no SQLite de verdade: isolamento entre lojas e o que cada tela mostra."""

import pytest

from propagandas import db
from src.infrastructure.sqlite import ConsultasDoPainel, ConsultasDoPonto


@pytest.fixture
def conexao(app):
    conexao = db.conectar(app.config["BANCO"])
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug, razao_social) VALUES (2, 'Outra', 'outra', 'Outra Ltda')")
        conexao.executemany("INSERT INTO grupos (id, empresa_id, nome) VALUES (?, ?, ?)", [(7, 1, "SP"), (8, 2, "RJ")])
        conexao.executemany("INSERT INTO telas (id, empresa_id, nome, codigo, grupo_id) VALUES (?, ?, ?, ?, ?)",
                            [(10, 1, "Vitrine", "v-1", 7), (11, 1, "Balcão", "b-1", None), (20, 2, "Da outra", "o-1", 8)])
        conexao.executemany("INSERT INTO propagandas (id, empresa_id, nome, arquivo, tipo, duracao, posicao) "
                            "VALUES (?, ?, ?, ?, 'imagem', 10, ?)", [(1, 1, "a", "a.png", 1), (2, 2, "b", "b.png", 1)])
        conexao.executemany("INSERT INTO propaganda_destinos (propaganda_id, tela_id, grupo_id) VALUES (?, ?, ?)",
                            [(1, 11, None), (1, None, 7), (2, 20, None)])
        conexao.execute("INSERT INTO usuarios (id, empresa_id, usuario, senha_hash, papel, token_sessao) "
                        "VALUES (5, 2, 'zeca', 'x', 'garcom', 't')")
        conexao.execute("INSERT INTO ponto_registros (empresa_id, usuario_id, usuario_nome, entrada) "
                        "VALUES (2, 5, 'zeca', '2026-10-04 12:00:00')")
        conexao.execute("INSERT INTO configuracoes (empresa_id, chave, valor) VALUES (2, 'ponto_quiosque', 'segredo-x')")
    yield conexao
    conexao.close()


def test_painel(conexao):
    loja, outra = ConsultasDoPainel(conexao, 1), ConsultasDoPainel(conexao, 2)
    assert [t["nome"] for t in loja.telas_para_escolher()] == ["Balcão", "Vitrine"]
    assert [(t["nome"], t["grupo_nome"]) for t in loja.telas()] == [("Balcão", None), ("Vitrine", "SP")]
    assert [(g["nome"], g["total"]) for g in loja.grupos_com_total()] == [("SP", 1)]
    assert [g["nome"] for g in outra.grupos_para_escolher()] == ["RJ"]
    assert loja.destinos_por_propaganda() == {1: {"telas": {11}, "grupos": {7}, "nomes": ["Balcão", "Grupo SP"]}}
    assert outra.destinos_por_propaganda() == {2: {"telas": {20}, "grupos": set(), "nomes": ["Da outra"]}}


def test_ponto(conexao):
    ponto = ConsultasDoPonto(conexao)
    assert ponto.lojas_com_ponto_aberto() == [2]
    assert ponto.loja_do_quiosque("segredo-x") == 2 and ponto.loja_do_quiosque("outro") is None
    assert ponto.nome_da_loja(2) == "Outra Ltda" and ponto.codigo_da_loja(2) == "outra" and ponto.nome_da_loja(99) == ""
    [zeca] = ponto.equipe(2)
    assert (zeca["usuario"], zeca["trabalhando_desde"]) == ("zeca", "2026-10-04 12:00:00")
    assert all(p["usuario"] != "zeca" for p in ponto.equipe(1))
