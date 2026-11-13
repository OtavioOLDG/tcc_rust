use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
};

use crate::error::AppError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ApiError {
    InvalidContentType,
    BodyTooLarge,
    Application(AppError),
}

impl From<AppError> for ApiError {
    fn from(error: AppError) -> Self {
        Self::Application(error)
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = match self {
            Self::InvalidContentType => StatusCode::UNSUPPORTED_MEDIA_TYPE,
            Self::BodyTooLarge => StatusCode::PAYLOAD_TOO_LARGE,
            Self::Application(AppError::PayloadTooLarge) => StatusCode::PAYLOAD_TOO_LARGE,
            Self::Application(AppError::UnsupportedImageFormat) => {
                StatusCode::UNSUPPORTED_MEDIA_TYPE
            }
            Self::Application(AppError::InvalidImage | AppError::ImageDimensionsTooLarge) => {
                StatusCode::BAD_REQUEST
            }
            Self::Application(AppError::ImageProcessingFailure) => {
                StatusCode::INTERNAL_SERVER_ERROR
            }
        };

        status.into_response()
    }
}

#[cfg(test)]
mod tests {
    use axum::{body::to_bytes, http::StatusCode, response::IntoResponse};

    use super::ApiError;
    use crate::error::AppError;

    #[tokio::test]
    async fn maps_errors_without_exposing_internal_details() {
        let cases = [
            (
                ApiError::InvalidContentType,
                StatusCode::UNSUPPORTED_MEDIA_TYPE,
            ),
            (ApiError::BodyTooLarge, StatusCode::PAYLOAD_TOO_LARGE),
            (
                ApiError::from(AppError::PayloadTooLarge),
                StatusCode::PAYLOAD_TOO_LARGE,
            ),
            (
                ApiError::from(AppError::UnsupportedImageFormat),
                StatusCode::UNSUPPORTED_MEDIA_TYPE,
            ),
            (
                ApiError::from(AppError::InvalidImage),
                StatusCode::BAD_REQUEST,
            ),
            (
                ApiError::from(AppError::ImageDimensionsTooLarge),
                StatusCode::BAD_REQUEST,
            ),
            (
                ApiError::from(AppError::ImageProcessingFailure),
                StatusCode::INTERNAL_SERVER_ERROR,
            ),
        ];

        for (error, expected_status) in cases {
            let response = error.into_response();

            assert_eq!(response.status(), expected_status);
            let body = to_bytes(response.into_body(), usize::MAX)
                .await
                .expect("error response body must be readable");
            assert!(body.is_empty());
        }
    }
}
