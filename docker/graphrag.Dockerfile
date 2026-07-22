# docker/graphrag.Dockerfile -- GraphRAG worker image (Task 9B-2B)
# Pinned dependency versions for reproducible builds.

FROM python:3.12-slim

WORKDIR /app

# Install pinned dependencies in a single layer.
RUN pip install --no-cache-dir \
      graphrag==0.1.1 \
      fastapi==0.116.1 \
      uvicorn==0.35.0 \
      pandas==2.2.3 \
      pyarrow==15.0.0 \
      httpx==0.28.1

# Copy API code into the image.
COPY graphrag_api/ /app/graphrag_api

# Expose the FastAPI port (informational).
EXPOSE 8111

# Start the FastAPI server.
CMD ["uvicorn", "graphrag_api.main:app", "--host", "0.0.0.0", "--port", "8111"]
