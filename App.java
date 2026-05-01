package com.aqara.bridge;

import org.apache.rocketmq.client.consumer.DefaultMQPushConsumer;
import org.apache.rocketmq.client.consumer.listener.ConsumeConcurrentlyStatus;
import org.apache.rocketmq.client.consumer.listener.MessageListenerConcurrently;
import org.apache.rocketmq.client.consumer.rebalance.AllocateMessageQueueAveragely;
import org.apache.rocketmq.common.message.MessageExt;
import org.apache.rocketmq.acl.common.AclClientRPCHook;
import org.apache.rocketmq.acl.common.SessionCredentials;

public class App {
    public static void main(String[] args) throws Exception {

        // Read variables from the environment
        String addr = System.getenv("RMQ_ENDPOINT");
        String topic = System.getenv("RMQ_TOPIC");
        String group = System.getenv("RMQ_GROUP");
        String accessKey = System.getenv("RMQ_ACCESS_KEY");
        String secretKey = System.getenv("RMQ_SECRET_KEY");

        if (addr == null || topic == null || group == null) {
            System.err.println("Error: The environment variables RMQ_ENDPOINT, RMQ_TOPIC or RMQ_GROUP are missing!");
            System.exit(1);
        }

        // Prepare authentication (ACL)
        AclClientRPCHook rpcHook = new AclClientRPCHook(new SessionCredentials(accessKey, secretKey));

        // Initialise the consumer
        DefaultMQPushConsumer consumer = new DefaultMQPushConsumer(group, rpcHook, new AllocateMessageQueueAveragely());

        consumer.setNamesrvAddr(addr);
        consumer.subscribe(topic, "*");

        // Register the listener for incoming messages
        consumer.registerMessageListener((MessageListenerConcurrently) (msgs, context) -> {
            for (MessageExt msg : msgs) {
                System.out.println("-------------------------------------------");
                System.out.println("Time: " + java.time.LocalDateTime.now());
                System.out.println("Message: " + new String(msg.getBody()));
                System.out.println("-------------------------------------------");
            }
            return ConsumeConcurrentlyStatus.CONSUME_SUCCESS;
        });

        try {
            consumer.start();
            System.out.println("Bridge has been successfully launched!");
            System.out.println("Connected to: " + addr);
            System.out.println("Topic: " + topic);
        } catch (Exception e) {
            System.err.println("Error starting the consumer: " + e.getMessage());
            e.printStackTrace();
        }
    }
}