FROM python:3.12-slim@sha256:d86b4c74b936c438cd4cc3a9f7256b9a7c27ad68c7caf8c205e18d9845af0164
RUN pip install --no-cache-dir pypdf==6.21.4
COPY services/documents/src/atlas_documents/pdf_worker.py /pdf_worker.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
USER 65534:65534
ENTRYPOINT ["python", "/pdf_worker.py"]
