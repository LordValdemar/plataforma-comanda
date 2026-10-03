"""Comandas: abrir, lançar pedidos, fechar a conta (pagamentos) e imprimir o cupom."""

import logging
import re
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import db, modulos
from .base import auditar, ler_config, papel_exigido, pode, pode_fechar_conta
from .cardapio import agrupar, produtos_ativos
from .formatos import ValorInvalido, agora_utc, entrada_reais, hoje_local, intervalo_utc, ler_reais, para_texto_utc, reais

bp = Blueprint("comanda", __name__, url_prefix="/comanda")
bp.before_request(modulos.exigir("comanda"))
log = logging.getLogger("propagandas.comanda")

FORMAS = {"dinheiro": "Dinheiro", "pix": "PIX", "debito": "Cartão de débito", "credito": "Cartão de crédito", "outro": "Outro"}
STATUS_ITEM = {
    "pendente": "Aguardando", "preparando": "Preparando", "pronto": "Pronto", "entregue": "Entregue", "cancelado": "Cancelado",
}
MAX_QUANTIDADE = 999


class ErroComanda(ValueError):
    """Operação não permitida (mensagem pode ser mostrada na tela)."""


# ---------------------------------------------------------------------------
# Regras (sem Flask, testáveis e usadas também pelos relatórios)
# ---------------------------------------------------------------------------

def taxa_padrao():
    try:
        return max(0.0, min(30.0, float(ler_config("taxa_servico", "10").replace(",", "."))))
    except ValueError:
        return 10.0


def totais(conexao, comanda):
    """Subtotal, taxa, desconto, total, pago e quanto falta (tudo em centavos)."""
    subtotal = conexao.execute(
        "SELECT COALESCE(SUM(preco_centavos * quantidade), 0) FROM cmd_itens WHERE comanda_id = ? AND status != 'cancelado'",
        (comanda["id"],),
    ).fetchone()[0]
    taxa = round(subtotal * comanda["taxa_percentual"] / 100) if comanda["cobrar_taxa"] else 0
    desconto = min(comanda["desconto_centavos"], subtotal + taxa)
    total = subtotal + taxa - desconto
    pagamentos = conexao.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) AS pago, COALESCE(SUM(recebido_centavos - valor_centavos), 0) AS troco "
        "FROM cmd_pagamentos WHERE comanda_id = ?",
        (comanda["id"],),
    ).fetchone()
    return {
        "subtotal": subtotal,
        "taxa": taxa,
        "desconto": desconto,
        "total": total,
        "pago": pagamentos["pago"],
        "troco": pagamentos["troco"],
        "restante": total - pagamentos["pago"],
    }


def abrir(conexao, empresa_id, numero, mesa="", cliente="", usuario_id=None):
    try:
        numero = int(str(numero).strip())
    except ValueError:
        raise ErroComanda("Informe o número da comanda.") from None
    if not 1 <= numero <= 99999:
        raise ErroComanda("O número da comanda vai de 1 a 99999.")
    try:
        with conexao:
            cursor = conexao.execute(
                "INSERT INTO cmd_comandas (empresa_id, numero, mesa, cliente, taxa_percentual, aberta_por) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (empresa_id, numero, mesa.strip()[:20] or None, cliente.strip()[:60] or None, taxa_padrao(), usuario_id),
            )
    except sqlite3.IntegrityError:
        raise ErroComanda(f"A comanda {numero} já está aberta.") from None
    return cursor.lastrowid


def lancar(conexao, comanda, pedidos, usuario_id=None):
    """pedidos: lista de (produto_id, quantidade, observação). Retorna quantos itens entraram."""
    if comanda["status"] != "aberta":
        raise ErroComanda("Esta comanda já foi fechada.")
    lancados = 0
    agora = para_texto_utc(agora_utc())
    with conexao:
        for produto_id, quantidade, observacao in pedidos:
            if not 1 <= quantidade <= MAX_QUANTIDADE:
                raise ErroComanda(f"Quantidade inválida: {quantidade}.")
            produto = conexao.execute(
                "SELECT * FROM cmd_produtos WHERE id = ? AND empresa_id = ? AND ativo = 1", (produto_id, comanda["empresa_id"])
            ).fetchone()
            if produto is None:
                raise ErroComanda("Um dos produtos saiu do cardápio. Confira o pedido.")
            # Produto que não passa pela cozinha (ex.: refrigerante em lata) já sai entregue.
            status = "pendente" if produto["vai_cozinha"] else "entregue"
            conexao.execute(
                "INSERT INTO cmd_itens (empresa_id, comanda_id, produto_id, nome, preco_centavos, quantidade, observacao, "
                "vai_cozinha, status, lancado_por, lancado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (comanda["empresa_id"], comanda["id"], produto["id"], produto["nome"], produto["preco_centavos"], quantidade,
                 (observacao or "").strip()[:120] or None, produto["vai_cozinha"], status, usuario_id, agora, agora),
            )
            lancados += 1
    return lancados


def mudar_status_item(conexao, item, novo):
    """Andamento do item: pendente → preparando → pronto → entregue (e voltar um passo)."""
    if item["status"] == "cancelado":
        raise ErroComanda("Este item foi cancelado.")
    if novo not in ("pendente", "preparando", "pronto", "entregue"):
        raise ErroComanda("Situação inválida.")
    with conexao:
        conexao.execute(
            "UPDATE cmd_itens SET status = ?, atualizado_em = ? WHERE id = ?",
            (novo, para_texto_utc(agora_utc()), item["id"]),
        )


def cancelar_item(conexao, comanda, item, motivo, usuario):
    if comanda["status"] != "aberta":
        raise ErroComanda("A comanda já foi fechada: reabra-a para cancelar itens.")
    if item["status"] == "cancelado":
        raise ErroComanda("Este item já foi cancelado.")
    # O garçom só desfaz um engano antes de a cozinha começar; depois disso, é com o caixa.
    if usuario["papel"] == "garcom" and not (item["status"] == "pendente" or not item["vai_cozinha"]):
        raise ErroComanda("A cozinha já começou este item. Peça ao caixa para cancelar.")
    motivo = (motivo or "").strip()[:120]
    if not motivo:
        raise ErroComanda("Informe o motivo do cancelamento.")
    with conexao:
        conexao.execute(
            "UPDATE cmd_itens SET status = 'cancelado', cancelado_por = ?, motivo_cancelamento = ?, atualizado_em = ? "
            "WHERE id = ?",
            (usuario["id"], motivo, para_texto_utc(agora_utc()), item["id"]),
        )
        auditar(conexao, "cancelar item", f"{item['quantidade']}x {item['nome']}: {motivo}", comanda["id"])


def registrar_pagamento(conexao, comanda, forma, valor, usuario_id=None):
    """Abate `valor` da conta. Em dinheiro, o que passar do restante vira troco."""
    if comanda["status"] != "aberta":
        raise ErroComanda("Esta comanda já foi fechada.")
    if forma not in FORMAS:
        raise ErroComanda("Forma de pagamento inválida.")
    if valor <= 0:
        raise ErroComanda("Informe o valor do pagamento.")
    restante = totais(conexao, comanda)["restante"]
    if restante <= 0:
        raise ErroComanda("Esta conta já está paga.")
    if valor > restante and forma != "dinheiro":
        raise ErroComanda(f"O valor passa do que falta pagar ({reais(restante)}). Só pagamento em dinheiro tem troco.")
    with conexao:
        conexao.execute(
            "INSERT INTO cmd_pagamentos (empresa_id, comanda_id, forma, valor_centavos, recebido_centavos, registrado_por) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (comanda["empresa_id"], comanda["id"], forma, min(valor, restante), valor, usuario_id),
        )
    return max(0, valor - restante)  # troco


def fechar(conexao, comanda, usuario_id=None):
    if comanda["status"] != "aberta":
        raise ErroComanda("Esta comanda já foi fechada.")
    contas = totais(conexao, comanda)
    if contas["restante"] > 0:
        raise ErroComanda(f"Ainda falta receber {reais(contas['restante'])}.")
    if contas["restante"] < 0:
        raise ErroComanda("Os pagamentos passam do total (o desconto mudou?). Remova um pagamento e lance de novo.")
    with conexao:
        conexao.execute(
            "UPDATE cmd_comandas SET status = 'fechada', total_centavos = ?, fechada_por = ?, fechada_em = ? WHERE id = ?",
            (contas["total"], usuario_id, para_texto_utc(agora_utc()), comanda["id"]),
        )
    log.info("Comanda %s fechada: %s", comanda["numero"], reais(contas["total"]))
    return contas


def buscar(conexao, comanda_id):
    # Só comandas da própria loja: o id de outra loja dá 404.
    comanda = conexao.execute(
        "SELECT * FROM cmd_comandas WHERE id = ? AND empresa_id = ?", (comanda_id, g.empresa_id)
    ).fetchone()
    if comanda is None:
        abort(404)
    return comanda


def _itens(conexao, comanda_id):
    return conexao.execute(
        "SELECT i.*, u.usuario AS garcom FROM cmd_itens i LEFT JOIN usuarios u ON u.id = i.lancado_por "
        "WHERE comanda_id = ? ORDER BY i.id",
        (comanda_id,),
    ).fetchall()


def _voltar(comanda_id):
    return redirect(url_for("comanda.detalhe", comanda_id=comanda_id))


# ---------------------------------------------------------------------------
# Rotas: garçom e caixa
# ---------------------------------------------------------------------------

@bp.route("/")
@papel_exigido("caixa", "garcom")
def lista():
    conexao = db.obter()
    numero = request.args.get("numero", "").strip()
    if numero:
        # Busca pelo número do cartão: abre a comanda, ou oferece abrir uma nova.
        achada = conexao.execute(
            "SELECT id FROM cmd_comandas WHERE empresa_id = ? AND numero = ? AND status = 'aberta'", (g.empresa_id, numero)
        ).fetchone() if numero.isdigit() else None
        if achada:
            return _voltar(achada["id"])
    abertas = conexao.execute(
        """
        SELECT c.*,
               (SELECT COALESCE(SUM(preco_centavos * quantidade), 0) FROM cmd_itens
                 WHERE comanda_id = c.id AND status != 'cancelado') AS consumo,
               (SELECT COUNT(*) FROM cmd_itens WHERE comanda_id = c.id AND status = 'pronto') AS prontos,
               (SELECT COUNT(*) FROM cmd_itens WHERE comanda_id = c.id AND status IN ('pendente', 'preparando')) AS na_cozinha
        FROM cmd_comandas c WHERE c.empresa_id = ? AND status = 'aberta' ORDER BY numero
        """,
        (g.empresa_id,),
    ).fetchall()
    prontos = conexao.execute(
        "SELECT i.*, c.numero, c.mesa FROM cmd_itens i JOIN cmd_comandas c ON c.id = i.comanda_id "
        "WHERE i.empresa_id = ? AND i.status = 'pronto' AND c.status != 'cancelada' ORDER BY i.atualizado_em",
        (g.empresa_id,),
    ).fetchall()
    return render_template("comanda/comandas.html", abertas=abertas, prontos=prontos, numero_buscado=numero)


@bp.route("/", methods=["POST"])
@papel_exigido("caixa", "garcom")
def nova():
    conexao = db.obter()
    numero = request.form.get("numero", "")
    try:
        comanda_id = abrir(conexao, g.empresa_id, numero, request.form.get("mesa", ""), request.form.get("cliente", ""), g.usuario["id"])
    except ErroComanda as erro:
        aberta = conexao.execute(
            "SELECT id FROM cmd_comandas WHERE empresa_id = ? AND numero = ? AND status = 'aberta'",
            (g.empresa_id, numero.strip()),
        ).fetchone()
        flash(str(erro), "erro")
        return _voltar(aberta["id"]) if aberta else redirect(url_for("comanda.lista"))
    return _voltar(comanda_id)


@bp.route("/<int:comanda_id>")
@papel_exigido("caixa", "garcom")
def detalhe(comanda_id):
    conexao = db.obter()
    comanda = buscar(conexao, comanda_id)
    return render_template(
        "comanda/comanda.html",
        comanda=comanda,
        itens=_itens(conexao, comanda_id),
        grupos=agrupar(produtos_ativos(conexao)),
        contas=totais(conexao, comanda),
        status_item=STATUS_ITEM,
    )


@bp.route("/<int:comanda_id>/dados", methods=["POST"])
@papel_exigido("caixa", "garcom")
def alterar_dados(comanda_id):
    conexao = db.obter()
    comanda = buscar(conexao, comanda_id)
    if comanda["status"] != "aberta":
        flash("Esta comanda já foi fechada.", "erro")
    else:
        mesa = request.form.get("mesa", "").strip()[:20] or None
        cliente = request.form.get("cliente", "").strip()[:60] or None
        with conexao:
            conexao.execute("UPDATE cmd_comandas SET mesa = ?, cliente = ? WHERE id = ?", (mesa, cliente, comanda_id))
        flash("Dados da comanda atualizados.", "ok")
    return _voltar(comanda_id)


def _pedidos_do_formulario(form, conexao):
    """Lê o formulário de lançamento: qtd_<id>/obs_<id> do cardápio e o lançamento rápido por código."""
    pedidos = []
    for chave, valor in form.items():
        encontrado = re.fullmatch(r"qtd_(\d+)", chave)
        if encontrado and valor.strip() not in ("", "0"):
            try:
                quantidade = int(valor)
            except ValueError:
                raise ErroComanda(f"Quantidade inválida: “{valor}”.") from None
            produto_id = int(encontrado.group(1))
            pedidos.append((produto_id, quantidade, form.get(f"obs_{produto_id}", "")))
    codigo = form.get("codigo", "").strip()
    if codigo:
        produto = conexao.execute(
            "SELECT id FROM cmd_produtos WHERE empresa_id = ? AND codigo = ? AND ativo = 1", (g.empresa_id, codigo)
        ).fetchone()
        if produto is None:
            raise ErroComanda(f"Nenhum produto com o código “{codigo}”.")
        try:
            quantidade = int(form.get("codigo_qtd") or 1)
        except ValueError:
            raise ErroComanda("Quantidade inválida.") from None
        pedidos.append((produto["id"], quantidade, form.get("codigo_obs", "")))
    return pedidos


@bp.route("/<int:comanda_id>/itens", methods=["POST"])
@papel_exigido("caixa", "garcom")
def lancar_itens(comanda_id):
    conexao = db.obter()
    comanda = buscar(conexao, comanda_id)
    try:
        pedidos = _pedidos_do_formulario(request.form, conexao)
        if not pedidos:
            raise ErroComanda("Escolha pelo menos um produto.")
        lancados = lancar(conexao, comanda, pedidos, g.usuario["id"])
    except ErroComanda as erro:
        flash(str(erro), "erro")
    else:
        flash(f"{lancados} item(ns) lançado(s) na comanda {comanda['numero']}.", "ok")
    return _voltar(comanda_id)


@bp.route("/<int:comanda_id>/itens/<int:item_id>", methods=["POST"])
@papel_exigido("caixa", "garcom")
def alterar_item(comanda_id, item_id):
    conexao = db.obter()
    comanda = buscar(conexao, comanda_id)
    item = conexao.execute("SELECT * FROM cmd_itens WHERE id = ? AND comanda_id = ?", (item_id, comanda_id)).fetchone()
    if item is None:
        abort(404)
    acao = request.form.get("acao")
    try:
        if acao == "entregue":
            mudar_status_item(conexao, item, "entregue")
            if request.form.get("voltar") == "lista":
                flash(f"{item['quantidade']}× {item['nome']} entregue na comanda {comanda['numero']}. "
                      "Foi engano? Abra a comanda e toque em “↩ Não entregue”.", "ok")
        elif acao == "pronto":
            # Desfaz um "Entregue" tocado sem querer: o item volta para a lista de prontos.
            if item["status"] != "entregue" or not item["vai_cozinha"]:
                raise ErroComanda("Este item não pode voltar para pronto.")
            mudar_status_item(conexao, item, "pronto")
        elif acao == "cancelar":
            cancelar_item(conexao, comanda, item, request.form.get("motivo"), g.usuario)
            flash(f"Item “{item['nome']}” cancelado.", "ok")
        else:
            abort(400)
    except ErroComanda as erro:
        flash(str(erro), "erro")
    if request.form.get("voltar") == "lista":
        return redirect(url_for("comanda.lista"))
    return _voltar(comanda_id)


# ---------------------------------------------------------------------------
# Rotas: caixa (fechamento, cancelamento, histórico)
# ---------------------------------------------------------------------------

@bp.route("/<int:comanda_id>/fechar", methods=["GET", "POST"])
@papel_exigido("caixa", "garcom")
def fechamento(comanda_id):
    if not pode_fechar_conta():
        abort(403)
    conexao = db.obter()
    comanda = buscar(conexao, comanda_id)
    if request.method == "POST":
        acao = request.form.get("acao")
        try:
            if acao == "ajustar":
                _ajustar_conta(conexao, comanda)
            elif acao == "pagar":
                troco = registrar_pagamento(
                    conexao, comanda, request.form.get("forma", ""), ler_reais(request.form.get("valor")), g.usuario["id"]
                )
                if troco:
                    flash(f"Troco: {reais(troco)}", "ok")
            elif acao == "remover_pagamento":
                if comanda["status"] != "aberta":
                    raise ErroComanda("Esta comanda já foi fechada.")
                with conexao:
                    pagamento = conexao.execute(
                        "SELECT * FROM cmd_pagamentos WHERE id = ? AND comanda_id = ?",
                        (request.form.get("pagamento_id"), comanda_id),
                    ).fetchone()
                    if pagamento:
                        conexao.execute("DELETE FROM cmd_pagamentos WHERE id = ?", (pagamento["id"],))
                        auditar(conexao, "remover pagamento",
                                   f"{FORMAS[pagamento['forma']]} {reais(pagamento['valor_centavos'])}", comanda_id)
            elif acao == "finalizar":
                fechar(conexao, comanda, g.usuario["id"])
                flash(f"Comanda {comanda['numero']} fechada. O cartão já pode ser usado de novo.", "ok")
                return redirect(url_for("comanda.cupom", comanda_id=comanda_id, imprimir=1))
            else:
                abort(400)
        except (ErroComanda, ValorInvalido) as erro:
            flash(str(erro), "erro")
        return redirect(url_for("comanda.fechamento", comanda_id=comanda_id))

    contas = totais(conexao, comanda)
    pagamentos = conexao.execute("SELECT * FROM cmd_pagamentos WHERE comanda_id = ? ORDER BY id", (comanda_id,)).fetchall()
    na_cozinha = conexao.execute(
        "SELECT COUNT(*) FROM cmd_itens WHERE comanda_id = ? AND status IN ('pendente', 'preparando', 'pronto')", (comanda_id,)
    ).fetchone()[0]
    return render_template(
        "comanda/fechar.html", comanda=comanda, contas=contas, pagamentos=pagamentos, formas=FORMAS,
        itens=_itens(conexao, comanda_id), na_cozinha=na_cozinha, entrada_reais=entrada_reais,
    )


def _ajustar_conta(conexao, comanda):
    if comanda["status"] != "aberta":
        raise ErroComanda("Esta comanda já foi fechada.")
    cobrar_taxa = 1 if request.form.get("cobrar_taxa") else 0
    # O garçom autorizado tira ou devolve a taxa de serviço; desconto, só caixa e administrador.
    if pode("caixa"):
        desconto = ler_reais(request.form.get("desconto"))
    elif "desconto" in request.form:
        abort(403)
    else:
        desconto = comanda["desconto_centavos"]
    contas = totais(conexao, comanda)
    taxa = round(contas["subtotal"] * comanda["taxa_percentual"] / 100) if cobrar_taxa else 0
    if desconto > contas["subtotal"] + taxa:
        raise ErroComanda("O desconto não pode passar do valor da conta.")
    with conexao:
        conexao.execute(
            "UPDATE cmd_comandas SET cobrar_taxa = ?, desconto_centavos = ? WHERE id = ?", (cobrar_taxa, desconto, comanda["id"])
        )
        if desconto != comanda["desconto_centavos"]:
            auditar(conexao, "desconto", f"{reais(comanda['desconto_centavos'])} → {reais(desconto)}", comanda["id"])
        if cobrar_taxa != comanda["cobrar_taxa"]:
            auditar(conexao, "taxa de serviço", "cobrada" if cobrar_taxa else "retirada", comanda["id"])


@bp.route("/<int:comanda_id>/cancelar", methods=["POST"])
@papel_exigido("caixa")
def cancelar(comanda_id):
    conexao = db.obter()
    comanda = buscar(conexao, comanda_id)
    motivo = request.form.get("motivo", "").strip()[:120]
    if comanda["status"] != "aberta":
        flash("Só dá para cancelar uma comanda aberta.", "erro")
    elif not motivo:
        flash("Informe o motivo do cancelamento.", "erro")
    elif conexao.execute("SELECT 1 FROM cmd_pagamentos WHERE comanda_id = ?", (comanda_id,)).fetchone():
        flash("Esta comanda tem pagamentos. Remova-os antes de cancelar.", "erro")
    else:
        agora = para_texto_utc(agora_utc())
        with conexao:
            conexao.execute(
                "UPDATE cmd_comandas SET status = 'cancelada', motivo_cancelamento = ?, fechada_por = ?, fechada_em = ?, "
                "total_centavos = 0 WHERE id = ?",
                (motivo, g.usuario["id"], agora, comanda_id),
            )
            conexao.execute(
                "UPDATE cmd_itens SET status = 'cancelado', cancelado_por = ?, motivo_cancelamento = ?, atualizado_em = ? "
                "WHERE comanda_id = ? AND status != 'cancelado'",
                (g.usuario["id"], "comanda cancelada", agora, comanda_id),
            )
            auditar(conexao, "cancelar comanda", motivo, comanda_id)
        flash(f"Comanda {comanda['numero']} cancelada.", "ok")
        return redirect(url_for("comanda.lista"))
    return _voltar(comanda_id)


@bp.route("/<int:comanda_id>/reabrir", methods=["POST"])
@papel_exigido()
def reabrir(comanda_id):
    conexao = db.obter()
    comanda = buscar(conexao, comanda_id)
    if comanda["status"] != "fechada":
        flash("Só dá para reabrir uma comanda fechada.", "erro")
        return redirect(url_for("comanda.cupom", comanda_id=comanda_id))
    try:
        with conexao:
            conexao.execute(
                "UPDATE cmd_comandas SET status = 'aberta', total_centavos = NULL, fechada_por = NULL, fechada_em = NULL "
                "WHERE id = ?",
                (comanda_id,),
            )
            auditar(conexao, "reabrir comanda", f"total era {reais(comanda['total_centavos'])}", comanda_id)
    except sqlite3.IntegrityError:
        flash(f"Já existe outra comanda {comanda['numero']} aberta. Feche-a antes de reabrir esta.", "erro")
        return redirect(url_for("comanda.cupom", comanda_id=comanda_id))
    flash(f"Comanda {comanda['numero']} reaberta.", "ok")
    return redirect(url_for("comanda.fechamento", comanda_id=comanda_id))


@bp.route("/<int:comanda_id>/cupom")
@papel_exigido("caixa", "garcom")
def cupom(comanda_id):
    """Conferência (comanda aberta) ou recibo (fechada), no tamanho da impressora térmica de 80 mm."""
    conexao = db.obter()
    comanda = buscar(conexao, comanda_id)
    itens = conexao.execute(
        "SELECT nome, preco_centavos, SUM(quantidade) AS quantidade FROM cmd_itens "
        "WHERE comanda_id = ? AND status != 'cancelado' GROUP BY nome, preco_centavos ORDER BY MIN(id)",
        (comanda_id,),
    ).fetchall()
    pagamentos = conexao.execute("SELECT * FROM cmd_pagamentos WHERE comanda_id = ? ORDER BY id", (comanda_id,)).fetchall()
    auditoria = conexao.execute(
        "SELECT a.*, u.usuario FROM cmd_auditoria a LEFT JOIN usuarios u ON u.id = a.usuario_id "
        "WHERE comanda_id = ? ORDER BY a.id",
        (comanda_id,),
    ).fetchall() if pode("caixa") else []
    return render_template(
        "comanda/cupom.html", comanda=comanda, itens=itens, pagamentos=pagamentos, formas=FORMAS,
        contas=totais(conexao, comanda), auditoria=auditoria,
        estabelecimento=ler_config("nome_estabelecimento") or g.usuario["empresa_nome"],
        endereco=ler_config("endereco"), rodape=ler_config("rodape_cupom", "Obrigado pela preferência!"),
    )


@bp.route("/historico")
@papel_exigido("caixa")
def historico():
    from .relatorios import ler_periodo  # evita importação circular

    inicio, fim = ler_periodo(request.args, padrao=hoje_local())
    de, ate = intervalo_utc(inicio, fim)
    comandas = db.obter().execute(
        "SELECT c.*, u.usuario AS fechada_por_nome FROM cmd_comandas c LEFT JOIN usuarios u ON u.id = c.fechada_por "
        "WHERE c.empresa_id = ? AND c.status != 'aberta' AND c.fechada_em >= ? AND c.fechada_em < ? ORDER BY c.fechada_em DESC",
        (g.empresa_id, de, ate),
    ).fetchall()
    return render_template("comanda/historico.html", comandas=comandas, inicio=inicio, fim=fim)
