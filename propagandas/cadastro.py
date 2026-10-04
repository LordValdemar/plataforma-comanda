"""Cadastro da empresa na porta de entrada. As regras (CPF/CNPJ, contato, endereço, logo) ficam em src/domain/empresas."""

from src.domain.empresas import (
    CAMPOS,
    LOGO_MAX_BYTES,
    UFS,
    CadastroInvalido,
    documento_formatado,
    endereco_completo,
    ler_dados,
)
from src.domain.empresas import ler_logo as _ler_logo

__all__ = ["CAMPOS", "LOGO_MAX_BYTES", "UFS", "CadastroInvalido", "documento_formatado", "endereco_completo",
           "ler_formulario", "ler_logo"]

ler_formulario = ler_dados


def ler_logo(arquivo):
    """Arquivo enviado (PNG ou JPG pequeno) → data URI. Lê só um byte além do limite."""
    return _ler_logo(arquivo.read(LOGO_MAX_BYTES + 1))
