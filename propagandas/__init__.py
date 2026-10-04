"""
Painel de Propagandas - sistema de sinalização digital para estabelecimentos.

Este pacote cria a aplicação Flask (create_app) com três partes:
- auth:      login, usuários, proteção CSRF
- painel:    cadastro das propagandas (área restrita)
- exibicao:  tela da TV e API pública da playlist
"""

import logging
import os
import secrets
from logging.handlers import TimedRotatingFileHandler

from flask import Flask, flash, g, redirect, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import (
    agenda,
    auth,
    cobranca,
    comanda,
    conta,
    db,
    empresa,
    exibicao,
    legal,
    painel,
    permissoes,
    plataforma,
    ponto,
    relatorios,
    telas,
)

PASTA_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_ligado(nome):
    return os.environ.get(nome, "").strip().lower() in {"1", "true", "sim", "yes"}


def montar_config(sobrescrever=None):
    """Lê a configuração das variáveis de ambiente (veja o README)."""
    sobrescrever = sobrescrever or {}
    pasta_dados = os.path.abspath(
        sobrescrever.get("PASTA_DADOS")
        or os.environ.get("PASTA_DADOS")
        or os.path.join(PASTA_PROJETO, "dados")
    )
    config = {
        "PASTA_DADOS": pasta_dados,
        "BANCO": os.path.join(pasta_dados, "banco.sqlite3"),
        "PASTA_MIDIA": os.path.join(pasta_dados, "midia"),
        "PASTA_BACKUPS": os.path.join(pasta_dados, "backups"),
        "PASTA_LOGS": os.path.join(pasta_dados, "logs"),
        "BACKUP_MANTER": int(os.environ.get("BACKUP_MANTER", 7)),
        "MAX_CONTENT_LENGTH": int(os.environ.get("TAMANHO_MAX_MB", 500)) * 1024 * 1024,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": _env_ligado("COOKIE_SEGURO"),
        "SESSION_COOKIE_NAME": "painel_sessao",
        "PERMANENT_SESSION_LIFETIME": 7 * 24 * 3600,  # 7 dias
        "ATRAS_DE_PROXY": _env_ligado("ATRAS_DE_PROXY"),
        "FUSO_HORARIO": os.environ.get("FUSO_HORARIO", "America/Sao_Paulo"),
        # Identificação de quem opera a plataforma (aparece nas páginas legais)
        "NOME_PLATAFORMA": os.environ.get("NOME_PLATAFORMA", "Painel de Propagandas"),
        "CONTATO_PLATAFORMA": os.environ.get("CONTATO_PLATAFORMA", ""),
        "RETER_EXIBICOES_DIAS": int(os.environ.get("RETER_EXIBICOES_DIAS", 365)),
        "LOGS_DIAS": int(os.environ.get("LOGS_DIAS", 190)),
        # Alertas de tela offline
        "ALERTA_OFFLINE_MIN": int(os.environ.get("ALERTA_OFFLINE_MIN", 5)),
        "ALERTA_EMAILS": os.environ.get("ALERTA_EMAILS", ""),
        "ALERTA_WEBHOOK": os.environ.get("ALERTA_WEBHOOK", ""),
        "SMTP_HOST": os.environ.get("SMTP_HOST", ""),
        "SMTP_PORTA": int(os.environ.get("SMTP_PORTA", 587)),
        "SMTP_USUARIO": os.environ.get("SMTP_USUARIO", ""),
        "SMTP_SENHA": os.environ.get("SMTP_SENHA", ""),
        "SMTP_REMETENTE": os.environ.get("SMTP_REMETENTE", ""),
        # Cobrança automática pelo Asaas
        "ASAAS_API_KEY": os.environ.get("ASAAS_API_KEY", ""),
        "ASAAS_AMBIENTE": os.environ.get("ASAAS_AMBIENTE", "sandbox"),   # "sandbox" ou "producao"
        "ASAAS_WEBHOOK_TOKEN": os.environ.get("ASAAS_WEBHOOK_TOKEN", ""),
        "ASAAS_URL": os.environ.get("ASAAS_URL", ""),  # opcional: outro endereço da API (testes)
        "COBRANCA_TOLERANCIA_DIAS": int(os.environ.get("COBRANCA_TOLERANCIA_DIAS", 5)),
        # Plataforma de assinatura: qualquer pessoa cria a conta da loja e assina pelo site.
        "CADASTRO_ABERTO": _env_ligado("CADASTRO_ABERTO"),
        "TESTE_GRATIS_DIAS": int(os.environ.get("TESTE_GRATIS_DIAS", 7)),  # dias até a primeira fatura
    }
    config.update(sobrescrever)
    return config


def _chave_secreta(pasta_dados):
    """Usa CHAVE_SECRETA do ambiente ou cria uma chave aleatória no disco."""
    if os.environ.get("CHAVE_SECRETA"):
        return os.environ["CHAVE_SECRETA"]
    caminho = os.path.join(pasta_dados, "chave_secreta")
    if not os.path.exists(caminho):
        descritor = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descritor, "w") as f:
            f.write(secrets.token_hex(32))
    with open(caminho) as f:
        return f.read().strip()


def _configurar_logs(app):
    formato = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    # Um arquivo por dia, guardado por LOGS_DIAS (o Marco Civil da Internet pede
    # que provedores de aplicação guardem os registros de acesso por 6 meses).
    arquivo = TimedRotatingFileHandler(
        os.path.join(app.config["PASTA_LOGS"], "painel.log"),
        when="midnight", backupCount=app.config["LOGS_DIAS"], encoding="utf-8",
    )
    arquivo.setFormatter(formato)
    registro = logging.getLogger("propagandas")
    registro.setLevel(logging.INFO)
    registro.propagate = False  # o Waitress configura o log raiz; evita linhas duplicadas
    # Evita handlers duplicados quando create_app é chamado várias vezes (testes).
    for antigo in list(registro.handlers):
        registro.removeHandler(antigo)
        antigo.close()
    registro.addHandler(arquivo)
    if not app.testing:
        console = logging.StreamHandler()
        console.setFormatter(formato)
        registro.addHandler(console)


def create_app(sobrescrever=None):
    app = Flask(__name__)
    app.config.update(montar_config(sobrescrever))

    for chave in ("PASTA_DADOS", "PASTA_MIDIA", "PASTA_BACKUPS", "PASTA_LOGS"):
        os.makedirs(app.config[chave], exist_ok=True)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _chave_secreta(app.config["PASTA_DADOS"])

    if app.config["ATRAS_DE_PROXY"]:
        # Necessário para saber o IP real e o HTTPS quando há um proxy (Caddy/Nginx).
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    _configurar_logs(app)
    db.migrar(app.config["BANCO"])
    db.preencher_tamanhos(app.config["BANCO"], app.config["PASTA_MIDIA"])
    db.preencher_slugs(app.config["BANCO"])
    app.teardown_appcontext(db.fechar)

    auth.registrar(app)
    app.before_request(ponto.exigir)  # depois do login: sem ponto aberto, só a página do ponto
    app.register_blueprint(ponto.bp)
    app.register_blueprint(painel.bp)
    app.register_blueprint(exibicao.bp)
    app.register_blueprint(telas.bp)
    app.register_blueprint(relatorios.bp)
    app.register_blueprint(empresa.bp)
    app.register_blueprint(plataforma.bp)
    app.register_blueprint(legal.bp)
    app.register_blueprint(cobranca.bp)
    app.register_blueprint(conta.bp)
    comanda.registrar(app)
    permissoes.registrar(app)

    app.jinja_env.filters["tempo_desde"] = agenda.tempo_desde
    app.jinja_env.filters["data_local"] = agenda.local_formatado
    app.jinja_env.filters["duracao"] = agenda.duracao_formatada
    app.jinja_env.filters["reais"] = cobranca.reais

    if app.config["ASAAS_AMBIENTE"] not in ("sandbox", "producao"):
        raise ValueError("ASAAS_AMBIENTE deve ser 'sandbox' ou 'producao'")

    @app.context_processor
    def aviso_de_fatura():
        """Fatura vencida da empresa do usuário, para o aviso no topo do painel."""
        if getattr(g, "usuario", None) is None:
            return {}
        return {"fatura_vencida": cobranca.fatura_vencida(db.obter(), g.empresa_id)}

    @app.after_request
    def cabecalhos_de_seguranca(resposta):
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "DENY")
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        # Libera só o que o sistema usa: tela cheia e vídeo nas TVs, tela acesa na cozinha, câmera para o QR do ponto.
        resposta.headers.setdefault(
            "Permissions-Policy",
            "fullscreen=(self), autoplay=(self), screen-wake-lock=(self), camera=(self), microphone=(), "
            "geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), browsing-topics=()",
        )
        resposta.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self'; "
            "style-src 'self'; script-src 'self'; frame-src 'none'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        return resposta

    @app.errorhandler(413)
    def arquivo_grande_demais(_erro):
        limite = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        flash(f"Envio grande demais. O limite é {limite} MB por vez.", "erro")
        return redirect(url_for("painel.lista"))

    @app.errorhandler(400)
    def requisicao_invalida(erro):
        if request.path.startswith("/api/"):
            return {"erro": "requisição inválida"}, 400
        return (
            "<h1>Requisição inválida</h1><p>A página pode ter expirado. "
            "<a href='/'>Voltar ao painel</a> e tente de novo.</p>",
            400,
        )

    return app
