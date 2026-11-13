# Código da aplicação

- `main.rs`: inicialização do servidor nativo.
- `lib.rs`: módulos compartilhados e ponto de entrada para Spin na compilação WebAssembly.
- `api/`: roteador, handlers e conversão dos erros para respostas HTTP.
- `application/`: serviço de validação e processamento da imagem em memória.
- `domain/`: módulo de domínio.
- `error.rs`: erros internos da aplicação.

O processamento de imagem é independente do servidor HTTP e é compartilhado pelas modalidades de execução. Os testes Rust ficam próximos do código que verificam.
