"""Regras das contas sem banco: cadastro, login (senha, loja, bloqueio, empresa suspensa), 2FA e equipe."""

from dataclasses import replace

import pytest

from src.domain import totp
from src.domain.contas import (
    Bloqueado,
    CodigoIncorreto,
    Conta,
    CredenciaisInvalidas,
    EmpresaSuspensa,
    ErroUsuario,
    LojaAmbigua,
    PapelInvalido,
    ServicoDeContas,
    acesso,
)
from src.domain.erros import NaoEncontrado, SemPermissao
from src.domain.tentativas import LimiteDeTentativas

AGORA = 1_900_000_000.0


class SenhasFalsas:
    def gerar(self, senha):
        return "hash:" + senha

    def confere(self, senha_hash, senha):
        return senha_hash == "hash:" + senha


class Repo:
    def __init__(self):
        self.contas: dict[int, Conta] = {}
        self.lojas = {1: ("principal", True, None), 2: ("lanchonete", True, None), 3: ("padaria", True, None)}

    def _com_loja(self, conta):
        _, ativa, motivo = self.lojas[conta.empresa_id]
        return replace(conta, empresa_ativa=ativa, motivo_suspensao=motivo)

    def _muda(self, usuario_id, **campos):
        self.contas[usuario_id] = replace(self.contas[usuario_id], **campos)

    def conta(self, usuario_id):
        conta = self.contas.get(usuario_id)
        return None if conta is None else self._com_loja(conta)

    def contas_com_nome(self, nome, loja):
        return [self._com_loja(c) for c in self.contas.values()
                if c.usuario == nome and (loja is None or self.lojas[c.empresa_id][0] == loja)]

    def existe_alguem(self):
        return bool(self.contas)

    def empresa_existe(self, empresa_id):
        return empresa_id in self.lojas

    def nome_em_uso(self, empresa_id, nome):
        return any(c.empresa_id == empresa_id and c.usuario == nome for c in self.contas.values())

    def inserir(self, empresa_id, nome, senha_hash, papel, plataforma, token):
        novo = max(self.contas, default=0) + 1
        self.contas[novo] = Conta(novo, empresa_id, nome, papel, senha_hash, token, plataforma=plataforma)
        return novo

    def gravar_senha(self, usuario_id, senha_hash, token):
        self._muda(usuario_id, senha_hash=senha_hash, token_sessao=token)

    def gravar_totp_pendente(self, usuario_id, segredo):
        self._muda(usuario_id, totp_pendente=segredo)

    def ativar_totp(self, usuario_id, segredo, contador, token):
        self._muda(usuario_id, totp_segredo=segredo, totp_pendente=None, totp_ultimo=contador, token_sessao=token)

    def desativar_totp(self, usuario_id, token):
        self._muda(usuario_id, totp_segredo=None, totp_ultimo=0, token_sessao=token)

    def gravar_totp_ultimo(self, usuario_id, contador):
        self._muda(usuario_id, totp_ultimo=contador)

    def mudar_papel(self, usuario_id, papel):
        conta = self.contas[usuario_id]
        self._muda(usuario_id, papel=papel, fecha_conta=conta.fecha_conta and papel == "garcom")

    def definir_fecha_conta(self, usuario_id, pode):
        self._muda(usuario_id, fecha_conta=pode)

    def excluir(self, usuario_id):
        del self.contas[usuario_id]


def montar():
    repo, relogio = Repo(), [AGORA]
    tentativas = LimiteDeTentativas(5, 15 * 60, relogio=lambda: relogio[0])
    servico = ServicoDeContas(repo, SenhasFalsas(), tentativas, relogio=lambda: relogio[0])
    return servico, repo, relogio


def test_acesso_conforme_a_empresa():
    assert acesso(True, None, False) == "liberado"
    assert acesso(False, "inadimplencia", False) == "so_pagamento"
    assert acesso(False, "manual", False) == "suspenso"
    assert acesso(False, "manual", True) == "liberado"          # quem opera a plataforma sempre entra


def test_cadastro_confere_nome_senha_papel_e_loja():
    servico, repo, _ = montar()
    joao = servico.criar(2, "  joao ", "senha-forte", "garcom")
    assert repo.contas[joao].usuario == "joao" and repo.contas[joao].senha_hash == "hash:senha-forte"
    assert servico.criar(3, "joao", "outra-senha", "caixa")   # o mesmo nome em outra loja pode
    for argumentos, mensagem in [
        ((2, "joao", "senha-forte", "garcom"), "já existe nesta loja"),
        ((2, "", "senha-forte", "garcom"), "nome de usuário"),
        ((2, "x" * 51, "senha-forte", "garcom"), "nome de usuário"),
        ((2, "maria", "curta", "garcom"), "pelo menos 8"),
        ((2, "maria", "senha-forte", "dono"), "Papel inválido"),
        ((9, "maria", "senha-forte", "garcom"), "Empresa não encontrada"),
    ]:
        with pytest.raises(ErroUsuario, match=mensagem):
            servico.criar(*argumentos)


def test_login_escolhe_a_loja_pela_senha_e_pede_o_codigo_quando_empata():
    servico, repo, _ = montar()
    servico.criar(2, "joao", "senha-da-lanchonete", "garcom")
    servico.criar(3, "joao", "senha-da-padaria", "caixa")
    assert servico.entrar("joao", "senha-da-padaria", None, "1.1.1.1").empresa_id == 3
    servico.criar(1, "joao", "senha-da-padaria", "editor")
    with pytest.raises(LojaAmbigua):
        servico.entrar("joao", "senha-da-padaria", None, "1.1.1.1")
    assert servico.entrar("joao", "senha-da-padaria", "padaria", "1.1.1.1").empresa_id == 3
    with pytest.raises(CredenciaisInvalidas):
        servico.entrar("joao", "senha-da-padaria", "lanchonete", "1.1.1.1")   # senha certa, loja errada


def test_cinco_erros_bloqueiam_o_ip_por_15_minutos():
    servico, _, relogio = montar()
    servico.criar(2, "ana", "senha-da-ana", "caixa")
    for _ in range(5):
        with pytest.raises(CredenciaisInvalidas):
            servico.entrar("ana", "errada", None, "6.6.6.6")
    with pytest.raises(Bloqueado):
        servico.entrar("ana", "senha-da-ana", None, "6.6.6.6")   # nem a senha certa entra
    assert servico.entrar("ana", "senha-da-ana", None, "7.7.7.7").usuario == "ana"   # outro IP
    relogio[0] += 15 * 60
    assert servico.entrar("ana", "senha-da-ana", None, "6.6.6.6").usuario == "ana"


def test_acerto_zera_os_erros():
    servico, _, _ = montar()
    servico.criar(2, "ana", "senha-da-ana", "caixa")
    for _ in range(4):
        with pytest.raises(CredenciaisInvalidas):
            servico.entrar("ana", "errada", None, "6.6.6.6")
    servico.entrar("ana", "senha-da-ana", None, "6.6.6.6")
    for _ in range(4):
        with pytest.raises(CredenciaisInvalidas):
            servico.entrar("ana", "errada", None, "6.6.6.6")
    servico.entrar("ana", "senha-da-ana", None, "6.6.6.6")   # ainda não bloqueado


def test_empresa_suspensa():
    servico, repo, _ = montar()
    servico.criar(2, "ana", "senha-da-ana", "caixa")
    repo.lojas[2] = ("lanchonete", False, "manual")
    with pytest.raises(EmpresaSuspensa):
        servico.entrar("ana", "senha-da-ana", None, "1.1.1.1")
    repo.lojas[2] = ("lanchonete", False, "inadimplencia")
    assert servico.entrar("ana", "senha-da-ana", None, "1.1.1.1").acesso == "so_pagamento"   # entra para pagar


def test_2fa_ativar_entrar_sem_reusar_e_desativar():
    servico, repo, relogio = montar()
    ana = repo.conta(servico.criar(2, "ana", "senha-da-ana", "admin"))
    segredo = servico.segredo_para_ativar(ana)
    assert servico.segredo_para_ativar(repo.conta(ana.id)) == segredo        # o mesmo até confirmar
    with pytest.raises(CodigoIncorreto):
        servico.ativar_2fa(repo.conta(ana.id), "000000")
    token_antes = repo.contas[ana.id].token_sessao
    servico.ativar_2fa(repo.conta(ana.id), totp.codigo_atual(segredo, relogio[0]))
    ana = repo.conta(ana.id)
    assert ana.tem_2fa and ana.totp_pendente is None and ana.token_sessao != token_antes   # outros aparelhos saem
    assert servico.entrar("ana", "senha-da-ana", None, "1.1.1.1").tem_2fa                 # falta o código
    with pytest.raises(CodigoIncorreto, match="já usado"):
        servico.confirmar_codigo(ana.id, totp.codigo_atual(segredo, relogio[0]), "1.1.1.1")  # o da ativação
    relogio[0] += 30
    assert servico.confirmar_codigo(ana.id, totp.codigo_atual(segredo, relogio[0]), "1.1.1.1").id == ana.id
    with pytest.raises(ErroUsuario, match="Senha incorreta"):
        servico.desativar_2fa_propria(repo.conta(ana.id), "errada", totp.codigo_atual(segredo, relogio[0] + 30))
    with pytest.raises(CodigoIncorreto):
        servico.desativar_2fa_propria(repo.conta(ana.id), "senha-da-ana", "123456")
    relogio[0] += 30
    servico.desativar_2fa_propria(repo.conta(ana.id), "senha-da-ana", totp.codigo_atual(segredo, relogio[0]))
    assert not repo.conta(ana.id).tem_2fa


def test_codigos_errados_tambem_bloqueiam():
    servico, repo, relogio = montar()
    ana = repo.conta(servico.criar(2, "ana", "senha-da-ana", "admin"))
    repo._muda(ana.id, totp_segredo=totp.novo_segredo())
    for _ in range(5):
        with pytest.raises(CodigoIncorreto):
            servico.confirmar_codigo(ana.id, "000000", "6.6.6.6")
    with pytest.raises(Bloqueado):
        servico.confirmar_codigo(ana.id, totp.codigo_atual(repo.contas[ana.id].totp_segredo, relogio[0]), "6.6.6.6")
    with pytest.raises(CodigoIncorreto):
        servico.confirmar_codigo(999, "123456", "8.8.8.8")


def test_trocar_a_propria_senha():
    servico, repo, _ = montar()
    ana = repo.conta(servico.criar(2, "ana", "senha-da-ana", "admin"))
    for atual, nova, confirmacao, mensagem in (("errada", "nova-senha", "nova-senha", "atual está incorreta"),
                                               ("senha-da-ana", "nova-senha", "outra", "não conferem"),
                                               ("senha-da-ana", "curta", "curta", "pelo menos 8")):
        with pytest.raises(ErroUsuario, match=mensagem):
            servico.mudar_minha_senha(ana, atual, nova, confirmacao)
    servico.mudar_minha_senha(ana, "senha-da-ana", "nova-senha", "nova-senha")
    assert repo.contas[ana.id].senha_hash == "hash:nova-senha" and repo.contas[ana.id].token_sessao != ana.token_sessao


def test_equipe_da_loja():
    servico, repo, _ = montar()
    admin = servico.criar(2, "dono", "senha-do-dono", "admin")
    garcom = servico.criar(2, "joao", "senha-do-joao", "garcom")
    de_fora = servico.criar(3, "maria", "senha-da-maria", "caixa")
    suporte = servico.criar(2, "suporte", "senha-do-suporte", "admin", plataforma=True)
    so_comanda = {"comanda"}
    with pytest.raises(ErroUsuario, match="não assinou"):
        servico.criar_na_loja(2, so_comanda, "ed", "senha-do-ed", "editor")
    assert servico.criar_na_loja(2, so_comanda, "bia", "senha-da-bia", "caixa")
    _, novo = servico.alternar_fecha_conta(2, garcom)
    assert novo and repo.contas[garcom].fecha_conta
    alvo, mudancas = servico.editar(2, admin, garcom, "caixa", "", so_comanda)
    assert mudancas == ["agora é Caixa"] and not repo.contas[garcom].fecha_conta   # a permissão do garçom não vai junto
    assert servico.editar(2, admin, garcom, "caixa", "", so_comanda)[1] == []
    _, mudancas = servico.editar(2, admin, garcom, None, "senha-nova-1", so_comanda)
    assert mudancas == ["tem senha nova (os aparelhos dela precisam entrar de novo)"]
    with pytest.raises(PapelInvalido):
        servico.alternar_fecha_conta(2, garcom)                 # agora é caixa
    for alvo_id in (admin, suporte):
        with pytest.raises(SemPermissao):
            servico.editar(2, admin, alvo_id, "caixa", "", so_comanda)
    for operacao in (lambda: servico.editar(2, admin, de_fora, "caixa", "", so_comanda),
                     lambda: servico.excluir(2, admin, de_fora), lambda: servico.desativar_2fa_de(2, de_fora),
                     lambda: servico.alternar_fecha_conta(2, de_fora)):
        with pytest.raises(NaoEncontrado):
            operacao()
    with pytest.raises(ErroUsuario, match="próprio usuário"):
        servico.excluir(2, admin, admin)
    assert servico.excluir(2, admin, garcom).usuario == "joao" and garcom not in repo.contas
