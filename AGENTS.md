# AGENT.md — API Rust do TCC

## Propósito

Este arquivo define as instruções que qualquer agente de programação deve seguir ao trabalhar na API Rust utilizada no TCC.

O objetivo é construir primeiro uma única API em Rust, segura, simples, determinística, testável e desacoplada do ambiente de execução. Docker e WebAssembly/WASI serão tratados somente em etapas posteriores.

A API deverá servir como base comum para os dois cenários experimentais do TCC:

1. execução nativa em contêiner Docker;
2. execução em WebAssembly/WASI com Fermyon Spin e Wasmtime.

A lógica central da aplicação deverá permanecer equivalente nos dois cenários.

---

## Prioridades do projeto

Toda decisão deve priorizar, nesta ordem:

1. segurança;
2. corretude;
3. simplicidade;
4. determinismo;
5. baixo acoplamento;
6. testabilidade;
7. reprodutibilidade;
8. desempenho;
9. facilidade de adaptação futura para Docker e WASM/WASI.

Não adicionar complexidade sem necessidade concreta.
Não adicionar dependências apenas por conveniência.
Não otimizar prematuramente.

---

## Escopo da API v1

A primeira versão possuirá somente:

```text
GET  /health
GET  /message
POST /image/grayscale
```

### `GET /health`

Finalidade:
- indicar que a aplicação está disponível;
- fornecer uma rota mínima para futuras medições de inicialização.

Resposta esperada:

```json
{"status":"ok"}
```

Características:
- sem I/O externo;
- sem estado;
- sem processamento pesado;
- determinística.

### `GET /message`

Finalidade:
- representar uma requisição HTTP extremamente leve.

Resposta esperada:

```json
{"message":"API Rust funcionando"}
```

Características:
- JSON simples;
- sem dependências externas;
- sem processamento relevante;
- determinística.

### `POST /image/grayscale`

Finalidade:
- representar uma carga computacional real;
- exercitar CPU, memória, decodificação e codificação;
- manter processamento determinístico.

Entrada:
- bytes da imagem diretamente no body HTTP;
- não usar `multipart/form-data` nesta versão;
- aceitar apenas PNG e JPEG.

Saída:
- sempre PNG;
- `Content-Type: image/png`.

Fluxo:

```text
request
  ↓
validar Content-Type
  ↓
limitar tamanho
  ↓
receber bytes
  ↓
validar formato
  ↓
decodificar
  ↓
validar dimensões
  ↓
converter para grayscale
  ↓
codificar PNG
  ↓
response
```

O processamento deve ocorrer em memória.
Não persistir arquivos em disco.

---

## Limites de segurança iniciais

Aplicar desde a primeira versão:

```text
Tamanho máximo do upload: 5 MB
Dimensões máximas:         4096 x 4096
Formatos permitidos:       PNG e JPEG
Formato de saída:          PNG
```

Os valores podem ser revisados posteriormente, mas os limites não devem ser removidos.

Validar tanto:
- tamanho do payload;
- dimensões reais da imagem após decodificação.

---

## Arquitetura

Usar uma arquitetura em camadas simples, parecida conceitualmente com MVC, porém não MVC puro.

Estrutura de referência:

```text
rust-benchmark-api/
│
├── Cargo.toml
├── Cargo.lock
│
├── src/
│   ├── main.rs
│   ├── lib.rs
│   ├── error.rs
│   ├── api/
│   │   ├── mod.rs
│   │   ├── routes.rs
│   │   └── handlers.rs
│   ├── application/
│   │   ├── mod.rs
│   │   └── services.rs
│   └── domain/
│       ├── mod.rs
│       └── models.rs
└── tests/
    └── api_tests.rs
```

Não criar uma estrutura excessivamente complexa.
Subdividir módulos somente quando o crescimento real justificar.

---

## Responsabilidades

### `main.rs`

Deve permanecer mínimo.

Pode:
- inicializar o runtime necessário;
- construir a aplicação;
- definir o endereço local;
- iniciar o servidor;
- lidar com falha fatal de inicialização.

Não deve conter:
- regras de negócio;
- processamento de imagem;
- validação de domínio;
- lógica de handlers;
- lógica usada pelo benchmark.

### `lib.rs`

Deve expor os módulos reutilizáveis.
A lógica central não pode ficar presa ao executável.

### `api/`

Responsável exclusivamente pela camada HTTP:
- rotas;
- handlers;
- request/response;
- Content-Type;
- limites de request;
- conversão entre HTTP e tipos internos;
- mapeamento de erros para status HTTP.

Axum pode existir aqui.

### `application/`

Responsável pela coordenação da lógica da aplicação.

Não deve depender de:
- Axum;
- Docker;
- WASM;
- Spin;
- Wasmtime.

A lógica deve receber e retornar tipos Rust comuns.

### `domain/`

Responsável por:
- modelos;
- regras puras;
- validações centrais.

Preferencialmente não deve acessar:
- rede;
- filesystem;
- shell;
- banco de dados;
- variáveis sensíveis;
- processos externos.

### `error.rs`

Responsável por erros internos controlados.

Não expor ao cliente:
- stack traces;
- caminhos locais;
- detalhes do decoder;
- informações sensíveis;
- mensagens internas desnecessárias.

---

## Regra arquitetural principal

A lógica que será medida não deve depender diretamente da camada HTTP.

Exemplo conceitual:

```rust
pub fn process_grayscale(input: &[u8]) -> Result<Vec<u8>, AppError> {
    // processamento
}
```

Essa função deve ser utilizável por:
- handler Axum;
- testes unitários;
- adaptador WASM futuro.

Ela não deve saber de onde vieram os bytes.

---

## Dependências aprovadas

Dependências diretas permitidas inicialmente:

```text
axum
tokio
serde
serde_json
image
```

### `axum`
Uso:
- HTTP;
- rotas;
- handlers;
- respostas.

Restringir à camada HTTP.

### `tokio`
Uso:
- runtime assíncrono;
- servidor HTTP nativo.

Não levar Tokio para a lógica de domínio.
Usar apenas features necessárias.

### `serde`
Uso:
- serialização;
- desserialização;
- tipos de resposta e entrada.

### `serde_json`
Uso:
- JSON.

### `image`
Uso:
- decodificação de PNG/JPEG;
- processamento grayscale;
- codificação PNG.

Não adicionar outra biblioteca de imagens sem justificativa.

---

## Política para novas dependências

Uma nova crate só pode ser adicionada após verificar:

1. necessidade concreta;
2. se a biblioteca padrão resolve de maneira adequada;
3. maturidade da crate;
4. adoção pela comunidade;
5. manutenção;
6. origem confiável;
7. impacto na árvore de dependências;
8. vulnerabilidades conhecidas;
9. licença;
10. impacto na portabilidade futura.

Não adicionar automaticamente crates obscuras ou pouco estabelecidas.

---

## Dependências que não devem ser adicionadas sem nova decisão

Nesta fase, não adicionar automaticamente:

```text
thiserror
anyhow
reqwest
uuid
chrono
regex
tower-http
validator
sqlx
diesel
sea-orm
redis
JWT
OAuth
ORMs
drivers de banco
mensageria
```

Isso não significa que sejam ruins; apenas não são necessárias no escopo atual.

---

## Segurança

Segurança é requisito obrigatório.

Regras gerais:
- não usar `unsafe` no código próprio;
- preferir `#![forbid(unsafe_code)]`;
- nunca armazenar segredos no código;
- nunca commitar credenciais;
- não executar shell;
- não criar processos externos;
- não acessar filesystem sem necessidade;
- não acessar rede externa;
- não usar banco de dados nesta versão;
- evitar estado global mutável;
- validar toda entrada;
- limitar payload;
- limitar dimensões de imagem;
- retornar erros seguros;
- não revelar detalhes internos.

Durante desenvolvimento, preferir:

```text
127.0.0.1
```

Não usar `0.0.0.0` sem necessidade explícita.

Não executar a aplicação como root/Administrador.

---

## Segurança da máquina de desenvolvimento

O agente não deve:
- instruir a executar scripts aleatórios da internet;
- executar comandos destrutivos desnecessários;
- pedir privilégios elevados sem justificativa;
- adicionar repositórios externos sem necessidade;
- adicionar dependências Git se crates.io for suficiente;
- criar acesso a diretórios pessoais;
- abrir portas para rede externa sem solicitação.

Manter o projeto isolado de dados pessoais e credenciais.

---

## `Cargo.lock`

O `Cargo.lock` deve ser versionado.

Não remover.

Objetivos:
- reprodutibilidade;
- auditoria;
- estabilidade da árvore de dependências;
- comparação experimental consistente.

---

## Ferramentas obrigatórias de qualidade

Ferramentas oficiais/principais:

```bash
cargo fmt
cargo clippy
cargo test
```

Ferramentas externas aprovadas:

```bash
cargo audit
cargo deny check
```

Somente utilizar ferramentas externas maduras e amplamente reconhecidas pela comunidade Rust.

---

## Política de Clippy

Executar preferencialmente:

```bash
cargo clippy --all-targets --all-features -- -D warnings
```

Não adicionar `#[allow(...)]` apenas para silenciar warnings.

Antes de ignorar um lint:
1. entender o motivo;
2. confirmar que o lint não se aplica;
3. documentar a decisão quando necessário.

---

## Boas práticas Rust

Esperado:
- ownership claro;
- borrowing apropriado;
- tipos expressivos;
- funções pequenas;
- módulos coesos;
- tratamento explícito de erros;
- `Result` para falhas previsíveis;
- evitar clones desnecessários;
- pattern matching quando apropriado;
- ausência de warnings;
- ausência de código morto;
- ausência de `unsafe`;
- evitar abstrações prematuras.

### `unwrap` e `expect`

Em produção:
- evitar em caminhos dependentes de entrada externa;
- não usar para parsing de request;
- não usar para processamento de imagem enviado pelo cliente.

Em testes:
- podem ser utilizados quando aumentarem clareza.

---

## Tratamento de erros

Categorias conceituais esperadas:

```text
InvalidContentType
PayloadTooLarge
UnsupportedImageFormat
InvalidImage
ImageDimensionsTooLarge
ImageProcessingFailure
InternalError
```

Os nomes podem ser refinados durante a implementação.

Erros causados pela entrada devem resultar em 4xx.
Falhas internas devem resultar em 5xx genérico.

---

## Determinismo

A mesma entrada deve produzir a mesma saída.

Não utilizar no processamento medido:
- randomização;
- relógio atual;
- timestamps;
- APIs externas;
- banco;
- estado persistente;
- metadados variáveis.

O grayscale deve ser reproduzível.

---

## Estado e concorrência

A primeira versão deve ser essencialmente stateless.

Evitar:
- estado global mutável;
- mutexes;
- locks;
- caches;
- estruturas compartilhadas.

Adicionar apenas se houver justificativa concreta.

---

## Testes obrigatórios

### Unidade

Testar o processamento de imagem sem iniciar servidor HTTP.

Casos mínimos:
- PNG válido;
- JPEG válido;
- body vazio;
- imagem corrompida;
- formato não suportado;
- dimensões excessivas;
- imagem mínima válida;
- geração de PNG;
- dimensões preservadas;
- grayscale correto.

### `/health`

Verificar:
- status;
- JSON;
- conteúdo;
- Content-Type.

### `/message`

Verificar:
- status;
- JSON;
- conteúdo;
- determinismo.

### `/image/grayscale`

Verificar:
- PNG válido;
- JPEG válido;
- resposta `image/png`;
- saída decodificável;
- dimensões preservadas;
- body vazio;
- payload excessivo;
- dimensão excessiva;
- formato inválido;
- imagem corrompida.

---

## Critérios para considerar uma alteração válida

Antes de concluir uma etapa, executar:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
cargo audit
cargo deny check
```

A alteração não está pronta se:
- houver warning relevante;
- algum teste falhar;
- houver vulnerabilidade impeditiva;
- cargo-deny reprovar;
- uma nova dependência não tiver justificativa.

---

# Plano de implementação

## Fase 0 — Preparação

1. confirmar Rust toolchain;
2. criar projeto Cargo;
3. iniciar Git;
4. versionar `Cargo.lock`;
5. configurar `.gitignore`;
6. verificar rustfmt;
7. verificar Clippy;
8. instalar cargo-audit;
9. instalar cargo-deny;
10. executar `cargo deny init`.

Critério:

```bash
cargo check
cargo fmt --check
cargo clippy
cargo test
```

devem funcionar.

## Fase 1 — Estrutura arquitetural

Criar:

```text
src/
├── main.rs
├── lib.rs
├── error.rs
├── api/
├── application/
└── domain/
```

O projeto deve compilar antes de continuar.

## Fase 2 — Dependências

Adicionar somente:

```text
axum
tokio
serde
serde_json
image
```

Usar apenas features necessárias.

Depois executar:

```bash
cargo check
cargo audit
cargo deny check
```

## Fase 3 — `/health`

Implementar `GET /health`.

Esperado:

```text
200
application/json
{"status":"ok"}
```

## Fase 4 — `/message`

Implementar `GET /message`.

Esperado:

```json
{"message":"API Rust funcionando"}
```

## Fase 5 — Serviço de imagem

Implementar primeiro a lógica sem HTTP.

Exemplo conceitual:

```rust
fn process_grayscale(input: &[u8]) -> Result<Vec<u8>, AppError>
```

Responsabilidades:
1. validar entrada;
2. decodificar;
3. validar dimensões;
4. converter para grayscale;
5. codificar PNG;
6. retornar bytes.

Nenhum tipo Axum deve aparecer nessa função.

## Fase 6 — `/image/grayscale`

Criar o handler HTTP somente depois da lógica isolada estar testada.

Handler:
1. valida Content-Type;
2. aplica limite de body;
3. recebe bytes;
4. chama serviço;
5. mapeia erro;
6. retorna PNG.

## Fase 7 — Erros

Criar representação central de erros.

Garantir:
- 4xx para erro do cliente;
- 5xx para erro interno;
- nenhum detalhe sensível.

## Fase 8 — Hardening

Revisar:
- `127.0.0.1`;
- limites;
- formatos;
- dimensões;
- ausência de filesystem;
- ausência de shell;
- ausência de rede externa;
- ausência de segredos;
- ausência de `unsafe`;
- ausência de `unwrap` perigoso;
- ausência de estado global mutável.

## Fase 9 — Testes

Executar todos os testes unitários e HTTP.
Somente avançar com todos aprovados.

## Fase 10 — Qualidade

Executar:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
cargo audit
cargo deny check
```

---

# Definition of Done — API v1

A API v1 só estará concluída quando:

- [ ] `/health` funcionar;
- [ ] `/message` funcionar;
- [ ] `/image/grayscale` funcionar;
- [ ] PNG for aceito;
- [ ] JPEG for aceito;
- [ ] saída for sempre PNG;
- [ ] grayscale estiver correto;
- [ ] limites estiverem aplicados;
- [ ] lógica de imagem estiver desacoplada do HTTP;
- [ ] `main.rs` estiver mínimo;
- [ ] código estiver sem `unsafe`;
- [ ] sem banco;
- [ ] sem filesystem desnecessário;
- [ ] sem chamadas externas;
- [ ] sem segredos;
- [ ] `Cargo.lock` estiver versionado;
- [ ] testes passarem;
- [ ] rustfmt passar;
- [ ] Clippy passar;
- [ ] cargo-audit passar;
- [ ] cargo-deny passar.

---

# Etapas posteriores

Somente depois da API v1 estar concluída:

```text
API Rust
   ↓
validação funcional
   ↓
adaptador/execução nativa
   ↓
Docker
   ↓
adaptador WASM
   ↓
Spin/Wasmtime
   ↓
validação de equivalência
   ↓
benchmark
```

Não antecipar Docker ou WASM durante a implementação da lógica central.

---

# Equivalência futura entre os ambientes

Preservar nos dois cenários:
- mesma lógica;
- mesmos dados de entrada;
- mesmas respostas;
- mesmas imagens;
- mesmos limites;
- mesmo algoritmo grayscale;
- mesmos comportamentos funcionais;
- mesmos critérios de erro, quando tecnicamente possível.

Os adaptadores HTTP podem ser diferentes.

O código de `application` e `domain` deve permanecer reutilizável.

---

# Skills obrigatórias

## k6 e testes de desempenho

Para qualquer tarefa relacionada a:

- k6;
- teste de carga;
- teste de desempenho;
- benchmark HTTP;
- throughput;
- requests por segundo;
- RPS;
- VUs;
- latência;
- p90;
- p95;
- p99;
- thresholds;
- smoke test;
- load test;
- stress test;
- spike test;
- soak test;
- scripts de k6;
- fixtures de benchmark;
- coleta de resultados;
- interpretação de resultados de performance;
- comparação de desempenho entre Docker e WebAssembly/WASI;

o agente deve obrigatoriamente ler e seguir:

`.agents/skills/k6-load-testing/SKILL.md`

A skill deve ser utilizada antes de:

- criar scripts k6;
- modificar scripts k6;
- executar testes de carga;
- instalar ou atualizar k6;
- alterar parâmetros de carga;
- interpretar resultados;
- preparar experimentos de desempenho;
- comparar Docker com WebAssembly/WASI.

As regras gerais deste `AGENTS.md` continuam válidas.

Caso exista conflito entre o `AGENTS.md` e a skill, o agente deve interromper a tarefa e informar o conflito antes de realizar alterações.

Nunca executar teste de carga contra hosts externos, públicos ou de terceiros sem autorização explícita do usuário.

O alvo padrão para testes locais deve ser:

`http://127.0.0.1:3000`

---

# Instruções obrigatórias para agentes

Ao modificar este projeto:

1. não alterar a arquitetura sem solicitação explícita;
2. não adicionar crates automaticamente;
3. não utilizar dependências obscuras;
4. não usar `unsafe`;
5. não adicionar banco;
6. não adicionar autenticação sem solicitação;
7. não criar acesso ao filesystem sem necessidade;
8. não executar shell;
9. não criar processos externos;
10. não abrir a API para a rede externa por padrão;
11. não implementar Docker ou WASM antes da etapa correspondente;
12. não misturar processamento de imagem com handlers;
13. não duplicar lógica entre adaptadores;
14. manter comportamento determinístico;
15. manter o projeto stateless;
16. preservar `Cargo.lock`;
17. executar os controles de qualidade antes de concluir uma alteração;
18. explicar qualquer desvio das regras deste arquivo.

---

# Regra final

> Construir primeiro uma API Rust pequena, segura, determinística, idiomática e bem testada. A lógica central deve permanecer independente do ambiente de execução para posteriormente servir de base aos dois cenários experimentais do TCC.
