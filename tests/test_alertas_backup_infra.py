"""Infraestrutura dos alertas e do backup: monitoramento no SQLite, limpeza de exibições e backup sem mídia (Comanda local)."""

import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from propagandas import db
from src.domain.backup import BackupInvalido, FormatoDoBackup
from src.infrastructure.backup import Backups
from src.infrastructure.sqlite import RepositorioDeMonitoramentoSQLite, apagar_exibicoes_anteriores


@pytest.fixture
def conexao(app):
    conexao = db.conectar(app.config["BANCO"])
    yield conexao
    conexao.close()


def test_monitoramento_e_limpeza(conexao):
    with conexao:
        conexao.execute("INSERT INTO empresas (id, nome, ativa) VALUES (2, 'Suspensa', 0)")
        conexao.execute("INSERT INTO telas (id, empresa_id, nome, codigo, ultimo_contato) VALUES (5, 1, 'Balcão', 'b-1', "
                        "'2026-10-04 12:00:00')")
        conexao.execute("INSERT INTO telas (id, empresa_id, nome, codigo) VALUES (6, 1, 'Nova', 'b-2')")
        conexao.executemany("INSERT INTO exibicoes (empresa_id, tela_id, propaganda_id, propaganda_nome, exibido_em, duracao) "
                            "VALUES (1, 5, 1, 'x', ?, 5)", [("2026-01-01 00:00:00",), ("2026-10-01 00:00:00",)])
    repo = RepositorioDeMonitoramentoSQLite(conexao)
    assert [e.id for e in repo.empresas_ativas()] == [1]
    [tela] = repo.telas_com_contato()   # a tela que nunca falou não entra
    assert (tela.id, tela.ultimo_contato, tela.avisado) == (5, datetime(2026, 10, 4, 12, tzinfo=timezone.utc), False)
    repo.marcar_aviso(5, True)
    assert repo.telas_com_contato()[0].avisado
    assert apagar_exibicoes_anteriores(conexao, datetime(2026, 6, 1, tzinfo=timezone.utc)) == 1
    assert conexao.execute("SELECT COUNT(*) FROM exibicoes").fetchone()[0] == 1


def test_backup_sem_midia_vai_e_volta(tmp_path):
    banco = tmp_path / "comanda.sqlite3"
    with sqlite3.connect(banco) as c:
        c.execute("CREATE TABLE t (v TEXT)")
        c.execute("INSERT INTO t VALUES ('antes')")
    backups = Backups(FormatoDoBackup("comanda.sqlite3", False, "da Comanda"), str(banco), str(tmp_path / "backups"),
                      str(tmp_path))
    agora = datetime(2026, 10, 4, 10, 0, 0)
    arquivo = backups.criar(agora, manter=1)
    assert zipfile.ZipFile(arquivo).namelist() == ["comanda.sqlite3"] and backups.feito_no_dia(agora.date())
    with sqlite3.connect(banco) as c:
        c.execute("UPDATE t SET v = 'depois'")
    segundo = backups.criar(agora + timedelta(minutes=1), manter=1)   # o primeiro é apagado
    assert sorted(p.name for p in (tmp_path / "backups").iterdir()) == ["backup-20261004-100100.zip"]
    with sqlite3.connect(banco) as c:
        c.execute("UPDATE t SET v = 'estragado'")
    guardados = backups.restaurar(segundo, agora + timedelta(minutes=2))
    with sqlite3.connect(banco) as c:
        assert c.execute("SELECT v FROM t").fetchone()[0] == "depois"
    assert (tmp_path / guardados / "comanda.sqlite3").exists()

    ruim = tmp_path / "ruim.zip"
    with zipfile.ZipFile(ruim, "w") as z:
        z.writestr("comanda.sqlite3", b"")
        z.writestr("../fora.txt", b"x")
    with pytest.raises(BackupInvalido):
        backups.restaurar(str(ruim), agora)
    assert not (tmp_path.parent / "fora.txt").exists()
