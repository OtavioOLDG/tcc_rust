use axum::{
    Router,
    routing::{get, post},
};

use super::handlers;

pub fn router() -> Router {
    Router::new()
        .route("/health", get(handlers::health))
        .route("/message", get(handlers::message))
        .route("/image/grayscale", post(handlers::image_grayscale))
}
