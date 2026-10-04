"""A ferramenta que copia o núcleo para as versões locais (e o teste que vai junto)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _ferramenta():
    especificacao = importlib.util.spec_from_file_location("copiar_nucleo", RAIZ / "ferramentas" / "copiar_nucleo.py")
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def _rodar_teste_copiado(destino):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_nucleo.py"],
                          cwd=destino, capture_output=True, text=True)


def test_copia_e_confere(tmp_path):
    ferramenta = _ferramenta()
    destino = tmp_path / "local"
    (destino / ".git").mkdir(parents=True)
    (destino / "src" / "domain" / "velho.py").parent.mkdir(parents=True)
    (destino / "src" / "domain" / "velho.py").write_text("# arquivo que saiu do núcleo\n")
    digital = ferramenta.copiar(destino)
    assert not (destino / "src" / "domain" / "velho.py").exists()
    assert (destino / "src" / "domain" / "comanda" / "servico.py").read_bytes() == \
        (RAIZ / "src" / "domain" / "comanda" / "servico.py").read_bytes()
    assert json.loads((destino / "src" / "nucleo.json").read_text())["impressao_digital"] == digital
    assert _rodar_teste_copiado(destino).returncode == 0
    # Alguém muda uma regra na cópia: o teste da versão local falha.
    regra = destino / "src" / "domain" / "comanda" / "entidades.py"
    regra.write_text(regra.read_text().replace("MAIOR_CEDULA = 20000", "MAIOR_CEDULA = 99999"))
    resultado = _rodar_teste_copiado(destino)
    assert resultado.returncode != 0 and "mude lá e copie de novo" in resultado.stdout


def test_so_copia_para_um_repositorio(tmp_path):
    import pytest

    with pytest.raises(SystemExit, match="não parece um repositório"):
        _ferramenta().copiar(tmp_path)
