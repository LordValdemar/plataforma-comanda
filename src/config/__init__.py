"""Configuração do sistema: variáveis de ambiente e o arquivo configuracao.env."""

from .arquivo import ARQUIVO_PADRAO, PASTA_PROJETO, carregar, ler
from .configuracao import montar_config

__all__ = ["ARQUIVO_PADRAO", "PASTA_PROJETO", "carregar", "ler", "montar_config"]
