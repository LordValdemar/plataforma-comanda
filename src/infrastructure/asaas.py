"""Cliente da API v3 do Asaas (https://docs.asaas.com), seguindo o contrato GatewayDeCobranca do domínio.

Só monta os pedidos e lê as respostas: quem fala HTTP é a função `chamar` recebida (a porta de
entrada passa a dela, que sabe a chave e o endereço; os testes passam um Asaas falso).
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from src.domain.cobranca import ErroNoGateway

USER_AGENT = "PainelPropagandas/1.0"  # o Asaas exige o cabeçalho User-Agent
MAX_PAGINAS = 20

Chamar = Callable[..., dict[str, Any]]   # chamar(metodo, caminho, corpo=None, parametros=None)


def enviar_http(metodo: str, url: str, chave: str, corpo: dict[str, Any] | None = None) -> dict[str, Any]:
    """Um pedido à API (urllib, só a biblioteca padrão). Erros viram ErroNoGateway com a mensagem do Asaas."""
    dados = json.dumps(corpo).encode() if corpo is not None else None
    pedido = urllib.request.Request(url, data=dados, method=metodo, headers={
        "access_token": chave, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resposta:
            resultado: dict[str, Any] = json.loads(resposta.read() or b"{}")
            return resultado
    except urllib.error.HTTPError as erro:
        try:
            detalhes = json.loads(erro.read() or b"{}")
            mensagem = "; ".join(e.get("description", "") for e in detalhes.get("errors", [])) or str(erro)
        except ValueError:
            mensagem = str(erro)
        raise ErroNoGateway(mensagem) from erro
    except urllib.error.URLError as erro:
        raise ErroNoGateway(f"não foi possível falar com o Asaas ({erro.reason})") from erro


class ClienteAsaas:
    def __init__(self, chamar: Chamar) -> None:
        self._chamar = chamar

    def criar_cliente(self, nome: str, documento: str, email: str, referencia: str) -> dict[str, Any]:
        return self._chamar("POST", "/customers", {
            "name": nome, "cpfCnpj": documento, "email": email or None, "externalReference": referencia,
            "notificationDisabled": False,  # o Asaas avisa o cliente sobre faturas
        })

    def criar_assinatura(self, cliente_id: str, valor_centavos: int, primeiro_vencimento: str, descricao: str,
                         referencia: str) -> dict[str, Any]:
        return self._chamar("POST", "/subscriptions", {
            "customer": cliente_id,
            "billingType": "UNDEFINED",  # o cliente escolhe PIX, boleto ou cartão na fatura
            "value": valor_centavos / 100, "nextDueDate": primeiro_vencimento, "cycle": "MONTHLY",
            "description": descricao, "externalReference": referencia,
        })

    def atualizar_assinatura(self, assinatura_id: str, valor_centavos: int, descricao: str) -> dict[str, Any]:
        return self._chamar("PUT", f"/subscriptions/{assinatura_id}", {
            "value": valor_centavos / 100, "description": descricao,
            "updatePendingPayments": True,  # aplica também às faturas ainda não pagas
        })

    def cancelar_assinatura(self, assinatura_id: str) -> dict[str, Any]:
        return self._chamar("DELETE", f"/subscriptions/{assinatura_id}")

    def faturas_da_assinatura(self, assinatura_id: str) -> list[dict[str, Any]]:
        """Todas as faturas da assinatura (a API devolve em páginas de até 100)."""
        faturas: list[dict[str, Any]] = []
        for pagina in range(MAX_PAGINAS):
            resposta = self._chamar("GET", f"/subscriptions/{assinatura_id}/payments",
                                    parametros={"limit": 100, "offset": pagina * 100})
            faturas.extend(resposta.get("data", []))
            if not resposta.get("hasMore"):
                return faturas
        raise ErroNoGateway("a assinatura tem faturas demais para sincronizar")

    def buscar_fatura(self, fatura_id: str) -> dict[str, Any]:
        """Situação atual de uma fatura, direto do Asaas (fonte da verdade)."""
        return self._chamar("GET", f"/payments/{fatura_id}")
