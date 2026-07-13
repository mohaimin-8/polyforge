# JCAC planner service image (deploy/helm/polyforge-operator, planner.image).
# Stdlib-only HTTP service (ADR 0014) plus the shared simulator model it
# imports via a repo-relative sys.path — so the image preserves the repo
# layout (planner.py resolves ../../research/jcac_sim from its own path).
FROM python:3.13-slim
WORKDIR /app
COPY services/planner/planner.py ./services/planner/
COPY research/jcac_sim/ ./research/jcac_sim/
USER 65532:65532
EXPOSE 8090
ENTRYPOINT ["python", "services/planner/planner.py", "--port", "8090"]
