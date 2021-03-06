"""
MQTT client module
"""

import logging
import time
import json
import paho.mqtt.client as mqtt
from simplesensor.shared.threadsafeLogger import ThreadsafeLogger
from distutils.version import LooseVersion, StrictVersion
from simplesensor.shared.moduleProcess import ModuleProcess
from . import moduleConfigLoader as configLoader
from .version import __version__

class MQTTClientModule(ModuleProcess):
    """ Threaded MQTT client for processing and publishing outbound messages"""

    def __init__(self, baseConfig, pInBoundEventQueue, pOutBoundEventQueue, loggingQueue):

        super(MQTTClientModule, self).__init__()
        self.config = baseConfig
        self.alive = True
        self.inQueue = pInBoundEventQueue

        # Module config
        self.moduleConfig = configLoader.load(self.loggingQueue, __name__)

        # Constants
        self._keepAlive = self.moduleConfig['MqttKeepAlive']
        self._feedName = self.moduleConfig['MqttFeedName']
        self._username = self.moduleConfig['MqttUsername']
        self._key = self.moduleConfig['MqttKey']
        self._host = self.moduleConfig['MqttHost']
        self._port = self.moduleConfig['MqttPort']
        self._publishJson = self.moduleConfig['MqttPublishJson']

        # MQTT setup
        self._client = mqtt.Client()
        self._client.username_pw_set(self._username, self._key)
        self._client.on_connect    = self.on_connect
        self._client.on_disconnect = self.on_disconnect
        self._client.on_message    = self.on_message
        self.mqttConnected = False

        # Logging setup
        self.logger = ThreadsafeLogger(loggingQueue, "MQTT")

    def check_ss_version(self):
        #check for min version met
        self.logger.info('Module version %s' %(__version__))
        if LooseVersion(self.config['ss_version']) < LooseVersion(self.moduleConfig['MinSimpleSensorVersion']):
            self.logger.error('This module requires a min SimpleSensor %s version.  This instance is running version %s' %(self.moduleConfig['MinSimpleSensorVersion'],self.config['ss_version']))
            return False
        return True

    def on_connect(self, client, userdata, flags, rc):
        self.logger.debug('MQTT onConnect called')
        # Result code 0 is success
        if rc == 0:
            self.mqttConnected = True

            # Subscribe to feed here
        else:
            self.logger.error('MQTT failed to connect: %s'%rc)
            raise RuntimeError('MQTT failed to connect: %s'%rc)

    def on_disconnect(self, client, userdata, rc):
        self.logger.debug('MQTT onDisconnect called')
        self.mqttConnected = False
        if rc != 0:
            self.logger.debug('MQTT disconnected unexpectedly: %s'%rc)
            self.handle_reconnect(rc)

    def handle_reconnect(self, result_code):
        pass

    def on_message(self, client, userdata, msg):
        self.logger.debug('MQTT onMessage called for client: %s'%client)

    def connect(self):
        """ Connect to MQTT broker
        Skip calling connect if already connected.
        """
        if self.mqttConnected:
            return

        self._client.connect(self._host, port=self._port, keepalive=self._keepAlive)

    def disconnect(self):
        """ Check if connected"""
        if self.mqttConnected:
            self._client.disconnect()

    def subscribe(self, feed=False):
        """Subscribe to feed, defaults to feed specified in config"""
        if not feed: feed = _feedName
        self._client.subscribe('{0}/feeds/{1}'.format(self._username, feed))

    def publish(self, value, feed=False):
        """Publish a value to a feed"""
        if not feed: feed = _feedName
        self._client.publish('{0}/feeds/{1}'.format(self._username, feed), payload=value)

    def publish_face_values(self, message):
        """ Publish face detection values to individual MQTT feeds
        Parses _extendedData.predictions.faceAttributes property
        """
        try:
            for face in message.extended_data['predictions']:
                faceAttrs = face['faceAttributes']
                for key in faceAttrs:
                    if type(faceAttrs[key]) is dict:
                        val = self.flatten_dict(faceAttrs[key])
                        print('val: ', val)
                    else:
                        val = faceAttrs[key]
                    self.publish(val, key)
        except Exception as e:
            self.logger.error('Error publishing values: %s'%e)

    def flatten_dict(self, aDict):
        """ Get average of simple dictionary of numerical values """
        try:
            val = float(sum(aDict[key] for key in aDict)) / len(aDict)
        except Exception as e:
            self.logger.error('Error flattening dict, returning 0: %s'%e)
        return val or 0
  
    def publish_json_message(self, message):
        self.publish(message.stringify())

    def stringify_message(self, message):
        """ Dump into JSON string """
        return json.dumps(message.__dict__).encode('utf8')

    def process_queue(self):
        """ Process incoming messages. """

        while self.alive:
            # Pump the loop
            self._client.loop(timeout=1)
            if (self.inQueue.empty() == False):
                try:
                    message = self.inQueue.get(block=False,timeout=1)
                    if message is not None and self.mqttConnected:
                        if message.topic.upper() == "SHUTDOWN":
                            self.logger.debug("SHUTDOWN command handled")
                            self.shutdown()
                        else:
                            # Send message as string or split into channels
                            if self._publishJson:
                                self.publish_json_message(message)
                            elif self._publishFaceData:
                                self.publish_face_values(message)
                            else:
                                self.publish_values(message)

                except Exception as e:
                    self.logger.error("MQTT unable to read queue : %s " %e)
            else:
                time.sleep(.25)

    def shutdown(self):
        self.logger.info("Shutting down")
        self.alive = False
        time.sleep(1)
        self.exit = True

    def run(self):
       if not self.check_ss_version():
            #cant run with wrong version so we return early
            return False

        """ Thread start method"""
        self.logger.info("Running MQTT")

        self.connect()
        self.alive = True

        # Start queue loop
        self.process_queue()