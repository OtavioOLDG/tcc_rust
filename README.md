# API Rust para comparação de ambientes de execução

Este repositório reúne a aplicação Rust e os artefatos experimentais de um TCC que compara sua execução em três modalidades: Local, Docker e WebAssembly/WASI com Fermyon Spin. As modalidades utilizam a mesma lógica de processamento de imagem; seus pontos de entrada e mecanismos de execução diferem.

## Aplicação

| Método e rota | Finalidade | Resposta |
| --- | --- | --- |
| `GET /health` | Verificar disponibilidade | JSON com o estado da aplicação |
| `GET /message` | Requisição HTTP leve | JSON com uma mensagem |
| `POST /image/grayscale` | Converter imagem em escala de cinza | Imagem PNG |

A rota de imagem recebe o arquivo diretamente no corpo da requisição, aceita PNG ou JPEG e processa os dados em memória. O limite de entrada é de 5 MiB e cada dimensão da imagem é limitada a 4096 pixels.

## Organização

| Caminho | Conteúdo |
| --- | --- |
| [`src/`](src/) | Código da aplicação, roteador HTTP e processamento compartilhado |
| [`k6/`](k6/README.md) | Scripts de carga HTTP, imagem de referência e resumos de execuções |
| [`benchmarks/`](benchmarks/README.md) | Instrumentação de recursos, medições de inicialização e resultados coletados |
| [`results/`](results/README.md) | Artefatos de campanhas adicionais de recursos WASM |
| [`infra/vagrant/`](infra/vagrant/README.md) | Configuração das máquinas virtuais e provisionamento |
| [`Cargo.toml`](Cargo.toml) e [`Cargo.lock`](Cargo.lock) | Dependências e versões fixadas da aplicação |
| [`Dockerfile`](Dockerfile) | Construção e execução da aplicação em contêiner |
| [`spin.toml`](spin.toml) | Configuração do componente WebAssembly para o Spin |
| [`AGENTS.md`](AGENTS.md) | Instruções de trabalho para agentes neste projeto |

Os diretórios de resultados preservam campanhas e tentativas diferentes. Os manifestos e a metodologia do TCC identificam a utilização dos arquivos e as limitações de cada conjunto.

Arquivos `.sha256`, pacotes `.tar.gz` e `benchmarks/analysis/` estão fora do versionamento e podem não estar presentes em uma cópia obtida pelo Git.

## Como as partes se relacionam

Nas modalidades Local e Docker, a aplicação utiliza um servidor HTTP nativo. Na modalidade WebAssembly/WASI, o Spin recebe as requisições e as encaminha ao roteador compartilhado. Em todas elas, o processamento da imagem é realizado pela mesma lógica da aplicação.

O k6 gera as requisições de carga; os coletores registram recursos da VM e do processo ou contêiner. O tempo de inicialização tem scripts e registros próprios. Os READMEs das pastas explicam como esses materiais estão organizados.
