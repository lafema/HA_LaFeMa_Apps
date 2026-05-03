# Lafema Home Assistant App Repository

This repository contains custom apps for Home Assistant.

## Installation

To add these apps to your Home Assistant instance:

1. Go to **Settings** > **Apps** (Note: On older Home Assistant versions, this is still labeled as **Add-ons**).
2. Click the **Install app** button in the bottom right corner.
3. Click the three-dot menu (⋮) in the top right and select **Repositories**.
4. Add the following URL:
   `https://github.com/lafema/HA_LaFeMa_Apps`
5. Once added, you can find and install the apps directly from the store under the category **Lafema Apps**.

## Available Apps

### RocketMQ to MQTT Bridge
A lightweight Java-based bridge that forwards messages from an Apache RocketMQ 4.0 server to a local MQTT broker. 
This application is specifically designed to run in containerized environments and is ready to be used as a Home 
Assistant App.