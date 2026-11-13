# Versões do harness de partida Docker com contêiner reutilizado

Data: 11/11/2026.

## Versão anterior preservada

- SHA-256: `ebc2200a70ca54d7e9f13748f063f724d3d3cce546494fce672dc4c7e922fec0`.
- Arquivo e sidecar originais preservados no subdiretório com esse hash.
- Esquema JSON 3; espera externa por docker stop de sete segundos.
- O piloto concluído e a campanha interrompida na r13 foram relatados pelo usuário. Seus JSONs não foram reclassificados, apagados ou modificados nesta revisão. A limpeza posterior não valida retroativamente a r13.

## Versão v2

- Arquivo de entrega: `../../cold_start_docker_start.py`.
- HARNESS_VERSION: `docker-start-reused-v2`; esquema JSON 4.
- SHA-256: `0908284253df833764e4d458a7ad183c99b47f02078c2cdc729af2a15c295993`.
- Cada amostra e manifesto registram versão, hash anterior e política operacional de limpeza.
- Tolerância graciosa do contêiner: cinco segundos, preservada.
- Espera pelo CLI docker stop: 15 segundos, separada dos processos auxiliares.
- Encerramento dos auxiliares: cinco segundos por etapa, sem alteração dos valores anteriores.
- Confirmação por inspect: orçamento nominal de três segundos por etapa, com registro das consultas; o timeout de cada comando usa o tempo restante. O encerramento dos processos auxiliares possui orçamento próprio, portanto os prazos de espera não são garantias do tempo total da limpeza.
- Estados aceitos entre medições: exited, sem processos ativos, pausa, reinicialização ou estado dead. Estado created é permitido apenas para limpeza final de uma preparação/partida que não se completou, sem permitir reutilização como amostra válida.
- São registrados comando, orçamento, resultado/timeout, duração de espera, duração incluindo encerramento do CLI, saída, estado final, código de saída e OOMKilled quando disponível.
- forced continua indicando apenas docker kill explicitamente solicitado pelo harness; código 137 não identifica sozinho quem provocou o encerramento.
- Timeout ou outro erro do comando continua invalidando a amostra e interrompendo a campanha, mesmo que o contêiner seja posteriormente confirmado como parado.

## Preservação metodológica e segurança

Mantidos o endpoint, os critérios HTTP, o relógio monotônico, os limites das sondagens, o limite global de inicialização, o condicionamento, a reutilização do mesmo contêiner, as 30 repetições oficiais, as duas do piloto, os intervalos, o Image ID e a configuração Docker. A comparação estática das funções de sondagem e do corpo operacional de run_one confirmou sua preservação; somente metadados foram acrescentados à construção do resultado nessa função.

O aumento do prazo operacional pode mudar o tempo efetivo entre partidas, embora ocorra fora da janela cronometrada. A versão nova deve ser identificada separadamente nas análises, sem completar a campanha anterior por soma de tentativas. Não é uma alteração de sinais da aplicação Rust, da imagem ou de dependências.

A publicação de JSONs continua exclusiva, com sincronização e SHA-256. O contêiner próprio é confirmado por ID, nome, imagem e labels antes das operações. Não há remoção por filtro, prune ou remoção forçada de recursos desconhecidos. O arquivo cold_start.py da campanha com docker run permanece intacto.

## Validação executada

36 testes offline com Docker, processos e rede simulados; todos aprovados. Incluem campanha completa com três timeouts reais, reprodução da classificação da r13, erro de parada, estado morto, mudança de posse, SIGINT, falha de publicação, falha de limpeza final, preservação de hashes e não sobrescrita. Os testes ficam em `../../tests/test_docker_start_harness.py` e não iniciam Docker ou a API.

Nenhuma VM, campanha real, serviço ou compilação Rust foi executado. Os 15 segundos são uma proposta operacional inicial: um novo piloto na VM é necessário antes de outra campanha oficial, mediante execução manual autorizada. A validação offline não garante que 30 tentativas se completarão no ambiente real.

O sidecar atual foi atualizado para acompanhar a versão nova. O script permanece autossuficiente e a opção --expected-sha256 continua opcional. A transferência para Windows/VM não foi realizada.
