"""Empresas no SQLite de verdade: módulos do plano, uso, código disputado, cadastro gravado só nas colunas certas e a
plataforma criando, suspendendo e excluindo (em cascata) uma empresa."""

import pytest

from propagandas import db
from src.domain.empresas import CodigoEmUso, Configuracoes, DadosDaEmpresa, Limites, ServicoDaPlataforma, ServicoDeEmpresas
from src.infrastructure.sqlite import RepositorioDeEmpresasSQLite


@pytest.fixture
def conexao(app):
    conexao = db.conectar(app.config["BANCO"])
    with conexao:
        conexao.execute("INSERT INTO planos (id, nome, preco_centavos, modulos) VALUES (5, 'Comanda', 9900, 'comanda')")
        conexao.execute("INSERT INTO empresas (id, nome, slug, plano_id, limite_telas, limite_mb) "
                        "VALUES (2, 'Lanchonete', 'lanchonete', 5, 1, 1)")
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (3, 'Padaria', 'padaria')")
    yield conexao
    conexao.close()


def test_modulos_limites_e_uso(conexao):
    repo = RepositorioDeEmpresasSQLite(conexao)
    empresas = ServicoDeEmpresas(repo, 1)
    assert empresas.modulos(2) == {"comanda"} and empresas.modulos(3) == set() and empresas.modulos(1) == {"painel", "comanda"}
    with conexao:
        conexao.execute("INSERT INTO telas (empresa_id, nome, codigo) VALUES (2, 'Balcão', 'balcao-z')")
        conexao.execute("INSERT INTO propagandas (empresa_id, nome, arquivo, tipo, tamanho, duracao, posicao) VALUES (2, 'a', 'a.png', 'imagem', 1000, 10, 1)")
    assert repo.uso(2).telas == 1 and repo.uso(2).bytes == 1000 and repo.uso(3).telas == 0
    assert not empresas.cabe_mais_uma_tela(2) and empresas.cabe_mais_uma_tela(3)


def test_configuracoes_e_codigo(conexao):
    repo = RepositorioDeEmpresasSQLite(conexao)
    empresas = ServicoDeEmpresas(repo, 1)
    empresas.salvar_configuracoes(2, Configuracoes("Lanchonete do Zé", "zeca", "a@b.com", "", {"uf": "MA", "bairro": "Centro"}))
    linha = conexao.execute("SELECT nome, slug, alerta_emails, uf, bairro FROM empresas WHERE id = 2").fetchone()
    assert tuple(linha) == ("Lanchonete do Zé", "zeca", "a@b.com", "MA", "Centro")
    with pytest.raises(CodigoEmUso):   # duas lojas gravando o mesmo código ao mesmo tempo: o banco desempata
        repo.salvar_configuracoes(3, "Padaria", "zeca", "", "", {})
    with pytest.raises(ValueError, match="coluna desconhecida"):
        repo.salvar_configuracoes(3, "Padaria", "padaria", "", "", {"senha_hash": "x"})
    assert conexao.execute("SELECT slug FROM empresas WHERE id = 3").fetchone()[0] == "padaria"   # nada gravado
    assert empresas.codigo_livre("Zeca") == "zeca-2"
    assert db.gerar_slug(conexao, "Padaria") == "padaria-2" and db.gerar_slug(conexao, "Padaria", 3) == "padaria"


def test_plataforma_cria_atualiza_e_exclui(conexao):
    repo = RepositorioDeEmpresasSQLite(conexao)
    plataforma = ServicoDaPlataforma(repo, 1, ServicoDeEmpresas(repo, 1).codigo_livre)
    empresa_id, _ = plataforma.criar(DadosDaEmpresa("Lanchonete", Limites(2, None), "comanda", {"cidade": "Dom Pedro"}),
                                     lambda _id: None)
    linha = conexao.execute("SELECT slug, limite_telas, limite_mb, modulos_liberados, cidade FROM empresas WHERE id = ?",
                            (empresa_id,)).fetchone()
    assert tuple(linha) == ("lanchonete-2", 2, None, "comanda", "Dom Pedro")
    plataforma.atualizar(empresa_id, DadosDaEmpresa("Lanchonete Nova", Limites(None, 50), ""), ativa=False)
    assert repo.cliente(empresa_id).motivo_suspensao == "manual" and not repo.cliente(empresa_id).ativa
    with conexao:
        conexao.execute("INSERT INTO propagandas (empresa_id, nome, arquivo, tipo, tamanho, duracao, posicao) "
                        "VALUES (?, 'a', 'x.png', 'imagem', 1, 10, 1)", (empresa_id,))
    apagados = []
    plataforma.excluir(empresa_id, "Lanchonete Nova", lambda _id: None, apagados.append)
    assert apagados == ["x.png"] and repo.cliente(empresa_id) is None
    assert conexao.execute("SELECT COUNT(*) FROM propagandas WHERE empresa_id = ?", (empresa_id,)).fetchone()[0] == 0
