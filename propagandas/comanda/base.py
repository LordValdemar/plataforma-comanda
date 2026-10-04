"""Peças comuns do módulo Comanda (porta de entrada HTTP): permissão por papel, configurações e o serviço.

Toda consulta do módulo filtra pela empresa do usuário logado (g.empresa_id): uma loja nunca
vê nem altera os dados de outra.
"""

from functools import wraps

from flask import abort, g, request

from src.domain.comanda import Ator, ServicoDeComandas
from src.infrastructure.sqlite import RepositorioDeComandasSQLite

from .. import db, permissoes
from ..auth import login_obrigatorio


def servico_de_comandas():
    """Os casos de uso da Comanda, gravando no banco da loja de quem está logado."""
    return ServicoDeComandas(RepositorioDeComandasSQLite(db.obter(), g.empresa_id))


def ator_atual():
    """Quem faz a operação (para o histórico). Chame depois de conferir a permissão: aí já se sabe se foi autorizado."""
    return Ator(usuario_id=g.usuario["id"] if g.get("usuario") is not None else None, autorizado_por=g.get("autorizado_por"))


def papel_exigido(*papeis):
    """Libera a rota para os papéis indicados (o administrador da loja sempre pode).

    O módulo em si (a loja assinou a Comanda?) é conferido antes, no before_request dos blueprints.
    """
    def decorador(funcao):
        @wraps(funcao)
        @login_obrigatorio()
        def interna(*args, **kwargs):
            if g.usuario["papel"] != "admin" and g.usuario["papel"] not in papeis:
                abort(403)
            return funcao(*args, **kwargs)

        @wraps(funcao)
        def com_api(*args, **kwargs):
            # A tela da cozinha (fetch) precisa de 401 em JSON para saber que a sessão acabou.
            if g.usuario is None and "/api/" in request.path:
                return {"erro": "faça login de novo"}, 401
            return interna(*args, **kwargs)
        return com_api
    return decorador


def exigir_funcao(nome):
    """Libera a rota conforme a tela de Permissões da loja (ver permissoes.FUNCOES)."""
    def decorador(funcao):
        protegida = permissoes.exigir(nome)(funcao)

        @wraps(funcao)
        def com_api(*args, **kwargs):
            if g.usuario is None and "/api/" in request.path:
                return {"erro": "faça login de novo"}, 401
            return protegida(*args, **kwargs)

        com_api.funcao_exigida = nome
        return com_api
    return decorador


def pode(*papeis):
    """Para os templates: o usuário logado tem um destes papéis (ou é administrador)?"""
    return g.usuario is not None and (g.usuario["papel"] == "admin" or g.usuario["papel"] in papeis)


def pode_fechar_conta():
    """Para os botões: fecha contas (ou pode, pedindo autorização). Quem decide é a tela de Permissões."""
    return permissoes.permite("fechar_conta")


def ler_config(chave, padrao=""):
    return db.ler_config(g.empresa_id, "comanda." + chave, padrao)


def gravar_config(chave, valor):
    db.gravar_config(g.empresa_id, "comanda." + chave, str(valor))
