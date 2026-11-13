# Ambiente Vagrant

O `Vagrantfile` descreve três máquinas virtuais para as modalidades Local, Docker e WASM, usando VirtualBox e a imagem base Ubuntu Jammy. Cada VM está configurada com duas vCPUs e 4096 MiB de memória; a pasta compartilhada padrão do Vagrant é desabilitada.

`provisioning/common.sh` contém a preparação comum; os arquivos `local.sh`, `docker.sh` e `wasm.sh` correspondem às modalidades indicadas pelos nomes. Os scripts descrevem alterações feitas no ambiente das VMs.

Esta pasta apresenta a configuração versionada. As versões e condições efetivamente observadas durante os experimentos são identificadas nos registros de cada campanha.
