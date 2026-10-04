"""A plataforma administrando as empresas, sem banco: criar, suspender (motivo) e excluir."""

import pytest

from src.domain.cobranca import ErroNoGateway
from src.domain.empresas import CadastroInvalido, DadosDaEmpresa, EmpresaCliente, Limites, ServicoDaPlataforma
from src.domain.empresas.plataforma import motivo_da_suspensao
from src.domain.erros import NaoEncontrado

SEM_LIMITE = Limites(None, None)


class PlataformaFalsa:
    def __init__(self):
        self.empresas = {1: EmpresaCliente(1, "Minha", True, None, "", False),
                         2: EmpresaCliente(2, "Mercado", True, None, "painel", True)}
        self.arquivos = {2: ["a.png", "b.mp4"]}
        self.gravado = None

    def cliente(self, empresa_id):
        return self.empresas.get(empresa_id)

    def codigo_em_uso(self, codigo, exceto_id=None):
        return False

    def criar_cliente(self, dados, codigo, cadastro):
        novo = max(self.empresas) + 1
        self.empresas[novo] = EmpresaCliente(novo, dados.nome, True, None, dados.modulos_liberados, False)
        self.gravado = (codigo, dict(cadastro), dados.limites)
        return novo

    def atualizar_cliente(self, empresa_id, nome, limites, ativa, motivo, modulos_liberados):
        self.empresas[empresa_id] = EmpresaCliente(empresa_id, nome, ativa, motivo, modulos_liberados,
                                                   self.empresas[empresa_id].tem_assinatura)

    def arquivos_de_midia(self, empresa_id):
        return self.arquivos.get(empresa_id, [])

    def apagar(self, empresa_id):
        del self.empresas[empresa_id]


@pytest.fixture
def repo():
    return PlataformaFalsa()


def plataforma(repo):
    return ServicoDaPlataforma(repo, 1, codigo_livre=lambda nome: nome.lower().replace(" ", "-"))


def test_criar(repo):
    empresa_id, admin = plataforma(repo).criar(
        DadosDaEmpresa(" Padaria Nova ", Limites(3, 100), "comanda", {"uf": "ma", "nome": "ignorado"}), lambda i: f"admin {i}")
    assert admin == f"admin {empresa_id}" and repo.empresas[empresa_id].nome == "Padaria Nova"
    assert repo.gravado == ("padaria-nova", {"uf": "MA"}, Limites(3, 100))
    with pytest.raises(CadastroInvalido, match="^Informe o nome"):
        plataforma(repo).criar(DadosDaEmpresa(" ", SEM_LIMITE, ""), lambda i: None)
    with pytest.raises(CadastroInvalido, match="^Empresa não criada: O CEP"):
        plataforma(repo).criar(DadosDaEmpresa("X", SEM_LIMITE, "", {"cep": "1"}), lambda i: None)

    def recusa(_empresa_id):
        raise ValueError("usuário já existe")
    antes = set(repo.empresas)
    with pytest.raises(ValueError):
        plataforma(repo).criar(DadosDaEmpresa("Outra", SEM_LIMITE, ""), recusa)
    assert set(repo.empresas) == antes


def test_motivo_da_suspensao():
    ativa = EmpresaCliente(2, "M", True, None, "", False)
    em_atraso = EmpresaCliente(2, "M", False, "inadimplencia", "", False)
    assert motivo_da_suspensao(ativa, False) == "manual"
    assert motivo_da_suspensao(em_atraso, False) == "inadimplencia"      # continua suspensa pelo atraso
    assert motivo_da_suspensao(em_atraso, True) is None


def test_atualizar(repo):
    antes, mudou = plataforma(repo).atualizar(2, DadosDaEmpresa(" ", Limites(1, None), "comanda"), ativa=False)
    assert mudou and antes.ativa and repo.empresas[2] == EmpresaCliente(2, "Mercado", False, "manual", "comanda", True)
    _, mudou = plataforma(repo).atualizar(1, DadosDaEmpresa("Minha", SEM_LIMITE, ""), ativa=False)
    assert not mudou and repo.empresas[1].ativa                          # a principal nunca é suspensa
    with pytest.raises(NaoEncontrado):
        plataforma(repo).atualizar(99, DadosDaEmpresa("x", SEM_LIMITE, ""), ativa=True)


def test_excluir(repo):
    apagados, canceladas = [], []
    with pytest.raises(CadastroInvalido, match="principal"):
        plataforma(repo).excluir(1, "Minha", canceladas.append, apagados.append)
    with pytest.raises(CadastroInvalido, match="nome exato"):
        plataforma(repo).excluir(2, "mercado", canceladas.append, apagados.append)

    def asaas_fora(_empresa_id):
        raise ErroNoGateway("fora do ar")
    with pytest.raises(ErroNoGateway):
        plataforma(repo).excluir(2, "Mercado", asaas_fora, apagados.append)
    assert 2 in repo.empresas and apagados == []                          # nada apagado sem parar a cobrança
    assert plataforma(repo).excluir(2, " Mercado ", canceladas.append, apagados.append).nome == "Mercado"
    assert canceladas == [2] and apagados == ["a.png", "b.mp4"] and 2 not in repo.empresas
