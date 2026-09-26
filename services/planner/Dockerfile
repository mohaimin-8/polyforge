# JCAC planner service image (deploy/helm/polyforge-operator, planner.image).
# Stdlib-only HTTP service (ADR 0014) plus the shared simulator model it
# imports via a repo-relative sys.path — so the image preserves the repo
# layout (planner.py resolves ../../research/jcac_sim from its own path).
# Base images are pinned by digest (audit 2026-09-26: floating tags could change a
# rebuilt image underneath its signature). Refresh deliberately:
#   docker buildx imagetools inspect <image:tag>  -> the index digest
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b
WORKDIR /app
COPY services/planner/planner.py ./services/planner/
COPY research/jcac_sim/ ./research/jcac_sim/
USER 65532:65532
EXPOSE 8090
ENTRYPOINT ["python", "services/planner/planner.py", "--port", "8090"]
