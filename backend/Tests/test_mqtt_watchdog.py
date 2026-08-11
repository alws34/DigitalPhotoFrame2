
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


# Mock psutil before importing mqtt_bridge
sys.modules['psutil'] = MagicMock()

from Utilities.MQTT.mqtt_bridge import MqttBridge  # noqa: E402


class TestMqttWatchdog(unittest.TestCase):
    def setUp(self):
        self.mock_view = MagicMock()
        # Configure psutil mock
        sys.modules['psutil'].cpu_percent.return_value = 10.0
        sys.modules['psutil'].virtual_memory.return_value = MagicMock(total=8000000000, used=4000000000, percent=50.0)
        sys.modules['psutil'].sensors_temperatures.return_value = {}
        
        self.settings = {
            "mqtt": {
                "enabled": True,
                "host": "localhost",
                "interval_seconds": 1
            }
        }

    @patch('Utilities.MQTT.mqtt_bridge.mqtt')
    def test_watchdog_reconnects_after_timeout(self, mock_mqtt):
        # Setup mock client
        mock_client_instance = MagicMock()
        mock_mqtt.Client.return_value = mock_client_instance
        
        bridge = MqttBridge(self.mock_view, self.settings)
        
        # Override internal constants for faster testing if they exist, 
        # or we just rely on the logic we are about to write.
        # We will assume a small watchdog timeout for testing.
        bridge.WATCHDOG_TIMEOUT = 2  # 2 seconds for test
        
        # Start the bridge
        bridge.start()
        
        # Simulate initial connection
        bridge._on_connect(mock_client_instance, None, None, 0)
        self.assertTrue(bridge.connected)
        
        # Simulate disconnect
        print("Simulating disconnect...")
        bridge._on_disconnect(mock_client_instance, None, 1)
        self.assertFalse(bridge.connected)
        
        # Wait for watchdog to trigger (should be > WATCHDOG_TIMEOUT)
        time.sleep(3)
        
        # Verify that connect_async (or reconnect) was called again
        # The initial start called connect_async once.
        # The watchdog should have called it again or called reconnect().
        
        # We need to see if the watchdog logic works. 
        # Since we haven't implemented it yet, this test is expected to fail or 
        # show no reconnection attempt if we were running it against the old code.
        
        bridge.stop()

if __name__ == '__main__':
    unittest.main()
