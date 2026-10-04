"""Cadastro da empresa: CPF/CNPJ, contato, endereço e logo (usados no cupom da Comanda e na cobrança)."""

import base64
import re

from . import cobranca

UFS = ("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO").split()
LOGO_MAX_BYTES = 300 * 1024  # o logo vai junto em cada cupom: pequeno imprime melhor e mais rápido
TIPOS_DE_LOGO = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}
# Campos de texto (coluna em empresas, tamanho máximo).
CAMPOS = (("razao_social", 120), ("email", 120), ("telefone", 20), ("cep", 9), ("logradouro", 120), ("numero", 20),
          ("complemento", 60), ("bairro", 60), ("cidade", 60), ("uf", 2))


class CadastroInvalido(ValueError):
    pass


def ler_formulario(form):
    """Campos do formulário, conferidos. Devolve {coluna: valor} (só os que vieram) ou levanta CadastroInvalido."""
    dados = {}
    for campo, tamanho in CAMPOS:
        if campo in form:
            dados[campo] = form.get(campo, "").strip()[:tamanho]
    if "documento" in form:
        documento = cobranca.so_numeros(form.get("documento"))
        if documento and not cobranca.documento_valido(documento):
            raise CadastroInvalido("Confira o CPF ou CNPJ: os dígitos não conferem.")
        dados["documento"] = documento
    if dados.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", dados["email"]):
        raise CadastroInvalido("Confira o e-mail da empresa.")
    if dados.get("telefone"):
        numeros = cobranca.so_numeros(dados["telefone"])
        if len(numeros) not in (10, 11):
            raise CadastroInvalido("Informe o telefone com DDD, ex.: (99) 98436-9495.")
        dados["telefone"] = f"({numeros[:2]}) {numeros[2:-4]}-{numeros[-4:]}"
    if dados.get("cep"):
        numeros = cobranca.so_numeros(dados["cep"])
        if len(numeros) != 8:
            raise CadastroInvalido("O CEP tem 8 números.")
        dados["cep"] = f"{numeros[:5]}-{numeros[5:]}"
    if "uf" in dados:
        dados["uf"] = dados["uf"].upper()
        if dados["uf"] and dados["uf"] not in UFS:
            raise CadastroInvalido("Escolha o estado (UF) da lista.")
    return dados


def ler_logo(arquivo):
    """PNG ou JPG pequeno → data URI. Levanta CadastroInvalido se não servir."""
    conteudo = arquivo.read(LOGO_MAX_BYTES + 1)
    if len(conteudo) > LOGO_MAX_BYTES:
        raise CadastroInvalido("O logo pode ter até 300 KB. Diminua a imagem e envie de novo.")
    tipo = next((t for inicio, t in TIPOS_DE_LOGO.items() if conteudo.startswith(inicio)), None)
    if tipo is None:
        raise CadastroInvalido("O logo precisa ser uma imagem PNG ou JPG.")
    return f"data:{tipo};base64,{base64.b64encode(conteudo).decode()}"


def documento_formatado(documento):
    d = cobranca.so_numeros(documento)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return documento or ""


def endereco_completo(empresa):
    """'Travessa Manoel de Oliveira Gomes, 03 - Centro - Dom Pedro/MA'."""
    rua = ", ".join(p for p in (empresa["logradouro"], empresa["numero"]) if p)
    if rua and empresa["complemento"]:
        rua += f" ({empresa['complemento']})"
    cidade = "/".join(p for p in (empresa["cidade"], empresa["uf"]) if p)
    return " - ".join(p for p in (rua, empresa["bairro"], cidade) if p)
