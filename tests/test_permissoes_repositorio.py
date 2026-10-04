"""Repositório das autorizações no SQLite de verdade: leitura simultânea do mesmo código e isolamento entre lojas."""

import threading

import pytest

from propagandas import db
from src.domain.permissoes import (
    AUTORIZACAO,
    CodigoInvalido,
    ErroDeAutorizacao,
    ServicoDeAutorizacoes,
    TabelaDePermissoes,
)
from src.infrastructure.sqlite import RepositorioDePermissoesSQLite

LOJA = {"painel", "comanda"}
NIVEIS = {("fechar_conta", "garcom"): AUTORIZACAO}


@pytest.fixture
def banco(app):
    """Duas lojas: na 1, um caixa (10) e oito garçons (11..18); na 2, um garçom (20)."""
    caminho = app.config["BANCO"]
    conexao = db.conectar(caminho)
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Outra loja', 'outra-loja')")
        pessoas = [(10, 1, "caixa", "caixa")] + [(i, 1, f"garcom{i}", "garcom") for i in range(11, 19)]
        pessoas.append((20, 2, "garcom-da-outra", "garcom"))
        conexao.executemany(
            "INSERT INTO usuarios (id, empresa_id, usuario, senha_hash, papel, token_sessao) VALUES (?, ?, ?, 'x', ?, 't')",
            pessoas,
        )
    conexao.close()
    return caminho


def servico(caminho, empresa_id=1):
    conexao = db.conectar(caminho)
    repo = RepositorioDePermissoesSQLite(conexao, empresa_id)
    repo.gravar_niveis(NIVEIS)
    return ServicoDeAutorizacoes(repo, TabelaDePermissoes(repo.niveis_configurados()), LOJA), conexao


def test_niveis_gravados_voltam_iguais_e_ficam_na_loja(banco):
    loja, conexao = servico(banco)
    assert loja._repo.niveis_configurados() == NIVEIS
    loja._repo.gravar_niveis({("fechar_conta", "garcom"): 0, ("desconto", "caixa"): 2})
    assert loja._repo.niveis_configurados() == {("fechar_conta", "garcom"): 0, ("desconto", "caixa"): 2}
    assert RepositorioDePermissoesSQLite(conexao, 3).niveis_configurados() == {}
    conexao.close()


def test_oito_garcons_lendo_o_mesmo_codigo_so_um_consegue(banco):
    loja, conexao = servico(banco)
    codigo = loja.gerar_codigo(loja.pessoa(10), "fechar_conta", "minutos", 15).codigo
    conexao.close()
    largada = threading.Barrier(8)
    resultados = []

    def ler(garcom_id):
        outra, conexao = servico(banco)
        largada.wait()
        try:
            resultados.append(outra.usar_codigo(codigo, outra.pessoa(garcom_id)).usado_por)
        except ErroDeAutorizacao:
            resultados.append(None)
        finally:
            conexao.close()

    garcons = [threading.Thread(target=ler, args=(i,)) for i in range(11, 19)]
    for garcom in garcons:
        garcom.start()
    for garcom in garcons:
        garcom.join()
    vencedores = [r for r in resultados if r is not None]
    assert len(vencedores) == 1
    loja, conexao = servico(banco)
    liberacao = loja.liberacao_vigente(loja.pessoa(vencedores[0]), "fechar_conta")
    assert (liberacao.quem_autorizou, liberacao.descricao) == ("caixa", "por 15 minutos")
    assert liberacao.ate is not None
    assert [lib.quem_usou for lib in loja.ativas(loja.pessoa(10))] == [f"garcom{vencedores[0]}"]
    conexao.close()


def test_outra_loja_nao_usa_nem_ve_o_codigo(banco):
    loja, conexao = servico(banco)
    caixa = loja.pessoa(10)
    codigo = loja.gerar_codigo(caixa, "fechar_conta", "sempre", None).codigo
    outra, conexao_outra = servico(banco, empresa_id=2)
    assert outra.pessoa(10) is None            # o caixa não existe para a outra loja
    assert outra.pessoa(11) is None
    with pytest.raises(CodigoInvalido):
        outra.usar_codigo(codigo, outra.pessoa(20))
    liberacao = loja.usar_codigo(codigo, loja.pessoa(11))
    assert outra._repo.por_id(liberacao.id) is None
    outra._repo.revogar(liberacao.id, liberacao.usado_em, 20)   # tentar encerrar pela outra loja: nada muda
    outra._repo.consumir([liberacao.id], liberacao.usado_em)
    assert loja.liberacao_vigente(loja.pessoa(11), "fechar_conta").id == liberacao.id
    assert outra._repo.ativas(liberacao.usado_em, None) == [] and outra._repo.recentes(None, 15) == []
    conexao.close()
    conexao_outra.close()


def test_gastar_e_encerrar_ficam_gravados(banco):
    loja, conexao = servico(banco)
    caixa, garcom = loja.pessoa(10), loja.pessoa(11)
    uma = loja.usar_codigo(loja.gerar_codigo(caixa, "fechar_conta", "uma", None).codigo, garcom)
    sempre = loja.usar_codigo(loja.gerar_codigo(caixa, "fechar_conta", "sempre", None).codigo, loja.pessoa(12))
    loja.gastar([uma.id])
    loja.encerrar(sempre.id, caixa)
    linhas = {r["id"]: r for r in conexao.execute("SELECT * FROM autorizacoes")}
    assert linhas[uma.id]["consumida_em"] and not linhas[uma.id]["revogada_em"]
    assert linhas[sempre.id]["revogada_em"] and linhas[sempre.id]["encerrada_por"] == 10
    assert loja.ativas(caixa) == []
    assert {lib.id for lib in loja.recentes(caixa)} == {uma.id, sempre.id}
    conexao.close()
