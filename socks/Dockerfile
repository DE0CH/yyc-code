# yyc-socks (socks/README.md): built and tested by .github/workflows/socks-image.yml
FROM rust:1.99.1-slim-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo test --release --locked && cargo build --release --locked && strip target/release/yyc-socks

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /src/target/release/yyc-socks /usr/local/bin/yyc-socks
ENTRYPOINT ["/usr/local/bin/yyc-socks"]
