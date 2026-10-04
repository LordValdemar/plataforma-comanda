"""Alertas de tela offline por e-mail e/ou webhook (Slack, Teams, Discord, Google Chat...).

O servidor de e-mail (SMTP) é da plataforma e vem das variáveis de ambiente.
Quem recebe os alertas é definido por empresa, na página "Empresa". Na empresa
principal, ALERTA_EMAILS e ALERTA_WEBHOOK do ambiente valem quando a página está em branco.
"""

import ipaddress
import json
import logging
import smtplib
import socket
import ssl
import urllib.request
from email.message import EmailMessage
from urllib.parse import urlsplit

from flask import current_app

from . import agenda, db
from .auth import EMPRESA_PRINCIPAL

log = logging.getLogger("propagandas.alertas")

ONLINE_SEGUNDOS = 75  # a TV manda sinal de vida a cada 30 s: offline depois de dois sinais perdidos


def canais_da_empresa(empresa, config):
    """{"emails": [...], "webhook": "...", "nomes": ["e-mail", "webhook"]} da empresa."""
    emails = empresa["alerta_emails"]
    webhook = empresa["alerta_webhook"]
    if empresa["id"] == EMPRESA_PRINCIPAL:
        emails = emails or config["ALERTA_EMAILS"]
        webhook = webhook or config["ALERTA_WEBHOOK"]
    lista_emails = [e.strip() for e in emails.split(",") if e.strip()] if config["SMTP_HOST"] else []
    nomes = (["e-mail"] if lista_emails else []) + (["webhook"] if webhook else [])
    return {"emails": lista_emails, "webhook": webhook, "nomes": nomes}


def _enviar_email(config, destinatarios, assunto, mensagem):
    email = EmailMessage()
    email["Subject"] = assunto
    email["From"] = config["SMTP_REMETENTE"] or config["SMTP_USUARIO"]
    email["To"] = ", ".join(destinatarios)
    email.set_content(mensagem)
    contexto = ssl.create_default_context()
    if config["SMTP_PORTA"] == 465:
        servidor = smtplib.SMTP_SSL(config["SMTP_HOST"], 465, timeout=20, context=contexto)
    else:
        servidor = smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORTA"], timeout=20)
        servidor.starttls(context=contexto)
    with servidor:
        if config["SMTP_USUARIO"]:
            servidor.login(config["SMTP_USUARIO"], config["SMTP_SENHA"])
        servidor.send_message(email)


class EnderecoBloqueado(ValueError):
    pass


def validar_url_webhook(url):
    """Só aceita HTTPS para endereços públicos da internet.

    O webhook é definido pelos clientes; sem isso, um cliente poderia fazer o
    servidor acessar a rede interna ou os metadados da nuvem (ataque SSRF).
    """
    partes = urlsplit(url)
    if partes.scheme != "https" or not partes.hostname:
        raise EnderecoBloqueado("o webhook precisa ser um endereço https://")
    try:
        enderecos = {info[4][0] for info in socket.getaddrinfo(partes.hostname, partes.port or 443)}
    except socket.gaierror as erro:
        raise EnderecoBloqueado(f"não foi possível encontrar {partes.hostname}") from erro
    for endereco in enderecos:
        if not ipaddress.ip_address(endereco.split("%")[0]).is_global:
            raise EnderecoBloqueado(f"{partes.hostname} aponta para um endereço interno ({endereco})")


class _SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise EnderecoBloqueado("o webhook tentou redirecionar para outro endereço")


_abridor = urllib.request.build_opener(_SemRedirecionamento)


def _enviar_webhook(url, mensagem):
    validar_url_webhook(url)
    # "text" é o formato do Slack/Teams/Google Chat; "content" é o do Discord.
    corpo = json.dumps({"text": mensagem, "content": mensagem}).encode()
    pedido = urllib.request.Request(url, data=corpo, headers={"Content-Type": "application/json"}, method="POST")
    with _abridor.open(pedido, timeout=20) as resposta:
        resposta.read()


def enviar_alerta(mensagem, canais, config=None):
    """Envia pelos canais da empresa. Retorna a lista de erros (vazia = tudo certo)."""
    config = config or current_app.config
    log.warning("ALERTA: %s", mensagem)
    erros = []
    if canais["emails"]:
        try:
            _enviar_email(config, canais["emails"], "Painel de Propagandas: " + mensagem[:80], mensagem)
        except Exception as erro:
            log.exception("Falha ao enviar alerta por e-mail")
            erros.append(f"e-mail: {erro}")
    if canais["webhook"]:
        try:
            _enviar_webhook(canais["webhook"], mensagem)
        except Exception as erro:
            log.exception("Falha ao enviar alerta por webhook")
            erros.append(f"webhook: {erro}")
    return erros


def esta_online(tela):
    if not tela["ultimo_contato"]:
        return False
    if tela["fechada_em"] and tela["fechada_em"] >= tela["ultimo_contato"]:
        return False  # a TV avisou que a janela foi fechada
    segundos = (agenda.agora_utc() - agenda.de_texto_utc(tela["ultimo_contato"])).total_seconds()
    return segundos < ONLINE_SEGUNDOS


def verificar_telas():
    """Avisa uma vez quando uma tela cai e outra vez quando ela volta (empresas ativas)."""
    config = current_app.config
    limite = config["ALERTA_OFFLINE_MIN"] * 60
    conexao = db.obter()
    agora = agenda.agora_utc()
    empresas = {e["id"]: e for e in conexao.execute("SELECT * FROM empresas WHERE ativa = 1")}
    telas = conexao.execute("SELECT * FROM telas WHERE ultimo_contato IS NOT NULL").fetchall()
    for tela in telas:
        empresa = empresas.get(tela["empresa_id"])
        if empresa is None:
            continue
        sem_contato = (agora - agenda.de_texto_utc(tela["ultimo_contato"])).total_seconds()
        if sem_contato > limite and not tela["alerta_offline"]:
            mensagem = (
                f"⚠️ A tela “{tela['nome']}” está sem comunicação desde "
                f"{agenda.local_formatado(tela['ultimo_contato'])}."
            )
            novo_estado = 1
        elif sem_contato <= limite and tela["alerta_offline"]:
            mensagem = f"✅ A tela “{tela['nome']}” voltou a funcionar."
            novo_estado = 0
        else:
            continue
        enviar_alerta(f"[{empresa['nome']}] {mensagem}", canais_da_empresa(empresa, config), config)
        with conexao:
            conexao.execute("UPDATE telas SET alerta_offline = ? WHERE id = ?", (novo_estado, tela["id"]))
