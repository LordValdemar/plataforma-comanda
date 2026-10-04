"""Copia o núcleo (as regras de negócio em src/domain) para as versões locais.

As versões locais (Comanda e Painel, instaladas no servidor do estabelecimento) usam as
mesmas regras da plataforma, mas cada uma tem o próprio banco: por isso só o domínio é
copiado, e o acesso ao banco (src/infrastructure) é de cada uma.

Uso, com os repositórios lado a lado:

    python ferramentas/copiar_nucleo.py ../Comanda ../S

A cópia leva o arquivo src/nucleo.json (commit de origem e impressão digital dos arquivos) e
o teste tests/test_nucleo.py, que confere que ninguém mudou a cópia à mão: mudanças nas
regras são feitas aqui, na plataforma, e copiadas de novo.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVOS = ("src/__init__.py", "src/domain")


def arquivos_do_nucleo(raiz: Path) -> list[Path]:
    """Os .py do núcleo, em ordem (relativos a `raiz`)."""
    lista: list[Path] = []
    for item in ARQUIVOS:
        caminho = raiz / item
        lista += sorted(caminho.rglob("*.py")) if caminho.is_dir() else [caminho]
    return [p.relative_to(raiz) for p in lista]


def impressao_digital(raiz: Path) -> str:
    """sha256 dos caminhos e conteúdos (muda se qualquer arquivo do núcleo mudar, sumir ou aparecer)."""
    soma = hashlib.sha256()
    for relativo in arquivos_do_nucleo(raiz):
        soma.update(relativo.as_posix().encode() + b"\0" + (raiz / relativo).read_bytes() + b"\0")
    return soma.hexdigest()


def commit_atual() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "desconhecido"


def copiar(destino: Path) -> str:
    if not (destino / ".git").exists():
        raise SystemExit(f"{destino} não parece um repositório (falta .git).")
    shutil.rmtree(destino / "src" / "domain", ignore_errors=True)
    for relativo in arquivos_do_nucleo(RAIZ):
        alvo = destino / relativo
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RAIZ / relativo, alvo)
    (destino / "tests").mkdir(exist_ok=True)
    shutil.copyfile(RAIZ / "ferramentas" / "test_nucleo.py", destino / "tests" / "test_nucleo.py")
    digital = impressao_digital(destino)
    assert digital == impressao_digital(RAIZ)
    (destino / "src" / "nucleo.json").write_text(
        json.dumps({"origem": "LordValdemar/plataforma-comanda", "commit": commit_atual(), "impressao_digital": digital},
                   indent=2) + "\n", encoding="utf-8")
    return digital


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for pasta in sys.argv[1:]:
        print(f"{pasta}: núcleo copiado ({copiar(Path(pasta).resolve())[:12]})")
