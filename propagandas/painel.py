"""Cadastro das propagandas (área restrita). As regras ficam em src/domain/painel."""

import logging
import os
import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from src.domain.erros import NaoEncontrado
from src.domain.horario import ler_dias, ler_hora
from src.domain.painel import (
    Destinos,
    ErroDePropaganda,
    Programacao,
    ServicoDePropagandas,
    ler_data,
    ler_duracao,
    ler_letreiro,
)
from src.infrastructure.sqlite import RepositorioDePropagandasSQLite

from . import agenda, db, modulos, planos
from .auth import login_obrigatorio
from .midia import EXTENSOES, detectar_tipo, extensao_de

bp = Blueprint("painel", __name__)
bp.before_request(modulos.exigir("painel"))  # só para lojas com o Painel no plano
log = logging.getLogger("propagandas.painel")


def servico():
    repositorio = RepositorioDePropagandasSQLite(db.obter(), g.empresa_id)
    # O relógio é lido a cada uso (lambda): os testes trocam agenda.agora_local.
    return ServicoDePropagandas(repositorio, agenda.fuso(), relogio=lambda: agenda.agora_local())


def destinos_por_propaganda(conexao):
    """Para a lista: {propaganda_id: {"telas": {ids}, "grupos": {ids}, "nomes": [..]}}"""
    resultado = {}
    linhas = conexao.execute(
        """
        SELECT d.propaganda_id, d.tela_id, d.grupo_id, t.nome AS tela_nome, gr.nome AS grupo_nome
        FROM propaganda_destinos d
        LEFT JOIN telas t ON t.id = d.tela_id
        LEFT JOIN grupos gr ON gr.id = d.grupo_id
        JOIN propagandas p ON p.id = d.propaganda_id
        WHERE p.empresa_id = ?
        ORDER BY gr.nome, t.nome
        """,
        (g.empresa_id,),
    )
    for linha in linhas:
        destino = resultado.setdefault(linha["propaganda_id"], {"telas": set(), "grupos": set(), "nomes": []})
        if linha["grupo_id"]:
            destino["grupos"].add(linha["grupo_id"])
            destino["nomes"].append("Grupo " + linha["grupo_nome"])
        else:
            destino["telas"].add(linha["tela_id"])
            destino["nomes"].append(linha["tela_nome"])
    return resultado


def _destinos_do_formulario(formulario):
    """Telas e grupos marcados (o serviço descarta os que não são da loja)."""
    return Destinos(frozenset(int(v) for v in formulario.getlist("telas") if v.isdigit()),
                    frozenset(int(v) for v in formulario.getlist("grupos") if v.isdigit()))


def _letreiro_do_formulario(formulario):
    return ler_letreiro(formulario.get("letreiro_modo", "geral"), formulario.get("letreiro_texto"))


def _apagar_arquivo(propaganda):
    caminho = os.path.join(current_app.config["PASTA_MIDIA"], propaganda.arquivo)
    if os.path.exists(caminho):
        os.remove(caminho)
    log.info("“%s” excluiu a propaganda “%s”", g.usuario["usuario"], propaganda.nome)


@bp.route("/")
@login_obrigatorio()
def lista():
    conexao = db.obter()
    propagandas = servico()
    itens, situacoes = propagandas.lista()
    return render_template(
        "propagandas.html",
        itens=itens,
        situacoes=situacoes,
        destinos=destinos_por_propaganda(conexao),
        telas=conexao.execute("SELECT id, nome FROM telas WHERE empresa_id = ? ORDER BY nome", (g.empresa_id,)).fetchall(),
        grupos=conexao.execute("SELECT id, nome FROM grupos WHERE empresa_id = ? ORDER BY nome", (g.empresa_id,)).fetchall(),
        dias=agenda.DIAS,
        agenda=agenda,
        letreiro=propagandas.letreiro,
        pausado=propagandas.pausado,
        empresa=planos.empresa(conexao, g.empresa_id),
        uso=planos.uso(conexao, g.empresa_id),
        extensoes=", ".join(sorted(e.upper() for e in EXTENSOES)),
    )


@bp.route("/enviar", methods=["POST"])
@login_obrigatorio()
def enviar():
    conexao = db.obter()
    propagandas = servico()
    pasta = current_app.config["PASTA_MIDIA"]
    enviados = 0
    # Onde as novas propagandas aparecem: escolhido no envio. Sem nada marcado, em nenhuma
    # TV (ficam guardadas até alguém escolher as telas).
    destinos = None if request.form.get("destino") == "todas" else propagandas.destinos_da_loja(
        _destinos_do_formulario(request.form))

    for arquivo in request.files.getlist("arquivos"):
        if not arquivo or not arquivo.filename:
            continue
        nome_original = os.path.basename(arquivo.filename)[:200]
        extensao = extensao_de(nome_original)
        cabecalho = arquivo.stream.read(32)
        arquivo.stream.seek(0)
        tipo = detectar_tipo(cabecalho)
        if extensao not in EXTENSOES or tipo != EXTENSOES[extensao]:
            flash(f"“{nome_original}” foi ignorado: o formato não é suportado ou o arquivo está corrompido.", "erro")
            log.warning("Envio recusado: “%s” por “%s”", nome_original, g.usuario["usuario"])
            continue

        # O nome no disco é aleatório: nunca usamos o nome enviado como caminho.
        nome_disco = f"{uuid.uuid4().hex}.{extensao}"
        caminho = os.path.join(pasta, nome_disco)
        arquivo.save(caminho)
        tamanho = os.path.getsize(caminho)
        if not planos.cabe_no_armazenamento(conexao, g.empresa_id, tamanho):
            os.remove(caminho)
            flash(f"“{nome_original}” não foi enviado: o limite de armazenamento do seu plano foi atingido.", "erro")
            continue
        try:
            propagandas.cadastrar(nome_original, nome_disco, tipo, tamanho, request.form.get("duracao"), destinos)
        except Exception:
            os.remove(caminho)
            raise
        enviados += 1
        log.info("“%s” enviou “%s” (%s)", g.usuario["usuario"], nome_original, nome_disco)

    if enviados:
        if destinos is None or destinos:
            flash(f"{enviados} propaganda(s) adicionada(s).", "ok")
        else:
            flash(f"{enviados} propaganda(s) adicionada(s), mas ainda sem tela: escolha onde vão aparecer "
                  "(em Editar, ou marque várias e use “Telas e grupos”).", "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/propaganda/<int:propaganda_id>/atualizar", methods=["POST"])
@login_obrigatorio()
def atualizar(propaganda_id):
    formulario = request.form
    programacao = Programacao(
        nome=formulario.get("nome", ""),
        duracao=ler_duracao(formulario.get("duracao")),
        ativo=formulario.get("ativo") == "on",
        inicio=ler_data(formulario.get("inicio")),
        fim=ler_data(formulario.get("fim")),
        dias_semana=ler_dias(formulario.getlist("dias")),
        hora_inicio=ler_hora(formulario.get("hora_inicio")),
        hora_fim=ler_hora(formulario.get("hora_fim")),
        para_todas=formulario.get("destino", "todas") == "todas",
        letreiro=_letreiro_do_formulario(formulario),
        destinos=_destinos_do_formulario(formulario),
    )
    try:
        propaganda = servico().atualizar(propaganda_id, programacao)
    except NaoEncontrado:
        abort(404)
    except ErroDePropaganda as erro:
        flash(str(erro), "erro")
        return redirect(url_for("painel.lista"))
    log.info("“%s” alterou a propaganda “%s”", g.usuario["usuario"], propaganda.nome)
    flash("Alterações salvas.", "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/propaganda/<int:propaganda_id>/mover/<direcao>", methods=["POST"])
@login_obrigatorio()
def mover(propaganda_id, direcao):
    if direcao not in ("cima", "baixo"):
        abort(404)
    try:
        servico().mover(propaganda_id, direcao)
    except NaoEncontrado:
        abort(404)
    return redirect(url_for("painel.lista"))


@bp.route("/propaganda/<int:propaganda_id>/excluir", methods=["POST"])
@login_obrigatorio()
def excluir(propaganda_id):
    try:
        _apagar_arquivo(servico().excluir(propaganda_id))
    except NaoEncontrado:
        abort(404)
    flash("Propaganda excluída.", "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/lote", methods=["POST"])
@login_obrigatorio()
def lote():
    """Aplica a mesma mudança em várias propagandas marcadas na lista."""
    formulario = request.form
    propagandas = servico()
    acao = formulario.get("acao", "")
    try:
        itens = propagandas.marcadas(int(v) for v in formulario.getlist("ids") if v.isdigit())
        quantas = len(itens)
        if acao in ("ativar", "desativar"):
            propagandas.definir_em_lote(itens, "ativo", acao == "ativar")
            mensagem = f"{quantas} propaganda(s) {'ativada(s)' if acao == 'ativar' else 'desativada(s)'}."
        elif acao == "tempo":
            duracao = ler_duracao(formulario.get("duracao"))
            propagandas.definir_em_lote(itens, "duracao", duracao)
            mensagem = f"Tempo de {duracao} s aplicado em {quantas} propaganda(s)."
        elif acao == "letreiro":
            if formulario.get("letreiro_modo") == "proprio" and not formulario.get("letreiro_texto", "").strip():
                raise ErroDePropaganda("Escreva o texto do letreiro.")
            propagandas.definir_em_lote(itens, "letreiro", _letreiro_do_formulario(formulario))
            mensagem = f"Letreiro atualizado em {quantas} propaganda(s)."
        elif acao == "telas":
            if formulario.get("destino") == "todas":
                propagandas.definir_destinos_em_lote(itens, "trocar", None)
                mensagem = f"{quantas} propaganda(s) agora aparecem em todas as telas."
            else:
                modo = formulario.get("modo", "trocar")
                destinos = propagandas.destinos_da_loja(_destinos_do_formulario(formulario))
                if destinos and modo not in ("trocar", "acrescentar", "tirar"):
                    abort(400)
                propagandas.definir_destinos_em_lote(itens, modo, destinos)
                mensagem = f"Telas atualizadas em {quantas} propaganda(s)."
        elif acao == "excluir":
            for item in itens:
                _apagar_arquivo(propagandas.excluir(item.id))
            mensagem = f"{quantas} propaganda(s) excluída(s)."
        else:
            abort(400)
    except ErroDePropaganda as erro:
        flash(str(erro), "erro")
        return redirect(url_for("painel.lista"))
    log.info("“%s” aplicou “%s” em %d propaganda(s)", g.usuario["usuario"], acao, quantas)
    flash(mensagem, "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/pausa", methods=["POST"])
@login_obrigatorio()
def pausa():
    """Pausa (ou retoma) todas as propagandas da loja em todas as TVs."""
    pausar = request.form.get("acao") == "pausar"
    servico().pausar(pausar)
    log.info("“%s” %s as propagandas", g.usuario["usuario"], "pausou" if pausar else "retomou")
    if pausar:
        flash("Propagandas pausadas. As TVs ficam com a tela preta em até 15 segundos.", "ok")
    else:
        flash("Propagandas no ar de novo.", "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/letreiro", methods=["POST"])
@login_obrigatorio()
def letreiro():
    servico().gravar_letreiro(request.form.get("letreiro", ""))
    log.info("“%s” alterou o letreiro", g.usuario["usuario"])
    flash("Letreiro salvo.", "ok")
    return redirect(url_for("painel.lista"))
