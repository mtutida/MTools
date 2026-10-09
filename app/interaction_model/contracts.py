from abc import ABC, abstractmethod

class IExecutionController(ABC):
    @abstractmethod
    def start(self): pass
    @abstractmethod
    def stop(self): pass

class ISchedulerGateway(ABC):
    @abstractmethod
    def submit_job(self, job): pass
