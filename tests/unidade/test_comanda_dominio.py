"""Regras da Comanda testadas sem banco nem navegador (domínio puro + repositório em memória)."""

import pytest

from src.domain.comanda import (
    Ator,
    ComandaNaoEncontrada,
    ErroComanda,
    ItemNaoEncontrado,
    Pedido,
    ProdutoDoCardapio,
    ServicoDeComandas,
    taxa_percentual_valida,
)
from src.domain.dinheiro import ValorInvalido, entrada_reais, ler_reais, porcentagem, reais

from .repositorio_falso import RepositorioFalso

CAIXA = Ator(usuario_id=7)
GARCOM_AUTORIZADO = Ator(usuario_id=9, autorizado_por="caixa")


@pytest.fixture
def repo():
    repositorio = RepositorioFalso()
    repositorio.produtos = {
        1: ProdutoDoCardapio(id=1, nome="X-Salada", preco_centavos=2000, vai_cozinha=True),
        2: ProdutoDoCardapio(id=2, nome="Refri lata", preco_centavos=600, vai_cozinha=False),
        3: ProdutoDoCardapio(id=3, nome="Pastel", preco_centavos=1005, vai_cozinha=True),
    }
    return repositorio


@pytest.fixture
def servico(repo):
    return ServicoDeComandas(repo)


def comanda_com(servico, *pedidos, taxa=10.0, numero=1):
    comanda_id = servico.abrir(numero, "4", "Ana", taxa, CAIXA)
    if pedidos:
        servico.lancar(comanda_id, [Pedido(p, q) for p, q in pedidos], CAIXA)
    return comanda_id


# -- dinheiro -----------------------------------------------------------------------

@pytest.mark.parametrize("texto, centavos", [
    ("12,50", 1250), ("12.50", 1250), ("R$ 1.234,56", 123456), ("1.234", 123400), ("0,01", 1), ("", 0), ("7", 700),
])
def test_ler_reais(texto, centavos):
    assert ler_reais(texto) == centavos


@pytest.mark.parametrize("texto", ["abc", "1,234", "12,5,0", "-3", "1e5", "9999999999"])
def test_ler_reais_recusa_lixo(texto):
    with pytest.raises(ValorInvalido):
        ler_reais(texto)


def test_ler_reais_sem_zero():
    with pytest.raises(ValorInvalido):
        ler_reais("", permitir_zero=False)
    with pytest.raises(ValorInvalido):
        ler_reais("0,00", permitir_zero=False)


def test_mostrar_reais():
    assert reais(123456789) == "R$ 1.234.567,89"
    assert reais(-150) == "-R$ 1,50"
    assert reais(None) == "R$ 0,00"
    assert entrada_reais(1250) == "12,50"


def test_porcentagem_arredonda_como_no_comercio():
    assert porcentagem(1005, 10) == 101   # 100,5 → 101 (o round() do Python daria 100)
    assert porcentagem(1004, 10) == 100
    assert porcentagem(999, 12.5) == 125  # 124,875 → 125
    assert porcentagem(1, 10) == 0


def test_taxa_de_servico_valida():
    assert taxa_percentual_valida("12,5") == 12.5
    for errada in ("31", "-1", "abc"):
        with pytest.raises(ErroComanda):
            taxa_percentual_valida(errada)


# -- abrir e lançar --------------------------------------------------------------------

def test_abrir_valida_numero_e_nao_repete(servico):
    servico.abrir("12", " 4 ", "", 10, CAIXA)
    for numero in ("0", "100000", "abc"):
        with pytest.raises(ErroComanda):
            servico.abrir(numero, "", "", 10, CAIXA)
    with pytest.raises(ErroComanda, match="já está aberta"):
        servico.abrir(12, "", "", 10, CAIXA)
    comanda = next(iter(servico._repo.comandas.values()))
    assert comanda.numero == 12


def test_lancar_tudo_ou_nada(servico, repo):
    comanda_id = comanda_com(servico)
    with pytest.raises(ErroComanda, match="saiu do cardápio"):
        servico.lancar(comanda_id, [Pedido(1, 1), Pedido(99, 1)], CAIXA)
    assert servico.comanda(comanda_id).itens == []        # nem o X-Salada entrou
    with pytest.raises(ErroComanda, match="Quantidade"):
        servico.lancar(comanda_id, [Pedido(1, 0)], CAIXA)
    with pytest.raises(ErroComanda, match="pelo menos um"):
        servico.lancar(comanda_id, [], CAIXA)


def test_bebida_ja_sai_entregue_e_lanche_vai_para_cozinha(servico):
    comanda = servico.comanda(comanda_com(servico, (1, 1), (2, 2)))
    assert [(i.nome, i.status) for i in comanda.itens] == [("X-Salada", "pendente"), ("Refri lata", "entregue")]


def test_quem_lanca_primeiro_passa_a_atender(servico):
    comanda_id = comanda_com(servico)
    servico.lancar(comanda_id, [Pedido(1, 1)], Ator(5), atendente=5)
    servico.lancar(comanda_id, [Pedido(1, 1)], Ator(6), atendente=6)
    assert servico.comanda(comanda_id).garcom_id == 5


# -- contas --------------------------------------------------------------------------------

def test_totais_com_taxa_desconto_e_cancelado(servico):
    comanda_id = comanda_com(servico, (1, 2), (2, 1))   # 40,00 + 6,00
    item = servico.comanda(comanda_id).itens[1]
    servico.cancelar_item(comanda_id, item.id, "pediu errado", CAIXA)
    servico.ajustar(comanda_id, True, 500, CAIXA)
    totais = servico.comanda(comanda_id).totais
    assert (totais.subtotal, totais.taxa, totais.desconto, totais.total) == (4000, 400, 500, 3900)


def test_taxa_com_meio_centavo(servico):
    totais = servico.comanda(comanda_com(servico, (3, 1))).totais   # 10,05 + 10%
    assert (totais.taxa, totais.total) == (101, 1106)


def test_desconto_nao_passa_da_conta(servico):
    comanda_id = comanda_com(servico, (1, 1))
    with pytest.raises(ErroComanda, match="não pode passar"):
        servico.ajustar(comanda_id, True, 2201, CAIXA)
    servico.ajustar(comanda_id, False, 2000, CAIXA)          # sem taxa, até 20,00
    assert servico.comanda(comanda_id).totais.total == 0


# -- pagamento e fechamento ------------------------------------------------------------------

def test_pagamento_troco_so_em_dinheiro(servico):
    comanda_id = comanda_com(servico, (1, 1))   # 22,00 com taxa
    with pytest.raises(ErroComanda, match="Só pagamento em dinheiro"):
        servico.registrar_pagamento(comanda_id, "pix", 3000, CAIXA)
    with pytest.raises(ErroComanda, match="Forma"):
        servico.registrar_pagamento(comanda_id, "cheque", 100, CAIXA)
    with pytest.raises(ErroComanda, match="valor"):
        servico.registrar_pagamento(comanda_id, "pix", 0, CAIXA)
    servico.registrar_pagamento(comanda_id, "pix", 1000, CAIXA)
    pagamento = servico.registrar_pagamento(comanda_id, "dinheiro", 2000, CAIXA)
    assert (pagamento.valor_centavos, pagamento.troco) == (1200, 800)
    totais = servico.comanda(comanda_id).totais
    assert (totais.pago, totais.troco, totais.restante) == (2200, 800, 0)
    with pytest.raises(ErroComanda, match="já está paga"):
        servico.registrar_pagamento(comanda_id, "dinheiro", 100, CAIXA)


def test_fecha_so_com_a_conta_paga_e_grava_o_total(servico, repo):
    comanda_id = comanda_com(servico, (1, 1))
    servico.registrar_pagamento(comanda_id, "pix", 1000, CAIXA)
    with pytest.raises(ErroComanda, match="falta receber R\\$ 12,00"):
        servico.fechar(comanda_id, CAIXA)
    servico.registrar_pagamento(comanda_id, "debito", 1200, CAIXA)
    totais = servico.fechar(comanda_id, CAIXA)
    comanda = servico.comanda(comanda_id)
    assert (comanda.status, comanda.total_centavos, totais.total) == ("fechada", 2200, 2200)
    with pytest.raises(ErroComanda, match="já foi fechada"):
        servico.registrar_pagamento(comanda_id, "pix", 1, CAIXA)
    with pytest.raises(ErroComanda, match="já foi fechada"):
        servico.lancar(comanda_id, [Pedido(1, 1)], CAIXA)


def test_pagamento_a_mais_depois_de_desconto_impede_fechar(servico):
    comanda_id = comanda_com(servico, (1, 1))
    servico.registrar_pagamento(comanda_id, "pix", 2200, CAIXA)
    servico.ajustar(comanda_id, True, 200, CAIXA)
    with pytest.raises(ErroComanda, match="passam do total"):
        servico.fechar(comanda_id, CAIXA)
    pagamento = servico.comanda(comanda_id).pagamentos[0]
    servico.remover_pagamento(comanda_id, pagamento.id, CAIXA)
    servico.registrar_pagamento(comanda_id, "pix", 2000, CAIXA)
    assert servico.fechar(comanda_id, CAIXA).total == 2000


def test_erro_no_meio_desfaz_tudo(servico, repo):
    """Se algo falha dentro da transação, nada fica gravado pela metade."""
    comanda_id = comanda_com(servico, (1, 1))
    original = repo.registrar_historico

    def falha(*_):
        raise RuntimeError("banco caiu")

    repo.registrar_historico = falha
    with pytest.raises(RuntimeError):
        servico.registrar_pagamento(comanda_id, "pix", 1000, GARCOM_AUTORIZADO)
    repo.registrar_historico = original
    assert servico.comanda(comanda_id).pagamentos == []


# -- histórico e autorização ---------------------------------------------------------------------

def test_historico_anota_quem_autorizou(servico, repo):
    comanda_id = comanda_com(servico, (1, 1))
    servico.registrar_pagamento(comanda_id, "pix", 1200, CAIXA)                  # sem autorização: não anota
    servico.registrar_pagamento(comanda_id, "pix", 1000, GARCOM_AUTORIZADO)
    servico.fechar(comanda_id, GARCOM_AUTORIZADO)
    assert [(acao, detalhe) for _, acao, detalhe, _ in repo.historico] == [
        ("pagamento", "PIX R$ 10,00 (autorizado por caixa)"),
        ("fechar conta", "autorizado por caixa"),
    ]


# -- cozinha e cancelamentos -----------------------------------------------------------------------

def test_andamento_do_item(servico):
    comanda_id = comanda_com(servico, (1, 1), (2, 1))
    lanche, lata = servico.comanda(comanda_id).itens
    servico.mudar_situacao(lanche.id, "preparando")
    with pytest.raises(ErroComanda, match="Situação"):
        servico.mudar_situacao(lanche.id, "voando")
    with pytest.raises(ErroComanda, match="não pode voltar"):
        servico.desfazer_entrega(comanda_id, lanche.id)      # ainda não foi entregue
    servico.entregar(comanda_id, lanche.id)
    servico.desfazer_entrega(comanda_id, lanche.id)
    assert servico.item(comanda_id, lanche.id).status == "pronto"
    with pytest.raises(ErroComanda, match="não pode voltar"):
        servico.desfazer_entrega(comanda_id, lata.id)        # não passa pela cozinha
    with pytest.raises(ItemNaoEncontrado):
        servico.mudar_situacao(9999, "pronto")


def test_tudo_pronto_so_mexe_no_que_esta_na_cozinha(servico):
    comanda_id = comanda_com(servico, (1, 1), (3, 1), (2, 1))
    assert servico.marcar_tudo_pronto(comanda_id) == 2
    assert [i.status for i in servico.comanda(comanda_id).itens] == ["pronto", "pronto", "entregue"]


def test_cozinha_comecou(servico):
    comanda_id = comanda_com(servico, (1, 1), (2, 1))
    lanche, lata = servico.comanda(comanda_id).itens
    assert not lanche.cozinha_comecou and not lata.cozinha_comecou
    servico.mudar_situacao(lanche.id, "preparando")
    assert servico.item(comanda_id, lanche.id).cozinha_comecou


def test_cancelar_item_pede_motivo_e_nao_cancela_duas_vezes(servico):
    comanda_id = comanda_com(servico, (1, 1))
    item = servico.comanda(comanda_id).itens[0]
    with pytest.raises(ErroComanda, match="motivo"):
        servico.cancelar_item(comanda_id, item.id, "  ", CAIXA)
    servico.cancelar_item(comanda_id, item.id, "errado", CAIXA)
    with pytest.raises(ErroComanda, match="já foi cancelado"):
        servico.cancelar_item(comanda_id, item.id, "errado", CAIXA)
    with pytest.raises(ErroComanda, match="foi cancelado"):
        servico.mudar_situacao(item.id, "pronto")


def test_cancelar_comanda_e_reabrir(servico):
    comanda_id = comanda_com(servico, (1, 1))
    servico.registrar_pagamento(comanda_id, "pix", 500, CAIXA)
    with pytest.raises(ErroComanda, match="tem pagamentos"):
        servico.cancelar(comanda_id, "desistiu", CAIXA)
    servico.remover_pagamento(comanda_id, servico.comanda(comanda_id).pagamentos[0].id, CAIXA)
    with pytest.raises(ErroComanda, match="motivo"):
        servico.cancelar(comanda_id, "", CAIXA)
    servico.cancelar(comanda_id, "desistiu", CAIXA)
    comanda = servico.comanda(comanda_id)
    assert comanda.status == "cancelada" and comanda.totais.subtotal == 0
    with pytest.raises(ErroComanda, match="Só dá para reabrir uma comanda fechada"):
        servico.reabrir(comanda_id, CAIXA)


def test_reabrir_nao_duplica_numero_aberto(servico):
    primeira = comanda_com(servico, (2, 1))
    servico.registrar_pagamento(primeira, "pix", 660, CAIXA)
    servico.fechar(primeira, CAIXA)
    comanda_com(servico, (2, 1))                               # o cartão 1 foi usado de novo
    with pytest.raises(ErroComanda, match="Já existe outra comanda 1 aberta"):
        servico.reabrir(primeira, CAIXA)


def test_comanda_inexistente(servico):
    with pytest.raises(ComandaNaoEncontrada):
        servico.comanda(12345)


def test_trocar_garcom_fica_no_historico(servico, repo):
    comanda_id = servico.abrir(3, "", "", 10, CAIXA, garcom_id=5)
    nomes = {5: "maria", 6: "joao"}
    servico.alterar_dados(comanda_id, "7", "Ana", 6, nomes, CAIXA)
    servico.alterar_dados(comanda_id, "7", "Ana", 6, nomes, CAIXA)          # sem troca: não anota de novo
    servico.alterar_dados(comanda_id, "7", "Ana", None, nomes, CAIXA)
    assert [detalhe for _, acao, detalhe, _ in repo.historico if acao == "garçom"] == ["maria → joao", "joao → ninguém"]


def test_troco_nunca_chega_a_uma_cedula_de_200(servico):
    """Valor digitado errado (ex.: 8.140.100,00 em vez de 100,00) não vira um troco absurdo."""
    comanda_id = comanda_com(servico, (1, 1))           # 22,00
    with pytest.raises(ErroComanda, match="Confira o valor recebido"):
        servico.registrar_pagamento(comanda_id, "dinheiro", 814010000, CAIXA)
    with pytest.raises(ErroComanda, match="Troco de R\\$ 200,00"):
        servico.registrar_pagamento(comanda_id, "dinheiro", 22200, CAIXA)
    pagamento = servico.registrar_pagamento(comanda_id, "dinheiro", 22199, CAIXA)   # troco de 199,99: ainda vale
    assert pagamento.troco == 19999
