FROM node:24.14.0-bookworm-slim AS node
FROM python:3.12.13-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SAMPLE_MODE=true
COPY incidentcourt /app/incidentcourt
COPY scripts/hmem_bridge.ts /app/scripts/hmem_bridge.ts
COPY vendor/meterless-hmem /app/vendor/meterless-hmem
COPY data/sample.json.gz /app/data/sample.json.gz
COPY data/public/sample-manifest.json /app/data/public/sample-manifest.json
COPY run.py /app/run.py
RUN useradd --create-home --uid 10001 investigator && chown -R investigator:investigator /app
USER investigator
EXPOSE 8317
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8317"]
