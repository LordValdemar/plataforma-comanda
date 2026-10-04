"""Painel de propagandas (sem banco nem Flask)."""

from .exibicao import Exibicao, ler_registro
from .propaganda import (
    DURACAO_PADRAO,
    Destinos,
    ErroDePropaganda,
    Programacao,
    Propaganda,
    ler_data,
    ler_duracao,
    ler_letreiro,
    nova_ordem,
)
from .repositorio import RepositorioDePropagandas
from .servico import Playlist, ServicoDePropagandas, TelaDaPlaylist

__all__ = [
    "DURACAO_PADRAO", "Destinos", "ErroDePropaganda", "Exibicao", "Playlist", "Programacao", "Propaganda",
    "RepositorioDePropagandas", "ServicoDePropagandas", "TelaDaPlaylist", "ler_data", "ler_duracao", "ler_letreiro",
    "ler_registro", "nova_ordem",
]
