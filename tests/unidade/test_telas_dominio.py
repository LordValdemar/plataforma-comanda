"""Regras das telas sem banco: endereço, crachá do aparelho, pedido de conexão pelo QR e sinal de vida."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.erros import NaoEncontrado
from src.domain.painel import (
    ErroDeTela,
    MuitosPedidos,
    PedidoDeConexao,
    PedidoVencido,
    ServicoDeConexao,
    ServicoDeTelas,
    Tela,
    normalizar_codigo_de_pedido,
)
from src.domain.painel.telas import hash_do_cracha, novo_endereco

AGORA = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def tela(id_=1, **campos):
    return Tela(id=id_, empresa_id=1, nome=f"T{id_}", codigo=f"t{id_}-abc123", **campos)


def test_endereco_da_tela():
    assert novo_endereco("Promoções da Vitrine!").startswith("promocoes-da-vitrine-")
    assert novo_endereco("***").startswith("tela-")
    assert len(novo_endereco("x" * 80).rsplit("-", 1)[0]) == 30
    assert novo_endereco("a") != novo_endereco("a")
    assert normalizar_codigo_de_pedido(" ab-c 12 9z ") == "ABC129"


def test_cracha_e_sinal_de_vida():
    conectada = tela(aparelho_hash=hash_do_cracha("segredo"))
    assert conectada.aceita("segredo") and not conectada.aceita("outro") and not conectada.aceita(None)
    assert not tela().aceita("segredo")
    assert tela().precisa_registrar_contato(AGORA, com_novidade=False)               # nunca deu sinal
    recente = tela(ultimo_contato=AGORA - timedelta(seconds=10))
    assert not recente.precisa_registrar_contato(AGORA, com_novidade=False)           # 10 s: não grava de novo
    assert recente.precisa_registrar_contato(AGORA, com_novidade=True)
    assert tela(ultimo_contato=AGORA - timedelta(seconds=31)).precisa_registrar_contato(AGORA, False)
    acabou_de_fechar = tela(ultimo_contato=AGORA, fechada_em=AGORA - timedelta(seconds=1))
    assert not acabou_de_fechar.precisa_registrar_contato(AGORA, com_novidade=True)  # pedido que já estava a caminho
    assert replace(acabou_de_fechar, fechada_em=AGORA - timedelta(seconds=5)).precisa_registrar_contato(AGORA, False)


# -- serviço de quem administra ----------------------------------------------------------------

class RepoTelas:
    def __init__(self):
        self.lista, self.grupos, self.pedidos, self.em_uso = {}, {7: "SP"}, {}, set()

    def telas(self):
        return sorted(self.lista.values(), key=lambda t: t.nome)

    def tela(self, tela_id):
        return self.lista.get(tela_id)

    def endereco_em_uso(self, codigo):
        return codigo in self.em_uso or any(t.codigo == codigo for t in self.lista.values())

    def inserir_tela(self, nome, codigo, grupo_id):
        novo = max(self.lista, default=0) + 1
        self.lista[novo] = Tela(novo, 1, nome, codigo, grupo_id)
        return novo

    def atualizar_tela(self, tela_id, nome, grupo_id, letreiro):
        self.lista[tela_id] = replace(self.lista[tela_id], nome=nome, grupo_id=grupo_id, letreiro=letreiro)

    def trocar_endereco(self, tela_id, codigo):
        self.lista[tela_id] = replace(self.lista[tela_id], codigo=codigo)

    def desligar_aparelho(self, tela_id):
        self.lista[tela_id] = replace(self.lista[tela_id], aparelho_hash=None, aceita_link=False)

    def excluir_tela(self, tela_id):
        del self.lista[tela_id]

    def grupo(self, grupo_id):
        return self.grupos.get(grupo_id)

    def grupo_com_nome(self, nome):
        return nome in self.grupos.values()

    def inserir_grupo(self, nome):
        self.grupos[max(self.grupos) + 1] = nome

    def excluir_grupo(self, grupo_id):
        del self.grupos[grupo_id]

    def pedido(self, codigo):
        return self.pedidos.get(codigo)

    def ligar_aparelho(self, tela_id, pedido, agora):
        self.lista[tela_id] = replace(self.lista[tela_id], aparelho_hash=pedido.segredo_hash)
        self.pedidos[pedido.codigo] = replace(pedido, tela_id=tela_id)


def test_cadastro_de_telas_e_grupos():
    repo = RepoTelas()
    enderecos = iter(["balcao-aaaaaa", "balcao-aaaaaa", "balcao-bbbbbb", "balcao-cccccc"])
    telas = ServicoDeTelas(repo, relogio=lambda: AGORA, sortear_endereco=lambda nome: next(enderecos))
    balcao = telas.cadastrar("  Balcão  ", 7, cabe_no_plano=True)
    assert (balcao.nome, balcao.codigo, balcao.grupo_id, balcao.aceita_link) == ("Balcão", "balcao-aaaaaa", 7, False)
    assert telas.cadastrar("Balcão", 99, cabe_no_plano=True).codigo == "balcao-bbbbbb"   # sorteou de novo
    assert telas.tela(2).grupo_id is None                                                  # grupo de outra loja
    for nome, cabe, mensagem in (("", True, "Dê um nome"), ("X", False, "limite de telas")):
        with pytest.raises(ErroDeTela, match=mensagem):
            telas.cadastrar(nome, None, cabe_no_plano=cabe)
    atualizada = telas.atualizar(1, "", 99, "  Oi  ")
    assert (atualizada.nome, atualizada.grupo_id, atualizada.letreiro) == ("Balcão", None, "Oi")
    assert telas.atualizar(1, "Caixa", 7, "  ").letreiro is None
    telas.trocar_endereco(1)
    assert telas.tela(1).codigo == "balcao-cccccc"
    with pytest.raises(NaoEncontrado):
        telas.excluir(9)
    assert telas.excluir(2).nome == "Balcão" and [t.id for t in telas.telas()] == [1]
    assert telas.criar_grupo(" RJ ") == "RJ"
    with pytest.raises(ErroDeTela, match="já existe"):
        telas.criar_grupo("RJ")
    assert telas.excluir_grupo(7) == "SP"
    with pytest.raises(NaoEncontrado):
        telas.excluir_grupo(7)


def test_escolher_a_tela_de_uma_tv():
    repo = RepoTelas()
    telas = ServicoDeTelas(repo, relogio=lambda: AGORA)
    balcao = telas.cadastrar("Balcão", None, cabe_no_plano=True)
    repo.pedidos["ABC123"] = PedidoDeConexao(1, "ABC123", hash_do_cracha("s"), AGORA - timedelta(minutes=9))
    repo.pedidos["VELHO1"] = PedidoDeConexao(2, "VELHO1", hash_do_cracha("v"), AGORA - timedelta(minutes=11))
    with pytest.raises(PedidoVencido):
        telas.pedido("VELHO1")
    with pytest.raises(PedidoVencido):
        telas.pedido("NAOHA1")
    with pytest.raises(ErroDeTela, match="Escolha uma das telas"):
        telas.conectar("abc123", 99)
    assert telas.conectar("abc123", balcao.id).id == balcao.id
    assert telas.tela(balcao.id).aceita("s")
    with pytest.raises(ErroDeTela, match="já foi conectada"):
        telas.conectar("ABC123", balcao.id)
    assert telas.desconectar_aparelho(balcao.id).id == balcao.id and not telas.tela(balcao.id).conectada


# -- serviço do lado da TV ---------------------------------------------------------------------------

class RepoConexoes:
    def __init__(self, telas=()):
        self.telas = {t.id: t for t in telas}
        self.pedidos, self.contatos, self.fechadas = {}, [], []

    def tela_por_endereco(self, codigo):
        return next((t for t in self.telas.values() if t.codigo == codigo), None)

    def tela(self, tela_id):
        return self.telas.get(tela_id)

    def ligar_primeiro_aparelho(self, tela_id, cracha_hash, agora):
        if self.telas[tela_id].aparelho_hash is not None:
            return False
        self.telas[tela_id] = replace(self.telas[tela_id], aparelho_hash=cracha_hash, aceita_link=False)
        return True

    def apagar_pedidos_antigos(self, antes_de):
        self.pedidos = {k: p for k, p in self.pedidos.items() if p.criado_em >= antes_de}

    def pedidos_abertos(self):
        return len(self.pedidos)

    def codigo_de_pedido_em_uso(self, codigo):
        return codigo in self.pedidos

    def inserir_pedido(self, codigo, segredo_hash, agora):
        self.pedidos[codigo] = PedidoDeConexao(len(self.pedidos) + 1, codigo, segredo_hash, agora)

    def pedido_pelo_segredo(self, segredo_hash):
        return next((p for p in self.pedidos.values() if p.segredo_hash == segredo_hash), None)

    def apagar_pedido(self, pedido_id):
        self.pedidos = {k: p for k, p in self.pedidos.items() if p.id != pedido_id}

    def registrar_contato(self, tela_id, agora, ip, navegador, exibindo, mudar_exibindo):
        self.contatos.append((tela_id, ip, navegador, exibindo, mudar_exibindo))

    def marcar_fechada(self, tela_id, agora):
        self.fechadas.append(tela_id)


def test_tela_antiga_aceita_o_primeiro_aparelho_uma_vez():
    repo = RepoConexoes([tela(1, aceita_link=True), tela(2)])
    tv = ServicoDeConexao(repo, relogio=lambda: AGORA, sortear_cracha=lambda: "novo")
    assert tv.autorizar(repo.tela(1), None) == (True, "novo")
    assert tv.autorizar(repo.tela(1), "novo") == (True, None)
    assert tv.autorizar(repo.tela(1), "outro") == (False, None)       # o segundo aparelho não entra
    assert tv.autorizar(repo.tela(2), "qualquer") == (False, None)    # tela nova: só pelo QR
    corrida = replace(repo.tela(1), aparelho_hash=None, aceita_link=True)   # leu antes do outro gravar
    assert tv.autorizar(corrida, None) == (False, None)


def test_pedido_de_conexao_ate_a_tv_ficar_pronta():
    repo = RepoConexoes([tela(1), tela(2)])
    codigos = iter(["AAAAAA", "AAAAAA", "BBBBBB", "CCCCCC"])
    relogio = [AGORA]
    tv = ServicoDeConexao(repo, relogio=lambda: relogio[0], sortear_codigo=lambda: next(codigos),
                          sortear_cracha=lambda: "segredo-" + str(len(repo.pedidos)))
    assert tv.abrir_pedido() == ("AAAAAA", "segredo-0")
    assert tv.abrir_pedido() == ("BBBBBB", "segredo-1")               # código repetido: sorteia outro
    assert tv.situacao_do_pedido("segredo-0") == ("aguardando", None)
    assert tv.situacao_do_pedido("errado") == ("vencido", None) and tv.situacao_do_pedido("") == ("vencido", None)
    # quem administra escolheu a tela 2 para o pedido AAAAAA
    repo.pedidos["AAAAAA"] = replace(repo.pedidos["AAAAAA"], tela_id=2)
    repo.telas[2] = replace(repo.telas[2], aparelho_hash=hash_do_cracha("segredo-0"))
    assert tv.situacao_do_pedido("segredo-0") == ("pronto", repo.tela(2))
    assert "AAAAAA" not in repo.pedidos                                 # o pedido acaba
    # outra TV ficou com a tela antes desta perguntar
    repo.pedidos["BBBBBB"] = replace(repo.pedidos["BBBBBB"], tela_id=1)
    assert tv.situacao_do_pedido("segredo-1") == ("vencido", None)
    relogio[0] = AGORA + timedelta(minutes=11)
    assert tv.abrir_pedido()[0] == "CCCCCC"
    assert list(repo.pedidos) == ["CCCCCC"]                             # os anteriores já acabaram


def test_muitos_pedidos_abertos():
    repo = RepoConexoes()
    for i in range(500):
        repo.inserir_pedido(f"P{i:05d}", f"h{i}", AGORA)
    with pytest.raises(MuitosPedidos):
        ServicoDeConexao(repo, relogio=lambda: AGORA).abrir_pedido()


def test_telas_do_aparelho_e_contato():
    a = tela(1, aparelho_hash=hash_do_cracha("a"))
    b = replace(tela(2, aparelho_hash=hash_do_cracha("b")), nome="Abertura")
    repo = RepoConexoes([a, b, tela(3)])
    tv = ServicoDeConexao(repo, relogio=lambda: AGORA)
    assert [t.id for t in tv.telas_do_aparelho({1: "a", 2: "b", 3: "x", 9: "y", 1_000: ""})] == [2, 1]
    assert tv.telas_do_aparelho({1: "errado"}) == []
    tv.registrar_contato(a, "1.2.3.4" * 10, "Navegador" * 50)
    tv.registrar_contato(replace(a, ultimo_contato=AGORA), "ip", "nav")                    # 0 s depois: não grava
    tv.registrar_contato(replace(a, ultimo_contato=AGORA), "ip", "nav", exibindo="Café", mudar_exibindo=True)
    assert [(c[0], len(c[1]), len(c[2]), c[3], c[4]) for c in repo.contatos] == [(1, 45, 200, None, False), (1, 2, 3, "Café", True)]
    tv.marcar_fechada(a)
    assert repo.fechadas == [1] and tv.tela("t1-abc123").id == 1 and tv.tela("nada") is None
