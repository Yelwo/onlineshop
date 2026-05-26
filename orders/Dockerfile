FROM python:3.10-slim

WORKDIR /app

# Per-gateway Graftcode index URL. The free-tier token changes on every pricing
# rebuild, so it is passed at build time. `make build-orders` captures it from the
# running gateway's GUID (https://grft.dev/simple/<GUID>__free).
ARG GRAFT_INDEX_URL

COPY pyproject.toml orders.py app.py ./
ENV graftcode-config="name=pricing;runtime=python"

# Install the pricing graft client from the Graftcode index, then orders' own deps.
RUN pip install --no-cache-dir --extra-index-url "$GRAFT_INDEX_URL" "graft-pypi-pricing==0.1.0" \
    && pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
