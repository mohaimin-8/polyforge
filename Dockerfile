# syntax=docker/dockerfile:1
# Multi-stage build (roadmap W9): stage 1 compiles a static binary, stage 2
# copies it into a distroless image — no shell, no package manager, minimal
# CVE surface. CGO is off; the SQLite driver (modernc.org/sqlite) is pure Go,
# so the binary is fully static.
#
# NOT VERIFIED LOCALLY: Docker is not installed on the development machine.
# The image size (<20MB gate) and compose bring-up must be proven in CI or on
# a Docker-capable host before either is claimed.

# Base images are pinned by digest (audit 2026-09-26: floating tags could change a
# rebuilt image underneath its signature). Refresh deliberately:
#   docker buildx imagetools inspect <image:tag>  -> the index digest
FROM golang:1.26.9@sha256:d7722066f0b60ceccb6c0643cbed1f5f9e15506ac237146c95333504d7805d89 AS build
WORKDIR /src

# Dependency layer first so source edits don't invalidate the module cache.
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" \
    -o /out/control-plane ./cmd/control-plane

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /out/control-plane /control-plane
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/control-plane"]
