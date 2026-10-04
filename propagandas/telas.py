"""Cadastro das telas (TVs), grupos e monitoramento (somente administradores)."""

import logging

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from src.domain.erros import NaoEncontrado
from src.domain.painel import ErroDeTela, PedidoVencido, ServicoDeTelas, normalizar_codigo_de_pedido
from src.infrastructure.sqlite import RepositorioDeTelasSQLite

from . import agenda, alertas, db, modulos, permissoes, planos
from .auth import login_obrigatorio

bp = Blueprint("telas", __name__)
bp.before_request(modulos.exigir("painel"))  # só para lojas com o Painel no plano
log = logging.getLogger("propagandas.telas")


def servico():
    # O relógio é lido a cada uso (lambda): os testes podem trocá-lo.
    return ServicoDeTelas(RepositorioDeTelasSQLite(db.obter(), g.empresa_id), relogio=lambda: agenda.agora_utc())


def _grupo_do_formulario():
    valor = request.form.get("grupo_id", "")
    return int(valor) if valor.isdigit() else None


def _uma(acao):
    """Roda a ação na tela/grupo da loja: o id de outra loja dá 404."""
    try:
        return acao(servico())
    except NaoEncontrado:
        abort(404)


@bp.route("/telas")
@permissoes.exigir("telas")
def lista():
    conexao = db.obter()
    telas = conexao.execute(
        "SELECT t.*, gr.nome AS grupo_nome FROM telas t LEFT JOIN grupos gr ON gr.id = t.grupo_id "
        "WHERE t.empresa_id = ? ORDER BY t.nome",
        (g.empresa_id,),
    ).fetchall()
    empresa = planos.empresa(conexao, g.empresa_id)
    online = {t["id"]: alertas.esta_online(t) for t in telas}
    return render_template(
        "telas.html",
        telas=telas,
        online=online,
        offline=sum(1 for t in telas if t["ultimo_contato"] and not online[t["id"]]),
        grupos=conexao.execute(
            "SELECT gr.*, COUNT(t.id) AS total FROM grupos gr LEFT JOIN telas t ON t.grupo_id = gr.id "
            "WHERE gr.empresa_id = ? GROUP BY gr.id ORDER BY gr.nome",
            (g.empresa_id,),
        ).fetchall(),
        canais=alertas.canais_da_empresa(empresa, current_app.config),
        letreiro_geral=db.ler_config(g.empresa_id, "letreiro"),
        empresa=empresa,
        uso=planos.uso(conexao, g.empresa_id),
    )


@bp.route("/telas/nova", methods=["POST"])
@permissoes.exigir("telas")
def nova():
    try:
        tela = servico().cadastrar(request.form.get("nome", ""), _grupo_do_formulario(),
                                   cabe_no_plano=planos.pode_cadastrar_tela(db.obter(), g.empresa_id))
    except ErroDeTela as erro:
        flash(str(erro), "erro")
        return redirect(url_for("telas.lista"))
    log.info("“%s” cadastrou a tela “%s”", g.usuario["usuario"], tela.nome)
    flash(f"Tela “{tela.nome}” cadastrada. Na TV, abra {url_for('exibicao.conectar', _external=True)} e leia o QR code com o celular.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/telas/<int:tela_id>/atualizar", methods=["POST"])
@permissoes.exigir("telas")
def atualizar(tela_id):
    tela = _uma(lambda telas: telas.atualizar(tela_id, request.form.get("nome", ""), _grupo_do_formulario(),
                                             request.form.get("letreiro", "")))
    log.info("“%s” alterou a tela “%s”", g.usuario["usuario"], tela.nome)
    flash("Tela atualizada.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/telas/<int:tela_id>/novo-codigo", methods=["POST"])
@permissoes.exigir("telas")
def trocar_codigo(tela_id):
    tela = _uma(lambda telas: telas.trocar_endereco(tela_id))
    log.info("“%s” gerou novo endereço para a tela “%s”", g.usuario["usuario"], tela.nome)
    flash(f"Novo endereço gerado para “{tela.nome}”. O endereço antigo parou de funcionar.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/tela/parear/<codigo>", methods=["GET", "POST"])
@permissoes.exigir("conectar_tv")
def parear(codigo):
    """Aberta pelo celular ao ler o QR code da TV: escolhe qual tela aquela TV vai mostrar."""
    telas = servico()
    try:
        pedido = telas.pedido(codigo)
    except PedidoVencido as erro:
        flash(str(erro), "erro")
        return redirect(url_for("painel.lista"))
    if request.method == "POST":
        escolhida = request.form.get("tela_id", "")
        try:
            tela = telas.conectar(pedido.codigo, int(escolhida) if escolhida.isdigit() else None)
        except ErroDeTela as erro:
            flash(str(erro), "erro")
        else:
            log.info("“%s” conectou um aparelho à tela “%s”", g.usuario["usuario"], tela.nome)
            flash(f"TV conectada à tela “{tela.nome}”. Em alguns segundos ela começa a mostrar as propagandas.", "ok")
            return redirect(url_for("telas.lista") if permissoes.pode("telas") else url_for("painel.lista"))
    return render_template("tela_parear.html", codigo=pedido.codigo, telas=telas.telas())


@bp.route("/telas/conectar", methods=["POST"])
@permissoes.exigir("conectar_tv")
def conectar_por_codigo():
    """Para quando a câmera não funciona: digita o código que aparece na TV (admin ou editor)."""
    codigo = normalizar_codigo_de_pedido(request.form.get("codigo"))
    return redirect(url_for("telas.parear", codigo=codigo or "-"))


@bp.route("/telas/<int:tela_id>/desconectar-aparelho", methods=["POST"])
@permissoes.exigir("telas")
def desconectar_aparelho(tela_id):
    tela = _uma(lambda telas: telas.desconectar_aparelho(tela_id))
    log.info("“%s” desconectou o aparelho da tela “%s”", g.usuario["usuario"], tela.nome)
    flash(f"Aparelho desconectado da tela “{tela.nome}”. Ele volta para a página do QR code.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/telas/<int:tela_id>/excluir", methods=["POST"])
@permissoes.exigir("telas")
def excluir(tela_id):
    tela = _uma(lambda telas: telas.excluir(tela_id))
    log.info("“%s” excluiu a tela “%s”", g.usuario["usuario"], tela.nome)
    flash("Tela excluída. O histórico de exibições dela foi mantido nos relatórios.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/grupos/novo", methods=["POST"])
@permissoes.exigir("telas")
def novo_grupo():
    try:
        nome = servico().criar_grupo(request.form.get("nome", ""))
    except ErroDeTela as erro:
        flash(str(erro), "erro")
    else:
        log.info("“%s” criou o grupo “%s”", g.usuario["usuario"], nome)
        flash("Grupo criado.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/grupos/<int:grupo_id>/excluir", methods=["POST"])
@permissoes.exigir("telas")
def excluir_grupo(grupo_id):
    nome = _uma(lambda telas: telas.excluir_grupo(grupo_id))
    log.info("“%s” excluiu o grupo “%s”", g.usuario["usuario"], nome)
    flash("Grupo excluído. As telas dele ficaram sem grupo.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/alertas/testar", methods=["POST"])
@login_obrigatorio("admin")
def testar_alerta():
    empresa = planos.empresa(db.obter(), g.empresa_id)
    canais = alertas.canais_da_empresa(empresa, current_app.config)
    if not canais["nomes"]:
        flash("Nenhum canal de alerta configurado. Configure em “Empresa”.", "erro")
    else:
        erros = alertas.enviar_alerta(f"🔔 Teste de alerta enviado por {g.usuario['usuario']}.", canais)
        if erros:
            flash("Falha ao enviar: " + "; ".join(erros), "erro")
        else:
            flash("Alerta de teste enviado.", "ok")
    return redirect(url_for("telas.lista"))
