#![forbid(unsafe_code)]

use rust_benchmark_api::api::routes;

const DEFAULT_ADDRESS: &str = "127.0.0.1:3000";

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let address =
        std::env::var("RUST_BENCHMARK_ADDRESS").unwrap_or_else(|_| DEFAULT_ADDRESS.to_owned());

    let listener = tokio::net::TcpListener::bind(&address).await?;
    axum::serve(listener, routes::router()).await
}