# P0-8 supply-chain: pin the base image by DIGEST (not the mutable :3.12-slim tag) and run as a
# non-root user. The digest is python:3.12-slim resolved 2026-09-08; re-resolve on a deliberate base bump.
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

WORKDIR /app

# non-root runtime user (P0-8): create an unprivileged user up front and drop to it before CMD.
RUN groupadd -r app && useradd -r -g app -u 10001 app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app agent.py .
ENV PYTHONUNBUFFERED=1

USER 10001
CMD ["opentelemetry-instrument", "python", "-u", "agent.py"]
