"""Cadastro das propagandas (área restrita)."""

import logging
import os
import uuid
from datetime import date, datetime

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

from . import agenda, db, modulos, planos
from .auth import login_obrigatorio
from .midia import EXTENSOES, detectar_tipo, extensao_de

bp = Blueprint("painel", __name__)
bp.before_request(modulos.exigir("painel"))  # só para lojas com o Painel no plano
log = logging.getLogger("propagandas.painel")

DURACAO_PADRAO = 10


def ler_duracao(valor):
    try:
        return max(1, min(3600, int(valor)))
    except (TypeError, ValueError):
        return DURACAO_PADRAO


def ler_data(valor):
    """Aceita só datas AAAA-MM-DD válidas; qualquer outra coisa vira vazio."""
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError:
        return None


def ler_hora(valor):
    """Aceita HH:MM; qualquer outra coisa vira vazio."""
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def destinos_por_propaganda(conexao):
    """{propaganda_id: {"telas": {ids}, "grupos": {ids}, "nomes": [..]}}"""
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


LETREIRO_MODOS = ("geral", "proprio", "nenhum")


def ler_letreiro(formulario):
    """Letreiro durante a propaganda: None = o geral, '' = nenhum, texto = próprio."""
    modo = formulario.get("letreiro_modo", "geral")
    if modo == "nenhum":
        return ""
    if modo == "proprio":
        return formulario.get("letreiro_texto", "").strip()[:500] or None
    return None


def esta_pausado(empresa_id):
    """Pausa geral: as TVs da loja ficam sem propagandas até alguém retomar."""
    return db.ler_config(empresa_id, "pausado") == "1"


def ler_destinos(conexao, formulario):
    """Telas e grupos marcados no formulário, só os da empresa logada."""
    ids_telas = {r["id"] for r in conexao.execute("SELECT id FROM telas WHERE empresa_id = ?", (g.empresa_id,))}
    ids_grupos = {r["id"] for r in conexao.execute("SELECT id FROM grupos WHERE empresa_id = ?", (g.empresa_id,))}
    telas = {int(v) for v in formulario.getlist("telas") if v.isdigit()} & ids_telas
    grupos = {int(v) for v in formulario.getlist("grupos") if v.isdigit()} & ids_grupos
    return telas, grupos


def gravar_destinos(conexao, propaganda_id, telas, grupos):
    conexao.execute("DELETE FROM propaganda_destinos WHERE propaganda_id = ?", (propaganda_id,))
    conexao.executemany(
        "INSERT INTO propaganda_destinos (propaganda_id, tela_id) VALUES (?, ?)",
        [(propaganda_id, t) for t in sorted(telas)],
    )
    conexao.executemany(
        "INSERT INTO propaganda_destinos (propaganda_id, grupo_id) VALUES (?, ?)",
        [(propaganda_id, gr) for gr in sorted(grupos)],
    )


def buscar(conexao, propaganda_id):
    # Sempre filtrando pela empresa: o id na URL de outra empresa dá 404.
    item = conexao.execute(
        "SELECT * FROM propagandas WHERE id = ? AND empresa_id = ?", (propaganda_id, g.empresa_id)
    ).fetchone()
    if item is None:
        abort(404)
    return item


@bp.route("/")
@login_obrigatorio()
def lista():
    conexao = db.obter()
    itens = conexao.execute(
        "SELECT * FROM propagandas WHERE empresa_id = ? ORDER BY posicao, id", (g.empresa_id,)
    ).fetchall()
    agora = agenda.agora_local()
    return render_template(
        "propagandas.html",
        itens=itens,
        situacoes={item["id"]: agenda.situacao(item, agora) for item in itens},
        destinos=destinos_por_propaganda(conexao),
        telas=conexao.execute("SELECT id, nome FROM telas WHERE empresa_id = ? ORDER BY nome", (g.empresa_id,)).fetchall(),
        grupos=conexao.execute("SELECT id, nome FROM grupos WHERE empresa_id = ? ORDER BY nome", (g.empresa_id,)).fetchall(),
        dias=agenda.DIAS,
        agenda=agenda,
        letreiro=db.ler_config(g.empresa_id, "letreiro"),
        pausado=esta_pausado(g.empresa_id),
        empresa=planos.empresa(conexao, g.empresa_id),
        uso=planos.uso(conexao, g.empresa_id),
        extensoes=", ".join(sorted(e.upper() for e in EXTENSOES)),
    )


@bp.route("/enviar", methods=["POST"])
@login_obrigatorio()
def enviar():
    conexao = db.obter()
    duracao = ler_duracao(request.form.get("duracao"))
    pasta = current_app.config["PASTA_MIDIA"]
    enviados = 0

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
            with conexao:
                conexao.execute(
                    "INSERT INTO propagandas (empresa_id, nome, arquivo, tipo, tamanho, duracao, posicao) "
                    "VALUES (?, ?, ?, ?, ?, ?, "
                    "(SELECT COALESCE(MAX(posicao), 0) + 1 FROM propagandas WHERE empresa_id = ?))",
                    (g.empresa_id, nome_original, nome_disco, tipo, tamanho, duracao, g.empresa_id),
                )
        except Exception:
            os.remove(caminho)
            raise
        enviados += 1
        log.info("“%s” enviou “%s” (%s)", g.usuario["usuario"], nome_original, nome_disco)

    if enviados:
        flash(f"{enviados} propaganda(s) adicionada(s).", "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/propaganda/<int:propaganda_id>/atualizar", methods=["POST"])
@login_obrigatorio()
def atualizar(propaganda_id):
    conexao = db.obter()
    item = buscar(conexao, propaganda_id)
    formulario = request.form
    nome = formulario.get("nome", "").strip()[:200] or item["nome"]
    inicio = ler_data(formulario.get("inicio"))
    fim = ler_data(formulario.get("fim"))
    hora_inicio = ler_hora(formulario.get("hora_inicio"))
    hora_fim = ler_hora(formulario.get("hora_fim"))
    dias = "".join(d for d in agenda.TODOS_OS_DIAS if d in formulario.getlist("dias"))
    para_todas = formulario.get("destino", "todas") == "todas"

    telas_escolhidas, grupos_escolhidos = ler_destinos(conexao, formulario)

    erro = None
    if inicio and fim and fim < inicio:
        erro = "A data de término não pode ser antes da data de início."
    elif not dias:
        erro = "Escolha pelo menos um dia da semana."
    elif hora_inicio and hora_fim and hora_inicio == hora_fim:
        erro = "O horário de início e de fim não podem ser iguais."
    elif not para_todas and not telas_escolhidas and not grupos_escolhidos:
        erro = "Escolha pelo menos uma tela ou grupo (ou marque “Todas as telas”)."
    if erro:
        flash(f"“{item['nome']}”: {erro}", "erro")
        return redirect(url_for("painel.lista"))

    with conexao:
        conexao.execute(
            """
            UPDATE propagandas
            SET nome = ?, duracao = ?, ativo = ?, inicio = ?, fim = ?,
                dias_semana = ?, hora_inicio = ?, hora_fim = ?, para_todas = ?, letreiro = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (
                nome,
                ler_duracao(formulario.get("duracao")),
                1 if formulario.get("ativo") == "on" else 0,
                inicio,
                fim,
                dias,
                hora_inicio,
                hora_fim,
                1 if para_todas else 0,
                ler_letreiro(formulario),
                propaganda_id,
                g.empresa_id,
            ),
        )
        if para_todas:
            gravar_destinos(conexao, propaganda_id, set(), set())
        else:
            gravar_destinos(conexao, propaganda_id, telas_escolhidas, grupos_escolhidos)
    log.info("“%s” alterou a propaganda “%s”", g.usuario["usuario"], nome)
    flash("Alterações salvas.", "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/propaganda/<int:propaganda_id>/mover/<direcao>", methods=["POST"])
@login_obrigatorio()
def mover(propaganda_id, direcao):
    if direcao not in ("cima", "baixo"):
        abort(404)
    conexao = db.obter()
    itens = conexao.execute(
        "SELECT id FROM propagandas WHERE empresa_id = ? ORDER BY posicao, id", (g.empresa_id,)
    ).fetchall()
    ids = [linha["id"] for linha in itens]
    if propaganda_id not in ids:
        abort(404)
    posicao = ids.index(propaganda_id)
    destino = posicao - 1 if direcao == "cima" else posicao + 1
    if 0 <= destino < len(ids):
        ids[posicao], ids[destino] = ids[destino], ids[posicao]
        with conexao:
            conexao.executemany(
                "UPDATE propagandas SET posicao = ? WHERE id = ? AND empresa_id = ?",
                [(numero, item_id, g.empresa_id) for numero, item_id in enumerate(ids, start=1)],
            )
    return redirect(url_for("painel.lista"))


def _excluir(conexao, item):
    with conexao:
        conexao.execute("DELETE FROM propagandas WHERE id = ? AND empresa_id = ?", (item["id"], g.empresa_id))
    caminho = os.path.join(current_app.config["PASTA_MIDIA"], item["arquivo"])
    if os.path.exists(caminho):
        os.remove(caminho)
    log.info("“%s” excluiu a propaganda “%s”", g.usuario["usuario"], item["nome"])


@bp.route("/propaganda/<int:propaganda_id>/excluir", methods=["POST"])
@login_obrigatorio()
def excluir(propaganda_id):
    conexao = db.obter()
    _excluir(conexao, buscar(conexao, propaganda_id))
    flash("Propaganda excluída.", "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/lote", methods=["POST"])
@login_obrigatorio()
def lote():
    """Aplica a mesma mudança em várias propagandas marcadas na lista."""
    conexao = db.obter()
    formulario = request.form
    marcados = {int(v) for v in formulario.getlist("ids") if v.isdigit()}
    itens = [
        item for item in conexao.execute(
            "SELECT * FROM propagandas WHERE empresa_id = ? ORDER BY posicao, id", (g.empresa_id,)
        ) if item["id"] in marcados
    ]
    acao = formulario.get("acao", "")
    if not itens:
        flash("Marque pelo menos uma propaganda na lista.", "erro")
        return redirect(url_for("painel.lista"))
    ids = [item["id"] for item in itens]
    marcas = ",".join("?" * len(ids))

    if acao in ("ativar", "desativar"):
        with conexao:
            conexao.execute(
                f"UPDATE propagandas SET ativo = ? WHERE empresa_id = ? AND id IN ({marcas})",
                (1 if acao == "ativar" else 0, g.empresa_id, *ids),
            )
        mensagem = f"{len(ids)} propaganda(s) {'ativada(s)' if acao == 'ativar' else 'desativada(s)'}."
    elif acao == "tempo":
        duracao = ler_duracao(formulario.get("duracao"))
        with conexao:
            conexao.execute(
                f"UPDATE propagandas SET duracao = ? WHERE empresa_id = ? AND id IN ({marcas})",
                (duracao, g.empresa_id, *ids),
            )
        mensagem = f"Tempo de {duracao} s aplicado em {len(ids)} propaganda(s)."
    elif acao == "letreiro":
        if formulario.get("letreiro_modo") == "proprio" and not formulario.get("letreiro_texto", "").strip():
            flash("Escreva o texto do letreiro.", "erro")
            return redirect(url_for("painel.lista"))
        with conexao:
            conexao.execute(
                f"UPDATE propagandas SET letreiro = ? WHERE empresa_id = ? AND id IN ({marcas})",
                (ler_letreiro(formulario), g.empresa_id, *ids),
            )
        mensagem = f"Letreiro atualizado em {len(ids)} propaganda(s)."
    elif acao == "telas":
        modo = formulario.get("modo", "trocar")
        telas, grupos = ler_destinos(conexao, formulario)
        if formulario.get("destino") == "todas":
            with conexao:
                conexao.execute(
                    f"UPDATE propagandas SET para_todas = 1 WHERE empresa_id = ? AND id IN ({marcas})", (g.empresa_id, *ids)
                )
                for item_id in ids:
                    gravar_destinos(conexao, item_id, set(), set())
            mensagem = f"{len(ids)} propaganda(s) agora aparecem em todas as telas."
        elif not telas and not grupos:
            flash("Marque pelo menos uma tela ou grupo.", "erro")
            return redirect(url_for("painel.lista"))
        elif modo not in ("trocar", "acrescentar", "tirar"):
            abort(400)
        else:
            atuais = destinos_por_propaganda(conexao)
            with conexao:
                for item in itens:
                    destino = atuais.get(item["id"], {"telas": set(), "grupos": set()})
                    # "Todas as telas" não tem lista: acrescentar/tirar parte do zero.
                    telas_atuais = set() if item["para_todas"] else set(destino["telas"])
                    grupos_atuais = set() if item["para_todas"] else set(destino["grupos"])
                    if modo == "trocar":
                        novas_telas, novos_grupos = telas, grupos
                    elif modo == "acrescentar":
                        novas_telas, novos_grupos = telas_atuais | telas, grupos_atuais | grupos
                    else:
                        novas_telas, novos_grupos = telas_atuais - telas, grupos_atuais - grupos
                    conexao.execute("UPDATE propagandas SET para_todas = 0 WHERE id = ?", (item["id"],))
                    gravar_destinos(conexao, item["id"], novas_telas, novos_grupos)
            mensagem = f"Telas atualizadas em {len(ids)} propaganda(s)."
    elif acao == "excluir":
        for item in itens:
            _excluir(conexao, item)
        mensagem = f"{len(ids)} propaganda(s) excluída(s)."
    else:
        abort(400)
    log.info("“%s” aplicou “%s” em %d propaganda(s)", g.usuario["usuario"], acao, len(ids))
    flash(mensagem, "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/pausa", methods=["POST"])
@login_obrigatorio()
def pausa():
    """Pausa (ou retoma) todas as propagandas da loja em todas as TVs."""
    pausar = request.form.get("acao") == "pausar"
    db.gravar_config(g.empresa_id, "pausado", "1" if pausar else "")
    log.info("“%s” %s as propagandas", g.usuario["usuario"], "pausou" if pausar else "retomou")
    if pausar:
        flash("Propagandas pausadas. As TVs ficam com a tela preta em até 15 segundos.", "ok")
    else:
        flash("Propagandas no ar de novo.", "ok")
    return redirect(url_for("painel.lista"))


@bp.route("/letreiro", methods=["POST"])
@login_obrigatorio()
def letreiro():
    texto = request.form.get("letreiro", "").strip()[:500]
    db.gravar_config(g.empresa_id, "letreiro", texto)
    log.info("“%s” alterou o letreiro", g.usuario["usuario"])
    flash("Letreiro salvo.", "ok")
    return redirect(url_for("painel.lista"))
