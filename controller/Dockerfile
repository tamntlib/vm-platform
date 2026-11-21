FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 ANSIBLE_HOME=/run/work/ansible ANSIBLE_COLLECTIONS_PATH=/opt/collections TMPDIR=/run/work
COPY controller/requirements.lock ansible/requirements.yml /opt/toolchain/
RUN python -c "from pathlib import Path; c=Path('/sys/fs/cgroup/cpu.max').read_text().split(); m=Path('/sys/fs/cgroup/memory.max').read_text().strip(); assert c[0]!='max' and int(c[0])/int(c[1])<=2; assert m!='max' and int(m)<=1073741824; print('Build limits verified:',c,m)" && mkdir -p /run/work && pip install --root-user-action=ignore --no-cache-dir -r /opt/toolchain/requirements.lock && ansible-galaxy collection install -r /opt/toolchain/requirements.yml -p /opt/collections
ENV ANSIBLE_LOCAL_TEMP=/run/work/ansible-local ANSIBLE_REMOTE_TEMP=/run/work/ansible-remote
WORKDIR /workspace
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "/workspace/tests"]
