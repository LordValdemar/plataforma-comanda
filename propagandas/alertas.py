"""Alertas de tela offline por e-mail e/ou webhook (Slack, Teams, Discord, Google Chat...).

O servidor de e-mail (SMTP) é da plataforma e vem das variáveis de ambiente.
Quem recebe os alertas é definido por empresa, na página "Empresa". Na empresa
principal, ALERTA_EMAILS e ALERTA_WEBHOOK do ambiente valem quando a página está em branco.

As regras ficam em src/domain/alertas.py; o envio em src/infrastructure/alertas.py.
"""

import socket  # noqa: F401 (os testes trocam socket.getaddrinfo para simular o DNS)

from flask import current_app

from src.domain import alertas as regras
from src.domain.alertas import ONLINE_SEGUNDOS, Canais, MonitorDeTelas
from src.infrastructure.alertas import EnderecoBloqueado, EnviadorDeAlertas, Smtp, validar_url_webhook
from src.infrastructure.alertas import abridor as _abridor
from src.infrastructure.sqlite import RepositorioDeMonitoramentoSQLite

from . import agenda, db
from .auth import EMPRESA_PRINCIPAL

__all__ = ["ONLINE_SEGUNDOS", "Canais", "EnderecoBloqueado", "_abridor", "canais_da_empresa", "enviar_alerta",
           "esta_online", "validar_url_webhook", "verificar_telas"]


def _canais(empresa_id, alerta_emails, alerta_webhook, config):
    padrao = empresa_id == EMPRESA_PRINCIPAL
    return regras.canais(alerta_emails, alerta_webhook, bool(config["SMTP_HOST"]),
                         config["ALERTA_EMAILS"] if padrao else "", config["ALERTA_WEBHOOK"] if padrao else "")


def canais_da_empresa(empresa, config):
    """Os canais da empresa (linha da tabela empresas)."""
    return _canais(empresa["id"], empresa["alerta_emails"], empresa["alerta_webhook"], config)


def enviar_alerta(mensagem, canais, config=None):
    """Envia pelos canais da empresa. Devolve a lista de erros (vazia = tudo certo)."""
    return EnviadorDeAlertas(Smtp.da_configuracao(config or current_app.config)).enviar(mensagem, canais)


def esta_online(tela):
    return regras.esta_online(agenda.de_texto_utc(tela["ultimo_contato"]) if tela["ultimo_contato"] else None,
                              agenda.de_texto_utc(tela["fechada_em"]) if tela["fechada_em"] else None, agenda.agora_utc())


def verificar_telas():
    """Avisa uma vez quando uma tela cai e outra vez quando ela volta (empresas ativas)."""
    config = current_app.config
    monitor = MonitorDeTelas(
        RepositorioDeMonitoramentoSQLite(db.obter()),
        lambda mensagem, empresa: enviar_alerta(
            mensagem, _canais(empresa.id, empresa.alerta_emails, empresa.alerta_webhook, config), config),
        config["ALERTA_OFFLINE_MIN"], relogio=lambda: agenda.agora_utc(), hora_local=agenda.local_formatado,
    )
    return monitor.verificar()
