# Estudo 41 — Usuários, Acessos e Segurança (Usua / `Usuarios`, `usuario_*`, `Sistemas`, `Topicos`)

> **Data:** 19/set/2026
> **Escopo:** Autenticação e autorização do ERP (usuários internos, grupos, acessos por sistema/tópico), usuários do portal web (representantes), cadastro de sistemas e menu web, logs de acesso/sistema.
> **Restrições:** apenas leitura; sem objetos novos em produção; **atenção redobrada com senhas (não replicar esquemas vulnráveis)**.
> **Relações:** Estudos 03–40 (todos os módulos usam `Usuarios`/`Log_*`), `microdata.Empresas` (RLS por login), dgbcomex (portal web).

---

## 1. Identidade

Dois domínios distintos de usuário:

| Domínio | Tabela | Linhas | Uso |
|---------|--------|--------|-----|
| **Desktop ERP** (colaboradores) | `Usuarios` | 39 | login dos EXEs (SIMFatura, SIMRet, SIMTecidos...) |
| **Portal web** (representantes) | `usuario` | 35 | login do portal `meu Microdata` / dgbcomex (JSF) |

Camadas de apoio: `Sistemas` (catálogo de módulos), `Topicos` (itens de menu por sistema), `Usuario_Acessos` (ACL por usuário/tópico), `menu`/`menu_it` (menu web), logs.

---

## 2. Usuários do ERP (`Usuarios`, 39)

| Coluna | Tipo | Observação |
|--------|------|------------|
| `Cod_Usuario` | int | PK (não-IDENTITY; último usado 41) |
| `Nome_Usuario` | char(50) | nome do usuário/grupo |
| `Senha` | char(10) | **criptografia legada XOR Microdata** (bytes não-ASCII) |
| `senha_crypt` | varchar(40) | hash hex de 40 chars (estilo SHA-1) p/ usuários (`Tipo='U'`), preenchido p/ ~15 usuários |
| `Nivel` | smallint | 0 (master, 3) / 1 (normal, 36) |
| `Tipo` | char | `'U'` = usuário (35), `'G'` = grupo (4) |
| `Grupo` | int | associação a grupo (NULL na prática) |
| `Ativo` | char | `'S'`/`'N'` |

**Grupos (`Tipo='G'`):** EXPEDICAO(3), PRODUCAO(4), ADMINISTRATIVO(6), VENDAS(14) — usuários coletivos antigos, senha idêntica (`'\xDD'`).
**Ex.:** `Master` (1, Nivel 0), `ADMINISTRADOR` (2, Nivel 0, hash `df58248c...`), `meumicrodata` (5, portal), AUGUSTO(9), Leila(10), FATURA(15), ROOT(18)...

> **Segurança:** `Senha` = XOR legado (quebra trivial) e `senha_crypt` = hash sem salt aparente. **Não reutilizar** como autenticação da API nova — usar scheme moderno (bcrypt/argon2 + salt) e considerar a tabela apenas como identidade/origem dos nomes.

---

## 3. ACL do ERP (`Usuario_Acessos`, 19.748)

Linhas por usuário × sistema × tópico:

| Coluna | Tipo | Significado |
|--------|------|-------------|
| `Cod_Usuario` | int | usuário (FK → `Usuarios`) |
| `Cod_Sistema` | smallint | módulo (→ `Sistemas.Cod_Sistema`) |
| `Cod_Topico` | int | item de menu (→ `Topicos.Cod_Topico`) |
| `Habilitar` / `INCLUI` / `ALTERA` / `EXCLUI` | bit | permissões CRUD do tópico |
| `Direitos` | text | permissões extras em texto (campos etc.) |
| `CONSULTA` | int | nível de consulta |

Tópicos mais populados (sistemas): **250 SIMTecidos (3.341)**, 170 SIMFatura (2.393), 230 SIMCarteira (2.018), 100 SIMRec (1.627), 220 SIMRet (1.456), 140 SIMLivros (1.391), 510 SIMCMTextil (846)... **201 `meu Microdata`** (267).

---

## 4. Catálogo de sistemas (`Sistemas`, ~64)

Mapeia os módulos do ERP (confirmando os prefixos usados nos estudos 03–41):

| Cód | Sistema | Cód | Sistema |
|-----|---------|-----|---------|
| 18 | SIMPDV | 300 | SIMTinturaria |
| 100 | SIMRec (contas a receber) | 330 | SIMCompra |
| 110 | SIMPag (contas a pagar) | 340 | SIMPCP (confecção) |
| 120 | SIMFluxo | 360 | SIMFolha |
| 130 | SIMDireta (mala direta) | 390 | SIMCustos |
| 140 | SIMLivros (fiscal) | 410 | SIMFios |
| 150 | SIMBan | 420 | SIMReport |
| 160 | SIMContabilSPED | 460 | SIMInventário |
| 170 | SIMFatura (faturamento) | 480 | SIMCRM |
| 180 | SIMCheques | 490 | SIMConfeccao |
| 190 | SIMAtivo | 510 | SIMCMTextil (compras) |
| 200 | SIMAgenda | 560 | SIMSPED_Fiscal |
| 201 | **meu Microdata** (portal) | 561 | SIMPIS_COFINS |
| 210 | SIMPneus | 590 | SIMPlanoMestre |
| 220 | SIMRet (retaguarda almox) | 8107 | SIMContabilidade |
| 232 | SimCustoTextil | 8151 | LivrosMod3 |
| 240 | SIMVendas | 8212 | SIMFinanceiro |
| 250 | SIMTecidos (têxtil, o maior ACL) | 9014 | SIMConnect |

Colunas extras: `Path` (EXE em `U:\Microdata_Windows\...`), `Icone`, `Versao_Minima`, `menu`, `icone_web`, `grupo_sistema`, `Sistemas_Config`/`_Itens`.

---

## 5. Tópicos/menu desktop (`Topicos`) e menu web

- `Topicos` (Cod_Topico, Nome_Topico, Objeto_Topico = `item_<sistema>`, Sistema, Disponivel): itens de menu de cada EXE (ex. sistema 100: `item_Dolar`, `item_SQL`, `item_Cadastros`, `item_Clientes`, `item_Conceitos`, `item_Cond_Pagto`, `item_Contas`...). Cópias congeladas: `Topicos_20111221`, `Topicos_20120307`.
- `menu` (menu_id, nome = `customers`, `sellers`, `orders`, `invoicing`, `stock`, `administration`, `Informativos`...) + `menu_it` (menu_id, nome, acao, seq, mobile, **url JSF** — ex. `atendCli.jsf`, `maioresVendedoresConsulta.jsf`, `metaFaturamentoComissaoVendedorPontuacaoConsulta.jsf`, `front_end_cd=1`): **menu do portal web** (dgbcomex meumicrodata).
- `usuario_menu` (911): itens de menu habilitados por usuário do portal.
- `menu_acesso_rap`: atalhos.

---

## 6. Usuários do portal / representantes (`usuario`, 35) — RISCO

| Coluna | Observação |
|--------|------------|
| `usu_cd` | PK (não-IDENTITY) |
| `nome`, `login` | nome fantasia / login (ex. `super`, `raquel`, `alisson`) |
| **`senha` (varbinary)** | **senha em texto claro** (`b'PAGANINI'`, `b'Esmeralda852456#&'`, `b'123'`...) — fator crítico de segurança |
| `grupo_cd` | 1 = interno, 2 = (Raquel), 3 = representantes (maioria) |
| `cd_sis` | sistema de origem (ex. `'40'`, `'37'`, `'1'`) |
| `email`, `gerencia_documentos`, `chat_em_janela`, `analytics_url`, `emp_cd_principal` | perfil do portal |

Representantes cadastrados (grupo_cd=3): PAGANINI, CONSTANTINO, GULLI, PROFECTUS (BPACK), ABRANTES, JBREY, ANTONIO, VALERIA, JULIO (LAMARCA), RUI, FELIPE, JOTACE, FORMIGA, DAWSON, MARCIO, etc. — a rede comercial do portal.

Complementos: `usuario_empresa` (1 — usu 1 → emp `01`), `usuario_grupos` (0), `SIS_UsuarioEmpresa` (68 — vínculo usuário ↔ empresa p/ config), `SIS_EmpresaPadrao_Usuario` (9), `usuario_depto` (0), `Perfil_Notificacoes`/`perfil_grupos_interesse`.

> **Segurança:** senhas em texto claro na tabela `usuario` — legado do portal. Migração deve zerar/hashar essas senhas e mover autenticação para serviço próprio (OAuth/JWT + bcrypt) sem expor `usuario.senha`.

---

## 7. Perfis de cliente/representante participativo (`perfil_usuario`, 16)

Cadastro completo de perfil (nome, cpf/cnpj, isento, email, tipo/status, sexo, nascimento, endereço completo, representante, mensagem pessoal, telefones, `tipo_perfil`, `acesso_externo`) com auditoria `perfil_alteracao`/`datahora_alteracao`/`datahora_cadastro`. Usado pelo portal/CRM (relaciona-se ao Estudo CRM).

---

## 8. Logs de acesso e sistema

| Tabela | Linhas | Conteúdo |
|--------|--------|----------|
| `Log_Acesso` | 539 (retidos ~30d) | login: Sistema, Usuario, DataHora, Versao (ex. `25.2.174.0`), IPMaq, MacPc, NomePC (`MICRODATA-APP01`) — **ativo em set/2026** (310 registros); identity em 17.274 → limpeza periódica |
| `Log_Sistema` | 90.556 | por Sistema (170=31.075, 100=14.251, 110=13.430, 8151=11.413, 230=9.204...), Empresa, Documento, Via, Usuario, Observacao |
| `*_Log` (módulos) | — | dezenas de tabelas de auditoria por módulo (ex. `Cte_Peca_Log`, `PCP_Pedido_Log`, `Fat_*_Log`, `ctb*Log` contábeis, `Fnn_*LOG` financeiras) — mapeadas nos estudos de cada módulo |
| `Conn_Logs` / `Conn_LogExceptions` | — | log de conexões/exceções do EXE (estudo Infra quando aplicável) |
| `mic_*_Log` | — | logs do `SIMConnect` (mic_ChangeLog_Event / mic_ExecutionLog_Event) |

`rpt_log` (estudo 40) e `Sic_Ctrl_DocLog` (SIC) completam o conjunto de auditoria.

---

## 9. Observações para a nova API

1. **Autenticação**: não basear em `Usuarios.Senha` (XOR) nem `usuario.senha` (plano) — implementar auth própria (OAuth2/JWT + bcrypt/argon2). Usar `Usuarios` como identidade dos colaboradores e `usuario` como identidade dos representantes (portal) **após** hashar.
2. **Autorização**: o ERP usa `Usuario_Acessos` (Habilitar/INCLUI/ALTERA/EXCLUI) por (sistema, tópico). Para a API, mapear **escopos** a partir dos tópicos dos sistemas relevantes (ex. SIMFatura → `fatura:read/write`, SIMRec/SIMPag → `fin:...`). A coluna `CONSULTA` (int) regula o nível de consulta — ex. ver `microdata` RLS.
3. **Multiempresa**: `usuario_empresa`/`SIS_UsuarioEmpresa`/`emp_cd_principal` definem acesso por empresa; a API já projeta `microdata.Empresas` com RLS por `SUSER_NAME()` — avaliar se o token deve carregar o conjunto de empresas permitidas.
4. **Auditoria**: `Log_Acesso` retém ~30 dias (identity alto + purge) — para a API, criar trilha própria de auditoria ingestada no Neon (não ler `Log_*` do ERP para fins de compliance, apenas referência).
5. **Menu web (JSF)**: o portal atual (metadados em `menu`/`menu_it`) é o front que `dgbcomex` tende a substituir; o mapa nome⇄url JSF pode ser reaproveitado como requisito de telas da nova API.
6. **Catálogo `Sistemas`** serve de dicionário de "módulos" — útil para documentar quais tabelas pertencem a que sistema nas marts.

### 9.1 Como os itens 2 e 3 ficaram na API (D5/D6, 05/out/2026)

Os itens acima eram recomendações; a implementação escolheu um recorte pragmático, porque
`Usuario_Acessos` é ACL de **sistema × tópico** do ERP de desktop (250 tópicos só em SIMTecidos) e
não se traduz 1:1 para 14 rotas de API:

- **Escopos**: em vez de `fatura:read/write`, três nomes que correspondem às **telas** que o produto
  tem — `estoque:leitura`, `faturamento:leitura`, `financeiro:leitura` — declarados por router
  (`src/api/auth/escopos.py`, Doc 44 §6.1). São todos de leitura: a API alvo não escreve no ERP, então
  não há `write` para mapear. `admin` e `'*'` passam em tudo; ausência de escopo é `403`.
- **Nível de consulta (`CONSULTA`)**: não portado. Não há tela que peça menos que a leitura inteira do
  KPI; se surgir, vira um escopo novo (ex.: `financeiro:bruto`).
- **Multiempresa**: o token carrega **uma** empresa (`char(2)`), não o conjunto. O ERP fazia o mesmo
  na prática pelo RLS do login (`SUSER_NAME()` → `SIS_UsuarioEmpresa`), e o warehouse só tem dados da
  empresa `13`. Usuários com mais de uma empresa são, hoje, um `admin` sem empresa no token — que vê
  todas e pode trocar com `?empresa=`. Se o produto precisar de usuário multiempresa de verdade, o
  caminho é `empresa text[]` no token + o `empresa = any(:empresas)` no `where`.
- **Identidades**: `Usuarios` (colaboradores) e `usuario` (representantes) continuam **fora** da API —
  o cadastro é o `auth.usuario` do Neon, gerenciado por CLI, sem espelhar as 39/35 contas.

---

## 10. Próximos passos

- Estudo **Infra** (último da fila de módulos): `Etc_Parametros`, `Sistemas_Config`, conexões (`Conn_*`), `SIMConnect` (`mic_*`), `SCH_*` (agendador), réplicas, `DBInternet_*`, logs de infra.
- Consolidar requisitos de auth/permissões para o contrato da API (documento transversal após os estudos).

---

_Fontes: pesquisa direta em `sys.tables`/`INFORMATION_SCHEMA` + amostragem de `Usuarios`, `usuario`, `Usuario_Acessos`, `Sistemas`, `Topicos`, `menu`, `menu_it`, `Log_Acesso`, `Log_Sistema`._