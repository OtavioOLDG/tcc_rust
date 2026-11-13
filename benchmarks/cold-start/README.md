# Medições de tempo de inicialização

Esta pasta reúne scripts e registros de medições da disponibilidade inicial da API. A medição usa a primeira resposta HTTP válida de `/health`; a VM já está iniciada. O procedimento e os limites de interpretação são descritos na metodologia do TCC.

## Conteúdo

- `cold_start.py` e `cold_start2.py`: versões de scripts de medição preservadas no projeto, com condições e caminhos próprios.
- `cold_start_docker_start.py`: medição adicional com contêiner Docker reutilizado. Não substitui a medição com criação de contêiner.
- `conditioning/` e `pilot/`: registros de condicionamento e de ensaios piloto.
- `results/official/`: registros identificados como oficiais por modalidade; `results/campaigns/` contém manifestos de campanhas.
- `docker-start-reused/`: registros da campanha com contêiner reutilizado.
- [`harness-versions/docker-start/`](harness-versions/docker-start/README.md): documentação e versão anterior preservada do script de contêiner reutilizado.
- `tests/`: testes do script de contêiner reutilizado.

Os manifestos distinguem campanhas, versões dos scripts, condicionamentos, falhas e interrupções. O nome de um diretório não determina, isoladamente, a validade ou a inclusão de cada amostra.

A campanha em [`docker-start-reused/official/campanha-2026-11-11/`](docker-start-reused/official/campanha-2026-11-11/) tinha originalmente como nome de pasta o identificador `05dda0ee5a4a4685936a8c255e740296`. Esse identificador e os caminhos da coleta continuam registrados nos JSONs originais; os caminhos históricos no manifesto não foram reescritos e diferem do local atual no repositório.

Os arquivos `.sha256` e os pacotes `.tar.gz` de transferência estão excluídos do versionamento por configuração deste repositório. Alguns comentários e documentos históricos ainda se referem a esses arquivos; eles podem não estar disponíveis em uma cópia obtida pelo Git.
