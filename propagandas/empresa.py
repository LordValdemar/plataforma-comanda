"""Configurações da empresa, uso do plano e exportação dos dados (portabilidade, LGPD).

As regras do cadastro ficam em src/domain/empresas; aqui ficam as rotas e o pacote de exportação.
"""

import csv
import io
import json
import logging
import os
import tempfile
import zipfile

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, send_file, url_for

from src.domain.empresas import LOGO_MAX_BYTES, UFS, CadastroInvalido, Configuracoes, documento_formatado

from . import agenda, alertas, db, planos
from .auth import login_obrigatorio

bp = Blueprint("empresa", __name__)
log = logging.getLogger("propagandas.empresa")


@bp.route("/empresa", methods=["GET", "POST"])
@login_obrigatorio("admin")
def configuracoes():
    conexao = db.obter()
    empresas = planos.servico(conexao)
    if request.method == "POST" and request.form.get("acao") == "remover_logo":
        empresas.remover_logo(g.empresa_id)
        flash("Logo removido.", "ok")
        return redirect(url_for("empresa.configuracoes"))
    if request.method == "POST":
        arquivo = request.files.get("logo")
        codigo_atual = g.usuario["empresa_slug"] or ""
        configuracoes = Configuracoes(
            nome=request.form.get("nome", ""),
            codigo=request.form.get("codigo", codigo_atual),   # sem o campo no formulário, o código continua o mesmo
            alerta_emails=request.form.get("alerta_emails", ""), alerta_webhook=request.form.get("alerta_webhook", ""),
            cadastro=request.form, logo=arquivo.read(LOGO_MAX_BYTES + 1) if arquivo and arquivo.filename else None,
        )
        try:
            empresas.salvar_configuracoes(g.empresa_id, configuracoes)
        except CadastroInvalido as erro:
            flash(str(erro), "erro")
        else:
            codigo = configuracoes.codigo.strip().lower()
            if codigo != codigo_atual:
                log.info("“%s” trocou o código da loja de “%s” para “%s”", g.usuario["usuario"], codigo_atual, codigo)
                flash(f"Código da loja trocado. O endereço de entrada da equipe agora é "
                      f"{url_for('auth.entrar_na_loja', slug=codigo, _external=True)}", "ok")
            log.info("“%s” alterou as configurações da empresa %s", g.usuario["usuario"], g.empresa_id)
            flash("Configurações salvas.", "ok")
            return redirect(url_for("empresa.configuracoes"))

    empresa = planos.empresa(conexao, g.empresa_id)
    return render_template(
        "empresa.html",
        empresa=empresa,
        UFS=UFS,
        documento=documento_formatado(empresa["documento"]),
        uso=empresas.uso(g.empresa_id),
        smtp_configurado=bool(current_app.config["SMTP_HOST"]),
        canais=alertas.canais_da_empresa(empresa, current_app.config),
        plano=planos.consultas(conexao).plano(empresa["plano_id"]),
        tem_faturas=planos.consultas(conexao).tem_faturas(g.empresa_id),
    )


@bp.route("/empresa/exportar")
@login_obrigatorio("admin")
def exportar():
    """Baixa um .zip com todos os dados da empresa e os arquivos de mídia."""
    leitura = planos.consultas()
    empresa_id = g.empresa_id
    dados = {
        "exportado_em": agenda.para_texto_utc(agenda.agora_utc()) + " UTC",
        **leitura.dados_para_exportar(empresa_id),
    }

    exibicoes = io.StringIO()
    escritor = csv.writer(exibicoes, delimiter=";")
    escritor.writerow(["exibido_em_utc", "tela_id", "propaganda_id", "propaganda", "duracao_segundos"])
    escritor.writerows(leitura.exibicoes_para_exportar(empresa_id))

    arquivo = tempfile.TemporaryFile()  # apagado sozinho quando o download termina
    with zipfile.ZipFile(arquivo, "w", zipfile.ZIP_DEFLATED) as pacote:
        pacote.writestr("dados.json", json.dumps(dados, ensure_ascii=False, indent=2))
        pacote.writestr("exibicoes.csv", "﻿" + exibicoes.getvalue())
        for propaganda in dados["propagandas"]:
            caminho = os.path.join(current_app.config["PASTA_MIDIA"], propaganda["arquivo"])
            if os.path.exists(caminho):
                pacote.write(caminho, "midia/" + propaganda["arquivo"], compress_type=zipfile.ZIP_STORED)
    arquivo.seek(0)
    log.info("“%s” exportou os dados da empresa %s", g.usuario["usuario"], empresa_id)
    return send_file(
        arquivo,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"dados-empresa-{empresa_id}-{agenda.agora_local():%Y%m%d}.zip",
    )
