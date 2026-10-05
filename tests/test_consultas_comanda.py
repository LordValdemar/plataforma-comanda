"""Consultas das telas da Comanda no SQLite de verdade: o que cada tela mostra, sempre só da própria loja."""

import pytest

from propagandas import db
from src.domain.comanda import Ator, Pedido, ServicoDeComandas
from src.infrastructure.sqlite import ConsultasDaComanda, RepositorioDeComandasSQLite

CAIXA = Ator(None)


@pytest.fixture
def conexao(app):
    conexao = db.conectar(app.config["BANCO"])
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Outra', 'outra')")
        conexao.execute("INSERT INTO usuarios (id, empresa_id, usuario, senha_hash, papel, token_sessao) VALUES (7, 1, 'ana', 'x', 'garcom', 't')")
        conexao.execute("INSERT INTO cmd_produtos (id, empresa_id, nome, preco_centavos, vai_cozinha, codigo) "
                        "VALUES (1, 1, 'Pastel', 800, 1, '12')")
        conexao.execute("INSERT INTO cmd_produtos (id, empresa_id, nome, preco_centavos, vai_cozinha) VALUES (2, 1, 'Lata', 600, 0)")
    yield conexao
    conexao.close()


def test_telas_da_comanda(conexao):
    loja = ServicoDeComandas(RepositorioDeComandasSQLite(conexao, 1))
    aberta = loja.abrir(5, "2", "Ana", 10, CAIXA, 7)
    loja.lancar(aberta, [Pedido(1, 2), Pedido(2, 1), Pedido(1, 1)], Ator(7))
    fechada = loja.abrir(6, "", "", 10, CAIXA)
    loja.lancar(fechada, [Pedido(2, 1)], CAIXA)
    loja.registrar_pagamento(fechada, "pix", 660, CAIXA)
    loja.fechar(fechada, CAIXA)
    RepositorioDeComandasSQLite(conexao, 1).registrar_historico(fechada, "fechar conta", "autorizado por maria", 7)
    leitura, outra = ConsultasDaComanda(conexao, 1), ConsultasDaComanda(conexao, 2)

    assert leitura.comanda(aberta)["garcom_nome"] == "ana" and outra.comanda(aberta) is None
    assert leitura.aberta_com_numero("5") == aberta and leitura.aberta_com_numero("6") is None
    [linha] = leitura.abertas()
    assert (linha["numero"], linha["consumo"], linha["aguardando"], linha["garcom_nome"]) == (5, 3000, 2, "ana")
    assert [(i["nome"], i["quantidade"]) for i in leitura.itens_do_cupom(aberta)] == [("Pastel", 3), ("Lata", 1)]
    assert len(leitura.itens(aberta)) == 3 and outra.itens(aberta) == []
    assert leitura.itens_na_cozinha(aberta) == 2                      # a lata não vai para a cozinha
    assert [p["forma"] for p in leitura.pagamentos(fechada)] == ["pix"] and outra.pagamentos(fechada) == []
    assert [(a["acao"], a["usuario"]) for a in leitura.auditoria(fechada)] == [("fechar conta", "ana")]
    assert outra.auditoria(fechada) == []
    [encerrada] = leitura.encerradas("2000-01-01", "2100-01-01")
    assert (encerrada["numero"], encerrada["autorizacao"]) == (6, "autorizado por maria")
    assert leitura.produto_pelo_codigo("12") == 1 and leitura.produto_pelo_codigo("99") is None
    assert outra.produto_pelo_codigo("12") is None
    assert [g["usuario"] for g in leitura.garcons()] == ["ana"] and outra.garcons() == []
    assert [i["comanda_id"] for i in leitura.na_cozinha()] == [aberta, aberta] and outra.na_cozinha() == []
    assert leitura.prontos_para_entregar() == [] and leitura.entregues_desde("2000-01-01", 15) == []
