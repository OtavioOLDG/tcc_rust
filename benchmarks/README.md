# Protocolo de Benchmark

Este diretório contém a instrumentação utilizada para a coleta de métricas
do experimento Local × Docker × WebAssembly/WASI.

Os testes preliminares e dry runs não constituem resultados oficiais do TCC.

## Aplicação avaliada

Todos os ambientes executam a mesma versão da aplicação Rust.

Pacote experimental:

SHA-256:
8503f41aaf0d476ec62555a5e44f0211170846d4266090527cbdac1fffaa5b56

A única operação utilizada para coleta de desempenho é:

POST /image/grayscale

As rotas /health e /message são utilizadas somente para validação funcional
e não fazem parte das amostras oficiais.

## Fixture

Arquivo:

k6/fixtures/benchmark.jpeg

Características:

- formato: JPEG;
- dimensões: 640x640 pixels;
- tamanho: 20582 bytes;
- SHA-256:
  13328a9d4466f6fd22f55c5b948ce8fc9ec39532f82dc50cd8bb58285ce19d70

A fixture não deve ser modificada durante o experimento.

## Ambientes

Cada ambiente utiliza uma VM independente com:

- Ubuntu 22.04;
- 2 vCPUs;
- 4096 MiB de RAM.

Ambientes:

- Local: execução nativa da aplicação Rust;
- Docker: aplicação executada em container;
- WASM: aplicação WebAssembly/WASI executada por Spin/Wasmtime.

Somente uma VM do experimento deve permanecer ligada durante cada série
de medições.

## Gerador de carga

O k6 é executado externamente às VMs, no WSL Ubuntu.

Versão:

k6 v2.2.0

Commit:

00a9a1b7f5

Binário:

/usr/bin/k6

SHA-256:

9fced9ff140d73bf3d69b3cde2493adce3807b35ab210a5cb0771045d0ad5a29

Script oficial:

k6/scripts/image-load.js

## Cenários de carga

São utilizados três níveis de concorrência:

- 10 VUs;
- 50 VUs;
- 100 VUs.

Para cada nível:

1. 15 segundos de warm-up;
2. 5 minutos (300 segundos) de medição oficial;
3. 30 segundos de descanso antes da próxima execução.

Cada cenário é repetido 5 vezes.

Quantidade total:

3 cargas × 5 repetições × 3 ambientes = 45 execuções oficiais.

## Métricas HTTP

O k6 registra:

- throughput;
- quantidade de requisições;
- taxa de erro;
- latência média;
- mediana;
- p90;
- p95;
- p99;
- máximo.

## CPU e RAM

As métricas são amostradas em instantes programados aproximadamente a cada
1 segundo. Os coletores utilizam uma agenda temporal absoluta para evitar o
acúmulo de atrasos ao longo da execução.

O intervalo real entre duas amostras consecutivas é registrado na coluna
`interval_ms`.

Caso a VM esteja sob alta utilização e um instante de amostragem seja perdido,
o coletor avança para o próximo instante programado, sem gerar múltiplas
amostras atrasadas em sequência.

A duração da coleta oficial é de 5 minutos (300 segundos).

### VM inteira

O arquivo `collectors/vm-metrics.sh` coleta:

- CPU total da VM;
- RAM utilizada pela VM.

A RAM utilizada é calculada por:

MemTotal - MemAvailable

A CPU é normalizada para a capacidade total da VM:

- 0% = sem utilização;
- 50% = aproximadamente uma das duas vCPUs completamente utilizada;
- 100% = capacidade total das duas vCPUs.

### Local

O arquivo `collectors/process-metrics.sh` mede o processo
`rust-benchmark-api`.

São coletados:

- CPU do processo;
- RSS atual;
- maior RSS observado durante a janela;
- VmHWM como informação complementar.

### Docker

O arquivo `collectors/docker-metrics.sh` mede o cgroup do container.

São coletados:

- CPU do container;
- memória atual do cgroup;
- maior memória observada durante a janela.

O pico é calculado pelo próprio coletor a partir de `memory.current`,
pois o ambiente utilizado não disponibiliza `memory.peak`.

### WASM

O arquivo `collectors/process-metrics.sh` mede o processo
`spin trigger http`, que contém a execução do componente WASM/Wasmtime.

São coletados:

- CPU do runtime;
- RSS atual;
- maior RSS observado durante a janela;
- VmHWM como informação complementar.

## Pico

Para CPU:

pico = maior percentual observado em uma janela de amostragem registrada
durante os 5 minutos (300 segundos).

Para RAM:

pico = maior consumo observado durante os 5 minutos (300 segundos) de medição.

O pico observado durante a janela é a métrica oficial.

VmHWM é mantido somente como informação complementar porque representa o
histórico do processo desde sua inicialização.

## Controle experimental

Durante as medições oficiais:

- não executar builds;
- não atualizar dependências;
- não instalar pacotes;
- não executar downloads;
- não alterar código;
- não modificar a fixture;
- não alterar os recursos das VMs;
- não executar mais de uma VM experimental simultaneamente;
- manter o computador no mesmo perfil de energia.

Dry runs e testes de validação devem ser mantidos separados dos resultados
oficiais.

## Integridade dos instrumentos

Os instrumentos utilizados na coleta oficial foram congelados antes do início
dos experimentos.

- `k6/scripts/image-load.js`
  SHA-256: `86e75c52e2cf0bcecd7125aba68cdd69ed30df2690578443982a421878b463ed`

- `benchmarks/collectors/vm-metrics.sh`
  SHA-256: `1e97dadad3c2b6eb17322bbcc3327868b87ad36c9ef6d860036283cb0b522e78`

- `benchmarks/collectors/process-metrics.sh`
  SHA-256: `450938a883ae988f107dfef92872c36a3672724f4053a420e7550cb25b26e39e`

- `benchmarks/collectors/docker-metrics.sh`
  SHA-256: `2099fb953f6cdfdf445263e6c88b47dcff9e3a2ffabf12aba66308ee923ab34b`