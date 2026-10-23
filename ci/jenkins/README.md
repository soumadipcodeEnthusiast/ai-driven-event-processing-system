# Local Jenkins

Runs the repository's [`Jenkinsfile`](../../Jenkinsfile) on a Jenkins controller in Docker. You get the same stages CI runs, against your current working tree.

```bash
ci/jenkins/run-local.sh               # snapshot working tree → start Jenkins → run a build → print stage results
ci/jenkins/run-local.sh --load-test   # … plus the JMeter stage (stack up via `make up`, JMETER_HOME set)
ci/jenkins/run-local.sh --no-build    # just start Jenkins: http://127.0.0.1:9095
ci/jenkins/run-local.sh --stop
```

## How it works

| Piece | What it does |
|---|---|
| `Dockerfile` | `jenkins/jenkins:lts` + git, make, docker CLI, Python 3.11 (copied from `python:3.11-slim`) + the plugins in `plugins.txt` |
| `init.groovy.d/01-local-security.groovy` | No login (Jenkins is bound to 127.0.0.1 only), 4 executors |
| `init.groovy.d/02-pipeline-job.groovy` | Creates the `ai-event-processor` pipeline job: Jenkinsfile from SCM, with the load-test parameters |
| `run-local.sh` | Snapshots the working tree (tracked + untracked, honouring `.gitignore`) into a throwaway git repo under `~/.cache/ai-event-jenkins/snapshot`, starts Jenkins, triggers a build and prints per-stage results |

Design notes:

- **Your repo is never modified or committed to.** Jenkins checks out the snapshot repo, branch `ci-snapshot`. The Jenkinsfile's `cleanWs()` only ever deletes Jenkins' own workspace.
- **Docker-outside-of-Docker.** The host's Docker socket is mounted, so `docker build` and the promtool/terraform containers run on the host daemon. `JENKINS_HOME` is mounted at the same path inside and outside the container, which lets `docker run -v "$PWD":…` steps resolve workspace paths.
- **Caches.** Maven is seeded once from `~/.m2`, and pip downloads persist in `~/.cache/ai-event-jenkins/pip-cache`. Terraform providers are shared with `make tf-validate` through `~/.terraform.d/plugin-cache`.
- **Load test.** When the compose stack is running, the container joins its network (`ai-event-processor_event-net`), so the JMeter stage reaches `kafka:29092`, `ingestion-service:8080` and the other services by name. JMeter is mounted from `JMETER_HOME`.

Expect the load-test stage to fail locally on **REQ-004** (broker ack p95 ≤ 50 ms): a single Kafka broker on Docker Desktop measured p95 171 ms at 1 000 events/s, with everything else passing (10/11). The gate is meant for the multi-broker cluster. Docker Desktop also needs about 4 GB free for the Test stage (embedded Kafka + JVM + mypy in parallel); with less, steps stall and are killed.

Stages (see the Jenkinsfile): Build (Maven + 3 × pip) → Test (JUnit + embedded-Kafka IT; ruff, mypy and pytest for the three Python packages; promtool; terraform validate) → Docker Build (4 images) → optional Load test (JMeter) → Push to ECR (main branch only).
