FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.13-slim
LABEL org.opencontainers.image.title="LogSlice" \
      org.opencontainers.image.description="Query messy log files with SQL" \
      org.opencontainers.image.source="https://github.com/yagnang-vyas/log-slice" \
      org.opencontainers.image.licenses="MIT"
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl \
    && useradd --create-home logslice
USER logslice
WORKDIR /logs
ENTRYPOINT ["logslice"]
CMD ["--help"]
