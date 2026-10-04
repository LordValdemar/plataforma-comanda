"""Regras das empresas sem banco: módulos, cadastro, limites, configurações e abertura de loja."""

import pytest

from src.domain.empresas import (
    MB,
    CadastroInvalido,
    CodigoEmUso,
    Configuracoes,
    Limites,
    NovaLoja,
    ServicoDeEmpresas,
    Uso,
    codigo_do_nome,
    codigo_valido,
    documento_formatado,
    endereco_completo,
    ler_dados,
    ler_emails,
    ler_logo,
    modulos,
)
from src.domain.erros import NaoEncontrado

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 10


class EmpresasFalsas:
    def __init__(self):
        self.empresas = {1: {"nome": "Plataforma", "slug": "plataforma-x", "liberados": "", "plano": None},
                         2: {"nome": "Lanchonete", "slug": "lanchonete", "liberados": "comanda", "plano": "painel"}}
        self.limite = Limites(2, 1)
        self.gasto = Uso(1, MB // 2)
        self.salvo = None

    def existe(self, empresa_id):
        return empresa_id in self.empresas

    def modulos(self, empresa_id):
        e = self.empresas.get(empresa_id)
        return None if e is None else (e["liberados"], e["plano"])

    def limites(self, empresa_id):
        return self.limite

    def uso(self, empresa_id):
        return self.gasto

    def codigo_em_uso(self, codigo, exceto_id=None):
        return any(e["slug"] == codigo and i != exceto_id for i, e in self.empresas.items())

    def salvar_configuracoes(self, empresa_id, nome, codigo, emails, webhook, cadastro):
        self.salvo = (empresa_id, nome, codigo, emails, webhook, dict(cadastro))

    def remover_logo(self, empresa_id):
        pass

    def criar(self, nome, codigo, email_cobranca):
        novo = max(self.empresas) + 1
        self.empresas[novo] = {"nome": nome, "slug": codigo, "liberados": "", "plano": None}
        return novo

    def apagar(self, empresa_id):
        del self.empresas[empresa_id]


@pytest.fixture
def repo():
    return EmpresasFalsas()


def servico(repo, webhook=None):
    return ServicoDeEmpresas(repo, 1, conferir_webhook=lambda url: webhook)


def test_modulos():
    assert modulos.ler(" comanda, xyz,painel ") == {"painel", "comanda"}
    assert modulos.juntar({"comanda", "painel"}) == "painel,comanda"
    assert modulos.em_ordem("comanda,painel") == ["painel", "comanda"]
    assert modulos.da_empresa(True, "", None) == {"painel", "comanda"}


def test_modulos_da_empresa(repo):
    empresas = servico(repo)
    assert empresas.modulos(1) == {"painel", "comanda"}            # a principal usa todos
    assert empresas.modulos(2) == {"painel", "comanda"}            # do plano + liberado à mão
    assert empresas.modulos(99) == set()
    with pytest.raises(NaoEncontrado):
        empresas.conferir_existe(99)


def test_limites(repo):
    empresas = servico(repo)
    assert empresas.cabe_mais_uma_tela(2)
    repo.gasto = Uso(2, 0)
    assert not empresas.cabe_mais_uma_tela(2)
    assert empresas.cabe_no_armazenamento(2, MB) and not empresas.cabe_no_armazenamento(2, MB + 1)
    repo.limite = Limites(None, None)
    assert empresas.cabe_mais_uma_tela(2) and empresas.cabe_no_armazenamento(2, 10**12)
    assert Uso(0, MB * 3).mb == 3


def test_cadastro_conferido():
    dados = ler_dados({"telefone": "99984369495", "cep": "65765000", "uf": "ma", "documento": "36.740.823/0001-09",
                       "email": "sac@loja.com.br", "razao_social": " Loja " + "x" * 200})
    assert (dados["telefone"], dados["cep"], dados["uf"], dados["documento"]) == ("(99) 98436-9495", "65765-000", "MA",
                                                                                  "36740823000109")
    assert len(dados["razao_social"]) == 120 and "bairro" not in dados        # só os campos que vieram
    for campos, mensagem in [({"documento": "111.111.111-11"}, "CPF ou CNPJ"), ({"email": "sem-arroba"}, "e-mail"),
                             ({"telefone": "1234"}, "DDD"), ({"cep": "123"}, "CEP"), ({"uf": "XX"}, "UF")]:
        with pytest.raises(CadastroInvalido, match=mensagem):
            ler_dados(campos)
    assert documento_formatado("36740823000109") == "36.740.823/0001-09"
    assert documento_formatado("52998224725") == "529.982.247-25" and documento_formatado(None) == ""
    assert endereco_completo({"logradouro": "Rua A", "numero": "03", "complemento": "fundos", "bairro": "Centro",
                              "cidade": "Dom Pedro", "uf": "MA"}) == "Rua A, 03 (fundos) - Centro - Dom Pedro/MA"


def test_logo_e_emails():
    assert ler_logo(PNG).startswith("data:image/png;base64,")
    with pytest.raises(CadastroInvalido, match="PNG ou JPG"):
        ler_logo(b"GIF89a")
    with pytest.raises(CadastroInvalido, match="300 KB"):
        ler_logo(PNG + b"x" * 300 * 1024)
    assert ler_emails(" a@b.com, c@d.com ,") == ["a@b.com", "c@d.com"]
    with pytest.raises(CadastroInvalido):
        ler_emails("a@b.com, sem arroba")


def test_codigo_da_loja(repo):
    assert codigo_do_nome("Padeiro & Lanches São João!") == "padeiro-lanches-sao-joao" and codigo_do_nome("!!!") == "loja"
    assert codigo_valido("padeiro-lanches") and not codigo_valido("-x") and not codigo_valido("admin")
    repo.empresas[3] = {"nome": "", "slug": "lanchonete-2", "liberados": "", "plano": None}
    assert servico(repo).codigo_livre("Lanchonete") == "lanchonete-3"
    assert servico(repo).codigo_livre("Lanchonete", 2) == "lanchonete"   # o próprio código não conta


def test_salvar_configuracoes(repo):
    empresas = servico(repo)
    empresas.salvar_configuracoes(2, Configuracoes(" Lanchonete do Zé ", "Lanchonete-Ze", "a@b.com, c@d.com",
                                                   " https://exemplo.com/x ", {"uf": "ma"}, PNG))
    assert repo.salvo[:5] == (2, "Lanchonete do Zé", "lanchonete-ze", "a@b.com, c@d.com", "https://exemplo.com/x")
    assert repo.salvo[5]["uf"] == "MA" and repo.salvo[5]["logo"].startswith("data:image/png")
    for config, mensagem in [(Configuracoes(" ", "lanchonete"), "nome"),
                             (Configuracoes("Z", "lanchonete", cadastro={"cep": "1"}), "CEP"),
                             (Configuracoes("Z", "AD"), "letras minúsculas"),
                             (Configuracoes("Z", "plataforma-x"), "já é de outra loja"),
                             (Configuracoes("Z", "lanchonete", "x"), "e-mails de alerta")]:
        with pytest.raises(CadastroInvalido, match=mensagem):
            empresas.salvar_configuracoes(2, config)
    with pytest.raises(CadastroInvalido, match="Webhook recusado: endereço interno"):
        servico(repo, webhook="endereço interno").salvar_configuracoes(2, Configuracoes("Z", "lanchonete", alerta_webhook="http://10.0.0.1"))


def test_abrir_loja(repo):
    empresas = servico(repo)
    loja = NovaLoja("Padaria Pão Quente", "", "dono@padaria.com", True, True)
    empresa_id, admin = empresas.abrir_loja(loja, lambda empresa_id: f"admin de {empresa_id}")
    assert (admin, repo.empresas[empresa_id]["slug"]) == (f"admin de {empresa_id}", "padaria-pao-quente")
    with pytest.raises(CodigoEmUso):
        empresas.abrir_loja(NovaLoja("Outra", "lanchonete", "a@b.com", True, True), lambda _id: None)

    def recusa(_empresa_id):
        raise ValueError("nome de usuário já existe")
    antes = set(repo.empresas)
    with pytest.raises(ValueError):
        empresas.abrir_loja(NovaLoja("Mais uma", "", "a@b.com", True, True), recusa)
    assert set(repo.empresas) == antes                                    # a empresa foi desfeita
    for loja, mensagem in [(NovaLoja(" ", "", "a@b.com", True, True), "nome da loja"),
                           (NovaLoja("X", "", "sem-arroba", True, True), "e-mail"),
                           (NovaLoja("X", "", "a@b.com", False, True), "senhas"),
                           (NovaLoja("X", "", "a@b.com", True, False), "Termos")]:
        with pytest.raises(CadastroInvalido, match=mensagem):
            empresas.abrir_loja(loja, lambda _id: None)
