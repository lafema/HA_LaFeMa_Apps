package com.aqara.bridge;

import org.apache.rocketmq.client.consumer.DefaultMQPushConsumer;
import org.apache.rocketmq.client.consumer.listener.ConsumeConcurrentlyStatus;
import org.apache.rocketmq.client.consumer.listener.MessageListenerConcurrently;
import org.apache.rocketmq.client.consumer.rebalance.AllocateMessageQueueAveragely;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.acl.common.AclClientRPCHook;
import org.apache.rocketmq.acl.common.SessionCredentials;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONArray;
import com.alibaba.fastjson.JSONObject;

public class App {
    private static MqttClient mqttClient;

    public static void main(String[] args) throws Exception {
        // Read variables from the environment
        String rmqAddr = System.getenv("RMQ_ENDPOINT");
        String rmqTopic = System.getenv("RMQ_TOPIC");
        String rmqGroup = System.getenv("RMQ_GROUP");
        String accessKey = System.getenv("RMQ_ACCESS_KEY");
        String secretKey = System.getenv("RMQ_SECRET_KEY");
        String mqttUrl = System.getenv("MQTT_URL");

        // 2. Sicherheits-Check
        if (mqttUrl == null || rmqAddr == null || accessKey == null) {
            System.err.println("-------------------------------------------------------");
            System.err.println("❌ FEHLER: Fehlende Konfiguration in der .env Datei!");
            if (mqttUrl == null) System.err.println("👉 MQTT_URL fehlt (z.B. tcp://192.168.x.x:1883)");
            if (rmqAddr == null) System.err.println("👉 RMQ_ENDPOINT fehlt");
            if (accessKey == null) System.err.println("👉 RMQ_ACCESS_KEY fehlt");
            System.err.println("-------------------------------------------------------");
            System.exit(1);
        }

        // MQTT initialisieren & verbinden
        try {
            mqttClient = new MqttClient(mqttUrl, "AqaraRocketMQBridge", new MemoryPersistence());
            MqttConnectOptions options = new MqttConnectOptions();
            options.setAutomaticReconnect(true);
            options.setCleanSession(true);
            options.setConnectionTimeout(10);

            // Falls dein Broker User/Pass nutzt:
            if (System.getenv("MQTT_USER") != null) {
                options.setUserName(System.getenv("MQTT_USER"));
                options.setPassword(System.getenv("MQTT_PASS").toCharArray());
            }

            System.out.println("⏳ Verbinde mit MQTT Broker: " + mqttUrl);
            mqttClient.connect(options);
            System.out.println("✅ MQTT verbunden!");
        } catch (Exception e) {
            System.err.println("❌ MQTT Verbindungsfehler: " + e.getMessage());
            // Wir brechen hier nicht ab, da AutomaticReconnect später greifen kann
        }

        // 4. RocketMQ Consumer Setup
        AclClientRPCHook rpcHook = new AclClientRPCHook(new SessionCredentials(accessKey, secretKey));
        DefaultMQPushConsumer consumer = new DefaultMQPushConsumer(rmqGroup, rpcHook, new AllocateMessageQueueAveragely());
        consumer.setNamesrvAddr(rmqAddr);
        consumer.subscribe(rmqTopic, "*");

        // 5. Nachrichten-Verarbeitung
        consumer.registerMessageListener((MessageListenerConcurrently) (msgs, context) -> {
            for (MessageExt msg : msgs) {
                try {
                    String body = new String(msg.getBody());
                    JSONObject json = JSON.parseObject(body);
                    JSONArray dataArray = json.getJSONArray("data");

                    if (dataArray != null) {
                        for (int i = 0; i < dataArray.size(); i++) {
                            JSONObject item = dataArray.getJSONObject(i);
                            String subjectId = item.getString("subjectId");
                            String resourceId = item.getString("resourceId");
                            String value = item.getString("value");

                            // Topic-Struktur: aqara/[GeräteID]/[SensorTyp]
                            String mqttTopic = "aqara/" + subjectId + "/" + resourceId;

                            if (mqttClient != null && mqttClient.isConnected()) {
                                MqttMessage mqttMsg = new MqttMessage(value.getBytes());
                                mqttMsg.setQos(0);
                                mqttClient.publish(mqttTopic, mqttMsg);
                                System.out.println("📤 MQTT -> " + mqttTopic + ": " + value);
                            }
                        }
                    }
                } catch (Exception e) {
                    System.err.println("⚠️ Fehler bei Nachrichten-Weiterleitung: " + e.getMessage());
                }
            }
            return ConsumeConcurrentlyStatus.CONSUME_SUCCESS;
        });

        consumer.start();
        System.out.println("🚀 Bridge gestartet: RocketMQ (" + rmqTopic + ") -> MQTT");
    }
}