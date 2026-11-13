use std::io::Cursor;

use image::{ImageFormat, ImageReader, Limits};

use crate::error::AppError;

pub const MAX_INPUT_SIZE: usize = 5 * 1024 * 1024;
pub const MAX_IMAGE_DIMENSION: u32 = 4096;

pub fn process_grayscale(input: &[u8]) -> Result<Vec<u8>, AppError> {
    validate_input(input)?;

    let format = detect_supported_format(input)?;
    let (width, height) = read_dimensions(input, format)?;
    validate_dimensions(width, height)?;

    let image = decode_with_limits(input, format)?;
    validate_dimensions(image.width(), image.height())?;

    let grayscale = image.grayscale();
    let mut output = Cursor::new(Vec::new());
    grayscale
        .write_to(&mut output, ImageFormat::Png)
        .map_err(|_| AppError::ImageProcessingFailure)?;

    Ok(output.into_inner())
}

fn validate_input(input: &[u8]) -> Result<(), AppError> {
    if input.is_empty() {
        return Err(AppError::InvalidImage);
    }

    if input.len() > MAX_INPUT_SIZE {
        return Err(AppError::PayloadTooLarge);
    }

    Ok(())
}

fn detect_supported_format(input: &[u8]) -> Result<ImageFormat, AppError> {
    match image::guess_format(input).map_err(|_| AppError::InvalidImage)? {
        format @ (ImageFormat::Png | ImageFormat::Jpeg) => Ok(format),
        _ => Err(AppError::UnsupportedImageFormat),
    }
}

fn read_dimensions(input: &[u8], format: ImageFormat) -> Result<(u32, u32), AppError> {
    ImageReader::with_format(Cursor::new(input), format)
        .into_dimensions()
        .map_err(|_| AppError::InvalidImage)
}

fn validate_dimensions(width: u32, height: u32) -> Result<(), AppError> {
    if width > MAX_IMAGE_DIMENSION || height > MAX_IMAGE_DIMENSION {
        return Err(AppError::ImageDimensionsTooLarge);
    }

    Ok(())
}

fn decode_with_limits(input: &[u8], format: ImageFormat) -> Result<image::DynamicImage, AppError> {
    let mut reader = ImageReader::with_format(Cursor::new(input), format);
    let mut limits = Limits::default();
    limits.max_image_width = Some(MAX_IMAGE_DIMENSION);
    limits.max_image_height = Some(MAX_IMAGE_DIMENSION);
    reader.limits(limits);

    reader.decode().map_err(|_| AppError::InvalidImage)
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use image::{DynamicImage, ImageFormat, ImageReader, Rgb, RgbImage};

    use super::{MAX_IMAGE_DIMENSION, MAX_INPUT_SIZE, process_grayscale};
    use crate::error::AppError;

    const VALID_ONE_PIXEL_GIF: &[u8] = &[
        0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00, 0x00, 0x00,
        0x00, 0xff, 0xff, 0xff, 0x21, 0xf9, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00, 0x2c, 0x00, 0x00,
        0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x02, 0x44, 0x01, 0x00, 0x3b,
    ];

    #[test]
    fn accepts_valid_png() {
        let input = encode_test_image(ImageFormat::Png, 2, 2);

        let output = process_grayscale(&input).expect("valid PNG input must be processed");

        assert!(!output.is_empty());
    }

    #[test]
    fn accepts_valid_jpeg() {
        let input = encode_test_image(ImageFormat::Jpeg, 2, 2);

        let output = process_grayscale(&input).expect("valid JPEG input must be processed");

        assert!(!output.is_empty());
    }

    #[test]
    fn encodes_output_as_png() {
        let input = encode_test_image(ImageFormat::Jpeg, 2, 2);

        let output = process_grayscale(&input).expect("valid input must be processed");
        let output_format =
            image::guess_format(&output).expect("service output format must be detectable");

        assert_eq!(output_format, ImageFormat::Png);
    }

    #[test]
    fn preserves_image_dimensions() {
        let input = encode_test_image(ImageFormat::Png, 3, 2);

        let output = process_grayscale(&input).expect("valid input must be processed");
        let decoded = decode_png(&output);

        assert_eq!((decoded.width(), decoded.height()), (3, 2));
    }

    #[test]
    fn converts_every_output_pixel_to_grayscale() {
        let input = encode_test_image(ImageFormat::Png, 3, 2);

        let output = process_grayscale(&input).expect("valid input must be processed");
        let decoded = decode_png(&output).to_rgb8();

        for pixel in decoded.pixels() {
            let [red, green, blue] = pixel.0;
            assert_eq!(red, green);
            assert_eq!(green, blue);
        }
    }

    #[test]
    fn produces_deterministic_output() {
        let input = encode_test_image(ImageFormat::Png, 3, 2);

        let first = process_grayscale(&input).expect("first processing must succeed");
        let second = process_grayscale(&input).expect("second processing must succeed");

        assert_eq!(first, second);
    }

    #[test]
    fn rejects_empty_input() {
        assert_eq!(process_grayscale(&[]), Err(AppError::InvalidImage));
    }

    #[test]
    fn rejects_invalid_image_bytes() {
        let corrupted_png = b"\x89PNG\r\n\x1a\ninvalid PNG data";

        assert_eq!(
            process_grayscale(corrupted_png),
            Err(AppError::InvalidImage)
        );
    }

    #[test]
    fn rejects_unsupported_image_format() {
        assert_eq!(
            process_grayscale(VALID_ONE_PIXEL_GIF),
            Err(AppError::UnsupportedImageFormat)
        );
    }

    #[test]
    fn rejects_dimensions_above_limit() {
        for (width, height) in [(MAX_IMAGE_DIMENSION + 1, 1), (1, MAX_IMAGE_DIMENSION + 1)] {
            let input = encode_test_image(ImageFormat::Png, width, height);

            assert_eq!(
                process_grayscale(&input),
                Err(AppError::ImageDimensionsTooLarge)
            );
        }
    }

    #[test]
    fn accepts_dimensions_at_limit() {
        for (width, height) in [(MAX_IMAGE_DIMENSION, 1), (1, MAX_IMAGE_DIMENSION)] {
            let input = encode_test_image(ImageFormat::Png, width, height);

            let output = process_grayscale(&input).expect("dimensions at limit must be accepted");
            let decoded = decode_png(&output);

            assert_eq!((decoded.width(), decoded.height()), (width, height));
        }
    }

    #[test]
    fn accepts_one_pixel_image() {
        let input = encode_test_image(ImageFormat::Png, 1, 1);

        let output = process_grayscale(&input).expect("one-pixel image must be processed");
        let decoded = decode_png(&output);

        assert_eq!((decoded.width(), decoded.height()), (1, 1));
    }

    #[test]
    fn rejects_input_above_size_limit() {
        let input = vec![0; MAX_INPUT_SIZE + 1];

        assert_eq!(process_grayscale(&input), Err(AppError::PayloadTooLarge));
    }

    #[test]
    fn accepts_input_size_at_limit_before_format_validation() {
        let input = vec![0; MAX_INPUT_SIZE];

        assert_eq!(process_grayscale(&input), Err(AppError::InvalidImage));
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
            .expect("service output must be a decodable PNG")
    }
}
