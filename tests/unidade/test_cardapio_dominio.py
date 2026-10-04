"""Regras do cardápio sem banco: produto digitado, categorias em ordem, agrupar para a tela."""

from dataclasses import replace

import pytest

from src.domain.cardapio import (
    Categoria,
    CategoriaRepetida,
    DadosDoProduto,
    ErroDeCardapio,
    Produto,
    ServicoDeCardapio,
    agrupar,
    nome_de_categoria,
)
from src.domain.dinheiro import ValorInvalido
from src.domain.erros import NaoEncontrado
from src.domain.ordem import trocar_com_vizinho


def test_produto_digitado():
    dados = DadosDoProduto.do_formulario({"nome": " X-Burger ", "preco": "25,90", "codigo": " 12 ", "categoria_id": "3",
                                          "vai_cozinha": "on"})
    assert dados == DadosDoProduto("X-Burger", 2590, "12", 3, True)
    assert DadosDoProduto.do_formulario({"nome": "Lata", "preco": "6"}) == DadosDoProduto("Lata", 600, None, None, False)
    for campos, erro in [({"nome": "", "preco": "1"}, ErroDeCardapio), ({"nome": "x" * 81, "preco": "1"}, ErroDeCardapio),
                         ({"nome": "x", "preco": "abc"}, ValorInvalido), ({"nome": "x", "preco": "1", "codigo": "12345678901"},
                                                                           ErroDeCardapio),
                         ({"nome": "x", "preco": "1", "categoria_id": "abc"}, ErroDeCardapio)]:
        with pytest.raises(erro):
            DadosDoProduto.do_formulario(campos)
    assert nome_de_categoria("  Bebidas ") == "Bebidas"
    with pytest.raises(ErroDeCardapio):
        nome_de_categoria("x" * 41)


def test_ordem_e_agrupar():
    assert trocar_com_vizinho([1, 2, 3], 3, para_cima=True) == [1, 3, 2]
    assert trocar_com_vizinho([1, 2, 3], 3, para_cima=False) is None
    produtos = [Produto(1, "Suco", 800, categoria="Bebidas"), Produto(2, "Água", 400, categoria="Bebidas"),
                Produto(3, "Bolo", 900, categoria="Doces"), Produto(4, "Sem", 100)]
    assert [(nome, [p.id for p in itens]) for nome, itens in agrupar(produtos)] == \
        [("Bebidas", [1, 2]), ("Doces", [3]), ("Outros", [4])]


class Repo:
    def __init__(self):
        self.categorias_ = {1: Categoria(1, "Lanches", 1), 2: Categoria(2, "Bebidas", 2)}
        self.produtos_ = {}

    def categorias(self):
        return sorted(self.categorias_.values(), key=lambda c: c.posicao)

    def categoria(self, categoria_id):
        return self.categorias_.get(categoria_id)

    def inserir_categoria(self, nome):
        if any(c.nome.lower() == nome.lower() for c in self.categorias_.values()):
            raise CategoriaRepetida(nome)
        novo = max(self.categorias_) + 1
        self.categorias_[novo] = Categoria(novo, nome, novo)
        return novo

    def renomear_categoria(self, categoria_id, nome):
        self.categorias_[categoria_id] = replace(self.categorias_[categoria_id], nome=nome)

    def gravar_ordem_das_categorias(self, ids):
        for posicao, ident in enumerate(ids, start=1):
            self.categorias_[ident] = replace(self.categorias_[ident], posicao=posicao)

    def excluir_categoria(self, categoria_id):
        del self.categorias_[categoria_id]

    def produtos(self, so_ativos):
        return [p for p in self.produtos_.values() if p.ativo or not so_ativos]

    def produto(self, produto_id):
        return self.produtos_.get(produto_id)

    def inserir_produto(self, dados):
        novo = len(self.produtos_) + 1
        self.produtos_[novo] = Produto(novo, dados.nome, dados.preco_centavos, dados.codigo, dados.categoria_id)
        return novo

    def atualizar_produto(self, produto_id, dados):
        self.produtos_[produto_id] = replace(self.produtos_[produto_id], nome=dados.nome, preco_centavos=dados.preco_centavos)

    def definir_ativo(self, produto_id, ativo):
        self.produtos_[produto_id] = replace(self.produtos_[produto_id], ativo=ativo)

    def excluir_produto(self, produto_id):
        del self.produtos_[produto_id]


def test_servico_do_cardapio():
    repo = Repo()
    cardapio = ServicoDeCardapio(repo)
    assert cardapio.criar_categoria(" Doces ") == "Doces"
    with pytest.raises(CategoriaRepetida, match="já existe"):
        cardapio.criar_categoria("doces")
    cardapio.mover_categoria(3, para_cima=True)
    cardapio.mover_categoria(1, para_cima=True)          # já é a primeira
    assert [c.nome for c in cardapio.categorias()] == ["Lanches", "Doces", "Bebidas"]
    with pytest.raises(ErroDeCardapio, match="Categoria não encontrada"):
        cardapio.criar_produto(DadosDoProduto("X", 100, None, 99, True))   # de outra loja ou inexistente
    produto = cardapio.criar_produto(DadosDoProduto("X-Burger", 2500, "1", 1, True))
    assert cardapio.alternar_ativo(produto).ativo and cardapio.a_venda() == []
    cardapio.alternar_ativo(produto)
    cardapio.atualizar_produto(produto, DadosDoProduto("X-Tudo", 3000, "1", 1, True))
    assert [g[0] for g in cardapio.grupos_a_venda()] == ["Outros"]       # o repositório falso não traz o nome
    for operacao in (lambda: cardapio.excluir_produto(99), lambda: cardapio.renomear_categoria(99, "x"),
                     lambda: cardapio.mover_categoria(99, True), lambda: cardapio.atualizar_produto(99, None)):
        with pytest.raises(NaoEncontrado):
            operacao()
    assert cardapio.excluir_categoria(1).nome == "Lanches" and cardapio.excluir_produto(produto).nome == "X-Tudo"
