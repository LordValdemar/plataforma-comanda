"""Repositórios das telas no SQLite de verdade: conexão da TV de ponta a ponta, corrida e isolamento entre lojas."""

import threading
from datetime import datetime, timezone

import pytest

from propagandas import db
from src.domain.erros import NaoEncontrado
from src.domain.painel import ErroDeTela, PedidoVencido, ServicoDeConexao, ServicoDeTelas
from src.infrastructure.sqlite import RepositorioDeConexoesSQLite, RepositorioDeTelasSQLite

AGORA = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def banco(app):
    caminho = app.config["BANCO"]
    conexao = db.conectar(caminho)
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Outra loja', 'outra-loja')")
        conexao.execute("INSERT INTO grupos (id, empresa_id, nome) VALUES (8, 2, 'Da outra')")
    conexao.close()
    return caminho


def loja(caminho, empresa_id=1):
    conexao = db.conectar(caminho)
    return ServicoDeTelas(RepositorioDeTelasSQLite(conexao, empresa_id), relogio=lambda: AGORA), conexao


def tv(caminho):
    conexao = db.conectar(caminho)
    return ServicoDeConexao(RepositorioDeConexoesSQLite(conexao), relogio=lambda: AGORA), conexao


def test_tv_conectada_pelo_qr_de_ponta_a_ponta(banco):
    telas, c1 = loja(banco)
    aparelho, c2 = tv(banco)
    balcao = telas.cadastrar("Balcão", 8, cabe_no_plano=True)          # o grupo 8 é da outra loja
    assert balcao.grupo_id is None and balcao.codigo.startswith("balcao-")
    codigo, segredo = aparelho.abrir_pedido()
    assert aparelho.situacao_do_pedido(segredo) == ("aguardando", None)
    assert telas.conectar(codigo.lower(), balcao.id).id == balcao.id
    estado, tela = aparelho.situacao_do_pedido(segredo)
    assert estado == "pronto" and tela.id == balcao.id
    assert aparelho.autorizar(aparelho.tela(balcao.codigo), segredo) == (True, None)   # o segredo virou o crachá
    assert aparelho.autorizar(aparelho.tela(balcao.codigo), "outro") == (False, None)
    assert aparelho.telas_do_aparelho({balcao.id: segredo})[0].id == balcao.id
    with pytest.raises(PedidoVencido):
        telas.pedido(codigo)                                             # o pedido acabou
    aparelho.registrar_contato(tela, "10.0.0.1", "TV", exibindo="Café", mudar_exibindo=True)
    linha = c1.execute("SELECT ultimo_ip, navegador, exibindo, fechada_em FROM telas").fetchone()
    assert tuple(linha) == ("10.0.0.1", "TV", "Café", None)
    aparelho.marcar_fechada(tela)
    assert c1.execute("SELECT exibindo, fechada_em IS NOT NULL FROM telas").fetchone()[:] == (None, 1)
    c1.close()
    c2.close()


def test_duas_tvs_numa_tela_antiga_so_uma_fica(banco):
    telas, conexao = loja(banco)
    antiga = telas.cadastrar("Antiga", None, cabe_no_plano=True)
    with conexao:
        conexao.execute("UPDATE telas SET aceita_link = 1 WHERE id = ?", (antiga.id,))
    conexao.close()
    largada, resultados = threading.Barrier(6), []

    def chegar(numero):
        aparelho, conexao = tv(banco)
        tela = aparelho.tela(antiga.codigo)
        largada.wait()
        resultados.append(aparelho.autorizar(tela, f"cracha-{numero}"))
        conexao.close()

    aparelhos = [threading.Thread(target=chegar, args=(i,)) for i in range(6)]
    for a in aparelhos:
        a.start()
    for a in aparelhos:
        a.join()
    vencedores = [cracha for pode, cracha in resultados if pode]
    assert len(vencedores) == 1
    aparelho, conexao = tv(banco)
    assert aparelho.autorizar(aparelho.tela(antiga.codigo), vencedores[0]) == (True, None)
    conexao.close()


def test_outra_loja_nao_mexe_nas_telas(banco):
    telas, c1 = loja(banco)
    balcao = telas.cadastrar("Balcão", None, cabe_no_plano=True)
    outra, c2 = loja(banco, empresa_id=2)
    assert outra.telas() == []
    for tentativa in (lambda: outra.atualizar(balcao.id, "x", None, None), lambda: outra.excluir(balcao.id),
                      lambda: outra.trocar_endereco(balcao.id), lambda: outra.desconectar_aparelho(balcao.id)):
        with pytest.raises(NaoEncontrado):
            tentativa()
    aparelho, c3 = tv(banco)
    codigo, _ = aparelho.abrir_pedido()
    with pytest.raises(ErroDeTela, match="Escolha uma das telas"):
        outra.conectar(codigo, balcao.id)                                # não liga a TV na tela de outra loja
    outra._repo.ligar_aparelho(balcao.id, outra.pedido(codigo), AGORA)  # nem direto pelo repositório
    assert not telas.tela(balcao.id).conectada
    assert c1.execute("SELECT tela_id FROM pareamentos").fetchone()[0] is None
    with pytest.raises(NaoEncontrado):
        telas.excluir_grupo(8)                                           # o grupo 8 é da outra loja
    assert c1.execute("SELECT COUNT(*) FROM grupos WHERE id = 8").fetchone()[0] == 1
    c1.close()
    c2.close()
    c3.close()
