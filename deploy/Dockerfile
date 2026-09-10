FROM python:3.12.14-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements /app/requirements
RUN python -m pip install --no-cache-dir --require-hashes -r requirements/alpha-py312.txt -r requirements/build-py312.txt
COPY pyproject.toml README.md LICENSE /app/
COPY gap_kernel /app/gap_kernel
COPY examples /app/examples
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation .
USER 1000:1000
