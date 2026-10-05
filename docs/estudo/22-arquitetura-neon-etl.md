# Estudo 22 — Arquitetura de destino (DBProDash → Neon) e carga pela API

Levantamento de **estrutura** (chaves, colunas de data e contagens) por `SELECT` somente leitura.
Nenhum dado real. Este documento define o **plano de dados/ETL** da nova API.

## 1. Contexto e restrições

- **`DBMicrodata_DGB`** é o banco do **fornecedor Microdata**, de produção: **não aceita objetos
  novos** e é acessado **somente leitura**.
- **`DBProDash`** é a **camada própria** criada internamente (porque não se pode criar nada no
  ERP). Suas views fazem `SELECT` em `DBMicrodata_DGB` por nome de 3 partes. É onde hoje vivem os
  dashboards/BI.
- **Neon (PostgreSQL, externo)** será o **destino dos dados**. Ele **não tem acesso ao
  `DBMicrodata_DGB`**.
- Portanto, **a aplicação (API em Python) é quem extrai do `DBMicrodata_DGB` e carrega no Neon**.

Consequência: **o `DBProDash` é o protótipo/molde** — o que for modelado e validado nele será
**portado para o Neon**, e a API passa a ter **dois papéis**: (a) servir dados (lendo do Neon) e
(b) **carregar/orquestrar o ETL** (ERP → Neon).

## 2. Arquitetura alvo

```
DBMicrodata_DGB (SQL Server, read-only, on-prem)
        |  SELECT (PK, watermark)
        v
  API Python  ──────────────►  Neon / PostgreSQL (externo)
   (extract + transform)          ├── raw    (espelho das fontes, sem regra)
                                  ├── core   (regras portadas do DBProDash)
                                  └── marts  (views _PBI/_Qlik portadas p/ consumo)
        ^                                 |
        |                                 |  leitura DIRETA (Prisma/Drizzle, DATABASE_URL)
DBProDash (on-prem)                       v
  = PROTÓTIPO das views          dgbcomex (Next.js + Vercel)
                                   dashboards · BI · CRM · ...
```

- A API lê do ERP e carrega no Neon; o **`dgbcomex` lê o Neon direto** (não passa pela API).
- O `DBProDash` deixa de ser lido em produção pela API; serve de **referência de regra**
  (contrato de negócio já validado).

### 2.1 Camada de consumo: `dgbcomex` (Next.js + Vercel)

- **Repositório separado** (`dgbcomex`), em **Next.js**, hospedado na **Vercel**.
- **Lê o Neon diretamente**, via driver ORM (Prisma/Drizzle/`postgres.js`) com `DATABASE_URL`
  (pooled) nas variáveis de ambiente da Vercel.
- É a camada de **produto/apresentação**: dashboards, BI, CRM e demais telas.
- Consequências de arquitetura:
  1. O **Neon é o contrato compartilhado** entre o ETL (Python) e o front (Next.js): as colunas/
     schemas são API pública de fato.
  2. O `dgbcomex` só enxerga o que o ETL grava — **nada de acesso ao ERP**.
  3. **`raw` não deve ser lido pelo front**; expor apenas `core`/`marts` (estáveis e documentados).
  4. Como o front é **serverless**, usar **connection pooling** do Neon; cargas pesadas de ETL não
     devem competir com a leitura do front (janela/limite de conexões; considerar read replica).
  5. **Ownership de migrations**: o schema Neon é alterado **pelo lado do ETL**; mudanças precisam
     ser compatíveis com o `dgbcomex` (versionar contrato, evitar `DROP`/rename sem etapa de transição).

## 3. O que replicar para o Neon

### 3.1 Contratos de negócio (portar do `DBProDash`)

Views de `DBProDash` a serem reescritas como views/materialized views no Neon:
Faturamento (`vwFaturamento*`), DRE tecidos (`vwDRESaldoTecidos*`), Saldo de tecidos
(`vwSaldoTecidos*`), Financeiro (`vwFinanceiroContasPagar/Receber`, `vwContasPagas*`),
Inventário/peças (`vwInventarioDePecas`, `vwSaidaPecas`), Fiscal
(`vwListagemDeEntradasSaidasPorCFOP`, `vwListagemDeEstornos`).

### 3.2 Views de BI do ERP (contratos de consumo)

`*_PBI` (7) e `*_Qlik` (5) do `DBMicrodata_DGB` — também portadas como `marts` no Neon.

### 3.3 Fontes (domínios a sincronizar)

| Domínio | Tabelas-fonte principais |
|---------|--------------------------|
| Pedidos/carteira | `Car_Pedido`, `Car_Itens_Pedido`, `Car_Romaneio`, `Car_Itens_Romaneio`, `Car_AnexosNFE_Cliente`, `CAR_Itens_Carteira_Data_Previsao_Log` |
| Receber | `Notas_Fiscais_Rec`, `Notas_Fiscais_Parcelas`, `Rec_Baixas` |
| Pagar | `NF_Entradas`, `NFE_Parcelas`, `Pag_Baixas` |
| Fiscal | `Liv_Saidas`, `Liv_SaiProd`, `Liv_Entradas`, `Liv_EntProd`, `Liv_XML`, `Liv_XML_Item` |
| Estoque de peças | `Cte_Peca`, `CTE_Baixa`, `CTE_Saldos`, `CTE_RomTransf`, `CTE_Itens_RomTransf` |
| Cadastros | `Clientes_Principal`, `Produtos`, `Rec_Vendedores`, `Fornecedores`, `Entidades`, `CAR_TABELA_PRECO*`, `Condicoes_Pagto` |
| Importação/previsão | `Ret_Aviso_Recebimento`, `Ret_Aviso_ItensRecebimento`, `Ret_Aviso_Itens_Pedido_Atend`, `Fat_Itens_Pedido_DI`, `Liv_EntProd_DI` |
| Bancos | `bco_lancamentos`, `BCO_HISTORICOS` |
| Web | `pedido_web`, `pedido_web_prod` |

## 4. Estratégia de carga

1. **Bootstrap (carga full)** uma vez por tabela, em **ordem de dependência**:
   cadastros → cabeçalhos → itens → logs.
2. **Incremental por watermark**: para cada tabela, ler `WHERE <watermark> > @ultimo`. A coluna de
   watermark é a **data/hora de movimento**; quando não houver, usar a PK/`ID` crescente (ver §5).
3. **Upsert** pela **chave natural do ERP** (a PK listada em §5), nunca por surrogate local.
4. **Idempotência/reprocesso**: carga reprocessável (a partir de uma janela), com registro de
   `ultimo_valor` por tabela (controle de watermark) no Neon.
5. **Exclusões/baixas**: o ERP é read-only; onde não há flag de exclusão, detectar por
   **reconciliação periódica** (full) ou por tabelas de baixa (`CTE_Baixa`, `Rec_Baixas`,
   `Pag_Baixas`). Preferir **soft delete** no Neon.
   **Implementado (02/out/2026):** toda carga **completa** guarda as chaves naturais vistas numa
   `temp table` indexada e apaga de `raw` o que não veio do ERP (`upsert.apagar_ausentes`). Sem
   isso o pedido `013290`, **excluído** no ERP, continuava no `raw` e inflava
   `marts.faturamento_diario` (`Fat_Itens_Pedido` chegou a ter 15 linhas a mais). Carga
   incremental e carga parcial (`--limite`) **não** reconciliam: só a completa sabe o que sumiu.
6. **Filhos sem data**: recarregar por **documento-pai** (delete+insert do documento) — ex.:
   `Liv_SaiProd`/`Liv_EntProd` (itens sem data), `CTE_Itens_RomTransf`, `Ret_Aviso_ItensRecebimento`.
7. **Controle de carga** (tabela no Neon): tabela, watermark, última execução, status, linhas,
   erro — permite retomar e auditar.

## 5. Mapa de chaves e watermarks (levantado)

Legenda watermark: **✅** coluna de data/hora adequada · **~** só data (pode exigir janela de
reprocesso) · **✖** sem data (usar PK/ID ou recarga por pai).

| Tabela | PK (chave de upsert) | Watermark | Linhas |
|--------|----------------------|-----------|-------:|
| `Car_Pedido` | Empresa, Pedido | ✅ `DataHora_Alteracao` | 15 799 |
| `Car_Itens_Pedido` | Empresa, Pedido, Item | ~ `DtEntrega`/`Data_Previsao_Fabril` | 41 224 |
| `Car_Romaneio` | Empresa, Romaneio, Tipo | ✅ `Data_Status`/`Data_Troca` | 14 596 |
| `Car_Itens_Romaneio` | Empresa, Romaneio, Tipo, Incremento | ✖ (recarga por romaneio) | 274 402 |
| `Car_AnexosNFE_Cliente` | Cliente | ✖ (full) | 309 |
| `CAR_Itens_Carteira_Data_Previsao_Log` | ID | ✅ `Data_Alteracao` | 5 838 |
| `Notas_Fiscais_Rec` | Nr_Empresa_NF, Nr_Documento_NF, Serie | ✅ `Data_Hora` | 11 739 |
| `Notas_Fiscais_Parcelas` | Empresa_Parcelas, Documento_Parcelas, Serie, Parcela_Parcelas | ~ `Vencimento_Parcelas` | 29 885 |
| `Rec_Baixas` | Empresa, Documento, Serie, Parcela, Parcial | ✅ `Data_Hora_Baixa` | 28 903 |
| `NF_Entradas` | Empresa, Documento, Serie, Tipo_Fornec, Fornecedor | ✅ `Data_Hora` | 8 631 |
| `NFE_Parcelas` | Empresa, Documento, Serie, Tipo_Fornec, Fornecedor, Parcela | ~ `Vencimento` | 8 736 |
| `Pag_Baixas` | Empresa, Documento, Serie, Tipo_Fornec, Fornecedor, Parcela, Parcial | ✅ `Data_Hora_Baixa` | 8 603 |
| `Liv_Saidas` | Empresa, Documento, Serie | ✅ `Data_Hora` | 12 098 |
| `Liv_SaiProd` | Empresa, Documento, Serie, NatOp, Seq, EmpProd, Produto, Incremento | ✖ (recarga por documento) | 34 043 |
| `Liv_Entradas` | Empresa, Documento, Serie, Fornecedor, Tipo_Fornec | ✅ `Data_Hora` | 1 957 |
| `Liv_EntProd` | Empresa, Documento, Serie, Tipo_Fornec, Fornecedor, NatOp, Seq, EmpProd, Produto, Incremento | ✖ (recarga por documento) | 22 522 |
| `Liv_XML` | ID_Empresa, ID | ✅ `dtCompt` | 1 690 |
| `Liv_XML_Item` | ID_Empresa, ID | ✖ (recarga por pai) | 20 109 |
| `Cte_Peca` | Empresa, Situacao, Nro_Rolo, Nro_Peca | ✅ `Alt_Data`/`Data_Hora` | 297 044 |
| `CTE_Baixa` | Empresa, Situacao, Nro_Rolo, Nro_Peca | ~ `Data_Saida` | 280 240 |
| `CTE_Saldos` | ID_Saldo | ~ `Data` | 5 577 |
| `CTE_RomTransf` | Empresa, Romaneio | ~ `Data_Rom` | 4 266 |
| `CTE_Itens_RomTransf` | Empresa, Romaneio, ID_RomTransf | ✖ (recarga por romaneio) | 130 622 |
| `Clientes_Principal` | Codigo_Cliente | ✅ `Ult_Atualizacao`/`ultima_atualizacao_dt` | 1 681 |
| `Produtos` | Empresa, Codigo | ✖ (full) | 64 |
| `Rec_Vendedores` | Codigo_Vendedores | ✖ (full) | 77 |
| `Fornecedores` | (sem PK) | ~ `Data_Cadastro` | 659 |
| `Entidades` | (sem PK) | ~ `Data_Cadastro` | 1 681 |
| `CAR_TABELA_PRECO` | ID | ✅ `DT_INCLUSAO` | 9 |
| `CAR_TABELA_PRECO_PRODUTO_VALOR` | ID | ✖ (full) | 104 |
| `Condicoes_Pagto` | Codigo_Pagto | ✖ (full) | 362 |
| `Ret_Aviso_Recebimento` | Empresa, Aviso | ✅ `Data_Hora_Receb` | 218 |
| `Ret_Aviso_ItensRecebimento` | Empresa, Aviso, Item_Aviso | ✖ (recarga por aviso) | 2 083 |
| `Ret_Aviso_Itens_Pedido_Atend` | Empresa, Aviso, Item_Aviso, Pedido, Item_Pedido, ID_Ent | ✖ (recarga por aviso) | 2 083 |
| `Fat_Itens_Pedido_DI` | Empresa, Pedido, Item, Id_DI | ~ `Data` | 2 473 |
| `Liv_EntProd_DI` | Empresa, Documento, Tipo_Fornec, Fornecedor, Serie, NatOp, Seq, EmpProd, Produto, Incremento_Produto, Cod_Liv_EntProd_DI | ✖ (recarga por documento) | 2 427 |
| `bco_lancamentos` | Cod_Empresas, Nr_Bco_Bancos, Nr_Age_Contas, Dig_Age_Contas, Nr_Conta_Contas, Dig_Conta_Contas, Incremento | ✅ `DataHoraGravacao`/`DataLancamento` | 10 864 |
| `BCO_HISTORICOS` | Cod_Historico | ✖ (full) | 37 |
| `pedido_web` | emp_cd, ped_nu | ✅ `salvo_dt` | 2 048 |
| `pedido_web_prod` | emp_cd, ped_nu, seq | ~ `entrega_dt`/`estoque_dt` | 4 915 |

Atenção: muitas PKs usam `char` com padding à direita (ex.: `Empresa char(2)`, `Produto char(6)`)
— **normalizar (`trim`) na transformação** antes de gravar no Neon.

## 6. Porte SQL Server → PostgreSQL (checklist)

| SQL Server | PostgreSQL |
|------------|------------|
| `ISNULL(x,y)` | `COALESCE(x,y)` |
| `GETDATE()` | `now()` / `CURRENT_TIMESTAMP` |
| `DateDiff(dd,a,b)` | `(b::date - a::date)` |
| `CONVERT(VARCHAR(30), d, 103)` | `to_char(d,'DD/MM/YYYY')` |
| `LEN` / `SUBSTRING` | `length` / `substring` |
| `STUFF(... FOR XML PATH(''))` (pivot) | `string_agg` / `crosstab` / pivot em Python |
| `TOP n` | `LIMIT n` |
| Nome de 3 partes `DB.dbo.T` | uma única base (Neon) |
| `CHAR(n)` com padding | `trim()` / `TEXT`/`VARCHAR` |
| `@var` / cursores | parâmetros / CTEs |
| `NUMERIC(19,10)` | `NUMERIC(19,10)` (equivalente) |

As procs `uspRel_CCusto_Niveis*` fazem `TRUNCATE+INSERT` (escrita) — **não portar como proc**;
virar carga/materialização no Neon. O **pivot do SIM Web estoque** (`uspSIMWebEstoque*`) usa SQL
dinâmico — **reimplementar em Python**.

## 7. Convenções sugeridas no Neon

- **Schemas**: `raw` (espelho fiel das fontes), `core` (regras, espelho do `DBProDash`) e `marts`
  (views de consumo `_PBI`/`_Qlik`).
- **Identificadores**: minúsculos, `snake_case`, sem aspas (evita conflito com o `case` do ERP);
  manter o nome original do ERP em comentário/coluna de rastreio.
- **Chaves**: `PRIMARY KEY` = chave natural do ERP (após trim); sem surrogate exposto.
- **Tipos**: datas em `timestamp`/`date`; valores em `numeric`; flags char(1) em `char(1)`/`text`.
- **Controle**: schema `etl` com `etl.watermark` (tabela, coluna, ultimo_valor) e `etl.execucoes`.
- **Contrato com o `dgbcomex`**: só `core`/`marts` são expostos; `raw` e `etl` ficam privados.
  Mudanças de schema devem ser **aditivas e versionadas** (o front lê direto, então rename/drop
  quebra a Vercel sem aviso).

## 8. Segurança

- Credenciais do ERP **e do Neon** apenas em `.env` local (gitignored) — **nunca** no repositório.
- A API autentica no ERP como **read-only** e no Neon como **owner da camada própria**.
- Sem dados reais em logs/documentação; mascarar quando necessário.

## 9. Riscos e dependências

- **`DBInternet_DGB` inexistente**: views que dependem dele (`vwDRESimWebEstoque` e cascata) estão
  quebradas — **não portar** enquanto não houver o banco/web fonte.
- **Procs que escrevem** no `DBProDash` (`uspRel_CCusto_Niveis*`, `uspCustoAdmArmFat*`) não devem
  ser executadas na origem.
- **Tabelas sem watermark** exigem recarga por pai/reconciliação — atenção a custo e janela.
- **Volume**: itens de romaneio (274k), peças baixadas (280k) e bases de peças (297k) dominam a
  carga; planejar carga full inicial fora do horário comercial e incremental por janela.
- **Fusos/datas**: ERP grava data+hora local; padronizar em `America/Sao_Paulo` na carga.
- **Leitura direta pelo `dgbcomex`**: sem camada de API entre o Neon e o front, qualquer mudança de
  schema em `core`/`marts` é **breaking change**; tratar as views como contrato versionado.
- **Concorrência de conexões**: ETL (Python) e front (Vercel/serverless) disputam o pool do Neon;
  separar horários/limites e considerar read replica para o front.

## 10. Próximos passos

1. Definir o **schema `raw`** mínimo (tabelas + colunas) a partir de §5/§3.
2. Implementar o **extractor** (paginado por watermark) e o **upsert** no Neon.
3. Portar as views do `DBProDash` para `core`/`marts` (validar números contra o on-prem).
4. Criar a tabela de **controle de carga** e a rotina de **reconciliação** (full periódico).
5. Só então expor os **endpoints** da API lendo do Neon.
