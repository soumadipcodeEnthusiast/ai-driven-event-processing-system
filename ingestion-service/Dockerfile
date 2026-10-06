# syntax=docker/dockerfile:1
# ingestion-service — Spring Boot 3 / Java 17
# Build:  docker build -t ingestion-service ./ingestion-service
# Requires the Maven wrapper (mvnw + .mvn/) committed in ingestion-service/.

# ─── Stage 1: build ───────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jdk-jammy AS builder

WORKDIR /build

# Wrapper + POM first so the dependency layer is cached until pom.xml changes.
COPY mvnw pom.xml ./
COPY .mvn/ .mvn/
RUN chmod +x mvnw \
    && ./mvnw -B -q dependency:go-offline

COPY src/ src/

# Tests run in CI (`./mvnw verify`); the image build only packages.
RUN ./mvnw -B -q package -DskipTests \
    && cp target/ingestion-service-*.jar /build/app.jar

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jre-jammy AS runtime

LABEL org.opencontainers.image.title="ingestion-service" \
      org.opencontainers.image.description="Kafka event ingestion and normalisation (REQ-A, REQ-B, REQ-C)" \
      org.opencontainers.image.source="https://github.com/your-org/ai-driven-system"

# Fixed UID/GID so Kubernetes `runAsUser: 10001` matches the image user.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=10001:10001 /build/app.jar /app/app.jar

USER 10001:10001

EXPOSE 8080

# Extra JVM flags can be appended at runtime via JAVA_OPTS (compose / k8s).
ENV JAVA_OPTS="-XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0 -Djava.security.egd=file:/dev/./urandom"

# No curl/wget in the JRE image: probe the actuator with bash's /dev/tcp.
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=5 \
    CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/8080 && printf 'GET /actuator/health HTTP/1.0\\r\\nHost: localhost\\r\\n\\r\\n' >&3 && grep -q '\"status\":\"UP\"' <&3"]

# `exec` keeps java as PID 1 so SIGTERM triggers Spring's graceful shutdown.
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
