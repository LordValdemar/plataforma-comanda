"""Tela da cozinha: pedidos por ordem de chegada, atualizada sozinha a cada poucos segundos."""

from datetime import timedelta

from flask import Blueprint, abort, g, render_template, request

from src.domain.comanda import ErroComanda
from src.domain.erros import NaoEncontrado

from .. import db, modulos
from .base import exigir_funcao, servico_de_comandas
from .formatos import agora_utc, hora, minutos_desde, para_texto_utc

bp = Blueprint("comanda_cozinha", __name__, url_prefix="/comanda")
bp.before_request(modulos.exigir("comanda"))

# A cozinha escolhe a situação direto (inclusive voltar uma etapa, se tocou errado).
SITUACOES = ("pendente", "preparando", "pronto", "entregue")
RECENTES_MINUTOS = 30  # itens entregues que ainda aparecem embaixo, para desfazer
RECENTES_MAXIMO = 15


def pedidos_da_cozinha(conexao):
    """Itens que passam pela cozinha e ainda não foram entregues, agrupados por comanda."""
    linhas = conexao.execute(
        "SELECT i.id, i.nome, i.quantidade, i.observacao, i.status, i.lancado_em, i.atualizado_em, "
        "c.id AS comanda_id, c.numero, c.mesa, u.usuario AS garcom "
        "FROM cmd_itens i JOIN cmd_comandas c ON c.id = i.comanda_id LEFT JOIN usuarios u ON u.id = i.lancado_por "
        "WHERE i.empresa_id = ? AND i.vai_cozinha = 1 AND i.status IN ('pendente', 'preparando', 'pronto') "
        "AND c.status != 'cancelada' ORDER BY i.lancado_em, i.id",
        (g.empresa_id,),
    ).fetchall()
    grupos = {}
    for linha in linhas:
        grupo = grupos.setdefault(linha["comanda_id"], {
            "comanda_id": linha["comanda_id"], "numero": linha["numero"], "mesa": linha["mesa"] or "",
            "desde": linha["lancado_em"], "itens": [],
        })
        grupo["itens"].append({
            "id": linha["id"],
            "nome": linha["nome"],
            "quantidade": linha["quantidade"],
            "observacao": linha["observacao"] or "",
            "status": linha["status"],
            "hora": hora(linha["lancado_em"]),
            "minutos": minutos_desde(linha["lancado_em"]),
            "garcom": linha["garcom"] or "",
        })
    resultado = list(grupos.values())
    for grupo in resultado:
        grupo["minutos"] = minutos_desde(grupo["desde"])
        grupo["tudo_pronto"] = all(item["status"] == "pronto" for item in grupo["itens"])
        del grupo["desde"]
    # Comandas com tudo pronto vão para o fim: o que falta fazer fica no alto da tela.
    resultado.sort(key=lambda grupo: grupo["tudo_pronto"])
    return resultado


def entregues_recentes(conexao):
    """Itens da cozinha entregues há pouco: se foi engano, a cozinha traz de volta."""
    limite = para_texto_utc(agora_utc() - timedelta(minutes=RECENTES_MINUTOS))
    linhas = conexao.execute(
        "SELECT i.id, i.nome, i.quantidade, i.atualizado_em, c.numero, c.mesa FROM cmd_itens i "
        "JOIN cmd_comandas c ON c.id = i.comanda_id "
        "WHERE i.empresa_id = ? AND i.vai_cozinha = 1 AND i.status = 'entregue' AND i.atualizado_em >= ? "
        "AND c.status != 'cancelada' ORDER BY i.atualizado_em DESC, i.id DESC LIMIT ?",
        (g.empresa_id, limite, RECENTES_MAXIMO),
    ).fetchall()
    return [
        {"id": linha["id"], "nome": linha["nome"], "quantidade": linha["quantidade"], "numero": linha["numero"],
         "mesa": linha["mesa"] or "", "hora": hora(linha["atualizado_em"])}
        for linha in linhas
    ]


@bp.route("/cozinha")
@exigir_funcao("cozinha")
def tela():
    return render_template("comanda/cozinha.html")


@bp.route("/api/cozinha")
@exigir_funcao("cozinha")
def api_pedidos():
    conexao = db.obter()
    return {"comandas": pedidos_da_cozinha(conexao), "recentes": entregues_recentes(conexao)}


@bp.route("/api/cozinha/itens/<int:item_id>", methods=["POST"])
@exigir_funcao("cozinha")
def api_mudar(item_id):
    novo = request.form.get("status", "")
    try:
        if novo not in SITUACOES:
            servico_de_comandas().comanda_do_item_ou_erro(item_id)  # item de outra loja: 404 antes do 400
            return {"erro": "situação inválida"}, 400
        servico_de_comandas().mudar_situacao(item_id, novo)
    except NaoEncontrado:
        abort(404)
    except ErroComanda as erro:
        return {"erro": str(erro)}, 409
    return {"status": novo}


@bp.route("/api/cozinha/comandas/<int:comanda_id>/pronto", methods=["POST"])
@exigir_funcao("cozinha")
def api_tudo_pronto(comanda_id):
    """Marca como prontos todos os itens da comanda que ainda estão na cozinha."""
    return {"alterados": servico_de_comandas().marcar_tudo_pronto(comanda_id)}
