"""Contas de uma loja só (Comanda local), sem banco: senha mínima, desativar, último administrador e 2FA."""

from dataclasses import replace

import pytest

from src.domain import totp
from src.domain.contas import CodigoIncorreto, ContaLocal, ErroUsuario, PapelInvalido, ServicoDeContasLocais
from src.domain.contas.locais import BloqueadoAqui, Desativado
from src.domain.erros import NaoEncontrado
from src.domain.tentativas import LimiteDeTentativas

PAPEIS = {"admin": "Administrador", "caixa": "Caixa", "garcom": "Garçom", "cozinha": "Cozinha"}
AGORA = 1_800_000_000.0


class SenhasFalsas:
    def gerar(self, senha):
        return "hash:" + senha

    def confere(self, senha_hash, senha):
        return senha_hash == "hash:" + senha


class Repo:
    def __init__(self):
        self.contas = {}

    def conta(self, usuario_id):
        return self.contas.get(usuario_id)

    def conta_com_nome(self, nome):
        return next((c for c in self.contas.values() if c.usuario.lower() == nome.lower()), None)

    def existe_alguem(self):
        return bool(self.contas)

    def nome_em_uso(self, nome):
        return self.conta_com_nome(nome) is not None

    def admins_ativos(self):
        return sum(1 for c in self.contas.values() if c.papel == "admin" and c.ativo)

    def inserir(self, nome, senha_hash, papel, token):
        novo = len(self.contas) + 1
        self.contas[novo] = ContaLocal(novo, nome, papel, senha_hash, token, True, False, None, 0)
        return novo

    def _mudar(self, usuario_id, **campos):
        self.contas[usuario_id] = replace(self.contas[usuario_id], **campos)

    def gravar_senha(self, usuario_id, senha_hash, token):
        self._mudar(usuario_id, senha_hash=senha_hash, token_sessao=token)

    def ativar_totp(self, usuario_id, segredo, contador):
        self._mudar(usuario_id, totp_segredo=segredo, totp_ultimo=contador)

    def desativar_totp(self, usuario_id, token):
        self._mudar(usuario_id, totp_segredo=None, totp_ultimo=0, token_sessao=token)

    def gravar_totp_ultimo(self, usuario_id, contador):
        self._mudar(usuario_id, totp_ultimo=contador)

    def mudar_papel(self, usuario_id, papel):
        self._mudar(usuario_id, papel=papel, fecha_conta=self.contas[usuario_id].fecha_conta and papel == "garcom")

    def definir_fecha_conta(self, usuario_id, pode):
        self._mudar(usuario_id, fecha_conta=pode)

    def definir_ativo(self, usuario_id, ativo, token):
        self._mudar(usuario_id, ativo=ativo, token_sessao=token)


@pytest.fixture
def contas():
    repo = Repo()
    return ServicoDeContasLocais(repo, SenhasFalsas(), PAPEIS, LimiteDeTentativas(3, 60), relogio=lambda: AGORA), repo


def test_criar_e_senha(contas):
    servico, repo = contas
    admin = servico.criar("dono", "123456", "admin")
    for argumentos, mensagem in [(("", "123456"), "nome de usuário"), (("x" * 41, "123456"), "até 40"),
                                 (("ana", "12345"), "pelo menos 6"), (("ana", "123456", "editor"), "Papel"),
                                 (("DONO", "123456"), "já existe")]:
        with pytest.raises(ErroUsuario, match=mensagem):
            servico.criar(*argumentos)
    with pytest.raises(PapelInvalido):
        servico.criar("ana", "123456", "editor")
    token = repo.contas[admin].token_sessao
    with pytest.raises(ErroUsuario, match="senha atual"):
        servico.mudar_minha_senha(repo.contas[admin], "errada", "nova-senha", "nova-senha")
    with pytest.raises(ErroUsuario, match="não conferem"):
        servico.mudar_minha_senha(repo.contas[admin], "123456", "nova-senha", "outra")
    servico.mudar_minha_senha(repo.contas[admin], "123456", "nova-senha", "nova-senha")
    assert repo.contas[admin].senha_hash == "hash:nova-senha" and repo.contas[admin].token_sessao != token
    with pytest.raises(NaoEncontrado):
        servico.conta(99)


def test_login_desativado_e_bloqueio(contas):
    servico, _repo = contas
    ana = servico.criar("ana", "123456")
    assert servico.entrar(" Ana ", "123456", "ip").id == ana
    servico.alternar_ativo(ana)
    with pytest.raises(Desativado):
        servico.entrar("ana", "123456", "ip")
    for _ in range(3):
        with pytest.raises(ErroUsuario, match="incorretos"):
            servico.entrar("ana", "errada", "ip")
    with pytest.raises(BloqueadoAqui, match="Espere 15 minutos"):
        servico.entrar("ana", "123456", "ip")


def test_ultimo_administrador_e_fecha_contas(contas):
    servico, repo = contas
    dono, ze = servico.criar("dono", "123456", "admin"), servico.criar("ze", "123456", "garcom")
    for acao in (lambda: servico.mudar_papel(dono, "caixa"), lambda: servico.alternar_ativo(dono)):
        with pytest.raises(ErroUsuario, match="pelo menos um administrador"):
            acao()
    assert servico.alternar_fecha_conta(ze)[1] and repo.contas[ze].fecha_conta
    servico.mudar_papel(ze, "caixa")
    assert not repo.contas[ze].fecha_conta                       # a permissão não acompanha a pessoa
    with pytest.raises(ErroUsuario, match="Só garçons"):
        servico.alternar_fecha_conta(ze)
    servico.mudar_papel(ze, "admin")
    servico.alternar_ativo(dono)                                  # agora há outro administrador
    assert not repo.contas[dono].ativo


def test_dois_fatores(contas):
    servico, repo = contas
    ana = servico.criar("ana", "123456")
    segredo = totp.novo_segredo()
    with pytest.raises(CodigoIncorreto):
        servico.ativar_2fa(repo.contas[ana], segredo, "000000")
    servico.ativar_2fa(repo.contas[ana], segredo, totp.codigo_atual(segredo, AGORA))
    conta = servico.entrar("ana", "123456", "ip")
    assert conta.tem_2fa and servico.pode_pedir_codigo(ana) == repo.contas[ana]
    codigo = totp.codigo_atual(segredo, AGORA + 30)
    with pytest.raises(CodigoIncorreto):
        servico.confirmar_codigo(repo.contas[ana], "123", "ip")
    servico.confirmar_codigo(repo.contas[ana], codigo, "ip2")
    with pytest.raises(CodigoIncorreto):                          # o mesmo código não vale duas vezes
        servico.confirmar_codigo(repo.contas[ana], codigo, "ip2")
    token = repo.contas[ana].token_sessao
    with pytest.raises(ErroUsuario, match="Senha incorreta"):
        servico.desativar_2fa_propria(repo.contas[ana], "errada", codigo)
    with pytest.raises(CodigoIncorreto):                          # anterior ao último usado também não
        servico.desativar_2fa_propria(repo.contas[ana], "123456", totp.codigo_atual(segredo, AGORA - 30))
    depois = ServicoDeContasLocais(repo, SenhasFalsas(), PAPEIS, relogio=lambda: AGORA + 60)
    depois.desativar_2fa_propria(repo.contas[ana], "123456", totp.codigo_atual(segredo, AGORA + 60))
    assert not repo.contas[ana].tem_2fa and repo.contas[ana].token_sessao != token
    assert servico.pode_pedir_codigo(ana) is None


def test_linha_de_comando(contas):
    servico, repo = contas
    ana = servico.criar("ana", "123456", "admin")
    servico.criar("dono", "123456", "admin")
    servico.alternar_ativo(ana)
    assert servico.pelo_nome(" ANA ").id == ana
    with pytest.raises(NaoEncontrado, match="“zé” não encontrado"):
        servico.pelo_nome("zé")
    servico.recuperar_acesso(ana, "senha-nova")
    assert repo.contas[ana].ativo and servico.entrar("ana", "senha-nova", "ip").id == ana
