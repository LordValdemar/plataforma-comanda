"""Regras dos alertas (canais, online, quando avisar) e do backup (nomes, quantos guardar, conteúdo aceito), sem banco."""

from datetime import date, datetime, timedelta, timezone

import pytest

from src.domain.alertas import Canais, EmpresaAvisada, MonitorDeTelas, TelaMonitorada, canais, esta_online
from src.domain.backup import BackupInvalido, FormatoDoBackup, a_apagar, feito_no_dia, nome_do_backup

AGORA = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


def test_canais():
    assert canais("", "", True, padrao_emails="dono@x.com", padrao_webhook="https://p") == Canais(("dono@x.com",), "https://p")
    assert canais(" a@b.com , c@d.com ,", "", True).nomes == ["e-mail"]
    assert canais("a@b.com", "https://w", smtp_configurado=False) == Canais((), "https://w")   # sem SMTP, só o webhook
    assert Canais().nomes == [] and Canais(("a@b",), "https://w").nomes == ["e-mail", "webhook"]


def test_online():
    assert esta_online(AGORA - timedelta(seconds=30), None, AGORA)
    assert not esta_online(AGORA - timedelta(seconds=80), None, AGORA)
    assert not esta_online(None, None, AGORA)
    assert not esta_online(AGORA - timedelta(seconds=5), AGORA - timedelta(seconds=1), AGORA)   # janela fechada
    assert esta_online(AGORA - timedelta(seconds=5), AGORA - timedelta(seconds=9), AGORA)        # reabriu depois


class Monitoramento:
    def __init__(self, telas):
        self.telas = telas
        self.marcas = []

    def empresas_ativas(self):
        return [EmpresaAvisada(1, "Loja", "", "")]

    def telas_com_contato(self):
        return self.telas

    def marcar_aviso(self, tela_id, avisado):
        self.marcas.append((tela_id, avisado))


def test_monitor_avisa_uma_vez_quando_cai_e_quando_volta():
    def tela(id, minutos, avisado, empresa=1):
        return TelaMonitorada(id, f"Tela {id}", empresa, AGORA - timedelta(minutes=minutos), avisado)
    repo = Monitoramento([tela(1, 10, False), tela(2, 10, True), tela(3, 1, True), tela(4, 1, False), tela(5, 10, False, empresa=9)])
    avisos = []
    monitor = MonitorDeTelas(repo, lambda mensagem, empresa: avisos.append((mensagem, empresa.id)), 5, lambda: AGORA,
                             lambda momento: f"{momento:%H:%M}")
    assert monitor.verificar() == 2
    assert avisos == [("[Loja] ⚠️ A tela “Tela 1” está sem comunicação desde 14:50.", 1),
                      ("[Loja] ✅ A tela “Tela 3” voltou a funcionar.", 1)]
    assert repo.marcas == [(1, True), (3, False)]   # tela 2 já avisada, 4 normal, 5 de empresa suspensa


def test_nomes_e_quantos_backups_guardar():
    assert nome_do_backup(datetime(2026, 1, 2, 3, 4, 5)) == "backup-20260102-030405.zip"
    nomes = ["backup-20260103-000000.zip", "backup-20260101-000000.zip", "backup-20260102-000000.zip", "outro.zip",
             "backup-20260104-000000.zip.parcial"]
    assert a_apagar(nomes, 2) == ["backup-20260101-000000.zip"]
    assert a_apagar(nomes, 0) == ["backup-20260101-000000.zip", "backup-20260102-000000.zip"]   # o mais novo sempre fica
    assert feito_no_dia(nomes, date(2026, 1, 2)) and not feito_no_dia(nomes, date(2026, 1, 4))


def test_conteudo_do_backup():
    painel = FormatoDoBackup("banco.sqlite3", com_midia=True, sistema="do Painel")
    painel.conferir(["banco.sqlite3", "midia/", "midia/a.png"])
    for nomes in (["midia/a.png"], ["banco.sqlite3", "midia/../../etc/passwd"], ["banco.sqlite3", "midia/.oculto"],
                  ["banco.sqlite3", "midia/sub/a.png"], ["banco.sqlite3", "outro.txt"]):
        with pytest.raises(BackupInvalido):
            painel.conferir(nomes)
    comanda = FormatoDoBackup("comanda.sqlite3", com_midia=False, sistema="da Comanda")
    comanda.conferir(["comanda.sqlite3"])
    with pytest.raises(BackupInvalido, match="não é um backup da Comanda"):
        comanda.conferir(["banco.sqlite3"])
    with pytest.raises(BackupInvalido, match="inesperado"):
        comanda.conferir(["comanda.sqlite3", "midia/a.png"])
