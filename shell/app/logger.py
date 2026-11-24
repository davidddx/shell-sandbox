import logging
FORMAT = '%(asctime)s %(clientip)-15s %(user)-8s %(message)s'
debug_logger = logging.getLogger(name="debug")
debug_logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
file_handler = logging.FileHandler(filename="debug.log")
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s - %(name)s - %(levelname)s]: %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)
debug_logger.addHandler(console_handler)
debug_logger.addHandler(file_handler)
