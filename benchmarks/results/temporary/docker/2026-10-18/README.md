# Organização temporária dos resultados Docker

Data: 18/10/2026. Organização solicitada pelo usuário após auditoria de 21 execuções (21 JSONs k6 e 42 CSVs de recursos).

Objetivo: manter cinco execuções por carga nas pastas oficiais e preservar as outras seis tentativas nesta pasta temporária. Após a organização inicial, os temporários foram renomeados primeiro para r21–r26 e conferidos; depois os oficiais foram renomeados, um trio por vez, para r1–r5 em cada carga. O conteúdo dos resultados foi preservado.

## Execuções mantidas nos caminhos originais

| Carga | Nomes atuais | Identificadores originais na mesma ordem | Situação |
| --- | --- | --- | --- |
| 10 VUs | r1, r2, r3, r4, r5 | r1, r2, r3, r4, r5 | Zero falhas HTTP; 299 amostras em cada coletor |
| 50 VUs | r1, r2, r3, r4, r5 | r4, r6, r7, r9, r10 | Zero falhas HTTP; 299 amostras em cada coletor |
| 100 VUs | r1, r2, r3, r4, r5 | r1, r3, r4, r5, r6 | Apenas a atual r2 (original r3) tem zero falhas HTTP e coleta íntegra, ainda com latência máxima de 22,817 s; as outras quatro permanecem com problemas |

Os caminhos originais são, relativos à raiz do repositório:

- `k6/results/official/docker/<carga>vus/rN.json`;
- `benchmarks/results/official/docker/<carga>vus/rN-docker.csv`;
- `benchmarks/results/official/docker/<carga>vus/rN-vm.csv`.

Em cada carga permanecem cinco JSONs, cinco CSVs de container e cinco CSVs de VM. Permanecer na pasta `official` não certifica validade experimental: em 100 VUs não há cinco execuções sem problemas disponíveis.

## Execuções movidas para esta pasta

| Carga | Run atual | Run original | Falhas HTTP | Amostras container / VM | Evidência |
| --- | --- | --- | --- | --- | --- |
| 50 VUs | r21 | r1 | 11 | 212 / 212 | Lacunas de até 60,471 s |
| 50 VUs | r22 | r2 | 24 | 140 / 141 | Lacunas de até 61,002 s; última amostra perto de 200 s |
| 50 VUs | r23 | r3 | 58 | 40 / 40 | Lacuna de aproximadamente 60 s; última amostra perto de 99 s |
| 50 VUs | r24 | r5 | 72 | 263 / 263 | Última amostra perto de 263 s |
| 50 VUs | r25 | r8 | 70 | 110 / 110 | Lacunas de até 120,557 s |
| 100 VUs | r26 | r2 | 123 | 129 / 129 | Mais falhas HTTP e menos amostras que as outras cinco tentativas de 100 VUs; múltiplas lacunas de até 60,541 s |

Cada tentativa movida conserva o trio de arquivos: JSON k6, CSV de container e CSV de VM. São 18 arquivos brutos movidos, nenhum apagado.

Foi adicionada uma exceção específica no `.gitignore` para esta pasta datada, permitindo versionar os arquivos preservados em vez de ocultá-los como temporários genéricos. Não foi realizado commit nem staging.

Nesta pasta, `k6/<carga>vus/` contém os JSONs e `resources/<carga>vus/` contém os CSVs correspondentes.

## Critério e limites

Em 50 VUs, preservaram-se no caminho oficial as cinco tentativas com zero falhas HTTP e os dois coletores regulares. Em 100 VUs, como há apenas uma tentativa sem falhas HTTP, manteve-se o melhor conjunto disponível de cinco arquivos por tipo; a original r2, agora temporária r26, foi movida por apresentar simultaneamente o maior número de falhas e a menor quantidade de amostras.

A seleção é posterior à observação dos resultados e não deve ser apresentada como uma campanha original de cinco tentativas bem-sucedidas. As tentativas movidas continuam relevantes para estudar estabilidade. Não se comprovou erro de procedimento ou a causa das anomalias; portanto, não foram classificadas como descartes experimentais definitivos.

Não misturar as execuções problemáticas mantidas em 100 VUs com medições regulares como se fossem todas válidas. A auditoria encontrou:

- atual r1 (original r1): 67 falhas; 240 amostras por coletor; lacuna de até 60,863 s;
- atual r2 (original r3): zero falhas; 299 amostras por coletor; latência máxima de 22,817 s e duração inferida k6 de 306,928 s;
- atual r3 (original r4): uma falha; 296/297 amostras; intervalos de até 2,774 s;
- atual r4 (original r5): três falhas; 195 amostras por coletor; lacuna de até 58,731 s e última amostra perto de 252 s;
- atual r5 (original r6): 37 falhas; 239 amostras por coletor; lacuna de até 61,187 s.

Cold start permanece fora deste trabalho. Não foram executadas novas cargas nem alterados os relatórios Local/WASM.

## Rastreabilidade e recuperação

[manifesto.json](manifesto.json) registra o caminho histórico original em `source`, o caminho atual em `destination` e o SHA-256 de cada um dos 18 arquivos temporários e dos 45 oficiais. Todos os caminhos do manifesto são relativos à raiz do repositório. [renomeacoes.json](renomeacoes.json) detalha cada trio, com números antigos/novos, caminhos antes/depois e hashes. O número rN atual identifica a organização dos arquivos e não altera a ordem histórica das tentativas.

Para recuperar uma tentativa, consultar os manifestos e conferir seu hash. Muitos caminhos históricos foram reutilizados na renumeração: nunca mover diretamente para `source` sem verificar que está livre, nem sobrescrever outro resultado. Para restaurar toda a numeração histórica, planejar também o retorno dos oficiais, usando destinos intermediários livres quando necessário. Os hashes e os números originais registrados preservam a identidade de cada execução.

As estatísticas de desempenho devem indicar explicitamente quais tentativas foram incluídas. As seis tentativas movidas devem continuar contabilizadas quando o objetivo for analisar falhas/estabilidade da campanha completa.
