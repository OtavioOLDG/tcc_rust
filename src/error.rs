#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AppError {
    PayloadTooLarge,
    UnsupportedImageFormat,
    InvalidImage,
    ImageDimensionsTooLarge,
    ImageProcessingFailure,
}
