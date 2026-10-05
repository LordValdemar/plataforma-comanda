"""
Ferramentas de administração pela linha de comando.

    python gerenciar.py listar-empresas
    python gerenciar.py criar-empresa "Padaria do João" [--limite-telas 5] [--limite-mb 2000]
    python gerenciar.py listar-usuarios
    python gerenciar.py criar-usuario NOME [--empresa ID] [--papel admin|editor] [--plataforma]
    python gerenciar.py trocar-senha NOME
    python gerenciar.py desativar-2fa NOME          # perdeu o celular
    python gerenciar.py backup
    python gerenciar.py restaurar CAMINHO_DO_BACKUP.zip
"""

import argparse
import getpass
import sys

from propagandas import create_app, db, planos
from propagandas.auth import EMPRESA_PRINCIPAL, PAPEIS, ErroUsuario, criar_usuario, desativar_2fa, trocar_senha
from propagandas.auth import servico as servico_de_contas
from propagandas.backup import criar_backup, restaurar_backup
from propagandas.planos import MB
from src.config import arquivo as arquivo_config
from src.domain.empresas import DadosDaEmpresa, Limites


def pedir_senha():
    senha = getpass.getpass("Senha: ")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    return senha


def buscar_usuario(conexao, nome, loja=None):
    """O mesmo nome pode existir em várias lojas: nesse caso, informe --loja CÓDIGO."""
    contas = servico_de_contas(conexao).contas_chamadas(nome, loja)
    if not contas:
        sys.exit(f"Usuário “{nome}” não encontrado.")
    if len(contas) > 1:
        sys.exit(f"Há usuários “{nome}” em mais de uma loja. Informe o código: --loja CÓDIGO (veja listar-usuarios).")
    return contas[0].id


def main(argumentos=None):
    parser = argparse.ArgumentParser(description="Administração do Painel de Propagandas")
    comandos = parser.add_subparsers(dest="comando", required=True)

    comandos.add_parser("listar-empresas", help="mostra as empresas (clientes)")
    empresa = comandos.add_parser("criar-empresa", help="cria uma empresa (cliente)")
    empresa.add_argument("nome")
    empresa.add_argument("--limite-telas", type=int)
    empresa.add_argument("--limite-mb", type=int)

    comandos.add_parser("listar-usuarios", help="mostra os usuários cadastrados")
    criar = comandos.add_parser("criar-usuario", help="cria um usuário")
    criar.add_argument("usuario")
    criar.add_argument("--empresa", type=int, default=EMPRESA_PRINCIPAL, help="id da empresa (padrão: 1, a principal)")
    criar.add_argument("--papel", choices=sorted(PAPEIS), default="admin")
    criar.add_argument("--plataforma", action="store_true", help="também administra a plataforma (todas as empresas)")

    trocar = comandos.add_parser("trocar-senha", help="redefine a senha (útil se esqueceu)")
    trocar.add_argument("usuario")
    trocar.add_argument("--loja", help="código da loja (se o nome existir em mais de uma)")
    dois_fatores = comandos.add_parser("desativar-2fa", help="desativa a verificação em duas etapas de um usuário")
    dois_fatores.add_argument("usuario")
    dois_fatores.add_argument("--loja", help="código da loja (se o nome existir em mais de uma)")

    comandos.add_parser("backup", help="faz um backup agora")
    restaurar = comandos.add_parser("restaurar", help="restaura um backup (pare o servidor antes)")
    restaurar.add_argument("arquivo")

    args = parser.parse_args(argumentos)
    arquivo_config.carregar()  # configuracao.env, se existir
    app = create_app()

    with app.app_context():
        conexao = db.obter()
        try:
            if args.comando == "listar-empresas":
                for e in planos.consultas(conexao).empresas_com_uso():
                    situacao = "ativa" if e["ativa"] else "SUSPENSA"
                    print(f"{e['id']:>4}  {e['nome']:<30} {situacao:<9} telas {e['telas']}/{e['limite_telas'] or '∞'}  "
                          f"{e['bytes'] / MB:.1f}/{e['limite_mb'] or '∞'} MB")

            elif args.comando == "criar-empresa":
                novo, _ = planos.servico_da_plataforma(conexao).criar(
                    DadosDaEmpresa(args.nome, Limites(args.limite_telas, args.limite_mb), "painel"))
                print(f"Empresa “{args.nome}” criada com id {novo}.")
                print(f"Crie o administrador: python gerenciar.py criar-usuario NOME --empresa {novo}")

            elif args.comando == "listar-usuarios":
                for linha in planos.consultas(conexao).todos_os_usuarios():
                    extras = (" +plataforma" if linha["plataforma"] else "") + (" +2FA" if linha["totp_segredo"] else "")
                    print(f"{linha['usuario']:<25} {PAPEIS[linha['papel']]:<14} {linha['empresa']:<25} loja {linha['slug']}{extras}")

            elif args.comando == "criar-usuario":
                criar_usuario(conexao, args.empresa, args.usuario, pedir_senha(), args.papel, args.plataforma)
                print(f"Usuário “{args.usuario}” criado ({PAPEIS[args.papel]}).")

            elif args.comando == "trocar-senha":
                trocar_senha(conexao, buscar_usuario(conexao, args.usuario, args.loja), pedir_senha())
                print("Senha alterada.")

            elif args.comando == "desativar-2fa":
                desativar_2fa(conexao, buscar_usuario(conexao, args.usuario, args.loja))
                print("Verificação em duas etapas desativada. O usuário entra só com a senha e pode ativar de novo.")

            elif args.comando == "backup":
                print("Backup criado em:", criar_backup(app.config))

            elif args.comando == "restaurar":
                db.fechar()
                resposta = input("Isso substitui TODOS os dados atuais (de todas as empresas). Continuar? [s/N] ")
                if resposta.strip().lower() != "s":
                    sys.exit("Cancelado.")
                guardados = restaurar_backup(app.config, args.arquivo)
                print(f"Backup restaurado. Os dados anteriores foram guardados em: {guardados}")

        except (ErroUsuario, ValueError, FileNotFoundError) as erro:
            sys.exit(f"Erro: {erro}")


if __name__ == "__main__":
    main()
