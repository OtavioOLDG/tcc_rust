# Coletores de recursos

Os scripts desta pasta coletam métricas durante os experimentos:

- `vm-metrics.sh`: CPU e memória da máquina virtual.
- `process-metrics.sh`: CPU e memória do processo selecionado.
- `docker-metrics.sh`: CPU e memória atribuídas ao cgroup do contêiner.

Essas contabilizações têm escopos diferentes. Os arquivos gerados e seus intervalos efetivos correspondem a uma tentativa e a um ambiente específicos. O [protocolo](../README.md) e os registros das campanhas apresentam os detalhes da coleta.
