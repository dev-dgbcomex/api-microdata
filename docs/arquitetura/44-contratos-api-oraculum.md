# Contratos da API (mapeamento legado `oraculum` → Postgres local + Neon)

> **Data:** 19/set/2026 (atualizado 25/set/2026)
> **Escopo:** catálogo das rotas da API legada `docs/legado/oraculum/src/main.py` (+
> `main_funcional_01.py` e `database.py`), com a fonte no legado, a tabela/view do ERP/BI e o
> **mart (warehouse local)** que a substitui. O **Postgres local** (`dgbcomex_warehouse`) é a
> fonte de verdade dos endpoints; **somente agregados pequenos de dashboard** são sincronizados
> on-demand para o **Neon** (ver [Doc 43](./43-arquitetura-alvo-api.md)). Todo endpoint do alvo lê
> o warehouse local, **nunca** o ERP.
> **Fonte dos dados:** recon executado em 19/set/2026 sobre `DBProDash` (defs de `sys.sql_modules`)
> e o SQL preservado em `docs/legado/oraculum`.

---

## 1. Visão geral das rotas

`main.py` expõe **17 rotas**:

| Grupo | Rotas | Qtd |
|-------|-------|-----|
| Negócio (dados de estoque) | `/dados`, `/sugestao-rolos/{pedido}`, `/pdf/sugestao-rolos/{pedido}` | 3 |
| Negócio (KPIs/dashboard) | `/faturamento/{data}`, `/faturamento-dia/{data}`, `/contas-pagas/{data}`, `/custos-administrativos-anual`, `/custos-administrativos-mensal`, `/descontos/{data}`, `/devolucoes/{data}`, `/estornos/{data}`, `/contas-receber-programado`, `/contas-pagar-programado`, `/dashboard-completo/{data}` | 11 |
| Auth (D5) | `/auth/login`, `/auth/eu` | 2 |
| Infra/dev | `/health` | 1 |

Das 17: **14 ficam no alvo** (re-exposição lendo o **warehouse local**), `/health` é redefinido
(sonda Postgres local + status ETL) e **`/test-procedure` e `/procedures` são eliminados**
(pass-through de `EXEC` e inventário interno). **Status (05/out/2026): as 14 + `/health` + as 2 de
auth publicadas e medidas contra as procedures** (`app/scripts/validar_api.py`: 300/300 fora a
corrida do ERP vivo descrita na §5).

Conexão atual (`database.py`): pyodbc → `DB_SERVER/DB_DATABASE` (SQL Server), uma conexão por
request; datas no padrão `DDMMYYYY` convertidas para `DD/MM/YYYY` (`formatar_data_para_sql`).

---

## 2. Contratos de negócio (endpoint a endpoint)

> Legenda: **Fonte ERP** = tabelas/views que alimentam o dado; **Mart (warehouse local)** = objeto
> no Postgres local `dgbcomex_warehouse` (schemas `core`/`marts`). `?` = contrato a confirmar com o negócio.

### 2.1 Estoque de peças / endereçamento (lê `DBMicrodata_DGB`)

| # | Rota | Fonte no legado | Fonte ERP | Mart (warehouse local) | Contrato JSON (alvo) | Notas |
|---|------|-----------------|-----------|-----------|----------------------|-------|
| 1 | `GET /dados` | Query inline (`main.py:35`) — `Cte_Peca` ⨝ `CTE_Baixa` (antijoin) ⨝ `Produtos_Tecidos`, `WHERE Nro_Rolo_Origem IS NULL AND CB.Empresa IS NULL` (peças em aberto) | `Cte_Peca`, `CTE_Baixa`, `Produtos_Tecidos` (Estudos 19/34) | `core.estoque_pecas_em_aberto` (grão peça: `Empresa/Situacao/Nro_Rolo/Nro_Peca`) | array de `{Empresa, Nro_Rolo, Nro_Peca, Produto, Produto_Descricao, Situacao, Situacao_Descricao, Cor, Cor_Descricao, Desenho, Desenho_Descricao, Categoria, Categoria_Descricao, Variante, Variante_Descricao, Largura, Metros, Peso, Data_Entrada, Gaveta, Chave}` | **Sem paginação no legado** (~300k peças `fetchall`); na Fase E: `limite` (padrão 500, máx. 10.000) + `offset` e filtros `produto`/`cor`/`situacao`. `Chave = Nro_Rolo+Situacao+Cor+Desenho` (concatenação ambígua no legado) →derivado com separador. Campos `char` com padding → `trim`. Campos do legado **fora** do mart (`Lote_Interno`, `Aviso`, `SubLote`, `Num_Etq_Aux`, `Linha`) ficam de fora até a Fase F |
| 2 | `GET /sugestao-rolos/{pedido}` | `EXEC DBMicrodata_DGB.dbo.uspEnderecamentoParaAtenderPedidoGeral @Pedido char(8)` — para cada item do pedido, acumula rolos em aberto até `Qtde_Saldo` (janela por `Gaveta/Tear DESC/Nro_Rolo DESC`) | `Vw_Car_Itens_Pedido`, `Cte_Peca`, `CTE_Baixa` (Estudos 28/34) | `core.pedido_sugestao_rolos` — uma linha por **item** com saldo (`LEFT JOIN` na lista de rolos: item sem sugestão sai com nulos e `Qtde_Pecas = 0`, como no cursor do legado) | array de `{Produto, Cor, Qtde_Item, Qtde_Saldo, Sublote, Gavetas, Rolos, Qtde_Pecas, Total_Metros}` | Parâmetro `char(8)`; legado com `CURSOR` + `FOR XML PATH` (não portar). Gd: tolerância `ABS(Soma-Saldo)<0.01`. `Qtde_Item` é `TOP 1 Qtde` do mesmo `(Pedido, Produto, Cor)` **sem `ORDER BY`** — na prática sai o **primeiro item**; reproduzido com `first_value(...) ORDER BY item` |
| 3 | `GET /pdf/sugestao-rolos/{pedido}` | Mesmo `EXEC` + reportlab (A4 paisagem, 3 cards/linha) | idem #2 | idem #2 (mesmo cálculo) | PDF `Content-Disposition: inline`; 404 se vazio | **Portado:** BytesIO em memória (o legado criava arquivo temporário por request). Card mostra Produto, Cor, Qtde Item, Qtde Saldo, Sublote, Gavetas, Qtde Peças, Rolos, Total Metros |

### 2.2 KPIs do dashboard (lê `DBProDash` — BI a portar)

| # | Rota | Proc legado (DBProDash) | Regra (fonte atual) | Fonte ERP (via view) | Mart (warehouse local) | Contrato JSON (alvo) | Notas |
|---|------|--------------------------|----------------------|----------------------|----------|----------------------|-------|
| 4 | `GET /faturamento/{data}` | `uspFaturamento` | `SUM(Vr_Total)+SUM(Acres_Desc) AS Faturamento` por **mês** da `Data_Nota` (`EOMONTH`) | `vwFaturamento` → `Fat_Pedido`, `Fat_Itens_Pedido`, `Fat_Nat_Pedido`, `Produtos_Tecidos`, `Clientes_Principal` (Estudo 29) | `marts.faturamento_diario` (`data_emissao`, `vr_total`, `acres_desc`, `metros` QMP, `vr_nota`) | `{"Faturamento": number}` | Janela de mês calculada na carga; parâmetro passa a **ISO** |
| 5 | `GET /faturamento-dia/{data}` | `uspFaturamentoDia` | idem por **dia** (`Data_Nota = data`), `ISNULL(...,0)` | idem | `marts.faturamento_diario` | `{"Faturamento": number}` | |
| 6 | `GET /contas-pagas/{data}` | `uspListagemBaixasPagar` | `SUM(ValorPago) AS ContasPagas` do mês de `Data_Baixa` (a `usp` **funciona**: devolve uma coluna `ContasPagas`) | `vwContasPagas` → `Pag_Baixas`, `NF_Entradas`, `NFE_Parcelas`, `Pag_Historicos`, `Pag_Operacoes`, `Bancos`, `Clientes_Principal` (Estudos 06/31) | `marts.contas_pagas_diario` (`data`, `valor_pago`) | `{"ContasPagas": number}` | O `SELECT` do `main.py` legado está comentado (rota devolvia `{}`), mas a procedure devolve dado: contrato **reaberto para `ContasPagas`** e medido em `scripts/validar_api.py` |
| 7 | `GET /custos-administrativos-anual` | `uspRel_CCusto_NiveisAnual` (**`TRUNCATE+INSERT` em `Rel_CCusto_Niveis`**) + `uspCustoAdmArmFat` | `Faturamento` = Σ dos **12 meses fechados** (mês corrente − 12 .. − 1) **÷ 12**; `Administrativo` = acumulado **histórico** de `1.1.1.1`/`1.1.1.2` **÷ 12**; `Armazenagem` = 0; `Porc_Administrativo` = admin/fat | `Rel_CCusto_Niveis` (carga por `sp_PagRel_CCusto_Niveis`), `vwContasPagasCentroCusto` (cod. despesa/departamento), `vwFaturamento` (Estudo 12) | `marts.custos_administrativo_mensal` + `marts.faturamento_diario` | `{"Faturamento", "Administrativo", "Armazenagem", "Porc_Administrativo", "Porc_Armazenagem"}` — **sem variantes `*_Replace`** | "Anual" = média mensal sobre 12 meses fechados; o administrativo é o **acumulado histórico ÷ 12** (quirk do legado). Procs que escrevem **não portar** |
| 8 | `GET /custos-administrativos-mensal` | `uspRel_CCusto_NiveisMensal` + `uspCustoAdmArmFatMensal` | `Faturamento` = **último mês fechado** (sem dividir); `Administrativo` = acumulado **histórico**, **sem dividir** → `Porc_Administrativo` passa de 100% | `vwContasPagasCentroCustoMensal` | idem | idem | As duas procedures discordam entre si (a mensal não divide nada); replicado como está e medido em `scripts/validar_api.py` |
| 9 | `GET /descontos/{data}` | `uspDesconto` | `SUM(Acres_Desc) AS Desconto` por mês (`vwFaturamento`) | idem #4 | `marts.faturamento_diario` (agregado `acres_desc`) | `{"Desconto": number}` | |
| 10 | `GET /devolucoes/{data}` | `uspDevolucao` | `SUM(Vr_Contabil) AS Devolucao` por mês com `Nova_CFOP IN ('1.201-1','1.201-2','1.202-1','2.202-1')` | `vwListagemDeEntradasSaidasPorCFOP` (CFOP de venda/entrada — Estudo 35) | `marts.devolucoes_diario` (`data`, `cfop`, `vr_contabil`) | `{"Devolucao": number}` | Lista de CFOPs de devolução a validar (podem crescer) |
| 11 | `GET /estornos/{data}` | `uspEstorno` | `SUM(Vr_Nota) AS Estorno` por mês (`Data_Emissao`) | `vwListagemDeEstornos` | `marts.estornos_diario` (`data`, `vr_nota`) | `{"Estorno": number}` | |
| 12 | `GET /contas-receber-programado` | `uspDashFinanceiroContasReceberProgramado` | `COUNT(*)` das linhas e `SUM(ValorTotal)` de `vwFinanceiroContasReceber` com `Vencimento >= 1º dia do mês corrente` (até `2050-12-31`) | `vwFinanceiroContasReceber` = `Nota_Fiscal`/`Valor`/`Vencimento` de `VW_Rec_DuplicatasEmAberto`, empresas 13/14 (Estudo 30) | `marts.financeiro_receber_programado` (`qtde_doc`, `valor_total`, `vencimento`) | `{"QtdeDoc": int, "ValorTotal": number}` | Janela aplicada **na API** (a view do ERP não filtra); "programado" inclui os vencidos do próprio mês |
| 13 | `GET /contas-pagar-programado` | `uspDashFinanceiroContasPagarProgramado` | idem, mas `COUNT(DISTINCT Documento)` sobre `Valor_Parcela` | `vwFinanceiroContasPagar` = `Documento`/`Valor_Parcela`/`Vencimento` de `VW_Pag_Titulo_Aberto`, empresas 13/14 (Estudo 31) | `marts.financeiro_pagar_programado` | `{"QtdeDoc": int, "ValorTotal": number}` | `DISTINCT` no count — manter semântica |
| 14 | `GET /dashboard-completo/{data}` | Orquestração: chama #4,#5,#6,#7,#8,#9,#10,#11,#12,#13 (10 procs por request) | agrega em um dict | — | leitura única dos marts #4–#13 (uma query por KPI ou cache) | `{data_consulta, faturamento, faturamento_dia, contas_pagas, custos_administrativos_anual, custos_administrativos_mensal, descontos, devolucoes, estornos, contas_receber_programado, contas_pagar_programado}` | Legado = 10 `EXEC` sequenciais por request → alvo = leitura leve/cache. #4–#6 e #9–#11 usam o mês de `data`; #7, #8, #12 e #13 são relativos a **hoje** (as procedures não recebem data) |

### 2.3 Infraeste (redefinido) e descartados

| # | Rota | Legado | Alvo |
|---|------|--------|------|
| 15 | `GET /health` | `SELECT 1` no SQL Server | Sonda **Postgres local** (`SELECT 1`) + latência + status `etl.watermark` (última execução por tabela, atraso) |
| 16 | `GET /test-procedure/{name}` | `EXEC DBProDash.dbo.{name}` genérico | **Eliminar** (porta de fuga/execução arbitrária) |
| 17 | `GET /procedures` | lista estática de procs | **Eliminar** (inventário interno) |

---

## 3. Contrato global de resposta (regras que valem para todos)

1. **Parâmetros de data**: alvo aceita **ISO `YYYY-MM-DD`** (validado pelo FastAPI: `15-03-2026` →
   `422`); o legado aceitava `15032026` e `15/03/2026`.
2. **Tipos**: valores monetários/quantidade sempre `numeric` — **removidas** as variantes
   `*_Replace` (string pt-BR). Formatação é responsabilidade do front.
3. **`trim` de chaves `char`** (Produto, Cor, Nro_Rolo, Códigos, Empresa) via validators Pydantic.
4. **Erros padronizados**: `400` (param inválido), `401/403` (auth), `404` (sem dados), `5xx`
   `{erro, detalhe}`. Legado usava `500 {erro, detalhes}` e `404 {erro}` mistos.
5. **Listagens grandes** (`/dados`, e listas de baixas quando reabertas): **paginação por cursor**.
6. **Multiempresa**: código `'13'` vira **parâmetro implícito por token** (não hard-coded no SQL);
   chave usa `Codigo_Empresas` (`char(2)`) por compatibilidade — [Estudo 42 §7](../estudo/42-infra-topologia-infraestrutura.md).

---

## 4. Catálogo de marts no Postgres local resultante

| Mart (schema) | Grão | Alimenta endpoints | Sync p/ Neon |
|---------------|------|--------------------|--------------|
| `core.estoque_pecas_em_aberto` | peça (`Empresa,Situacao,Nro_Rolo,Nro_Peca`) | 1, 2, 3 | não (volume pesado) |
| `core.pedido_sugestao_rolos` | pedido×item (com saldo) | 2, 3 | não |
| `marts.faturamento_diario` | dia (`data`) | 4, 5, 9, 14 | sim (KPI agregado) |
| `marts.contas_pagas_diario` | dia (`data`) | 6, 14 | sim (resumo) |
| `marts.custos_administrativo_mensal` | mês×categoria (`administrativo`) | 7, 8, 14 | sim (resumo) |
| `marts.devolucoes_diario` | dia×cfop | 10, 14 | sim (KPI agregado) |
| `marts.estornos_diario` | dia | 11, 14 | sim (KPI agregado) |
| `marts.financeiro_receber_programado` / `marts.financeiro_pagar_programado` | título a vencer | 12, 13, 14 | sim (resumo) |

Camadas (no Postgres local): `raw` (fontes), `core` (regra), `marts` (consumo) e `etl` (controle).


**Sync on-demand (D4, entregue 05/out/2026).** Os agregados marcados como "sim" sobem para o Neon em tabelas de mesmo nome/coluna (`marts.*`, migration `1003`), publicadas por `src/etl/publicar.py`:

- **Quando**: na chamada de `/dashboard-completo`, e só se defasado. A versão do warehouse é `max(fim)` das execuções `ok` (`etl.execucoes`); o Neon guarda `publicado_em` em `etl.marts_publicados`. Conservative de propósito: qualquer carga nova republica os 7 marts (4.880 linhas, ~2s).
- **Atomicidade**: `delete` + `insert` na mesma transação — o front nunca lê mart pela metade.
- **Best-effort**: falha de sync **não** derruba a rota; o KPI continua saindo do warehouse local. Desligável com `NEON_PUBLICAR_AUTOMATICO=false` (padrão) e publicável na mão com `python -m src.cli publicar-neon [--mart X] [--forcar] [--status]`.
- **O que não sobe**: `core.estoque_pecas_em_aberto` e `core.pedido_sugestao_rolos` (grão de peça/linha de pedido).
- **Estado visível** em `GET /health` → `neon_publicacao` (defasados + publicados).

Detalhes e convenções no [Doc 43](./43-arquitetura-alvo-api.md) e no
[Estudo 22](../estudo/22-arquitetura-neon-etl.md).

---

## 5. Validação do contrato (cutover)

1. Para cada KPI (#4–#13), comparar valor **legado on-prem** × **mart do Postgres local** para os
   mesmos períodos (amostras: mês corrente + 12 meses) — divergência aceitável < 0.01.
2. Para #1/#2/#3, comparar conjunto de rolos sugeridos para 10 pedidos reais.
3. Só então publicar os agregados sincronizados no **Neon** (objetos da API) e descomissionar `DBProDash`.

Execução: `app/scripts/validar_api.py` (API × procedures do ERP, em todas as rotas; última rodada
**300/300**) e `app/scripts/validar_fase_d.py` (marts × `DBProDash`; última rodada **2220/2220**).
O acesso do validador ao ERP passa por `src.db.erp.assert_read_only`, que só admite `SELECT`/`WITH`
e `EXEC` de `PROCEDURES_SOMENTE_LEITURA` (as `uspRel_CCusto_Niveis*` e a `sp_PagRel_CCusto_Niveis`
ficam de fora porque **escrevem**).

> O ERP é **vivo**: entre a carga e a comparação ele muda (nota emitida, romaneio lançado, peça
> baixada). As duas rodadas acima foram feitas logo após `bootstrap` dos domínios envolvidos; uma
> divergência isolada de um documento novo significa recarregar o domínio e repetir, não regerar
> regra.
>
> **Medido em 05/out/2026** (vale registrar porque muda a leitura do resultado): durante uma mesma
> sessão o `DBProDash` devolveu valores diferentes para chaves que antes batiam, **sem nenhuma
> recarga do warehouse** — `('00006573','1','000028','00056')` foi `4966,00` e depois `4977,90`, e
> `('00006677','1','000028','00056')` trocou de 38 para 39 peças. A peça `0000816455/001` teve
> `Cte_Peca.Metros` alterado de `41,1000` para `41,0000` entre duas leituras do mesmo dia, e uma
> duplicata de contas a receber mudou de saldo (`QtdeDoc` 876 → 875). As contagens por fonte
> continuam iguais (`erp = raw`: `Cte_Peca` 300.601, `CTE_Baixa` 282.267, `Car_Itens_Pedido`
> 41.479) e, com o warehouse recarregado, a divergência cai de 12 para 2 → é **churn do ERP**, não
> regra portada. Enquanto o time de estoque ajusta `Cte_Peca`, a paridade de rolos/ccustos oscila em
> ~1% das comparações; o sinal para suspeitar de regra é a divergência **persistir após
> `bootstrap --dominio <dominio>`**.

---

## 6. Autenticação, escopos e empresa (D5/D6)

Decisão: **auth nova**, JWT + bcrypt, **fora** de `public.usuario` (as 4 contas legadas têm senha de
60 caracteres que não é bcrypt — nenhum hash compatível). Usuários, papéis, escopos e empresa ficam
no schema próprio `auth` do **Neon** (`app/alembic/versions/1002_neon_auth.py`, escopos em
`1004_neon_escopos.py`).

| Rota | Método | Auth | Contrato |
|------|--------|------|----------|
| `/auth/login` | POST | pública | corpo `{email, senha}` → `{"token", "tipo": "Bearer", "expira_em_minutos", "papeis", "empresa", "escopos"}`; `401` com `detail` genérico (não revela se o e-mail existe) |
| `/auth/eu` | GET | `Bearer` | `{id, email, nome, papeis, empresa, admin, escopos}` |
| demais 14 + `/health` | — | `Bearer` + escopo por tela (abaixo); `/health` segue pública | `401` sem token / inválido / usuário inativo; `403` sem escopo ou accessing outra empresa |

- **Senha**: bcrypt (custo 12), mínimo de 8 caracteres; `usuarios.salvar` faz upsert por `email`.
- **Token**: HS256, `exp` = `JWT_TTL_MINUTOS` (30), `iss` fixo; `sub` = `id`. O `Settings` **recusa
  subir** com `JWT_SECRET` < 32 bytes quando a auth está exigida (o PyJWT rejeita chave curta).
- **Papéis**: `text[]` (`admin`, `leitura`). É o corte **grosso**: `admin` passa em tudo,
  `leitura` não tem restrição própria.

### 6.1 Escopos por tela (o corte que os papéis não dão)

O ERP controlava acesso por `Usuario_Acessos` (Sistema × Tópico — Estudo 41) e o produto precisa do
mesmo: quem é comercial não enxerga o *programed* financeiro. Com dois papéis isso não sai, então
cada tela declara o escopo que exige (`app/src/api/auth/escopos.py`):

| Escopo | Telas |
|--------|-------|
| `estoque:leitura` | `/dados`, `/sugestao-rolos/{pedido}`, `/pdf/sugestao-rolos/{pedido}` |
| `faturamento:leitura` | `/faturamento/{data}`, `/faturamento-dia/{data}`, `/descontos/{data}`, `/devolucoes/{data}`, `/estornos/{data}` |
| `financeiro:leitura` | `/contas-pagas/{data}`, `/contas-receber-programado`, `/contas-pagar-programado`, `/custos-administrativos-anual`, `/custos-administrativos-mensal` |
| faturamento **e** financeiro | `/dashboard-completo/{data}` — a tela soma os dois grupos, então exige os dois |

Regras: `auth.usuario.escopos` entra com `'{}'` (**falha fechada** — quem é criado sem `--escopos`
recebe `403` em toda tela de negócio); `admin` e o curinga `'*'` passam em tudo; `/auth/login` e
`/auth/eu` acceptam qualquer usuário autenticado (é onde a tela descobre o que pode ver).
`usuarios.salvar` **recusa escopo desconhecido** em vez de gravar lixo.

### 6.2 Corte por empresa

O ERP não filtra por empresa na `SELECT`: ele filtra por **RLS no login SQL**
(`microdata.Empresas` usa `SIS_UsuarioEmpresa` — Estudo 42 §7), ou seja, o servidor rodava *dentro*
da empresa do usuário e o número legado já era o da empresa, não do grupo. Aqui o corte é no `where`,
usando o `Codigo_Empresas` (`char(2)`) que o token carrega:

- `/dados`, `/sugestao-rolos/{pedido}` e `/pdf/sugestao-rolos/{pedido}` aplicam `empresa = <token>`
  (`empresa_efetiva`); pedido de outra empresa responde lista vazia / `404`, como pedido inexistente;
- `?empresa=XX` é aceito **só pelo `admin`** (uma empresa por vez) para conferir a outra — usuário
  comum que peça outra recebe `403`;
- usuário sem empresa no token é transversal (admin): sem filtro;
- os KPIs **não** levam empresa no `where`: os marts já são agregados pelas empresas `13`/`14`
  (`core.empresa_faturamento`) e filtrar quebraria a paridade com o legado — o corte de empresa
  nessa família é de **escopo**, não de linha.

### 6.3 Operação

- **CLI**: `python -m src.cli usuario --email ... [--senha ...] [--papeis leitura,admin]
  [--empresa 13] [--escopos estoque:leitura,faturamento:leitura,financeiro:leitura] [--inativo]`, e
  `--listar` para inventariar (mostra empresa e escopos). Sem `--senha` gera uma senha aleatória e
  mostra **uma vez**.
- **Desligar a auth** só para carga local/validador: `API_AUTENTICACAO_EXIGIDA=false`
  (`scripts/validar_api.py` já faz isso; a suíte de testes usa `tests/conftest.py`). O padrão é
  `true` — **fail closed**: sem usuário no Neon, tudo responde `401`. Desligar a auth também
  desliga o corte por escopo, mas **não** o de empresa, que vem do `where`.

Testes: `app/tests/test_auth.py` (bcrypt, token forjado/adulterado/sem `sub`, 401 por token
inválido, usuário inexistente, inativo, `/health` público, `admin` x papel) e
`app/tests/test_escopos_empresa.py` (matriz das **14 telas**: sem escopo `403`, com o escopo certo
`200`, escopo alheio `403`, `admin` passa em todas; corte por empresa com token `99` e via
`?empresa=`; paridade — o número com token é igual ao lido sem auth). Cobertura ponta a ponta feita
contra o Neon com 4 usuários descartáveis (completo, só estoque, nenhum escopo e admin), removidos
depois.

---

_Base: `docs/legado/oraculum/src/main.py`, `database.py`, `main_funcional_01.py`,
`database/uspEnderecamentoParaAtenderPedidoGera.sql`; defs de `DBProDash` (`sys.sql_modules`).