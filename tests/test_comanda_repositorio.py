"""Repositório da Comanda no SQLite de verdade: concorrência, transação desfeita e isolamento entre lojas."""

import threading

import pytest

from propagandas import db
from src.domain.comanda import Ator, ErroComanda, Pedido, ServicoDeComandas
from src.domain.erros import ErroDeDominio
from src.infrastructure.sqlite import RepositorioDeComandasSQLite

CAIXA = Ator(usuario_id=None)  # o banco do teste ainda não tem usuários


@pytest.fixture
def banco(app):
    """Caminho do banco já criado (com as tabelas) e uma loja com um produto no cardápio."""
    caminho = app.config["BANCO"]
    conexao = db.conectar(caminho)
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Outra loja', 'outra-loja')")
        conexao.execute("INSERT INTO cmd_produtos (id, empresa_id, nome, preco_centavos, vai_cozinha) VALUES (1, 1, 'Lanche', 2000, 1)")
    conexao.close()
    return caminho


def servico(caminho, empresa_id=1):
    conexao = db.conectar(caminho)
    return ServicoDeComandas(RepositorioDeComandasSQLite(conexao, empresa_id)), conexao


def comanda_de_22_reais(caminho):
    loja, conexao = servico(caminho)
    comanda_id = loja.abrir(10, "", "", 10, CAIXA)
    loja.lancar(comanda_id, [Pedido(1, 1)], CAIXA)   # 20,00 + 10% = 22,00
    conexao.close()
    return comanda_id


def test_dois_caixas_pagando_ao_mesmo_tempo_nao_pagam_a_mais(banco):
    comanda_id = comanda_de_22_reais(banco)
    largada = threading.Barrier(8)
    resultados = []

    def pagar():
        loja, conexao = servico(banco)
        largada.wait()
        try:
            loja.registrar_pagamento(comanda_id, "pix", 2200, CAIXA)
            resultados.append("pagou")
        except ErroComanda as erro:
            resultados.append(str(erro))
        finally:
            conexao.close()

    caixas = [threading.Thread(target=pagar) for _ in range(8)]
    for caixa in caixas:
        caixa.start()
    for caixa in caixas:
        caixa.join()
    assert resultados.count("pagou") == 1
    assert all(r == "Esta conta já está paga." for r in resultados if r != "pagou")
    loja, conexao = servico(banco)
    assert loja.comanda(comanda_id).totais.pago == 2200
    conexao.close()


def test_erro_no_meio_nao_deixa_nada_gravado(banco, monkeypatch):
    comanda_id = comanda_de_22_reais(banco)
    loja, conexao = servico(banco)

    def banco_caiu(*_):
        raise RuntimeError("falha no meio da gravação")

    monkeypatch.setattr(RepositorioDeComandasSQLite, "registrar_historico", banco_caiu)
    with pytest.raises(RuntimeError):
        loja.registrar_pagamento(comanda_id, "pix", 1000, Ator(None, autorizado_por="caixa"))
    assert conexao.execute("SELECT COUNT(*) FROM cmd_pagamentos").fetchone()[0] == 0
    with pytest.raises(RuntimeError):
        loja.cancelar(comanda_id, "desistiu", CAIXA)
    assert conexao.execute("SELECT status FROM cmd_comandas WHERE id = ?", (comanda_id,)).fetchone()[0] == "aberta"
    conexao.close()


def test_loja_nao_alcanca_comanda_de_outra(banco):
    comanda_id = comanda_de_22_reais(banco)
    outra, conexao = servico(banco, empresa_id=2)
    assert outra._repo.carregar(comanda_id) is None
    for tentativa in (
        lambda: outra.registrar_pagamento(comanda_id, "pix", 100, CAIXA),
        lambda: outra.fechar(comanda_id, CAIXA),
        lambda: outra.cancelar(comanda_id, "x", CAIXA),
        lambda: outra.lancar(comanda_id, [Pedido(1, 1)], CAIXA),   # o produto também é da outra loja
    ):
        with pytest.raises(ErroDeDominio):  # para a outra loja, a comanda e o item "não existem"
            tentativa()
    item_id = conexao.execute("SELECT id FROM cmd_itens").fetchone()[0]
    with pytest.raises(ErroDeDominio):
        outra.mudar_situacao(item_id, "pronto")
    assert outra.marcar_tudo_pronto(comanda_id) == 0
    assert conexao.execute("SELECT status FROM cmd_itens").fetchone()[0] == "pendente"
    conexao.close()


def test_fechamento_grava_os_valores_do_cupom(banco):
    comanda_id = comanda_de_22_reais(banco)
    loja, conexao = servico(banco)
    loja.ajustar(comanda_id, True, 150, CAIXA)
    loja.registrar_pagamento(comanda_id, "dinheiro", 5000, CAIXA)
    loja.fechar(comanda_id, CAIXA)
    linha = conexao.execute("SELECT * FROM cmd_comandas WHERE id = ?", (comanda_id,)).fetchone()
    assert (linha["status"], linha["total_centavos"], linha["taxa_centavos"], linha["desconto_centavos"]) == ("fechada", 2050, 200, 150)
    pagamento = conexao.execute("SELECT valor_centavos, recebido_centavos FROM cmd_pagamentos").fetchone()
    assert tuple(pagamento) == (2050, 5000)   # troco de 29,50
    conexao.close()


def test_so_numero_repetido_vira_comanda_ja_aberta(banco):
    """Outros erros do banco (ex.: usuário que não existe) aparecem como são, não como "já está aberta"."""
    import sqlite3

    loja, conexao = servico(banco)
    loja.abrir(5, "", "", 10, CAIXA)
    with pytest.raises(ErroComanda, match="A comanda 5 já está aberta"):
        loja.abrir(5, "", "", 10, CAIXA)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        loja.abrir(6, "", "", 10, Ator(usuario_id=999))
    conexao.close()
