FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --create-home --uid 10001 appuser
COPY pyproject.toml uv.lock README.md ./
COPY support_agent ./support_agent
COPY frontend ./frontend
# uv.lock is the reproducible source of both direct and transitive versions.
RUN pip install --no-cache-dir uv==0.13.5 && uv sync --frozen --no-dev --no-editable
COPY .streamlit ./.streamlit
RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000 8501
CMD ["python", "-m", "support_agent.serve"]
