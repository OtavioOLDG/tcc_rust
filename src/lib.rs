#![forbid(unsafe_code)]

pub mod api;
pub mod application;
pub mod domain;
pub mod error;

#[cfg(target_family = "wasm")]
use spin_sdk::{
    http::{IntoResponse, Request},
    http_service,
};

#[cfg(target_family = "wasm")]
use tower_service::Service;

#[cfg(target_family = "wasm")]
#[http_service]
async fn handle_spin_request(req: Request) -> impl IntoResponse {
    api::routes::router().call(req).await
}
