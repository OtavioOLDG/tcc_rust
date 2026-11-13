FROM rust:1.98.0-bookworm@sha256:82150a52ec202c1b14d7817e14516c392bb7f5cfebd88f1ed531cb37ebd39922 AS builder

WORKDIR /app

COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY src ./src

RUN cargo build --release --locked

FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS runtime

WORKDIR /app

COPY --from=builder \
    --chown=10001:10001 \
    /app/target/release/rust-benchmark-api \
    /app/rust-benchmark-api

USER 10001:10001

ENV RUST_BENCHMARK_ADDRESS=0.0.0.0:3000

EXPOSE 3000

ENTRYPOINT ["/app/rust-benchmark-api"]