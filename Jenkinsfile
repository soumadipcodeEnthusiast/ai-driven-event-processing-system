// Jenkinsfile — CI/CD for the AI-Driven Event Processing System (SRS v1.0 §6).
// Replaces the former GitHub Actions workflow (.github/workflows/ci.yml, removed).
//
// Requires on the agent: JDK 17, Python 3.11, Docker with Buildx, AWS CLI v2.
// Images are pushed to Amazon ECR (infra/terraform/ecr.tf): set the ECR_REGISTRY
// job/env variable to the `ecr_registry` Terraform output, and add an AWS
// credential "aws-ecr-credentials" (AWS Credentials plugin) with ECR push rights.

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    parameters {
        booleanParam(name: 'RUN_LOAD_TEST', defaultValue: false,
                     description: 'Run the JMeter load test (loadtest/) against a running stack. ' +
                                  'Needs JMETER_HOME on the agent and the stack reachable (ci/jenkins/run-local.sh wires both).')
        string(name: 'LOAD_RATE', defaultValue: '2000', description: 'Target events/s for the load test')
        string(name: 'LOAD_DURATION', defaultValue: '60', description: 'Load test duration, seconds')
    }

    environment {
        JAVA_VERSION   = '17'
        PYTHON_VERSION = '3.11'
        AWS_REGION     = 'ap-south-1'
        // <account>.dkr.ecr.<region>.amazonaws.com — `terraform output ecr_registry`
        REGISTRY       = "${env.ECR_REGISTRY ?: '000000000000.dkr.ecr.ap-south-1.amazonaws.com'}"
        REPO_PREFIX    = 'ai-event-processor'  // = var.cluster_name in Terraform
        IMAGE_TAG      = "${env.BUILD_NUMBER}-${env.GIT_COMMIT?.take(8) ?: 'local'}"
        // Ride out transient DNS/network failures on package downloads.
        PIP_RETRIES          = '10'
        PIP_DEFAULT_TIMEOUT  = '60'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
    }

    stages {

        stage('Build') {
            parallel {
                stage('ingestion-service (Maven)') {
                    steps {
                        dir('ingestion-service') {
                            sh 'chmod +x mvnw'
                            sh './mvnw -B -q -DskipTests package'
                        }
                    }
                }
                stage('forecasting-service (pip)') {
                    steps {
                        dir('forecasting-service') {
                            sh 'python3 -m venv .venv'
                            sh '. .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt -r requirements-dev.txt'
                        }
                    }
                }
                stage('diagnostic-service (pip)') {
                    steps {
                        dir('diagnostic-service') {
                            sh 'python3 -m venv .venv'
                            sh '. .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt -r requirements-dev.txt'
                        }
                    }
                }
                stage('event-archiver (pip)') {
                    steps {
                        dir('storage') {
                            sh 'python3 -m venv .venv'
                            sh '. .venv/bin/activate && pip install --upgrade pip && pip install -r requirements-dev.txt'
                        }
                    }
                }
            }
        }

        stage('Test') {
            parallel {
                stage('ingestion-service — unit + integration') {
                    steps {
                        dir('ingestion-service') {
                            sh './mvnw -B verify'
                        }
                    }
                    post {
                        always {
                            junit testResults: 'ingestion-service/target/surefire-reports/*.xml', allowEmptyResults: true
                        }
                    }
                }
                stage('forecasting-service — lint, typecheck, test') {
                    steps {
                        dir('forecasting-service') {
                            sh '. .venv/bin/activate && ruff check .'
                            sh '. .venv/bin/activate && mypy app'
                            sh '. .venv/bin/activate && pytest -v --tb=short'
                        }
                    }
                }
                stage('diagnostic-service — lint, typecheck, test') {
                    steps {
                        dir('diagnostic-service') {
                            sh '. .venv/bin/activate && ruff check .'
                            sh '. .venv/bin/activate && mypy app'
                            sh '. .venv/bin/activate && pytest -v --tb=short'
                        }
                    }
                }
                stage('event-archiver — lint, typecheck, test') {
                    steps {
                        dir('storage') {
                            sh '. .venv/bin/activate && ruff check .'
                            sh '. .venv/bin/activate && mypy archive_client archiver.py'
                            sh '. .venv/bin/activate && pytest -v --tb=short'
                        }
                    }
                }
                stage('Prometheus rules (promtool)') {
                    steps {
                        sh 'make prom-test'
                    }
                }
                stage('Terraform validate') {
                    steps {
                        sh 'make tf-validate'
                    }
                }
            }
        }

        stage('Docker Build') {
            matrix {
                axes {
                    axis {
                        name 'SERVICE'
                        values 'ingestion-service', 'forecasting-service', 'diagnostic-service', 'event-archiver', 'demo-console'
                    }
                }
                stages {
                    stage('build') {
                        steps {
                            // event-archiver's build context is storage/, demo-console's is console/;
                            // forecasting-service is built with the TFT (PyTorch) extra.
                            sh '''
                                CONTEXT="./$SERVICE"; ARGS=""
                                if [ "$SERVICE" = event-archiver ]; then CONTEXT=./storage; fi
                                if [ "$SERVICE" = demo-console ]; then CONTEXT=./console; fi
                                if [ "$SERVICE" = forecasting-service ]; then ARGS="--build-arg INSTALL_ML=true"; fi
                                docker build $ARGS -t "$REGISTRY/$REPO_PREFIX/$SERVICE:$IMAGE_TAG" "$CONTEXT"
                            '''
                        }
                    }
                }
            }
        }

        stage('Load test (JMeter)') {
            when {
                expression { return params.RUN_LOAD_TEST }
            }
            steps {
                // REQ-001 / REQ-004: fails the build if any requirement check fails.
                sh "loadtest/run-load-test.sh --rate ${params.LOAD_RATE} --duration ${params.LOAD_DURATION}"
            }
            post {
                always {
                    archiveArtifacts artifacts: 'loadtest/results/**', allowEmptyArchive: true
                }
            }
        }

        stage('Push') {
            when {
                branch 'main'
            }
            matrix {
                axes {
                    axis {
                        name 'SERVICE'
                        values 'ingestion-service', 'forecasting-service', 'diagnostic-service', 'event-archiver'
                    }
                }
                stages {
                    stage('push') {
                        steps {
                            withCredentials([[
                                $class: 'AmazonWebServicesCredentialsBinding',
                                credentialsId: 'aws-ecr-credentials'
                            ]]) {
                                sh '''
                                    aws ecr get-login-password --region "$AWS_REGION" | docker login "$REGISTRY" -u AWS --password-stdin
                                    docker push "$REGISTRY/$REPO_PREFIX/$SERVICE:$IMAGE_TAG"
                                    docker logout "$REGISTRY"
                                '''
                            }
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            cleanWs()
        }
    }
}
