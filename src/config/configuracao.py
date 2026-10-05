"""Configuração lida das variáveis de ambiente (veja o README e o configuracao.env.exemplo)."""

import os
from collections.abc import Mapping
from typing import Any

from .arquivo import PASTA_PROJETO


def _env_ligado(nome: str) -> bool:
    return os.environ.get(nome, "").strip().lower() in {"1", "true", "sim", "yes"}


def montar_config(sobrescrever: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Lê a configuração das variáveis de ambiente (veja o README)."""
    sobrescrever = dict(sobrescrever or {})
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
        "MAX_CONTENT_LENGTH": int(os.environ.get("TAMANHO_MAX_MB", 200)) * 1024 * 1024,
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
