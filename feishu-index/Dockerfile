# feishu-index (README.md): built and tested by .github/workflows/feishu-index-image.yml (the unit tests run here; the
# end-to-end tests against Postgres run in the workflow's test job).
FROM rust:1.99.1-slim-bookworm AS build
RUN apt-get update -q && apt-get install -yq --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY migrations ./migrations
COPY tests ./tests
RUN cargo test --release --locked --lib && cargo build --release --locked && strip target/release/feishu-index

# pdftotext (poppler-utils) extracts uploaded PDFs; ca-certificates for open.feishu.cn and openrouter.ai
FROM debian:bookworm-slim
RUN apt-get update -q && apt-get install -yq --no-install-recommends poppler-utils ca-certificates && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /src/target/release/feishu-index /usr/local/bin/feishu-index
USER 10001
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/feishu-index"]
