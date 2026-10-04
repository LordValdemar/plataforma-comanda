"""Ajustes da Comanda de cada loja: dados e logo do cupom e taxa de serviço."""

import base64

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import modulos
from .base import gravar_config, ler_config, papel_exigido
from .comandas import taxa_padrao

bp = Blueprint("comanda_ajustes", __name__, url_prefix="/comanda/ajustes")
bp.before_request(modulos.exigir("comanda"))

LOGO_MAX_BYTES = 300 * 1024  # o logo vai junto em cada cupom: pequeno imprime melhor e mais rápido
TIPOS_DE_LOGO = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}
# Campos de texto do cupom: (chave, tamanho máximo).
CAMPOS = (("nome_estabelecimento", 80), ("cnpj", 20), ("email", 80), ("endereco", 160), ("telefone", 40),
          ("local", 60), ("rodape_cupom", 160))


def dados_da_loja():
    """O que vai no cabeçalho e no fim do cupom."""
    dados = {chave: ler_config(chave) for chave, _ in CAMPOS}
    dados["nome_estabelecimento"] = dados["nome_estabelecimento"] or g.usuario["empresa_nome"]
    dados["rodape_cupom"] = ler_config("rodape_cupom", "Obrigado pela preferência!")
    dados["logo"] = ler_config("logo")
    return dados


def _ler_logo(arquivo):
    """PNG ou JPG pequeno → data URI (guardado nas configurações da loja). Erro em texto, se não servir."""
    conteudo = arquivo.read(LOGO_MAX_BYTES + 1)
    if len(conteudo) > LOGO_MAX_BYTES:
        return None, "O logo pode ter até 300 KB. Diminua a imagem e envie de novo."
    tipo = next((t for inicio, t in TIPOS_DE_LOGO.items() if conteudo.startswith(inicio)), None)
    if tipo is None:
        return None, "O logo precisa ser uma imagem PNG ou JPG."
    return f"data:{tipo};base64,{base64.b64encode(conteudo).decode()}", None


@bp.route("/", methods=["GET", "POST"])
@papel_exigido()
def pagina():
    if request.method == "POST":
        if request.form.get("acao") == "remover_logo":
            gravar_config("logo", "")
            flash("Logo removido do cupom.", "ok")
            return redirect(url_for("comanda_ajustes.pagina"))
        try:
            taxa = float(request.form.get("taxa_servico", "10").replace(",", ".") or 0)
        except ValueError:
            taxa = -1
        if not 0 <= taxa <= 30:
            flash("A taxa de serviço vai de 0 a 30%.", "erro")
            return redirect(url_for("comanda_ajustes.pagina"))
        arquivo = request.files.get("logo")
        if arquivo and arquivo.filename:
            logo, erro = _ler_logo(arquivo)
            if erro:
                flash(erro, "erro")
                return redirect(url_for("comanda_ajustes.pagina"))
            gravar_config("logo", logo)
        for chave, tamanho in CAMPOS:
            if chave in request.form:
                gravar_config(chave, request.form.get(chave, "").strip()[:tamanho])
        gravar_config("taxa_servico", f"{taxa:g}")
        flash("Ajustes salvos. A nova taxa vale para as comandas abertas daqui em diante.", "ok")
        return redirect(url_for("comanda_ajustes.pagina"))
    return render_template("comanda/ajustes.html", dados=dados_da_loja(),
                           taxa_servico=f"{taxa_padrao():g}".replace(".", ","))
