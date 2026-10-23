# syntax=docker/dockerfile:1
# Local Jenkins controller for running the repo's Jenkinsfile (ci/jenkins/README.md).
# Build: docker build -t ai-event-processor/jenkins:local ci/jenkins

# Python 3.11 is copied from the official image: the Jenkins image is Debian 13
# (Python 3.13), and the services pin wheels that target 3.11.
FROM python:3.11-slim AS py

FROM jenkins/jenkins:lts

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git make docker-cli \
    && rm -rf /var/lib/apt/lists/*

COPY --from=py /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=py /usr/local/lib/libpython3.11.so.1.0 /usr/local/lib/
COPY --from=py /usr/local/bin/python3.11 /usr/local/bin/
RUN ln -sf /usr/local/lib/libpython3.11.so.1.0 /usr/local/lib/libpython3.11.so \
    && ldconfig \
    && ln -sf /usr/local/bin/python3.11 /usr/local/bin/python3 \
    && python3 --version && python3 -m venv --help >/dev/null

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

# No setup wizard; init scripts disable auth (local only) and create the job.
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false"
COPY init.groovy.d/ /usr/share/jenkins/ref/init.groovy.d/
