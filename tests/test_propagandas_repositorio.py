"""Repositório das propagandas no SQLite de verdade: isolamento entre lojas, destinos e exibições sem duplicar."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from propagandas import db
from src.domain.erros import NaoEncontrado
from src.domain.painel import Destinos, Programacao, ServicoDePropagandas, TelaDaPlaylist
from src.infrastructure.sqlite import RepositorioDePropagandasSQLite

FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def banco(app):
    """Loja 1: telas 10 e 11, grupo 7. Loja 2: tela 20, grupo 8."""
    caminho = app.config["BANCO"]
    conexao = db.conectar(caminho)
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Outra loja', 'outra-loja')")
        conexao.executemany("INSERT INTO grupos (id, empresa_id, nome) VALUES (?, ?, ?)", [(7, 1, "SP"), (8, 2, "RJ")])
        conexao.executemany("INSERT INTO telas (id, empresa_id, nome, codigo, grupo_id) VALUES (?, ?, ?, ?, ?)",
                            [(10, 1, "Balcão", "balcao-a", 7), (11, 1, "Vitrine", "vitrine-a", None),
                             (20, 2, "Outra", "outra-a", 8)])
    conexao.close()
    return caminho


def servico(caminho, empresa_id=1):
    conexao = db.conectar(caminho)
    return ServicoDePropagandas(RepositorioDePropagandasSQLite(conexao, empresa_id), FUSO, relogio=lambda: AGORA), conexao


def test_cadastro_destinos_e_ordem_ficam_gravados(banco):
    loja, conexao = servico(banco)
    a = loja.cadastrar("a.png", "a.png", "imagem", 10, 5, None)
    b = loja.cadastrar("b.png", "b.png", "imagem", 10, 5, Destinos(frozenset({10, 20}), frozenset({7, 8})))
    assert loja.propaganda(b).destinos == Destinos(frozenset({10}), frozenset({7}))   # 20 e 8 são da outra loja
    loja.mover(b, "cima")
    assert [p.id for p in loja.lista()[0]] == [b, a]
    prog = Programacao("B", 30, True, "2026-10-01", None, "0123456", None, None, False, "", Destinos(frozenset({11})))
    loja.atualizar(b, prog)
    gravada = loja.propaganda(b)
    assert (gravada.nome, gravada.duracao, gravada.letreiro, gravada.destinos) == ("B", 30, "", Destinos(frozenset({11})))
    assert [p.id for p in loja.playlist(TelaDaPlaylist(11)).itens] == [b, a]
    assert [p.id for p in loja.playlist(TelaDaPlaylist(10, 7)).itens] == [a]
    loja.definir_destinos_em_lote(loja.marcadas([a, b]), "trocar", None)
    assert conexao.execute("SELECT COUNT(*) FROM propaganda_destinos").fetchone()[0] == 0
    conexao.close()


def test_outra_loja_nao_ve_nem_altera(banco):
    loja, conexao = servico(banco)
    pid = loja.cadastrar("a.png", "a.png", "imagem", 10, 5, Destinos(frozenset({10})))
    outra, conexao_outra = servico(banco, empresa_id=2)
    assert outra.lista()[0] == [] and outra._repo.buscar(pid) is None
    for tentativa in (lambda: outra.excluir(pid), lambda: outra.mover(pid, "cima"),
                      lambda: outra.atualizar(pid, Programacao("x", 1, True, None, None, "0", None, None, True, None))):
        with pytest.raises(NaoEncontrado):
            tentativa()
    outra._repo.definir([pid], "ativo", False)
    outra._repo.gravar_destinos(pid, Destinos(frozenset({20})))
    outra._repo.gravar_ordem([pid])
    outra.pausar(True)
    assert not loja.pausado
    propaganda = loja.propaganda(pid)
    assert propaganda.ativo and propaganda.destinos == Destinos(frozenset({10})) and propaganda.posicao == 1
    assert outra.playlist(TelaDaPlaylist(20, 8)).itens == []
    conexao.close()
    conexao_outra.close()


def test_tv_reenviando_o_mesmo_registro_nao_duplica(banco):
    loja, conexao = servico(banco)
    pid = loja.cadastrar("a.png", "a.png", "imagem", 10, 5, None)
    registros = [{"propaganda_id": pid, "duracao": 5, "inicio": "2026-10-01T14:59:00Z"},
                 {"propaganda_id": pid, "duracao": 5, "inicio": "2026-10-01T14:59:05Z"}]
    assert loja.registrar_exibicoes(10, registros, reter_dias=90) == (2, 2)
    assert loja.registrar_exibicoes(10, registros, reter_dias=90) == (2, 2)   # queda de rede: reenviou
    linhas = conexao.execute("SELECT empresa_id, tela_id, propaganda_nome, exibido_em FROM exibicoes ORDER BY id").fetchall()
    assert [tuple(r) for r in linhas] == [(1, 10, "a.png", "2026-10-01 14:59:00"), (1, 10, "a.png", "2026-10-01 14:59:05")]
    loja.excluir(pid)
    assert conexao.execute("SELECT COUNT(*) FROM exibicoes").fetchone()[0] == 2   # o relatório continua
    conexao.close()
