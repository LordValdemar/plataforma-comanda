"""Tarefas em segundo plano: monitorar telas, backup diário e limpeza."""

import logging
import threading
import time

from src.domain.relatorios.exibicoes import inicio_da_retencao
from src.infrastructure.sqlite import apagar_exibicoes_anteriores

from . import agenda, alertas, asaas, backup, cobranca, db, ponto

log = logging.getLogger("propagandas.tarefas")

INTERVALO = 60          # verifica as telas a cada minuto
INTERVALO_MANUTENCAO = 3600


def limpar_exibicoes_antigas(config):
    dias = config["RETER_EXIBICOES_DIAS"]
    apagadas = apagar_exibicoes_anteriores(db.obter(), inicio_da_retencao(agenda.agora_utc(), dias))
    if apagadas:
        log.info("%d registro(s) de exibição com mais de %d dias apagados", apagadas, dias)


def manutencao(config):
    if config["BACKUP_MANTER"] > 0 and not backup.fez_backup_hoje(config):
        backup.criar_backup(config)
    limpar_exibicoes_antigas(config)
    if asaas.configurado(config):
        cobranca.sincronizar_todas()  # caso algum webhook tenha se perdido; aplica a tolerância de atraso


def iniciar_tarefas(app):
    def rodar():
        ultima_manutencao = None
        while True:
            with app.app_context():
                try:
                    alertas.verificar_telas()
                except Exception:
                    log.exception("Falha ao verificar as telas")
                try:
                    ponto.fechar_fora_do_horario()
                except Exception:
                    log.exception("Falha ao fechar os pontos fora do horário")
                if ultima_manutencao is None or time.monotonic() - ultima_manutencao >= INTERVALO_MANUTENCAO:
                    ultima_manutencao = time.monotonic()
                    try:
                        manutencao(app.config)
                    except Exception:
                        log.exception("Falha na manutenção (backup/limpeza)")
            time.sleep(INTERVALO)

    linha = threading.Thread(target=rodar, name="tarefas", daemon=True)
    linha.start()
    return linha
