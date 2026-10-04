"""Acesso ao banco SQLite e migrações do esquema."""

import os
import re
import sqlite3
import unicodedata

from flask import current_app, g

# Cada item é uma versão do banco. Para mudar o esquema, ADICIONE um novo
# item no fim da lista (nunca altere os anteriores): bancos já instalados
# rodam só as migrações que ainda não têm.
MIGRACOES = [
    # 1 - estrutura inicial
    """
    CREATE TABLE usuarios (
        id            INTEGER PRIMARY KEY,
        usuario       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        senha_hash    TEXT    NOT NULL,
        papel         TEXT    NOT NULL CHECK (papel IN ('admin', 'editor')),
        token_sessao  TEXT    NOT NULL,
        criado_em     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE propagandas (
        id         INTEGER PRIMARY KEY,
        nome       TEXT    NOT NULL,
        arquivo    TEXT    NOT NULL UNIQUE,
        tipo       TEXT    NOT NULL CHECK (tipo IN ('imagem', 'video')),
        duracao    INTEGER NOT NULL CHECK (duracao BETWEEN 1 AND 3600),
        ativo      INTEGER NOT NULL DEFAULT 1,
        inicio     TEXT,
        fim        TEXT,
        posicao    INTEGER NOT NULL,
        criado_em  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE configuracoes (
        chave  TEXT PRIMARY KEY,
        valor  TEXT NOT NULL
    );
    """,
    # 2 - telas, grupos, agendamento e relatório de exibições
    """
    CREATE TABLE grupos (
        id    INTEGER PRIMARY KEY,
        nome  TEXT NOT NULL UNIQUE COLLATE NOCASE
    );

    CREATE TABLE telas (
        id              INTEGER PRIMARY KEY,
        nome            TEXT    NOT NULL,
        codigo          TEXT    NOT NULL UNIQUE,
        grupo_id        INTEGER REFERENCES grupos(id) ON DELETE SET NULL,
        letreiro        TEXT,                 -- NULL = usa o letreiro geral
        ultimo_contato  TEXT,                 -- UTC
        ultimo_ip       TEXT,
        navegador       TEXT,
        exibindo        TEXT,
        alerta_offline  INTEGER NOT NULL DEFAULT 0,
        criado_em       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    ALTER TABLE propagandas ADD COLUMN dias_semana TEXT NOT NULL DEFAULT '0123456';
    ALTER TABLE propagandas ADD COLUMN hora_inicio TEXT;
    ALTER TABLE propagandas ADD COLUMN hora_fim TEXT;
    ALTER TABLE propagandas ADD COLUMN para_todas INTEGER NOT NULL DEFAULT 1;

    CREATE TABLE propaganda_destinos (
        propaganda_id  INTEGER NOT NULL REFERENCES propagandas(id) ON DELETE CASCADE,
        tela_id        INTEGER REFERENCES telas(id) ON DELETE CASCADE,
        grupo_id       INTEGER REFERENCES grupos(id) ON DELETE CASCADE,
        CHECK ((tela_id IS NULL) <> (grupo_id IS NULL))
    );
    CREATE INDEX destinos_propaganda ON propaganda_destinos(propaganda_id);

    -- Sem chave estrangeira para propaganda: o relatório continua valendo
    -- mesmo depois que a propaganda é excluída.
    CREATE TABLE exibicoes (
        id               INTEGER PRIMARY KEY,
        tela_id          INTEGER REFERENCES telas(id) ON DELETE SET NULL,
        propaganda_id    INTEGER NOT NULL,
        propaganda_nome  TEXT    NOT NULL,
        exibido_em       TEXT    NOT NULL,    -- UTC
        duracao          REAL    NOT NULL,
        UNIQUE (tela_id, propaganda_id, exibido_em)
    );
    CREATE INDEX exibicoes_data ON exibicoes(exibido_em);
    """,
    # 3 - multiempresa (cada cliente vê só os próprios dados), 2FA e limites de plano.
    # As tabelas são reconstruídas para que empresa_id seja obrigatório e SEM valor
    # padrão: esquecer a empresa num INSERT dá erro em vez de vazar dados.
    """
    CREATE TABLE empresas (
        id                 INTEGER PRIMARY KEY,
        nome               TEXT    NOT NULL,
        ativa              INTEGER NOT NULL DEFAULT 1,
        limite_telas       INTEGER,            -- NULL = sem limite
        limite_mb          INTEGER,            -- armazenamento; NULL = sem limite
        alerta_emails      TEXT    NOT NULL DEFAULT '',
        alerta_webhook     TEXT    NOT NULL DEFAULT '',
        criado_em          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO empresas (id, nome) VALUES (1, 'Minha empresa');

    CREATE TABLE usuarios_novo (
        id            INTEGER PRIMARY KEY,
        empresa_id    INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        usuario       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        senha_hash    TEXT    NOT NULL,
        papel         TEXT    NOT NULL CHECK (papel IN ('admin', 'editor')),
        plataforma    INTEGER NOT NULL DEFAULT 0,   -- administra todas as empresas
        token_sessao  TEXT    NOT NULL,
        totp_segredo  TEXT,                          -- NULL = 2FA desligada
        totp_pendente TEXT,                          -- segredo gerado, aguardando confirmação
        totp_ultimo   INTEGER NOT NULL DEFAULT 0,    -- impede reuso do mesmo código
        criado_em     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    -- Quem já administrava o sistema vira administrador da plataforma.
    INSERT INTO usuarios_novo (id, empresa_id, usuario, senha_hash, papel, plataforma, token_sessao, criado_em)
        SELECT id, 1, usuario, senha_hash, papel, papel = 'admin', token_sessao, criado_em FROM usuarios;
    DROP TABLE usuarios;
    ALTER TABLE usuarios_novo RENAME TO usuarios;
    CREATE INDEX usuarios_empresa ON usuarios(empresa_id);

    CREATE TABLE grupos_novo (
        id          INTEGER PRIMARY KEY,
        empresa_id  INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        nome        TEXT    NOT NULL COLLATE NOCASE,
        UNIQUE (empresa_id, nome)
    );
    INSERT INTO grupos_novo (id, empresa_id, nome) SELECT id, 1, nome FROM grupos;
    DROP TABLE grupos;
    ALTER TABLE grupos_novo RENAME TO grupos;

    CREATE TABLE telas_novo (
        id              INTEGER PRIMARY KEY,
        empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        nome            TEXT    NOT NULL,
        codigo          TEXT    NOT NULL UNIQUE,
        grupo_id        INTEGER REFERENCES grupos(id) ON DELETE SET NULL,
        letreiro        TEXT,
        ultimo_contato  TEXT,
        ultimo_ip       TEXT,
        navegador       TEXT,
        exibindo        TEXT,
        alerta_offline  INTEGER NOT NULL DEFAULT 0,
        criado_em       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO telas_novo
        SELECT id, 1, nome, codigo, grupo_id, letreiro, ultimo_contato, ultimo_ip, navegador,
               exibindo, alerta_offline, criado_em
        FROM telas;
    DROP TABLE telas;
    ALTER TABLE telas_novo RENAME TO telas;
    CREATE INDEX telas_empresa ON telas(empresa_id);

    CREATE TABLE propagandas_novo (
        id           INTEGER PRIMARY KEY,
        empresa_id   INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        nome         TEXT    NOT NULL,
        arquivo      TEXT    NOT NULL UNIQUE,
        tipo         TEXT    NOT NULL CHECK (tipo IN ('imagem', 'video')),
        tamanho      INTEGER NOT NULL DEFAULT 0,   -- bytes, para o limite de armazenamento
        duracao      INTEGER NOT NULL CHECK (duracao BETWEEN 1 AND 3600),
        ativo        INTEGER NOT NULL DEFAULT 1,
        inicio       TEXT,
        fim          TEXT,
        dias_semana  TEXT    NOT NULL DEFAULT '0123456',
        hora_inicio  TEXT,
        hora_fim     TEXT,
        para_todas   INTEGER NOT NULL DEFAULT 1,
        posicao      INTEGER NOT NULL,
        criado_em    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO propagandas_novo (id, empresa_id, nome, arquivo, tipo, duracao, ativo, inicio, fim,
                                  dias_semana, hora_inicio, hora_fim, para_todas, posicao, criado_em)
        SELECT id, 1, nome, arquivo, tipo, duracao, ativo, inicio, fim,
               dias_semana, hora_inicio, hora_fim, para_todas, posicao, criado_em
        FROM propagandas;
    DROP TABLE propagandas;
    ALTER TABLE propagandas_novo RENAME TO propagandas;
    CREATE INDEX propagandas_empresa ON propagandas(empresa_id, posicao);

    CREATE TABLE configuracoes_novo (
        empresa_id  INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        chave       TEXT    NOT NULL,
        valor       TEXT    NOT NULL,
        PRIMARY KEY (empresa_id, chave)
    );
    INSERT INTO configuracoes_novo SELECT 1, chave, valor FROM configuracoes;
    DROP TABLE configuracoes;
    ALTER TABLE configuracoes_novo RENAME TO configuracoes;

    CREATE TABLE exibicoes_novo (
        id               INTEGER PRIMARY KEY,
        empresa_id       INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        tela_id          INTEGER REFERENCES telas(id) ON DELETE SET NULL,
        propaganda_id    INTEGER NOT NULL,
        propaganda_nome  TEXT    NOT NULL,
        exibido_em       TEXT    NOT NULL,
        duracao          REAL    NOT NULL,
        UNIQUE (tela_id, propaganda_id, exibido_em)
    );
    INSERT INTO exibicoes_novo
        SELECT id, 1, tela_id, propaganda_id, propaganda_nome, exibido_em, duracao FROM exibicoes;
    DROP TABLE exibicoes;
    ALTER TABLE exibicoes_novo RENAME TO exibicoes;
    CREATE INDEX exibicoes_empresa_data ON exibicoes(empresa_id, exibido_em);
    CREATE INDEX exibicoes_data ON exibicoes(exibido_em);
    """,
    # 4 - planos com preço e cobrança automática (Asaas)
    """
    CREATE TABLE planos (
        id              INTEGER PRIMARY KEY,
        nome            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        preco_centavos  INTEGER NOT NULL CHECK (preco_centavos >= 0),
        limite_telas    INTEGER,
        limite_mb       INTEGER,
        ativo           INTEGER NOT NULL DEFAULT 1
    );

    ALTER TABLE empresas ADD COLUMN plano_id INTEGER REFERENCES planos(id) ON DELETE SET NULL;
    ALTER TABLE empresas ADD COLUMN documento TEXT NOT NULL DEFAULT '';        -- CPF ou CNPJ (só números)
    ALTER TABLE empresas ADD COLUMN email_cobranca TEXT NOT NULL DEFAULT '';
    ALTER TABLE empresas ADD COLUMN motivo_suspensao TEXT;                     -- 'manual' ou 'inadimplencia'
    ALTER TABLE empresas ADD COLUMN cobranca_automatica INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE empresas ADD COLUMN asaas_cliente_id TEXT;
    ALTER TABLE empresas ADD COLUMN asaas_assinatura_id TEXT;
    UPDATE empresas SET motivo_suspensao = 'manual' WHERE ativa = 0;
    CREATE UNIQUE INDEX empresas_assinatura ON empresas(asaas_assinatura_id) WHERE asaas_assinatura_id IS NOT NULL;

    CREATE TABLE faturas (
        id              INTEGER PRIMARY KEY,
        empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        asaas_id        TEXT    NOT NULL UNIQUE,
        valor_centavos  INTEGER NOT NULL,
        vencimento      TEXT    NOT NULL,           -- AAAA-MM-DD
        status          TEXT    NOT NULL,           -- status do Asaas (PENDING, RECEIVED, OVERDUE...)
        link            TEXT,                       -- página de pagamento (PIX, boleto ou cartão)
        pago_em         TEXT,
        atualizado_em   TEXT    NOT NULL
    );
    CREATE INDEX faturas_empresa ON faturas(empresa_id, vencimento);

    -- Eventos de webhook já processados (o Asaas pode reenviar o mesmo evento).
    CREATE TABLE webhook_eventos (
        id           TEXT PRIMARY KEY,
        recebido_em  TEXT NOT NULL
    );
    """,
    # 5 - plataforma de assinatura: módulos (Painel e Comanda) por plano, código da loja
    # e usuários por empresa. O nome de usuário passa a ser único só dentro da empresa
    # (cada loja tem os seus: "joao" pode existir em várias), com papéis da Comanda.
    """
    ALTER TABLE planos ADD COLUMN modulos TEXT NOT NULL DEFAULT 'painel';   -- ex.: 'painel,comanda'
    ALTER TABLE planos ADD COLUMN descricao TEXT NOT NULL DEFAULT '';

    ALTER TABLE empresas ADD COLUMN slug TEXT;                               -- código da loja (login e endereço)
    ALTER TABLE empresas ADD COLUMN modulos_liberados TEXT NOT NULL DEFAULT ''; -- liberados à mão pela plataforma
    -- Quem já usava o sistema continua com o Painel, mesmo sem plano.
    UPDATE empresas SET modulos_liberados = 'painel';
    CREATE UNIQUE INDEX empresas_slug ON empresas(slug) WHERE slug IS NOT NULL;

    CREATE TABLE usuarios_novo (
        id            INTEGER PRIMARY KEY,
        empresa_id    INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        usuario       TEXT    NOT NULL COLLATE NOCASE,
        senha_hash    TEXT    NOT NULL,
        papel         TEXT    NOT NULL CHECK (papel IN ('admin', 'editor', 'caixa', 'garcom', 'cozinha')),
        plataforma    INTEGER NOT NULL DEFAULT 0,
        token_sessao  TEXT    NOT NULL,
        totp_segredo  TEXT,
        totp_pendente TEXT,
        totp_ultimo   INTEGER NOT NULL DEFAULT 0,
        criado_em     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (empresa_id, usuario)
    );
    INSERT INTO usuarios_novo SELECT id, empresa_id, usuario, senha_hash, papel, plataforma, token_sessao,
                                     totp_segredo, totp_pendente, totp_ultimo, criado_em FROM usuarios;
    DROP TABLE usuarios;
    ALTER TABLE usuarios_novo RENAME TO usuarios;
    CREATE INDEX usuarios_empresa ON usuarios(empresa_id);
    CREATE INDEX usuarios_nome ON usuarios(usuario);
    """,
    # 6 - Comanda (módulo de pedidos): cada tabela tem a empresa dona, e toda consulta filtra por ela.
    # Dinheiro em centavos (inteiros) e datas em UTC.
    """
    CREATE TABLE cmd_categorias (
        id          INTEGER PRIMARY KEY,
        empresa_id  INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        nome        TEXT    NOT NULL COLLATE NOCASE,
        posicao     INTEGER NOT NULL DEFAULT 0,
        UNIQUE (empresa_id, nome)
    );

    CREATE TABLE cmd_produtos (
        id              INTEGER PRIMARY KEY,
        empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        categoria_id    INTEGER REFERENCES cmd_categorias(id) ON DELETE SET NULL,
        codigo          TEXT,                         -- atalho opcional para lançar rápido
        nome            TEXT    NOT NULL,
        preco_centavos  INTEGER NOT NULL CHECK (preco_centavos >= 0),
        vai_cozinha     INTEGER NOT NULL DEFAULT 1,   -- 0 = sai pronto (ex.: refrigerante em lata)
        ativo           INTEGER NOT NULL DEFAULT 1,
        criado_em       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (empresa_id, codigo)
    );
    CREATE INDEX cmd_produtos_empresa ON cmd_produtos(empresa_id);

    CREATE TABLE cmd_comandas (
        id                  INTEGER PRIMARY KEY,
        empresa_id          INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        numero              INTEGER NOT NULL CHECK (numero > 0),
        mesa                TEXT,
        cliente             TEXT,
        status              TEXT    NOT NULL DEFAULT 'aberta' CHECK (status IN ('aberta', 'fechada', 'cancelada')),
        cobrar_taxa         INTEGER NOT NULL DEFAULT 1,
        taxa_percentual     REAL    NOT NULL DEFAULT 0,
        desconto_centavos   INTEGER NOT NULL DEFAULT 0 CHECK (desconto_centavos >= 0),
        total_centavos      INTEGER,
        aberta_por          INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        aberta_em           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fechada_por         INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        fechada_em          TEXT,
        motivo_cancelamento TEXT
    );
    -- Um número só fica aberto uma vez por loja (o cartão volta a ser usado depois de fechado).
    CREATE UNIQUE INDEX cmd_comandas_numero_aberta ON cmd_comandas(empresa_id, numero) WHERE status = 'aberta';
    CREATE INDEX cmd_comandas_fechada_em ON cmd_comandas(empresa_id, fechada_em);

    CREATE TABLE cmd_itens (
        id                  INTEGER PRIMARY KEY,
        empresa_id          INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        comanda_id          INTEGER NOT NULL REFERENCES cmd_comandas(id) ON DELETE CASCADE,
        produto_id          INTEGER REFERENCES cmd_produtos(id) ON DELETE SET NULL,
        nome                TEXT    NOT NULL,          -- cópia: o cardápio pode mudar depois
        preco_centavos      INTEGER NOT NULL,
        quantidade          INTEGER NOT NULL CHECK (quantidade BETWEEN 1 AND 999),
        observacao          TEXT,
        vai_cozinha         INTEGER NOT NULL,
        status              TEXT    NOT NULL CHECK (status IN ('pendente', 'preparando', 'pronto', 'entregue', 'cancelado')),
        lancado_por         INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        lancado_em          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        atualizado_em       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        cancelado_por       INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        motivo_cancelamento TEXT
    );
    CREATE INDEX cmd_itens_comanda ON cmd_itens(comanda_id);
    CREATE INDEX cmd_itens_empresa_status ON cmd_itens(empresa_id, status);

    CREATE TABLE cmd_pagamentos (
        id                INTEGER PRIMARY KEY,
        empresa_id        INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        comanda_id        INTEGER NOT NULL REFERENCES cmd_comandas(id) ON DELETE CASCADE,
        forma             TEXT    NOT NULL CHECK (forma IN ('dinheiro', 'pix', 'debito', 'credito', 'outro')),
        valor_centavos    INTEGER NOT NULL CHECK (valor_centavos > 0),
        recebido_centavos INTEGER NOT NULL,
        registrado_por    INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        registrado_em     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX cmd_pagamentos_comanda ON cmd_pagamentos(comanda_id);

    CREATE TABLE cmd_auditoria (
        id          INTEGER PRIMARY KEY,
        empresa_id  INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        quando      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        usuario_id  INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        comanda_id  INTEGER REFERENCES cmd_comandas(id) ON DELETE SET NULL,
        acao        TEXT    NOT NULL,
        detalhe     TEXT
    );
    """,
    # 7 - Comanda: o administrador pode autorizar um garçom a fechar contas.
    """
    ALTER TABLE usuarios ADD COLUMN fecha_conta INTEGER NOT NULL DEFAULT 0;
    """,
    # 8 - letreiro próprio por propaganda: NULL = usa o geral (ou o da tela), '' = sem letreiro.
    """
    ALTER TABLE propagandas ADD COLUMN letreiro TEXT;
    """,
    # 9 - controle de ponto: horário de trabalho por usuário e registros de entrada e saída.
    # O nome do usuário é copiado no registro: o histórico continua se o usuário for excluído.
    """
    ALTER TABLE usuarios ADD COLUMN exige_ponto INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE usuarios ADD COLUMN horario_dias TEXT NOT NULL DEFAULT '0123456';
    ALTER TABLE usuarios ADD COLUMN horario_inicio TEXT;
    ALTER TABLE usuarios ADD COLUMN horario_fim TEXT;

    CREATE TABLE ponto_registros (
        id             INTEGER PRIMARY KEY,
        empresa_id     INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        usuario_id     INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        usuario_nome   TEXT    NOT NULL,
        entrada        TEXT    NOT NULL,
        saida          TEXT,
        motivo_saida   TEXT,
        encerrado_por  INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        ip             TEXT
    );
    -- Cada pessoa tem no máximo um ponto aberto.
    CREATE UNIQUE INDEX ponto_um_aberto ON ponto_registros(usuario_id) WHERE saida IS NULL;
    CREATE INDEX ponto_empresa_entrada ON ponto_registros(empresa_id, entrada);
    """,
    # 10 - pareamento: cada tela funciona só no aparelho conectado a ela (pelo QR code da
    # página /tela). Guarda o hash do "crachá" (cookie secreto) do aparelho.
    # aceita_link = 1 só nas telas que já existiam: a TV que já usa o endereço se conecta
    # sozinha no próximo contato, uma vez. Telas novas só se conectam pelo QR code.
    """
    ALTER TABLE telas ADD COLUMN aparelho_hash TEXT;
    ALTER TABLE telas ADD COLUMN pareada_em TEXT;
    ALTER TABLE telas ADD COLUMN aceita_link INTEGER NOT NULL DEFAULT 1;

    -- Pedido de conexão feito por uma TV na página /tela: o código curto vai no QR code;
    -- o segredo fica só no cookie da TV (aqui, só o hash).
    CREATE TABLE pareamentos (
        id            INTEGER PRIMARY KEY,
        codigo        TEXT    NOT NULL UNIQUE,
        segredo_hash  TEXT    NOT NULL UNIQUE,
        criado_em     TEXT    NOT NULL,
        tela_id       INTEGER REFERENCES telas(id) ON DELETE CASCADE
    );
    """,
    # 11 - a TV avisa quando a janela é fechada: fica offline na hora, sem esperar os 3 minutos.
    """
    ALTER TABLE telas ADD COLUMN fechada_em TEXT;
    """,
    # 12 - Comanda: a taxa de serviço exata do cupom fica gravada ao fechar (os relatórios usam ela).
    """
    ALTER TABLE cmd_comandas ADD COLUMN taxa_centavos INTEGER;
    """,
]


def conectar(caminho):
    conexao = sqlite3.connect(caminho, timeout=15)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def migrar(caminho):
    conexao = conectar(caminho)
    try:
        # WAL permite ler (TVs) enquanto alguém grava (painel).
        conexao.execute("PRAGMA journal_mode = WAL")
        versao = conexao.execute("PRAGMA user_version").fetchone()[0]
        if versao >= len(MIGRACOES):
            return
        # Reconstruir tabelas exige desligar as chaves estrangeiras durante a
        # migração (procedimento oficial do SQLite); a integridade é conferida no fim.
        conexao.execute("PRAGMA foreign_keys = OFF")
        for numero in range(versao + 1, len(MIGRACOES) + 1):
            conexao.executescript(
                f"BEGIN;\n{MIGRACOES[numero - 1]}\nPRAGMA user_version = {numero};\nCOMMIT;"
            )
        problemas = conexao.execute("PRAGMA foreign_key_check").fetchall()
        if problemas:
            raise RuntimeError(f"Migração deixou referências inválidas: {[tuple(p) for p in problemas]}")
        conexao.execute("PRAGMA foreign_keys = ON")
    finally:
        conexao.close()


def obter():
    """Conexão do pedido atual (uma por requisição)."""
    if "db" not in g:
        g.db = conectar(current_app.config["BANCO"])
    return g.db


def fechar(_erro=None):
    conexao = g.pop("db", None)
    if conexao is not None:
        conexao.close()


def ler_config(empresa_id, chave, padrao=""):
    linha = obter().execute(
        "SELECT valor FROM configuracoes WHERE empresa_id = ? AND chave = ?", (empresa_id, chave)
    ).fetchone()
    return linha["valor"] if linha else padrao


def gravar_config(empresa_id, chave, valor):
    conexao = obter()
    with conexao:
        conexao.execute(
            "INSERT INTO configuracoes (empresa_id, chave, valor) VALUES (?, ?, ?) "
            "ON CONFLICT(empresa_id, chave) DO UPDATE SET valor = excluded.valor",
            (empresa_id, chave, valor),
        )


def gerar_slug(conexao, nome, ignorar_id=None):
    """Código da loja a partir do nome: "Padeiro Lanches" → "padeiro-lanches" (único)."""
    base = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:40].strip("-") or "loja"
    candidato, numero = base, 2
    while conexao.execute(
        "SELECT 1 FROM empresas WHERE slug = ? AND id IS NOT ?", (candidato, ignorar_id)
    ).fetchone():
        candidato, numero = f"{base}-{numero}", numero + 1
    return candidato


def preencher_slugs(caminho_banco):
    """Dá um código de loja às empresas criadas antes da versão com cadastro."""
    conexao = conectar(caminho_banco)
    try:
        with conexao:
            for linha in conexao.execute("SELECT id, nome FROM empresas WHERE slug IS NULL ORDER BY id").fetchall():
                conexao.execute("UPDATE empresas SET slug = ? WHERE id = ?", (gerar_slug(conexao, linha["nome"], linha["id"]), linha["id"]))
    finally:
        conexao.close()


def preencher_tamanhos(caminho_banco, pasta_midia):
    """Calcula o tamanho dos arquivos enviados antes da versão com limite de armazenamento."""
    conexao = conectar(caminho_banco)
    try:
        pendentes = conexao.execute("SELECT id, arquivo FROM propagandas WHERE tamanho = 0").fetchall()
        with conexao:
            for linha in pendentes:
                caminho = os.path.join(pasta_midia, linha["arquivo"])
                if os.path.exists(caminho):
                    conexao.execute(
                        "UPDATE propagandas SET tamanho = ? WHERE id = ?", (os.path.getsize(caminho), linha["id"])
                    )
    finally:
        conexao.close()
