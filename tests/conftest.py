import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from propagandas import auth, create_app  # noqa: E402

# Cabeçalhos mínimos de arquivos reais, para passar na validação de conteúdo.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 60


@pytest.fixture(autouse=True)
def limpar_bloqueios():
    from propagandas import ponto

    auth._tentativas.clear()
    ponto._codigos_errados.clear()
    yield
    auth._tentativas.clear()
    ponto._codigos_errados.clear()


@pytest.fixture
def app(tmp_path):
    app = create_app({"PASTA_DADOS": str(tmp_path / "dados"), "TESTING": True, "SECRET_KEY": "teste"})
    yield app


@pytest.fixture
def cliente(app):
    return app.test_client()


def csrf(cliente, pagina="/login"):
    html = cliente.get(pagina).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def postar(cliente, url, dados=None, pagina="/", **kwargs):
    dados = dict(dados or {})
    dados["csrf_token"] = csrf(cliente, pagina)
    return cliente.post(url, data=dados, **kwargs)


def configurar_admin(cliente, usuario="admin", senha="senha-forte-123"):
    return postar(
        cliente, "/configurar",
        {"usuario": usuario, "senha": senha, "confirmacao": senha},
        pagina="/configurar",
    )


def enviar(cliente, nome, conteudo, duracao=7, destino="todas", **destinos):
    """Envia uma propaganda. Os testes escolhem "Todas as telas" (o padrão do sistema é nenhuma)."""
    return postar(
        cliente, "/enviar",
        {"duracao": str(duracao), "arquivos": (io.BytesIO(conteudo), nome), "destino": destino, **destinos},
        content_type="multipart/form-data",
    )


@pytest.fixture
def logado(cliente):
    configurar_admin(cliente)
    return cliente


def conectar_tv(admin, tela_id, tv=None):
    """Conecta um aparelho à tela, como na loja: a TV abre /tela, o celular de quem administra
    lê o código e escolhe a tela. O crachá vai para `tv` (por padrão, o próprio cliente do admin),
    que assim pode fazer o papel da TV de várias telas nos testes."""
    tv = tv or admin
    aparelho = admin.application.test_client()  # aparelho novo: /tela mostra o QR code
    codigo = re.search(r'<p class="relogio">([A-Z0-9]{6})</p>', aparelho.get("/tela").get_data(as_text=True)).group(1)
    postar(admin, f"/tela/parear/{codigo}", {"tela_id": str(tela_id)}, pagina=f"/tela/parear/{codigo}")
    assert aparelho.get("/api/tela/conexao").get_json()["pronto"]
    nome = f"tela_aparelho_{tela_id}"
    tv.set_cookie(nome, aparelho.get_cookie(nome).value)
    return tv
