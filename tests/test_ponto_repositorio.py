"""Repositório do ponto no SQLite de verdade: QR lido ao mesmo tempo, ponto aberto uma vez só, isolamento entre lojas."""

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from propagandas import db
from src.domain.ponto import ErroDePonto, ServicoDePonto
from src.infrastructure.sqlite import RepositorioDePontoSQLite

FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc)   # sexta, 10:00 em São Paulo


@pytest.fixture
def banco(app):
    """Loja 1 com oito pessoas (11..18); loja 2 com uma (20)."""
    caminho = app.config["BANCO"]
    conexao = db.conectar(caminho)
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, slug) VALUES (2, 'Outra loja', 'outra-loja')")
        pessoas = [(i, 1, f"pessoa{i}") for i in range(11, 19)] + [(20, 2, "da-outra")]
        conexao.executemany(
            "INSERT INTO usuarios (id, empresa_id, usuario, senha_hash, papel, token_sessao) VALUES (?, ?, ?, 'x', 'caixa', 't')",
            pessoas,
        )
    conexao.close()
    return caminho


def servico(caminho, empresa_id=1):
    conexao = db.conectar(caminho)
    ponto = ServicoDePonto(RepositorioDePontoSQLite(conexao, empresa_id), FUSO, relogio=lambda: AGORA)
    return ponto, conexao


def test_oito_pessoas_lendo_o_mesmo_qr_so_uma_leva(banco):
    ponto, conexao = servico(banco)
    ponto.ligar(True)
    token = ponto.token_atual()
    conexao.close()
    largada, resultados = threading.Barrier(8), []

    def ler():
        outro, conexao = servico(banco)
        largada.wait()
        try:
            outro.usar_token(token)
            resultados.append("leu")
        except ErroDePonto as erro:
            resultados.append(type(erro).__name__)
        finally:
            conexao.close()

    leitores = [threading.Thread(target=ler) for _ in range(8)]
    for leitor in leitores:
        leitor.start()
    for leitor in leitores:
        leitor.join()
    assert resultados.count("leu") == 1 and set(resultados) == {"leu", "QrJaUsado"}
    ponto, conexao = servico(banco)
    assert ponto.geracao == 1 and ponto.token_atual() != token
    conexao.close()


def test_entrada_tocada_duas_vezes_abre_um_ponto_so(banco):
    ponto, conexao = servico(banco)
    ponto.ligar(True)
    ponto.exigir_qr(False)
    pessoa = ponto._repo.funcionario(11)
    assert ponto.registrar_entrada(pessoa, leu_o_qr=False, ip="10.0.0.1")
    assert not ponto._repo.abrir(pessoa, AGORA, "10.0.0.1")   # o índice único segura o segundo
    assert conexao.execute("SELECT COUNT(*) FROM ponto_registros").fetchone()[0] == 1
    assert ponto.registrar_saida(pessoa, leu_o_qr=False)
    [registro] = ponto.historico(AGORA - timedelta(hours=1), AGORA + timedelta(hours=1))
    assert (registro.usuario_nome, registro.motivo_saida) == ("pessoa11", "saída")
    conexao.close()


def test_outra_loja_nao_ve_nem_fecha_o_ponto(banco):
    ponto, conexao = servico(banco)
    ponto.ligar(True)
    ponto.exigir_qr(False)
    ponto.registrar_entrada(ponto._repo.funcionario(11), leu_o_qr=False)
    outra, conexao_outra = servico(banco, empresa_id=2)
    assert outra._repo.funcionario(11) is None
    assert outra._repo.aberto(11) is None
    assert outra._repo.fechar(11, AGORA, "x", None) == 0
    assert outra._repo.funcionarios_com_ponto_aberto() == []
    assert outra.historico(AGORA - timedelta(days=1), AGORA + timedelta(days=1)) == []
    assert not outra.ativo                                     # os ajustes também são de cada loja
    outra._repo.gravar_horario(11, ponto._repo.funcionario(11).horario, exige_ponto=False)
    outra._repo.encerrar_sessoes(11)
    linha = conexao.execute("SELECT exige_ponto, token_sessao FROM usuarios WHERE id = 11").fetchone()
    assert tuple(linha) == (1, "t")
    assert ponto.aberto(11) is not None
    conexao.close()
    conexao_outra.close()
