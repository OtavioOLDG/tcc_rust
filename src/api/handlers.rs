use axum::{
    Json,
    body::to_bytes,
    extract::Request,
    http::{HeaderMap, HeaderValue, header},
    response::{IntoResponse, Response},
};
use serde::Serialize;

use super::error::ApiError;
use crate::application::services::{MAX_INPUT_SIZE, process_grayscale};

#[derive(Serialize)]
pub(crate) struct HealthResponse {
    status: &'static str,
}

#[derive(Serialize)]
pub(crate) struct MessageResponse {
    message: &'static str,
}

pub(crate) async fn health() -> Json<HealthResponse> {
    Json(HealthResponse { status: "ok" })
}

pub(crate) async fn message() -> Json<MessageResponse> {
    Json(MessageResponse {
        message: "API Rust funcionando",
    })
}

pub(crate) async fn image_grayscale(request: Request) -> Result<Response, ApiError> {
    if !has_supported_content_type(request.headers()) {
        return Err(ApiError::InvalidContentType);
    }

    let body = to_bytes(request.into_body(), MAX_INPUT_SIZE)
        .await
        .map_err(|_| ApiError::BodyTooLarge)?;
    let output = process_grayscale(body.as_ref())?;

    Ok(png_response(output))
}

fn has_supported_content_type(headers: &HeaderMap) -> bool {
    headers
        .get(header::CONTENT_TYPE)
        .is_some_and(|value| matches!(value.as_bytes(), b"image/png" | b"image/jpeg"))
}

fn png_response(output: Vec<u8>) -> Response {
    let mut response = output.into_response();
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static("image/png"));
    response
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use axum::{
        body::{Body, Bytes, to_bytes},
        extract::Request,
        handler::Handler,
        http::{Method, StatusCode, header},
        response::IntoResponse,
        routing::post,
    };
    use image::{DynamicImage, ImageFormat, ImageReader, Rgb, RgbImage};

    use super::{health, image_grayscale, message};
    use crate::application::services::{MAX_IMAGE_DIMENSION, MAX_INPUT_SIZE};

    const VALID_ONE_PIXEL_GIF: &[u8] = &[
        0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00, 0x00, 0x00,
        0x00, 0xff, 0xff, 0xff, 0x21, 0xf9, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00, 0x2c, 0x00, 0x00,
        0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x02, 0x44, 0x01, 0x00, 0x3b,
    ];

    #[tokio::test]
    async fn health_returns_expected_response() {
        let response = health().await.into_response();

        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get(header::CONTENT_TYPE),
            Some(&header::HeaderValue::from_static("application/json"))
        );

        let body = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("the response body must be readable");
        assert_eq!(body.as_ref(), br#"{"status":"ok"}"#);
    }

    #[tokio::test]
    async fn message_returns_expected_response() {
        let response = message().await.into_response();

        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get(header::CONTENT_TYPE),
            Some(&header::HeaderValue::from_static("application/json"))
        );

        let body = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("the response body must be readable");
        assert_eq!(body.as_ref(), br#"{"message":"API Rust funcionando"}"#);
    }

    #[tokio::test]
    async fn image_grayscale_accepts_valid_png() {
        let input = encode_test_image(ImageFormat::Png, 2, 2);

        let response = call_image_route(image_request(Some("image/png"), input)).await;

        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get(header::CONTENT_TYPE),
            Some(&header::HeaderValue::from_static("image/png"))
        );
    }

    #[tokio::test]
    async fn image_grayscale_accepts_valid_jpeg() {
        let input = encode_test_image(ImageFormat::Jpeg, 2, 2);

        let response = call_image_route(image_request(Some("image/jpeg"), input)).await;

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn image_grayscale_returns_decodable_png_with_preserved_dimensions() {
        let input = encode_test_image(ImageFormat::Png, 3, 2);

        let response = call_image_route(image_request(Some("image/png"), input)).await;
        let output = response_body(response).await;
        let output_format =
            image::guess_format(&output).expect("response image format must be detectable");
        let decoded = decode_png(&output);

        assert_eq!(output_format, ImageFormat::Png);
        assert_eq!((decoded.width(), decoded.height()), (3, 2));
    }

    #[tokio::test]
    async fn image_grayscale_returns_grayscale_pixels() {
        let input = encode_test_image(ImageFormat::Png, 3, 2);

        let response = call_image_route(image_request(Some("image/png"), input)).await;
        let decoded = decode_png(&response_body(response).await).to_rgb8();

        for pixel in decoded.pixels() {
            let [red, green, blue] = pixel.0;
            assert_eq!(red, green);
            assert_eq!(green, blue);
        }
    }

    #[tokio::test]
    async fn image_grayscale_rejects_missing_or_unsupported_content_type() {
        for content_type in [None, Some("application/octet-stream")] {
            let input = encode_test_image(ImageFormat::Png, 1, 1);

            let response = call_image_route(image_request(content_type, input)).await;

            assert_empty_error_response(response, StatusCode::UNSUPPORTED_MEDIA_TYPE).await;
        }
    }

    #[tokio::test]
    async fn image_grayscale_rejects_empty_body() {
        let response = call_image_route(image_request(Some("image/png"), Vec::new())).await;

        assert_empty_error_response(response, StatusCode::BAD_REQUEST).await;
    }

    #[tokio::test]
    async fn image_grayscale_rejects_invalid_image() {
        let input = b"\x89PNG\r\n\x1a\ninvalid PNG data".to_vec();

        let response = call_image_route(image_request(Some("image/png"), input)).await;

        assert_empty_error_response(response, StatusCode::BAD_REQUEST).await;
    }

    #[tokio::test]
    async fn image_grayscale_rejects_body_above_limit() {
        let input = vec![0; MAX_INPUT_SIZE + 1];

        let response = call_image_route(image_request(Some("image/png"), input)).await;

        assert_empty_error_response(response, StatusCode::PAYLOAD_TOO_LARGE).await;
    }

    #[tokio::test]
    async fn image_grayscale_accepts_body_at_limit_before_image_validation() {
        let input = vec![0; MAX_INPUT_SIZE];

        let response = call_image_route(image_request(Some("image/png"), input)).await;

        assert_empty_error_response(response, StatusCode::BAD_REQUEST).await;
    }

    #[tokio::test]
    async fn image_grayscale_rejects_dimensions_above_limit() {
        let input = encode_test_image(ImageFormat::Png, MAX_IMAGE_DIMENSION + 1, 1);

        let response = call_image_route(image_request(Some("image/png"), input)).await;

        assert_empty_error_response(response, StatusCode::BAD_REQUEST).await;
    }

    #[tokio::test]
    async fn image_grayscale_rejects_unsupported_image_format() {
        let response = call_image_route(image_request(
            Some("image/png"),
            VALID_ONE_PIXEL_GIF.to_vec(),
        ))
        .await;

        assert_empty_error_response(response, StatusCode::UNSUPPORTED_MEDIA_TYPE).await;
    }

    fn image_request(content_type: Option<&str>, body: Vec<u8>) -> Request {
        let mut builder = Request::builder()
            .method(Method::POST)
            .uri("/image/grayscale");

        if let Some(content_type) = content_type {
            builder = builder.header(header::CONTENT_TYPE, content_type);
        }

        builder
            .body(Body::from(body))
            .expect("test request must be valid")
    }

    async fn call_image_route(request: Request) -> axum::response::Response {
        post(image_grayscale).call(request, ()).await
    }

    fn encode_test_image(format: ImageFormat, width: u32, height: u32) -> Vec<u8> {
        let pixels = RgbImage::from_fn(width, height, |x, y| {
            if (x + y) % 2 == 0 {
                Rgb([240, 30, 10])
            } else {
                Rgb([20, 100, 220])
            }
        });
        let image = DynamicImage::ImageRgb8(pixels);
        let mut output = Cursor::new(Vec::new());
        image
            .write_to(&mut output, format)
            .expect("test image must be encodable");
        output.into_inner()
    }

    fn decode_png(input: &[u8]) -> DynamicImage {
        ImageReader::with_format(Cursor::new(input), ImageFormat::Png)
            .decode()
            .expect("response body must be a decodable PNG")
    }

    async fn response_body(response: axum::response::Response) -> Bytes {
        to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("response body must be readable")
    }

    async fn assert_empty_error_response(
        response: axum::response::Response,
        expected_status: StatusCode,
    ) {
        assert_eq!(response.status(), expected_status);
        assert!(response_body(response).await.is_empty());
    }
}
