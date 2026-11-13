# Benchmarks com k6

Esta pasta contém a infraestrutura de geração de carga HTTP utilizada nos benchmarks do TCC
" Avaliação de Runtimes WebAssembly e Docker: Um Estudo sobre Eficiência e Latência de Execução ".

O Grafana k6 é utilizado como gerador de carga para comparar a mesma API Rust executada nos
ambientes Local/Native, Docker e WebAssembly/WASI.

O endpoint oficial utilizado nos benchmarks é:

POST /image/grayscale

A requisição envia a fixture fixa `fixtures/benchmark.jpeg` como `image/jpeg`. A API realiza a
conversão da imagem para escala de cinza em memória e retorna uma imagem PNG.

Os demais endpoints e scripts existentes nesta pasta possuem finalidade auxiliar e não fazem
parte das métricas oficiais do benchmark.

## Scripts

- `scripts/smoke.js`: executa uma carga mínima sobre `GET /health` para verificar disponibilidade
  básica da API. É utilizado somente como teste auxiliar e não produz resultados oficiais.

- `scripts/message-load.js`: exerce `GET /message` como carga HTTP leve. Foi utilizado durante o
  desenvolvimento e validações preliminares, mas não faz parte da campanha oficial do TCC.

- `scripts/image-load.js`: script oficial de geração de carga. Envia
  `fixtures/benchmark.jpeg` para `POST /image/grayscale` como `image/jpeg` e verifica:

  - status HTTP `200`;
  - `Content-Type: image/png`.

  A fixture é carregada uma única vez no contexto de inicialização do k6.

## Gerador de carga oficial

As execuções oficiais são realizadas com o k6 nativo no Windows.

Versão utilizada:

- k6: `v2.2.0`
- commit: `00a9a1b7f5`
- Go: `go1.26.5`
- plataforma: `windows/amd64`
- SHA-256 do executável utilizado:
  `87DFA91BC3E47BC4BD77911D59D7FF79F25CD76FB8322C97072F08F08A0DA5ED`

Execuções do k6 dentro do WSL não são utilizadas como resultados oficiais, devido a anomalias de
relógio observadas durante testes preliminares.

## Ambientes avaliados

A mesma API é avaliada em três ambientes separados:

1. Local/Native dentro de uma VM;
2. Docker dentro de uma VM;
3. WebAssembly/WASI dentro de uma VM.

Os ambientes utilizam VMs equivalentes, mantendo os mesmos recursos computacionais definidos para
a campanha experimental.

Endereços utilizados na rede privada de benchmark:

- Local/Native: `http://192.168.56.10:3000`
- Docker: `http://192.168.56.11:3000`
- WebAssembly/WASI: `http://192.168.56.12:3000`

A rede utilizada é restrita ao ambiente experimental e os serviços não são expostos publicamente.

## Configuração da carga

O script `image-load.js` aceita as seguintes variáveis de ambiente:

- `BASE_URL`: endereço da API;
- `VUS`: quantidade de usuários virtuais concorrentes;
- `DURATION`: duração da execução.

VUs representam concorrência e não equivalem diretamente a requisições por segundo (RPS).

A campanha oficial utiliza três níveis de carga:

- 10 VUs;
- 50 VUs;
- 100 VUs.

Para cada nível de carga são realizadas 5 tentativas experimentais.

Cada tentativa segue o seguinte protocolo:

1. reinicialização da aplicação/container antes da repetição;
2. preparação e sincronização dos coletores;
3. warm-up de 15 segundos;
4. pequeno intervalo entre o warm-up e a medição oficial;
5. execução oficial de 300 segundos;
6. encerramento e validação dos artefatos gerados.

O início do k6 e dos coletores de recursos é sincronizado por um timestamp Unix futuro (`START`).

Durante uma execução oficial não são executados diagnósticos ou outras tarefas dentro da VM que
possam interferir no resultado.

## Critério das cinco tentativas

As cinco repetições representam cinco tentativas experimentais sob as mesmas condições e não
necessariamente cinco execuções que precisam terminar sem falhas.

Uma tentativa que apresenta instabilidade provocada durante a carga, como timeout, congelamento
temporário, soft lockup ou perda de amostras dos coletores, continua sendo preservada como resultado
experimental.

Essas ocorrências são utilizadas na análise de estabilidade do ambiente.

Por exemplo, uma execução na qual:

- o k6 registra uma requisição bloqueada por dezenas de segundos;
- o coletor global da VM deixa de produzir amostras durante o mesmo período;
- o coletor específico do runtime/container apresenta o mesmo intervalo;

é classificada como uma execução com falha de estabilidade e não é repetida apenas para obter um
resultado sem erro.

Isso evita selecionar somente execuções bem-sucedidas e introduzir viés nos resultados.

Entretanto, execuções inválidas por motivos externos ao comportamento do sistema testado não são
contabilizadas entre as cinco tentativas. Exemplos:

- execução interrompida manualmente;
- erro de comando;
- timestamp `START` incorreto ou já expirado;
- erro na preparação dos coletores;
- alteração acidental do ambiente;
- problema de instrumentação não provocado pela carga avaliada.

Essas execuções podem ser preservadas para rastreabilidade, mas não constituem resultados
experimentais oficiais.

## Uso de execuções com falha nas métricas

Execuções que completam normalmente o período de 300 segundos podem ser utilizadas nas análises de:

- throughput;
- latência;
- utilização de CPU;
- utilização de memória RAM.

Quando uma tentativa apresenta congelamento ou perda significativa da janela de coleta, seus dados
não são misturados diretamente às médias de desempenho das execuções completas como se fossem uma
medição normal de 300 segundos.

Essas tentativas são analisadas separadamente como resultados de estabilidade, permitindo calcular,
por exemplo:

- quantidade de execuções completas;
- quantidade de execuções com falha;
- taxa de conclusão das tentativas;
- ocorrência de timeouts;
- duração dos períodos de congelamento;
- comportamento de CPU e memória imediatamente antes da falha.

Assim, desempenho e estabilidade são analisados separadamente.

## Fixture de imagem

A fixture oficial possui os seguintes metadados:

- arquivo: `benchmark.jpeg`;
- formato real: JPEG (`image/jpeg`);
- tamanho: `20582` bytes;
- dimensões: `640x640` pixels;
- SHA-256:
  `13328a9d4466f6fd22f55c5b948ce8fc9ec39532f82dc50cd8bb58285ce19d70`.

Ela respeita os limites definidos pela API: possui menos de 5 MiB, não excede 4096x4096 pixels e
utiliza um formato aceito.

A fixture deve permanecer exatamente a mesma durante toda a campanha comparável entre
Local/Native, Docker e WebAssembly/WASI.

Ela não deve ser baixada, regenerada ou substituída durante as execuções.

## Integridade do script oficial

O script oficial utilizado para a geração de carga possui o seguinte SHA-256:

`image-load.js`

`86e75c52e2cf0bcecd7125aba68cdd69ed30df2690578443982a421878b463ed`

Alterações no script durante uma série comparável devem ser evitadas. Caso uma modificação seja
necessária, ela deve ser documentada antes da continuação dos experimentos.

## Resultados

Os resultados oficiais do k6 são armazenados em:

`results/official/<ambiente>/<carga>vus/rN.json`

Exemplos:

`results/official/local/10vus/r1.json`

`results/official/docker/50vus/r1.json`

`results/official/wasm/100vus/r5.json`

Cada `rN` representa uma das cinco tentativas experimentais daquele ambiente e nível de carga.

Resultados que não pertencem à série experimental por erro de procedimento podem ser armazenados em:

`results/discarded/`

A pasta `discarded` não significa necessariamente que o arquivo deve ser apagado. Ela preserva
artefatos que não fazem parte da série oficial e permite rastrear problemas ocorridos durante a
execução do experimento.

Uma execução oficial que apresenta falha de estabilidade causada durante a carga deve permanecer
associada à sua repetição oficial e ser classificada posteriormente durante a análise.

## Validação dos resultados

Após cada execução são verificados, quando aplicável:

- número de requisições;
- throughput;
- checks do k6;
- `http_req_failed`;
- latência média;
- mediana;
- p90;
- p95;
- p99;
- latência máxima;
- quantidade de amostras dos coletores;
- maior intervalo entre amostras;
- integridade dos arquivos por SHA-256.

No resumo JSON do k6, a interpretação de `http_req_failed` deve utilizar principalmente o campo
`value`.

Por exemplo:

`"value": 0`

representa taxa de falha igual a zero, mesmo que os campos internos `passes` e `fails` pareçam
contraintuitivos.

## Reprodutibilidade

Durante a campanha experimental devem permanecer constantes:

- código da API;
- fixture utilizada;
- script `image-load.js`;
- versão do k6;
- configuração das VMs;
- recursos de CPU e RAM;
- duração dos testes;
- quantidades de VUs;
- configuração dos ambientes de execução;
- metodologia de coleta.

Os arquivos brutos oficiais não devem ser alterados após sua geração.

Quando transferidos entre VM, Windows e WSL, a integridade deve ser conferida por SHA-256.