"""Relatórios no SQLite de verdade: vendas (com comanda antiga sem taxa gravada) e exibições por dia local."""

import pytest

from propagandas import db
from src.domain.comanda import Ator, Pedido, ServicoDeComandas
from src.domain.relatorios import RelatorioDeExibicoes, RelatorioDeVendas
from src.infrastructure.sqlite import RepositorioDeComandasSQLite, RepositorioDeExibicoesSQLite, RepositorioDeVendasSQLite

DIA = ("2000-01-01 00:00:00", "2100-01-01 00:00:00")
CAIXA = Ator(None)


@pytest.fixture
def conexao(app):
    conexao = db.conectar(app.config["BANCO"])
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Outra', 'outra')")
        conexao.execute("INSERT INTO cmd_produtos (id, empresa_id, nome, preco_centavos, vai_cozinha) VALUES (1, 1, 'Lanche', 1005, 1)")
    yield conexao
    conexao.close()


def test_vendas_batem_com_os_cupons(conexao):
    loja = ServicoDeComandas(RepositorioDeComandasSQLite(conexao, 1))
    for numero, forma in ((1, "pix"), (2, "dinheiro")):
        comanda = loja.abrir(numero, "", "", 10, CAIXA)
        loja.lancar(comanda, [Pedido(1, 1)], CAIXA)                 # 10,05 + 10% = 11,06 (taxa 1,01)
        loja.registrar_pagamento(comanda, forma, 1106, CAIXA)
        loja.fechar(comanda, CAIXA)
    with conexao:   # uma comanda de antes de a taxa ser gravada no fechamento
        conexao.execute("UPDATE cmd_comandas SET taxa_centavos = NULL WHERE numero = 2")
    loja.abrir(3, "", "", 10, CAIXA)                                 # aberta: não entra no faturamento
    resumo = RelatorioDeVendas(RepositorioDeVendasSQLite(conexao, 1)).resumo(*DIA)
    assert (resumo.comandas, resumo.faturamento, resumo.taxa, resumo.ticket_medio, resumo.abertas) == (2, 2212, 202, 1106, 1)
    assert sorted((f.forma, f.valor) for f in resumo.formas) == [("dinheiro", 1106), ("pix", 1106)]
    assert [(p.nome, p.quantidade) for p in resumo.produtos] == [("Lanche", 2)]
    outra = RelatorioDeVendas(RepositorioDeVendasSQLite(conexao, 2)).resumo(*DIA)
    assert (outra.comandas, outra.faturamento, outra.abertas) == (0, 0, 0)
    planilha = RelatorioDeVendas(RepositorioDeVendasSQLite(conexao, 1)).planilha(*DIA, data_hora=str)
    assert [linha[0] for linha in planilha[1:]] == [1, 2] and planilha[1][-1] == "PIX 11,06"


def test_exibicoes_por_dia_local(conexao):
    with conexao:
        conexao.execute("INSERT INTO telas (id, empresa_id, nome, codigo) VALUES (5, 1, 'Balcão', 'balcao-x')")
        conexao.executemany(
            "INSERT INTO exibicoes (empresa_id, tela_id, propaganda_id, propaganda_nome, exibido_em, duracao) VALUES (?, ?, ?, ?, ?, ?)",
            [(1, 5, 9, "Café", "2026-10-04 02:30:00", 10), (1, 5, 9, "Café", "2026-10-04 12:00:00", 10),
             (2, None, 9, "Da outra", "2026-10-04 12:00:00", 10)])
    relatorio = RelatorioDeExibicoes(RepositorioDeExibicoesSQLite(conexao, 1))
    resumo = relatorio.resumo(*DIA, None)
    assert [(p.nome, p.excluida, p.exibicoes, p.telas) for p in resumo.por_propaganda] == [("Café", True, 2, 1)]
    linhas = relatorio.planilha(*DIA, None, ajuste_minutos=-180)    # 02:30 UTC = 23:30 do dia 3 em São Paulo
    assert [linha[0] for linha in linhas[1:]] == ["03/10/2026", "04/10/2026"]
    assert relatorio.resumo(*DIA, tela_id=99).total_exibicoes == 0
