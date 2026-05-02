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

# Install jq to view the HA options
RUN apt-get update && apt-get install -y jq && rm -rf /var/lib/apt/lists/*

# Create a startup script that maps the options
RUN echo '#!/bin/bash \n\
export RMQ_ENDPOINT=$(jq --raw-output ".RMQ_ENDPOINT" /data/options.json) \n\
export RMQ_TOPIC=$(jq --raw-output ".RMQ_TOPIC" /data/options.json) \n\
export RMQ_GROUP=$(jq --raw-output ".RMQ_GROUP" /data/options.json) \n\
export RMQ_ACCESS_KEY=$(jq --raw-output ".RMQ_ACCESS_KEY" /data/options.json) \n\
export RMQ_SECRET_KEY=$(jq --raw-output ".RMQ_SECRET_KEY" /data/options.json) \n\
export MQTT_URL=$(jq --raw-output ".MQTT_URL" /data/options.json) \n\
export MQTT_USER=$(jq --raw-output ".MQTT_USER" /data/options.json) \n\
export MQTT_PASS=$(jq --raw-output ".MQTT_PASS" /data/options.json) \n\
java --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-opens java.base/java.net=ALL-UNNAMED -jar app.jar' > /app/run.sh

RUN chmod +x /app/run.sh

# Start command
CMD ["/app/run.sh"]
