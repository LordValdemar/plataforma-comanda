"""Configurações da empresa, uso do plano e exportação dos dados (portabilidade, LGPD)."""

import csv
import io
import json
import logging
import os
import tempfile
import zipfile

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, send_file, url_for

from . import agenda, alertas, db, planos
from .auth import login_obrigatorio

bp = Blueprint("empresa", __name__)
log = logging.getLogger("propagandas.empresa")


@bp.route("/empresa", methods=["GET", "POST"])
@login_obrigatorio("admin")
def configuracoes():
    conexao = db.obter()
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()[:100]
        emails = [e.strip() for e in request.form.get("alerta_emails", "").split(",") if e.strip()]
        webhook = request.form.get("alerta_webhook", "").strip()[:500]
        if not nome:
            flash("Informe o nome da empresa.", "erro")
        elif any("@" not in e or " " in e for e in emails):
            flash("Confira os e-mails de alerta (separe por vírgula).", "erro")
        elif webhook and (erro_webhook := _erro_webhook(webhook)):
            flash(f"Webhook recusado: {erro_webhook}.", "erro")
        else:
            with conexao:
                conexao.execute(
                    "UPDATE empresas SET nome = ?, alerta_emails = ?, alerta_webhook = ? WHERE id = ?",
                    (nome, ", ".join(emails), webhook, g.empresa_id),
                )
            log.info("“%s” alterou as configurações da empresa %s", g.usuario["usuario"], g.empresa_id)
            flash("Configurações salvas.", "ok")
            return redirect(url_for("empresa.configuracoes"))

    empresa = planos.empresa(conexao, g.empresa_id)
    return render_template(
        "empresa.html",
        empresa=empresa,
        uso=planos.uso(conexao, g.empresa_id),
        smtp_configurado=bool(current_app.config["SMTP_HOST"]),
        canais=alertas.canais_da_empresa(empresa, current_app.config),
        plano=conexao.execute("SELECT * FROM planos WHERE id = ?", (empresa["plano_id"],)).fetchone(),
        tem_faturas=conexao.execute("SELECT 1 FROM faturas WHERE empresa_id = ?", (g.empresa_id,)).fetchone() is not None,
    )


def _erro_webhook(url):
    try:
        alertas.validar_url_webhook(url)
    except alertas.EnderecoBloqueado as erro:
        return str(erro)
    return None


def _linhas(conexao, sql, *parametros):
    return [dict(linha) for linha in conexao.execute(sql, parametros)]


@bp.route("/empresa/exportar")
@login_obrigatorio("admin")
def exportar():
    """Baixa um .zip com todos os dados da empresa e os arquivos de mídia."""
    conexao = db.obter()
    empresa_id = g.empresa_id
    dados = {
        "exportado_em": agenda.para_texto_utc(agenda.agora_utc()) + " UTC",
        "empresa": _linhas(conexao, "SELECT id, nome, criado_em, alerta_emails, alerta_webhook FROM empresas WHERE id = ?",
                           empresa_id)[0],
        # Sem senhas nem segredos de 2FA.
        "usuarios": _linhas(conexao, "SELECT usuario, papel, criado_em, totp_segredo IS NOT NULL AS dois_fatores "
                                     "FROM usuarios WHERE empresa_id = ?", empresa_id),
        "grupos": _linhas(conexao, "SELECT id, nome FROM grupos WHERE empresa_id = ?", empresa_id),
        "telas": _linhas(conexao, "SELECT id, nome, grupo_id, letreiro, ultimo_contato, criado_em FROM telas "
                                  "WHERE empresa_id = ?", empresa_id),
        "propagandas": _linhas(conexao, "SELECT id, nome, arquivo, tipo, tamanho, duracao, ativo, inicio, fim, dias_semana, "
                                        "hora_inicio, hora_fim, para_todas, posicao, criado_em FROM propagandas "
                                        "WHERE empresa_id = ? ORDER BY posicao", empresa_id),
        "destinos": _linhas(conexao, "SELECT d.* FROM propaganda_destinos d JOIN propagandas p ON p.id = d.propaganda_id "
                                     "WHERE p.empresa_id = ?", empresa_id),
        "configuracoes": _linhas(conexao, "SELECT chave, valor FROM configuracoes WHERE empresa_id = ?", empresa_id),
        "ponto": _linhas(conexao, "SELECT usuario_nome, entrada, saida, motivo_saida FROM ponto_registros "
                                  "WHERE empresa_id = ? ORDER BY entrada", empresa_id),
    }

    exibicoes = io.StringIO()
    escritor = csv.writer(exibicoes, delimiter=";")
    escritor.writerow(["exibido_em_utc", "tela_id", "propaganda_id", "propaganda", "duracao_segundos"])
    for linha in conexao.execute(
        "SELECT exibido_em, tela_id, propaganda_id, propaganda_nome, duracao FROM exibicoes "
        "WHERE empresa_id = ? ORDER BY exibido_em",
        (empresa_id,),
    ):
        escritor.writerow(list(linha))

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
