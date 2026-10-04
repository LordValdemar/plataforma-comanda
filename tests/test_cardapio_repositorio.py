"""Cardápio no SQLite de verdade: repetidos, ordem, categoria excluída e isolamento entre lojas."""

import pytest

from propagandas import db
from src.domain.cardapio import CategoriaRepetida, CodigoRepetido, DadosDoProduto, ServicoDeCardapio
from src.domain.erros import NaoEncontrado
from src.infrastructure.sqlite import RepositorioDeCardapioSQLite


@pytest.fixture
def lojas(app):
    conexao = db.conectar(app.config["BANCO"])
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Outra', 'outra')")
    yield (ServicoDeCardapio(RepositorioDeCardapioSQLite(conexao, 1)), ServicoDeCardapio(RepositorioDeCardapioSQLite(conexao, 2)),
           conexao)
    conexao.close()


def test_repetidos_e_ordem(lojas):
    loja, outra, _ = lojas
    for nome in ("Lanches", "Bebidas", "Doces"):
        loja.criar_categoria(nome)
    with pytest.raises(CategoriaRepetida, match="“BEBIDAS” já existe"):
        loja.criar_categoria("BEBIDAS")              # maiúsculas não importam
    outra.criar_categoria("Bebidas")                 # outra loja pode ter a mesma
    ids = [c.id for c in loja.categorias()]
    with pytest.raises(CategoriaRepetida):
        loja.renomear_categoria(ids[2], "lanches")
    loja.mover_categoria(ids[2], para_cima=True)
    assert [c.nome for c in loja.categorias()] == ["Lanches", "Doces", "Bebidas"]
    loja.criar_produto(DadosDoProduto("X-Burger", 2500, "12", ids[0], True))
    with pytest.raises(CodigoRepetido):
        loja.criar_produto(DadosDoProduto("X-Salada", 2600, "12", None, True))
    outra.criar_produto(DadosDoProduto("Pão", 100, "12", None, False))   # o código vale por loja


def test_ordem_do_cardapio_e_categoria_excluida(lojas):
    loja, _, _ = lojas
    loja.criar_categoria("Bebidas")
    loja.criar_categoria("Lanches")
    bebidas, lanches = (c.id for c in loja.categorias())
    loja.criar_produto(DadosDoProduto("Suco", 800, None, bebidas, False))
    loja.criar_produto(DadosDoProduto("X-Burger", 2500, None, lanches, True))
    sem = loja.criar_produto(DadosDoProduto("Bala", 50, None, None, False))
    fora = loja.criar_produto(DadosDoProduto("Antigo", 10, None, bebidas, False))
    loja.alternar_ativo(fora)
    assert [(g, [p.nome for p in itens]) for g, itens in loja.grupos_a_venda()] == \
        [("Bebidas", ["Suco"]), ("Lanches", ["X-Burger"]), ("Outros", ["Bala"])]
    assert [p.nome for p in loja.produtos()][-1] == "Antigo"          # fora do cardápio por último
    assert next(c for c in loja.categorias() if c.id == bebidas).produtos == 2
    loja.excluir_categoria(bebidas)
    assert next(p for p in loja.produtos() if p.nome == "Suco").categoria is None
    assert loja.excluir_produto(sem).nome == "Bala"


def test_outra_loja_nao_mexe(lojas):
    loja, outra, conexao = lojas
    loja.criar_categoria("Lanches")
    categoria = loja.categorias()[0].id
    produto = loja.criar_produto(DadosDoProduto("X-Burger", 2500, None, categoria, True))
    assert outra.categorias() == [] and outra.produtos() == []
    for operacao in (lambda: outra.excluir_produto(produto), lambda: outra.alternar_ativo(produto),
                     lambda: outra.excluir_categoria(categoria), lambda: outra.renomear_categoria(categoria, "x")):
        with pytest.raises(NaoEncontrado):
            operacao()
    with pytest.raises(Exception, match="Categoria não encontrada"):
        outra.criar_produto(DadosDoProduto("Pão", 100, None, categoria, False))
    assert conexao.execute("SELECT COUNT(*) FROM cmd_produtos").fetchone()[0] == 1
