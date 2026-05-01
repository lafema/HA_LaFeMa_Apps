# Stage 1: Build with Maven and Temurin JDK
FROM maven:3.8-eclipse-temurin-11 AS build
WORKDIR /app
RUN mkdir -p src/main/java/com/aqara/bridge
COPY pom.xml .
COPY App.java ./src/main/java/com/aqara/bridge/App.java
RUN mvn clean package

# Stage 2: Runtime with the lightweight Eclipse Temurin JRE
FROM eclipse-temurin:11-jre-focal
WORKDIR /app

# Copy the finished programme from the first stage
COPY --from=build /app/target/rocketmq-bridge-1.0.jar app.jar

# Start command
CMD ["java", "-jar", "app.jar"]