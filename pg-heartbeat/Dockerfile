# pg-heartbeat (pg-heartbeat/README.md): built and tested by .github/workflows/pg-heartbeat-image.yml
FROM rust:1.99.1-slim-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo test --release --locked --bins && cargo build --release --locked && strip target/release/pg-heartbeat

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /src/target/release/pg-heartbeat /usr/local/bin/pg-heartbeat
ENTRYPOINT ["/usr/local/bin/pg-heartbeat"]
