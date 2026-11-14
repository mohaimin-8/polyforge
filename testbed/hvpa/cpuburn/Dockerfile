# syntax=docker/dockerfile:1
# WP17 service image (testbed/hvpa/cpuburn). Build context: this directory.
# Base images pinned by digest, the same ones as Dockerfile.gateway.
FROM golang:1.26.10@sha256:672c944cfd07599b5f1bb179869270cdc94c6254e41db4d431259693cb286b82 AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/cpuburn .

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /out/cpuburn /cpuburn
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/cpuburn"]
