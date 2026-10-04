"""
Inicia o Painel de Propagandas em modo de produção (servidor Waitress).

Também inicia as tarefas em segundo plano (monitoramento das telas,
alertas, backup diário e limpeza dos relatórios antigos).

    python servidor.py

Para desenvolvimento, com recarga automática:

    flask --app propagandas run --debug
"""

import logging
import os
import socket

from waitress import serve

from propagandas import create_app
from propagandas.tarefas import iniciar_tarefas
from src.config import arquivo as arquivo_config


def ip_na_rede_local():
    """IP deste computador na rede da loja (para abrir o painel de outro aparelho)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as conexao:
            conexao.connect(("8.8.8.8", 80))  # UDP: nada é enviado, só escolhe a interface de rede
            return conexao.getsockname()[0]
    except OSError:
        return None


def opcoes_waitress(config):
    """Opções do Waitress. Atrás de um proxy (Caddy, Cloudflare Tunnel), confia nos
    cabeçalhos X-Forwarded-* SÓ quando a conexão vem do próprio proxy.

    Sem isso o Waitress descarta esses cabeçalhos: todo acesso pareceria vir de
    127.0.0.1 (o bloqueio por tentativas de senha valeria para todos ao mesmo
    tempo) e os endereços gerados sairiam com http:// em vez de https://.
    """
    opcoes = {"threads": int(os.environ.get("THREADS", 8)), "ident": "PainelPropagandas"}
    if config["ATRAS_DE_PROXY"]:
        opcoes.update(
            trusted_proxy=os.environ.get("PROXY_CONFIAVEL", "127.0.0.1"),
            trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
            clear_untrusted_proxy_headers=True,  # quem não é o proxy não consegue falsificar o IP
        )
    return opcoes


def main():
    arquivo = arquivo_config.carregar()  # configuracao.env, se existir
    app = create_app()
    iniciar_tarefas(app)  # monitoramento das telas, backup diário e limpeza

    host = os.environ.get("HOST", "0.0.0.0")
    porta = int(os.environ.get("PORTA", 5000))
    log = logging.getLogger("propagandas")
    log.info("Painel de Propagandas iniciado")
    log.info("Painel:   http://localhost:%s/", porta)
    ip = ip_na_rede_local()
    if ip and host == "0.0.0.0":
        log.info("Na rede da loja (celular, outros computadores e TVs): http://%s:%s/", ip, porta)
    log.info("Dados em: %s", app.config["PASTA_DADOS"])
    if arquivo:
        log.info("Configuração lida de: %s", arquivo)

    serve(app, host=host, port=porta, **opcoes_waitress(app.config))


if __name__ == "__main__":
    main()
