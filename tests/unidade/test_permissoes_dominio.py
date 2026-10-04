"""Regras das permissões e das autorizações por QR, sem banco: repositório em memória e relógio controlado."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.erros import NaoEncontrado
from src.domain.permissoes import (
    AUTORIZACAO,
    NAO,
    SIM,
    Cadastro,
    CodigoInvalido,
    CodigoJaUsado,
    CodigoVencido,
    ErroDeAutorizacao,
    Pessoa,
    SemPermissao,
    ServicoDeAutorizacoes,
    TabelaDePermissoes,
    descrever,
    ler_minutos,
    modulos_da_pessoa,
    normalizar_codigo,
)
from src.domain.permissoes.liberacoes import Liberacao

LOJA = {"painel", "comanda"}
INICIO = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def gente(id_, papel, **extra):
    return Pessoa(id=id_, papel=papel, modulos=modulos_da_pessoa(papel, False, LOJA), nome=f"{papel}{id_}", **extra)


ADMIN, CAIXA, GARCOM, OUTRO_GARCOM, COZINHA, EDITOR = (
    gente(1, "admin"), gente(2, "caixa"), gente(3, "garcom"), gente(4, "garcom"), gente(5, "cozinha"), gente(6, "editor"),
)


class Relogio:
    def __init__(self):
        self.agora = INICIO

    def __call__(self):
        return self.agora

    def andar(self, **tempo):
        self.agora += timedelta(**tempo)


class RepositorioFalso:
    def __init__(self, pessoas, niveis=None):
        self.cadastros = {p.id: Cadastro(p.id, p.nome, p.papel, fecha_conta=p.fecha_conta) for p in pessoas}
        self.niveis = dict(niveis or {})
        self.liberacoes: dict[int, Liberacao] = {}

    def transacao(self):
        return nullcontext()

    def niveis_configurados(self):
        return dict(self.niveis)

    def gravar_niveis(self, niveis):
        self.niveis.update(niveis)

    def cadastro(self, usuario_id):
        return self.cadastros.get(usuario_id)

    def _nome(self, usuario_id):
        cadastro = self.cadastros.get(usuario_id)
        return cadastro.nome if cadastro else None

    def apagar_antigas(self, antes_de):
        for lib in list(self.liberacoes.values()):
            if lib.criado_em < antes_de and not (lib.modo == "sempre" and lib.usado and lib.revogada_em is None):
                del self.liberacoes[lib.id]

    def apagar_codigos_abertos(self, autorizador_id):
        for lib in list(self.liberacoes.values()):
            if lib.autorizado_por == autorizador_id and not lib.usado:
                del self.liberacoes[lib.id]

    def inserir_codigo(self, codigo, funcao, autorizador_id, criado_em, modo, minutos):
        novo_id = max(self.liberacoes, default=0) + 1
        self.liberacoes[novo_id] = Liberacao(novo_id, codigo, funcao, modo, minutos, criado_em, autorizador_id,
                                             self._nome(autorizador_id))
        return novo_id

    def por_codigo(self, codigo):
        return next((lib for lib in self.liberacoes.values() if lib.codigo == codigo), None)

    def por_id(self, liberacao_id):
        return self.liberacoes.get(liberacao_id)

    def marcar_usado(self, liberacao_id, usuario_id, usado_em, ate):
        lib = self.liberacoes[liberacao_id]
        if lib.usado:
            return False
        self.liberacoes[liberacao_id] = replace(lib, usado_por=usuario_id, quem_usou=self._nome(usuario_id),
                                                usado_em=usado_em, ate=ate)
        return True

    def vigente(self, usuario_id, funcao, agora):
        validas = [lib for lib in self.liberacoes.values()
                   if lib.usado_por == usuario_id and lib.funcao == funcao and lib.vale(agora)]
        return min(validas, key=lambda lib: lib.uma_vez, default=None)

    def consumir(self, ids, agora):
        for i in ids:
            self.liberacoes[i] = replace(self.liberacoes[i], consumida_em=agora)

    def revogar(self, liberacao_id, agora, encerrada_por):
        self.liberacoes[liberacao_id] = replace(self.liberacoes[liberacao_id], revogada_em=agora)

    def ativas(self, agora, autorizador_id):
        return [lib for lib in self.liberacoes.values()
                if lib.vale(agora) and autorizador_id in (None, lib.autorizado_por)]

    def recentes(self, autorizador_id, limite):
        return [lib for lib in self.liberacoes.values()
                if lib.usado and autorizador_id in (None, lib.autorizado_por)][:limite]


def montar(niveis=None, pessoas=(ADMIN, CAIXA, GARCOM, OUTRO_GARCOM, COZINHA, EDITOR)):
    niveis = {("fechar_conta", "garcom"): AUTORIZACAO, **(niveis or {})}
    repo, relogio = RepositorioFalso(pessoas, niveis), Relogio()
    sorteio = iter(f"COD{i:05d}" for i in range(1, 1000))
    servico = ServicoDeAutorizacoes(repo, TabelaDePermissoes(niveis), LOJA, relogio=relogio, sortear=lambda: next(sorteio))
    return servico, repo, relogio


# -- tabela de permissões ---------------------------------------------------------------

def test_padroes_e_niveis_gravados():
    padrao = TabelaDePermissoes()
    assert padrao.nivel("fechar_conta", CAIXA) == SIM
    assert padrao.nivel("fechar_conta", GARCOM) == NAO
    assert padrao.nivel("cozinha", COZINHA) == SIM
    gravada = TabelaDePermissoes({("fechar_conta", "garcom"): AUTORIZACAO, ("cozinha", "cozinha"): NAO})
    assert gravada.nivel("fechar_conta", GARCOM) == AUTORIZACAO
    assert gravada.nivel_do_papel("cozinha", "cozinha") == SIM          # combinação fixa
    assert gravada.papeis_com("fechar_conta", SIM) == ["caixa"]


def test_niveis_que_nao_fazem_sentido_viram_nao():
    tabela = TabelaDePermissoes({("vendas", "caixa"): AUTORIZACAO, ("telas", "editor"): 7, ("telas", "caixa"): SIM})
    assert tabela.nivel_do_papel("vendas", "caixa") == NAO   # função que não aceita autorização
    assert tabela.nivel_do_papel("telas", "editor") == NAO   # valor estranho
    assert tabela.nivel_do_papel("telas", "caixa") == NAO    # papel de outro módulo


def test_administrador_pode_tudo_e_ninguem_logado_nada():
    tabela = TabelaDePermissoes({("fechar_conta", "caixa"): NAO})
    assert tabela.nivel("fechar_conta", ADMIN) == SIM
    assert tabela.nivel("fechar_conta", None) == NAO
    assert tabela.funcoes_que_pode_autorizar(None) == []
    assert not tabela.mostrar_autorizar(None)


def test_sem_o_modulo_nao_pode():
    so_painel = Pessoa(9, "caixa", modulos_da_pessoa("caixa", False, {"painel"}))
    assert so_painel.modulos == frozenset()
    assert TabelaDePermissoes().nivel("fechar_conta", so_painel) == NAO
    admin_so_comanda = Pessoa(10, "admin", modulos_da_pessoa("admin", False, {"comanda"}))
    assert TabelaDePermissoes().nivel("fechar_conta", admin_so_comanda) == SIM
    assert TabelaDePermissoes().nivel("telas", admin_so_comanda) == NAO          # a loja não tem o Painel
    assert "telas" not in TabelaDePermissoes().funcoes_que_pode_autorizar(admin_so_comanda)
    assert modulos_da_pessoa("editor", True, LOJA) == frozenset(LOJA)   # quem opera a plataforma abre tudo


def test_garcom_com_permissao_individual_de_fechar_conta_nao_autoriza():
    tabela = TabelaDePermissoes({("fechar_conta", "garcom"): AUTORIZACAO})
    garcom_que_fecha = replace(GARCOM, fecha_conta=True)
    assert tabela.nivel("fechar_conta", garcom_que_fecha) == SIM
    assert not tabela.pode_autorizar("fechar_conta", garcom_que_fecha)
    assert tabela.pode_autorizar("fechar_conta", CAIXA)
    assert tabela.funcoes_que_pode_autorizar(CAIXA) == ["fechar_conta"]
    assert tabela.mostrar_autorizar(CAIXA) and not tabela.mostrar_autorizar(GARCOM)


def test_administrador_ve_todas_as_funcoes_autorizaveis():
    funcoes = TabelaDePermissoes().funcoes_que_pode_autorizar(ADMIN)
    assert "fechar_conta" in funcoes and "telas" in funcoes and "vendas" not in funcoes
    assert not TabelaDePermissoes().mostrar_autorizar(ADMIN)   # ninguém precisa: o menu não mostra


def test_formulario_so_aceita_valores_validos():
    niveis = TabelaDePermissoes.niveis_do_formulario({
        "fechar_conta.garcom": "2", "vendas.caixa": "2", "cozinha.cozinha": "0", "desconto.caixa": "9",
        "cancelar.caixa": "x", "telas.editor": "1",
    }, {"comanda"})
    assert niveis == {("fechar_conta", "garcom"): AUTORIZACAO}


# -- pequenos ajudantes ------------------------------------------------------------------

def test_descrever_minutos_e_codigo_digitado():
    assert [descrever("uma", 5), descrever("sempre", 5), descrever("minutos", 30), descrever("minutos", 60),
            descrever("minutos", 180)] == ["uma vez", "sem prazo", "por 30 minutos", "por 1 hora", "por 3 horas"]
    assert (ler_minutos("abc"), ler_minutos(None), ler_minutos("0"), ler_minutos("99999")) == (5, 5, 1, 720)
    assert normalizar_codigo(" abc-0d1e fghjk ") == "ABCDEFGH"


# -- código e liberação -------------------------------------------------------------------

def test_codigo_lido_libera_e_guarda_quem_autorizou():
    servico, _, relogio = montar()
    codigo = servico.gerar_codigo(CAIXA, "fechar_conta", "minutos", 10).codigo
    relogio.andar(seconds=30)
    liberacao = servico.usar_codigo(codigo.lower(), GARCOM)
    assert (liberacao.quem_autorizou, liberacao.quem_usou, liberacao.descricao) == ("caixa2", "garcom3", "por 10 minutos")
    assert servico.liberacao_vigente(GARCOM, "fechar_conta").id == liberacao.id
    relogio.andar(minutes=10)
    assert servico.liberacao_vigente(GARCOM, "fechar_conta") is None   # o tempo acabou


def test_codigo_vale_dois_minutos_e_uma_leitura():
    servico, _, relogio = montar()
    codigo = servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo
    relogio.andar(seconds=121)
    with pytest.raises(CodigoVencido):
        servico.usar_codigo(codigo, GARCOM)
    codigo = servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo
    servico.usar_codigo(codigo, GARCOM)
    with pytest.raises(CodigoInvalido):
        servico.usar_codigo(codigo, OUTRO_GARCOM)
    with pytest.raises(CodigoInvalido):
        servico.usar_codigo("NAOEXISTE", GARCOM)


def test_novo_codigo_derruba_o_anterior_nao_lido():
    servico, _, _ = montar()
    primeiro = servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo
    segundo = servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo
    assert servico.situacao_do_codigo(primeiro, CAIXA.id) == ("trocado", None)
    assert servico.situacao_do_codigo(segundo, CAIXA.id) == ("aguardando", None)
    assert servico.situacao_do_codigo(segundo, ADMIN.id) == ("trocado", None)   # o código é de outra pessoa
    servico.usar_codigo(segundo, GARCOM)
    assert servico.situacao_do_codigo(segundo, CAIXA.id) == ("usado", "garcom3")
    with pytest.raises(CodigoInvalido):
        servico.usar_codigo(primeiro, GARCOM)


def test_uma_vez_acaba_quando_gasta_e_sem_prazo_ate_encerrar():
    servico, _, relogio = montar()
    uma = servico.usar_codigo(servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo, GARCOM)
    sempre = servico.usar_codigo(servico.gerar_codigo(CAIXA, "fechar_conta", "sempre", None).codigo, OUTRO_GARCOM)
    servico.gastar([uma.id])
    servico.gastar([])
    assert servico.liberacao_vigente(GARCOM, "fechar_conta") is None
    relogio.andar(days=30)
    assert servico.liberacao_vigente(OUTRO_GARCOM, "fechar_conta").id == sempre.id
    assert [lib.id for lib in servico.ativas(CAIXA)] == [sempre.id]
    servico.encerrar(sempre.id, CAIXA)
    assert servico.liberacao_vigente(OUTRO_GARCOM, "fechar_conta") is None
    assert len(servico.recentes(ADMIN)) == 2


def test_prefere_a_liberacao_com_prazo_a_de_uma_vez():
    servico, _, _ = montar()
    servico.usar_codigo(servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo, GARCOM)
    por_tempo = servico.usar_codigo(servico.gerar_codigo(ADMIN, "fechar_conta", "minutos", 5).codigo, GARCOM)
    assert servico.liberacao_vigente(GARCOM, "fechar_conta").id == por_tempo.id
    assert servico.liberacao_vigente(None, "fechar_conta") is None


def test_quem_nao_pode_autorizar_nao_gera_codigo():
    servico, _, _ = montar()
    with pytest.raises(SemPermissao):
        servico.gerar_codigo(GARCOM, "fechar_conta", "uma", None)
    with pytest.raises(SemPermissao):
        servico.gerar_codigo(CAIXA, "reabrir", "uma", None)   # o caixa não tem "Sim" em reabrir
    with pytest.raises(ErroDeAutorizacao):
        servico.gerar_codigo(CAIXA, "fechar_conta", "para-sempre", None)


@pytest.mark.parametrize(("quem", "mensagem"), [
    (CAIXA, "próprio código"),
    (ADMIN, "não precisa de autorização"),
    (EDITOR, "nem com autorização"),
    (COZINHA, "nem com autorização"),
])
def test_quem_nao_precisa_ou_nao_pode_nao_usa_o_codigo(quem, mensagem):
    servico, _, _ = montar()
    codigo = servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo
    with pytest.raises(ErroDeAutorizacao, match=mensagem):
        servico.usar_codigo(codigo, quem)
    servico.usar_codigo(codigo, GARCOM)   # o código continua valendo para quem precisa


def test_autorizador_que_perdeu_a_permissao_nao_libera():
    servico, repo, _ = montar()
    codigo = servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo
    repo.cadastros[CAIXA.id] = replace(repo.cadastros[CAIXA.id], papel="garcom")   # virou garçom depois
    with pytest.raises(ErroDeAutorizacao, match="não pode mais autorizar"):
        servico.usar_codigo(codigo, GARCOM)
    del repo.cadastros[CAIXA.id]   # ou saiu da loja
    with pytest.raises(ErroDeAutorizacao, match="não pode mais autorizar"):
        servico.usar_codigo(codigo, GARCOM)


def test_leitura_simultanea_so_um_consegue():
    servico, repo, _ = montar()
    codigo = servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo
    liberacao = repo.por_codigo(codigo)
    assert repo.marcar_usado(liberacao.id, OUTRO_GARCOM.id, INICIO, None)   # o outro chegou um instante antes
    repo.liberacoes[liberacao.id] = liberacao                                # ...depois da conferência deste
    repo.marcar_usado = lambda *_: False
    with pytest.raises(CodigoJaUsado):
        servico.usar_codigo(codigo, GARCOM)


def test_encerrar_so_quem_deu_ou_o_administrador():
    servico, _, _ = montar()
    lib = servico.usar_codigo(servico.gerar_codigo(CAIXA, "fechar_conta", "sempre", None).codigo, GARCOM)
    with pytest.raises(SemPermissao):
        servico.encerrar(lib.id, OUTRO_GARCOM)
    with pytest.raises(NaoEncontrado):
        servico.encerrar(999, ADMIN)
    assert servico.encerrar(lib.id, ADMIN).id == lib.id
    assert servico.ativas(ADMIN) == []


def test_historico_velho_sai_mas_sem_prazo_valendo_fica():
    servico, repo, relogio = montar()
    antiga = servico.usar_codigo(servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None).codigo, GARCOM)
    sempre = servico.usar_codigo(servico.gerar_codigo(ADMIN, "fechar_conta", "sempre", None).codigo, OUTRO_GARCOM)
    relogio.andar(days=91)
    servico.gerar_codigo(CAIXA, "fechar_conta", "uma", None)
    assert antiga.id not in repo.liberacoes and sempre.id in repo.liberacoes
