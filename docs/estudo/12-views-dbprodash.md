# Estudo 12 — Views do DBProDash (portando a regra de negócio)

Levantado em **SELECTs somente leitura** sobre as definições (`sys.sql_modules`) e contagens das
views. **Sem executar** procedures de escrita. Sem valores reais.

Complementa o Estudo 11: aqui abrimos cada view/procedure de dashboard para decidir se a **nova
API lê o `DBProDash`** ou **reimplementa a regra contra `DBMicrodata_DGB`**.

## 1. Resumo executivo

- As views do `DBProDash` são **finas e 100% derivadas** de tabelas de `DBMicrodata_DGB`
  (referências `cross-database`); nenhuma tem dado próprio.
- **Podem ser portadas** para consultas (linhas de SQL parametrizadas) na nova API — o que evita
  depender do segundo banco.
- **Única exceção:** o centro de custo. `DBProDash.dbo.Rel_CCusto_Niveis` é uma tabela
  **materializada** por uma procedure que faz `TRUNCATE + INSERT` (escrita). A API legada a
  executa; a nova API somente-leitura **não pode**. É preciso ler a tabela pronta ou portar
  `sp_PagRel_CCusto_Niveis` (40 680 caracteres / 24 parâmetros).
- Encontradas **2 procedures quebradas/incompletas** e um `UNION` redundante (detalhes na Seção 4).

## 2. Mapa: view do DBProDash → tabelas de `DBMicrodata_DGB`

| View (DBProDash) | Tabelas de origem | Uso |
|------------------|-------------------|-----|
| `vwFaturamento` | `Fat_Pedido`, `Fat_Itens_Pedido`, `Clientes_Principal`, `Fat_Nat_Pedido`, `Produtos_Tecidos` | faturamento, desconto, custos |
| `vwContasPagas` | `Pag_Baixas` | contas pagas do mês |
| `vwContasPagasCentroCusto` / `..Mensal` | `DBProDash.Rel_CCusto_Niveis` | custo adm/armazenagem |
| `vwListagemDeEstornos` | `Fat_Pedido`, `Fat_Itens_Pedido`, `Fat_Nat_Pedido`, `Fat_Vend_Pedido`, `Rec_Vendedores`, `Car_Cores`, `Car_Situacoes` | estornos |
| `vwListagemDeEntradasSaidasPorCFOP` | `Liv_Entradas`/`Liv_EntProd`/`Liv_EntNatOp`, `Liv_SaidAS`/`Liv_SaiProd`/`Liv_SaiNatOp`, `Liv_Natureza`, `liv_parametros` | devoluções |
| `vwFinanceiroContasReceber` | `VW_Rec_DuplicatasEmAberto` (DBMicrodata) | contas a receber |
| `vwFinanceiroContasPagar` | `VW_Pag_Titulo_Aberto` (DBMicrodata) | contas a pagar |

### 2.1 `vwFaturamento` — o núcleo (32 450 linhas)

Granularidade: **item faturado** (`Empresa+Pedido+Item`) de notas emitidas. Sempre filtra:

```
Fat_Pedido TP  ⋈ Fat_Itens_Pedido TIP (Empresa,Pedido)
                ⋈ Clientes_Principal CP (Codigo_Cliente = TP.Cliente)
                ⋈ Fat_Nat_Pedido NP (Empresa,Pedido)
WHERE TP.Flag_Emitido = 1
  AND TP.Tipo_Pedido = '1'                       -- venda
  AND TP.Empresa IN ('13','14')
  AND TIP.Base_Calc <> '-'
  AND (NP.NatOp, NP.Seq) IN (whitelist ~100 CFOPs/sequências válidas)
```

- `Metros` = `CASE Base_Calc`: `'P'` ou `'M'` → `Metros`; senão → `Qtde`.
- Saída: `Empresa, Pedido, Cliente, Nosso_Pedido, Cod_Produto, Nr_Nota, Data_Nota, Vr_Nota,
  Metros, Vr_Unitario, Vr_Total, Empresa_Auxiliar, Peso, Acres_Desc, Nome_Cliente, Item`.
- **É a regra de faturamento oficial** — a whitelist de CFOP deve virar constante/configuração.

### 2.2 `vwListagemDeEstornos` (44 linhas)

`Fat_Pedido ⋈ Fat_Itens_Pedido (Base_Calc<>'-') ⋈ Clientes_Principal ⋈ Fat_Nat_Pedido
⋈ Fat_Vend_Pedido ⋈ Rec_Vendedores ⋈ Car_Cores ⋈ Car_Situacoes`, com
`Tipo_Pedido = '4'`, `Flag_Emitido='1'`, `Empresa='13'` e
`LTrim(RTrim(NatOp))+Seq IN ('1.1021','2.1021')` (CFOP de **estorno**).

> **Armadilha:** o corpo tem **dois `SELECT` idênticos unidos por `UNION`** (não `UNION ALL`).
> Como as duas metades são iguais, o `UNION` remove as duplicatas e o resultado sai correto — mas
> é código redundante (metade do UNION é inútil). Ao portar, basta **um** SELECT.

### 2.3 `vwListagemDeEntradasSaidasPorCFOP` (14 056 linhas)

`UNION ALL` de duas agregações por `(Empresa, Documento, NatOp-Sq, Data)`:

- **Entradas** — `Liv_EntradAS ⋈ Liv_EntNatOp ⋈ Liv_Natureza LEFT JOIN Liv_EntProd`;
- **Saídas** — `Liv_SaidAS ⋈ Liv_SaiNatOp ⋈ Liv_Natureza LEFT JOIN Liv_SaiProd`.

Ambas `Empresa IN ('13')`, `Tipo` = `'E'`/`'S'`, `Nova_CFOP = NatOp+'-'+Seq`. Soma valores
contábeis (BC/ICMS/IPI/PIS/COFINS/ST). `Vr_CONtabil` das **entradas** ainda soma
`vr_obs_ipi` **se** `(SELECT obs_ipi FROM liv_parametros) = 'S'` — hoje **`'N'`** (ajuste inativo).

### 2.4 Contas a receber / a pagar

Views-espelho triviais (só recortam empresas `13/14`) das canônicas do `DBMicrodata_DGB`:

- `vwFinanceiroContasReceber` → `VW_Rec_DuplicatasEmAberto` (`QtdeDoc = Nota_Fiscal`,
  `ValorTotal = Valor`, `Vencimento`); saldo = `Σ Valor_Parcelas − Σ Rec_Baixas.Valor_Liquido`,
  `HAVING saldo > 0`.
- `vwFinanceiroContasPagar` → `VW_Pag_Titulo_Aberto` (`QtdeDoc = Documento`,
  `ValorTotal = Valor_Parcela`); `Total_Doc, Valor_Parcela, Valor_Baixas (=Σ Valor_Liquido),
  Valor_Saldo, Dias (=DateDiff(vcto, hoje)), CodigoBarra, Id_SisPag, Referente`;
  `HAVING Σ Valor_Liquido < Valor_Parcela`.

> `vwContasPagas` (8 603 = total de `Pag_Baixas`) usa `Pag_Baixas.Valor_Pago`; já
> `VW_Pag_Titulo_Aberto` usa `Pag_Baixas.Valor_Liquido`. São colunas distintas — a diferença
> (juros) precisa ser tratada nos endpoints de "pago" × "saldo".

## 3. Procedures — contrato de saída

| Procedure | Lógica | Saída |
|-----------|--------|-------|
| `uspFaturamento @data` | `Σ(Vr_Total)+Σ(Acres_Desc)` de `vwFaturamento` no **mês** de `@data` (`EOMONTH(@data,-1)+1` → `EOMONTH(@data)`) | `Faturamento` |
| `uspFaturamentoDia @data` | idem, no **dia** `@data` | `Faturamento` |
| `uspDesconto @data` | `Σ(Acres_Desc)` no mês | `Desconto` |
| `uspDevolucao @data` | `Σ(Vr_CONtabil)` onde `Nova_CFOP IN ('1.201-1','1.201-2','1.202-1','2.202-1')` no mês | `Devolucao` |
| `uspEstorno @data` | `Σ(Vr_Nota)` de `vwListagemDeEstornos` no mês | `Estorno` |
| `uspListagemBaixasPagar @data` | `Σ(ValorPago)` de `vwContasPagas` no mês | `ContasPagas` |
| `uspDashFinanceiroContasReceberProgramado` | `COUNT(QtdeDoc), Σ(ValorTotal)` de `vwFinanceiroContasReceber` com `Vencimento >= 1º do mês corrente` e `<= 2050-12-31` | `QtdeDoc, ValorTotal` |
| `uspDashFinanceiroContasPagarProgramado` | idem, `COUNT(DISTINCT Documento)` de `vwFinanceiroContasPagar` | `QtdeDoc, ValorTotal` |
| `uspCustoAdmArmFat` / `..Mensal` | ver 3.1 | `Faturamento, Administrativo, Armazenagem=0, Porc_*` |
| `uspRel_CCusto_NiveisAnual` / `Mensal` | **materializa** `Rel_CCusto_Niveis` via `sp_PagRel_CCusto_Niveis` (escrita) | — |

### 3.1 `uspCustoAdmArmFat` (anual e mensal)

- `Faturamento` = Σ dos **12 meses fechados** (mês corrente − 12 .. − 1) ÷ 12 (anual) ou do
  **último mês fechado** (mensal).
- `Administrativo` = `Σ Valor_Baixado` de `vwContasPagasCentroCusto` onde
  `Codigo_Departamento IN ('1.1.1.1','1.1.1.2')`. Na proc **anual** divide por 12 (acumulado
  histórico ÷ 12); na **mensal** não divide —quirk medido (02/out/2026).
- **`Armazenagem` é retornado como `0` fixo** e `Porc_Armazenagem = 0` fixo — não há cálculo de
  armazenagem nessas procs. `Porc_Administrativo = Administrativo / Faturamento`.
- Retorna também versões string com vírgula (`*_Replace`).

## 4. Achados / armadilhas para o porte

1. **Escrita escondida no dashboard:** `uspRel_CCusto_NiveisAnual/Mensal` fazem
   `TRUNCATE TABLE Rel_CCusto_Niveis` + `INSERT ... EXEC sp_PagRel_CCusto_Niveis`. A API legada
   **escreve** no banco; a nova API read-only não deve. `Rel_CCusto_Niveis` (1 842 linhas, 42
   colunas: `CodDesp1..3`, `CodDpto1..4`, `Valor_Baixado`, `Data_Baixa`, `MesAno`, `Referente`…)
   é um **snapshot** — fica com os dados da última execução da proc.
   **Portado (02/out/2026, rev `0011`):** a tabela virou fonte do ETL
   (`DBProDash.dbo.Rel_CCusto_Niveis` → `raw.rel_ccusto_niveis`, 1 842/1 842) e a regra virou
   `core.custo_baixas` + `marts.custos_por_departamento_mensal` +
   `marts.custos_administrativo_mensal` — opção A daqui. A chave natural exige `CodFornecedor`
   (sem ele 8 grupos repetem e a carga perde 8 linhas). As colunas `Tot*` são totais de janela do
   legado: nunca somar.
2. **`uspCustoAdministrativoArmazenagemPorFaturamento` está quebrada:** filtra por
   `Codigo_Custo`, coluna que **não existe** em `vwContasPagasCentroCusto` (que expõe
   `Codigo_Despesa`/`Codigo_Departamento`). Provável resíduo de refatoração.
3. **`vwContasPagasCentroCusto` e `vwContasPagasCentroCustoMensal` são idênticas** (ambas leem
   `Rel_CCusto_Niveis` sem filtro); o recorte mensal/anual é feito nas procs. As duas expõem só
   **7 colunas** (`Codigo_Despesa`, `Codigo_Departamento`, `Valor_Baixado`, `Data_Baixa`,
   `MesAno`, `Referente`, `Razao_Nome_Cliente`) — o rateio por nível vive na tabela, não na view.
4. **Escalas diferentes:** custo **anual** divide o faturamento dos 12 meses fechados por 12 e o
   administrativo histórico por 12; **mensal** não divide nenhum dos dois (e o faturamento é só do
   último mês fechado), então o `Porc_Administrativo` mensal passa de 100%. Validar se é o desejado.
5. **"Programado"** começa no 1º dia do **mês corrente** (`Vencimento >= 1º do mês corrente`, até
   2050-12-31) — ou seja, inclui os vencidos do próprio mês. Receber usa `COUNT(*)` das linhas,
   pagar `COUNT(DISTINCT Documento)`. Medido em 02/out/2026 (antes este estudo dizia "mês seguinte").
6. **`Pag_Baixas.Valor_Pago` × `Valor_Liquido`** (ver 2.4) e `Juros_Pagos` existem — decidir qual
   entra em cada métrica.
7. `liv_parametros.obs_ipi = 'N'` → o ajuste de IPI das entradas está desligado hoje (manter a
   condicional para não divergir se religarem).

## 5. Recomendação de arquitetura

- **Portar as views para consultas próprias** contra `DBMicrodata_DGB` (não depender do
  `DBProDash`): `vwFaturamento`, `vwListagemDeEstornos`, `vwListagemDeEntradasSaidasPorCFOP`,
  e as duas canônicas `VW_*`. São SQL determinístico, sem dado próprio.
- **Centro de custo:** opção A — ler `DBProDash.dbo.Rel_CCusto_Niveis` (exige conceder leitura
  também no `DBProDash`, e aceitar o *delay* do snapshot); opção B — portar
  `sp_PagRel_CCusto_Niveis` como consulta (trabalho alto: 40 KB de T-SQL).
  **Recomendação:** começar por **A** (rápido, dados já prontos) e planejar **B** se for preciso
  tempo-real.
  **Feito (02/out/2026):** A, como fonte do ETL — o espelho `raw` é a carga idempotente, lida do
  mesmo banco pelo mesmo pipeline (`qualificar_sql_server` monta `[DBProDash].[dbo].[X]`).
  B continua disponível se aparecer exigência de tempo real.
- Expor as constantes de negócio em configuração: **whitelist de CFOP** do faturamento,
  **CFOPs de devolução**, departamentos/despesas de custo (`1.1.1.1`…, `2.3.1`), empresas
  (`13`,`14`) e o parâmetro `obs_ipi`.

## 6. Contagens de apoio

- `vwFaturamento` **32 450** · `vwListagemDeEntradasSaidasPorCFOP` **14 056** ·
  `vwListagemDeEstornos` **44** · `vwContasPagas` **8 603**.
- `DBProDash.Rel_CCusto_Niveis` **1 842** (42 colunas).
- `sp_PagRel_CCusto_Niveis`: 24 parâmetros, definição de **40 680** caracteres.
- `Pag_Baixas`: colunas de valor = `Valor_Pago`, `Valor_Liquido`, `Juros_Pagos`.
