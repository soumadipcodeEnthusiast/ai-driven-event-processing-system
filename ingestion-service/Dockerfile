# ─── Stage 1: build ───────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jdk-alpine AS builder

WORKDIR /build

# Copy Maven wrapper + POM first for layer-cache efficiency
COPY pom.xml .
COPY .mvn/ .mvn/ 2>/dev/null || true

# Download dependencies (cached unless pom.xml changes)
RUN --mount=type=cache,target=/root/.m2 \
    test -f mvnw && chmod +x mvnw || true

COPY src/ src/

# Build fat JAR (skip tests in Docker build; tests run in CI)
RUN if [ -f mvnw ]; then \
        ./mvnw -B -q package -DskipTests; \
    else \
        apk add --no-cache maven && mvn -B -q package -DskipTests; \
    fi

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jre-alpine AS runtime

LABEL org.opencontainers.image.title="ingestion-service" \
      org.opencontainers.image.description="Kafka event ingestion and normalisation" \
      org.opencontainers.image.version="0.1.0-SNAPSHOT"

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

COPY --from=builder /build/target/ingestion-service-*.jar app.jar

RUN chown appuser:appgroup app.jar

USER appuser

EXPOSE 8080

ENTRYPOINT ["java", \
    "-XX:+UseContainerSupport", \
    "-XX:MaxRAMPercentage=75.0", \
    "-Djava.security.egd=file:/dev/./urandom", \
    "-jar", "app.jar"]
