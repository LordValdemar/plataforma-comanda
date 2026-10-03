"""Páginas públicas de Política de Privacidade e Termos de Uso (modelos para revisão jurídica)."""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, render_template, url_for

bp = Blueprint("legal", __name__)


@bp.route("/privacidade")
def privacidade():
    return render_template("privacidade.html")


@bp.route("/termos")
def termos():
    return render_template("termos.html")


@bp.route("/.well-known/security.txt")
def security_txt():
    """Contato para quem encontrar uma falha de segurança (RFC 9116).

    A validade é calculada na hora (sempre 6 meses à frente), então o arquivo nunca vence.
    """
    contato = current_app.config.get("CONTATO_PLATAFORMA", "").strip()
    if "@" not in contato:
        abort(404)
    validade = (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%dT00:00:00Z")
    texto = (
        f"Contact: mailto:{contato}\n"
        f"Expires: {validade}\n"
        "Preferred-Languages: pt, en\n"
        f"Canonical: {url_for('legal.security_txt', _external=True)}\n"
        f"Policy: {url_for('legal.privacidade', _external=True)}\n"
    )
    return texto, 200, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "max-age=86400"}
