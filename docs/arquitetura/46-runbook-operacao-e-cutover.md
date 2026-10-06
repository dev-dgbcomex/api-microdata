# Runbook de operação e cutover

O que fazer com a API **ligada**: como rodar o ETL, como publicar no Neon, o que monitorar e como
entregar o `dgbcomex`. Complementa o [Doc 44 (contratos)](./44-contratos-api-oraculum.md) — que
descreve *o que* a API devolve — e o [Doc 45 (plano)](./45-plano-de-implementacao.md) — que diz
*por que* ela é assim.

Medido em **05/out/2026** nesta máquina (Postgres 17 local + Neon `sa-east-1` + ERP em
`10.156.0.124`). Números concretos estão medidos, não estimados; onde são estimativa está escrito.

---

## 1. As três peças e quem manda em cada uma

| Peça | Onde | Quem escreve | Quem lê |
|------|------|---------------|---------|
| ERP `DBMicrodata_DGB` | SQL Server on-prem `10.156.0.124` | Microdata (e o ERP da DGB) | **ninguém escreve** — a API só faz `SELECT` |
| **Warehouse** `dgbcomex_warehouse` | Postgres 17 local, `localhost:5432` | o ETL desta aplicação (`raw`/`core`/`marts`/`etl`) | a API **e** o `dgbcomex`, se apontado para cá |
| **Neon** `dgbcomex` | `ep-still-mouse-...sa-east-1.aws.neon.tech` | esta aplicação, só em `marts`/`etl`/`auth` | o `dgbcomex` (Next.js) lê direto |
| API | `uvicorn src.api.main:app`, porta `58244` | — | o `dgbcomex` chama por HTTP |

O ERP é **fonte somente leitura**. A conexão passa por `src/db/erp.py`, que rejeita tudo que não seja
`SELECT`/`WITH` ou `EXEC` de uma das procedures de `PROCEDURES_SOMENTE_LEITURA`. Isso não é
convenção: é o que impede a API de escrever no banco de produção.

O warehouse local é o **ativo novo** e precisa de backup próprio (Doc 45 §9). O Neon é **derivado**:
se ele se perde, `bootstrap` reconstrói os marts a partir do warehouse.

---

## 2. Ligar a API

```powershell
cd M:\dev\api-microdata\app
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 58244
```

Sempre a partir de `app/`: o `.env`, o `alembic.ini` e o `pyproject.toml` são resolvidos a partir
dali (`APP_DIR` em `src/config.py`). `PYTHONPATH=.` é o que faz `import src` achar o pacote.

**Antes de chamar qualquer rota de negócio**, tem que existir usuário no Neon — a auth é falha
fechada por padrão (`API_AUTENTICACAO_EXIGIDA=true`, Doc 44 §6):

```powershell
.\.venv\Scripts\python.exe -m src.cli usuario --email fulano@dgb.com.br --empresa 13 `
  --escopos faturamento:leitura,financeiro:leitura
.\.venv\Scripts\python.exe -m src.cli usuario --listar
```

Sem `--senha` o CLI gera uma senha aleatória e mostra **uma vez**.

Conferir que subiu: `GET /health` responde `200` com `ok: true` e o bloco `etl.ultima` — se `etl`
estiver ausente, o warehouse está vazio e os KPIs viram lista vazia em vez de erro.

---

## 3. Rodar o ETL

### 3.1 Os comandos

```powershell
# 0. antes de qualquer carga: a coluna de watermark ainda é utilizável no ERP?
.\.venv\Scripts\python.exe -m src.cli check           # exit 1 se alguma watermark regrediu

# 1. carga (o caminho normal é incremental)
.\.venv\Scripts\python.exe -m src.cli bootstrap --tudo --incremental

# 1b. um domínio só (o que se faz enquanto o ERP está no ar)
.\.venv\Scripts\python.exe -m src.cli bootstrap --dominio estoque --incremental

# 1c. carga cheia, sem watermark — é a reconciliação, não o dia a dia
.\.venv\Scripts\python.exe -m src.cli bootstrap --tudo

# 2. conferir ERP x raw (o bootstrap já imprime; exit 1 se divergir)
# 3. publicar os agregados no Neon
.\.venv\Scripts\python.exe -m src.cli publicar-neon --status
```

O `bootstrap` imprime ao fim a tabela de conferência `erp=x raw=y` por fonte e **sai com 1** se
alguma divergir. `check` é o guarda-corpo do `--incremental`: se alguém no ERP começar a esvaziar a
coluna de watermark, o `check` reprova antes de a carga silenciosamente pular linhas.

### 3.2 Estratégia por fonte (46 fontes)

| Estratégia | Nº | O que faz | Quando aparece |
|-----------|-----|-----------|----------------|
| `full` | 25 | apaga e recarrega a tabela inteira | 5 tabelas **sem** coluna de alteração utilizável (`Fat_Pedido`, `Fat_Itens_Pedido`, `Car_Pedido`, `Car_Itens_Pedido`, `Clientes_Principal` — Doc 45 §9) |
| `recarrega_pai` | 11 | recarrega o pai e os filhos do documento | itens, parcelas e baixas |
| `watermark` | 7 | só o que mudou desde `etl.watermark` | o resto |
| `reconciliacao` | 3 | full **e** apaga o que sumiu no ERP | necessário para o faturamento (foi o pedido `013290`, excluído no ERP, que inflava a receita) |

Por domínio: cadastros 12, estoque 9, fiscal 7, custos 6, contas a pagar 5, faturamento 4, contas a
receber 3.

O `--tudo` **recarrega tudo** e é caro: a última carga completa somou **~3.700s (61min)** em 116
tabelas. As três que dominam esse tempo são `Fat_Itens_Pedido` (~193s, 101k linhas), `Cte_Peca`
(~184s, 301k) e `CTE_Baixa` (~52s, 277k). O dia a dia é `--tudo --incremental`; um `--tudo` full só
para reconciliação. O que **não** se faz é `--tudo` full em horário comercial.

### 3.3 Incremental é barato, mas não de graça

Medido: `bootstrap --dominio cadastros --incremental` leva **11s** e `--dominio faturamento
--incremental` leva **199s**. O incremental não elimina o custo das tabelas `full` — elas recarregam
inteiro de qualquer forma. O que ele economiza são as `watermark`.

---

## 4. Publicar no Neon

```powershell
.\.venv\Scripts\python.exe -m src.cli publicar-neon            # só o que defasou
.\.venv\Scripts\python.exe -m src.cli publicar-neon --forcar   # republica os 7
.\.venv\Scripts\python.exe -m src.cli publicar-neon --status   # o que já está lá
```

- **Só publica se defasou**: a versão do warehouse é `max(fim)` das execuções `ok` em `etl.execucoes`;
  o Neon guarda `publicado_em` em `etl.marts_publicados`. Enquanto nenhuma fonte carregou depois da
  última publicação, o comando não faz nada.
- **Atomicidade**: `delete` + `insert` na mesma transação. O `dgbcomex` lê snapshot antigo ou novo,
  nunca mart pela metade.
- **7 marts, 4.880 linhas, ~2s**. Os dois marts de grão pesado (`core.estoque_pecas_em_aberto`,
  `core.pedido_sugestao_rolos`) **não** sobem — ficam só no warehouse local.
- **Automático** é opcional e está **desligado** por padrão (`NEON_PUBLICAR_AUTOMATICO=false`): só o
  `/dashboard-completo` chamaria a publicação. Para o cutover, o certo é o cron chamar — assim o dado
  no Neon tem hora certa, em vez de depender de alguém abrir a tela.

---

## 5. Agendamento

O warehouse é Postgres local e o ETL é um comando que roda **na máquina que alcança o ERP**. Como
o ERP está em `10.156.0.124` (rede on-prem), o runner precisa estar nessa rede — na prática, a
máquina de desenvolvimento até a Fase F.

### 5.1 A sequência

```
1. src.cli check                                (2s,  exit 1 aborta)
2. src.cli bootstrap --tudo --incremental       (minutos; 61min se for full)
3. src.cli publicar-neon                        (~2s, se defasou)
```

O passo 3 é o que faz o `dgbcomex` ver o dado novo. Se ele falhar, a API continua servindo do
warehouse local — a falha de sync não derruba KPI (Doc 44 §4).

### 5.2 Agendador

Windows (**Task Scheduler**, `Program Files\PostgreSQL\17` já instalado, serviço
`postgresql-x64-17` rodando):

```
Program:  M:\dev\api-microdata\app\.venv\Scripts\python.exe
Argumentos: -X utf8 -m src.cli bootstrap --tudo --incremental
Start in: M:\dev\api-microdata\app
Schedule:  diária, fora do expediente (ex.: 22:00)
Run whether user is logged on or not
```

`pg_dump` **não está no PATH** nesta máquina — o backup (§7) precisa do caminho completo
`C:\Program Files\PostgreSQL\17\bin\pg_dump.exe`.

Num host Linux seria crontab. O que importa é o `PYTHONPATH=.` e o diretório de trabalho, porque o
CLI resolve `.env` e `alembic.ini` a partir de `app/`.

---

## 6. Monitoramento

`GET /health` é o painel — é público por decisão de projeto (não exige token, para o monitor não
depender de login) e responde `503` se o warehouse local ou o Neon não responderem.

```json
{
  "ok": true,
  "warehouse_local": {"ok": true, "schemas": ["core","etl","marts","public","raw"]},
  "neon": {"ok": true, "schemas": ["auth","etl","marts","public"]},
  "erp": {"ok": true, "servidor": "10.156.0.124", "banco": "DBMicrodata_DGB"},
  "etl": {"ok": true, "ultima": {"tabela": "...", "status": "ok", "fim": "...", "linhas": 0}},
  "neon_publicacao": {"ok": true, "automatico": false, "defasados": [], "publicados": {...}}
}
```

O que olhar:

| Sinal | Onde | O que significa |
|-------|------|----------------|
| `ok: false` / HTTP 503 | topo | warehouse ou Neon fora do ar — **as rotas de negócio caem** |
| `etl.ultima.status != "ok"` | `etl` | a última carga falhou; `etl.erros` tem a mensagem |
| `neon_publicacao.defasados` não vazio | `neon_publicacao` | o `dgbcomex` está com dado velho — rodar `publicar-neon` |
| `etl.ultima.fim` antigo | `etl` | o agendador não rodou |

`src.cli health` mostra o mesmo para as três bases, em linha de comando, sem subir a API — é o que o
agendador deve checar depois de cada passo.

Histórico para quando o `/health` já foi rotacionado: `etl.execucoes` (uma linha por fonte, com
`status`, `duracao_s`, `linhas`) e `etl.erros`. Estado atual: **177 execuções `ok`, 4 `parcial`,
19 `erro`** — os erros são de 05/out durante o ajuste do `Fat_Itens_Pedido`, não de hoje.

---

## 7. Backup e recovery

O warehouse local é o ativo novo (Doc 45 §9) e o ERP **não** guarda histórico do que foi excluído —
se o `raw` perder uma linha que o ERP já apagou, ela não volta por carga incremental.

```powershell
& "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" -h localhost -p 5432 -U postgres `
  -Fc -f C:\backup\warehouse_$(Get-Date -Format yyyyMMdd).dump dgbcomex_warehouse
```

`pg_dump` consistente sem parar o serviço; `-Fc` deixa o `pg_restore` picky. O `pg_basebackup` do
PG17 também serve, mas exige parar ou `pg_basebackup` com servidor primário — para este volume
(693MB) o `pg_dump` diário é suficiente.

Restore: `pg_restore -d dgbcomex_warehouse --clean --if-exists <arquivo>`.

O Neon não precisa de backup próprio: os `marts.*` saem do warehouse por `publicar-neon`. O que
**não** se recria é o schema `public` do `dgbcomex` (118 tabelas) — esse é do produto e o Neon faz
backup gerenciado.

---

## 8. Entregar o `dgbcomex`

Este é o item que fecha a Fase F e é **decisão de produto + trabalho no outro repositório**
(`M:\dev\dgbcomex`, Next.js/Vercel), não desta API. O que a API precisa entregar de contrato:

**O `dgbcomex` lê `marts.*` do Neon direto** (drizzle/Prisma já apontam para lá) — não passa pela
API. As 7 tabelas e colunas que ele pode usar:

| Tabela no Neon | Grão | Colunas |
|----------------|------|---------|
| `marts.faturamento_diario` | dia | `data, itens, pedidos, notas, clientes, metros, vr_total, desconto, faturamento, vr_nota, peso` |
| `marts.contas_pagas_diario` | dia | `data, baixas, documentos, fornecedores, valor_pago, juros_pagos, desconto_recebido, valor_liquido` |
| `marts.custos_administrativo_mensal` | mês | `mes, parcelas, valor_baixado` |
| `marts.devolucoes_diario` | dia×cfop | `data, tipo, documentos, valor` |
| `marts.estornos_diario` | dia | `data, itens, pedidos, notas, valor_nota, valor_itens, acres_desc` |
| `marts.financeiro_receber_programado` | título | `qtde_doc, valor_total, vencimento` |
| `marts.financeiro_pagar_programado` | título | `qtde_doc, valor_total, vencimento` |

O mapa de coluna está em `src/etl/publicar.py: COLUNAS` e é a **única** fonte de verdade: mudar uma
coluna de mart sem atualizar esse dicionário faz o `tests/test_publicar_neon.py` falhar de propósito,
em vez de o front receber um mart silenciosamente incompleto.

O schema `public` do `dgbcomex` **não se toca** — a API só escreve em `marts`, `etl` e `auth`.

**Ordem sugerida do cutover**

1. `publicar-neon --forcar` e conferir que os 7 marts estão no Neon com `publicado_em` recente.
2. No `dgbcomex`, apontar a tela de dashboard para `marts.*` e comparar com a tela atual.
3. **Período de sombra**: deixar as duas telas no ar 1–2 semanas, lado a lado, e rodar
   `scripts/validar_api.py` e `scripts/validar_fase_d.py` a cada carga.
4. Só então desligar o `oraculum` e parar de usar `DBProDash` como fonte. **Não apagar** o
   `DBProDash` — pode haver consulta ocasional; só deixa de ser fonte.

Critério de aceite: `dgbcomex` com dashboard servido do Neon, warehouse local completo, legado
desligado sem perda de tela.

---

## 9. Validação antes do cutover

```powershell
# API x procedures do ERP, em todas as rotas
.\.venv\Scripts\python.exe -m scripts.validar_api
# marts do warehouse x DBProDash
.\.venv\Scripts\python.exe -m scripts.validar_fase_d
```

Os dois saem com 1 na primeira divergência. O acesso ao ERP passa pelo filtro read-only (§1).

> **O ERP é vivo e o churn é real.** Entre a carga e a comparação o ERP muda: em 05/out, durante uma
> mesma sessão, o `DBProDash` devolveu valores diferentes para chaves que antes batiam, *sem recarga
> do warehouse* — peça `0000816455/001` foi de `41,1000` para `41,0000` m, e uma duplicata de contas a
> receber mudou de 876 para 875 documentos. As contagens por fonte seguem batendo (`erp = raw`:
> `Cte_Peca` 300.601, `CTE_Baixa` 277.285, `Car_Itens_Pedido` 41.479). última rodada:
> **296/300** no `validar_api` e **2201/2223** no `validar_fase_d`.
>
> **Como ler uma divergência**: recarregue o domínio e rode de novo
> (`bootstrap --dominio <dominio> --incremental`). Se a divergência **persistir** depois da recarga,
> é regra portada errada e vale investigar. Se sumir, era churn. Detalhamento em `docs/arquitetura/44`
> §5.

---

## 10. O que fazer quando dá errado

| Sintoma | Causa provável | O que fazer |
|---------|----------------|-------------|
| `401` em tudo | não existe usuário no Neon | `src.cli usuario --listar`; criar com `--escopos` |
| `403` em uma tela | falta o escopo daquela tela | `--escopos` inclui o grupo (Doc 44 §6.1) |
| `403` pedindo `?empresa=` | só `admin` troca de empresa | sem empresa no token = transversal |
| `[]` ou `404` em `/dados` | empresa do token não é a 13 | o warehouse só tem empresa 13 |
| `/health` com `503` | warehouse ou Neon fora | `src.cli health` diz qual dos dois |
| KPI vazio mas warehouse cheio | `NEON_PUBLICAR_AUTOMATICO` desligado e ninguém rodou o sync | `publicar-neon` |
| `check` exit 1 | coluna de watermark regrediu no ERP | voltar a `full`/`recarrega_pai` em `sources.py` |
| `bootstrap` exit 1 com contagem divergente | o ERP mudou durante a carga | recarregar o domínio; se persistir, é regra |
| API não sobe: `JWT_SECRET` curto | auth exigida e segredo com < 32 bytes | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| API não sobe: `ModuleNotFoundError: src` | rodou de outro diretório | `cd app` e `PYTHONPATH=.` |

---

## 11. Estado em 05/out/2026

- Warehouse local `dgbcomex_warehouse`: 46 fontes carregadas, **693MB**, raw maior = `cte_peca`
  (217MB / 300.601 linhas).
- Neon `dgbcomex`: 7 marts publicados (4.880 linhas), `auth.usuario` com a coluna `escopos`
  (migration `1004`), `public` do `dgbcomex` com 118 tabelas — intocado.
- API: 17 rotas (14 de negócio + `/health` + 2 de auth), `alembic` em `1004_neon_escopos`.
- Suíte: **192 testes passando**, ruff limpo.
- Pendente de decisão de produto (não é trabalho de código): itens 1–3 da Fase F no Doc 45 §7.

---

_Comandos medidos nesta máquina em 05/out/2026. Referências: Doc 43 (arquitetura alvo), Doc 44
(contratos), Doc 45 (plano), Estudo 22 (Neon/ETL), Estudo 41 (usuários/acessos)._