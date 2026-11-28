# HouseMind App image. Built by CI for amd64 and aarch64 and signed (E-137, E-143, B-10).
# Fixed to one exact image (QA 3, gat 7); Dependabot proposes a newer one every week.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.13-alpine3.22@sha256:c07d5b77c095f8dac20143fa560f181892bbcad3c4d7611e285cfec9b34ed08e

# The screens are plain files (HTML, JS, CSS, fonts), the same on every architecture, so they
# are built on the machine that builds, never under emulation: npm ci under QEMU for aarch64
# once hung for six hours. The digest is a multi-platform index, so it stays fixed.
FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${BUILD_FROM}
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_LICENSES.md requirements.lock ./
COPY src/ ./src/
# Exactly the versions of uv.lock, checked by their hashes (QA 2, M-33).
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps . && rm -rf /root/.cache
COPY knowledge/ /app/knowledge/
COPY ha_app_stable/CHANGELOG.md /app/CHANGELOG.md
COPY integration/ /app/integration/
COPY --from=frontend /build/dist/ /app/frontend/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "housemind"]
