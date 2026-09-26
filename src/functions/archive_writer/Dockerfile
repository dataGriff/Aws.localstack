# Archive writer + table setup. Build context is the repository root (see .dockerignore).
FROM public.ecr.aws/lambda/python:3.12

COPY build/requirements-writer.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}" \
 && rm -rf /root/.cache

COPY src/shared "${LAMBDA_TASK_ROOT}/shared"
COPY src/iceberg "${LAMBDA_TASK_ROOT}/iceberg"
COPY src/functions/__init__.py "${LAMBDA_TASK_ROOT}/functions/__init__.py"
COPY src/functions/archive_writer/__init__.py \
     src/functions/archive_writer/app.py \
     src/functions/archive_writer/writer.py \
     src/functions/archive_writer/table_setup.py \
     "${LAMBDA_TASK_ROOT}/functions/archive_writer/"

ENV PYICEBERG_HOME=/tmp
CMD ["functions.archive_writer.app.handler"]
