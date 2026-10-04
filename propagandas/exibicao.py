"""Tela de exibição (TV), API das telas e registro das exibições.

As regras (o que cada tela mostra, a conexão da TV pelo QR code e o crachá do aparelho)
ficam em src/domain/painel; aqui ficam as rotas, os cookies e os arquivos.
"""

import segno
from flask import (
    Blueprint,
    abort,
    after_this_request,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from src.domain.painel import MuitosPedidos, Playlist, ServicoDeConexao, ServicoDePropagandas, TelaDaPlaylist
from src.domain.painel.telas import PEDIDO_VALIDADE
from src.infrastructure.sqlite import RepositorioDeConexoesSQLite, RepositorioDePropagandasSQLite

from . import agenda, db
from .auth import EMPRESA_PRINCIPAL, csrf_isento

bp = Blueprint("exibicao", __name__)

UM_ANO = 365 * 24 * 3600
DEZ_ANOS = 10 * 365 * 24 * 3600
COOKIE_APARELHO = "tela_aparelho_{}"   # "crachá" da TV para a tela {id}
COOKIE_PEDIDO = "tela_pedido"           # segredo do pedido de conexão feito na página /tela


def empresa_ativa(empresa_id):
    linha = db.obter().execute("SELECT ativa FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    return bool(linha and linha["ativa"])


def _propagandas(empresa_id):
    repositorio = RepositorioDePropagandasSQLite(db.obter(), empresa_id)
    # O relógio é lido a cada uso (lambda): os testes trocam agenda.agora_local.
    return ServicoDePropagandas(repositorio, agenda.fuso(), relogio=lambda: agenda.agora_local())


def _conexao():
    return ServicoDeConexao(RepositorioDeConexoesSQLite(db.obter()), relogio=lambda: agenda.agora_utc())


def _resposta_playlist(playlist):
    resposta = jsonify({
        "pausado": playlist.pausado,
        "itens": [
            {
                "id": item.id,
                "tipo": item.tipo,
                "url": url_for("exibicao.midia", arquivo=item.arquivo),
                "duracao": item.duracao,
                "letreiro": item.letreiro,  # None = usa o letreiro geral da resposta
            }
            for item in playlist.itens
        ],
        "letreiro": playlist.letreiro,
    })
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def playlist_da_empresa(empresa_id, tela=None):
    """O que a tela mostra agora (tela None = o player geral). Empresa suspensa: nada."""
    if not empresa_ativa(empresa_id):
        return Playlist([], "")
    para = None if tela is None else TelaDaPlaylist(tela.id, tela.grupo_id, tela.letreiro)
    return _propagandas(empresa_id).playlist(para)


def _buscar_tela(codigo):
    tela = _conexao().tela(codigo)
    if tela is None:
        abort(404)
    return tela


# ---------------------------------------------------------------------------
# Pareamento: cada tela funciona só no aparelho conectado a ela
# ---------------------------------------------------------------------------

def _entregar_cracha(tela_id, token):
    @after_this_request
    def entregar(resposta):
        resposta.set_cookie(
            COOKIE_APARELHO.format(tela_id), token, max_age=DEZ_ANOS, httponly=True, samesite="Lax",
            secure=current_app.config["SESSION_COOKIE_SECURE"],
        )
        return resposta


def aparelho_autorizado(tela):
    """O aparelho que abriu a tela é o que está conectado a ela? (entrega o crachá, se acabou de ficar com ela)"""
    pode, cracha_novo = _conexao().autorizar(tela, request.cookies.get(COOKIE_APARELHO.format(tela.id)))
    if cracha_novo:
        _entregar_cracha(tela.id, cracha_novo)
    return pode


def _negado_api():
    # A TV (player.js) vai para a página /tela, que mostra o QR code para conectar de novo.
    return {"erro": "este aparelho não está conectado a esta tela", "conectar": url_for("exibicao.conectar")}, 403


def _crachas_deste_navegador():
    prefixo = COOKIE_APARELHO.format("")
    return {int(nome[len(prefixo):]): token for nome, token in request.cookies.items()
            if nome.startswith(prefixo) and nome[len(prefixo):].isdigit() and token}


@bp.route("/tela")
def conectar():
    """Endereço único para as TVs: mostra um QR code; quem administra lê e escolhe a tela.

    TV já conectada (tem o crachá de uma tela) vai direto para as propagandas dela: o atalho
    de quiosque da TV pode abrir sempre /tela, mesmo depois de reiniciar. Com mais de uma tela
    conectada no mesmo navegador, mostra a lista para escolher.
    """
    conectadas = _conexao().telas_do_aparelho(_crachas_deste_navegador())
    if len(conectadas) == 1:
        return redirect(url_for("exibicao.tela", codigo=conectadas[0].codigo))
    if conectadas:
        return render_template("tela_escolher.html", telas=conectadas)
    return conectar_nova()


@bp.route("/tela/nova")
def conectar_nova():
    """Mostra o QR code mesmo num navegador que já tem telas: para conectar mais uma."""
    conexao = _conexao()
    try:
        codigo, segredo = conexao.abrir_pedido()
    except MuitosPedidos as erro:
        return str(erro), 429
    endereco = url_for("telas.parear", codigo=codigo, _external=True)
    resposta = current_app.make_response(render_template(
        "tela_conectar.html", codigo=codigo, endereco=endereco,
        conectadas=conexao.telas_do_aparelho(_crachas_deste_navegador()),
        qr=segno.make(endereco, error="m").svg_data_uri(scale=10, border=2), validade=PEDIDO_VALIDADE,
    ))
    resposta.set_cookie(COOKIE_PEDIDO, segredo, max_age=PEDIDO_VALIDADE, httponly=True, samesite="Lax",
                        secure=current_app.config["SESSION_COOKIE_SECURE"])
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@bp.route("/api/tela/conexao")
def conexao_api():
    """A TV da página /tela pergunta a cada 2 s se alguém já escolheu a tela dela."""
    segredo = request.cookies.get(COOKIE_PEDIDO, "")
    estado, tela = _conexao().situacao_do_pedido(segredo)
    if estado == "vencido":
        return {"vencido": True}, 410  # a página recarrega e mostra um QR novo
    if estado == "aguardando":
        return {"pronto": False}
    _entregar_cracha(tela.id, segredo)  # o segredo do pedido vira o crachá da TV

    @after_this_request
    def apagar_pedido(resposta):
        resposta.delete_cookie(COOKIE_PEDIDO)
        return resposta
    return {"pronto": True, "url": url_for("exibicao.tela", codigo=tela.codigo)}


def _registrar_contato(tela, **novidade):
    _conexao().registrar_contato(
        tela, request.remote_addr or "", request.headers.get("User-Agent", ""),
        exibindo=novidade.get("exibindo"), mudar_exibindo="exibindo" in novidade,
    )


# ---------------------------------------------------------------------------
# Player geral (sem cadastro de tela): mostra só o que é "para todas as telas"
# da empresa principal. Mantido por compatibilidade com a primeira versão.
# ---------------------------------------------------------------------------

@bp.route("/player")
def player():
    return render_template("player.html", api=url_for("exibicao.playlist"), pulso="")


@bp.route("/api/playlist")
def playlist():
    return _resposta_playlist(playlist_da_empresa(EMPRESA_PRINCIPAL))


# ---------------------------------------------------------------------------
# Telas cadastradas: cada TV usa o próprio endereço /tela/<código>
# ---------------------------------------------------------------------------

@bp.route("/tela/<codigo>")
def tela(codigo):
    tela = _conexao().tela(codigo)
    if tela is None:
        return render_template("tela_desconhecida.html"), 404
    if not aparelho_autorizado(tela):
        return redirect(url_for("exibicao.conectar"))  # aparelho não conectado: mostra o QR code
    return render_template(
        "player.html",
        api=url_for("exibicao.playlist_tela", codigo=codigo),
        pulso=url_for("exibicao.pulso", codigo=codigo),
    )


@bp.route("/api/tela/<codigo>/playlist")
def playlist_tela(codigo):
    tela = _buscar_tela(codigo)
    if not aparelho_autorizado(tela):
        return _negado_api()
    _registrar_contato(tela)
    return _resposta_playlist(playlist_da_empresa(tela.empresa_id, tela))


@bp.route("/api/tela/<codigo>/pulso", methods=["POST"])
@csrf_isento
def pulso(codigo):
    """A TV avisa que está viva, o que está exibindo e o que já exibiu."""
    tela = _buscar_tela(codigo)
    if not aparelho_autorizado(tela):
        return _negado_api()
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        abort(400)
    if dados.get("saindo") is True:
        # A janela da TV foi fechada (ou recarregada): offline na hora. Se ela voltar, o
        # próximo contato limpa a marca.
        _conexao().marcar_fechada(tela)
        return {"saindo": True}
    registros = dados.get("exibicoes") or []
    if not isinstance(registros, list):
        abort(400)
    propagandas = _propagandas(tela.empresa_id)
    recebidos, gravados = propagandas.registrar_exibicoes(tela.id, registros, current_app.config["RETER_EXIBICOES_DIAS"])
    _registrar_contato(tela, exibindo=propagandas.nome_exibindo(dados.get("exibindo")))
    return {"recebidos": recebidos, "gravados": gravados}


# ---------------------------------------------------------------------------
# Arquivos e monitoramento
# ---------------------------------------------------------------------------

@bp.route("/midia/<arquivo>")
def midia(arquivo):
    # Os nomes no disco são únicos e nunca mudam, então a TV pode guardar
    # o arquivo em cache por muito tempo (economiza rede e aguenta quedas).
    resposta = send_from_directory(current_app.config["PASTA_MIDIA"], arquivo, max_age=UM_ANO)
    resposta.headers["Cache-Control"] = f"public, max-age={UM_ANO}, immutable"
    return resposta


@bp.route("/saude")
def saude():
    """Para monitoramento: responde 200 se o servidor e o banco estão ok."""
    db.obter().execute("SELECT 1").fetchone()
    return {"status": "ok"}
