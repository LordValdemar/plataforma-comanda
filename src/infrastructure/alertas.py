"""Envio dos alertas: e-mail pelo SMTP da plataforma e webhook (Slack, Teams, Discord, Google Chat...)."""

import ipaddress
import json
import logging
import smtplib
import socket
import ssl
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlsplit

from src.domain.alertas import Canais

log = logging.getLogger("propagandas.alertas")


class EnderecoBloqueado(ValueError):
    pass


def validar_url_webhook(url: str) -> None:
    """Só aceita HTTPS para endereços públicos da internet.

    O webhook é definido pelos clientes; sem isso, um cliente poderia fazer o
    servidor acessar a rede interna ou os metadados da nuvem (ataque SSRF).
    """
    partes = urlsplit(url)
    if partes.scheme != "https" or not partes.hostname:
        raise EnderecoBloqueado("o webhook precisa ser um endereço https://")
    try:
        enderecos = {str(info[4][0]) for info in socket.getaddrinfo(partes.hostname, partes.port or 443)}
    except socket.gaierror as erro:
        raise EnderecoBloqueado(f"não foi possível encontrar {partes.hostname}") from erro
    for endereco in enderecos:
        if not ipaddress.ip_address(endereco.split("%")[0]).is_global:
            raise EnderecoBloqueado(f"{partes.hostname} aponta para um endereço interno ({endereco})")


def motivo_para_recusar(url: str) -> str | None:
    """Para as telas: o motivo de recusar o webhook (ou None, se ele serve)."""
    try:
        validar_url_webhook(url)
    except EnderecoBloqueado as erro:
        return str(erro)
    return None


class _SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise EnderecoBloqueado("o webhook tentou redirecionar para outro endereço")


abridor = urllib.request.build_opener(_SemRedirecionamento)


@dataclass(frozen=True)
class Smtp:
    host: str
    porta: int
    usuario: str = ""
    senha: str = ""
    remetente: str = ""

    @classmethod
    def da_configuracao(cls, config: Any) -> "Smtp":
        return cls(config["SMTP_HOST"] or "", int(config["SMTP_PORTA"] or 587), config["SMTP_USUARIO"] or "",
                   config["SMTP_SENHA"] or "", config["SMTP_REMETENTE"] or "")


class EnviadorDeAlertas:
    def __init__(self, smtp: Smtp, assunto: str = "Painel de Propagandas") -> None:
        self._smtp = smtp
        self._assunto = assunto

    def _enviar_email(self, destinatarios: tuple[str, ...], mensagem: str) -> None:
        email = EmailMessage()
        email["Subject"] = f"{self._assunto}: {mensagem[:80]}"
        email["From"] = self._smtp.remetente or self._smtp.usuario
        email["To"] = ", ".join(destinatarios)
        email.set_content(mensagem)
        contexto = ssl.create_default_context()
        servidor: smtplib.SMTP
        if self._smtp.porta == 465:
            servidor = smtplib.SMTP_SSL(self._smtp.host, 465, timeout=20, context=contexto)
        else:
            servidor = smtplib.SMTP(self._smtp.host, self._smtp.porta, timeout=20)
            servidor.starttls(context=contexto)
        with servidor:
            if self._smtp.usuario:
                servidor.login(self._smtp.usuario, self._smtp.senha)
            servidor.send_message(email)

    @staticmethod
    def _enviar_webhook(url: str, mensagem: str) -> None:
        validar_url_webhook(url)
        # "text" é o formato do Slack/Teams/Google Chat; "content" é o do Discord.
        corpo = json.dumps({"text": mensagem, "content": mensagem}).encode()
        pedido = urllib.request.Request(url, data=corpo, headers={"Content-Type": "application/json"}, method="POST")
        with abridor.open(pedido, timeout=20) as resposta:
            resposta.read()

    def enviar(self, mensagem: str, canais: Canais) -> list[str]:
        """Envia pelos canais. Devolve os erros (vazio = tudo certo); um canal com erro não impede o outro."""
        log.warning("ALERTA: %s", mensagem)
        erros = []
        if canais.emails:
            try:
                self._enviar_email(canais.emails, mensagem)
            except Exception as erro:
                log.exception("Falha ao enviar alerta por e-mail")
                erros.append(f"e-mail: {erro}")
        if canais.webhook:
            try:
                self._enviar_webhook(canais.webhook, mensagem)
            except Exception as erro:
                log.exception("Falha ao enviar alerta por webhook")
                erros.append(f"webhook: {erro}")
        return erros
