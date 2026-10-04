"""Cardápio da loja: categorias e produtos (só o administrador altera)."""

import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import db, modulos
from .base import exigir_funcao
from .formatos import ValorInvalido, entrada_reais, ler_reais

bp = Blueprint("comanda_cardapio", __name__, url_prefix="/comanda/cardapio")
bp.before_request(modulos.exigir("comanda"))


def produtos_ativos(conexao):
    """Produtos à venda, na ordem do cardápio (categorias pela posição; sem categoria no fim)."""
    return conexao.execute(
        "SELECT p.*, c.nome AS categoria FROM cmd_produtos p LEFT JOIN cmd_categorias c ON c.id = p.categoria_id "
        "WHERE p.empresa_id = ? AND p.ativo = 1 ORDER BY c.id IS NULL, c.posicao, c.nome, p.nome",
        (g.empresa_id,),
    ).fetchall()


def agrupar(produtos):
    """[(categoria, [produtos])] mantendo a ordem do cardápio (o groupby do Jinja reordenaria por nome)."""
    grupos = []
    for produto in produtos:
        categoria = produto["categoria"] or "Outros"
        if not grupos or grupos[-1][0] != categoria:
            grupos.append((categoria, []))
        grupos[-1][1].append(produto)
    return grupos


def _dados_produto(form, conexao):
    nome = form.get("nome", "").strip()
    if not nome or len(nome) > 80:
        raise ValorInvalido("Informe o nome do produto (até 80 caracteres).")
    preco = ler_reais(form.get("preco"))
    codigo = form.get("codigo", "").strip() or None
    if codigo is not None and len(codigo) > 10:
        raise ValorInvalido("O código pode ter no máximo 10 caracteres.")
    categoria_id = form.get("categoria_id") or None
    if categoria_id is not None:
        if conexao.execute(
            "SELECT 1 FROM cmd_categorias WHERE id = ? AND empresa_id = ?", (categoria_id, g.empresa_id)
        ).fetchone() is None:
            raise ValorInvalido("Categoria não encontrada.")
    return {
        "nome": nome,
        "preco_centavos": preco,
        "codigo": codigo,
        "categoria_id": categoria_id,
        "vai_cozinha": 1 if form.get("vai_cozinha") else 0,
    }


def _categoria(conexao, categoria_id):
    categoria = conexao.execute(
        "SELECT * FROM cmd_categorias WHERE id = ? AND empresa_id = ?", (categoria_id, g.empresa_id)
    ).fetchone()
    if categoria is None:
        abort(404)
    return categoria


def _produto(conexao, produto_id):
    produto = conexao.execute(
        "SELECT * FROM cmd_produtos WHERE id = ? AND empresa_id = ?", (produto_id, g.empresa_id)
    ).fetchone()
    if produto is None:
        abort(404)
    return produto


@bp.route("/")
@exigir_funcao("cardapio")
def lista():
    conexao = db.obter()
    categorias = conexao.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM cmd_produtos p WHERE p.categoria_id = c.id) AS produtos "
        "FROM cmd_categorias c WHERE c.empresa_id = ? ORDER BY posicao, nome",
        (g.empresa_id,),
    ).fetchall()
    produtos = conexao.execute(
        "SELECT p.*, c.nome AS categoria FROM cmd_produtos p LEFT JOIN cmd_categorias c ON c.id = p.categoria_id "
        "WHERE p.empresa_id = ? ORDER BY p.ativo DESC, c.id IS NULL, c.posicao, c.nome, p.nome",
        (g.empresa_id,),
    ).fetchall()
    return render_template("comanda/cardapio.html", categorias=categorias, produtos=produtos, entrada_reais=entrada_reais)


@bp.route("/categorias", methods=["POST"])
@exigir_funcao("cardapio")
def nova_categoria():
    nome = request.form.get("nome", "").strip()
    conexao = db.obter()
    if not nome or len(nome) > 40:
        flash("Informe o nome da categoria (até 40 caracteres).", "erro")
    else:
        try:
            with conexao:
                proxima = conexao.execute(
                    "SELECT COALESCE(MAX(posicao), 0) + 1 FROM cmd_categorias WHERE empresa_id = ?", (g.empresa_id,)
                ).fetchone()[0]
                conexao.execute(
                    "INSERT INTO cmd_categorias (empresa_id, nome, posicao) VALUES (?, ?, ?)", (g.empresa_id, nome, proxima)
                )
        except sqlite3.IntegrityError:
            flash(f"A categoria “{nome}” já existe.", "erro")
        else:
            flash(f"Categoria “{nome}” criada.", "ok")
    return redirect(url_for("comanda_cardapio.lista"))


@bp.route("/categorias/<int:categoria_id>", methods=["POST"])
@exigir_funcao("cardapio")
def alterar_categoria(categoria_id):
    conexao = db.obter()
    categoria = _categoria(conexao, categoria_id)
    acao = request.form.get("acao")
    if acao == "excluir":
        with conexao:
            # Os produtos ficam "sem categoria" (ON DELETE SET NULL).
            conexao.execute("DELETE FROM cmd_categorias WHERE id = ?", (categoria_id,))
        flash(f"Categoria “{categoria['nome']}” excluída.", "ok")
    elif acao in ("subir", "descer"):
        ordem = [linha["id"] for linha in conexao.execute(
            "SELECT id FROM cmd_categorias WHERE empresa_id = ? ORDER BY posicao, nome", (g.empresa_id,))]
        i = ordem.index(categoria_id)
        j = i - 1 if acao == "subir" else i + 1
        if 0 <= j < len(ordem):
            ordem[i], ordem[j] = ordem[j], ordem[i]
            with conexao:
                for posicao, ident in enumerate(ordem, start=1):
                    conexao.execute("UPDATE cmd_categorias SET posicao = ? WHERE id = ?", (posicao, ident))
    elif acao == "renomear":
        nome = request.form.get("nome", "").strip()
        if not nome or len(nome) > 40:
            flash("Informe o nome da categoria (até 40 caracteres).", "erro")
        else:
            try:
                with conexao:
                    conexao.execute("UPDATE cmd_categorias SET nome = ? WHERE id = ?", (nome, categoria_id))
            except sqlite3.IntegrityError:
                flash(f"A categoria “{nome}” já existe.", "erro")
    else:
        abort(400)
    return redirect(url_for("comanda_cardapio.lista"))


@bp.route("/produtos", methods=["POST"])
@exigir_funcao("cardapio")
def novo_produto():
    conexao = db.obter()
    try:
        dados = _dados_produto(request.form, conexao)
        with conexao:
            conexao.execute(
                "INSERT INTO cmd_produtos (empresa_id, nome, preco_centavos, codigo, categoria_id, vai_cozinha) "
                "VALUES (:empresa_id, :nome, :preco_centavos, :codigo, :categoria_id, :vai_cozinha)",
                {**dados, "empresa_id": g.empresa_id},
            )
    except ValorInvalido as erro:
        flash(str(erro), "erro")
    except sqlite3.IntegrityError:
        flash("Já existe um produto com esse código.", "erro")
    else:
        flash(f"“{dados['nome']}” adicionado ao cardápio.", "ok")
    return redirect(url_for("comanda_cardapio.lista"))


@bp.route("/produtos/<int:produto_id>", methods=["POST"])
@exigir_funcao("cardapio")
def alterar_produto(produto_id):
    conexao = db.obter()
    produto = _produto(conexao, produto_id)
    acao = request.form.get("acao")
    if acao == "salvar":
        try:
            dados = _dados_produto(request.form, conexao)
            with conexao:
                conexao.execute(
                    "UPDATE cmd_produtos SET nome = :nome, preco_centavos = :preco_centavos, codigo = :codigo, "
                    "categoria_id = :categoria_id, vai_cozinha = :vai_cozinha WHERE id = :id",
                    {**dados, "id": produto_id},
                )
        except ValorInvalido as erro:
            flash(str(erro), "erro")
        except sqlite3.IntegrityError:
            flash("Já existe um produto com esse código.", "erro")
        else:
            flash(f"“{dados['nome']}” atualizado. Itens já lançados mantêm o preço da hora do pedido.", "ok")
    elif acao == "ativo":
        with conexao:
            conexao.execute("UPDATE cmd_produtos SET ativo = 1 - ativo WHERE id = ?", (produto_id,))
        flash(f"“{produto['nome']}” {'fora do cardápio' if produto['ativo'] else 'de volta ao cardápio'}.", "ok")
    elif acao == "excluir":
        with conexao:
            # Os itens já vendidos guardam nome e preço: o histórico continua certo.
            conexao.execute("DELETE FROM cmd_produtos WHERE id = ?", (produto_id,))
        flash(f"“{produto['nome']}” excluído.", "ok")
    else:
        abort(400)
    return redirect(url_for("comanda_cardapio.lista"))
