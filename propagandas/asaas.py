"""Asaas na porta de entrada: a chave e o endereço vêm da configuração do app.

Os pedidos e respostas ficam em src/infrastructure/asaas.py (ClienteAsaas). As chamadas passam
por `_enviar`, que os testes substituem por um Asaas falso.
"""

import logging
from urllib.parse import urlencode

from flask import current_app

from src.domain.cobranca import ErroNoGateway
from src.infrastructure.asaas import ClienteAsaas, enviar_http

log = logging.getLogger("propagandas.asaas")

URLS = {
    "sandbox": "https://api-sandbox.asaas.com/v3",
    "producao": "https://api.asaas.com/v3",
}
ErroAsaas = ErroNoGateway   # nome antigo, usado pelas telas e pelos testes
_enviar = enviar_http


def configurado(config=None):
    config = config or current_app.config
    return bool(config["ASAAS_API_KEY"])


def chamar(metodo, caminho, corpo=None, parametros=None):
    config = current_app.config
    if not configurado(config):
        raise ErroAsaas("a integração com o Asaas não está configurada (ASAAS_API_KEY)")
    url = (config["ASAAS_URL"] or URLS[config["ASAAS_AMBIENTE"]]) + caminho
    if parametros:
        url += "?" + urlencode(parametros)
    log.info("Asaas %s %s", metodo, caminho)
    return _enviar(metodo, url, config["ASAAS_API_KEY"], corpo)


def cliente():
    """O Asaas como o domínio o vê (GatewayDeCobranca). `chamar` é lido a cada uso: os testes podem trocá-lo."""
    return ClienteAsaas(lambda *args, **kwargs: chamar(*args, **kwargs))


# Atalhos com os nomes de antes.

def criar_cliente(nome, documento, email, referencia):
    return cliente().criar_cliente(nome, documento, email, referencia)


def criar_assinatura(cliente_id, valor_centavos, primeiro_vencimento, descricao, referencia):
    return cliente().criar_assinatura(cliente_id, valor_centavos, primeiro_vencimento, descricao, referencia)


def atualizar_assinatura(assinatura_id, valor_centavos, descricao):
    return cliente().atualizar_assinatura(assinatura_id, valor_centavos, descricao)


def cancelar_assinatura(assinatura_id):
    return cliente().cancelar_assinatura(assinatura_id)


def faturas_da_assinatura(assinatura_id):
    return cliente().faturas_da_assinatura(assinatura_id)


def buscar_fatura(fatura_id):
    return cliente().buscar_fatura(fatura_id)
