"""Copia o núcleo (as regras de negócio em src/domain) para as versões locais.

As versões locais (Comanda e Painel, instaladas no servidor do estabelecimento) usam as
mesmas regras da plataforma. O domínio é sempre copiado. O acesso ao banco
(src/infrastructure) é de cada versão, porque os bancos são diferentes; quando uma versão
tem as mesmas tabelas da plataforma (o Painel local, por exemplo), ela pode receber também
alguns arquivos de infraestrutura, escolhidos na primeira cópia com --infra.

Uso, com os repositórios lado a lado:

    python ferramentas/copiar_nucleo.py ../Comanda
    python ferramentas/copiar_nucleo.py ../S --infra sqlite/datas.py,sqlite/propagandas.py

Nas cópias seguintes, a lista de infraestrutura vem do src/nucleo.json do destino.

A cópia leva o arquivo src/nucleo.json (commit de origem, arquivos e impressão digital) e
o teste tests/test_nucleo.py, que confere que ninguém mudou a cópia à mão: mudanças nas
regras são feitas aqui, na plataforma, e copiadas de novo.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DOMINIO = ("src/__init__.py", "src/domain")


def arquivos_do_nucleo(raiz: Path, infra: list[str] | None = None) -> list[Path]:
    """Os .py do núcleo, em ordem (relativos a `raiz`): o domínio e a infraestrutura escolhida."""
    lista: list[Path] = []
    for item in DOMINIO:
        caminho = raiz / item
        lista += sorted(caminho.rglob("*.py")) if caminho.is_dir() else [caminho]
    lista += [raiz / "src" / "infrastructure" / item for item in sorted(infra or [])]
    return [p.relative_to(raiz) for p in lista]


def impressao_digital(raiz: Path, infra: list[str] | None = None) -> str:
    """sha256 dos caminhos e conteúdos (muda se qualquer arquivo do núcleo mudar, sumir ou aparecer)."""
    soma = hashlib.sha256()
    for relativo in arquivos_do_nucleo(raiz, infra):
        soma.update(relativo.as_posix().encode() + b"\0" + (raiz / relativo).read_bytes() + b"\0")
    return soma.hexdigest()


def commit_atual() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "desconhecido"


def _infra_registrada(destino: Path) -> list[str]:
    registro = destino / "src" / "nucleo.json"
    if not registro.exists():
        return []
    return list(json.loads(registro.read_text(encoding="utf-8")).get("infraestrutura", []))


def copiar(destino: Path, infra: list[str] | None = None) -> str:
    if not (destino / ".git").exists():
        raise SystemExit(f"{destino} não parece um repositório (falta .git).")
    infra = sorted(infra if infra is not None else _infra_registrada(destino))
    for item in infra:
        if not (RAIZ / "src" / "infrastructure" / item).is_file() or not item.endswith(".py") or ".." in item:
            raise SystemExit(f"--infra: {item} não é um arquivo .py de src/infrastructure.")
    shutil.rmtree(destino / "src" / "domain", ignore_errors=True)
    for relativo in arquivos_do_nucleo(RAIZ, infra):
        alvo = destino / relativo
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RAIZ / relativo, alvo)
    (destino / "tests").mkdir(exist_ok=True)
    shutil.copyfile(RAIZ / "ferramentas" / "test_nucleo.py", destino / "tests" / "test_nucleo.py")
    digital = impressao_digital(destino, infra)
    assert digital == impressao_digital(RAIZ, infra)
    registro = {"origem": "LordValdemar/plataforma-comanda", "commit": commit_atual(), "infraestrutura": infra,
                "impressao_digital": digital}
    (destino / "src" / "nucleo.json").write_text(json.dumps(registro, indent=2) + "\n", encoding="utf-8")
    return digital


if __name__ == "__main__":
    argumentos = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    argumentos.add_argument("destinos", nargs="+", help="pastas dos repositórios locais")
    argumentos.add_argument("--infra", help="arquivos de src/infrastructure a copiar também (separados por vírgula)")
    lidos = argumentos.parse_args()
    escolhida = [i.strip() for i in lidos.infra.split(",") if i.strip()] if lidos.infra is not None else None
    for pasta in lidos.destinos:
        print(f"{pasta}: núcleo copiado ({copiar(Path(pasta).resolve(), escolhida)[:12]})")
