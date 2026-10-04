"""Backup e restauração (banco + arquivos de mídia) em um único .zip.

As regras ficam em src/domain/backup.py e o trabalho com os arquivos em src/infrastructure/backup.py.
"""

from datetime import datetime

from src.domain.backup import PASTA_MIDIA_NO_ZIP, FormatoDoBackup
from src.infrastructure.backup import Backups

__all__ = ["NOME_BANCO_NO_ZIP", "PASTA_MIDIA_NO_ZIP", "criar_backup", "fez_backup_hoje", "restaurar_backup"]

NOME_BANCO_NO_ZIP = "banco.sqlite3"
FORMATO = FormatoDoBackup(NOME_BANCO_NO_ZIP, com_midia=True, sistema="do Painel de Propagandas")


def _backups(config):
    return Backups(FORMATO, config["BANCO"], config["PASTA_BACKUPS"], config["PASTA_DADOS"], config["PASTA_MIDIA"])


def criar_backup(config):
    """Gera dados/backups/backup-AAAAMMDD-HHMMSS.zip e apaga os mais antigos."""
    return _backups(config).criar(datetime.now(), config["BACKUP_MANTER"])


def restaurar_backup(config, caminho_zip):
    """Substitui banco e mídia pelo conteúdo do backup (os dados atuais ficam guardados). Pare o servidor antes."""
    return _backups(config).restaurar(caminho_zip, datetime.now())


def fez_backup_hoje(config):
    return _backups(config).feito_no_dia(datetime.now().date())
