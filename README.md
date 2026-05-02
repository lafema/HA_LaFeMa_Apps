# RocketMQ to MQTT Bridge

A lightweight Java-based bridge that forwards messages from an Apache RocketMQ 4.0 server to a local MQTT broker. This application is specifically designed to run in containerized environments and is ready to be used as a Home Assistant Add-on.

## Features

*   **Real-time Relay**: Consumes messages from RocketMQ and publishes them immediately to specific MQTT topics.
*   **ACL Support**: Full support for RocketMQ Access Control Lists (AccessKey/SecretKey) for secure cloud or remote connections.
*   **JSON Processing**: Parses incoming JSON payloads and maps them to a hierarchical MQTT topic structure.
*   **Home Assistant Ready**: Optimized for deployment as a local Home Assistant Add-on.
*   **Memory Persistence**: Uses RAM-based persistence for MQTT to minimize disk I/O, protecting storage media like SD cards in single-board computers.

## Architecture

1.  **Source**: Apache RocketMQ (Remote or Cloud Broker).
2.  **Bridge**: Java 11 Application (JVM) handling the subscription and translation.
3.  **Destination**: Local MQTT Broker (e.g., Mosquitto).

**MQTT Topic Structure:**  
Messages are published to: `bridge/[subjectId]/[resourceId]`  
*Example Output:* `bridge/device_01/sensor_temp` -> Payload: `22.5`

---

## Configuration

The application is configured via environment variables.

| Variable | Description | Example |
| :--- | :--- | :--- |
| `RMQ_ENDPOINT` | RocketMQ NameServer address | `mq-broker.example.com:9876` |
| `RMQ_TOPIC` | RocketMQ topic to subscribe to | `1234567890abcdef...` |
| `RMQ_GROUP` | Consumer group ID (often same as topic) | `1234567890abcdef...` |
| `RMQ_ACCESS_KEY` | RocketMQ Access Key | `K.YOUR_ACCESS_KEY` |
| `RMQ_SECRET_KEY` | RocketMQ Secret Key | `YOUR_SECRET_KEY` |
| `MQTT_URL` | Local MQTT Broker URL | `tcp://192.168.1.100:1883` |
| `MQTT_USER` | MQTT Username (optional) | `mosquitto_user` |
| `MQTT_PASS` | MQTT Password (optional) | `your_password` |

---

## Technical Details

*   **Runtime**: Java 11 (Eclipse Temurin JRE)
*   **RocketMQ Client**: Apache RocketMQ 4.9.4
*   **MQTT Client**: Eclipse Paho 1.2.5
*   **JSON Engine**: Alibaba Fastjson 1.2.83