"""Tela de exibição (TV), API das telas e registro das exibições."""

import hashlib
import hmac
import secrets
from datetime import timedelta

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

from src.domain.painel import Playlist, ServicoDePropagandas, TelaDaPlaylist
from src.infrastructure.sqlite import RepositorioDePropagandasSQLite

from . import agenda, db
from .auth import EMPRESA_PRINCIPAL, csrf_isento

bp = Blueprint("exibicao", __name__)

UM_ANO = 365 * 24 * 3600
INTERVALO_CONTATO = 30  # segundos; evita gravar no banco a cada troca de propaganda


def empresa_ativa(empresa_id):
    linha = db.obter().execute("SELECT ativa FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    return bool(linha and linha["ativa"])


def _servico(empresa_id):
    repositorio = RepositorioDePropagandasSQLite(db.obter(), empresa_id)
    # O relógio é lido a cada uso (lambda): os testes trocam agenda.agora_local.
    return ServicoDePropagandas(repositorio, agenda.fuso(), relogio=lambda: agenda.agora_local())


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
    para = None if tela is None else TelaDaPlaylist(tela["id"], tela["grupo_id"], tela["letreiro"])
    return _servico(empresa_id).playlist(para)


def _buscar_tela(codigo):
    tela = db.obter().execute("SELECT * FROM telas WHERE codigo = ?", (codigo,)).fetchone()
    if tela is None:
        abort(404)
    return tela


# ---------------------------------------------------------------------------
# Pareamento: cada tela funciona só no aparelho conectado a ela
# ---------------------------------------------------------------------------

COOKIE_APARELHO = "tela_aparelho_{}"   # "crachá" da TV para a tela {id}
COOKIE_PEDIDO = "tela_pedido"           # segredo do pedido de conexão feito na página /tela
DEZ_ANOS = 10 * 365 * 24 * 3600
PEDIDO_VALIDADE = 10 * 60               # o QR code da página /tela vale 10 minutos
MAX_PEDIDOS_ABERTOS = 500               # contra quem tenta encher o banco abrindo /tela sem parar
LETRAS_DO_PEDIDO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sem 0/O, 1/I/L: fácil de digitar


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _entregar_cracha(tela_id, token):
    @after_this_request
    def entregar(resposta):
        resposta.set_cookie(
            COOKIE_APARELHO.format(tela_id), token, max_age=DEZ_ANOS, httponly=True, samesite="Lax",
            secure=current_app.config["SESSION_COOKIE_SECURE"],
        )
        return resposta


def aparelho_autorizado(tela):
    """O aparelho que abriu a tela é o que está conectado a ela?

    Telas que já existiam antes do pareamento (aceita_link) aceitam, uma vez, o primeiro aparelho
    que chegar pelo endereço: assim as TVs que já estavam ligadas continuam funcionando.
    """
    token = request.cookies.get(COOKIE_APARELHO.format(tela["id"]), "")
    if tela["aparelho_hash"]:
        return bool(token) and hmac.compare_digest(_hash(token), tela["aparelho_hash"])
    if not tela["aceita_link"]:
        return False
    token = token or secrets.token_urlsafe(32)
    conexao = db.obter()
    with conexao:
        conectou = conexao.execute(
            "UPDATE telas SET aparelho_hash = ?, pareada_em = ?, aceita_link = 0 WHERE id = ? AND aparelho_hash IS NULL",
            (_hash(token), agenda.para_texto_utc(agenda.agora_utc()), tela["id"]),
        ).rowcount
    if conectou:
        _entregar_cracha(tela["id"], token)
    return bool(conectou)


def _negado_api():
    # A TV (player.js) vai para a página /tela, que mostra o QR code para conectar de novo.
    return {"erro": "este aparelho não está conectado a esta tela", "conectar": url_for("exibicao.conectar")}, 403


def pedido_por_codigo(conexao, codigo):
    """Pedido de conexão ainda válido (lido pelo celular de quem administra)."""
    limite = agenda.para_texto_utc(agenda.agora_utc() - timedelta(seconds=PEDIDO_VALIDADE))
    return conexao.execute(
        "SELECT * FROM pareamentos WHERE codigo = ? AND criado_em >= ?", ((codigo or "").upper(), limite)
    ).fetchone()


def conectar_aparelho(conexao, pedido, tela):
    """Liga a TV do pedido à tela escolhida. O aparelho que estava nela deixa de funcionar."""
    with conexao:
        conexao.execute(
            "UPDATE telas SET aparelho_hash = ?, pareada_em = ?, aceita_link = 0 WHERE id = ?",
            (pedido["segredo_hash"], agenda.para_texto_utc(agenda.agora_utc()), tela["id"]),
        )
        conexao.execute("UPDATE pareamentos SET tela_id = ? WHERE id = ?", (tela["id"], pedido["id"]))


def telas_deste_aparelho(conexao):
    """Telas cujo crachá está guardado neste navegador (um computador pode mostrar várias)."""
    prefixo = COOKIE_APARELHO.format("")
    telas = []
    for nome, token in request.cookies.items():
        if nome.startswith(prefixo) and nome[len(prefixo):].isdigit() and token:
            tela = conexao.execute("SELECT * FROM telas WHERE id = ?", (int(nome[len(prefixo):]),)).fetchone()
            if tela and tela["aparelho_hash"] and hmac.compare_digest(_hash(token), tela["aparelho_hash"]):
                telas.append(tela)
    return sorted(telas, key=lambda t: t["nome"].lower())


@bp.route("/tela")
def conectar():
    """Endereço único para as TVs: mostra um QR code; quem administra lê e escolhe a tela.

    TV já conectada (tem o crachá de uma tela) vai direto para as propagandas dela: o atalho
    de quiosque da TV pode abrir sempre /tela, mesmo depois de reiniciar. Com mais de uma tela
    conectada no mesmo navegador, mostra a lista para escolher.
    """
    conexao = db.obter()
    conectadas = telas_deste_aparelho(conexao)
    if len(conectadas) == 1:
        return redirect(url_for("exibicao.tela", codigo=conectadas[0]["codigo"]))
    if conectadas:
        return render_template("tela_escolher.html", telas=conectadas)
    return conectar_nova()


@bp.route("/tela/nova")
def conectar_nova():
    """Mostra o QR code mesmo num navegador que já tem telas: para conectar mais uma."""
    conexao = db.obter()
    agora = agenda.agora_utc()
    with conexao:
        conexao.execute("DELETE FROM pareamentos WHERE criado_em < ?",
                        (agenda.para_texto_utc(agora - timedelta(seconds=PEDIDO_VALIDADE)),))
    if conexao.execute("SELECT COUNT(*) FROM pareamentos").fetchone()[0] >= MAX_PEDIDOS_ABERTOS:
        return "Muitos pedidos de conexão abertos. Tente de novo em alguns minutos.", 429
    segredo = secrets.token_urlsafe(32)
    while True:
        codigo = "".join(secrets.choice(LETRAS_DO_PEDIDO) for _ in range(6))
        if not conexao.execute("SELECT 1 FROM pareamentos WHERE codigo = ?", (codigo,)).fetchone():
            break
    with conexao:
        conexao.execute("INSERT INTO pareamentos (codigo, segredo_hash, criado_em) VALUES (?, ?, ?)",
                        (codigo, _hash(segredo), agenda.para_texto_utc(agora)))
    endereco = url_for("telas.parear", codigo=codigo, _external=True)
    resposta = current_app.make_response(render_template(
        "tela_conectar.html", codigo=codigo, endereco=endereco, conectadas=telas_deste_aparelho(conexao),
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
    conexao = db.obter()
    pedido = conexao.execute("SELECT * FROM pareamentos WHERE segredo_hash = ?", (_hash(segredo),)).fetchone() if segredo else None
    limite = agenda.agora_utc() - timedelta(seconds=PEDIDO_VALIDADE)
    if pedido is None or agenda.de_texto_utc(pedido["criado_em"]) < limite:
        return {"vencido": True}, 410  # a página recarrega e mostra um QR novo
    if pedido["tela_id"] is None:
        return {"pronto": False}
    tela = conexao.execute("SELECT * FROM telas WHERE id = ?", (pedido["tela_id"],)).fetchone()
    with conexao:
        conexao.execute("DELETE FROM pareamentos WHERE id = ?", (pedido["id"],))
    if tela is None or not hmac.compare_digest(tela["aparelho_hash"] or "", pedido["segredo_hash"]):
        return {"vencido": True}, 410
    _entregar_cracha(tela["id"], segredo)  # o segredo do pedido vira o crachá da TV

    @after_this_request
    def apagar_pedido(resposta):
        resposta.delete_cookie(COOKIE_PEDIDO)
        return resposta
    return {"pronto": True, "url": url_for("exibicao.tela", codigo=tela["codigo"])}


FECHADA_TOLERANCIA = 3  # segundos: pedido que já estava a caminho quando a janela fechou não a "reabre"


def _registrar_contato(tela, **extras):
    agora = agenda.agora_utc()
    if tela["fechada_em"] and (agora - agenda.de_texto_utc(tela["fechada_em"])).total_seconds() < FECHADA_TOLERANCIA:
        return
    if not extras and tela["ultimo_contato"] and not tela["fechada_em"]:
        segundos = (agora - agenda.de_texto_utc(tela["ultimo_contato"])).total_seconds()
        if segundos < INTERVALO_CONTATO:
            return
    campos = {
        "ultimo_contato": agenda.para_texto_utc(agora),
        "ultimo_ip": (request.remote_addr or "")[:45],
        "navegador": request.headers.get("User-Agent", "")[:200],
        "fechada_em": None,  # deu sinal de vida: a janela está aberta
        **extras,
    }
    conexao = db.obter()
    with conexao:
        conexao.execute(
            f"UPDATE telas SET {', '.join(f'{c} = ?' for c in campos)} WHERE id = ?",
            (*campos.values(), tela["id"]),
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
    tela = db.obter().execute("SELECT * FROM telas WHERE codigo = ?", (codigo,)).fetchone()
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
    return _resposta_playlist(playlist_da_empresa(tela["empresa_id"], tela))


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
        agora_texto = agenda.para_texto_utc(agenda.agora_utc())
        conexao = db.obter()
        with conexao:
            conexao.execute("UPDATE telas SET ultimo_contato = ?, fechada_em = ?, exibindo = NULL WHERE id = ?",
                            (agora_texto, agora_texto, tela["id"]))
        return {"saindo": True}
    registros = dados.get("exibicoes") or []
    if not isinstance(registros, list):
        abort(400)
    propagandas = _servico(tela["empresa_id"])
    recebidos, gravados = propagandas.registrar_exibicoes(tela["id"], registros, current_app.config["RETER_EXIBICOES_DIAS"])
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
