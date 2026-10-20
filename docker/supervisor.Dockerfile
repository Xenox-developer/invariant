FROM rust:1.90-bookworm@sha256:3914072ca0c3b8aad871db9169a651ccfce30cf58303e5d6f2db16d1d8a7e58f AS build
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates
COPY docker ./docker
RUN cargo build --locked --release -p invariant-supervisor
FROM docker:27.4-cli@sha256:af0bcc63244e5110ac6499e4a9160a67101bf61abda35c2503ffc40038e8ee0a AS cli
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
COPY --from=cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=build /build/target/release/invariant-supervisor /usr/local/bin/invariant-supervisor
COPY --from=build /build/target/release/invariant-acceptance /usr/local/bin/invariant-acceptance
ENTRYPOINT ["invariant-supervisor"]
