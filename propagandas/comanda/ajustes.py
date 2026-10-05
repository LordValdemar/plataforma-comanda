"""Ajustes da Comanda de cada loja: local e mensagem do cupom e taxa de serviço.

Nome, CPF/CNPJ, contato, endereço e logo do cupom vêm do cadastro da empresa (página Empresa).
"""

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from src.domain.comanda import ErroComanda, taxa_percentual_valida

from .. import cadastro, db, modulos, planos
from .base import gravar_config, ler_config, papel_exigido
from .comandas import taxa_padrao

bp = Blueprint("comanda_ajustes", __name__, url_prefix="/comanda/ajustes")
bp.before_request(modulos.exigir("comanda"))


def dados_da_loja():
    """O que vai no cabeçalho e no fim do cupom (cadastro da empresa + ajustes da Comanda)."""
    empresa = planos.empresa(db.obter(), g.empresa_id)
    # Quem preencheu os dados na versão anterior (aqui nos ajustes) continua vendo-os até completar a Empresa.
    return {
        "nome_estabelecimento": g.usuario["empresa_nome"],
        "cnpj": cadastro.documento_formatado(empresa["documento"]) or ler_config("cnpj"),
        "email": empresa["email"] or ler_config("email"),
        "telefone": empresa["telefone"] or ler_config("telefone"),
        "endereco": cadastro.endereco_completo(empresa) or ler_config("endereco"),
        "logo": empresa["logo"] or ler_config("logo"),
        "local": ler_config("local"),
        "rodape_cupom": ler_config("rodape_cupom", "Obrigado pela preferência!"),
    }


@bp.route("/", methods=["GET", "POST"])
@papel_exigido()
def pagina():
    if request.method == "POST":
        try:
            taxa = taxa_percentual_valida(request.form.get("taxa_servico", "10"))
        except ErroComanda as erro:
            flash(str(erro), "erro")
            return redirect(url_for("comanda_ajustes.pagina"))
        gravar_config("local", request.form.get("local", "").strip()[:60])
        gravar_config("rodape_cupom", request.form.get("rodape_cupom", "").strip()[:160])
        gravar_config("taxa_servico", f"{taxa:g}")
        flash("Ajustes salvos. A nova taxa vale para as comandas abertas daqui em diante.", "ok")
        return redirect(url_for("comanda_ajustes.pagina"))
    return render_template("comanda/ajustes.html", dados=dados_da_loja(),
                           taxa_servico=f"{taxa_padrao():g}".replace(".", ","))
