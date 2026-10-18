"""MQTT client (paho 1.x/2.x) subscribed to <prefix>/#, handing messages to the asyncio loop."""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("gateway.mqtt")


class MqttIn:
    def __init__(self, host: str, port: int, prefix: str, on_message: Callable[[str, bytes], None]):
        import paho.mqtt.client as mqtt

        self.host, self.port, self.prefix = host, port, prefix
        self.state = "connecting"
        self._on_message = on_message
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="")
        except AttributeError:
            self.client = mqtt.Client(client_id="")
        self.client.on_connect = self._connected
        self.client.on_disconnect = self._disconnected
        self.client.on_message = self._message
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)
        try:
            self.client.connect_async(host, port, keepalive=15)
        except Exception as e:  # noqa: BLE001
            log.warning("mqtt: cannot connect to %s:%d: %s", host, port, e)
            self.state = "down"
        self.client.loop_start()

    def _connected(self, client, userdata, flags, rc, *args):
        ok = (rc == 0) if isinstance(rc, int) else not getattr(rc, "is_failure", False)
        if ok:
            self.state = "up"
            client.subscribe(f"{self.prefix}/#", qos=1)
            log.info("mqtt: connected to %s:%d, subscribed %s/#", self.host, self.port, self.prefix)
        else:
            self.state = "down"

    def _disconnected(self, client, userdata, *args):
        if self.state == "up":
            log.warning("mqtt: broker connection lost; reconnecting")
        self.state = "reconnecting"

    def _message(self, client, userdata, msg):
        try:
            self._on_message(msg.topic, msg.payload)
        except Exception:  # noqa: BLE001 - never kill the network thread
            log.exception("mqtt: handler failed")

    def stop(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
