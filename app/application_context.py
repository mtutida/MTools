
from app.interaction_model.run_controller import RunController
from app.ancillary.logging_service import LoggingService


class ApplicationContext:

    def __init__(self):

        # logging service expected by main.py
        self.logger = LoggingService()

        # interaction controllers
        self.run_controller = RunController()
