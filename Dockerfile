FROM python:3.13.5-slim
WORKDIR /workspace/DriveClarify
COPY requirements/cpu.lock.txt requirements/cpu.lock.txt
RUN python -m venv /opt/reproduce && \
    /opt/reproduce/bin/python -m pip install --no-cache-dir --require-hashes \
    --only-binary=:all: -r requirements/cpu.lock.txt
COPY . .
RUN /opt/reproduce/bin/python -m pip install --no-build-isolation --no-deps .
ENTRYPOINT ["bash", "reproduce.sh", "--venv", "/opt/reproduce", "--skip-install"]

