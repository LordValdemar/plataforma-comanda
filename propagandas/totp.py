"""Verificação em duas etapas: as regras ficam em src/domain/totp.py; aqui, o relógio e o QR code.

O relógio do 2FA é o `time` deste módulo (os testes trocam só ele, sem mexer no resto do sistema).
"""

import time

import segno

from src.domain import totp as _regras
from src.domain.totp import DIGITOS, PASSO, novo_segredo, uri

__all__ = ["DIGITOS", "PASSO", "agora", "codigo_atual", "novo_segredo", "qr_code", "uri", "verificar"]


def agora():
    return time.time()


def codigo_atual(segredo, momento=None):
    return _regras.codigo_atual(segredo, momento or agora())


def verificar(segredo, codigo, ultimo_usado=0, momento=None):
    return _regras.verificar(segredo, codigo, ultimo_usado, momento or agora())


def qr_code(segredo, usuario, emissor="Painel de Propagandas"):
    """QR code (imagem SVG embutida) para o aplicativo autenticador ler."""
    return segno.make(uri(segredo, usuario, emissor), error="m").svg_data_uri(scale=5, border=2)
