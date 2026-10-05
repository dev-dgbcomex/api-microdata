# Plano de implementação (do design à troca da API legada)

> **Data:** 19/set/2026
> **Situação:** conclusão da **Fase A (design)** — estudos 03–42 + Docs 43/44. Este documento
> detalha **tudo o que vamos fazer** dali em diante: decisões pendentes, tarefas por fase,
> estrutura de código, ordem de execução e critérios de aceite.
> **Não é design congelado:** será corrigido à medida que a implementação levantar fatos novos
> (como nos estudos).

---

## 1. Onde estamos e para onde vamos

Concluído (Fase A):
- Mapa de módulos do ERP (estudos 03–42) e arquitetura-alvo ([Doc 43](./43-arquitetura-alvo-api.md)).
- Contratos da API legada → Neon ([Doc 44](./44-contratos-api-oraculum.md)) e plano de dados/ETL
  ([Estudo 22](../estudo/22-arquitetura-neon-etl.md)).

A partir daqui, **na ordem**, faremos:

```
B. Scaffold do projeto (app/)      → código base + schemas (Postgres local + Neon)
C. ETL: bootstrap raw + incremental → warehouse no Postgres LOCAL
D. core/marts (regras de negócio)  → o "DBProDash" portado (Postgres local)
E. Endpoints da API lendo local     → substitui o oraculum (14 contratos); KPIs → Neon on-demand
F. Cutover e descomissionamento   → dgbcomex usa Neon (marts da API), DBProDash fora
```

Regra transversal (manter sempre): **nunca** escrever no `DBMicrodata_DGB`; credenciais só em
`.env` gitignored; **Postgres local = warehouse completo** (raw/core/marts/etl); **Neon `dgbcomex`
recebe apenas agregados pequenos de dashboard** (disparados pela API on-demand); o **`public` do
Neon é do app dgbcomex** (drizzle) — não alterado pelo api-microdata, que mantém schemas próprios
no Neon; mudanças de marts são aditivas e versionadas.

---

## 2. Decisões pendentes (confirmar antes de codar)

| # | Decisão | Opções | Recomendação | Bloqueia |
|---|---------|--------|--------------|----------|
| D1 | **Neon**: criar projeto/database/branch | Branches por fase (dev/prod) | ✔ **resolvida**: Neon `dgbcomex` já criado e migrado (118 tabelas em `public`) | B,C |
| D2 | **Credenciais** do Neon | `DATABASE_URL` no `.env` local (gitignored) | ✔ **resolvida**: `DATABASE_URL` (Neon) + `DATABASE_URL_LOCAL` (Postgres local) no `app/.env` | B |
| D2b | **Postgres local (warehouse)** | nativo / Docker / Neon | ✔ **resolvida**: PostgreSQL 17 **nativo** nesta máquina (`localhost:5432`), database `dgbcomex_warehouse` criado | B,C |
| D3 | **Runner do ETL** | VM on-prem (mesma rede do ERP) / máquina dev / CI | máquina dev nesta fase (warehouse está aqui); VM separada quando for p/ produção | C |
| D4 | **Frequência do ETL** | diária / horária / on-demand | **dupla**: ETL pesado diário no **Postgres local**; **sync de KPIs p/ Neon é on-demand** (no request, quando defasado) | C,D,E |
| D5 | **Auth do alvo** | reusar tabela `usuario` (senha em **texto claro** — ruim) vs auth nova JWT+bcrypt | **auth nova** (JWT+bcrypt), escopos derivados de `Usuario_Acessos` | E |
| D6 | **Multiempresa** | `Codigo_Empresas` (`char(2)`) imbutido por token vs header | empresa do token (padrão `SIS_UsuarioEmpresa`); suportar `?empresa=13` p/ dev | E |
| D7 | **`/contas-pagas`** | reabrir como resumo vs lista vs manter `{}` | **resumo** `{ValorPago, QtdeBaixas}` do mês + lista paginada | E |
| D8 | **PDF** | reportlab (mantém) vs weasyprint | reportlab (mesmo layout, zero refactor) | E |

> Toda decisão tem impacto documentável: confirmar com o usuário **antes** de iniciar a fase que
> ela bloqueia (coluna "Bloqueia"). As recomendações são o default a adotar se não houver objeção.

---

## 3. Fase B — Scaffold do projeto (`app/`)

Objetivo: esqueleto executável com schemas criados no **Postgres local** (warehouse, vazio) e
schemas próprios da API no Neon (marts pequenos) + convenções prontas.

### 3.1 Estrutura de código

```
app/
├─ pyproject.toml              # deps: fastapi, uvicorn, pydantic(-settings), pyodbc,
│                              #       psycopg[binary], SQLAlchemy, alembic, reportlab,
│                              #       bcrypt, PyJWT, python-dateutil, python-dotenv
├─ alembic/                    # migrations: schemas do Postgres local (raw/core/marts/etl)
│  └─ env.py                   # + migrations dos schemas próprios no Neon (marts/etl da API)
├─ .env                        # gitignored (DB_* do ERP + DATABASE_URL Neon + DATABASE_URL_LOCAL)
├─ src/
│  ├─ config.py                # pydantic-settings; carrega .env
│  ├─ db/
│  │  ├─ erp.py                # pyodbc → DBMicrodata_DGB (somente leitura)
│  │  └─ warehouse.py          # engine SQLAlchemy + pooling Postgres LOCAL
│  │  └─ neon.py               # engine SQLAlchemy + pooling Neon (schemas próprios da API)
│  ├─ etl/
│  │  ├─ bootstrap.py          # carga full em ordem de dependência → Postgres local
│  │  ├─ incremental.py        # por watermark / por documento-pai
│  │  ├─ reconcile.py          # reconciliação (baixas/exclusões sem flag)
│  │  ├─ extract/              # 1 módulo por domínio (faturamento, estoque, financeiro…)
│  │  ├─ transform/            # trim de char, tipos, datas, timezone
│  │  └─ load/                 # upsert no Postgres local (chave natural do ERP)
│  ├─ sync/                    # sync ON-DEMAND de KPIs/agregados pequenos → Neon
│  │  └─ dashboards.py         # re-gera no Neon só os marts pequenos sob demanda
│  └─ api/
│     ├─ main.py               # FastAPI; CORS restrito; docs OpenAPI
│     ├─ auth/                 # JWT + bcrypt + escopos (D5)
│     ├─ routers/              # 1 módulo por grupo de rotas (dashboard, estoque, pdf)
│     ├─ schemas/              # Pydantic = contrato (mesmo JSON do legado, tipado)
│     └─ pdf/                  # sugestao_rolos → PDF (reportlab)
```

### 3.2 Migrations iniciais (alembic)

**Postgres local (`dgbcomex_warehouse`):**

| Migration | Cria |
|-----------|------|
| `0001_etl` | schema `etl` + `etl.watermark` (tabela, coluna, ultimo_valor, ultima_exec, status, linhas, levou_s) + `etl.execucoes` + `etl.erros` |
| `0002_raw` | schema `raw` **vazio** (as tabelas-fonte são geradas do catálogo do ERP, ver 3.3) |
| `0003_core` | schema `core` + regras (faturamento, contas pagas, financeiro programado, estoque em aberto) |
| `0004_marts` | schema `marts` + views de consumo (KPIs diários, estoque em aberto, sugestão de rolos) |

**Neon (schemas próprios da API, NÃO `public`):**

| Migration | Cria |
|-----------|------|
| `1001_neon_marts` | schema `marts` (API) + `etl` (API) para os **agregados pequenos** de dashboard |

Critério de aceite B: `alembic upgrade head` sobe os 4 schemas no **Postgres local** + schemas
próprios no Neon (`public` intocado); `/health` responde lendo o local; ETL conecta no ERP (read-only).

### 3.3 Decisões tomadas na Fase B (verificadas no ERP em 02/out/2026)

1. **`raw` é gerado do catálogo do ERP, não escrito à mão.** As 46 fontes do registry somam ~2.400 colunas;
   `python -m src.cli introspect --apply` lê `sys.columns`/`sys.types` e cria as tabelas com
   nomes snake_case, tipos traduzidos e `COMMENT ON` citando a coluna de origem. Novas colunas no
   ERP entram por `ALTER TABLE ... ADD COLUMN` (aditivo, sem dropar). `--recriar` recria o schema
   quando é preciso corrigir tipos gerados.
2. **Tipos:** `char/varchar` → `varchar(n)`, `nvarchar` → `varchar(n/2)` (2 bytes por caractere no
   SQL Server), `decimal` → `numeric(p,s)`, `smalldatetime/datetime` → `timestamp`, `money` →
   `numeric(19,4)`, `uniqueidentifier` → `uuid`, `xml/text` → `text`. Tipos-alias do ERP
   (`Pagto`, `Codigo`…) usam o comprimento do **alias**, não o do tipo do sistema.
3. **Watermarks: 5 das 12 declaradas no estudo não existem de fato.** Conferido no ERP:

   | Fonte | Watermark do estudo | Realidade | Estratégia adotada |
   |-------|--------------------|-----------|--------------------|
   | `Clientes_Principal` | `Ult_Atualizacao` | 100% NULL (`ultima_atualizacao_dt` = 8%) | `full` |
   | `Fat_Pedido` | `Ult_Atualizacao` | 100% NULL (`dh_Emissao` = 97%, mas é emissão, não alteração) | `full` |
   | `Fat_Itens_Pedido` | `Ult_Atualizacao` | 100% NULL | `recarrega_pai` (Fat_Pedido) |
   | `Car_Pedido` | `DataHora_Alteracao` | 0,16% preenchido, max de 2018 | `full` |
   | `Car_Itens_Pedido` | `DtEntrega` | 100% NULL | `recarrega_pai` (Car_Pedido) |

   `python -m src.cli check` reprova (exit 1) qualquer fonte `watermark` com cobertura < 95% ou
   `max()` nulo, e também qualquer filho `recarrega_pai` sem as colunas da chave do pai.
4. **Carga parcial nunca grava watermark.** `--limite` marca a execução como `parcial` em
   `etl.execucoes` e não grava `etl.watermark` (gravar o máximo global após carga parcial pularia
   linhas na execução seguinte).
5. **`Fat_Parc_Pedido` tem a chave do pai com nomes próprios** (`Empresa_Parcelas`,
   `Documento_Parcelas`), declarado em `Fonte.chave_pai` para a recarga por documento funcionar.

**Estado da Fase B (conferido):** 37 tabelas em `raw` (2.166 colunas), `alembic_version` =
`1001_neon_marts`, `/health` = 200 nos três destinos e prova de conceito de carga com contagem
idêntica ao ERP (`Condicoes_Pagto` 364, `Fat_Pedido` 12.746, `Fat_Itens_Pedido` 101.113,
`Fat_Parc_Pedido` 30.102).

---

## 4. Fase C — ETL: bootstrap `raw` + incremental (**no Postgres local**)

Objetivo: dados das fontes dos contratos no `raw` (sem regra) do **warehouse local**, confiáveis e idempotentes.

### 4.1 Tabelas-fonte por domínio (contratos da Doc 44)

| Domínio | Tabelas-fonte (ERP) | Watermark/estratégia (referência) |
|---------|---------------------|-----------------------------------|
| Cadastros | `Clientes_Principal`, `Produtos`, `Produtos_Tecidos`, `Fornecedores` (view), `Rec_Vendedores`, `Condicoes_Pagto`, `Sistemas/Usuarios` (p/ auth) | `Ult_Atualizacao`/full — Estudo 22 §5 |
| Faturamento | `Fat_Pedido`, `Fat_Itens_Pedido`, `Fat_Nat_Pedido`, `Fat_Parc_Pedido` | conferir watermark (`Data_Nota`/`Ult_*`) — Estudo 29 |
| Contas a pagar | `NF_Entradas`, `NFE_Parcelas`, `Pag_Baixas`, `Pag_Historicos`, `Pag_Operacoes`, `Bancos` + rateios `NFE_CCustos_*`, `Pag_DespesasReceitas`, `Pag_CCusto_Departamento` | `Data_Hora`/`Data_Hora_Baixa`; itens sem data por pai — Estudo 31 |
| Contas a receber | `Notas_Fiscais_Rec`, `Notas_Fiscais_Parcelas`, `Rec_Baixas` | `Data_Hora`/`Vencimento` — Estudo 30 |
| Fiscal/CFOP (devoluções/estornos) | `Liv_Saidas`, `Liv_SaiProd`, `Liv_Entradas`, `Liv_EntProd` (mapa de CFOP de devolução) | `Data_Hora` por documento — Estudo 35 |
| Estoque de peças (dados + sugestão de rolos) | `Cte_Peca`, `CTE_Baixa`, `Vw_Car_Itens_Pedido` (pedido), `Car_Pedido`, `Car_Itens_Pedido` | `Alt_Data`/`Data_Hora`; peça em aberto = antijoin — Estudo 34 |

> Antes de implementar, **conferir a coluna watermark exata** de cada tabela nova (Fat_*, Pag_*,
> Rec_*, Liv_* já mapeados no Estudo 22; confirmar `Fat_*` no Estudo 29) — mesmo método dos estudos.
> **Já conferido em 02/out/2026**: ver 3.3 (5 watermarks do estudo não existem no ERP).

### 4.2 Ordem de carga (dependências)

1. Cadastros (`Clientes_Principal`, `Produtos`/`Produtos_Tecidos`, vendedores, cond. pagto, bancos).
2. Cabeçalhos (`Car_Pedido`, `Fat_Pedido`, `NF_Entradas`, `Notas_Fiscais_Rec`, `Liv_Saidas`/`Liv_Entradas`).
3. Itens/parcelas (`Fat_Itens_Pedido`, `NFE_Parcelas`, `Notas_Fiscais_Parcelas`, `Liv_SaiProd`/`Liv_EntProd`).
4. Baixas/logs (`Pag_Baixas`, `Rec_Baixas`, rateios, `CTE_Baixa`).
5. Estoque de peças (`Cte_Peca` — a maior, ~317k; full fora do expediente).

### 4.3 Regras de carga

- **Upsert** pela chave natural do ERP (após `trim` de `char`).
- **Incremental**: `WHERE watermark > etl.watermark.ultimo_valor`; **filhos sem data**: recarga por
  documento-pai (delete+insert do documento).
- **Idempotente/reprocessável**; registra em `etl.execucoes` (status, linhas, duração, erro).
- **Reconciliação** periódica (full leve) para baixas sem flag (`CTE_Baixa`, `Pag_Baixas`, `Rec_Baixas`).
- Timezone **`America/Sao_Paulo`** na gravação; datas `date`/`timestamp`.

Critério de aceite C: cada tabela do `raw` (local) com contagem batendo com a do ERP (amostras) e
`etl.watermark` (local) avançando nas execuções posteriores.

**Estado da Fase C (02/out/2026): bootstrap concluído e conferido.** As 37 fontes iniciais foram carregadas
por `python -m src.cli bootstrap --dominio <dominio>` na ordem de dependência (pai antes do filho),
todas com `status = ok` e contagem idêntica ao ERP:

| Domínio | Fontes | Linhas | Tempo |
|---------|--------|--------|-------|
| cadastros | 12 | ~23k | 9s |
| faturamento | 4 | 156.705 | 3min15 |
| contas_pagar | 5 | ~26k | 8s |
| contas_receber | 3 | 71.073 | 24s |
| fiscal | 4 | 68.935 | 1min19 |
| estoque | 4 | 640.171 | 4min18 |

Total: **37 tabelas, 2.166 colunas, 987.878 linhas, 353 MB** no Postgres local. `Cte_Peca`
(300.601) e `CTE_Baixa` (282.193) concentram o volume — ficam só no local, conforme o plano.

Extras implementados na Fase C:
- `src/etl/pipeline.py` (carga de uma fonte) + `src/etl/bootstrap.py` (ordem de dependência e
  conferência ERP x raw; sai com exit 1 se alguma tabela divergir).
- **Largura de coluna pelo dado**: o ERP legado estoura o tipo declarado
  (`Fornecedores.Inscricao_Municipal` é `char(7)` e grava 8). O `introspect` perfila o
  `max(len(coluna))` de cada coluna de texto e usa `varchar(max(declarado, observado))`
  (acima de 1.000 caracteres vira `text`).
- `linhas_erp` cai para `count_big(*)` quando a fonte é view (`sys.partitions` não tem linhas).
- **Pendência conhecida**: os filhos com `recarrega_pai` (`Liv_EntProd`, `Liv_SaiProd`,
  `Fat_Itens_Pedido`, `Fat_Parc_Pedido`…) são relidos inteiros a cada execução, mesmo quando o pai
  não mudou. Próximo passo: recarregar só os documentos cujo pai mudou.

---

## 5. Fase D — `core`/`marts` (regras portadas, **no Postgres local**)

Objetivo: reproduzir no Postgres local o que o `DBProDash` faz hoje, validado número a número.

Portar as regras (da Doc 44 §2.2 e Estudos 12/29/30/31):

| Mart/core | Regra a portar (fonte atual) |
|-----------|------------------------------|
| `core.estoque_pecas_em_aberto` | `Cte_Peca` antijoin `CTE_Baixa` (sem `Nro_Rolo_Origem`) = `VW_CTE_PECA_EM_ABERTO` |
| `core.pedido_sugestao_rolos` | janela `SUM(Metros) OVER (gaveta, tear DESC, rolo DESC)` até `Qtde_Saldo` (Vw_Car_Itens_Pedido) |
| `marts.faturamento_diario` | `vwFaturamento` → `SUM(Vr_Total)+SUM(Acres_Desc)` por `Data_Nota` (QMP `Base_Calc` P/M) |
| `marts.contas_pagas_diario` | `vwContasPagas` → baixas por `Data_Baixa` (**sem filtro** de empresa/tipo) |
| `marts.custos_por_departamento_mensal` | `vwContasPagasCentroCusto` → Σ `Valor_Baixado` por mês × `Codigo_Departamento` × `Codigo_Despesa` |
| `marts.devolucoes_diario` | `vwListagemDeEntradasSaidasPorCFOP` com CFOP de devolução (1.201/1.156/1.202/2.202) |
| `marts.estornos_diario` | `vwListagemDeEstornos` → `SUM(Vr_Nota)` por `Data_Emissao` |
| `core.financeiro_receber/pagar_programado` | `vwFinanceiroContasReceber/Pagar` → vencimento > fim do mês anterior |
| `marts` KPI-final | Faturamento, Desconto, Devolução, Estorno, Receber/Pagar programado, Custos admin + razão |

**Faturamento: portado e validado (02/out/2026).** `core.faturamento_itens` (rev `0005`) +
`marts.faturamento_diario`/`faturamento_mensal` (rev `0006`) reproduzem `vwFaturamento`:

- `vwFaturamento`, `vwContasPagas`, `vwListagemDeEstornos`, `vwFinanceiro*` e todas as `usp*` do
  dashboard estão com **`WITH ENCRYPTION`** (`OBJECT_DEFINITION(...) IS NULL`): dá para consultar,
  **não dá para ler o SQL**. A regra foi reconstituída pelos Estudos 11/12/29 + pela view irmã
  legível `DBMicrodata.dbo.Vw_Fat_Saida_Qlik`.
- **Whitelist de CFOP**: 104 pares `(NatOp, Seq)` em `core.cfop_faturamento`. Base = a whitelist da
  `Vw_Fat_Saida_Qlik` (106 pares) **menos `5.911/1` e `6.901/1`**, que a `vwFaturamento` não usa —
  diferença descoberta comparando item a item (174 itens / R$ 64.554,73).
- Regra: `Fat_Pedido ⋈ Fat_Itens_Pedido ⋈ Clientes_Principal ⋈ Fat_Nat_Pedido`, com
  `Flag_Emitido='1'`, `Tipo_Pedido='1'`, empresas `13/14` (`core.empresa_faturamento`),
  `Base_Calc <> '-'`, whitelist de CFOP; `Metros = CASE Base_Calc WHEN 'P'/'M' THEN Metros ELSE Qtde`.
- Validação (`python -m scripts.validar_fase_d`): **74/74 comparações iguais** ao `DBProDash` —
  32.663 itens, `Σ Vr_Total` R$ 133.792.323,99, `Σ Acres_Desc` −R$ 424.768,28, e os 13 meses +
  45 dias de faturamento/desconto. Cobre o critério de aceite D para esta fatia.

**Financeiro, estornos e devoluções: portados e validados (02/out/2026).** Revs `0007` e `0008`:

| Objeto | Regra portada | Conferência no `DBProDash` |
|--------|---------------|----------------------------|
| `core.pag_titulo_aberto` | `VW_Pag_Titulo_Aberto` (canônica legível): `NFE_Parcelas` LEFT JOIN baixas agrupadas por parcela `HAVING Σ líquido < valor` | 129 títulos / R$ 3.873.033,09 |
| `core.rec_duplicatas_em_aberto` | `VW_Rec_DuplicatasEmAberto` (canônica legível) | 1.019 duplicatas / R$ 3.956.192,84 |
| `marts.contas_pagas_diario`/`mensal` | `vwContasPagas` = `Pag_Baixas` sem filtro (8.652 baixas, Σ R$ 111.713.506,49) | igual em 13 meses |
| `marts.financeiro_{pagar,receber}_programado` | espelhos `vwFinanceiro*` (empresas `13`/`14`); a janela `vencimento >= 1º do mês seguinte e <= 2050-12-31` fica na API | títulos, valor e extremos de vencimento |
| `core.estornos_itens` + `marts.estornos_{diario,mensal}` | `vwListagemDeEstornos`: `Tipo_Pedido='4'`, `Flag_Emitido='1'`, `Empresa='13'`, `Base_Calc<>'-'`, natureza `1.102`/`2.102` seq 1 | 44 itens / 24 pedidos / R$ 287.994,78, 18 meses |
| `core.devolucoes_documentos` + `marts.devolucoes_{diario,mensal}` | `uspDevolucao`: `Vr_Contabil` das naturalezas `1.201-1`, `1.201-2`, `1.202-1`, `2.202-1`, por `Data_Entrada`/`Data_Saida` | 293 documentos / R$ 2.480.456,50, 54 meses |

- `vwFinanceiroContasPagar/Receber` = canônicas **sem filtro além das empresas 13/14** (122+7 e
  1017+2): o "programado" é a janela de vencimento aplicada pela procedure, não pela view.
- **Armadilha das devoluções:** `Liv_Entradas`/`Liv_Saidas` têm `Documento` **reaproveitado por
  fornecedores diferentes**. Sem `Tipo_Fornec`+`Fornecedor` na chave natural, a junção com
  `Liv_*NatOp` duplica linhas e troca valores entre meses (3 meses divergiam).
- Três fontes novas entraram no registry (`Liv_SaiNatOp`, `Liv_EntNatOp`, `Liv_Natureza`; 14.283
  linhas) porque `Vr_CONtabil` vive nas tabelas de natureza, não nos itens.
- Validação (`python -m scripts.validar_fase_d`): **148/148 comparações iguais** ao `DBProDash`
  (13 meses + 45 dias, nas 6 fatias: faturamento, faturamento diário, contas pagas, financeiro,
  estornos e devoluções).

**Estoque em aberto: portado e validado (02/out/2026).** Rev `0009`. A canônica
`DBMicrodata.dbo.VW_CTE_PECA_EM_ABERTO` é **legível**, então a regra veio pronta:

- `CTE_Peca` **antijoin** `CTE_Baixa` por `(Empresa, Nro_Rolo, Nro_Peca)`, com `INNER JOIN` em
  `Produtos` (por `EmpProd`+`Codigo`), `Car_Situacoes`, `Car_Cores`, `Car_Desenhos`,
  `Car_Categorias` e `LEFT JOIN` em `Car_Variante`.
- **Sem filtro de `Nro_Rolo_Origem`** (o plano antigo citava; a view não usa) — só o antijoin.
- 5 cadastros novos no registry (`Car_Cores`, `Car_Situacoes`, `Car_Desenhos`, `Car_Categorias`,
  `Car_Variante`; 82 linhas) porque os `INNER JOIN` descartam peças com cadastro ausente.
- Conferência: **18.408 peças**, Σ metros 1.328.342,55, Σ peso 335.934,4908 — igual à canônica
  **e** à `DBProDash.vwSaldoTecidosEstoqueDetalhado` (criptada, 18.408 linhas).

**Sugestão de rolos: portada e validada (02/out/2026).** Rev `0010`. A `uspEnderecamentoParaAtenderPedidoGeral`
do `DBProDash` é criptografada, mas a **procedure irmã legível** na base canônica
(`DBMicrodata.dbo`) é a mesma regra, então a portada segue dela:

- 3 views: `core.estoque_rolos_disponiveis` (peça com `Nro_Rolo_Origem IS NULL` e sem baixa por
  `Empresa+Situacao+Nro_Rolo+Nro_Peca`), `core.sugestao_rolos_acumulado`
  (`ROW_NUMBER`/`SUM ... ROWS UNBOUNDED PRECEDING` por `Produto+Cor`) e `core.pedido_sugestao_rolos`
  (uma linha por **item** do pedido, com `Sublote`, `Gavetas`, `Rolos`, `Qtde_Pecas`, `Total_Metros`).
- `Qtde_Saldo = Qtde - Qtde_Romaneio - Qtde_Acerto`; só itens com saldo positivo geram linha.
  `Vws_Car_Itens_Pedido` é `Car_Itens_Pedido` + `INNER JOIN Liv_Diario` (só 1 empresa no estoque, filtro inócuo).
- **Armadilha de ordenação:** no SQL Server `NULL` é o menor valor, então `Tear DESC` joga os rolos
  **sem tear para o fim**; no Postgres o padrão de `DESC` é `NULLS FIRST` — o oposto. A ordem correta é
  `tear DESC NULLS LAST`. Sem isso a sugestão começa pelo rolo errado (achado na validação: 2 itens
  de produto `000020`/`00002` divergentes em `Rolos` e `Total_Metros`).
- A procedure emite uma linha por item (cursor) e usa `TOP 1 Qtde` para exibição quando o mesmo
  `(Produto, Cor)` repete no pedido — arbitrário no legado; aqui é `min(qtde)` (4 combinações
  repetidas com saldo > 0).
- Conferência contra a mesma lógica escrita como `SELECT` sobre as tabelas legíveis do ERP:
  **571/571** (95 itens sugeridos em 69 pedidos, 1.243 peças, 83.715,10 m) e
  `core.estoque_rolos_disponiveis` = **18.384 peças em 98 combinações** (≠ 18.408 do estoque em
  aberto: aqui o `Nro_Rolo_Origem` e a `Situacao` entram no antijoin).

**Custos por centro de custo: portado e validado (02/out/2026).** Rev `0011`. É o único objeto do
dashboard que é **tabela**, não view: `Rel_CCusto_Niveis` (1.842 linhas, 42 colunas) é o snapshot que
`sp_PagRel_CCusto_Niveis` materializa com `TRUNCATE + INSERT`.

- Segue a **opção A** do Estudo 12 §5 (rápida e sem reimplementar 40 KB de T-SQL): a tabela entra
  como fonte do ETL (`DBProDash.dbo.Rel_CCusto_Niveis` → `raw.rel_ccusto_niveis`, 1.842/1.842
  conferidas) e as regras viram views. As procs que escrevem não são portadas.
- `core.custo_baixas` (documento/parcela × despesa × departamento) +
  `marts.custos_por_departamento_mensal` (mês × `Codigo_Departamento` × `Codigo_Despesa`) +
  `marts.custos_administrativo_mensal` (departamentos `1.1.1.1`/`1.1.1.2`, base do
  `Porc_Administrativo` de `uspCustoAdmArmFat`).
- A chave natural precisa de `CodFornecedor`: sem ela 8 grupos se repetem e a carga perde 8 linhas
  (1.842 → 1.834).
- Colunas `Tot*` da tabela são totais de janela do legado — nunca somar; o mart usa `Valor_Baixado`.
- `vwContasPagasCentroCusto` expõe só 7 colunas (`Codigo_Despesa`, `Codigo_Departamento`,
  `Valor_Baixado`, `Data_Baixa`, `MesAno`, `Referente`, `Razao_Nome_Cliente`), então o mart agrupa
  pelos **códigos com ponto**, como o dashboard.
- Validação: **1038/1038** (518 grupos mês/despesa/departamento + 16 meses administrativos).

**Estado da Fase D (02/out/2026): 8 de 8 fatias portadas.** `python -m scripts.validar_fase_d` fecha
em **1765/1765** comparações iguais ao `DBProDash`; `pytest` 54 testes; `ruff` limpo. Registry com
46 fontes (45 do ERP canônico + `DBProDash.dbo.Rel_CCusto_Niveis`).

Validação D: para amostras (mês corrente + 12 meses), KPIs do Neon **iguais** aos do `DBProDash`
(divergência < 0.01); para estoque, 10 pedidos reais com mesma sugestão de rolos. Só então os
endpoints voltam a ser confiáveis.

---

## 6. Fase E — Endpoints da API (substitui o `oraculum`)

Objetivo: os 14 contratos de negócio + health, lendo **o warehouse Postgres local**, com auth.
Nos endpoints de dashboard, a API **sincroniza no Neon** (schemas próprios) apenas o **agregado
pequeno** correspondente, on-demand — nunca o volume bruto do ERP.

| Grupo | Rotas |
|-------|-------|
| Estoques | `GET /dados` (paginação por cursor), `GET /sugestao-rolos/{pedido}`, `GET /pdf/sugestao-rolos/{pedido}` |
| KPI | `GET /faturamento/{data}`, `/faturamento-dia/{data}`, `/contas-pagas/{data}`, `/custos-administrativos-anual|mensal`, `/descontos/{data}`, `/devolucoes/{data}`, `/estornos/{data}`, `/contas-receber|pagar-programado` |
| Agregado | `GET /dashboard-completo/{data}` (alias que lê os marts) |
| Infra | `GET /health` (sonda Neon + status `etl.watermark`) |

> **Já feito (02/out/2026):** as 13 rotas de negócio + `/health` leem o warehouse local, com
> `scripts/validar_api.py` comparando cada uma contra a procedure original (**264/264**).
> `GET /pdf/sugestao-rolos/{pedido}` (reportlab), auth e sync on-demand ao Neon ficam para os
> próximos passos; `/procedures` e `/test-procedure/{nome}` foram eliminados (porta de fuga).

Entregáveis da fase:
1. **Auth** (D5/D6): login, JWT curto, `bcrypt`; escopos por rota derivados dos tópicos.
2. **Schemas Pydantic** idênticos em forma ao legado, porém tipados, `trim`, datas ISO.
3. **Data de entrada**: aceita ISO; legacy `DDMMYYYY` em modo deprecado (log de aviso).
4. **Erros padronizados**; CORS restrito às origens do `dgbcomex`.
5. **PDF** (reportlab) servido do `marts`**local** (404 quando não há material).
6. **Sync on-demand p/ Neon** (D4): ao chamar um KPI de dashboard, re-sincroniza no Neon o mart
   pequeno se o `etl.watermark` local estiver à frente da última publicação.
7. **Teste de contrato**: script que bate KPI ONDE/legado × local e compara JSON (mesmo p/ `/dados`).

> **Volume no Neon**: dashboard usa **agregados pequenos** (dezenas a centenas de linhas por mart,
> não tabelas brutas). O que não é agregado explicável para dashboard **permanece só no Postgres
> local** — ex.: `Cte_Peca`, `Fat_*`, `Pag_*` nunca sobem para o Neon.

Critério de aceite E: contrato automático passando; latência de `/dashboard-completo` muito menor
que a do legado (unica leitura, não 10 procs); auth bloqueando rota sem escopo; Neon enxuto
(só agregados pequenos; `public` inalterado).

---

## 7. Fase F — Cutover e descomissionamento

1. Apontar o `dgbcomex` (repo separado, Next.js/Vercel) para os **marts da API no Neon** (KPIs de
   dashboard); `public` continua sendo o schema do app (drizzle).
2. Período de sombra: manter o legado no ar (read-only) 1–2 semanas comparando os mesmos KPIs.
3. Desligar o `oraculum`; remover proc escreve/DBProDash do uso; **não** excluir `DBProDash`
   (pode haver consulta ocasional) — apenas deixar de ser fonte.
4. Documentar contrato final + runbook de ETL (cron do Postgres local, sync on-demand, monitor).

Critério de aceite F: `dgbcomex` operando com dashboards servidos do Neon (marts da API);
warehouse local completo; legado desligado sem perda de tela.

---

## 8. Próximas ações imediatas (ordem)

- [x] Neon `dgbcomex` criado e **`public` migrado** (118 tabelas) via `npm run db:migrate:all` no repo dgbcomex.
- [x] Seed do dgbcomex executado no Neon (4 usuários demo + menus).
- [x] **Postgres local** nativo instalado (PG 17, `localhost:5432`) e database `dgbcomex_warehouse` criado.
- [x] Fase B: criar `app/` com pyproject + venv + deps; `.env` com `DATABASE_URL` (Neon) e `DATABASE_URL_LOCAL`.
- [x] Alembic: migrations 0001–0004 no **Postgres local** + schemas próprios no Neon (Fase B).
- [x] Módulo `db/erp.py` (conexão read-only) + prova de conceito de extract de 1 domínio
      (`Fat_Pedido`: 12.746 linhas × 215 colunas em ~17s, contagem idêntica ao ERP).
- [x] Implementar ETL incremental + `etl.watermark` (local); bootstrap conferido (37 fontes
      iniciais + 9 acrescentadas na Fase D = 46 no registry).
- [x] PORTAR regras `core`/`marts` (local) e validar KPIs (Fase D) — **8/8 fatias prontas**:
      faturamento, contas pagas, financeiro programado, estornos, devoluções, estoque em aberto,
      sugestão de rolos e custos/centro de custo (**2198/2198** comparações iguais ao `DBProDash`).
- [x] Fase E (1/3): **13 rotas de negócio** lendo o warehouse local — `/dados`, `/sugestao-rolos/{pedido}`
      e os 11 KPIs (incluindo `/dashboard-completo/{data}`), com `/health`; contratos medidos contra as
      procedures em `scripts/validar_api.py` (**300/300**). `/procedures` e `/test-procedure/{nome}`
      do legado **não** foram portados (eram `EXEC` arbitrário no ERP).
- [x] ETL: carga completa agora **reconcilia exclusões** do ERP (`upsert.apagar_ausentes`) — o
      pedido `013290` (excluído no ERP) sobrevivia no `raw` e inflava o faturamento.
- [ ] Fase E (2/3): **auth** (D5/D6) e **PDF** de sugestão de rolos (reportlab).
- [ ] Fase E (3/3): **sync on-demand** dos KPIs p/ Neon.
- [ ] Cutover (Fase F) e documentação final.

---

## 9. Riscos que vão ditar o ritmo

- **Volume**: `Cte_Peca` (~317k) e itens de romaneio dominam a carga — **vão só para o Postgres
  local** (agendar fora do expediente); o Neon nunca recebe esse volume.
- **Watermarks ausentes**: confirmado no ERP — `Clientes_Principal`, `Fat_Pedido`,
  `Fat_Itens_Pedido`, `Car_Pedido` e `Car_Itens_Pedido` **não têm coluna de alteração utilizável**
  (3 delas são 100% NULL). Viram `full`/`recarrega_pai`; `src.cli check` impede regressão.
- **Dependência de rede**: runner do ETL precisa alcançar `10.156.0.124` (ERP on-prem).
- **Auth nova**: migrar usuários/escopos (do `Usuario_Acessos`) é trabalho próprio — iniciar cedo.
- **Contrato do front**: mudanças em marts (locais e do Neon) quebram o `dgbcomex` — aditivo e versionado.
- **`public` compartilhado**: o Neon já tem dados do produto (118 tabelas em `public`); a API só
  tem schemas próprios no Neon e **nunca** altera `public` — integrações ficam nos marts.
- **Bootstrap do Neon já feito** (setup): estrutura do `public` criada por `scripts/migrate.js`
  + `apply-drizzle-migrations.js` + seed (4 usuários demo) — nada disso é responsabilidade do
  api-microdata.
- **Postgres local = novo ativo**: backup/recovery (pg_dump) e liberação de porta 5432 precisam
  ser definidos; é o warehouse de verdade (fonte dos endpoints).

---

_Estado: Fase A concluída. Infra pronta (Neon migrado + Postgres local 17 criado)._
_Fase B concluída (02/out/2026): scaffold `app/` executável, `alembic upgrade head` no head local,
37 tabelas `raw` geradas do catálogo do ERP, `/health` = 200 e PoC de carga validada contra o ERP._
_Fase C concluída (02/out/2026): bootstrap das 37 fontes carregado e reconciliado com o ERP
(987.878 linhas, contagem idêntica em todas as tabelas) + incremental por watermark e recarga por
pai. Próximo checkpoint: Fase D (`core`/`marts` com as regras portadas). Decisões D5–D8 ainda
pendentes de confirmação (auth JWT, contrato do front, on-demand do Neon, decommission do legado)._