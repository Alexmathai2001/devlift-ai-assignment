# ---- Stage 1: build the jar ----
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
# Copy pom first so dependency downloads are cached unless pom.xml changes
COPY pom.xml .
RUN mvn -q -B dependency:go-offline
COPY src ./src
RUN mvn -q -B package -DskipTests

# ---- Stage 2: slim runtime image ----
FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
# Run as a non-root user
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /build/target/app.jar app.jar
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "app.jar"]
