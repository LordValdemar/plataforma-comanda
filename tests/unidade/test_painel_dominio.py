"""Regras do painel de propagandas sem banco: agenda, destinos, lote, ordem e o que vai para cada TV."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.domain.erros import NaoEncontrado
from src.domain.painel import (
    Destinos,
    ErroDePropaganda,
    Programacao,
    Propaganda,
    ServicoDePropagandas,
    TelaDaPlaylist,
    ler_data,
    ler_duracao,
    ler_letreiro,
    ler_registro,
    nova_ordem,
)

FUSO = ZoneInfo("America/Sao_Paulo")
QUINTA_8H = datetime(2026, 10, 1, 8, 0, tzinfo=FUSO)


def propaganda(id_=1, **campos):
    return Propaganda(id=id_, nome=f"p{id_}", arquivo=f"{id_}.png", tipo="imagem", duracao=10, posicao=id_, **campos)


# -- leitura do formulário ---------------------------------------------------------------

def test_valores_do_formulario():
    assert (ler_duracao("15"), ler_duracao("0"), ler_duracao("99999"), ler_duracao("x"), ler_duracao(None)) == (15, 1, 3600, 10, 10)
    assert (ler_data("2026-02-28"), ler_data("2026-02-30"), ler_data(" "), ler_data(None)) == ("2026-02-28", None, None, None)
    assert (ler_letreiro("nenhum", "x"), ler_letreiro("proprio", "  Oi "), ler_letreiro("proprio", " "),
            ler_letreiro("geral", "x")) == ("", "Oi", None, None)


# -- agenda ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("campos", "agora", "codigo"), [
    ({}, QUINTA_8H, "no_ar"),
    ({"ativo": False}, QUINTA_8H, "inativa"),
    ({"inicio": "2026-10-02"}, QUINTA_8H, "agendada"),
    ({"fim": "2026-09-30"}, QUINTA_8H, "encerrada"),
    ({"fim": "2026-10-01"}, QUINTA_8H, "no_ar"),                    # o último dia ainda vale
    ({"dias_semana": "4"}, QUINTA_8H, "fora_do_dia"),
    ({"hora_inicio": "06:00", "hora_fim": "10:00"}, QUINTA_8H, "no_ar"),
    ({"hora_inicio": "06:00", "hora_fim": "10:00"}, QUINTA_8H.replace(hour=10), "fora_do_horario"),
    ({"hora_inicio": "22:00", "hora_fim": "02:00"}, QUINTA_8H.replace(hour=1), "no_ar"),   # vira a noite
    ({"hora_inicio": "22:00", "hora_fim": "02:00"}, QUINTA_8H.replace(hour=3), "fora_do_horario"),
    ({"hora_inicio": "18:00"}, QUINTA_8H.replace(hour=23, minute=59), "no_ar"),             # sem fim: até meia-noite
])
def test_situacao(campos, agora, codigo):
    assert propaganda(**campos).situacao(agora)[0] == codigo


def test_texto_da_situacao_agendada():
    assert propaganda(inicio="2026-12-25").situacao(QUINTA_8H) == ("agendada", "Começa em 25/12/2026")


def test_programacao_confere_o_que_foi_escolhido():
    base = Programacao("x", 10, True, None, None, "0123456", None, None, True, None)
    base.conferir()
    for mudanca, mensagem in [
        ({"inicio": "2026-10-10", "fim": "2026-10-01"}, "término"),
        ({"dias_semana": ""}, "dia da semana"),
        ({"hora_inicio": "10:00", "hora_fim": "10:00"}, "iguais"),
        ({"para_todas": False}, "tela ou grupo"),
    ]:
        with pytest.raises(ErroDePropaganda, match=mensagem):
            replace(base, **mudanca).conferir()
    replace(base, para_todas=False, destinos=Destinos(grupos=frozenset({3}))).conferir()


# -- destinos e ordem ----------------------------------------------------------------------------

def test_destinos():
    atuais = Destinos(frozenset({1, 2}), frozenset({7}))
    outros = Destinos(frozenset({2, 3}), frozenset())
    assert atuais.combinar("trocar", outros) == outros
    assert atuais.combinar("acrescentar", outros) == Destinos(frozenset({1, 2, 3}), frozenset({7}))
    assert atuais.combinar("tirar", outros) == Destinos(frozenset({1}), frozenset({7}))
    with pytest.raises(ErroDePropaganda):
        atuais.combinar("misturar", outros)
    assert atuais.so_da_loja({1}, set()) == Destinos(frozenset({1}), frozenset())
    assert atuais.inclui(5, 7) and atuais.inclui(1, None) and not atuais.inclui(5, None)
    assert not Destinos()


def test_aparece_na_tela():
    so_grupo = propaganda(para_todas=False, destinos=Destinos(grupos=frozenset({7})))
    assert so_grupo.aparece_na(10, 7) and not so_grupo.aparece_na(10, None)
    assert not so_grupo.aparece_na(None, None)            # o player geral só mostra as "para todas"
    assert propaganda().aparece_na(None, None)
    assert not propaganda(para_todas=False).aparece_na(10, 7)   # sem destino: em lugar nenhum


def test_nova_ordem():
    assert nova_ordem([1, 2, 3], 2, "cima") == [2, 1, 3]
    assert nova_ordem([1, 2, 3], 2, "baixo") == [1, 3, 2]
    assert nova_ordem([1, 2, 3], 1, "cima") is None and nova_ordem([1, 2, 3], 3, "baixo") is None
    with pytest.raises(ErroDePropaganda):
        nova_ordem([1, 2], 1, "lado")


# -- registros que a TV manda ----------------------------------------------------------------------

def test_registro_de_exibicao():
    agora = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    antigo = agora - timedelta(days=90)
    certo = {"propaganda_id": "4", "duracao": 9.96, "inicio": "2026-10-01T11:59:00Z"}
    lido = ler_registro(certo, agora, antigo)
    assert (lido.propaganda_id, lido.duracao, lido.inicio) == (4, 10.0, datetime(2026, 10, 1, 11, 59, tzinfo=timezone.utc))
    for errado in ("x", {}, {**certo, "duracao": 0}, {**certo, "duracao": 90000}, {**certo, "inicio": "2026-10-01T11:00"},
                   {**certo, "inicio": "2026-10-01T14:00:00Z"}, {**certo, "inicio": "2025-01-01T00:00:00Z"},
                   {**certo, "propaganda_id": "abc"}):
        assert ler_registro(errado, agora, antigo) is None


# -- serviço ----------------------------------------------------------------------------------------

class RepositorioFalso:
    def __init__(self, itens=(), telas=(10, 11), grupos=(7,)):
        self.itens = {p.id: p for p in itens}
        self.telas, self.grupos = set(telas), set(grupos)
        self.configs, self.exibicoes = {}, []

    def transacao(self):
        return nullcontext()

    def listar(self):
        return sorted(self.itens.values(), key=lambda p: (p.posicao, p.id))

    def buscar(self, pid):
        return self.itens.get(pid)

    def telas_da_loja(self):
        return self.telas

    def grupos_da_loja(self):
        return self.grupos

    def inserir(self, nome, arquivo, tipo, tamanho, duracao, para_todas):
        pid = max(self.itens, default=0) + 1
        self.itens[pid] = Propaganda(pid, nome, arquivo, tipo, duracao, posicao=pid, tamanho=tamanho, para_todas=para_todas)
        return pid

    def gravar_programacao(self, pid, p):
        self.itens[pid] = replace(self.itens[pid], nome=p.nome, duracao=p.duracao, ativo=p.ativo, inicio=p.inicio,
                                  fim=p.fim, dias_semana=p.dias_semana, hora_inicio=p.hora_inicio, hora_fim=p.hora_fim,
                                  para_todas=p.para_todas, letreiro=p.letreiro)

    def gravar_destinos(self, pid, destinos):
        self.itens[pid] = replace(self.itens[pid], destinos=destinos)

    def gravar_ordem(self, ids):
        for posicao, pid in enumerate(ids, start=1):
            self.itens[pid] = replace(self.itens[pid], posicao=posicao)

    def definir(self, ids, campo, valor):
        for pid in ids:
            self.itens[pid] = replace(self.itens[pid], **{campo: valor})

    def excluir(self, pid):
        del self.itens[pid]

    def config(self, chave):
        return self.configs.get(chave)

    def gravar_config(self, chave, valor):
        self.configs[chave] = valor

    def nomes(self):
        return {p.id: p.nome for p in self.itens.values()}

    def gravar_exibicoes(self, tela_id, exibicoes):
        self.exibicoes += [(tela_id, e, nome) for e, nome in exibicoes]
        return len(exibicoes)


def montar(itens=(), momento=QUINTA_8H):
    repo = RepositorioFalso(itens)
    return ServicoDePropagandas(repo, FUSO, relogio=lambda: momento), repo


def test_cadastrar_no_fim_e_com_destinos_da_loja():
    servico, repo = montar()
    todas = servico.cadastrar("a.png", "1.png", "imagem", 100, "7", None)
    nenhuma = servico.cadastrar("b.png", "2.png", "imagem", 100, None, Destinos())
    so_balcao = servico.cadastrar("c" * 300, "3.png", "imagem", 100, 5, Destinos(frozenset({10, 999})))
    assert repo.itens[todas].para_todas and repo.itens[todas].duracao == 7
    assert not repo.itens[nenhuma].para_todas and not repo.itens[nenhuma].destinos
    assert repo.itens[so_balcao].destinos == Destinos(frozenset({10}))   # a 999 não é desta loja
    assert len(repo.itens[so_balcao].nome) == 200
    assert [p.id for p in servico.lista()[0]] == [todas, nenhuma, so_balcao]


def test_atualizar_confere_e_mantem_o_nome_se_vier_vazio():
    servico, repo = montar([propaganda(1, para_todas=False, destinos=Destinos(frozenset({10})))])
    prog = Programacao("", 20, True, None, None, "01234", "08:00", "18:00", False, "",
                       Destinos(frozenset({11})))
    atualizada = servico.atualizar(1, prog)
    assert (atualizada.nome, atualizada.duracao, atualizada.destinos.telas) == ("p1", 20, frozenset({11}))
    with pytest.raises(ErroDePropaganda, match="“p1”: Escolha pelo menos uma tela"):
        servico.atualizar(1, replace(prog, destinos=Destinos(frozenset({999}))))
    todas = servico.atualizar(1, replace(prog, para_todas=True))
    assert todas.para_todas and not todas.destinos     # "todas as telas" limpa a lista
    with pytest.raises(NaoEncontrado):
        servico.atualizar(9, prog)


def test_mover_e_excluir():
    servico, repo = montar([propaganda(1), propaganda(2), propaganda(3)])
    servico.mover(3, "cima")
    servico.mover(1, "cima")                     # já é a primeira: nada muda
    assert [p.id for p in repo.listar()] == [1, 3, 2]
    with pytest.raises(NaoEncontrado):
        servico.mover(9, "cima")
    assert servico.excluir(2).arquivo == "2.png" and 2 not in repo.itens
    with pytest.raises(NaoEncontrado):
        servico.excluir(2)


def test_acoes_em_lote():
    servico, repo = montar([propaganda(1), propaganda(2, para_todas=False, destinos=Destinos(frozenset({10}))),
                            propaganda(3)])
    with pytest.raises(ErroDePropaganda, match="Marque pelo menos uma"):
        servico.marcadas([99])
    itens = servico.marcadas([2, 1, 99])
    assert [p.id for p in itens] == [1, 2]
    servico.definir_em_lote(itens, "ativo", False)
    servico.definir_em_lote(itens, "duracao", "99999")
    assert [(p.ativo, p.duracao) for p in repo.listar()] == [(False, 3600), (False, 3600), (True, 10)]
    with pytest.raises(ErroDePropaganda):
        servico.definir_em_lote(itens, "arquivo", "x")
    servico.definir_destinos_em_lote(servico.marcadas([1, 2]), "acrescentar", Destinos(frozenset({11})))
    assert repo.itens[1].destinos.telas == {11} and not repo.itens[1].para_todas   # "todas" parte do zero
    assert repo.itens[2].destinos.telas == {10, 11}
    servico.definir_destinos_em_lote(servico.marcadas([2]), "tirar", Destinos(frozenset({10})))
    assert repo.itens[2].destinos.telas == {11}
    with pytest.raises(ErroDePropaganda, match="tela ou grupo"):
        servico.definir_destinos_em_lote(itens, "trocar", Destinos(frozenset({999})))
    servico.definir_destinos_em_lote(servico.marcadas([1, 2]), "trocar", None)
    assert all(repo.itens[i].para_todas and not repo.itens[i].destinos for i in (1, 2))


def test_playlist_de_cada_tela():
    itens = [
        propaganda(1),
        propaganda(2, para_todas=False, destinos=Destinos(frozenset({10}))),
        propaganda(3, para_todas=False, destinos=Destinos(grupos=frozenset({7}))),
        propaganda(4, hora_inicio="12:00", hora_fim="14:00"),
        propaganda(5, letreiro="Promoção"),
    ]
    servico, repo = montar(itens)
    assert [p.id for p in servico.playlist(TelaDaPlaylist(10)).itens] == [1, 2, 5]
    assert [p.id for p in servico.playlist(TelaDaPlaylist(11, grupo_id=7)).itens] == [1, 3, 5]
    assert [p.id for p in servico.playlist(None).itens] == [1, 5]
    servico.gravar_letreiro("  Bem-vindo  ")
    assert servico.playlist(TelaDaPlaylist(10)).letreiro == "Bem-vindo"
    assert servico.playlist(TelaDaPlaylist(10, letreiro="Só aqui")).letreiro == "Só aqui"
    servico.pausar(True)
    pausada = servico.playlist(TelaDaPlaylist(10))
    assert pausada.pausado and pausada.itens == [] and servico.pausado
    servico.pausar(False)
    assert not servico.pausado


def test_registrar_exibicoes():
    agora = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    servico, repo = montar([propaganda(1)], momento=agora)
    registros = [{"propaganda_id": 1, "duracao": 10, "inicio": "2026-10-01T11:59:00Z"},
                 {"propaganda_id": 8, "duracao": 5, "inicio": "2026-10-01T11:58:00Z"},
                 {"lixo": True}]
    assert servico.registrar_exibicoes(10, registros, reter_dias=90) == (3, 2)
    assert [nome for _, _, nome in repo.exibicoes] == ["p1", "(propaganda excluída)"]
    assert servico.registrar_exibicoes(10, [registros[0]] * 1500, reter_dias=90) == (1000, 1000)
    assert servico.nome_exibindo(1) == "p1"
    assert servico.nome_exibindo("1") is None and servico.nome_exibindo(True) is None and servico.nome_exibindo(9) is None
