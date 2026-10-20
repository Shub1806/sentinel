# syntax=docker/dockerfile:1

# ---- Stage 1: build the jar with the full JDK ----
FROM eclipse-temurin:21-jdk-noble AS build
WORKDIR /src
COPY .mvn .mvn
COPY mvnw pom.xml ./
COPY src src
# The cache mount keeps downloaded libraries between builds, so rebuilds are fast.
# Tests are skipped here: they need Docker (Testcontainers) and run separately, e.g. in CI.
RUN --mount=type=cache,target=/root/.m2 \
    ./mvnw -B -q -DskipTests package \
 && cp target/orders-*.jar /src/app.jar

# ---- Stage 2: runtime with only the JRE (smaller, no compiler) ----
FROM eclipse-temurin:21-jre-noble
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /src/app.jar app.jar
USER 10001
EXPOSE 8080
# Heap = up to 75% of the container's memory limit; exit cleanly on out-of-memory
# so Kubernetes restarts the pod instead of leaving it half-broken.
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75 -XX:+ExitOnOutOfMemoryError"
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
