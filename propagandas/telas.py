"""Cadastro das telas (TVs), grupos e monitoramento (somente administradores)."""

import logging
import re
import secrets
import unicodedata

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import alertas, db, exibicao, modulos, permissoes, planos
from .auth import login_obrigatorio

bp = Blueprint("telas", __name__)
bp.before_request(modulos.exigir("painel"))  # só para lojas com o Painel no plano
log = logging.getLogger("propagandas.telas")


# Sem letras e números que se confundem (0/o, 1/l/i): fácil de digitar no controle da TV.
LETRAS_DO_CODIGO = "abcdefghjkmnpqrstuvwxyz23456789"


def novo_codigo(conexao, nome):
    """Endereço da tela: o nome dela + 6 letras aleatórias, ex.: "promocoes-k7m2x9".

    O final aleatório impede adivinhar o endereço: quem não tem o código não vê nem registra
    nada da tela (31^6, cerca de 900 milhões de combinações para cada nome).
    """
    base = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:30].strip("-") or "tela"
    while True:
        codigo = f"{base}-{''.join(secrets.choice(LETRAS_DO_CODIGO) for _ in range(6))}"
        if not conexao.execute("SELECT 1 FROM telas WHERE codigo = ?", (codigo,)).fetchone():
            return codigo


def _ler_grupo(conexao):
    valor = request.form.get("grupo_id", "")
    if not valor.isdigit():
        return None
    linha = conexao.execute(
        "SELECT id FROM grupos WHERE id = ? AND empresa_id = ?", (int(valor), g.empresa_id)
    ).fetchone()
    return linha["id"] if linha else None


def _buscar(conexao, tela_id):
    tela = conexao.execute("SELECT * FROM telas WHERE id = ? AND empresa_id = ?", (tela_id, g.empresa_id)).fetchone()
    if tela is None:
        abort(404)
    return tela


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
    conexao = db.obter()
    nome = request.form.get("nome", "").strip()[:100]
    if not nome:
        flash("Dê um nome para a tela (ex.: “Balcão”, “Vitrine”).", "erro")
        return redirect(url_for("telas.lista"))
    if not planos.pode_cadastrar_tela(conexao, g.empresa_id):
        flash("O limite de telas do seu plano foi atingido. Fale com o suporte para ampliar.", "erro")
        return redirect(url_for("telas.lista"))
    with conexao:
        conexao.execute(
            # Tela nova só funciona no aparelho conectado pelo QR code da página /tela.
            "INSERT INTO telas (empresa_id, nome, codigo, grupo_id, aceita_link) VALUES (?, ?, ?, ?, 0)",
            (g.empresa_id, nome, novo_codigo(conexao, nome), _ler_grupo(conexao)),
        )
    log.info("“%s” cadastrou a tela “%s”", g.usuario["usuario"], nome)
    flash(f"Tela “{nome}” cadastrada. Na TV, abra {url_for('exibicao.conectar', _external=True)} e leia o QR code com o celular.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/telas/<int:tela_id>/atualizar", methods=["POST"])
@permissoes.exigir("telas")
def atualizar(tela_id):
    conexao = db.obter()
    tela = _buscar(conexao, tela_id)
    nome = request.form.get("nome", "").strip()[:100] or tela["nome"]
    letreiro = request.form.get("letreiro", "").strip()[:500] or None
    with conexao:
        conexao.execute(
            "UPDATE telas SET nome = ?, grupo_id = ?, letreiro = ? WHERE id = ? AND empresa_id = ?",
            (nome, _ler_grupo(conexao), letreiro, tela_id, g.empresa_id),
        )
    log.info("“%s” alterou a tela “%s”", g.usuario["usuario"], nome)
    flash("Tela atualizada.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/telas/<int:tela_id>/novo-codigo", methods=["POST"])
@permissoes.exigir("telas")
def trocar_codigo(tela_id):
    conexao = db.obter()
    tela = _buscar(conexao, tela_id)
    with conexao:
        conexao.execute("UPDATE telas SET codigo = ? WHERE id = ? AND empresa_id = ?",
                        (novo_codigo(conexao, tela["nome"]), tela_id, g.empresa_id))
    log.info("“%s” gerou novo endereço para a tela “%s”", g.usuario["usuario"], tela["nome"])
    flash(f"Novo endereço gerado para “{tela['nome']}”. O endereço antigo parou de funcionar.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/tela/parear/<codigo>", methods=["GET", "POST"])
@permissoes.exigir("conectar_tv")
def parear(codigo):
    """Aberta pelo celular ao ler o QR code da TV: escolhe qual tela aquela TV vai mostrar."""
    conexao = db.obter()
    pedido = exibicao.pedido_por_codigo(conexao, codigo)
    if pedido is None:
        flash("Este código de conexão venceu ou não existe. Na TV, abra de novo o endereço /tela e leia o código novo.", "erro")
        return redirect(url_for("painel.lista"))
    telas = conexao.execute("SELECT * FROM telas WHERE empresa_id = ? ORDER BY nome", (g.empresa_id,)).fetchall()
    if request.method == "POST":
        tela = next((t for t in telas if str(t["id"]) == request.form.get("tela_id")), None)
        if tela is None:
            flash("Escolha uma das telas da lista.", "erro")
        elif pedido["tela_id"] is not None:
            flash("Esta TV já foi conectada.", "erro")
        else:
            exibicao.conectar_aparelho(conexao, pedido, tela)
            log.info("“%s” conectou um aparelho à tela “%s”", g.usuario["usuario"], tela["nome"])
            flash(f"TV conectada à tela “{tela['nome']}”. Em alguns segundos ela começa a mostrar as propagandas.", "ok")
            return redirect(url_for("telas.lista") if permissoes.pode("telas") else url_for("painel.lista"))
    return render_template("tela_parear.html", codigo=pedido["codigo"], telas=telas)


@bp.route("/telas/conectar", methods=["POST"])
@permissoes.exigir("conectar_tv")
def conectar_por_codigo():
    """Para quando a câmera não funciona: digita o código que aparece na TV (admin ou editor)."""
    codigo = "".join(c for c in request.form.get("codigo", "").upper() if c.isalnum())[:6]
    return redirect(url_for("telas.parear", codigo=codigo or "-"))


@bp.route("/telas/<int:tela_id>/desconectar-aparelho", methods=["POST"])
@permissoes.exigir("telas")
def desconectar_aparelho(tela_id):
    conexao = db.obter()
    tela = _buscar(conexao, tela_id)
    with conexao:
        conexao.execute("UPDATE telas SET aparelho_hash = NULL, pareada_em = NULL, aceita_link = 0 WHERE id = ?", (tela_id,))
    log.info("“%s” desconectou o aparelho da tela “%s”", g.usuario["usuario"], tela["nome"])
    flash(f"Aparelho desconectado da tela “{tela['nome']}”. Ele volta para a página do QR code.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/telas/<int:tela_id>/excluir", methods=["POST"])
@permissoes.exigir("telas")
def excluir(tela_id):
    conexao = db.obter()
    tela = _buscar(conexao, tela_id)
    with conexao:
        conexao.execute("DELETE FROM telas WHERE id = ? AND empresa_id = ?", (tela_id, g.empresa_id))
    log.info("“%s” excluiu a tela “%s”", g.usuario["usuario"], tela["nome"])
    flash("Tela excluída. O histórico de exibições dela foi mantido nos relatórios.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/grupos/novo", methods=["POST"])
@permissoes.exigir("telas")
def novo_grupo():
    conexao = db.obter()
    nome = request.form.get("nome", "").strip()[:100]
    if not nome:
        flash("Dê um nome para o grupo (ex.: “Lojas de SP”).", "erro")
    elif conexao.execute("SELECT 1 FROM grupos WHERE nome = ? AND empresa_id = ?", (nome, g.empresa_id)).fetchone():
        flash(f"O grupo “{nome}” já existe.", "erro")
    else:
        with conexao:
            conexao.execute("INSERT INTO grupos (empresa_id, nome) VALUES (?, ?)", (g.empresa_id, nome))
        log.info("“%s” criou o grupo “%s”", g.usuario["usuario"], nome)
        flash("Grupo criado.", "ok")
    return redirect(url_for("telas.lista"))


@bp.route("/grupos/<int:grupo_id>/excluir", methods=["POST"])
@permissoes.exigir("telas")
def excluir_grupo(grupo_id):
    conexao = db.obter()
    grupo = conexao.execute("SELECT * FROM grupos WHERE id = ? AND empresa_id = ?", (grupo_id, g.empresa_id)).fetchone()
    if grupo is None:
        abort(404)
    with conexao:
        # Propagandas que só iam para este grupo ficam sem destino (não aparecem
        # em lugar nenhum) e o painel avisa. Nunca passam a ir para todas as telas.
        conexao.execute("DELETE FROM grupos WHERE id = ? AND empresa_id = ?", (grupo_id, g.empresa_id))
    log.info("“%s” excluiu o grupo “%s”", g.usuario["usuario"], grupo["nome"])
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
